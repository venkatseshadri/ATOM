"""Module 4 — Trade Construction (STUB). Owns price intent + trade-strike choice.

Phase 0: strikes are derived from the ATM that Module 12 resolved from spot (short = ATM,
wing = ATM ± WING). The PREMIUMS and the selection *policy* (which delta/offset) remain
illustrative — those become real in Phase 2 with greek-driven selection.
"""
from __future__ import annotations

from ..contracts import Instrument, Leg, MarketSnapshot, StrategyDecision, StructurePlan

WING = 100      # illustrative wing width (strike points)


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
        atm = int(instrument.strike)          # ATM resolved by Module 12 from spot
        if i == "OPEN":
            short, hedge = atm, atm - WING
            legs = (self._leg(instrument, short, "SELL", 123.40),
                    self._leg(instrument, hedge, "BUY", 45.60))
            credit, max_loss = 77.80 * lot, 22.20 * lot
            msg = (f"OPEN bull put spread → short = ATM {short} PE @ ₹123.40 (SELL) | "
                   f"hedge = ATM−{WING} {hedge} PE @ ₹45.60 (BUY) → "
                   f"credit ₹{credit:,.0f}, max loss ₹{max_loss:,.0f}")
        elif i == "MORPH_ADD":
            short, hedge = atm + WING, atm + 2 * WING
            legs = (self._leg(instrument, short, "SELL", 108.20, "CE"),
                    self._leg(instrument, hedge, "BUY", 52.40, "CE"))
            credit, max_loss = 55.80 * lot, 44.20 * lot
            msg = (f"LEG SHIFT (morph-add) → + bear call spread: "
                   f"short = ATM+{WING} {short} CE @ ₹108.20 (SELL) | "
                   f"hedge = ATM+{2*WING} {hedge} CE @ ₹52.40 (BUY) → now IRON_FLY")
        elif i == "MORPH_CLOSE_LEG":
            legs = (self._leg(instrument, atm, "BUY", 158.00),
                    self._leg(instrument, atm - WING, "SELL", 96.00))
            credit, max_loss = 0.0, 0.0
            msg = (f"LEG SHIFT (morph) → CLOSE bull put spread (threatened): "
                   f"BUY back {atm} PE | SELL {atm - WING} PE → KEEP bear call as RUNNER")
        elif i == "EXIT":
            if decision.structure == "square_off_put":
                legs = (self._leg(instrument, atm, "BUY", 118.00),
                        self._leg(instrument, atm - WING, "SELL", 70.00))
                msg = (f"EXIT → square off bull put spread: "
                       f"BUY back {atm} PE | SELL {atm - WING} PE")
            else:
                legs = (self._leg(instrument, atm + WING, "BUY", 61.00, "CE"),
                        self._leg(instrument, atm + 2 * WING, "SELL", 28.00, "CE"))
                msg = (f"EXIT → square off RUNNER (bear call): "
                       f"BUY back {atm + WING} CE | SELL {atm + 2 * WING} CE")
            credit, max_loss = 0.0, 0.0
        else:
            legs, credit, max_loss = (), 0.0, 0.0
            msg = "HOLD — no structure change"
        self.t.emit("structure_builder", "build",
                    {"intent": i, "atm": atm, "net_credit": credit, "max_loss": max_loss},
                    msg=msg)
        return StructurePlan(legs=legs, net_credit=credit, max_loss=max_loss)
