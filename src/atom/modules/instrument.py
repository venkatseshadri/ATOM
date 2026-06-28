"""Module 12 — Instrument & Symbol Master (STUB). Owns expiry calendar + strike ladder + symbol."""
from __future__ import annotations

from ..contracts import Instrument

EXPIRY = "03JUL26"          # illustrative weekly expiry
LOT = 75                    # illustrative NIFTY lot size


class InstrumentMaster:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def resolve(self, index: str, spot: float = 0.0,
                expiry_rule: str = "weekly") -> Instrument:
        self.t.emit("instrument", "resolve", {"index": index, "expiry": EXPIRY},
                    msg=f"INSTRUMENT master → {index} weekly expiry {EXPIRY}, "
                        f"lot {LOT}, tick 0.05")
        return Instrument(tradingsymbol=f"{index}{EXPIRY}ATM", index=index,
                          expiry=EXPIRY, strike=0.0, right="PE",
                          lot_size=LOT, tick_size=0.05)
