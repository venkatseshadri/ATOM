"""Module 4 — Trade Construction (STUB). Owns price intent + trade-strike choice.

Phase 0 emits leg-shift / morph narration for each lifecycle intent (illustrative,
hard-coded values — no real strike/greek selection yet).
"""
from __future__ import annotations

from ..contracts import Instrument, Leg, MarketSnapshot, StrategyDecision, StructurePlan


class StructureBuilder:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def _leg(self, base: Instrument, strike: int, action: str, price: float,
             right: str = "PE") -> Leg:
        inst = Instrument(tradingsymbol=f"{base.index}{base.expiry}{strike}{right}",
                          index=base.index, expiry=base.expiry, strike=float(strike),
                          right=right, lot_size=base.lot_size, tick_size=base.tick_size)
        return Leg(instrument=inst, action=action, qty=base.lot_size,
                   price=price, order_type="LIMIT")

    def build(self, decision: StrategyDecision, snapshot: MarketSnapshot,
              instrument: Instrument) -> StructurePlan:
        i = decision.intent
        lot = instrument.lot_size
        if i == "OPEN":
            legs = (self._leg(instrument, 23400, "SELL", 123.40),
                    self._leg(instrument, 23300, "BUY", 45.60))
            credit, max_loss = 77.80 * lot, 22.20 * lot
            msg = (f"OPEN bull put spread @ NIFTY ₹{snapshot.spot:,.1f} → "
                   f"SELL 23400 PE @ ₹123.40 | BUY 23300 PE @ ₹45.60 (hedge) → "
                   f"credit ₹{credit:,.0f}, max loss ₹{max_loss:,.0f}")
        elif i == "MORPH_ADD":
            legs = (self._leg(instrument, 23500, "SELL", 108.20, "CE"),
                    self._leg(instrument, 23600, "BUY", 52.40, "CE"))
            credit, max_loss = 55.80 * lot, 44.20 * lot
            msg = ("LEG SHIFT (morph-add) → + bear call spread: "
                   "SELL 23500 CE @ ₹108.20 | BUY 23600 CE @ ₹52.40 → "
                   "structure now IRON_FLY (both sides)")
        elif i == "MORPH_CLOSE_LEG":
            legs = (self._leg(instrument, 23400, "BUY", 158.00),
                    self._leg(instrument, 23300, "SELL", 96.00))
            credit, max_loss = 0.0, 0.0
            msg = ("LEG SHIFT (morph) → CLOSE bull put spread (threatened): "
                   "BUY back 23400 PE | SELL 23300 PE → KEEP bear call as RUNNER")
        elif i == "EXIT":
            if decision.structure == "square_off_put":
                legs = (self._leg(instrument, 23400, "BUY", 118.00),
                        self._leg(instrument, 23300, "SELL", 70.00))
                msg = ("EXIT → square off bull put spread: "
                       "BUY back 23400 PE | SELL 23300 PE")
            else:
                legs = (self._leg(instrument, 23500, "BUY", 61.00, "CE"),
                        self._leg(instrument, 23600, "SELL", 28.00, "CE"))
                msg = ("EXIT → square off RUNNER (bear call): "
                       "BUY back 23500 CE | SELL 23600 CE")
            credit, max_loss = 0.0, 0.0
        else:
            legs, credit, max_loss = (), 0.0, 0.0
            msg = "HOLD — no structure change"
        self.t.emit("structure_builder", "build",
                    {"intent": i, "net_credit": credit, "max_loss": max_loss}, msg=msg)
        return StructurePlan(legs=legs, net_credit=credit, max_loss=max_loss)
