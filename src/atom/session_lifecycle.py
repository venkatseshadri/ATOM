"""Module 7 — Session Lifecycle (Phase 3, real).

Owns the trading-day clock: day-type classification, market phase, entry-window
authority (one-way latch), EOD square-off authority (escalating, confirmation-gated).
Fail-SAFE, not fail-open: any ambiguity resolves toward blocking entries / forcing flat.

ATOM's real architecture is stateless-per-invocation (cron fires run_live_once.py once a
minute — see runner.py's docstring: "stateless between runs"). There is no long-running
process to host the module doc's self-correcting cadence scheduler (§7.4) — the external
cron tick already IS the cadence. Every invocation is, by construction, what §7.6.2 calls
a "cold start": every function here recomputes phase/deadlines fresh from (now, calendar)
alone, never from replayed history. The only things that must survive across invocations
are the ONE-WAY LATCHES (entry cutoff, square-off escalation) — persisted via AtomState,
not in-memory, so a clock hiccup or process restart can never un-latch them.

Consumes the shared holiday calendar (same file antariksh's plumbing tests use) — never
authors holidays, per the module's own boundary (7.1.1.1).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

HOLIDAYS_FILE = Path("/root/.picoclaw/workspace/config/market_holidays.json")

# Standard NSE/BSE session (IST). No half-day/muhurat data exists in the shared
# calendar today (checked: 18 holiday-only entries, no early-close flag) — fail-safe
# default is the standard session; a half-day override slot is left for when that data
# exists rather than inventing one.
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)

LAST_ENTRY_CUTOFF_OFFSET = timedelta(minutes=60)   # 14:30 on a normal day
SQUAREOFF_START_OFFSET = timedelta(minutes=12)      # 15:18
HARD_FLAT_DEADLINE_OFFSET = timedelta(minutes=5)    # 15:25 (before 15:30 close)
EXPIRY_SQUAREOFF_START_OFFSET = timedelta(minutes=20)   # 15:10 — tighter on expiry day
POST_OPEN_SETTLE = timedelta(minutes=15)            # entry window doesn't start at 09:15

DAY_FULL_TRADING = "FULL_TRADING"
DAY_HOLIDAY = "HOLIDAY"
DAY_WEEKEND = "WEEKEND"
DAY_NON_TRADING = "NON_TRADING"   # fail-safe: date absent from calendar

PHASE_PRE_OPEN = "PRE_OPEN"
PHASE_OPEN = "OPEN"
PHASE_CLOSE_IMMINENT = "CLOSE_IMMINENT"
PHASE_POST_CLOSE = "POST_CLOSE"
PHASE_CLOSED = "CLOSED"
PHASE_NON_TRADING = "NON_TRADING_DAY"

_PHASE_ORDER = {PHASE_PRE_OPEN: 0, PHASE_OPEN: 1, PHASE_CLOSE_IMMINENT: 2,
                PHASE_POST_CLOSE: 3, PHASE_CLOSED: 4}


class MarketCalendar:
    """7.1 — day-type + session timings. Read-only consumer of the shared holiday file."""

    def __init__(self, holidays_path: Path | str = HOLIDAYS_FILE) -> None:
        self.holidays_path = Path(holidays_path)
        self._holidays: set[str] | None = None
        self._years: set[int] | None = None

    def _load(self) -> None:
        if self._holidays is not None:
            return
        try:
            data = json.loads(self.holidays_path.read_text())
            self._holidays = {h["date"] for h in data.get("holidays", [])}
            # the file is year-scoped ("year": 2026, "last_updated": ...) — a query for
            # a date outside the years it actually covers is the real "calendar gap"
            # scenario (7.1.1.1: "stale calendar not updated for new year"), distinct
            # from an ordinary non-holiday weekday which is correctly just absent from
            # an exclusion-list-style holidays[] array.
            self._years = {int(h["date"][:4]) for h in data.get("holidays", [])}
            if "year" in data:
                self._years.add(int(data["year"]))
        except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
            self._holidays, self._years = set(), set()   # fail-safe: nothing known-good

    def day_type(self, d: date) -> str:
        self._load()
        if not self._years or d.year not in self._years:
            return DAY_NON_TRADING   # fail-safe: calendar doesn't cover this year at all
        if d.isoformat() in self._holidays:
            return DAY_HOLIDAY
        if d.weekday() >= 5:   # Sat=5, Sun=6
            return DAY_WEEKEND
        return DAY_FULL_TRADING

    def session_times(self, d: date) -> tuple[datetime, datetime]:
        """(open, close) for the day — standard hours (no half-day data available)."""
        return (datetime.combine(d, SESSION_OPEN), datetime.combine(d, SESSION_CLOSE))

    def deadlines(self, d: date, is_expiry_today: bool = False) -> "Deadlines":
        _, close = self.session_times(d)
        squareoff_offset = EXPIRY_SQUAREOFF_START_OFFSET if is_expiry_today else SQUAREOFF_START_OFFSET
        last_entry_cutoff = close - LAST_ENTRY_CUTOFF_OFFSET
        squareoff_start = close - squareoff_offset
        hard_flat_deadline = close - HARD_FLAT_DEADLINE_OFFSET
        # guarantee monotonic ordering even under a pathologically short/misconfigured
        # session — clamp rather than emit an inverted deadline (7.1.2.2)
        squareoff_start = max(squareoff_start, last_entry_cutoff)
        hard_flat_deadline = max(hard_flat_deadline, squareoff_start)
        return Deadlines(last_entry_cutoff, squareoff_start, hard_flat_deadline, close)


@dataclass(frozen=True)
class Deadlines:
    last_entry_cutoff: datetime
    squareoff_start: datetime
    hard_flat_deadline: datetime
    close: datetime


def compute_phase(now: datetime, day_type: str, open_: datetime, close: datetime,
                  squareoff_start: datetime, halted: bool = False) -> str:
    """7.2 — pure phase computation. No in-memory transition history; every call is
    self-contained (restart-safe by construction, not by special-casing restarts)."""
    if day_type != DAY_FULL_TRADING:
        return PHASE_NON_TRADING
    if now < open_:
        phase = PHASE_PRE_OPEN
    elif now < squareoff_start:
        phase = PHASE_OPEN
    elif now < close:
        phase = PHASE_CLOSE_IMMINENT
    elif now < close + timedelta(minutes=30):
        phase = PHASE_POST_CLOSE
    else:
        phase = PHASE_CLOSED
    return f"HALTED:{phase}" if halted and phase in (PHASE_OPEN, PHASE_CLOSE_IMMINENT) else phase


def entry_window(open_: datetime, deadlines: Deadlines) -> tuple[datetime, datetime]:
    """7.3.1.1 — [start, cutoff], clamped so start never exceeds cutoff (degenerate
    session → empty window, never entries)."""
    start = open_ + POST_OPEN_SETTLE
    cutoff = deadlines.last_entry_cutoff
    return (start, cutoff) if start <= cutoff else (cutoff, cutoff)   # empty window


def is_entry_allowed(now: datetime, window_start: datetime, window_cutoff: datetime,
                     halted: bool = False, latched_closed: bool = False) -> bool:
    """7.3.1.2 — the one-way latch itself is the CALLER's persisted state
    (latched_closed); this function only tells the caller whether a fresh clock read
    ALSO says no — callers should latch True the first time this returns False and
    never re-open it that day regardless of later clock readings."""
    if latched_closed or halted:
        return False
    return window_start <= now < window_cutoff


def square_off_level(now: datetime, deadlines: Deadlines, is_flat: bool | None,
                     halted: bool = False) -> int:
    """7.5.1 — 0 = not yet due, 1 = soft, 2 = aggressive, 3 = hard/market.
    `is_flat=None` means position-state is unavailable — fail-safe: treat as NOT flat
    (keep escalating) per 7.5.2's "cannot-confirm-flat" stance."""
    if is_flat is True:
        return 0
    if now < deadlines.squareoff_start:
        return 0
    if halted:
        return -1   # unresolved-position escalation — cannot execute, not a normal level
    span = deadlines.hard_flat_deadline - deadlines.squareoff_start
    elapsed = now - deadlines.squareoff_start
    if span.total_seconds() <= 0 or elapsed >= span:
        return 3
    frac = elapsed.total_seconds() / span.total_seconds()
    return 2 if frac >= 0.5 else 1


