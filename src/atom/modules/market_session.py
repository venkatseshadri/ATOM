"""Module 7 — Session Lifecycle (STUB). Market-session time authority (NOT broker session)."""
from __future__ import annotations


class MarketSession:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def tick(self) -> str:
        self.t.emit("market_session", "tick", {"phase": "OPEN"})
        return "OPEN"

    def is_square_off(self) -> bool:
        return False
