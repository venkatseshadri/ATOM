"""Scenario tapes for the Phase 0 walking skeleton.

Each scenario is a scripted sequence of cycles (a lookup tape — NOT real strategy) that
exercises a distinct exit path: morph→EOD, stop-loss, take-profit, trailing-stop, and a
risk rejection. One scenario → one log, so each path is independently reviewable and the
same scenarios stay useful as phases turn MOCK → REAL.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Step:
    clock: str
    regime: str
    conf: float
    next_state: str = "SINGLE_SPREAD"
    stop_event: str | None = None      # None | "SL" | "TSL" | "TP"
    realized: float = 0.0
    reject: str | None = None          # risk-gate rejection reason


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    expect: str
    steps: list = field(default_factory=list)


SCENARIOS = [
    Scenario(
        name="A_morph_to_eod",
        title="Morph lifecycle → EOD square-off",
        expect="open bull put → add bear call (iron fly) → close threatened leg (runner) "
               "→ EOD exit, realized ₹+4,200",
        steps=[
            Step("09:20", "TREND_UP", 0.71, "SINGLE_SPREAD"),
            Step("11:30", "SIDEWAYS", 0.64, "IRON_FLY"),
            Step("13:30", "REVERSAL", 0.69, "RUNNER"),
            Step("15:25", "EOD", 1.00, "FLAT", realized=4200.0),
        ],
    ),
    Scenario(
        name="B_stop_loss",
        title="Stop-loss hit",
        expect="open bull put → SL breached → exit at max loss, realized ₹-1,665",
        steps=[
            Step("09:20", "TREND_UP", 0.71, "SINGLE_SPREAD"),
            Step("10:05", "TREND_DOWN", 0.66, "FLAT", stop_event="SL", realized=-1665.0),
        ],
    ),
    Scenario(
        name="C_take_profit",
        title="Take-profit hit",
        expect="open bull put → TP target (50% credit) → exit, realized ₹+2,918",
        steps=[
            Step("09:20", "TREND_UP", 0.71, "SINGLE_SPREAD"),
            Step("12:30", "SIDEWAYS", 0.70, "FLAT", stop_event="TP", realized=2918.0),
        ],
    ),
    Scenario(
        name="D_trailing_stop",
        title="Trailing-stop hit",
        expect="open bull put → rides into profit → TSL trails and triggers → "
               "exit, realized ₹+1,460",
        steps=[
            Step("09:20", "TREND_UP", 0.71, "SINGLE_SPREAD"),
            Step("14:10", "SIDEWAYS", 0.61, "FLAT", stop_event="TSL", realized=1460.0),
        ],
    ),
    Scenario(
        name="E_risk_reject",
        title="Risk gate rejects entry",
        expect="entry signal but drawdown floor breached → no order, stays FLAT",
        steps=[
            Step("09:20", "TREND_UP", 0.71, "FLAT", reject="DRAWDOWN_FLOOR_10PCT"),
        ],
    ),
]
