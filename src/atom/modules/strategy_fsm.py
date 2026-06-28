"""Module 3 — Strategy FSM (STUB). Owns the lifecycle decision (open/morph/hold/exit)."""
from __future__ import annotations

from ..contracts import PositionState, RegimeState, StrategyDecision


class StrategyFSM:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def decide(self, regime: RegimeState, position: PositionState) -> StrategyDecision:
        self.t.emit("strategy_fsm", "decide",
                    {"regime": regime.regime, "state": position.fsm_state})
        return StrategyDecision(intent="OPEN", structure="bull_put_spread",
                                rationale="canned")
