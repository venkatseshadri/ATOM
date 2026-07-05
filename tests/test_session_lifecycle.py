"""Module 7 — Session Lifecycle tests (Phase 3, testcases T3.5 EOD square-off +
supporting day-type/phase/entry-window/clock-integrity behavior)."""
import os
from datetime import date, datetime, timedelta

from atom.session_lifecycle import (
    DAY_FULL_TRADING, DAY_HOLIDAY, DAY_NON_TRADING, DAY_WEEKEND,
    PHASE_CLOSE_IMMINENT, PHASE_CLOSED, PHASE_NON_TRADING, PHASE_OPEN, PHASE_POST_CLOSE,
    PHASE_PRE_OPEN, MarketCalendar, compute_phase, detect_clock_anomaly, entry_window,
    is_entry_allowed, square_off_level, square_off_status,
)

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "holidays_fixture.json")


def _cal():
    return MarketCalendar(holidays_path=FIX)


# ---- 7.1 Day-type classification --------------------------------------------

def test_normal_weekday_is_full_trading():
    assert _cal().day_type(date(2026, 7, 6)) == DAY_FULL_TRADING   # Monday


def test_holiday_weekday_is_holiday():
    assert _cal().day_type(date(2026, 10, 2)) == DAY_HOLIDAY   # Friday, in fixture


def test_weekend_is_weekend():
    assert _cal().day_type(date(2026, 7, 4)) == DAY_WEEKEND   # Saturday


def test_year_outside_calendar_coverage_fails_safe():
    """The calendar is year-scoped (2026 only) — a 2027 query is the real "stale
    calendar" gap the spec calls out, not an ordinary non-holiday day."""
    assert _cal().day_type(date(2027, 7, 6)) == DAY_NON_TRADING


def test_missing_calendar_file_fails_safe():
    cal = MarketCalendar(holidays_path="/nonexistent/path.json")
    assert cal.day_type(date(2026, 7, 6)) == DAY_NON_TRADING


# ---- 7.1.2 Deadlines ----------------------------------------------------------

def test_deadlines_are_monotonic_and_close_relative():
    d = _cal().deadlines(date(2026, 7, 6))
    assert d.last_entry_cutoff <= d.squareoff_start <= d.hard_flat_deadline <= d.close
    assert d.close == datetime(2026, 7, 6, 15, 30)
    assert d.squareoff_start == datetime(2026, 7, 6, 15, 18)


def test_expiry_day_has_tighter_squareoff_start():
    normal = _cal().deadlines(date(2026, 7, 6), is_expiry_today=False)
    expiry = _cal().deadlines(date(2026, 7, 6), is_expiry_today=True)
    assert expiry.squareoff_start < normal.squareoff_start


# ---- 7.2 Phase evaluator ------------------------------------------------------

def test_phase_transitions_through_the_day():
    d = date(2026, 7, 6)
    open_, close = _cal().session_times(d)
    dl = _cal().deadlines(d)
    assert compute_phase(datetime(2026, 7, 6, 9, 0), DAY_FULL_TRADING, open_, close,
                         dl.squareoff_start) == PHASE_PRE_OPEN
    assert compute_phase(datetime(2026, 7, 6, 11, 0), DAY_FULL_TRADING, open_, close,
                         dl.squareoff_start) == PHASE_OPEN
    assert compute_phase(datetime(2026, 7, 6, 15, 20), DAY_FULL_TRADING, open_, close,
                         dl.squareoff_start) == PHASE_CLOSE_IMMINENT
    assert compute_phase(datetime(2026, 7, 6, 15, 45), DAY_FULL_TRADING, open_, close,
                         dl.squareoff_start) == PHASE_POST_CLOSE
    assert compute_phase(datetime(2026, 7, 6, 20, 0), DAY_FULL_TRADING, open_, close,
                         dl.squareoff_start) == PHASE_CLOSED


def test_non_trading_day_phase():
    d = date(2026, 7, 4)  # Saturday
    open_, close = _cal().session_times(d)
    assert compute_phase(datetime(2026, 7, 4, 11, 0), DAY_WEEKEND, open_, close,
                         close) == PHASE_NON_TRADING


def test_halt_overlay_only_shows_during_active_phases():
    d = date(2026, 7, 6)
    open_, close = _cal().session_times(d)
    dl = _cal().deadlines(d)
    p = compute_phase(datetime(2026, 7, 6, 11, 0), DAY_FULL_TRADING, open_, close,
                      dl.squareoff_start, halted=True)
    assert p == "HALTED:OPEN"
    # halt overlay doesn't apply outside OPEN/CLOSE_IMMINENT (e.g. pre-open)
    p2 = compute_phase(datetime(2026, 7, 6, 9, 0), DAY_FULL_TRADING, open_, close,
                       dl.squareoff_start, halted=True)
    assert p2 == PHASE_PRE_OPEN


# ---- 7.3 Entry-window authority -----------------------------------------------

def test_entry_window_start_after_open_settle():
    d = date(2026, 7, 6)
    open_, _ = _cal().session_times(d)
    dl = _cal().deadlines(d)
    start, cutoff = entry_window(open_, dl)
    assert start == datetime(2026, 7, 6, 9, 30)
    assert cutoff == dl.last_entry_cutoff


