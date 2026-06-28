"""Module 3 — Strategy FSM (STUB). Owns the lifecycle decision (open/morph/hold/exit)."""
from __future__ import annotations

from ..contracts import PositionState, RegimeState, StrategyDecision


class StrategyFSM:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def decide(self, regime: RegimeState, position: PositionState) -> StrategyDecision:
        self.t.emit("strategy_fsm", "decide", {"intent": "OPEN"},
                    msg=f"ENTRY CRITERIA met (regime {regime.regime} conf "
                        f"{regime.confidence}, state {position.fsm_state}) "
                        f"→ decision: OPEN bull put spread")
        return StrategyDecision(intent="OPEN", structure="bull_put_spread",
                                rationale="range-bound → sell put spread with-trend")
