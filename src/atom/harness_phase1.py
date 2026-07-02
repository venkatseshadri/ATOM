"""Phase-1 PORCUPINE harness — runs scenarios_phase1.SCENARIOS against the REAL pipeline
(runner.run_once) using the shared fixture DB, asserts each scenario's Expect block.
Companion to harness.py (Phase-0 skeleton scenarios); see scenarios_phase1.py docstring
for why these are separate tracks.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import config, phase1
from .atom_state import AtomState
from .penguin import PenguinReader
from .runner import run_once
from .scenarios_phase1 import SCENARIOS, Scenario

FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                    "tests", "fixtures", "capture_nifty_fixture.sqlite")


class _EmptyChainReader:
    """Wraps a real PenguinReader but strips the option chain — forces build_order()
    to find no premium, without fabricating fake indicator data."""
    def __init__(self, inner: PenguinReader) -> None:
        self._inner = inner

    def latest_snapshot(self, strike_window: int = 6):
        s = self._inner.latest_snapshot(strike_window)
        return None if s is None else type(s)(s.ts, s.spot, s.atm_strike, s.expiry,
                                                s.days_to_expiry, s.ind, {})


@dataclass
class Result:
    name: str
    checks: list = field(default_factory=list)   # (description, ok)

    @property
    def passed(self) -> bool:
        return all(ok for _, ok in self.checks)


def _run_once_for(sc: Scenario, reader, state, now, max_stale):
    return run_once(reader, state, now=now, max_stale_sec=max_stale)


def run(sc: Scenario) -> Result:
    r = Result(sc.name)
    cfg = {**config.DEFAULTS, **sc.config_overrides}
    phase1.configure(cfg)
    try:
        base_reader = PenguinReader(FIX)
        reader = _EmptyChainReader(base_reader) if sc.empty_chain else base_reader
        bar_ts = base_reader.latest_snapshot().ts
        now = datetime.fromisoformat(bar_ts) + timedelta(seconds=sc.now_offset_sec)
        max_stale = sc.stale_max_sec if sc.stale_max_sec is not None else 1e9

        with tempfile.TemporaryDirectory() as d:
            state = AtomState(os.path.join(d, "s.sqlite"))
            state.reset()
            if sc.pre_seed_fsm:
                state.checkpoint_and_record(sc.pre_seed_fsm, "2000-01-01T00:00:00", None,
                                            now.isoformat(), {})

            result = _run_once_for(sc, reader, state, now, max_stale)

            if sc.replay:
                replay_result = _run_once_for(sc, reader, state, now, max_stale)
                r.checks.append(("replay: second run == NO_OP/no_new_bar",
                                 replay_result["action"] == "NO_OP"
                                 and replay_result.get("reason") == "no_new_bar"))

            e = sc.expect
            r.checks.append((f"action == {e.action}", result["action"] == e.action))
            if e.reason is not None:
                # early-exit paths (no_data/no_new_bar/stale_feed) key it "reason";
                # a full cycle's STAND_DOWN/SKIP sub-reason is decision["structure"]
                # instead — there is no "reason" key at all on that path.
                actual = result.get("reason", result.get("structure"))
                r.checks.append((f"reason == {e.reason}", actual == e.reason))
            if e.regime is not None:
                r.checks.append((f"regime == {e.regime}", result.get("regime") == e.regime))
            if e.confidence is not None:
                r.checks.append((f"confidence == {e.confidence}",
                                 result.get("confidence") == e.confidence))
            if e.fsm_state_after is not None:
                fsm_state, _ = state.load()
                r.checks.append((f"fsm_state_after == {e.fsm_state_after}",
                                 fsm_state == e.fsm_state_after))
            if e.paper_trades_count_after is not None:
                n = state._c().execute("select count(*) from paper_trades").fetchone()[0]
                r.checks.append((f"paper_trades_count_after == {e.paper_trades_count_after}",
                                 n == e.paper_trades_count_after))
    finally:
        phase1.configure(dict(config.DEFAULTS))
    return r


def run_all() -> list[Result]:
    return [run(sc) for sc in SCENARIOS]
