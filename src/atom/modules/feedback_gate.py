"""Module 10 — Feedback Gate (STUB). Governance: approve/promote/rollback (decides, 16 stores)."""
from __future__ import annotations

from dataclasses import replace

from ..contracts import ParameterSet


class FeedbackGate:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def evaluate(self, candidate: ParameterSet, backtest_ok: bool = True) -> ParameterSet:
        state = "APPROVED" if backtest_ok else "REJECTED"
        self.t.emit("feedback_gate", "evaluate", {"approval_state": state},
                    msg=f"FEEDBACK GATE → backtest {'passed' if backtest_ok else 'failed'}"
                        f" + awaiting morning human approval → {state}")
        return replace(candidate, approval_state=state)
