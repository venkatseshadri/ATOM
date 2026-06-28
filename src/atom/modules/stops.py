"""Module 6 — Stop Management (STUB). Owns SL/TSL/TP levels + breach->exit trigger."""
from __future__ import annotations

from ..contracts import MarketSnapshot, PositionState


class StopManagement:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def manage(self, position: PositionState, snapshot: MarketSnapshot) -> dict:
        self.t.emit("stop_management", "manage", {"state": position.fsm_state})
        return {"sl": 0.0, "tsl": 0.0, "tp": 0.0, "exit": False}
