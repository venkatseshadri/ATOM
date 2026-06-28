"""Module 10 — Feedback Gate (STUB). Governance: approve/promote/rollback (decides, 16 stores)."""
from __future__ import annotations

from dataclasses import replace

from ..contracts import ParameterSet


class FeedbackGate:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def evaluate(self, candidate: ParameterSet, backtest_ok: bool = True) -> ParameterSet:
        self.t.emit("feedback_gate", "evaluate", {"approved": backtest_ok})
        return replace(candidate,
                       approval_state="APPROVED" if backtest_ok else "REJECTED")
