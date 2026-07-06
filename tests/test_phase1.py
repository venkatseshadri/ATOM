"""Phase 1 validation — real Penguin pipeline (reader + 7-family regime + FSM entry +
real-premium construction + freshness gate + state)."""
import os
from datetime import datetime

import pytest

from atom import phase1
from atom.atom_state import AtomState
from atom.penguin import PenguinReader
from atom.runner import run_once

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "capture_nifty_fixture.sqlite")


def _now_at_bar(reader):
    return datetime.fromisoformat(reader.latest_snapshot().ts)


# ---- reader ----------------------------------------------------------------

def test_reader_snapshot_has_real_fields():
    s = PenguinReader(FIX).latest_snapshot()
    assert s.spot > 0 and s.atm_strike > 0 and s.expiry
    assert s.chain, "option chain (real premiums) must be populated"


def test_reader_is_readonly():
    before = os.path.getmtime(FIX)
    PenguinReader(FIX).latest_snapshot()
    assert os.path.getmtime(FIX) == before, "ATOM must never write the Penguin DB"


# ---- regime (7-family consensus) -------------------------------------------

def test_regime_bull_row():
    ind = {"st_consensus": "bullish", "rsi": 62, "ema20_slope": 1.0, "structure_type": "HH",
           "pcr_total": 0.8, "sentiment": "bullish", "vwap": 100, "spot": 110, "adx": 30}
    label, conf, *_ = phase1.classify_regime(ind)
    assert label == "TREND_UP" and conf > 0


def test_regime_bear_row():
    ind = {"st_consensus": "bearish", "rsi": 20, "ema20_slope": -1.0, "structure_type": "LL",
           "pcr_total": 1.3, "sentiment": "bearish", "vwap": 110, "spot": 100, "adx": 40}
    label, *_ = phase1.classify_regime(ind)
    assert label == "TREND_DOWN"


def test_regime_low_adx_is_sideways():
    ind = {"st_consensus": "bullish", "rsi": 62, "structure_type": "HH", "adx": 10}
    label, *_ = phase1.classify_regime(ind)
    assert label == "SIDEWAYS"


# ---- FSM entry decision ----------------------------------------------------

_MORNING = "2026-07-03T09:20:00"
_AFTER_EOD = "2026-07-03T15:25:00"   # past the 15:20 default cutoff


def test_decide_opens_on_trend():
    assert phase1.decide("FLAT", "TREND_UP", 0.7, _MORNING) == ("OPEN", "bull_put_spread")
    assert phase1.decide("FLAT", "TREND_DOWN", 0.7, _MORNING) == ("OPEN", "bear_call_spread")


def test_decide_skips_when_position_open():
    assert phase1.decide("SINGLE_SPREAD", "TREND_UP", 0.9, _MORNING)[0] == "SKIP"


def test_decide_stands_down_low_conf_and_sideways():
    assert phase1.decide("FLAT", "TREND_UP", 0.1, _MORNING)[0] == "STAND_DOWN"
    assert phase1.decide("FLAT", "SIDEWAYS", 0.9, _MORNING)[0] == "STAND_DOWN"


def test_decide_refuses_new_entry_past_eod_cutoff():
    """The bug this guards: without this gate, a position could open right at/after
    the EOD cutoff and get force-closed 1-2 minutes later by check_exit's is_eod,
    repeating in a churn loop — confirmed live 2026-07-03, 4 such ~2min trades."""
    intent, reason = phase1.decide("FLAT", "TREND_UP", 0.9, _AFTER_EOD)
    assert intent == "STAND_DOWN" and reason == "after_eod_cutoff"
    # even a clean, high-confidence trend must not open this late
    assert phase1.decide("FLAT", "TREND_DOWN", 0.99, _AFTER_EOD)[0] == "STAND_DOWN"


# ---- construction uses REAL premiums (no fabrication) ----------------------

def test_build_order_real_premiums():
    s = PenguinReader(FIX).latest_snapshot()
    o = phase1.build_order("bear_call_spread", s)
    assert o is not None
    assert o.legs[0][0] == "BUY" and o.legs[1][0] == "SELL"  # hedge first
    assert all(leg[3] is not None for leg in o.legs)        # real LTPs
    assert o.net_credit != 0


