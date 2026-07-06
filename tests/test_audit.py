"""Module 15 — Telemetry & Audit tests (Phase 4 acceptance T4.3 audit reconstruction)."""
import pytest

from atom.audit import AuditTrail


@pytest.fixture
def trail(tmp_path):
    return AuditTrail(str(tmp_path / "audit.sqlite"))


# ---- 15.1.1 Envelope validation --------------------------------------------------

def test_missing_source_quarantined(trail):
    e = trail.append("", "decision.open", {}, "2026-07-06T09:16:00")
    assert e is None
    assert trail.dead_letter_count() == 1


def test_missing_type_quarantined(trail):
    e = trail.append("phase1", "", {}, "2026-07-06T09:16:00")
    assert e is None
    assert trail.dead_letter_count() == 1


def test_well_formed_event_accepted(trail):
    e = trail.append("phase1", "decision.open", {"regime": "TREND_DOWN"}, "2026-07-06T09:16:00")
    assert e is not None and e.type == "decision.open"


# ---- 15.1.2 Taxonomy -------------------------------------------------------------

def test_registered_type_stored_canonically(trail):
    e = trail.append("phase1", "decision.open", {}, "t1")
    assert e.registered is True and e.type == "decision.open"


def test_unregistered_type_accepted_but_flagged(trail):
    e = trail.append("foo", "some.weird.type", {}, "t1")
    assert e is not None and e.registered is False
    assert e.type == "unregistered.some.weird.type"
    assert trail.taxonomy_drift() == {"unregistered.some.weird.type": 1}


# ---- 15.6/15.7 Trade reconstruction (T4.3) ---------------------------------------

def test_reconstruct_full_trade_lifecycle_in_order(trail):
    trail.append("phase1", "decision.open", {"regime": "TREND_DOWN"}, "t1", trade_id="T1")
    trail.append("risk", "risk.approved", {"qty": 1}, "t2", trade_id="T1")
    trail.append("stop_management", "position.tsl_trigger", {"pnl": 2200}, "t3", trade_id="T1")
    events = trail.reconstruct_trade("T1")
    assert [e.type for e in events] == ["decision.open", "risk.approved", "position.tsl_trigger"]


def test_reconstruct_only_returns_that_trades_events(trail):
    trail.append("phase1", "decision.open", {}, "t1", trade_id="T1")
    trail.append("phase1", "decision.open", {}, "t2", trade_id="T2")
    assert len(trail.reconstruct_trade("T1")) == 1
    assert len(trail.reconstruct_trade("T2")) == 1


def test_reconstruct_unknown_trade_id_returns_empty(trail):
    assert trail.reconstruct_trade("NONEXISTENT") == []


def test_uncorrelated_events_allowed_with_null_trade_id(trail):
    """15.1.1 — a startup/health event with no trade context is allowed, just
    uncorrelated (not attached to any trade_id)."""
    e = trail.append("system", "system.error", {"msg": "boot"}, "t1")
    assert e is not None and e.trade_id is None


# ---- 15.8 Integrity / tamper-evidence --------------------------------------------

def test_clean_chain_has_no_breaks(trail):
    trail.append("phase1", "decision.open", {}, "t1", trade_id="T1")
    trail.append("risk", "risk.approved", {}, "t2", trade_id="T1")
    assert trail.verify_integrity() == []


def test_tampered_payload_detected(trail):
    trail.append("phase1", "decision.open", {"regime": "TREND_DOWN"}, "t1", trade_id="T1")
    c = trail._c()
    c.execute("UPDATE decision_trace SET payload_json='{\"regime\":\"TAMPERED\"}'")
    c.commit()
    c.close()
    breaks = trail.verify_integrity()
    assert any("TAMPERED" in b for b in breaks)


def test_deleted_event_breaks_the_chain(trail):
    """Deleting an event breaks the NEXT event's prior_hash link — detectable even
    though the deleted row itself is gone."""
    trail.append("phase1", "decision.open", {}, "t1", trade_id="T1")
    trail.append("risk", "risk.approved", {}, "t2", trade_id="T1")
    trail.append("stop_management", "position.tsl_trigger", {}, "t3", trade_id="T1")
    c = trail._c()
    c.execute("DELETE FROM decision_trace WHERE type='risk.approved'")
    c.commit()
    c.close()
    breaks = trail.verify_integrity()
    assert any("CHAIN_BREAK" in b for b in breaks)


def test_hash_chain_links_sequentially(trail):
    e1 = trail.append("phase1", "decision.open", {}, "t1", trade_id="T1")
    e2 = trail.append("risk", "risk.approved", {}, "t2", trade_id="T1")
    assert e1.prior_hash is None
    assert e2.prior_hash == e1.hash
