"""ATOM Orchestrator (Phase 0).

Wires all 16 modules into one logged pass that narrates a full session flow with
illustrative (hard-coded) values:
  connect/auth -> data capture/flow -> monitor/indicators -> entry criteria ->
  construct spread -> risk gate -> order placed (short + hedge) -> position open.
No trading logic — proves the skeleton and freezes the seams. Every one of the 16
modules emits a trace.
"""
from __future__ import annotations

import inspect
import os
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
from .modules.stop_management import StopManagement
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

    def _call(self, obj, method: str, *args, **kwargs):
        """Invoke a module method through enter/exit code-level tracing.

        Prints which file + module.method ran; the method body (the "meat") is an
        unimplemented stub in Phase 0. This is what makes the run technically
        validatable against the built files.
        """
        fn = getattr(obj, method)
        short = type(obj).__module__.rsplit(".", 1)[-1]
        qual = f"{type(obj).__name__}.{method}"
        rel = os.path.relpath(inspect.getsourcefile(fn))
        line = fn.__code__.co_firstlineno
        self.t.enter(short, qual, rel, line)
        result = fn(*args, **kwargs)
        self.t.exit(short, qual, type(result).__name__)
        return result

    def run_cycle(self, index: str = "NIFTY") -> CycleResult:
        self.t.stage("CONNECT & SESSION")
        self._call(self.session_mod, "tick")
        param_set = self._call(self.config, "parameter_set")
        account = self._call(self.config, "account_state")
        session = self._call(self.auth, "login")
        instrument = self._call(self.instrument, "resolve", index)

        self.t.stage("DATA CAPTURE & FLOW")
        snapshot = self._call(self.market_data, "snapshot", index, session)

        self.t.stage("MONITOR & DECIDE")
        regime = self._call(self.regime, "classify", snapshot)
        position = self.ledger.flat()
        decision = self._call(self.fsm, "decide", regime, position)

        self.t.stage("CONSTRUCT & RISK")
        plan = self._call(self.builder, "build", decision, snapshot, instrument)
        verdict = self._call(self.risk, "gate", plan, account, position)

        self.t.stage("EXECUTE")
        fills = self._call(self.order, "execute", plan, verdict, session)
        position = self._call(self.ledger, "apply", fills)
        self._call(self.stops, "manage", position, snapshot)

        self.t.stage("RESEARCH LOOP (offline preview)")
        findings = self._call(self.post_mortem, "analyze", [], self.t.events)
        candidate = self._call(self.optimization, "propose", findings)
        self._call(self.feedback_gate, "evaluate", candidate)

        return CycleResult(
            session=session, instrument=instrument, snapshot=snapshot,
            regime=regime, decision=decision, plan=plan, account=account,
            verdict=verdict, fills=fills, position=position,
            parameter_set=param_set,
        )
