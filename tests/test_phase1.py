"""Phase 1 validation — real Penguin pipeline (reader + 7-family regime + FSM entry +
real-premium construction + freshness gate + state)."""
import os
from datetime import datetime

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
    label, conf, _ = phase1.classify_regime(ind)
    assert label == "TREND_UP" and conf > 0


def test_regime_bear_row():
    ind = {"st_consensus": "bearish", "rsi": 20, "ema20_slope": -1.0, "structure_type": "LL",
           "pcr_total": 1.3, "sentiment": "bearish", "vwap": 110, "spot": 100, "adx": 40}
    label, _, _ = phase1.classify_regime(ind)
    assert label == "TREND_DOWN"


def test_regime_low_adx_is_sideways():
    ind = {"st_consensus": "bullish", "rsi": 62, "structure_type": "HH", "adx": 10}
    label, _, _ = phase1.classify_regime(ind)
    assert label == "SIDEWAYS"


# ---- FSM entry decision ----------------------------------------------------

def test_decide_opens_on_trend():
    assert phase1.decide("FLAT", "TREND_UP", 0.7) == ("OPEN", "bull_put_spread")
    assert phase1.decide("FLAT", "TREND_DOWN", 0.7) == ("OPEN", "bear_call_spread")


def test_decide_skips_when_position_open():
    assert phase1.decide("SINGLE_SPREAD", "TREND_UP", 0.9)[0] == "SKIP"


def test_decide_stands_down_low_conf_and_sideways():
    assert phase1.decide("FLAT", "TREND_UP", 0.1)[0] == "STAND_DOWN"
    assert phase1.decide("FLAT", "SIDEWAYS", 0.9)[0] == "STAND_DOWN"


# ---- construction uses REAL premiums (no fabrication) ----------------------

def test_build_order_real_premiums():
    s = PenguinReader(FIX).latest_snapshot()
    o = phase1.build_order("bear_call_spread", s)
    assert o is not None
    assert o.legs[0][0] == "SELL" and o.legs[1][0] == "BUY"
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
