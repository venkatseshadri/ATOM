"""Module 13 — Order/Execution (STUB). Owns placement mechanics + order reconciliation."""
from __future__ import annotations

from ..contracts import Fill, RiskVerdict, Session, StructurePlan
from ..util import now


class Order:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def execute(self, plan: StructurePlan, verdict: RiskVerdict,
                session: Session) -> list[Fill]:
        if not verdict.approved:
            self.t.emit("order", "skip", {}, msg="ORDER → skipped (risk rejected)")
            return []
        self.t.emit("order", "execute", {"legs": len(plan.legs)},
                    msg=f"ORDER placement → {len(plan.legs)} leg(s)")
        fills: list[Fill] = []
        for i, leg in enumerate(plan.legs):
            fill = Fill(order_id=f"O{1001 + i}", leg_symbol=leg.instrument.tradingsymbol,
                        fill_price=leg.price, qty=leg.qty, status="FILLED", ts=now())
            self.t.emit("order", "fill", {"id": fill.order_id, "price": leg.price},
                        msg=f"  {leg.action} {leg.instrument.tradingsymbol} "
                            f"x{leg.qty} @ ₹{leg.price} → FILLED (id {fill.order_id})")
            fills.append(fill)
        return fills
