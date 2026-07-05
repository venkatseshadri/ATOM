"""Phase 3 — full paper-trade lifecycle integration test (GATE 3's own criterion:
"Board reviews full paper trade lifecycle + risk invariants").

Chains all 5 Phase 3 modules together over a REAL Phase 1/2 construction (real Penguin
chain data, real PaperOrder) — decision -> risk-gated -> placed (paper fill) -> stops
managed -> EOD square-off. Per-module unit tests already cover each module's own leaf
behavior (test_risk.py, test_stop_management.py, test_session_lifecycle.py,
test_order_execution.py, test_connectivity.py) — this file proves they compose correctly,
which no single module's tests can show alone.

Deliberately NOT wired into runner.py/cron yet in this pass — same phased-rollout
discipline as Phase 2's Module 12/4 (built + proven, live-wiring is a separate,
reviewable step). See PHASE-3-TECHNICAL.md.
"""
import os
from datetime import date

from atom import config, order_execution as oe, phase1, risk
from atom import session_lifecycle as sl
from atom import stop_management as sm
from atom.instrument import InstrumentMaster
from atom.penguin import PenguinReader
from atom.telemetry import Telemetry

FIX_NIFTY = os.path.join(os.path.dirname(__file__), "fixtures", "capture_nifty_fixture.sqlite")
FIX_MASTER = os.path.join(os.path.dirname(__file__), "fixtures", "scrip_master_fixture.duckdb")


def _im():
    return InstrumentMaster(Telemetry(echo=False), db_path=FIX_MASTER)


def _clean_account(**overrides):
    base = {"capital": 200000, "deployed": 0, "realized_pnl_today": 0,
           "reserved_risk_open": 0, "open_count": 0, "trades_today": 0,
           "peak_equity": 200000, "current_equity": 200000,
           "reentries_today_by_family": {}, "duplicate_suspected": False}
    return {**base, **overrides}


def test_full_lifecycle_open_gate_fill_stops_squareoff():
    """T3.1/T3.4/T3.5 composed: a real construction clears the risk gate, fills on
    real chain data with protective-first sequencing, gets tracked stops that ratchet
    correctly, and is force-flattened at square-off."""
    snap = PenguinReader(FIX_NIFTY).latest_snapshot()
    order = phase1.build_order("bear_call_spread", snap, im=_im())
    assert order is not None, "fixture must produce a real, real-priced structure"

    # ---- Module 5: risk gate ----
    plan = risk.plan_from_paper_order(order)
    verdict = risk.evaluate(plan, _clean_account())
    assert verdict.verdict == risk.APPROVED
    assert verdict.permitted_qty == 1

    # ---- Module 13: sequence + paper-fill each leg against the REAL chain ----
    requests = []
    for i, (action, strike, right, _entry) in enumerate(order.legs):
        protective = (action == "BUY")
        requests.append(oe.OrderRequest(
            leg_id=f"leg{i}", tradingsymbol=order.tsyms[i] if order.tsyms else f"{strike}{right}",
            action=action, qty=order.lot, order_type="MARKET", limit_price=None,
            tick_size=0.05, lot_size=order.lot,
            client_order_id=oe.make_client_order_id("plan1", f"leg{i}"), protective=protective))
    sequenced = oe.sequence_legs(requests)
    assert sequenced[0].protective is True   # hedge (BUY) goes first, never naked

    fills = []
    for action, strike, right, _entry in order.legs:
        req = next(r for r in sequenced if r.action == action)
        f = oe.submit_paper(req, snap.chain, strike, right)
        fills.append(f)
    assert all(f.status == oe.FILLED for f in fills), f"expected real fills, got {fills}"
    leg_in = oe.detect_leg_in(list(zip(sequenced, fills)), planned_leg_count=len(order.legs))
    assert leg_in is None   # both legs filled -> no structural incompleteness

    # ---- Module 6: initial levels from the REAL net_credit/max_loss ----
    cfg = dict(config.DEFAULTS)
    lv = sm.initial_levels(order.net_credit, order.max_loss, cfg)
    assert lv.sl < 0 < lv.tp

    # simulate a favorable run then a partial reversal — TSL must never loosen
    lv2 = sm.update_levels(lv, 0.60 * order.net_credit, order.net_credit, cfg)
    tight = lv2.tsl
    lv3 = sm.update_levels(lv2, 0.40 * order.net_credit, order.net_credit, cfg)
    assert lv3.tsl == tight

    # ---- Module 7: square-off authority forces flat at end of day ----
    cal_dl = sl.MarketCalendar(holidays_path=os.path.join(
        os.path.dirname(__file__), "fixtures", "holidays_fixture.json")).deadlines(date(2026, 7, 6))
    level = sl.square_off_level(cal_dl.hard_flat_deadline, cal_dl, is_flat=False)
    assert level == 3   # hard/market escalation at the deadline

    # ---- Module 13 again: exit orders reverse each leg, forced MARKET ----
    exit_orders = oe.exit_orders_from_position(order.legs, order.tsyms, order.lot, 0.05, "plan1")
    assert len(exit_orders) == len(order.legs)
    assert all(o.order_type == "MARKET" for o in exit_orders)
    for original, exit_o in zip(order.legs, exit_orders):
        assert exit_o.action != original[0]   # reverses the original side


def test_full_lifecycle_risk_gate_blocks_before_any_fill_attempted():
    """T3.2/T3.7: when the account is already at the drawdown floor, the risk gate
    rejects BEFORE Module 13 ever touches the (real) chain — no fill is attempted on
    a trade that should never have existed."""
    snap = PenguinReader(FIX_NIFTY).latest_snapshot()
    order = phase1.build_order("bear_call_spread", snap, im=_im())
    assert order is not None

    plan = risk.plan_from_paper_order(order)
    account = _clean_account(peak_equity=200000, current_equity=180000)  # exactly 10% DD
    verdict = risk.evaluate(plan, account)
    assert verdict.verdict == risk.REJECTED
    assert "DRAWDOWN_FLOOR_HIT" in verdict.reasons
    assert verdict.permitted_qty == 0   # nothing to fill — the gate stopped it upstream
