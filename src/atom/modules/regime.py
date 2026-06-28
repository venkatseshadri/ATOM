"""Module 2 — Regime (STUB). Owns the regime label from the 7 indicator families."""
from __future__ import annotations

from ..contracts import MarketSnapshot, RegimeState
from ..util import now


class Regime:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def classify(self, snapshot: MarketSnapshot) -> RegimeState:
        self.t.emit("regime", "classify", {"index": snapshot.index})
        # Canned: same label regardless of input (proves wiring, not behaviour).
        return RegimeState(index=snapshot.index, ts=now(),
                           regime="SIDEWAYS", confidence=0.0)
