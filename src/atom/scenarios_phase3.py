"""Phase-3 PORCUPINE scenarios — fault injection + invariant checks for Modules 5/6/7/11,
closing the gap `docs/PORCUPINE.md` flagged as "still open" for Phase 3.

Two catalogues:
- `SCENARIOS` drive the REAL pipeline (`runner.run_once(use_tsl=True, risk_gate=True)`)
  against the shared fixture DB, seeding `paper_trades` rows to engineer specific account
  states (daily-loss cap hit, re-entry limit hit, tiny capital) — same technique
  `scenarios_phase1.py` uses for FSM/freshness faults, extended to Module 5's gate.
- `DIRECT_CHECKS` exercise Modules 6/7/11 as pure-function fault scenarios (bad tick,
  halted market at square-off, stale broker margin) — these aren't wired into
  `run_once`'s gating yet (see PHASE-3-TECHNICAL.md), so there's no pipeline path to
  drive them through; testing the functions directly is the honest equivalent.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class Expect:
    action: str
    reason: str | None = None
    fsm_state_after: str | None = None
    paper_trades_count_after: int | None = None
    risk_verdict: str | None = None          # APPROVED | RESIZED | REJECTED
    risk_reason_contains: str | None = None  # substring check against reasons


@dataclass(frozen=True)
class SeedTrade:
    """A closed trade to pre-seed into paper_trades, dated TODAY (the fixture bar's own
    date) so Module 5's derive_account() daily-scoped queries pick it up."""
    structure: str
    realized_pnl: float


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    expect_desc: str
    config_overrides: dict = field(default_factory=dict)
    seed_trades: tuple[SeedTrade, ...] = ()
    capital: float = 200000.0
    expect: Expect = None


SCENARIOS = [
    Scenario(
        name="P3_risk_gate_approves_clean_entry",
        title="Ample capital, clean account — risk gate approves what Phase 1 would open",
        expect_desc="positive control: risk_gate=True must not block a trade that would "
                    "have opened anyway",
        config_overrides={"regime.entry.min_confidence": 0.0},
        expect=Expect(action="OPEN", fsm_state_after="SINGLE_SPREAD",
                     paper_trades_count_after=1, risk_verdict="APPROVED"),
    ),
    Scenario(
        name="P3_risk_gate_blocks_tiny_capital",
        title="Capital too small for even 1 lot — REJECTED, no trade recorded",
        expect_desc="capital=100 forces every cap (margin/deployment/at-risk) to reject; "
                    "OPEN must downgrade to STAND_DOWN with zero paper_trades written",
        config_overrides={"regime.entry.min_confidence": 0.0},
        capital=100.0,
        expect=Expect(action="STAND_DOWN", fsm_state_after="FLAT",
                     paper_trades_count_after=0, risk_verdict="REJECTED"),
    ),
    Scenario(
        name="P3_risk_gate_blocks_daily_loss_cap",
        title="Daily loss cap already hit earlier today — new entry blocked regardless of size",
        expect_desc="seed a closed trade with realized_pnl = -daily_loss cap; even with "
                    "ample capital, DAILY_LOSS_CAP_HIT rejects the new OPEN",
        config_overrides={"regime.entry.min_confidence": 0.0},
        seed_trades=(SeedTrade("prior_loss", -20000.0),),
        expect=Expect(action="STAND_DOWN", fsm_state_after="FLAT",
                     paper_trades_count_after=1,  # only the seeded trade — new one blocked
                     risk_verdict="REJECTED", risk_reason_contains="DAILY_LOSS_CAP_HIT"),
    ),
    Scenario(
        name="P3_risk_gate_blocks_reentry_limit",
        title="Same-family re-entry limit already hit today — new entry of that family blocked",
        expect_desc="seed 2 prior CLOSED bear_call_spread trades today (risk.reentry.max=2); "
                    "the fixture's own regime forces a bear_call_spread OPEN attempt, which "
                    "must now be rejected on REENTRY_LIMIT_HIT",
        config_overrides={"regime.entry.min_confidence": 0.0},
        seed_trades=(SeedTrade("bear_call_spread", 500.0), SeedTrade("bear_call_spread", 500.0)),
        expect=Expect(action="STAND_DOWN", fsm_state_after="FLAT",
                     paper_trades_count_after=2,
                     risk_verdict="REJECTED", risk_reason_contains="REENTRY_LIMIT_HIT"),
    ),
]


# ---- Direct fault-injection checks (Modules 6/7/11 — not wired into run_once's gating,
# so there is no pipeline path to drive them through; testing the functions directly
# under an injected fault is the honest equivalent). Each entry is (name, title, fn)
# where fn() returns bool. ----------------------------------------------------------

def _check_bad_tick_does_not_poison_tsl() -> bool:
    """Module 6 — a single implausible print (50x the credit collected) must not
    permanently disable the stop (PORCUPINE-caught 2026-07-05, fixed same session)."""
    from . import config as _cfg
    from . import stop_management as sm
    cfg = dict(_cfg.DEFAULTS)
    net_credit, max_loss = 5000.0, 9000.0
    lv = sm.initial_levels(net_credit, max_loss, cfg)
    lv = sm.update_levels(lv, 50 * net_credit, net_credit, cfg)   # bad tick
    lv = sm.update_levels(lv, 0.30 * net_credit, net_credit, cfg)  # reverts to sane
    return lv.tsl <= net_credit and lv.tsl > 0   # a real, reachable, positive floor


def _check_halted_market_at_squareoff_is_unresolved_not_flat() -> bool:
    """Module 7 — a halt persisting into the square-off window must escalate as
    unresolved, never be silently treated as a normal (successful) square-off."""
    from datetime import date
    from . import session_lifecycle as sl
    cal = sl.MarketCalendar(holidays_path=os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "tests", "fixtures", "holidays_fixture.json"))
    dl = cal.deadlines(date(2026, 7, 6))
    level = sl.square_off_level(dl.squareoff_start, dl, is_flat=False, halted=True)
    status = sl.square_off_status(dl.squareoff_start, dl, is_flat=False, halted=True)
    return level == -1 and status == "HALTED_UNRESOLVED"


def _check_stale_broker_margin_is_informational_not_blocking() -> bool:
    """Module 11/5 — a stale broker_limits.json must not block trading; it's a
    secondary signal, the capital/deployment gates are the real safety net."""
    from . import risk
    plan = {"legs": [("BUY", 24000, "PE", 45.0), ("SELL", 24200, "PE", 120.0)],
           "requested_qty": 1, "variant": "full", "claimed_max_loss_per_lot": None,
           "family_id": "bull_put_spread", "index": "NIFTY", "lot_size": 65}
    account = {"capital": 200000, "deployed": 0, "realized_pnl_today": 0,
              "reserved_risk_open": 0, "open_count": 0, "trades_today": 0,
              "peak_equity": 200000, "current_equity": 200000,
              "reentries_today_by_family": {}, "duplicate_suspected": False,
              "broker_margin_available": False, "broker_free_margin": None,
              "broker_margin_reason": "STALE"}
    v = risk.evaluate(plan, account)
    return v.verdict == "APPROVED"


DIRECT_CHECKS = [
    ("P3_direct_bad_tick_tsl_guard", "Bad tick doesn't permanently disable the TSL",
     _check_bad_tick_does_not_poison_tsl),
    ("P3_direct_halted_squareoff", "Halted market at square-off escalates, never 'flat'",
     _check_halted_market_at_squareoff_is_unresolved_not_flat),
    ("P3_direct_stale_margin_informational", "Stale broker margin doesn't block trading",
     _check_stale_broker_margin_is_informational_not_blocking),
]
