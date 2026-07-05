"""Module 11 — Connectivity & Auth (Phase 3, real, read-only).

ATOM never authenticates to the broker itself, and won't while paper-only (Phase 7 = real
capital). An independent Shoonya login was considered and rejected — Board decision
2026-07-05: it risks invalidating antariksh's live feed session under the same
credentials (most broker APIs allow one active session per app; a second login commonly
kicks the first). Sharing beats duplicating.

ATOM doesn't need the actual broker token at all — it only needs to know whether the
shared session (antariksh's live feed, which Penguin's capture pipeline depends on) is
healthy enough to trust the data about to be read. This module reads antariksh's live
feed heartbeat files (`data/live/feed_{INDEX}.heartbeat` — a plain ISO timestamp, updated
every cycle) as a read-only proxy for that. Fail-safe: missing/stale/malformed heartbeat
all resolve to "not alive", never a false positive.

This is a SECONDARY signal. The PRIMARY protection against acting on stale data is
already `runner.py`'s own bar-freshness gate (`_age_sec` vs `max_stale_sec` on the
Penguin bar timestamp itself) — that's stronger since it's the actual data ATOM is about
to trade on, not a side-channel heartbeat. Module 11 adds an independent "is the upstream
session itself alive" check for cases where the feed process is up but somehow not
producing fresh bars (or vice versa)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

HEARTBEAT_DIR = Path("/home/trading_ceo/antariksh/data/live")
DEFAULT_STALE_AFTER_SEC = 90


@dataclass(frozen=True)
class SessionHealth:
    index: str
    alive: bool
    last_heartbeat: str | None
    age_sec: float | None
    reason: str | None   # None if alive; else NO_HEARTBEAT_FILE | MALFORMED_HEARTBEAT | HEARTBEAT_STALE


def read_heartbeat(index: str, heartbeat_dir: Path | str = HEARTBEAT_DIR) -> str | None:
    path = Path(heartbeat_dir) / f"feed_{index}.heartbeat"
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return None


def check_session_health(index: str, now: datetime | None = None,
                         heartbeat_dir: Path | str = HEARTBEAT_DIR,
                         stale_after_sec: float = DEFAULT_STALE_AFTER_SEC) -> SessionHealth:
    """Fail-safe: any read/parse failure resolves to alive=False, never a guess."""
    now = now or datetime.now()
    raw = read_heartbeat(index, heartbeat_dir)
    if raw is None:
        return SessionHealth(index, False, None, None, "NO_HEARTBEAT_FILE")
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return SessionHealth(index, False, raw, None, "MALFORMED_HEARTBEAT")
    if ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    age = (now - ts).total_seconds()
    if age < 0:
        return SessionHealth(index, False, raw, age, "HEARTBEAT_IN_FUTURE")
    alive = age <= stale_after_sec
    return SessionHealth(index, alive, raw, age, None if alive else "HEARTBEAT_STALE")
