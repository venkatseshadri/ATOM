"""Module 9 — Optimization (STUB). Produces a candidate ParameterSet."""
from __future__ import annotations

from ..contracts import ParameterSet
from ..util import now


class Optimization:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def propose(self, findings: dict, history=None) -> ParameterSet:
        self.t.emit("optimization", "propose", {"version": "cand-1"},
                    msg="AI OPTIMIZER (LLM) → candidate cand-1: ADX entry 22→24, "
                        "short Δ 0.45→0.40 | objective: drawdown-adjusted PnL ↑, survival OK")
        return ParameterSet(version="cand-1", valid_for=now()[:10],
                            params={"adx_entry": 24, "short_delta": 0.40},
                            evidence_ref="backtest-60d", approval_state="CANDIDATE")
