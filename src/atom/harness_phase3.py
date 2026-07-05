"""Phase-3 PORCUPINE harness — runs scenarios_phase3.SCENARIOS against the REAL pipeline
(runner.run_once(use_tsl=True, risk_gate=True)) using the shared fixture DB, seeding
paper_trades to engineer specific account states. Companion to harness_phase1.py; see
scenarios_phase3.py docstring for the two-catalogue split (pipeline-driven vs direct).
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime

from . import config, phase1
from .atom_state import AtomState
from .penguin import PenguinReader
from .runner import run_once
from .scenarios_phase3 import SCENARIOS, Scenario

FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                    "tests", "fixtures", "capture_nifty_fixture.sqlite")


@dataclass
class Result:
    name: str
    checks: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(ok for _, ok in self.checks)


def _seed(state: AtomState, structure: str, realized_pnl: float, ts: str) -> None:
    """Write one CLOSED trade dated `ts` so derive_account()'s substr(ts,1,10)=today
    queries pick it up — same 2-call open+close sequence the real pipeline uses."""
    order = phase1.PaperOrder(structure, (("BUY", 100, "PE", 1.0), ("SELL", 200, "PE", 2.0)),
                              1000.0, 2000.0, 65, "01-JAN-2099")
    state.checkpoint_and_record("SINGLE_SPREAD", ts, order, ts, {"regime": "n/a", "confidence": 0.0})
    state.record_exit_and_checkpoint(ts, ts, "SL", realized_pnl, {}, ts)


def run(sc: Scenario) -> Result:
    r = Result(sc.name)
    cfg = {**config.DEFAULTS, **sc.config_overrides}
    phase1.configure(cfg)
    try:
        reader = PenguinReader(FIX)
        bar_ts = reader.latest_snapshot().ts
        today = bar_ts[:10]
        now = datetime.fromisoformat(bar_ts)

        with tempfile.TemporaryDirectory() as d:
            state = AtomState(os.path.join(d, "s.sqlite"))
            state.reset()
            for i, seed in enumerate(sc.seed_trades):
                _seed(state, seed.structure, seed.realized_pnl, f"{today}T09:{15+i:02d}:00")

            result = run_once(reader, state, now=now, max_stale_sec=1e9,
                             use_tsl=True, risk_gate=True, capital=sc.capital)

            e = sc.expect
            r.checks.append((f"action == {e.action}", result["action"] == e.action))
            if e.reason is not None:
                actual = result.get("reason", result.get("structure"))
                r.checks.append((f"reason == {e.reason}", actual == e.reason))
            if e.fsm_state_after is not None:
                fsm_state, _ = state.load()
                r.checks.append((f"fsm_state_after == {e.fsm_state_after}",
                                 fsm_state == e.fsm_state_after))
            if e.paper_trades_count_after is not None:
                n = state._c().execute("select count(*) from paper_trades").fetchone()[0]
                r.checks.append((f"paper_trades_count_after == {e.paper_trades_count_after}",
                                 n == e.paper_trades_count_after))
            rv = result.get("risk_verdict")
            if e.risk_verdict is not None:
                r.checks.append((f"risk_verdict == {e.risk_verdict}",
                                 rv is not None and rv.verdict == e.risk_verdict))
            if e.risk_reason_contains is not None:
                r.checks.append((f"risk reasons contain {e.risk_reason_contains}",
                                 rv is not None and e.risk_reason_contains in rv.reasons))
    finally:
        phase1.configure(dict(config.DEFAULTS))
    return r


def run_all() -> list[Result]:
    return [run(sc) for sc in SCENARIOS]