def test_build_order_none_when_premium_missing():
    s = PenguinReader(FIX).latest_snapshot()
    s2 = type(s)(s.ts, s.spot, 99999, s.expiry, s.days_to_expiry, s.ind, {})  # empty chain
    assert phase1.build_order("bull_put_spread", s2) is None


# ---- cycle purity ----------------------------------------------------------

def test_cycle_is_deterministic():
    s = PenguinReader(FIX).latest_snapshot()
    a = phase1.cycle("FLAT", s)
    b = phase1.cycle("FLAT", s)
    assert a[1] == b[1] and (a[2] is None) == (b[2] is None)


# ---- runner: freshness gate + state ----------------------------------------

def test_run_once_enters_then_skips(tmp_path):
    reader = PenguinReader(FIX)
    state = AtomState(str(tmp_path / "s.sqlite"))
    r1 = run_once(reader, state, now=_now_at_bar(reader), max_stale_sec=1e9)
    assert r1["action"] in ("OPEN", "STAND_DOWN")          # depends on the real bar
    # second run on the SAME bar → idempotent no-op
    r2 = run_once(reader, state, now=_now_at_bar(reader), max_stale_sec=1e9)
    assert r2["action"] == "NO_OP" and r2["reason"] == "no_new_bar"


def test_replay_same_bar_does_not_duplicate_order(tmp_path):
    """A real OPEN, replayed on the identical bar, must not double-write paper_trades
    or re-fire the FSM transition — no_new_bar has to short-circuit before decide()."""
    phase1.configure({**phase1.config.DEFAULTS, "regime.entry.min_confidence": 0.0})
    try:
        reader = PenguinReader(FIX)
        state = AtomState(str(tmp_path / "s.sqlite"))
        now = _now_at_bar(reader)
        r1 = run_once(reader, state, now=now, max_stale_sec=1e9)
        assert r1["action"] == "OPEN"
        n1 = state._c().execute("select count(*) from paper_trades").fetchone()[0]
        assert n1 == 1

        r2 = run_once(reader, state, now=now, max_stale_sec=1e9)
        assert r2["action"] == "NO_OP" and r2["reason"] == "no_new_bar"
        n2 = state._c().execute("select count(*) from paper_trades").fetchone()[0]
        assert n2 == 1                          # unchanged, no duplicate
        assert state.load() == ("SINGLE_SPREAD", now.isoformat())
    finally:
        phase1.configure(dict(phase1.config.DEFAULTS))


def test_checkpoint_and_trade_land_in_one_transaction(tmp_path):
    """fsm_state and the paper_trades row for an OPEN must never be visible apart —
    checkpoint() and record_paper_trade() used to be two separate connections/commits,
    which left a crash window producing fsm=SINGLE_SPREAD with no matching trade row."""
    phase1.configure({**phase1.config.DEFAULTS, "regime.entry.min_confidence": 0.0})
    try:
        reader = PenguinReader(FIX)
        state = AtomState(str(tmp_path / "s.sqlite"))
        r = run_once(reader, state, now=_now_at_bar(reader), max_stale_sec=1e9)
        assert r["action"] == "OPEN"
        fsm_state, _ = state.load()
        n = state._c().execute("select count(*) from paper_trades").fetchone()[0]
        # if these ever disagree, the atomic transaction broke
        assert (fsm_state == "SINGLE_SPREAD") == (n == 1)
        assert fsm_state == "SINGLE_SPREAD" and n == 1
    finally:
        phase1.configure(dict(phase1.config.DEFAULTS))


def test_stale_feed_stands_down(tmp_path):
    reader = PenguinReader(FIX)
    state = AtomState(str(tmp_path / "s.sqlite"))
    r = run_once(reader, state, now=datetime.now(), max_stale_sec=1.0)
    assert r["action"] == "STAND_DOWN" and r["reason"] == "stale_feed"


def test_checkpoint_advances_cursor(tmp_path):
    reader = PenguinReader(FIX)
    state = AtomState(str(tmp_path / "s.sqlite"))
    run_once(reader, state, now=_now_at_bar(reader), max_stale_sec=1e9)
    _, last_bar_ts = state.load()
    assert last_bar_ts == reader.latest_snapshot().ts


