"""Module 5 — Risk & Sizing (STUB). Hard gate; owns binding size + max-loss + envelope."""
from __future__ import annotations

from ..contracts import AccountState, PositionState, RiskVerdict, StructurePlan


class Risk:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def gate(self, plan: StructurePlan, account: AccountState,
             position: PositionState) -> RiskVerdict:
        self.t.emit("risk", "gate", {"legs": len(plan.legs)})
        qty = plan.legs[0].qty if plan.legs else 0
        return RiskVerdict(approved=True, adjusted_qty=qty, breached=(),
                           sl=0.0, tsl=0.0, tp=0.0)
