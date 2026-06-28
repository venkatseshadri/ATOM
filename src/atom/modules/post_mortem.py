"""Module 8 — Post-Mortem (STUB). Consumes records (14) + traces (15); owns no raw data."""
from __future__ import annotations


class PostMortem:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def analyze(self, trades: list, traces: list) -> dict:
        self.t.emit("post_mortem", "analyze", {"trades": len(trades)},
                    msg=f"POST-MORTEM → scoring {len(trades)} trades / "
                        f"{len(traces)} trace events (per-trade/session/regime)")
        return {"per_trade": [], "per_session": {}, "per_regime": {}}
