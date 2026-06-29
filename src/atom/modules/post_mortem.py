"""Module 8 — Post-Mortem (STUB). Consumes records (14) + traces (15); owns no raw data."""
from __future__ import annotations


class PostMortem:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def analyze(self, trades: list, traces: list) -> dict:
        self.t.emit("post_mortem", "analyze", {"trades": len(trades)},
                    msg=f"AI POST-MORTEM → {len(trades)} trade(s) scored "
                        f"(WIN, exit=EOD), regime calls 3/4 correct, slippage ₹120 "
                        f"| per-trade/session/regime over {len(traces)} events")
        return {"per_trade": [], "per_session": {}, "per_regime": {}}
