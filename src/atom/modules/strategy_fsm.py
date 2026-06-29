"""Module 3 — Strategy FSM (STUB). Owns the lifecycle decision (open/morph/hold/exit).

Phase 0 uses a scripted transition tape (a lookup, NOT real strategy logic) so the
walking skeleton can demonstrate the full morph lifecycle in the log.
"""
from __future__ import annotations

from ..contracts import PositionState, RegimeState, StrategyDecision

# (regime, current_state) -> (intent, structure)  — scripted skeleton tape
TAPE = {
    ("TREND_UP", "FLAT"): ("OPEN", "bull_put_spread"),
    ("SIDEWAYS", "FLAT"): ("OPEN", "bull_put_spread"),          # default / test path
    ("SIDEWAYS", "SINGLE_SPREAD"): ("MORPH_ADD", "add_bear_call_spread"),
    ("REVERSAL", "IRON_FLY"): ("MORPH_CLOSE_LEG", "close_put_keep_runner"),
    ("EOD", "RUNNER"): ("EXIT", "square_off"),
}


class StrategyFSM:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def decide(self, regime: RegimeState, position: PositionState) -> StrategyDecision:
        intent, structure = TAPE.get((regime.regime, position.fsm_state), ("HOLD", "none"))
        self.t.emit("strategy_fsm", "decide", {"intent": intent, "structure": structure},
                    msg=f"FSM [state={position.fsm_state} + regime={regime.regime}] "
                        f"→ {intent} ({structure})")
        return StrategyDecision(intent=intent, structure=structure,
                                rationale="scripted skeleton tape")
