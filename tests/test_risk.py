"""Module 5 — Risk & Sizing tests (Phase 3 acceptance T3.1-T3.3, T3.7 + supporting gates)."""
import random

from atom import config, risk
from atom.risk import APPROVED, REJECTED, RESIZED, evaluate, plan_from_paper_order
from atom.phase1 import PaperOrder


def _plan(requested_qty=1, claimed=None, family="bull_put_spread", index="NIFTY",
         legs=None, lot_size=65):
    legs = legs or [("BUY", 24000, "PE", 45.0), ("SELL", 24200, "PE", 120.0)]
    return {"legs": legs, "requested_qty": requested_qty, "variant": "full",
            "claimed_max_loss_per_lot": claimed, "family_id": family, "index": index,
            "lot_size": lot_size}


def _account(capital=200000, deployed=0, realized_pnl_today=0, reserved_risk_open=0,
            open_count=0, trades_today=0, peak_equity=200000, current_equity=200000,
            reentries=None, duplicate=False):
    return {"capital": capital, "deployed": deployed, "realized_pnl_today": realized_pnl_today,
            "reserved_risk_open": reserved_risk_open, "open_count": open_count,
            "trades_today": trades_today, "peak_equity": peak_equity,
            "current_equity": current_equity,
            "reentries_today_by_family": reentries or {}, "duplicate_suspected": duplicate}


CFG = dict(config.DEFAULTS)


# ---- 5.1 Input validation -----------------------------------------------------

def test_missing_required_field_rejects():
    p = _plan(); del p["requested_qty"]
    v = evaluate(p, _account(), CFG)
    assert v.verdict == REJECTED and "INPUT_INCOMPLETE" in v.reasons


def test_negative_qty_rejects():
    v = evaluate(_plan(requested_qty=-1), _account(), CFG)
    assert v.verdict == REJECTED and "INPUT_INVALID" in v.reasons


def test_deployed_exceeding_capital_state_inconsistent():
    v = evaluate(_plan(), _account(capital=100000, deployed=200000), CFG)
    assert v.verdict == REJECTED and "STATE_INCONSISTENT" in v.reasons


# ---- 5.2 Max-loss derivation (never trust the plan's claim) -------------------

def test_max_loss_derived_from_geometry_not_trusted():
    """200-wide vertical, credit 75 → per-lot = 125; claimed figure is ignored/flagged."""
    legs = [("BUY", 24000, "PE", 45.0), ("SELL", 24200, "PE", 120.0)]
    v = evaluate(_plan(legs=legs, claimed=999.0), _account(), CFG)
    assert v.max_loss_per_lot == round((200 - 75) * 65, 2)
    assert "PLAN_UNDERSTATED_RISK" in v.reasons or "PLAN_OVERSTATED_RISK" in v.reasons


def test_naked_leg_undefined_risk_rejected():
    legs = [("SELL", 24200, "PE", 120.0), ("SELL", 24100, "PE", 90.0)]  # no BUY at all
    v = evaluate(_plan(legs=legs), _account(), CFG)
    assert v.verdict == REJECTED and "UNDEFINED_RISK" in v.reasons


def test_credit_exceeding_width_is_implausible():
    legs = [("BUY", 24000, "PE", 5.0), ("SELL", 24200, "PE", 250.0)]  # credit 245 > width 200
    v = evaluate(_plan(legs=legs), _account(), CFG)
    assert v.verdict == REJECTED and "QUOTE_IMPLAUSIBLE" in v.reasons


def test_degenerate_same_strike_rejected():
    legs = [("BUY", 24000, "PE", 45.0), ("SELL", 24000, "PE", 120.0)]
    v = evaluate(_plan(legs=legs), _account(), CFG)
    assert v.verdict == REJECTED and "STRUCTURE_UNSUPPORTED" in v.reasons


# ---- T3.1 Deploy cap -----------------------------------------------------------

def test_deploy_cap_resizes_when_over():
    # ceiling 200k, already reserved 150k, per-lot ~8125 -> headroom fits ~6 lots
    v = evaluate(_plan(requested_qty=20), _account(reserved_risk_open=150000), CFG)
    assert v.verdict == RESIZED
    assert v.permitted_qty * v.max_loss_per_lot <= 200000 - 150000 + 1e-6


