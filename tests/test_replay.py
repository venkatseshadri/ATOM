"""replay.py — real-pipeline backtest driver, end-to-end against the test fixture."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))   # repo root, for replay.py

from replay import replay   # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "capture_nifty_fixture.sqlite")


def test_replay_runs_without_crashing_on_fixture():
    """The fixture lacks market_data/futures data (VWAP recompute) and only spans
    ~1 hour — this isn't testing rich output, it's testing the pipeline doesn't crash
    on a DB missing tables the live one has, and never touches live state (replay()
    takes no AtomState/live db at all, so that's true by construction)."""
    trades = replay("2026-06-30T00:00:00", "2026-06-30T23:59:59", db_path=FIX)
    assert isinstance(trades, list)


def test_replay_trade_shape_when_a_trade_occurs():
    trades = replay("2026-06-30T00:00:00", "2026-06-30T23:59:59", db_path=FIX)
    for t in trades:
        assert "entry_regime" in t and "entry_votes" in t and "entry_indicators" in t
        assert t["net_credit"] > 0 and t["max_loss"] > 0
        # every trade must resolve one way or another within the replayed window,
        # or still be open — never left in a broken partial state
        assert (t["exit_ts"] is None) == (t["realized_pnl"] is None)


def test_replay_empty_window_returns_no_trades():
    trades = replay("2020-01-01T00:00:00", "2020-01-01T23:59:59", db_path=FIX)
    assert trades == []
