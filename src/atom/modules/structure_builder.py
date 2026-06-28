"""Module 4 — Trade Construction (STUB). Owns price intent + trade-strike choice."""
from __future__ import annotations

from ..contracts import Instrument, Leg, MarketSnapshot, StrategyDecision, StructurePlan

# illustrative bull put spread around the 23400 ATM (hard-coded for Phase 0)
SHORT_STRIKE = 23400
HEDGE_STRIKE = 23300
SHORT_PREMIUM = 123.40      # sell the nearer PE (higher premium)
HEDGE_PREMIUM = 45.60       # buy the farther PE (cheaper) as the hedge


class StructureBuilder:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def _leg(self, base: Instrument, strike: int, action: str, price: float) -> Leg:
        inst = Instrument(tradingsymbol=f"{base.index}{base.expiry}{strike}PE",
                          index=base.index, expiry=base.expiry, strike=float(strike),
                          right="PE", lot_size=base.lot_size, tick_size=base.tick_size)
        return Leg(instrument=inst, action=action, qty=base.lot_size,
                   price=price, order_type="LIMIT")

    def build(self, decision: StrategyDecision, snapshot: MarketSnapshot,
              instrument: Instrument) -> StructurePlan:
        legs = (
            self._leg(instrument, SHORT_STRIKE, "SELL", SHORT_PREMIUM),
            self._leg(instrument, HEDGE_STRIKE, "BUY", HEDGE_PREMIUM),
        )
        lot = instrument.lot_size
        width = SHORT_STRIKE - HEDGE_STRIKE
        credit_per_share = SHORT_PREMIUM - HEDGE_PREMIUM
        net_credit = credit_per_share * lot
        max_loss = (width - credit_per_share) * lot
        self.t.emit("structure_builder", "build",
                    {"net_credit": net_credit, "max_loss": max_loss},
                    msg=f"CONSTRUCT bull put spread @ NIFTY ₹{snapshot.spot:,.1f} → "
                        f"SELL {SHORT_STRIKE} PE @ ₹{SHORT_PREMIUM} | "
                        f"BUY {HEDGE_STRIKE} PE @ ₹{HEDGE_PREMIUM} (hedge) → "
                        f"net credit ₹{net_credit:,.0f}, max loss ₹{max_loss:,.0f}")
        return StructurePlan(legs=legs, net_credit=net_credit, max_loss=max_loss)
