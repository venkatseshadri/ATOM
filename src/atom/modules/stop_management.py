"""Module 6 — Stop Management (STUB). Owns SL/TSL/TP levels + breach->exit trigger."""
from __future__ import annotations

from ..contracts import MarketSnapshot, PositionState

_BREACH = {
    "SL": "SL BREACH → short leg tested, MTM −₹1,665 → raise EXIT (stop-loss)",
    "TSL": "TSL HIT → trailed stop locked profit, MTM +₹1,460 → raise EXIT (trailing-stop)",
    "TP": "TP HIT → 50% credit captured, MTM +₹2,918 → raise EXIT (take-profit)",
}


class StopManagement:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def manage(self, position: PositionState, snapshot: MarketSnapshot,
               event: str | None = None) -> dict:
        if event in _BREACH:
            self.t.emit("stop_management", "breach", {"event": event},
                        msg=_BREACH[event])
            return {"sl": 0.0, "tsl": 0.0, "tp": 0.0, "exit": True, "reason": event}
        self.t.emit("stop_management", "manage", {"state": position.fsm_state},
                    msg=f"STOPS armed → SL set, TSL inactive, TP target "
                        f"(state {position.fsm_state})")
        return {"sl": 0.0, "tsl": 0.0, "tp": 0.0, "exit": False}
