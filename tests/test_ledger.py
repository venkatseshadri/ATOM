"""Module 14 — Ledger & Persistence tests (Phase 4 acceptance T4.1/T4.5)."""
import pytest

from atom.ledger import (
    Leg, MATCH, MISSED_FILL, PHANTOM_FILL, UNKNOWN_AT_BROKER,
    apply_fill, compute_pnl, finalize_eod, reconcile, validate_fill,
)


# ---- 14.1 Fill validation + application -----------------------------------------

def test_validate_missing_leg():
    assert validate_fill("", 10.0, 65, 65, "FILLED") == "MISSING_LEG"


def test_validate_negative_price():
    assert validate_fill("L1", -1.0, 65, 65, "FILLED") == "INVALID_PRICE"


def test_validate_nan_price():
    assert validate_fill("L1", float("nan"), 65, 65, "FILLED") == "INVALID_PRICE"


def test_validate_zero_qty():
    assert validate_fill("L1", 10.0, 0, 65, "FILLED") == "ZERO_QTY"


def test_validate_lot_size_violation():
    assert validate_fill("L1", 10.0, 70, 65, "FILLED") == "LOT_SIZE_VIOLATION"


def test_validate_unknown_status_fails_closed():
    v = validate_fill("L1", 10.0, 65, 65, "SOMETHING_NEW")
    assert v == "NOT_EXECUTED_STATUS:SOMETHING_NEW"


def test_validate_rejected_status_not_applied():
    assert validate_fill("L1", 10.0, 65, 65, "REJECTED") is not None


def test_apply_fill_new_leg():
    r = apply_fill(None, "L1", 100.0, 65, "SELL", 65)
    assert r.accepted and r.leg.signed_qty == -65 and r.leg.avg_entry_price == 100.0


def test_apply_fill_malformed_rejected_position_unchanged():
    prior = Leg("L1", -65, 100.0)
    r = apply_fill(prior, "L1", 10.0, 0, "SELL", 65)
    assert not r.accepted and r.leg == prior   # position untouched


def test_apply_fill_same_direction_weighted_average():
    prior = Leg("L1", -65, 100.0)
    r = apply_fill(prior, "L1", 120.0, 65, "SELL", 65)
    assert r.accepted
    assert r.leg.signed_qty == -130
    assert r.leg.avg_entry_price == pytest.approx((100.0 * 65 + 120.0 * 65) / 130)


def test_apply_fill_opposite_direction_reduces_and_keeps_avg():
    prior = Leg("L1", -130, 110.0)
    r = apply_fill(prior, "L1", 90.0, 65, "BUY", 65)
    assert r.accepted
    assert r.leg.signed_qty == -65
    assert r.leg.avg_entry_price == 110.0   # remaining qty's basis unchanged


# ---- 14.1.2 Idempotent dedup (via AtomState) ------------------------------------

def test_duplicate_fill_id_suppressed(tmp_path):
    from atom.atom_state import AtomState
    state = AtomState(str(tmp_path / "s.sqlite"))
    assert state.record_fill_applied("fill1", "L1", 100.0, 65, "2026-07-06T09:15:00") is True
    assert state.is_fill_applied("fill1") is True
    assert state.record_fill_applied("fill1", "L1", 100.0, 65, "2026-07-06T09:16:00") is False


def test_distinct_fill_ids_both_applied(tmp_path):
    from atom.atom_state import AtomState
    state = AtomState(str(tmp_path / "s.sqlite"))
    assert state.record_fill_applied("fill1", "L1", 100.0, 65, "t1") is True
    assert state.record_fill_applied("fill2", "L1", 100.0, 65, "t2") is True


# ---- 14.3/14.4 Mark-to-market + P&L ----------------------------------------------

def test_unrealized_pnl_short_decayed_is_positive():
    legs = (Leg("L1", -65, 11.0),)
    r = compute_pnl(legs, {"L1": 9.0}, realized=0.0, lot_size=1)
    assert r.unrealized == pytest.approx((11.0 - 9.0) * 65)
    assert r.confidence == "OK"


def test_unrealized_pnl_short_adverse_is_negative():
    legs = (Leg("L1", -65, 11.0),)
    r = compute_pnl(legs, {"L1": 14.0}, realized=0.0, lot_size=1)
    assert r.unrealized < 0


