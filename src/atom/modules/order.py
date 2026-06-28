"""Module 13 — Order/Execution (STUB). Owns placement mechanics + order reconciliation."""
from __future__ import annotations

from ..contracts import Fill, RiskVerdict, Session, StructurePlan
from ..util import now


class Order:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def execute(self, plan: StructurePlan, verdict: RiskVerdict,
                session: Session) -> list[Fill]:
        self.t.emit("order", "execute", {"approved": verdict.approved})
        fills: list[Fill] = []
        for i, leg in enumerate(plan.legs):
            fills.append(Fill(order_id=f"O{i}", leg_symbol=leg.instrument.tradingsymbol,
                              fill_price=leg.price, qty=leg.qty,
                              status="FILLED", ts=now()))
        return fills