def test_is_entry_allowed_boundary_inclusive_block():
    d = date(2026, 7, 6)
    open_, _ = _cal().session_times(d)
    start, cutoff = entry_window(open_, _cal().deadlines(d))
    assert is_entry_allowed(cutoff - timedelta(seconds=1), start, cutoff) is True
    assert is_entry_allowed(cutoff, start, cutoff) is False   # >= cutoff blocks


def test_one_way_latch_never_reopens():
    """Once the caller has latched closed (per the one-way-latch contract), a clock
    rewind to before cutoff must NOT be honored — enforced by the caller passing
    latched_closed=True regardless of what `now` says."""
    d = date(2026, 7, 6)
    open_, _ = _cal().session_times(d)
    start, cutoff = entry_window(open_, _cal().deadlines(d))
    rewound_now = cutoff - timedelta(minutes=1)
    assert is_entry_allowed(rewound_now, start, cutoff, latched_closed=True) is False


def test_halt_blocks_entries_even_inside_window():
    d = date(2026, 7, 6)
    open_, _ = _cal().session_times(d)
    start, cutoff = entry_window(open_, _cal().deadlines(d))
    mid = start + timedelta(minutes=5)
    assert is_entry_allowed(mid, start, cutoff, halted=True) is False


def test_degenerate_window_when_start_would_exceed_cutoff():
    """A pathologically short session (cutoff before the settle-delayed start) must
    clamp to an empty window, never an inverted one."""
    d = date(2026, 7, 6)
    open_, _ = _cal().session_times(d)
    dl = _cal().deadlines(d)
    tiny_cutoff = dl.last_entry_cutoff.__class__(2026, 7, 6, 9, 20)  # before settle
    from atom.session_lifecycle import Deadlines
    tiny_dl = Deadlines(tiny_cutoff, dl.squareoff_start, dl.hard_flat_deadline, dl.close)
    start, cutoff = entry_window(open_, tiny_dl)
    assert start == cutoff   # empty window
    assert is_entry_allowed(datetime(2026, 7, 6, 9, 25), start, cutoff) is False


# ---- 7.5 EOD square-off authority ---------------------------------------------

def test_squareoff_level_zero_before_window():
    d = date(2026, 7, 6)
    dl = _cal().deadlines(d)
    assert square_off_level(dl.squareoff_start - timedelta(minutes=1), dl, is_flat=False) == 0


def test_squareoff_level_zero_when_already_flat():
    d = date(2026, 7, 6)
    dl = _cal().deadlines(d)
    assert square_off_level(dl.squareoff_start, dl, is_flat=True) == 0


def test_squareoff_escalates_soft_then_aggressive_then_hard():
    d = date(2026, 7, 6)
    dl = _cal().deadlines(d)
    lvl_start = square_off_level(dl.squareoff_start, dl, is_flat=False)
    span = dl.hard_flat_deadline - dl.squareoff_start
    lvl_mid = square_off_level(dl.squareoff_start + span / 2, dl, is_flat=False)
    lvl_end = square_off_level(dl.hard_flat_deadline, dl, is_flat=False)
    assert lvl_start == 1
    assert lvl_mid == 2
    assert lvl_end == 3


def test_squareoff_unavailable_position_state_treated_as_not_flat():
    """T3.5-adjacent: position-state feed unavailable must NOT be silently treated as
    flat — fail-safe keeps escalating (7.5.2)."""
    d = date(2026, 7, 6)
    dl = _cal().deadlines(d)
    assert square_off_level(dl.squareoff_start, dl, is_flat=None) >= 1


def test_squareoff_halted_gives_unresolved_escalation_not_a_normal_level():
    d = date(2026, 7, 6)
    dl = _cal().deadlines(d)
    assert square_off_level(dl.squareoff_start, dl, is_flat=False, halted=True) == -1


def test_squareoff_status_failed_after_close_with_open_position():
    d = date(2026, 7, 6)
    dl = _cal().deadlines(d)
    assert square_off_status(dl.close, dl, is_flat=False) == "SQUAREOFF_FAILED"


def test_squareoff_status_flat_confirmed():
    d = date(2026, 7, 6)
    dl = _cal().deadlines(d)
    assert square_off_status(dl.close, dl, is_flat=True) == "FLAT"


def test_squareoff_status_cannot_confirm_flat():
    d = date(2026, 7, 6)
    dl = _cal().deadlines(d)
    assert square_off_status(dl.squareoff_start, dl, is_flat=None) == "CANNOT_CONFIRM_FLAT"


# ---- 7.6 Clock integrity -------------------------------------------------------

def test_clock_backward_jump_detected():
    prev = datetime(2026, 7, 6, 11, 0)
    now = datetime(2026, 7, 6, 10, 59)
    assert detect_clock_anomaly(prev, now) == "CLOCK_BACKWARD"


def test_clock_forward_jump_detected():
    prev = datetime(2026, 7, 6, 11, 0)
    now = datetime(2026, 7, 6, 15, 45)
    assert detect_clock_anomaly(prev, now) == "CLOCK_FORWARD_JUMP"


def test_clock_normal_progression_no_anomaly():
    prev = datetime(2026, 7, 6, 11, 0)
    now = datetime(2026, 7, 6, 11, 1)
    assert detect_clock_anomaly(prev, now) is None


def test_clock_first_reading_no_anomaly():
    assert detect_clock_anomaly(None, datetime(2026, 7, 6, 11, 0)) is None
