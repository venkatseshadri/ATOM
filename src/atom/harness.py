"""ATOM PORCUPINE-style regression harness.

Runs each scenario through the real Orchestrator, reads the resulting telemetry, and
asserts the scenario's `Expect` block (end state, P&L sign, FSM state path, exit reason).
Green/red per scenario.

Phase 0: drives the walking skeleton (scripted tapes). As modules turn REAL, the SAME
harness asserts real behaviour — add fault injection + tighter expectations per phase.
This is the seam through which ATOM "leverages PORCUPINE" once modules are built.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .orchestrator import Orchestrator
from .scenarios import SCENARIOS, Scenario
from .telemetry import Telemetry


def _sign(x: float) -> str:
    return "+" if x > 0 else "-" if x < 0 else "0"


@dataclass
class Result:
    name: str
    checks: list = field(default_factory=list)   # (description, ok)

    @property
    def passed(self) -> bool:
        return all(ok for _, ok in self.checks)


def run(scenario: Scenario) -> Result:
    orch = Orchestrator(Telemetry(echo=False))
    final = orch.run_scenario(scenario)
    ev = orch.t.events

    states = tuple(e.payload["state"] for e in ev
                   if e.source == "ledger" and e.type == "apply")
    breaches = [e.payload.get("event") for e in ev
                if e.source == "stop_management" and e.type == "breach"]

    r = Result(scenario.name)
    spec = scenario.spec
    r.checks.append((f"end state == {spec.end_state}",
                     final.fsm_state == spec.end_state))
    r.checks.append((f"realized sign == {spec.realized_sign}",
                     _sign(final.realized_pnl) == spec.realized_sign))
    if spec.states:
        r.checks.append((f"state path == {spec.states}", states == spec.states))
    if spec.exit_reason:
        r.checks.append((f"exit reason == {spec.exit_reason}",
                         spec.exit_reason in breaches))
    # invariant every scenario must hold: end the day flat (no overnight risk)
    r.checks.append(("invariant: ends FLAT (no overnight)", final.fsm_state == "FLAT"))
    return r


def run_all() -> list[Result]:
    return [run(sc) for sc in SCENARIOS]
