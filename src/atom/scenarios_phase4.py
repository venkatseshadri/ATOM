"""Phase-4 PORCUPINE scenarios — fault injection for Modules 14/15/16.

All direct fault-injection (no single unified pipeline path spans all three modules
the way Module 5 did for Phase 3) — each check exercises the real module under an
injected fault. Same two-catalogue spirit as scenarios_phase3.py's DIRECT_CHECKS.
"""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime


def _check_config_freeze_rejects_mid_day_edit() -> bool:
    """Module 16 — the real bug this phase fixed: a mid-day config edit must be
    silently ignored, not applied to the already-frozen day."""
    from atom.config_freeze import ConfigFreezeStore
    with tempfile.TemporaryDirectory() as d:
        store = ConfigFreezeStore(os.path.join(d, "p.sqlite"))
        clean = {"risk.sl.pct": 35}
        ps1, _ = store.freeze_for_session("2026-07-06", clean, "2026-07-06T09:15:00")
        edited = {"risk.sl.pct": 99}
        ps2, _ = store.freeze_for_session("2026-07-06", edited, "2026-07-06T11:00:00")
        return ps1.version == ps2.version and ps2.params["risk.sl.pct"] == 35


def _check_config_rollback_refuses_corrupted_last_known_good() -> bool:
    """Module 16 (16.6.2) — if last-known-good itself now fails validation (schema
    evolved / storage rot), rollback must refuse, never activate something unverified."""
    from atom.config_freeze import ConfigFreezeStore
    with tempfile.TemporaryDirectory() as d:
        store = ConfigFreezeStore(os.path.join(d, "p.sqlite"))
        ps, _ = store.freeze_for_session("2026-07-06", {"risk.sl.pct": 35}, "2026-07-06T09:15:00")
        version = int(ps.version.split(":")[0][1:])
        store.mark_last_known_good(version)
        # corrupt the stored last-known-good directly (simulates storage rot)
        c = store._c()
        c.execute("UPDATE parameter_sets SET params_json='{\"risk.sl.pct\": 500}' WHERE version=?",
                 (version,))
        c.commit(); c.close()
        rolled, reason = store.rollback("2026-07-07", "2026-07-07T09:15:00")
        return rolled is None and reason is not None and "INVALID" in reason


def _check_restart_recovery_mid_position() -> bool:
    """Module 14 (14.6) — a 'crash and restart' with an OPEN position must
    reconstruct fsm_state + position + net_credit correctly from a fresh process."""
    from atom import phase1
    from atom.atom_state import AtomState
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "s.sqlite")
        order = phase1.PaperOrder("bull_put_spread",
                                  (("BUY", 100, "PE", 1.0), ("SELL", 200, "PE", 2.0)),
                                  1000.0, 2000.0, 65, "01-JAN-2099")
        AtomState(db).checkpoint_and_record("SINGLE_SPREAD", "t1", order, "t1",
                                            {"regime": "n/a", "confidence": 0.0})
        recovered = AtomState(db)   # simulates a fresh process after crash
        fsm_state, _ = recovered.load()
        pos = recovered.last_open_position()
        return fsm_state == "SINGLE_SPREAD" and pos is not None and pos["net_credit"] == 1000.0


def _check_reconciliation_flags_missed_fill_without_mutating_ledger() -> bool:
    """Module 14 (14.8) — broker shows more quantity than the ledger (a fill that
    happened during downtime): flagged, ledger never auto-mutated."""
    from atom.ledger import Leg, MISSED_FILL, reconcile
    legs = (Leg("NIFTY24000PE", -65, 100.0),)
    divergences = reconcile(legs, {"NIFTY24000PE": -130})
    return (divergences[0].classification == MISSED_FILL
           and legs[0].signed_qty == -65)   # unchanged — never auto-healed


def _check_eod_finalization_idempotent_under_double_run() -> bool:
    """Module 14 (14.9.2) — re-running EOD finalization for an already-closed day
    must be a safe no-op, never a silent rewrite of the frozen daily figure."""
    from atom.atom_state import AtomState
    with tempfile.TemporaryDirectory() as d:
        state = AtomState(os.path.join(d, "s.sqlite"))
        first = state.finalize_day("2026-07-06", 120.0, True, (), "2026-07-06T15:30:00")
        second = state.finalize_day("2026-07-06", 999999.0, True, (), "2026-07-06T15:31:00")
        stored = state.day_finalization("2026-07-06")
        return first is True and second is False and stored["daily_realized"] == 120.0


def _check_audit_tamper_detected() -> bool:
    """Module 15 (15.8) — a tampered event must be caught by the hash chain, even
    though the row itself still looks structurally valid."""
    from atom.audit import AuditTrail
    with tempfile.TemporaryDirectory() as d:
        trail = AuditTrail(os.path.join(d, "audit.sqlite"))
        trail.append("phase1", "decision.open", {"regime": "TREND_DOWN"}, "t1", trade_id="T1")
        c = trail._c()
        c.execute("UPDATE decision_trace SET payload_json='{\"regime\":\"TAMPERED\"}'")
        c.commit(); c.close()
        breaks = trail.verify_integrity()
        return any("TAMPERED" in b for b in breaks)


def _check_malformed_fill_never_mutates_position() -> bool:
    """Module 14 (14.1.1) — a fill with qty=0 (or off-lot) must quarantine, not
    silently no-op-apply to a position that then looks subtly wrong."""
    from atom.ledger import Leg, apply_fill
    prior = Leg("NIFTY24000PE", -65, 100.0)
    result = apply_fill(prior, "NIFTY24000PE", 10.0, 70, "SELL", 65)  # 70 not a multiple of 65
    return not result.accepted and result.leg == prior


DIRECT_CHECKS = [
    ("P4_config_freeze_rejects_mid_day_edit",
     "Module 16: a mid-day config edit is silently ignored, not applied",
     _check_config_freeze_rejects_mid_day_edit),
    ("P4_config_rollback_refuses_corrupted_lkg",
     "Module 16: rollback refuses a corrupted last-known-good rather than activating it",
     _check_config_rollback_refuses_corrupted_last_known_good),
    ("P4_restart_recovery_mid_position",
     "Module 14: a fresh process after crash reconstructs the open position correctly",
     _check_restart_recovery_mid_position),
    ("P4_reconciliation_flags_missed_fill",
     "Module 14: broker/ledger divergence is flagged, ledger never auto-mutated",
     _check_reconciliation_flags_missed_fill_without_mutating_ledger),
    ("P4_eod_finalization_idempotent",
     "Module 14: re-running EOD finalization twice never rewrites the frozen figure",
     _check_eod_finalization_idempotent_under_double_run),
    ("P4_audit_tamper_detected",
     "Module 15: a tampered audit event is caught by the hash chain",
     _check_audit_tamper_detected),
    ("P4_malformed_fill_quarantined",
     "Module 14: an off-lot-size fill is quarantined, never silently applied",
     _check_malformed_fill_never_mutates_position),
]
