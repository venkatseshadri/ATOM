#!/usr/bin/env python3
"""Phase 4 — narrated expected-vs-actual demo, per module (16/14/15).

Prints GIVEN/WHEN/EXPECT/ACTUAL for the key scenarios — a human-readable companion to
the raw pytest logs in logs/phase4/*.log. Same pattern as tools/phase3_demo_log.py.
"""
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "src")

from atom import audit as audit_mod, config, config_freeze, ledger as ledger_mod

RULE = "=" * 78


def line(given, when, expect, actual, match=None):
    ok = "OK" if match is None or match else "MISMATCH"
    print(f"  GIVEN  {given}")
    print(f"  WHEN   {when}")
    print(f"  EXPECT {expect}")
    print(f"  ACTUAL {actual}   [{ok}]\n")


def module16():
    print(f"\n{RULE}\nMODULE 16 — CONFIG & PARAMETERSET\n{RULE}\n")
    with tempfile.TemporaryDirectory() as d:
        store = config_freeze.ConfigFreezeStore(str(Path(d) / "params.sqlite"))
        clean = {"risk.sl.pct": 35}
        ps1, _ = store.freeze_for_session("2026-07-06", clean, "2026-07-06T09:15:00")
        line("clean config, first freeze of the day", "freeze_for_session()",
            f"v{1} frozen, sl.pct=35", f"{ps1.version}, sl.pct={ps1.params['risk.sl.pct']}",
            match=(ps1.params["risk.sl.pct"] == 35))

        edited = {"risk.sl.pct": 999}
        ps2, _ = store.freeze_for_session("2026-07-06", edited, "2026-07-06T11:00:00")
        line("mid-day edit to sl.pct=999 attempted", "freeze_for_session() (same day)",
            "SAME version, edit ignored, sl.pct still 35",
            f"{ps2.version}, sl.pct={ps2.params['risk.sl.pct']}",
            match=(ps1.version == ps2.version and ps2.params["risk.sl.pct"] == 35))

        bad = {"risk.sl.pct": 500}
        ps3, violations = store.freeze_for_session("2026-07-07", bad, "2026-07-07T09:15:00")
        line("out-of-bounds candidate (sl.pct=500, max 100)", "freeze_for_session()",
            "REJECTED, OUT_OF_BOUNDS", f"{ps3} {violations}",
            match=(ps3 is None and any("OUT_OF_BOUNDS" in v for v in violations)))


def module14():
    print(f"\n{RULE}\nMODULE 14 — LEDGER & PERSISTENCE\n{RULE}\n")
    legs = (ledger_mod.Leg("NIFTY24000PE", -65, 11.0),)

    r = ledger_mod.compute_pnl(legs, {"NIFTY24000PE": 9.0}, realized=0.0, lot_size=1)
    line("short 65 @ entry 11, mark decays to 9", "compute_pnl()",
        f"unrealized = (11-9)*65 = {(11.0-9.0)*65}", f"unrealized={r.unrealized}",
        match=(r.unrealized == (11.0 - 9.0) * 65))

    r2 = ledger_mod.compute_pnl(legs, {"NIFTY24000PE": None}, realized=0.0, lot_size=1)
    line("mark is missing/stale", "compute_pnl()",
        "unrealized=None, confidence=LOW_CONFIDENCE (never a fake zero)",
        f"unrealized={r2.unrealized}, confidence={r2.confidence}",
        match=(r2.unrealized is None and r2.confidence == "LOW_CONFIDENCE"))

    d = ledger_mod.reconcile(legs, {"NIFTY24000PE": -130})
    line("ledger short 65, broker reports short 130", "reconcile()",
        "MISSED_FILL, ledger untouched", f"{d[0].classification}, ledger still {legs[0].signed_qty}",
        match=(d[0].classification == ledger_mod.MISSED_FILL and legs[0].signed_qty == -65))

    eod = ledger_mod.finalize_eod((ledger_mod.Leg("NIFTY24000PE", -65, 11.0),), (100.0,))
    line("one leg still open at EOD", "finalize_eod()",
        "flat=False, alarm=NOT_FLAT_AT_EOD", f"flat={eod.flat}, alarm={eod.alarm}",
        match=(not eod.flat and eod.alarm == "NOT_FLAT_AT_EOD"))


def module15():
    print(f"\n{RULE}\nMODULE 15 — TELEMETRY & AUDIT\n{RULE}\n")
    with tempfile.TemporaryDirectory() as d:
        trail = audit_mod.AuditTrail(str(Path(d) / "audit.sqlite"))
        trail.append("phase1", "decision.open", {"regime": "TREND_DOWN"}, "t1", trade_id="T1")
        trail.append("risk", "risk.approved", {"qty": 1}, "t2", trade_id="T1")
        trail.append("stop_management", "position.tsl_trigger", {"pnl": 2200}, "t3", trade_id="T1")

        events = trail.reconstruct_trade("T1")
        line("3 events logged for trade T1", "reconstruct_trade()",
            "decision.open, risk.approved, position.tsl_trigger (in order)",
            [e.type for e in events],
            match=([e.type for e in events] == ["decision.open", "risk.approved",
                                                "position.tsl_trigger"]))

        c = trail._c()
        c.execute("UPDATE decision_trace SET payload_json='{\"regime\":\"TAMPERED\"}' "
                 "WHERE type='decision.open'")
        c.commit(); c.close()
        breaks = trail.verify_integrity()
        line("decision.open payload tampered after the fact", "verify_integrity()",
            "TAMPERED event detected", breaks, match=any("TAMPERED" in b for b in breaks))


if __name__ == "__main__":
    module16()
    module14()
    module15()
