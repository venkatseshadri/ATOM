"""Scenario tapes + expectations for the ATOM PORCUPINE-style harness.

Each scenario is a scripted sequence of cycles (a lookup tape — NOT real strategy) that
exercises a distinct exit path, paired with an `Expect` block the harness asserts. One
scenario → one log; the same scenarios stay useful (and the assertions get stricter) as
phases turn modules MOCK → REAL.
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
class Expect:
    """What the harness asserts after running the scenario."""
    end_state: str                     # final FSM state
    realized_sign: str                 # "+" | "-" | "0"
    states: tuple = ()                 # expected ledger state progression
    exit_reason: str | None = None     # SL | TSL | TP (stop-raised exits)


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    expect: str                        # human-readable description
    steps: list = field(default_factory=list)
    spec: Expect = None                # machine-checkable expectations


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
        spec=Expect(end_state="FLAT", realized_sign="+",
                    states=("SINGLE_SPREAD", "IRON_FLY", "RUNNER", "FLAT")),
    ),
    Scenario(
        name="B_stop_loss",
        title="Stop-loss hit",
        expect="open bull put → SL breached → exit at max loss, realized ₹-1,665",
        steps=[
            Step("09:20", "TREND_UP", 0.71, "SINGLE_SPREAD"),
            Step("10:05", "TREND_DOWN", 0.66, "FLAT", stop_event="SL", realized=-1665.0),
        ],
        spec=Expect(end_state="FLAT", realized_sign="-",
                    states=("SINGLE_SPREAD", "FLAT"), exit_reason="SL"),
    ),
    Scenario(
        name="C_take_profit",
        title="Take-profit hit",
        expect="open bull put → TP target (50% credit) → exit, realized ₹+2,918",
        steps=[
            Step("09:20", "TREND_UP", 0.71, "SINGLE_SPREAD"),
            Step("12:30", "SIDEWAYS", 0.70, "FLAT", stop_event="TP", realized=2918.0),
        ],
        spec=Expect(end_state="FLAT", realized_sign="+",
                    states=("SINGLE_SPREAD", "FLAT"), exit_reason="TP"),
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
        spec=Expect(end_state="FLAT", realized_sign="+",
                    states=("SINGLE_SPREAD", "FLAT"), exit_reason="TSL"),
    ),
    Scenario(
        name="E_risk_reject",
        title="Risk gate rejects entry",
        expect="entry signal but drawdown floor breached → no order, stays FLAT",
        steps=[
            Step("09:20", "TREND_UP", 0.71, "FLAT", reject="DRAWDOWN_FLOOR_10PCT"),
        ],
        spec=Expect(end_state="FLAT", realized_sign="0", states=()),
    ),
]
