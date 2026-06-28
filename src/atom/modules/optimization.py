"""Module 9 — Optimization (STUB). Produces a candidate ParameterSet."""
from __future__ import annotations

from ..contracts import ParameterSet
from ..util import now


class Optimization:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def propose(self, findings: dict, history=None) -> ParameterSet:
        self.t.emit("optimization", "propose", {})
        return ParameterSet(version="cand-0", valid_for=now()[:10], params={},
                            evidence_ref="none", approval_state="CANDIDATE")
