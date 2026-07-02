"""Phase-1 PORCUPINE scenarios — drive the REAL pipeline (runner.run_once / phase1.cycle),
not the Phase-0 skeleton's scripted Orchestrator tape. Each scenario replays the shared
fixture DB (real Penguin snapshot shape) with targeted config/state forcing to land on a
specific branch, then asserts the real output — same pattern used for manual validation
during the 2026-07-02 GATE 1 review, made permanent here.

Phase 0's scenarios (scenarios.py / harness.py) test a scripted morph/hold/exit lifecycle
that doesn't exist in real code yet (that's Phase 3+). These test what's actually live.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Expect:
    """What the harness asserts after running the scenario."""
    action: str                        # OPEN | SKIP | STAND_DOWN | NO_OP
    reason: str | None = None          # e.g. single_position_open, premiums_unavailable
    regime: str | None = None
    confidence: float | None = None
    fsm_state_after: str | None = None
    paper_trades_count_after: int | None = None


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    expect_desc: str                   # human-readable description
    config_overrides: dict = field(default_factory=dict)
    pre_seed_fsm: str | None = None    # force fsm_state before running (default FLAT)
    empty_chain: bool = False          # wipe the option chain to force premiums_unavailable
    stale_max_sec: float | None = None # override max_stale_sec very low to force stale_feed
    now_offset_sec: float = 0.0        # shift 'now' relative to the fixture bar's ts
    replay: bool = False               # run twice on the same bar, check idempotency too
    expect: Expect = None


SCENARIOS = [
    Scenario(
        name="P1_clean_trend_up_opens",
        title="Clean bullish vote opens a real bull put spread",
        expect_desc="min_confidence forced to 0 so the fixture's regime always clears the "
                     "gate; asserts a real premium order is constructed and fsm flips",
        config_overrides={"regime.entry.min_confidence": 0.0},
        expect=Expect(action="OPEN", fsm_state_after="SINGLE_SPREAD",
                      paper_trades_count_after=1),
    ),
    Scenario(
        name="P1_low_confidence_stands_down",
        title="Below-threshold confidence stands down, does not open",
        expect_desc="min_confidence forced above 1.0 (unreachable) → STAND_DOWN, fsm stays FLAT",
        config_overrides={"regime.entry.min_confidence": 1.01},
        expect=Expect(action="STAND_DOWN", reason="low_confidence",
                      fsm_state_after="FLAT", paper_trades_count_after=0),
    ),
    Scenario(
        name="P1_already_open_skips",
        title="Position already open — SKIP regardless of regime/confidence",
        expect_desc="fsm pre-seeded to SINGLE_SPREAD; even with min_confidence=0 (would "
                     "otherwise OPEN), decide() must stop at branch 1",
        config_overrides={"regime.entry.min_confidence": 0.0},
        pre_seed_fsm="SINGLE_SPREAD",
        expect=Expect(action="SKIP", reason="single_position_open",
                      fsm_state_after="SINGLE_SPREAD", paper_trades_count_after=0),
    ),
    Scenario(
        name="P1_premiums_missing_overrules_open",
        title="decide()=OPEN but build_order() finds no real premium",
        expect_desc="empty option chain → cycle() overrules OPEN to STAND_DOWN "
                     "(premiums_unavailable); fsm must NOT falsely flip to SINGLE_SPREAD",
        config_overrides={"regime.entry.min_confidence": 0.0},
        empty_chain=True,
        expect=Expect(action="STAND_DOWN", reason="premiums_unavailable",
                      fsm_state_after="FLAT", paper_trades_count_after=0),
    ),
    Scenario(
        name="P1_stale_feed_stands_down",
        title="Bar older than max_stale_sec → STAND_DOWN, not silently accepted",
        expect_desc="max_stale_sec forced to 1s while 'now' is pinned to the bar's own "
                     "timestamp + 150s of drift",
        stale_max_sec=1.0,
        now_offset_sec=150.0,
        expect=Expect(action="STAND_DOWN", reason="stale_feed"),
    ),
    Scenario(
        name="P1_replay_same_bar_no_duplicate",
        title="Replaying the identical bar must not duplicate the order or re-fire fsm",
        expect_desc="run once (OPEN), then run again on the same bar_ts — second run must "
                     "be NO_OP/no_new_bar with paper_trades count unchanged",
        config_overrides={"regime.entry.min_confidence": 0.0},
        replay=True,
        expect=Expect(action="OPEN", fsm_state_after="SINGLE_SPREAD",
                      paper_trades_count_after=1),
    ),
]
