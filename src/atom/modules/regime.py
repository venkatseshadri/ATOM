"""Module 2 — Regime (STUB). Owns the regime label from the 7 indicator families."""
from __future__ import annotations

from ..contracts import MarketSnapshot, RegimeState
from ..util import now

# illustrative 7-family read (hard-coded — no real indicators computed in Phase 0)
FAMILIES = {"SuperTrend": "bull", "ADX": 22, "RSI": 54, "ATR%": 0.4,
            "Volume": "avg", "Structure": "range", "PCR": 0.98}


class Regime:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def classify(self, snapshot: MarketSnapshot) -> RegimeState:
        # Canned: same label regardless of input (proves wiring, not behaviour).
        self.t.emit("regime", "classify", {"regime": "SIDEWAYS", "confidence": 0.62},
                    msg=f"MONITORING → 7-family read {FAMILIES} "
                        f"→ regime=SIDEWAYS (conf 0.62)")
        return RegimeState(index=snapshot.index, ts=now(),
                           regime="SIDEWAYS", confidence=0.62)