# ---- config (dotted-key trading configuration) ------------------------------

def test_config_loads_and_coerces():
    from atom import config
    cfg = config.load_config()
    assert cfg["strategy.wing.strikes"] == 4 and isinstance(cfg["strategy.wing.strikes"], int)
    assert cfg["indicator.ema.enabled"] is True
    assert isinstance(cfg["regime.entry.min_confidence"], float)


def test_config_drives_wing(tmp_path):
    from atom import config, phase1
    p = tmp_path / "c.conf"
    p.write_text("strategy.wing.strikes=2\nstrategy.lot.size=50\n")
    phase1.configure(config.load_config(str(p)))
    s = PenguinReader(FIX).latest_snapshot()
    o = phase1.build_order("bear_call_spread", s)
    assert o.lot == 50
    assert abs(o.legs[0][1] - o.legs[1][1]) == 100   # wing = 2 strikes * 50
    phase1.configure(config.load_config())           # restore defaults


# ---- operator-log presentation views (family / branch / AI mock) ------------

def test_family_view_maps_votes_to_words():
    votes = {"trend": 1, "momentum": -1, "price_action": 0, "structure": -1,
             "sentiment": 1, "participation": 0, "volatility": 0}
    fv = phase1.family_view(votes)
    assert len(fv) == 7                              # all 7 families, ordered
    assert fv[0] == ("Trend (SuperTrend)", "Up")
    assert fv[1][1] == "Down" and fv[2][1] == "Neutral"
    assert fv[6][1] == "Neutral"                     # volatility always neutral


def test_branch_scores_winner_matches_regime():
    ind = {"st_consensus": "bearish", "rsi": 20, "ema20_slope": -1.0, "structure_type": "LL",
           "pcr_total": 1.3, "sentiment": "bearish", "vwap": 110, "spot": 100, "adx": 40}
    label, conf, probs, _ = phase1.classify_regime(ind)
    b = phase1.branch_scores(ind)
    assert set(b) == {"NotUp", "NotDown", "Sideways"}
    # probs alias the real three-way; winner branch maps back to the regime
    assert b["NotUp"]["prob"] == probs["DOWN"] and b["NotUp"]["regime"] == "TREND_DOWN"
    winner = max(b, key=lambda k: b[k]["prob"])
    assert b[winner]["regime"] == label
    assert b["NotUp"]["value"] >= 1                  # raw bearish family mass


# ---- exit check: SL / TP / EOD (minimal slice) ------------------------------

class _FakeReader:
    """Controlled per-leg prices — check_exit() only needs latest_price_for()."""
    def __init__(self, prices: dict):
        self.prices = prices   # {(strike, right): (ltp, ts)}

    def latest_price_for(self, expiry, strike, right, index="NIFTY"):
        return self.prices.get((strike, right))


_POSITION = {
    "ts": "2026-07-01T09:45:00", "bar_ts": "2026-07-01T09:44:00",
    "structure": "bull_put_spread", "net_credit": 5370.0, "max_loss": 9630.0, "lot": 75,
    "legs": [["BUY", 23750, "PE", 233.75], ["SELL", 23950, "PE", 305.35]],
    "expiry": "28-JUL-2026",
}


def _reader_at(hedge_ltp, short_ltp, ts="2026-07-02T10:00:00"):
    return _FakeReader({(23750, "PE"): (hedge_ltp, ts), (23950, "PE"): (short_ltp, ts)})


def test_check_exit_still_holding_no_trigger():
    # small favorable move, nowhere near SL or TP
    ec = phase1.check_exit(_POSITION, _reader_at(220.0, 290.0), "2026-07-02T10:00:00")
    assert not ec.triggered and ec.reason is None
    assert ec.current_pnl is not None


def test_check_exit_sl_triggers():
    # spread moves hard against the position — mark-to-market loss past 35% of max_loss
    ec = phase1.check_exit(_POSITION, _reader_at(233.75, 380.0), "2026-07-02T10:00:00")
    assert ec.triggered and ec.reason == "SL"
    assert ec.current_pnl <= ec.sl_threshold


def test_check_exit_tp_triggers():
    # both legs decay hard — captured most of the credit
    ec = phase1.check_exit(_POSITION, _reader_at(50.0, 60.0), "2026-07-02T10:00:00")
    assert ec.triggered and ec.reason == "TP"
    assert ec.current_pnl >= ec.tp_threshold


