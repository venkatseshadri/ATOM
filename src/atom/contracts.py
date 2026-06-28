"""ATOM frozen contract objects (Phase 0).

The ONLY objects modules exchange. Implemented as frozen dataclasses, so the
contracts are literally immutable. Boundaries between producers/consumers are
governed by SEAM_RECONCILIATION.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TraceEvent:
    source: str
    type: str
    payload: dict[str, Any]
    ts: str


@dataclass(frozen=True)
class Session:
    """Broker session (Module 11). NOT the market session (Module 7)."""
    broker: str
    token: str
    state: str          # e.g. "authenticated"
    expires_at: str


@dataclass(frozen=True)
class Instrument:
    tradingsymbol: str
    index: str
    expiry: str
    strike: float
    right: str          # "CE" | "PE"
    lot_size: int
    tick_size: float


@dataclass(frozen=True)
class OptionQuote:
    strike: float
    right: str
    ltp: float
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float


@dataclass(frozen=True)
class MarketSnapshot:
    index: str
    ts: str
    spot: float
    chain: tuple[OptionQuote, ...]
    ohlc: dict[str, list]               # timeframe -> bars (Module 1 owns these)


@dataclass(frozen=True)
class RegimeState:
    index: str
    ts: str
    regime: str         # TREND_UP | TREND_DOWN | SIDEWAYS | REVERSAL
    confidence: float


@dataclass(frozen=True)
class StrategyDecision:
    intent: str         # OPEN | MORPH_ADD | MORPH_CLOSE_LEG | HOLD | EXIT
    structure: str
    rationale: str


@dataclass(frozen=True)
class Leg:
    instrument: Instrument
    action: str         # BUY | SELL
    qty: int
    price: float
    order_type: str     # MKT | LIMIT  (Module 4 owns the price intent)


@dataclass(frozen=True)
class StructurePlan:
    legs: tuple[Leg, ...]
    net_credit: float
    max_loss: float


@dataclass(frozen=True)
class AccountState:
    """Equity/funds/margin snapshot consumed by Risk (Module 5).

    GAP G1 (SEAM_RECONCILIATION §2): the *provider* of this object is not yet
    assigned (equity from Ledger + funds/margin from broker via Auth). The
    contract shape is frozen here; the provider is decided before Phase 3.
    """
    equity: float
    available_funds: float
    used_margin: float
    realized_pnl_today: float
    trades_today: int
    reentries_today: int


@dataclass(frozen=True)
class RiskVerdict:
    approved: bool
    adjusted_qty: int
    breached: tuple[str, ...]
    sl: float
    tsl: float
    tp: float


@dataclass(frozen=True)
class OrderRequest:
    instrument: Instrument
    side: str           # BUY | SELL
    qty: int
    order_type: str     # MKT | LIMIT
    price: float


@dataclass(frozen=True)
class Fill:
    order_id: str
    leg_symbol: str
    fill_price: float
    qty: int
    status: str         # FILLED | PARTIAL | REJECTED | CANCELLED
    ts: str


@dataclass(frozen=True)
class PositionState:
    fsm_state: str      # FLAT | SINGLE_SPREAD | IRON_FLY | RUNNER
    legs: tuple[Leg, ...]
    live_pnl: float
    realized_pnl: float


@dataclass(frozen=True)
class ParameterSet:
    """The day's frozen numbers (canonical connect-back; supersedes the old
    'ResearchCache' term — GAP G3)."""
    version: str
    valid_for: str          # the trading day this set is approved for
    params: dict[str, Any]
    evidence_ref: str
    approval_state: str     # CANDIDATE | APPROVED | REJECTED


# The frozen contract registry — used by Phase 0 conformance tests.
ALL_CONTRACTS = (
    TraceEvent, Session, Instrument, OptionQuote, MarketSnapshot, RegimeState,
    StrategyDecision, Leg, StructurePlan, AccountState, RiskVerdict,
    OrderRequest, Fill, PositionState, ParameterSet,
)
