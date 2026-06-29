"""Module 14 — Ledger & Persistence (STUB). Owns position truth + P&L (flat-truth source)."""
from __future__ import annotations

from ..contracts import Fill, PositionState


class Ledger:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def apply(self, fills: list[Fill], next_state: str | None = None,
              realized: float = 0.0) -> PositionState:
        if next_state is None:
            next_state = "SINGLE_SPREAD" if fills else "FLAT"
        tail = f", realized P&L ₹{realized:+,.0f}" if next_state == "FLAT" and realized else ""
        self.t.emit("ledger", "apply", {"state": next_state, "fills": len(fills)},
                    msg=f"LEDGER → state → {next_state}, {len(fills)} leg(s){tail}")
        return PositionState(fsm_state=next_state, legs=(),
                             live_pnl=0.0, realized_pnl=realized)

    def flat(self) -> PositionState:
        return PositionState(fsm_state="FLAT", legs=(), live_pnl=0.0, realized_pnl=0.0)