def test_check_exit_eod_forces_close_even_mid_range():
    # P&L sits between SL and TP thresholds, but bar time is past the EOD cutoff
    ec = phase1.check_exit(_POSITION, _reader_at(220.0, 290.0), "2026-07-02T15:25:00")
    assert ec.triggered and ec.reason == "EOD" and ec.is_eod


def test_check_exit_eod_forces_close_even_with_no_price_data():
    """The whole point of EOD: a stuck position with stale/missing prices must still
    close, not silently stay open forever waiting for data that may never arrive."""
    reader = _FakeReader({})   # neither leg has any price at all
    ec = phase1.check_exit(_POSITION, reader, "2026-07-02T15:25:00")
    assert ec.triggered and ec.reason == "EOD"
    assert ec.current_pnl is None


def test_check_exit_missing_price_does_not_falsely_trigger_sl_tp():
    reader = _FakeReader({(23750, "PE"): (220.0, "2026-07-02T10:00:00")})  # short leg missing
    ec = phase1.check_exit(_POSITION, reader, "2026-07-02T10:00:00")
    assert not ec.triggered and ec.current_pnl is None


# ---- Phase 3: check_exit(levels_state=...) real TSL path (opt-in, legacy unaffected) --

def test_check_exit_levels_state_none_is_byte_identical_to_legacy():
    """Omitting levels_state (every test above) must match passing an explicit None."""
    a = phase1.check_exit(_POSITION, _reader_at(220.0, 290.0), "2026-07-02T10:00:00")
    b = phase1.check_exit(_POSITION, _reader_at(220.0, 290.0), "2026-07-02T10:00:00",
                          levels_state=None)
    assert a == b


def test_check_exit_levels_state_fresh_matches_legacy_thresholds():
    """A fresh (never-armed) levels_state must produce the same sl/tp as the legacy
    static formula — Module 6's initial_levels() uses the identical pct-of-max-loss /
    pct-of-credit math."""
    ec = phase1.check_exit(_POSITION, _reader_at(220.0, 290.0), "2026-07-02T10:00:00",
                           levels_state={})
    assert ec.sl_threshold == -round(0.35 * 9630.0, 2)
    assert ec.tp_threshold == round(0.50 * 5370.0, 2)
    assert ec.tsl_armed is False


def test_check_exit_tsl_arms_trails_and_never_loosens_across_cycles():
    """Simulates the real caller loop: thread the returned tsl/tsl_armed/high_water_pnl
    into the next cycle's levels_state, exactly as runner.py will via AtomState."""
    levels_state = {}
    # cycle 1: strong favorable move (~60% of credit captured) -> arms + trails
    ec1 = phase1.check_exit(_POSITION, _reader_at(10.0, 38.64), "2026-07-02T10:00:00",
                            levels_state=levels_state)
    assert ec1.tsl_armed is True and ec1.tsl is not None
    tight = ec1.tsl

    # cycle 2: reverses hard (~35% captured) — must not un-arm or loosen
    levels_state = {"tsl": ec1.tsl, "tsl_armed": ec1.tsl_armed,
                    "high_water_pnl": ec1.high_water_pnl}
    ec2 = phase1.check_exit(_POSITION, _reader_at(10.0, 56.54), "2026-07-02T10:01:00",
                            levels_state=levels_state)
    assert ec2.tsl_armed is True
    assert ec2.tsl == tight   # ratchet: unchanged, never loosened
    assert not ec2.triggered   # pnl still above the trailed floor -> holding, not exiting


def test_record_exit_and_checkpoint_atomic(tmp_path):
    state = AtomState(str(tmp_path / "s.sqlite"))
    state.checkpoint_and_record("SINGLE_SPREAD", "2026-07-01T09:44:00",
                                 phase1.PaperOrder("bull_put_spread",
                                                    (("BUY", 23750, "PE", 233.75),
                                                     ("SELL", 23950, "PE", 305.35)),
                                                    5370.0, 9630.0, 75, "28-JUL-2026"),
                                 "2026-07-01T09:45:00.000000",
                                 {"regime": "TREND_UP", "confidence": 0.75})
    pos = state.last_open_position()
    assert pos is not None

    state.record_exit_and_checkpoint(pos["ts"], "2026-07-02T15:25:00", "EOD", 7113.75,
                                     {"hedge_ltp": 196.65, "short_ltp": 173.4},
                                     "2026-07-02T15:25:00")
    assert state.load() == ("FLAT", "2026-07-02T15:25:00")
    assert state.last_open_position() is None          # closed, no longer "open"


