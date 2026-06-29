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
                    msg=f"FEEDBACK GATE → PORCUPINE backtest 60d "
                        f"{'PASS' if backtest_ok else 'FAIL'}, promotion 1/2 days → "
                        f"{state} (pending 08:45 morning human approval)")
        return replace(candidate, approval_state=state)
