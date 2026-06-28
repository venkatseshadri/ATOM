"""Module 12 — Instrument & Symbol Master (STUB). Owns expiry calendar + strike ladder + symbol."""
from __future__ import annotations

from ..contracts import Instrument


class InstrumentMaster:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def resolve(self, index: str, spot: float = 0.0,
                expiry_rule: str = "weekly") -> Instrument:
        self.t.emit("instrument", "resolve", {"index": index})
        return Instrument(tradingsymbol=f"{index}CANNED", index=index,
                          expiry="1970-01-01", strike=0.0, right="CE",
                          lot_size=1, tick_size=0.05)
