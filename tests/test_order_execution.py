"""Module 13 — Order/Execution tests (Phase 3 acceptance T3.6 partial fill + supporting)."""
from atom.order_execution import (
    COMPLETE, FILLED, REJECTED, TERMINAL, TRANSIENT, UNWIND, OrderRequest,
    classify_reject, detect_leg_in, exit_orders_from_position, is_duplicate_submit,
    make_client_order_id, remaining_qty, round_to_tick, sequence_legs, submit_paper,
    validate_order,
)


def _order(leg_id="l1", action="SELL", qty=65, protective=False, order_type="MARKET",
          limit_price=None, tick=0.05):
    return OrderRequest(leg_id=leg_id, tradingsymbol="NIFTY07JUL26P24200", action=action,
                        qty=qty, order_type=order_type, limit_price=limit_price,
                        tick_size=tick, lot_size=65, client_order_id="cid1",
                        protective=protective)


# ---- 13.1 Validation + tick rounding -------------------------------------------

def test_round_to_tick_sell_rounds_down():
    assert round_to_tick(100.13, 0.05, "SELL") == 100.10


def test_round_to_tick_buy_rounds_up():
    assert round_to_tick(100.11, 0.05, "BUY") == 100.15


def test_validate_rejects_off_lot_qty():
    assert validate_order("SELL", 70, 65, "MARKET", None, 0.05) == "INVALID_LOT_QTY"


def test_validate_rejects_limit_without_price():
    assert validate_order("SELL", 65, 65, "LIMIT", None, 0.05) == "INVALID_PLAN"


def test_validate_clean_order_passes():
    assert validate_order("SELL", 65, 65, "MARKET", None, 0.05) is None


# ---- 13.3 Idempotency -----------------------------------------------------------

def test_client_order_id_deterministic():
    assert make_client_order_id("plan1", "leg1") == make_client_order_id("plan1", "leg1")


def test_client_order_id_differs_per_leg():
    assert make_client_order_id("plan1", "leg1") != make_client_order_id("plan1", "leg2")


def test_duplicate_submit_detected():
    seen = {"abc123"}
    assert is_duplicate_submit(seen, "abc123") is True
    assert is_duplicate_submit(seen, "xyz999") is False


# ---- 13.2 Sequencing + leg-in detection -----------------------------------------

def test_sequence_puts_protective_legs_first():
    short = _order("short", protective=False)
    hedge = _order("hedge", protective=True)
    ordered = sequence_legs([short, hedge])
    assert ordered[0].leg_id == "hedge"
    assert ordered[1].leg_id == "short"


def test_leg_in_naked_short_triggers_complete():
    from atom.order_execution import Fill
    short = _order("short", protective=False)
    hedge = _order("hedge", protective=True)
    pairs = [
        (short, Fill("short", "c1", FILLED, 65, 120.0, 120.0, 0.0)),
        (hedge, Fill("hedge", "c2", REJECTED, 0, None, None, None, "NO_LIQUIDITY")),
    ]
    assert detect_leg_in(pairs, planned_leg_count=2) == COMPLETE


def test_leg_in_missing_short_triggers_unwind():
    from atom.order_execution import Fill
    hedge = _order("hedge", protective=True)
    short = _order("short", protective=False)
    pairs = [
        (hedge, Fill("hedge", "c1", FILLED, 65, 45.0, 45.0, 0.0)),
        (short, Fill("short", "c2", REJECTED, 0, None, None, None, "NO_LIQUIDITY")),
    ]
    assert detect_leg_in(pairs, planned_leg_count=2) == UNWIND


def test_no_leg_in_when_fully_filled():
    from atom.order_execution import Fill
    a, b = _order("a"), _order("b")
    pairs = [(a, Fill("a", "c1", FILLED, 65, 1.0, 1.0, 0.0)),
            (b, Fill("b", "c2", FILLED, 65, 1.0, 1.0, 0.0))]
    assert detect_leg_in(pairs, planned_leg_count=2) is None


def test_no_leg_in_when_fully_unfilled():
    from atom.order_execution import Fill
    a, b = _order("a"), _order("b")
    pairs = [(a, Fill("a", "c1", REJECTED, 0, None, None, None, "x")),
            (b, Fill("b", "c2", REJECTED, 0, None, None, None, "x"))]
    assert detect_leg_in(pairs, planned_leg_count=2) is None


# ---- Paper fill simulation (real chain data, no fabrication) --------------------

def test_submit_paper_fills_from_real_chain():
    chain = {(24200, "PE"): {"ltp": 120.35, "oi": 100}}
    o = _order()
    f = submit_paper(o, chain, 24200, "PE")
    assert f.status == FILLED
    assert f.avg_fill_price == round_to_tick(120.35, 0.05, "SELL")


def test_submit_paper_rejects_missing_quote_never_fabricates():
    f = submit_paper(_order(), {}, 24200, "PE")
    assert f.status == REJECTED and f.reason == "NO_LIQUIDITY"


def test_submit_paper_rejects_invalid_order_before_lookup():
    bad = _order(qty=70)   # not a lot multiple
    f = submit_paper(bad, {(24200, "PE"): {"ltp": 100.0}}, 24200, "PE")
    assert f.status == REJECTED and f.reason == "INVALID_LOT_QTY"


def test_submit_paper_computes_slippage_vs_limit():
    chain = {(24200, "PE"): {"ltp": 120.00}}
    o = _order(order_type="LIMIT", limit_price=118.00)
    f = submit_paper(o, chain, 24200, "PE")
    assert f.slippage == round((120.00 - 118.00) * -1, 4)   # SELL: worse fill = negative


# ---- T3.6 Partial fill ----------------------------------------------------------

def test_remaining_qty_tracks_partial_fill():
    from atom.order_execution import Fill
    o = _order(qty=130)
    f = Fill(o.leg_id, o.client_order_id, "PARTIAL", 65, 120.0, 120.0, 0.0)
    assert remaining_qty(o, f) == 65


def test_remaining_qty_zero_when_fully_filled():
    from atom.order_execution import Fill
    o = _order(qty=65)
    f = Fill(o.leg_id, o.client_order_id, FILLED, 65, 120.0, 120.0, 0.0)
    assert remaining_qty(o, f) == 0


# ---- 13.7 Reject classification -------------------------------------------------

def test_transient_reject_classified():
    assert classify_reject("NO_LIQUIDITY") == TRANSIENT


def test_terminal_reject_classified():
    assert classify_reject("INVALID_LOT_QTY") == TERMINAL


# ---- 13.11 Exit/square-off construction ------------------------------------------

def test_exit_orders_reverse_each_leg():
    legs = (("BUY", 24000, "PE", 45.0), ("SELL", 24200, "PE", 120.0))
    tsyms = ("NIFTY07JUL26P24000", "NIFTY07JUL26P24200")
    orders = exit_orders_from_position(legs, tsyms, lot_size=65, tick_size=0.05, plan_id="p1")
    assert orders[0].action == "SELL"   # was BUY -> exit sells it
    assert orders[1].action == "BUY"    # was SELL -> exit buys it back
    assert all(o.order_type == "MARKET" for o in orders)   # forced exit, no passive limit
