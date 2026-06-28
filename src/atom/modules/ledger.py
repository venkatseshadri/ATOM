"""Module 14 — Ledger & Persistence (STUB). Owns position truth + P&L (flat-truth source)."""
from __future__ import annotations

from ..contracts import Fill, PositionState


class Ledger:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def apply(self, fills: list[Fill]) -> PositionState:
        state = "SINGLE_SPREAD" if fills else "FLAT"
        self.t.emit("ledger", "apply", {"state": state, "fills": len(fills)},
                    msg=f"POSITION open → {state}, {len(fills)} legs filled, live P&L ₹0")
        return PositionState(fsm_state=state, legs=(), live_pnl=0.0, realized_pnl=0.0)

    def flat(self) -> PositionState:
        return PositionState(fsm_state="FLAT", legs=(), live_pnl=0.0, realized_pnl=0.0)
