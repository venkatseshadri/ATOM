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

    def classify(self, snapshot: MarketSnapshot, scripted: str | None = None,
                 confidence: float = 0.62) -> RegimeState:
        # Default canned label (same regardless of input — proves wiring, T0.3).
        # `scripted` is used only by the session-demo to walk the lifecycle tape.
        label = scripted or "SIDEWAYS"
        self.t.emit("regime", "classify", {"regime": label, "confidence": confidence},
                    msg=f"MONITORING → 7-family read {FAMILIES} "
                        f"→ regime={label} (conf {confidence})")
        return RegimeState(index=snapshot.index, ts=now(),
                           regime=label, confidence=confidence)
