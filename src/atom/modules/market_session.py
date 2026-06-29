"""Module 7 — Session Lifecycle (STUB). Market-session time authority (NOT broker session).

Owns the trading-day clock + the (mock) cron schedule that drives capture, entry window,
square-off, and the EOD research timer.
"""
from __future__ import annotations

# illustrative cron schedule (systemd-timer style names — Phase 0 mock)
CRON = [
    ("09:14", "atom-capture.timer", "feed warmup / connect"),
    ("09:15", "atom-session.timer", "entry window opens"),
    ("*/1m 09:20–15:20", "atom-cycle.timer", "intraday decision cycle"),
    ("15:20", "atom-lockout.timer", "no new entries"),
    ("15:30", "atom-squareoff.timer", "force EOD square-off"),
    ("15:45", "atom-research.timer", "EOD AI research loop (offline)"),
    ("08:45+1", "atom-approval.timer", "morning human approval gate"),
]


class MarketSession:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def schedule(self) -> list:
        self.t.emit("market_session", "schedule", {"jobs": len(CRON)},
                    msg="MOCK CRON schedule (illustrative):")
        for when, unit, what in CRON:
            self.t.emit("market_session", "cron_job", {"when": when, "unit": unit},
                        msg=f"    {when:<18} {unit:<22} → {what}")
        return CRON

    def tick(self, clock: str = "09:20") -> str:
        self.t.emit("market_session", "tick", {"clock": clock, "phase": "OPEN"},
                    msg=f"SESSION → {clock} market OPEN, within entry window")
        return "OPEN"

    def is_square_off(self) -> bool:
        return False