def test_deploy_cap_already_breached_rejects_all_new():
    v = evaluate(_plan(), _account(reserved_risk_open=250000), CFG)
    assert v.verdict == REJECTED and "OVER_CAP_ALREADY" in v.reasons


def test_deploy_cap_boundary_inclusive_approve():
    """portfolio_risk_after exactly == ceiling -> APPROVE (<=), per 5.3.1."""
    per_lot_money = (200 - 75) * 65  # 8125
    reserved = 200000 - per_lot_money
    v = evaluate(_plan(requested_qty=1), _account(reserved_risk_open=reserved), CFG)
    assert v.verdict == APPROVED


# ---- T3.2 DD floor --------------------------------------------------------------

def test_dd_floor_blocks_new_entry():
    v = evaluate(_plan(), _account(peak_equity=200000, current_equity=180000), CFG)  # exactly 10% down
    assert v.verdict == REJECTED and "DRAWDOWN_FLOOR_HIT" in v.reasons


def test_dd_floor_ok_above_floor():
    v = evaluate(_plan(), _account(peak_equity=200000, current_equity=185000), CFG)
    assert "DRAWDOWN_FLOOR_HIT" not in v.reasons


# ---- Daily loss cap -------------------------------------------------------------

def test_daily_loss_cap_hit_rejects():
    v = evaluate(_plan(), _account(realized_pnl_today=-20000), CFG)
    assert v.verdict == REJECTED and "DAILY_LOSS_CAP_HIT" in v.reasons


# ---- 5.7 Count gates ------------------------------------------------------------

def test_reentry_limit_hit():
    v = evaluate(_plan(family="bull_put_spread"),
                 _account(reentries={"bull_put_spread": 2}), CFG)
    assert v.verdict == REJECTED and "REENTRY_LIMIT_HIT" in v.reasons


def test_reentry_under_limit_passes():
    v = evaluate(_plan(family="bull_put_spread"),
                 _account(reentries={"bull_put_spread": 1}), CFG)
    assert "REENTRY_LIMIT_HIT" not in v.reasons


def test_max_concurrent_single_position_mode():
    v = evaluate(_plan(), _account(open_count=1), CFG)  # risk.concurrent.max default 1
    assert v.verdict == REJECTED and "MAX_CONCURRENT_HIT" in v.reasons


def test_daily_trade_limit_hit():
    v = evaluate(_plan(), _account(trades_today=10), CFG)
    assert v.verdict == REJECTED and "DAILY_TRADE_LIMIT_HIT" in v.reasons


def test_duplicate_suspected_rejects():
    v = evaluate(_plan(), _account(duplicate=True), CFG)
    assert v.verdict == REJECTED and "DUPLICATE_SUSPECTED" in v.reasons


# ---- 5.5 Sizing reconciliation --------------------------------------------------

def test_reconciliation_takes_binding_minimum():
    # tiny capital forces margin/deployment to bind well below at-risk ceiling
    v = evaluate(_plan(requested_qty=5), _account(capital=20000), CFG)
    assert v.permitted_qty < 5
    assert v.verdict in (RESIZED, REJECTED)


def test_clean_approve_when_everything_fits():
    v = evaluate(_plan(requested_qty=1), _account(), CFG)
    assert v.verdict == APPROVED and v.reasons == ("APPROVED_CLEAN",)


# ---- T3.7 Non-override / determinism -------------------------------------------

def test_no_force_field_exists_to_honor():
    """The plan/account dicts have no force/override field in the contract at all —
    even if a caller stuffs one in, evaluate() never reads it."""
    p = _plan(); p["force_approve"] = True
    a = _account(reserved_risk_open=999999999)   # deliberately over cap
    v = evaluate(p, a, CFG)
    assert v.verdict == REJECTED   # the extra field changed nothing


def test_deterministic_same_inputs_same_verdict():
    p, a = _plan(requested_qty=3), _account()
    v1, v2 = evaluate(p, a, CFG), evaluate(p, a, CFG)
    assert v1 == v2


def test_missing_config_reference_fails_closed():
    p = _plan(lot_size=0)
    v = evaluate(p, _account(), CFG)
    assert v.verdict == REJECTED and "REFERENCE_MISSING" in v.reasons


