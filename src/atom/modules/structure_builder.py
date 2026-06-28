"""Module 4 — Trade Construction (STUB). Owns price intent + trade-strike choice."""
from __future__ import annotations

from ..contracts import Instrument, Leg, MarketSnapshot, StrategyDecision, StructurePlan


class StructureBuilder:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def build(self, decision: StrategyDecision, snapshot: MarketSnapshot,
              instrument: Instrument) -> StructurePlan:
        self.t.emit("structure_builder", "build", {"intent": decision.intent})
        leg = Leg(instrument=instrument, action="SELL", qty=instrument.lot_size,
                  price=0.0, order_type="LIMIT")
        return StructurePlan(legs=(leg,), net_credit=0.0, max_loss=0.0)
