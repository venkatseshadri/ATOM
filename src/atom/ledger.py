"""Module 14 — Ledger & Persistence (Phase 4, real).

Single source of truth for what ATOM holds and what it's worth. Snapshot-of-record
style (Approach B in the module doc): `AtomState.paper_trades` IS the mutable position
record; this module adds the accounting math (cost basis, mark-to-market, realized vs
unrealized P&L with the flat-invariant check) and the mechanics the doc calls out as
"the spine" — idempotent fill application, reconciliation, EOD finalization — on top of
it, rather than building a parallel event-sourced store.

Recoverability (14.6) and single-writer integrity (14.7) are already satisfied by
ATOM's existing architecture, not new code here: every cron invocation is a fresh
process that reconstructs state from `AtomState` alone (same stateless-per-invocation
design as Module 7), and `cron/run_atom_paper.sh`'s flock already enforces exactly one
writer at a time. Tested directly against the real `AtomState`, not re-implemented.

Broker reconciliation (14.8) has no live broker to reconcile against yet (paper-only) —
built generically against any externally-reported position list so it's real and
testable now, ready for Phase 7."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

MATCH, MISSED_FILL, PHANTOM_FILL, UNKNOWN_AT_BROKER = (
    "MATCH", "MISSED_FILL", "PHANTOM_FILL", "UNKNOWN_AT_BROKER")


@dataclass(frozen=True)
class Leg:
    key: str            # canonical instrument identity (tradingsymbol)
    signed_qty: int      # +long, -short
    avg_entry_price: float


@dataclass(frozen=True)
class FillResult:
    accepted: bool
    reason: str | None   # None if accepted; else the quarantine reason
    leg: Leg | None


def validate_fill(leg_key: str, price: float, qty: int, lot_size: int, status: str) -> str | None:
    """14.1.1 — returns a quarantine reason or None. Only executed-quantity fills may
    ever reach the applier; unknown status strings fail closed (never assumed 'filled')."""
    if not leg_key:
        return "MISSING_LEG"
    if price != price or price < 0:   # NaN or negative
        return "INVALID_PRICE"
    if qty == 0:
        return "ZERO_QTY"
    if lot_size <= 0 or qty % lot_size != 0:
        return "LOT_SIZE_VIOLATION"
    if status not in ("FILLED", "PARTIAL"):
        return f"NOT_EXECUTED_STATUS:{status}"
    return None


def apply_fill(prior_leg: Leg | None, leg_key: str, fill_price: float, fill_qty: int,
              side: str, lot_size: int, status: str = "FILLED") -> FillResult:
    """14.1.2/14.2.1 — weighted-average cost basis on same-direction adds; a fill on
    the opposite side reduces/closes the position (handled by the caller via
    close_leg(), since a close realizes P&L which this pure position-update doesn't
    compute). Idempotency (dedup by fill id) is the CALLER's job (AtomState tracks
    applied fill ids durably) — this function is the deterministic math, called only
    once a fill is known-new."""
    err = validate_fill(leg_key, fill_price, fill_qty, lot_size, status)
    if err:
        return FillResult(False, err, prior_leg)
    signed = fill_qty if side == "BUY" else -fill_qty
    if prior_leg is None or prior_leg.key != leg_key:
        return FillResult(True, None, Leg(leg_key, signed, fill_price))
    new_qty = prior_leg.signed_qty + signed
    if (prior_leg.signed_qty >= 0) == (signed >= 0):   # same direction -> weighted avg
        total_cost = abs(prior_leg.signed_qty) * prior_leg.avg_entry_price + abs(signed) * fill_price
        new_avg = total_cost / abs(new_qty) if new_qty != 0 else prior_leg.avg_entry_price
        return FillResult(True, None, Leg(leg_key, new_qty, new_avg))
    # opposite direction: reduces the position; avg_entry_price of the REMAINING qty
    # is unchanged (only the disposed portion realizes P&L, computed by the caller)
    return FillResult(True, None, Leg(leg_key, new_qty, prior_leg.avg_entry_price))


# ---- 14.3/14.4 Mark-to-market + P&L split ---------------------------------------

@dataclass(frozen=True)
class PnLReport:
    realized: float
    unrealized: float | None      # None when flagged low-confidence (stale/missing mark)
    total: float | None
    confidence: str                # OK | LOW_CONFIDENCE | FLAT
    flat: bool


def compute_pnl(legs: tuple[Leg, ...], marks: dict[str, float | None], realized: float,
               lot_size: int) -> PnLReport:
    """14.4.1/14.4.2/14.4.3 — the flat-invariant check (14.4.3): flat with any
    nonzero-would-be unrealized is impossible by construction here (no legs -> no
    unrealized terms), but a caller passing legs that don't sum to flat while claiming
    flat is a contract violation we still guard in the harness/tests."""
    if not legs:
        return PnLReport(realized, 0.0, realized, "FLAT", True)
    total_unrealized = 0.0
    any_stale = False
    for leg in legs:
        mark = marks.get(leg.key)
        if mark is None:
            any_stale = True
            continue
        total_unrealized += (mark - leg.avg_entry_price) * (-1 if leg.signed_qty < 0 else 1) \
            * abs(leg.signed_qty) * lot_size
    if any_stale:
        return PnLReport(realized, None, None, "LOW_CONFIDENCE", False)
    return PnLReport(realized, round(total_unrealized, 2), round(realized + total_unrealized, 2),
                     "OK", False)


# ---- 14.8 Reconciliation ---------------------------------------------------------

@dataclass(frozen=True)
class Divergence:
    leg_key: str
    classification: str   # MATCH | MISSED_FILL | PHANTOM_FILL | UNKNOWN_AT_BROKER
    ledger_qty: int
    broker_qty: int


def reconcile(ledger_legs: tuple[Leg, ...], broker_positions: dict[str, int]) -> tuple[Divergence, ...]:
    """14.8.1 — never mutates the ledger; only classifies and reports. Broker qty >
    ledger qty suggests a missed fill (downtime); ledger qty > broker suggests a
    phantom/double-applied fill; a broker leg the ledger has never seen is flagged
    UNKNOWN_AT_BROKER (mapping bug or an out-of-scope manual trade)."""
    ledger_by_key = {leg.key: leg.signed_qty for leg in ledger_legs}
    keys = set(ledger_by_key) | set(broker_positions)
    out = []
    for key in sorted(keys):
        l_qty = ledger_by_key.get(key, 0)
        b_qty = broker_positions.get(key, 0)
        if l_qty == b_qty:
            out.append(Divergence(key, MATCH, l_qty, b_qty))
        elif key not in ledger_by_key:
            out.append(Divergence(key, UNKNOWN_AT_BROKER, l_qty, b_qty))
        elif abs(b_qty) > abs(l_qty):
            out.append(Divergence(key, MISSED_FILL, l_qty, b_qty))
        else:
            out.append(Divergence(key, PHANTOM_FILL, l_qty, b_qty))
    return tuple(out)


# ---- 14.9 End-of-day finalization ------------------------------------------------

@dataclass(frozen=True)
class EODResult:
    flat: bool
    daily_realized: float
    residual_legs: tuple[str, ...]
    alarm: str | None


def finalize_eod(open_legs: tuple[Leg, ...], daily_realized_events: tuple[float, ...]) -> EODResult:
    """14.9.1/14.9.2 — the day cannot close clean while any risk remains open; a
    residual is recorded and alarmed, never silently carried. Daily realized is the
    sum of the day's realization EVENTS (append-only), not a recomputation from
    scratch — matches 14.4.2's 'accumulates by event' invariant."""
    total = round(sum(daily_realized_events), 2)
    if open_legs:
        return EODResult(False, total, tuple(leg.key for leg in open_legs),
                         "NOT_FLAT_AT_EOD")
    return EODResult(True, total, (), None)
