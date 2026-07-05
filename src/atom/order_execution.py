"""Module 13 — Order/Execution (Phase 3, real, PAPER fills).

ATOM stays paper-only through Phase 6 (Phase 7 = real capital) — there is no live broker
connection to submit orders to yet (Module 11's real-session design is still pending).
This module implements the REAL decision logic (order assembly/validation, idempotent
client-order-ids, protective-leg-first sequencing, leg-in detection + complete-vs-unwind
policy, partial-fill accounting, reject classification, exit/square-off construction) —
the "broker call" itself is a paper-fill simulator that looks up the SAME real Penguin
chain data Module 4 already uses (no fabrication, matching Phase 1/2's discipline), not a
network call. §13.4 (modify), §13.9 (broker reconciliation), §13.10 (ack/TTL timeouts) are
thin/deferred — they're fundamentally about a live broker round-trip that doesn't exist
yet; documented here rather than silently skipped, same honesty as the greek-data gap in
Modules 4/6.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from math import floor

FILLED, PARTIAL, REJECTED, WORKING = "FILLED", "PARTIAL", "REJECTED", "WORKING"
COMPLETE, UNWIND = "COMPLETE", "UNWIND"
TRANSIENT, TERMINAL = "TRANSIENT", "TERMINAL"


@dataclass(frozen=True)
class OrderRequest:
    leg_id: str
    tradingsymbol: str
    action: str            # BUY | SELL
    qty: int               # units (already lot_size-multiplied)
    order_type: str        # MARKET | LIMIT
    limit_price: float | None
    tick_size: float
    lot_size: int
    client_order_id: str
    protective: bool = False   # True for the hedge/wing leg (13.2.1 sequencing)


@dataclass(frozen=True)
class Fill:
    leg_id: str
    client_order_id: str
    status: str             # FILLED | PARTIAL | REJECTED | WORKING
    filled_qty: int
    avg_fill_price: float | None
    expected_price: float | None
    slippage: float | None
    reason: str | None = None


# ---- 13.1 Order placement: validation + assembly ------------------------------

def round_to_tick(price: float, tick: float, side_conservative: str = "SELL") -> float:
    """13.1.2 — round to a valid tick, side-conservative: round sells down, buys up
    (never overstate a credit, never understate a debit)."""
    if tick <= 0:
        return price
    n = price / tick
    return round((floor(n) if side_conservative == "SELL" else -floor(-n)) * tick, 2)


def validate_order(action: str, qty: int, lot_size: int, order_type: str,
                   limit_price: float | None, tick_size: float) -> str | None:
    """13.1.1/13.1.2 — pre-submission validation; returns a reject reason or None.
    Fails fast, never guesses a default."""
    if action not in ("BUY", "SELL"):
        return "INVALID_PLAN"
    if qty <= 0 or lot_size <= 0 or qty % lot_size != 0:
        return "INVALID_LOT_QTY"
    if order_type == "LIMIT" and (limit_price is None or limit_price <= 0):
        return "INVALID_PLAN"
    if tick_size <= 0:
        return "INVALID_PLAN"
    return None


def make_client_order_id(plan_id: str, leg_id: str) -> str:
    """13.3.1 — deterministic, idempotent: the SAME (plan_id, leg_id) always produces
    the SAME id, so a retry naturally re-derives an identical tag instead of minting a
    fresh one (that's what makes 13.3.2's dedupe possible at all)."""
    return hashlib.sha256(f"{plan_id}:{leg_id}".encode()).hexdigest()[:16]


def is_duplicate_submit(already_submitted: set[str], client_order_id: str) -> bool:
    """13.3.2 — duplicate-submission detection on retry."""
    return client_order_id in already_submitted


# ---- 13.2 Multi-leg sequencing & leg-in detection -----------------------------

def sequence_legs(orders: list[OrderRequest]) -> list[OrderRequest]:
    """13.2.1 — protective-leg-first: buy wings before selling the body, so the
    account is never short without its hedge. Risk-conservative default for a
    short-premium defined-risk structure."""
    return sorted(orders, key=lambda o: not o.protective)


def detect_leg_in(order_fill_pairs: list[tuple[OrderRequest, Fill]],
                  planned_leg_count: int) -> str | None:
    """13.2.4 — structural completeness check. Returns COMPLETE (missing leg is the
    cheap protective wing — finish it, restore defined risk), UNWIND (missing leg is
    the premium-collecting short — don't chase, flatten what filled), or None (either
    fully filled or fully unfilled, no leg-in risk)."""
    filled = [pair for pair in order_fill_pairs if pair[1].status == FILLED]
    if len(filled) == planned_leg_count or len(filled) == 0:
        return None
    filled_ids = {order.leg_id for order, _ in filled}
    missing = [order for order, _ in order_fill_pairs if order.leg_id not in filled_ids]
    any_protective_missing = any(order.protective for order in missing)
    return COMPLETE if any_protective_missing else UNWIND


# ---- Paper fill simulation (stands in for a real broker call) -----------------

def submit_paper(order: OrderRequest, chain: dict, strike: float, right: str) -> Fill:
    """Simulated broker call: look up the SAME real Penguin chain ltp Module 4 already
    consumes (chain: {(strike,right): {'ltp':..,'oi':..}}) — no fabrication. Missing/
    zero ltp -> REJECTED (illiquid/no quote), never a fabricated fill."""
    err = validate_order(order.action, order.qty, order.lot_size, order.order_type,
                         order.limit_price, order.tick_size)
    if err:
        return Fill(order.leg_id, order.client_order_id, REJECTED, 0, None, None, None, err)
    q = chain.get((strike, right))
    if not q or q.get("ltp") is None or q["ltp"] <= 0:
        return Fill(order.leg_id, order.client_order_id, REJECTED, 0, None, None, None,
                    "NO_LIQUIDITY")
    fill_price = round_to_tick(q["ltp"], order.tick_size,
                               "SELL" if order.action == "SELL" else "BUY")
    expected = order.limit_price if order.limit_price is not None else fill_price
    slippage = round((fill_price - expected) * (1 if order.action == "BUY" else -1), 4)
    return Fill(order.leg_id, order.client_order_id, FILLED, order.qty, fill_price,
               expected, slippage)


# ---- 13.6 Partial-fill accounting ----------------------------------------------

def remaining_qty(order: OrderRequest, fill: Fill) -> int:
    """13.6.1 — remaining quantity still to fill."""
    return max(0, order.qty - fill.filled_qty)


# ---- 13.7 Reject classification -------------------------------------------------

_TRANSIENT_REASONS = {"NO_LIQUIDITY", "RATE_LIMITED", "SESSION_EXPIRED", "TIMEOUT"}
_TERMINAL_REASONS = {"INVALID_PLAN", "INVALID_LOT_QTY", "INVALID_SYMBOL", "MARGIN_INSUFFICIENT"}


def classify_reject(reason: str) -> str:
    """13.7.1 — transient (safe to retry once conditions change) vs terminal (retrying
    with the same plan will just reject again)."""
    if reason in _TERMINAL_REASONS:
        return TERMINAL
    return TRANSIENT   # fail toward "might be retryable" only for genuinely unknown reasons


# ---- 13.11 Exit / square-off order construction --------------------------------

def exit_orders_from_position(legs: tuple, tsyms: tuple, lot_size: int, tick_size: float,
                              plan_id: str) -> list[OrderRequest]:
    """13.11.1 — derive the exit (reversing) order for each leg of an open position.
    legs: PaperOrder.legs shape (action, strike, right, entry_price) tuples."""
    orders = []
    for i, leg in enumerate(legs):
        action, strike, right, _entry_price = leg
        reverse_action = "SELL" if action == "BUY" else "BUY"
        tsym = tsyms[i] if i < len(tsyms) else f"{strike}{right}"
        leg_id = f"exit_{i}"
        orders.append(OrderRequest(
            leg_id=leg_id, tradingsymbol=tsym, action=reverse_action, qty=lot_size,
            order_type="MARKET", limit_price=None, tick_size=tick_size, lot_size=lot_size,
            client_order_id=make_client_order_id(plan_id, leg_id),
            # exit-side sequencing mirrors entry: closing a SHORT means the exit BUYS it
            # back — that's the risk-capping side, so it goes first if sequenced.
            protective=(action == "SELL"),
        ))
    return orders