# ---- Phase 3: run_once(use_tsl=, risk_gate=) opt-in wiring, legacy default off ----

def test_run_once_defaults_are_legacy_behavior(tmp_path):
    """use_tsl/risk_gate default False — confirms the opt-in flags don't change
    anything unless a caller explicitly asks for them."""
    reader = PenguinReader(FIX)
    state = AtomState(str(tmp_path / "s.sqlite"))
    r = run_once(reader, state, now=_now_at_bar(reader), max_stale_sec=1e9)
    assert r["risk_verdict"] is None


def test_run_once_audit_logs_decision_and_risk_verdict(tmp_path):
    """Module 15 wiring: a real OPEN under risk_gate=True must produce a
    reconstructable decision.open + risk.approved pair under the same trade_id."""
    from atom.audit import AuditTrail
    phase1.configure({**phase1.config.DEFAULTS, "regime.entry.min_confidence": 0.0})
    try:
        reader = PenguinReader(FIX)
        state = AtomState(str(tmp_path / "s.sqlite"))
        trail = AuditTrail(str(tmp_path / "audit.sqlite"))
        now = _now_at_bar(reader)
        r = run_once(reader, state, now=now, max_stale_sec=1e9, risk_gate=True, audit=trail)
        assert r["action"] == "OPEN"
        events = trail.reconstruct_trade(now.isoformat())
        types = [e.type for e in events]
        assert "decision.open" in types and "risk.approved" in types
    finally:
        phase1.configure(dict(phase1.config.DEFAULTS))


def test_run_once_audit_logs_exit_trigger(tmp_path, monkeypatch):
    """A triggered exit must log a position.*_trigger event keyed by the
    ORIGINAL position's entry ts, not the exit cycle's own timestamp."""
    from atom.audit import AuditTrail
    reader = PenguinReader(FIX)
    state = AtomState(str(tmp_path / "s.sqlite"))
    trail = AuditTrail(str(tmp_path / "audit.sqlite"))
    state.checkpoint_and_record("SINGLE_SPREAD", "2000-01-01T00:00:00",
                                phase1.PaperOrder("bull_put_spread",
                                    (("BUY", 23750, "PE", 233.75), ("SELL", 23950, "PE", 305.35)),
                                    5370.0, 9630.0, 75, "28-JUL-2026"),
                                "2000-01-01T00:00:00.000000", {"regime": "TREND_UP", "confidence": 0.75})
    monkeypatch.setattr(phase1, "check_exit",
                        lambda position, rdr, bar_ts, levels_state=None: phase1.ExitCheck(
                            True, "TP", 5000.0, 200.0, bar_ts, 100.0, bar_ts, -1000.0, 2000.0, False))
    r = run_once(reader, state, now=_now_at_bar(reader), max_stale_sec=1e9, audit=trail)
    assert r["action"] == "EXIT"
    events = trail.reconstruct_trade("2000-01-01T00:00:00.000000")
    assert events and events[0].type == "position.tp_trigger"


def test_run_once_use_tsl_persists_ratchet_when_not_triggered(tmp_path, monkeypatch):
    """Runner's responsibility under test: when check_exit doesn't trigger and
    use_tsl=True, the returned ratchet state must be written back via
    update_stop_state so the NEXT (separate) cron process reloads it."""
    reader = PenguinReader(FIX)
    state = AtomState(str(tmp_path / "s.sqlite"))
    state.checkpoint_and_record("SINGLE_SPREAD", "2000-01-01T00:00:00",
                                phase1.PaperOrder("bull_put_spread",
                                    (("BUY", 23750, "PE", 233.75), ("SELL", 23950, "PE", 305.35)),
                                    5370.0, 9630.0, 75, "28-JUL-2026"),
                                "2000-01-01T00:00:00.000000", {"regime": "TREND_UP", "confidence": 0.75})

    monkeypatch.setattr(phase1, "check_exit",
                        lambda position, rdr, bar_ts, levels_state=None: phase1.ExitCheck(
                            False, None, 3200.0, 200.0, bar_ts, 100.0, bar_ts, -1000.0, 2685.0,
                            False, tsl=1500.0, tsl_armed=True, high_water_pnl=3200.0))

    r = run_once(reader, state, now=_now_at_bar(reader), max_stale_sec=1e9, use_tsl=True)
    assert r["action"] not in ("EXIT",)
    pos = state.last_open_position()
    assert pos["tsl"] == 1500.0 and pos["tsl_armed"] is True and pos["high_water_pnl"] == 3200.0