# ---- Adapter: real PaperOrder -> plan -------------------------------------------

def test_plan_from_paper_order_roundtrips():
    order = PaperOrder(structure="bull_put_spread",
                       legs=(("BUY", 24000, "PE", 45.0), ("SELL", 24200, "PE", 120.0)),
                       net_credit=75.0 * 65, max_loss=125.0 * 65, lot=65,
                       expiry="07-JUL-2026", tsyms=(), index="NIFTY")
    plan = plan_from_paper_order(order)
    v = evaluate(plan, _account(), CFG)
    assert v.verdict == APPROVED


# ---- T3.3 Risk invariant (property test, stdlib random — no hypothesis dep) ----

def test_property_no_approved_path_breaches_any_hard_limit():
    rng = random.Random(20260705)
    atrisk_ceiling = CFG["risk.deploy.inr"]
    dd_floor_pct = CFG["risk.dd.floor.pct"] / 100.0
    daily_cap = CFG["risk.daily_loss.inr"]
    reentry_max = CFG["risk.reentry.max"]
    concurrent_max = CFG["risk.concurrent.max"]

    for _ in range(500):
        short_k = rng.choice([23800, 24000, 24200, 24500])
        width = rng.choice([50, 100, 150, 200, 300])
        long_k = short_k - width
        short_p = rng.uniform(20, 300)
        long_p = short_p * rng.uniform(0.1, 0.95)   # keep credit < width usually
        legs = [("BUY", long_k, "PE", round(long_p, 2)),
                ("SELL", short_k, "PE", round(short_p, 2))]
        qty = rng.randint(1, 50)
        capital = rng.uniform(20000, 500000)
        deployed = rng.uniform(0, capital)
        reserved = rng.uniform(0, atrisk_ceiling * 1.5)
        peak = rng.uniform(capital, capital * 1.3)
        equity = rng.uniform(peak * 0.7, peak * 1.05)
        realized = rng.uniform(-daily_cap * 1.5, daily_cap)
        open_count = rng.randint(0, 3)
        reentries = rng.randint(0, reentry_max + 2)

        p = _plan(requested_qty=qty, legs=legs)
        a = _account(capital=capital, deployed=deployed, reserved_risk_open=reserved,
                    peak_equity=peak, current_equity=equity, realized_pnl_today=realized,
                    open_count=open_count, reentries={"bull_put_spread": reentries})
        v = evaluate(p, a, CFG)

        if v.verdict in (APPROVED, RESIZED):
            assert v.permitted_qty >= 1
            assert reserved + v.max_loss_total <= atrisk_ceiling + 1e-6
            assert equity > peak * (1 - dd_floor_pct) - 1e-6
            assert realized > -daily_cap - 1e-6
            assert reentries < reentry_max
            assert open_count < concurrent_max
        else:
            assert v.permitted_qty == 0


# ---- Real broker margin sanity gate (optional, only when supplied) ------------

def test_broker_margin_absent_key_unaffected():
    """Existing accounts (no broker_margin_available key at all) are completely
    unaffected — the gate is opt-in per-account, not a global default."""
    v = evaluate(_plan(), _account(), CFG)
    assert v.verdict == APPROVED


def test_broker_margin_low_hard_blocks():
    account = {**_account(), "broker_margin_available": True, "broker_free_margin": 1000.0,
              "broker_margin_reason": None}
    v = evaluate(_plan(), account, CFG)
    assert v.verdict == REJECTED and "BROKER_MARGIN_LOW" in v.reasons


def test_broker_margin_sufficient_passes():
    account = {**_account(), "broker_margin_available": True, "broker_free_margin": 579918.15,
              "broker_margin_reason": None}
    v = evaluate(_plan(), account, CFG)
    assert v.verdict == APPROVED


def test_broker_margin_unknown_is_informational_not_a_hard_block():
    """Stale/missing broker data must NOT block trading — it's a secondary signal;
    the existing gates (capital/deployment/margin) are the real safety net."""
    account = {**_account(), "broker_margin_available": False, "broker_free_margin": None,
              "broker_margin_reason": "STALE"}
    v = evaluate(_plan(), account, CFG)
    assert v.verdict == APPROVED
    assert any("BROKER_MARGIN_UNKNOWN" in r for r in v.reasons)