def test_stale_mark_flags_low_confidence_not_a_fake_zero():
    legs = (Leg("L1", -65, 11.0),)
    r = compute_pnl(legs, {"L1": None}, realized=0.0, lot_size=1)
    assert r.unrealized is None and r.confidence == "LOW_CONFIDENCE"


def test_flat_is_exactly_zero_and_flagged_flat():
    r = compute_pnl((), {}, realized=250.0, lot_size=1)
    assert r.unrealized == 0.0 and r.flat is True and r.total == 250.0


def test_total_equals_realized_plus_unrealized():
    legs = (Leg("L1", -65, 11.0), Leg("L2", 65, 3.0))
    r = compute_pnl(legs, {"L1": 9.0, "L2": 4.0}, realized=60.0, lot_size=1)
    assert r.total == pytest.approx(r.realized + r.unrealized)


# ---- 14.8 Reconciliation ---------------------------------------------------------

def test_reconcile_perfect_match():
    legs = (Leg("L1", -65, 100.0),)
    d = reconcile(legs, {"L1": -65})
    assert d[0].classification == MATCH


def test_reconcile_missed_fill_broker_has_more():
    legs = (Leg("L1", -65, 100.0),)
    d = reconcile(legs, {"L1": -130})
    assert d[0].classification == MISSED_FILL


def test_reconcile_phantom_fill_ledger_has_more():
    legs = (Leg("L1", -130, 100.0),)
    d = reconcile(legs, {"L1": -65})
    assert d[0].classification == PHANTOM_FILL


def test_reconcile_unknown_at_broker():
    d = reconcile((), {"L1": -65})
    assert d[0].classification == UNKNOWN_AT_BROKER


def test_reconcile_never_mutates_ledger_legs():
    legs = (Leg("L1", -65, 100.0),)
    reconcile(legs, {"L1": -130})
    assert legs[0].signed_qty == -65   # unchanged — reconcile only reports


# ---- 14.9 EOD finalization -------------------------------------------------------

def test_eod_clean_flat():
    r = finalize_eod((), (100.0, -40.0, 60.0))
    assert r.flat and r.daily_realized == 120.0 and r.alarm is None


def test_eod_residual_leg_alarms():
    r = finalize_eod((Leg("L1", -65, 100.0),), (100.0,))
    assert not r.flat and r.alarm == "NOT_FLAT_AT_EOD"
    assert r.residual_legs == ("L1",)


def test_eod_daily_realized_sums_events_not_recompute():
    r = finalize_eod((), (10.0, 10.0, 10.0))
    assert r.daily_realized == 30.0


def test_eod_finalization_idempotent_via_atomstate(tmp_path):
    from atom.atom_state import AtomState
    state = AtomState(str(tmp_path / "s.sqlite"))
    assert state.finalize_day("2026-07-06", 120.0, True, (), "2026-07-06T15:30:00") is True
    assert state.finalize_day("2026-07-06", 999.0, True, (), "2026-07-06T15:31:00") is False
    stored = state.day_finalization("2026-07-06")
    assert stored["daily_realized"] == 120.0   # the FIRST finalization wins, never rewritten


# ---- 14.6/14.7 Recovery + single-writer (architecture-level, no new code) -------

def test_recovery_reconstructs_open_position_from_a_fresh_connection(tmp_path):
    """14.6.1 — a 'crash and restart' is just a new AtomState instance pointed at the
    same file; ATOM's stateless-per-invocation design makes this the NORMAL path, not
    a special recovery mode (same principle as Module 7's session-lifecycle doc)."""
    from atom import phase1
    from atom.atom_state import AtomState
    db = str(tmp_path / "s.sqlite")
    order = phase1.PaperOrder("bull_put_spread",
                              (("BUY", 100, "PE", 1.0), ("SELL", 200, "PE", 2.0)),
                              1000.0, 2000.0, 65, "01-JAN-2099")
    AtomState(db).checkpoint_and_record("SINGLE_SPREAD", "t1", order, "t1", {"regime": "n/a", "confidence": 0.0})

    # simulate a crash + restart: brand-new AtomState instance, same file
    recovered = AtomState(db)
    fsm_state, _ = recovered.load()
    pos = recovered.last_open_position()
    assert fsm_state == "SINGLE_SPREAD"
    assert pos is not None and pos["net_credit"] == 1000.0