def square_off_status(now: datetime, deadlines: Deadlines, is_flat: bool | None,
                      halted: bool = False) -> str:
    """7.5.2 — end-of-day outcome record. FLAT / IN_PROGRESS / SQUAREOFF_FAILED /
    CANNOT_CONFIRM_FLAT / HALTED_UNRESOLVED."""
    if is_flat is True:
        return "FLAT"
    if now < deadlines.squareoff_start:
        return "FLAT" if is_flat is not False else "NOT_YET_DUE"
    if halted:
        return "HALTED_UNRESOLVED"
    if is_flat is None:
        return "CANNOT_CONFIRM_FLAT"
    if now >= deadlines.close:
        return "SQUAREOFF_FAILED"
    return "IN_PROGRESS"


def detect_clock_anomaly(prev_ts: datetime | None, now: datetime,
                         max_forward_gap: timedelta = timedelta(minutes=10)) -> str | None:
    """7.6.1 — backward jump or an implausibly large forward jump. Returns a reason
    string or None. Callers must NEVER un-latch a one-way latch on a backward jump —
    that's enforced by the latch being external persisted state, not by this function."""
    if prev_ts is None:
        return None
    if now < prev_ts:
        return "CLOCK_BACKWARD"
    if now - prev_ts > max_forward_gap:
        return "CLOCK_FORWARD_JUMP"
    return None
