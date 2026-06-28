"""ATOM Orchestrator (Phase 0).

Wires all 16 modules into one logged pass that narrates a full session flow with
illustrative (hard-coded) values:
  connect/auth -> data capture/flow -> monitor/indicators -> entry criteria ->
  construct spread -> risk gate -> order placed (short + hedge) -> position open.
No trading logic — proves the skeleton and freezes the seams. Every one of the 16
modules emits a trace.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .telemetry import Telemetry
from .modules.auth import Auth
from .modules.instrument import InstrumentMaster
from .modules.market_data import MarketData
from .modules.regime import Regime
from .modules.strategy_fsm import StrategyFSM
from .modules.structure_builder import StructureBuilder
from .modules.risk import Risk
from .modules.order import Order
from .modules.ledger import Ledger
from .modules.stops import StopManagement
from .modules.market_session import MarketSession
from .modules.post_mortem import PostMortem
from .modules.optimization import Optimization
from .modules.feedback_gate import FeedbackGate


@dataclass
class CycleResult:
    """The contract objects produced in one pass, in pipeline order."""
    session: object
    instrument: object
    snapshot: object
    regime: object
    decision: object
    plan: object
    account: object
    verdict: object
    fills: list
    position: object
    parameter_set: object


# the 16 modules expected to emit a trace in a full pass
EXPECTED_SOURCES = {
    "config", "telemetry", "auth", "instrument", "market_data", "regime",
    "strategy_fsm", "structure_builder", "risk", "stop_management",
    "market_session", "order", "ledger", "post_mortem", "optimization",
    "feedback_gate",
}


class Orchestrator:
    def __init__(self, telemetry: Telemetry | None = None) -> None:
        self.t = telemetry or Telemetry()
        self.t.emit("telemetry", "init", msg="TELEMETRY → audit trail open")
        self.config = Config(self.t)
        self.auth = Auth(self.t)
        self.instrument = InstrumentMaster(self.t)
        self.market_data = MarketData(self.t)
        self.regime = Regime(self.t)
        self.fsm = StrategyFSM(self.t)
        self.builder = StructureBuilder(self.t)
        self.risk = Risk(self.t)
        self.order = Order(self.t)
        self.ledger = Ledger(self.t)
        self.stops = StopManagement(self.t)
        self.session_mod = MarketSession(self.t)
        self.post_mortem = PostMortem(self.t)
        self.optimization = Optimization(self.t)
        self.feedback_gate = FeedbackGate(self.t)

    def run_cycle(self, index: str = "NIFTY") -> CycleResult:
        self.t.stage("CONNECT & SESSION")
        self.session_mod.tick()
        param_set = self.config.parameter_set()
        account = self.config.account_state()
        session = self.auth.login()
        instrument = self.instrument.resolve(index)

        self.t.stage("DATA CAPTURE & FLOW")
        snapshot = self.market_data.snapshot(index, session)

        self.t.stage("MONITOR & DECIDE")
        regime = self.regime.classify(snapshot)
        position = self.ledger.flat()
        decision = self.fsm.decide(regime, position)

        self.t.stage("CONSTRUCT & RISK")
        plan = self.builder.build(decision, snapshot, instrument)
        verdict = self.risk.gate(plan, account, position)

        self.t.stage("EXECUTE")
        fills = self.order.execute(plan, verdict, session)
        position = self.ledger.apply(fills)
        self.stops.manage(position, snapshot)

        self.t.stage("RESEARCH LOOP (offline preview)")
        findings = self.post_mortem.analyze(trades=[], traces=self.t.events)
        candidate = self.optimization.propose(findings)
        self.feedback_gate.evaluate(candidate)

        return CycleResult(
            session=session, instrument=instrument, snapshot=snapshot,
            regime=regime, decision=decision, plan=plan, account=account,
            verdict=verdict, fills=fills, position=position,
            parameter_set=param_set,
        )
