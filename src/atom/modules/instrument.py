"""Module 12 — Instrument & Symbol Master (STUB). Owns expiry calendar + strike ladder + symbol.

Phase 0: the ATM-from-spot derivation is real deterministic math (Module 12's job); the
expiry, lot, interval and the greeks/premiums elsewhere remain illustrative constants.
"""
from __future__ import annotations

from ..contracts import Instrument

EXPIRY = "03JUL26"                      # illustrative weekly expiry
LOT = {"NIFTY": 75, "SENSEX": 20}       # illustrative lot sizes
STRIKE_STEP = {"NIFTY": 50, "SENSEX": 100}   # strike-ladder interval per index


class InstrumentMaster:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def resolve(self, index: str, spot: float = 0.0,
                expiry_rule: str = "weekly") -> Instrument:
        step = STRIKE_STEP.get(index, 50)
        lot = LOT.get(index, 75)
        atm = int(round(spot / step) * step) if spot else 0   # ATM from spot
        ladder = f"{atm - 2 * step}..{atm + 2 * step} step {step}"
        self.t.emit("instrument", "resolve",
                    {"index": index, "atm": atm, "step": step},
                    msg=f"INSTRUMENT → {index} weekly {EXPIRY}, lot {lot}, step {step} "
                        f"→ ATM = round(spot ₹{spot:,.1f} / {step}) × {step} = {atm} "
                        f"| ladder {ladder}")
        return Instrument(tradingsymbol=f"{index}{EXPIRY}{atm}PE", index=index,
                          expiry=EXPIRY, strike=float(atm), right="PE",
                          lot_size=lot, tick_size=0.05)
