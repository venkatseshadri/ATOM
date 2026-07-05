#!/usr/bin/env python3
"""Phase 3 — narrated expected-vs-actual demo, per module (modules 7/5/6 so far).

Prints GIVEN/WHEN/EXPECT/ACTUAL for the key scenarios in each module's testcases.md —
a human-readable companion to the raw pytest logs in logs/phase3/*.log.
"""
import sys
from datetime import date, datetime

sys.path.insert(0, "src")

from atom import config, risk, session_lifecycle as sl, stop_management as sm

RULE = "=" * 78


def line(given, when, expect, actual, match=None):
    """`match`: explicit bool from the caller (a real equality check on the actual
    typed values) — avoids comparing pretty-printed EXPECT text against a differently
    -formatted ACTUAL repr, which produced false MISMATCH labels in an earlier draft."""
    ok = "OK" if match is None or match else "MISMATCH"
    print(f"  GIVEN  {given}")
    print(f"  WHEN   {when}")
    print(f"  EXPECT {expect}")
    print(f"  ACTUAL {actual}   [{ok}]\n")


def module7():
    print(f"\n{RULE}\nMODULE 7 — SESSION LIFECYCLE\n{RULE}\n")
    cal = sl.MarketCalendar(holidays_path="tests/fixtures/holidays_fixture.json")

    d = date(2026, 7, 6)
    line("2026-07-06 (Monday, in-calendar year)", "day_type() called",
        sl.DAY_FULL_TRADING, cal.day_type(d))

    d2 = date(2026, 10, 2)
    line("2026-10-02 (Friday, listed holiday in fixture)", "day_type() called",
        sl.DAY_HOLIDAY, cal.day_type(d2))

    dl = cal.deadlines(d)
    open_, close = cal.session_times(d)
    line("close=15:30, squareoff offset=12m", "deadlines() computed",
        "squareoff_start=15:18", f"squareoff_start={dl.squareoff_start.strftime('%H:%M')}")

    start, cutoff = sl.entry_window(open_, dl)
    now = datetime(2026, 7, 6, 14, 29, 59)
    line(f"cutoff={cutoff.strftime('%H:%M')}, now=14:29:59", "is_entry_allowed()",
        True, sl.is_entry_allowed(now, start, cutoff))
    now2 = datetime(2026, 7, 6, 14, 30, 0)
    line(f"cutoff={cutoff.strftime('%H:%M')}, now=14:30:00", "is_entry_allowed()",
        False, sl.is_entry_allowed(now2, start, cutoff))

    lvl = sl.square_off_level(dl.hard_flat_deadline, dl, is_flat=False)
    line("now=hard_flat_deadline, position still open", "square_off_level()",
        3, lvl)


def module5():
    print(f"\n{RULE}\nMODULE 5 — RISK & SIZING\n{RULE}\n")
    cfg = dict(config.DEFAULTS)
    plan = {"legs": [("BUY", 24000, "PE", 45.0), ("SELL", 24200, "PE", 120.0)],
           "requested_qty": 1, "variant": "full", "claimed_max_loss_per_lot": None,
           "family_id": "bull_put_spread", "index": "NIFTY", "lot_size": 65}
    account = {"capital": 200000, "deployed": 0, "realized_pnl_today": 0,
              "reserved_risk_open": 0, "open_count": 0, "trades_today": 0,
              "peak_equity": 200000, "current_equity": 200000,
              "reentries_today_by_family": {}, "duplicate_suspected": False}
    v = risk.evaluate(plan, account, cfg)
    line("200-wide vertical, credit 75, clean account", "evaluate()",
        "APPROVED qty=1", f"{v.verdict} qty={v.permitted_qty}")

    account2 = {**account, "realized_pnl_today": -20000}
    v2 = risk.evaluate(plan, account2, cfg)
    line("realized_pnl_today = -20000 (== daily loss cap)", "evaluate()",
        "REJECTED DAILY_LOSS_CAP_HIT", f"{v2.verdict} {v2.reasons}",
        match=(v2.verdict == "REJECTED" and "DAILY_LOSS_CAP_HIT" in v2.reasons))

    account3 = {**account, "reserved_risk_open": 150000}
    v3 = risk.evaluate({**plan, "requested_qty": 20}, account3, cfg)
    line("reserved=150k, ceiling=200k, requested=20 lots", "evaluate()",
        "RESIZED (fits headroom only, < 20)", f"{v3.verdict} qty={v3.permitted_qty}",
        match=(v3.verdict == "RESIZED" and 0 < v3.permitted_qty < 20))

    account4 = {**account, "reentries_today_by_family": {"bull_put_spread": 2}}
    v4 = risk.evaluate(plan, account4, cfg)
    line("re-entry count=2, max=2", "evaluate()",
        "REJECTED REENTRY_LIMIT_HIT", f"{v4.verdict} {v4.reasons}",
        match=(v4.verdict == "REJECTED" and "REENTRY_LIMIT_HIT" in v4.reasons))

    # T3.7 non-override
    plan_forced = {**plan, "force_approve": True}
    account5 = {**account, "reserved_risk_open": 999999999}
    v5 = risk.evaluate(plan_forced, account5, cfg)
    line("force_approve=True stuffed into plan, way over cap", "evaluate()",
        "REJECTED (force field ignored)", f"{v5.verdict}",
        match=(v5.verdict == "REJECTED"))


def module6():
    print(f"\n{RULE}\nMODULE 6 — STOP MANAGEMENT\n{RULE}\n")
    cfg = dict(config.DEFAULTS)
    net_credit, max_loss = 5000.0, 9000.0
    lv = sm.initial_levels(net_credit, max_loss, cfg)
    line("credit=5000, max_loss=9000, sl.pct=35, tp.pct=50", "initial_levels()",
        f"sl={-round(0.35*max_loss,2)} tp={round(0.5*net_credit,2)}",
        f"sl={lv.sl} tp={lv.tp}")

    lv2 = sm.update_levels(lv, 0.30 * net_credit, net_credit, cfg)
    line("pnl reaches 30% of credit (activation)", "update_levels()",
        "tsl_armed=True", f"tsl_armed={lv2.tsl_armed}")

    lv3 = sm.update_levels(lv2, 0.80 * net_credit, net_credit, cfg)
    tight = lv3.tsl
    lv4 = sm.update_levels(lv3, 0.35 * net_credit, net_credit, cfg)
    line("profit runs to 80% then reverses to 35%", "update_levels() ratchet",
        f"tsl stays {tight} (never loosens)", f"tsl={lv4.tsl}", match=(lv4.tsl == tight))

    t = sm.check_breach(100.0, lv, is_eod=True, net_credit_money=net_credit, cfg=cfg)
    line("is_eod=True, pnl mid-range (would not otherwise trigger)", "check_breach()",
        "TIME (unconditional)", t.reason, match=(t.reason == "TIME"))


if __name__ == "__main__":
    module7()
    module5()
    module6()
