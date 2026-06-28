"""Module 1 — Market Data (STUB). Owns spot, chain, greeks/IV values, multi-TF OHLC."""
from __future__ import annotations

from ..contracts import MarketSnapshot, OptionQuote, Session
from ..util import now


class MarketData:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def snapshot(self, index: str, session: Session) -> MarketSnapshot:
        self.t.emit("market_data", "snapshot", {"index": index})
        quote = OptionQuote(strike=0.0, right="CE", ltp=0.0, iv=0.0,
                            delta=0.0, gamma=0.0, theta=0.0, vega=0.0)
        return MarketSnapshot(index=index, ts=now(), spot=0.0,
                              chain=(quote,), ohlc={"1m": []})
