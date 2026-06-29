"""Module 5 — Risk & Sizing (STUB). Hard gate; owns binding size + max-loss + envelope."""
from __future__ import annotations

from ..contracts import AccountState, PositionState, RiskVerdict, StructurePlan


class Risk:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def gate(self, plan: StructurePlan, account: AccountState,
             position: PositionState, reject: str | None = None) -> RiskVerdict:
        if reject:
            self.t.emit("risk", "gate", {"approved": False, "breach": reject},
                        msg=f"RISK gate → BREACH {reject} → REJECTED (no order)")
            return RiskVerdict(approved=False, adjusted_qty=0, breached=(reject,),
                               sl=0.0, tsl=0.0, tp=0.0)
        qty = plan.legs[0].qty if plan.legs else 0
        self.t.emit("risk", "gate", {"approved": True, "max_loss": plan.max_loss},
                    msg=f"RISK gate → deploy ₹{account.available_funds:,.0f}, "
                        f"size 1 lot ({qty}), max loss ₹{plan.max_loss:,.0f} ≤ cap "
                        f"→ APPROVED")
        return RiskVerdict(approved=True, adjusted_qty=qty, breached=(),
                           sl=plan.max_loss, tsl=0.0, tp=round(plan.net_credit * 0.5))
