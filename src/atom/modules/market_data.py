"""Module 1 — Market Data (STUB). Owns spot, chain, greeks/IV values, multi-TF OHLC."""
from __future__ import annotations

from ..contracts import MarketSnapshot, OptionQuote, Session
from ..util import now

# illustrative tick stream + chain (hard-coded — Phase 0 has no real feed)
SPOT_TICKS = [23408.0, 23411.5, 23412.5]
CHAIN = (
    OptionQuote(strike=23400, right="PE", ltp=123.40, iv=12.5,
                delta=-0.45, gamma=0.0008, theta=-9.2, vega=6.1),
    OptionQuote(strike=23300, right="PE", ltp=45.60, iv=13.1,
                delta=-0.27, gamma=0.0006, theta=-6.4, vega=4.8),
)


class MarketData:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def snapshot(self, index: str, session: Session) -> MarketSnapshot:
        self.t.emit("market_data", "feed_connect",
                    msg=f"DATA CAPTURE started → feed connected ({session.broker}) "
                        f"→ subscribing {index} chain")
        for i, s in enumerate(SPOT_TICKS, 1):
            self.t.emit("market_data", "tick", {"spot": s},
                        msg=f"tick {i}/{len(SPOT_TICKS)} flowing → {index} spot ₹{s:,.1f}")
        spot = SPOT_TICKS[-1]
        self.t.emit("market_data", "snapshot", {"spot": spot, "strikes": len(CHAIN)},
                    msg=f"snapshot assembled → spot ₹{spot:,.1f}, {len(CHAIN)} strikes, IV~12.5%")
        return MarketSnapshot(index=index, ts=now(), spot=spot,
                              chain=CHAIN, ohlc={"1m": []})