def test_run_once_risk_gate_blocks_over_cap_entry(tmp_path):
    """A tiny capital forces every gate (margin/deployment/at-risk) to reject even
    1 lot — the OPEN must be downgraded to STAND_DOWN and no trade recorded."""
    phase1.configure({**phase1.config.DEFAULTS, "regime.entry.min_confidence": 0.0})
    try:
        reader = PenguinReader(FIX)
        state = AtomState(str(tmp_path / "s.sqlite"))
        r = run_once(reader, state, now=_now_at_bar(reader), max_stale_sec=1e9,
                    risk_gate=True, capital=100.0)
        if r["order"] is not None:
            pytest.fail("risk_gate=True with capital=100 should never let an order through")
        assert r["risk_verdict"] is not None and r["risk_verdict"].verdict == "REJECTED"
        n = state._c().execute("select count(*) from paper_trades").fetchone()[0]
        assert n == 0
    finally:
        phase1.configure(dict(phase1.config.DEFAULTS))


def test_run_once_risk_gate_allows_clean_entry(tmp_path):
    """Ample capital: risk_gate=True must not block a trade that would have opened
    anyway under the legacy path."""
    phase1.configure({**phase1.config.DEFAULTS, "regime.entry.min_confidence": 0.0})
    try:
        reader = PenguinReader(FIX)
        state = AtomState(str(tmp_path / "s.sqlite"))
        r = run_once(reader, state, now=_now_at_bar(reader), max_stale_sec=1e9,
                    risk_gate=True, capital=200000.0)
        assert r["action"] == "OPEN"
        assert r["risk_verdict"].verdict == "APPROVED"
    finally:
        phase1.configure(dict(phase1.config.DEFAULTS))


def test_run_once_exit_takes_priority_over_new_entry_check(tmp_path, monkeypatch):
    """When SINGLE_SPREAD and exit triggers, run_once must close+return EXIT without
    ever calling decide()/classify_regime() for a new entry on the same tick."""
    reader = PenguinReader(FIX)
    state = AtomState(str(tmp_path / "s.sqlite"))
    now = _now_at_bar(reader)
    state.checkpoint_and_record("SINGLE_SPREAD", "2000-01-01T00:00:00",
                                 phase1.PaperOrder("bull_put_spread",
                                                    (("BUY", 23750, "PE", 233.75),
                                                     ("SELL", 23950, "PE", 305.35)),
                                                    5370.0, 9630.0, 75, "28-JUL-2026"),
                                 now.isoformat(), {"regime": "TREND_UP", "confidence": 0.75})

    monkeypatch.setattr(phase1, "check_exit",
                        lambda position, rdr, bar_ts, levels_state=None: phase1.ExitCheck(
                            True, "TP", 5000.0, 200.0, bar_ts, 100.0, bar_ts, -1000.0, 2000.0, False))
    r = run_once(reader, state, now=now, max_stale_sec=1e9)
    assert r["action"] == "EXIT" and r["reason"] == "TP"
    fsm_state, _ = state.load()
    assert fsm_state == "FLAT"


def test_ai_optimizer_is_mock_and_type_safe():
    from atom import ai_optimizer, config
    cfg = config.load_config()
    opt = ai_optimizer.load_optimized(cfg)
    assert "MOCK" in opt["source"]                   # honesty: labelled mock
    for k, v in opt["overrides"].items():
        assert k in cfg and type(v) is type(cfg[k])  # keys real, types preserved
        assert opt["applied"][k] == v                # merged onto base
