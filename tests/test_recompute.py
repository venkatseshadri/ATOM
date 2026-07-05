"""recompute.py — fresh st_consensus/structure_type/vwap for historical replay."""
import os

from atom import recompute

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "capture_nifty_fixture.sqlite")


def test_fixed_st_consensus_15m_wins_on_disagreement():
    assert recompute.fixed_st_consensus("bullish", "bearish") == "bearish"
    assert recompute.fixed_st_consensus("bearish", "bullish") == "bullish"


def test_fixed_st_consensus_falls_back_to_5m_when_15m_missing():
    assert recompute.fixed_st_consensus("bearish", None) == "bearish"


def test_fixed_st_consensus_none_when_both_missing():
    assert recompute.fixed_st_consensus(None, None) is None


def test_fixed_structure_type_clean_hh_ll():
    assert recompute.fixed_structure_type(110, 96, 105, 90) == "HH"
    assert recompute.fixed_structure_type(100, 85, 105, 90) == "LL"


def test_fixed_structure_type_ambiguous_bar_is_mixed():
    # inside bar: high lower AND low higher than prior — neither clean HH nor LL
    assert recompute.fixed_structure_type(100, 95, 105, 90) == "MIXED"
    # outside bar: took out both prior extremes
    assert recompute.fixed_structure_type(110, 85, 105, 90) == "MIXED"


def test_day_vwap_series_empty_before_availability_date():
    # 2026-06-01 predates VWAP_AVAILABLE_FROM (2026-06-12) — no futures volume data
    # exists that far back, must return {} rather than fabricate zeros.
    assert recompute.day_vwap_series("NIFTY", "2026-06-01", FIX) == {}


def test_day_vwap_series_degrades_gracefully_on_missing_market_data_table():
    # The fixture only carries market_data_enriched, not raw market_data — must not
    # crash just because a date is past the availability threshold.
    assert recompute.day_vwap_series("NIFTY", "2026-06-30", FIX) == {}


def test_corrected_indicators_overrides_st_consensus_and_structure():
    ind = {
        "timestamp": "2026-07-01T10:00:00",
        "st_5min_direction": "bullish", "st_15min_direction": "bearish",
        "struct_cur": ("2026-07-01T10:00:00", 100, 95),
        "struct_prev": ("2026-07-01T09:59:00", 105, 90),
    }
    out = recompute.corrected_indicators(ind, day_vwap=None)
    assert out["st_consensus"] == "bearish"        # 15m wins
    assert out["structure_type"] == "MIXED"        # inside bar
    assert out["vwap"] is None                     # no day_vwap supplied


def test_corrected_indicators_uses_day_vwap_when_available():
    ind = {"timestamp": "2026-07-01T10:00:00", "st_5min_direction": None,
           "st_15min_direction": None, "struct_cur": None, "struct_prev": None}
    out = recompute.corrected_indicators(ind, day_vwap={"2026-07-01T10:00:00": 24000.5})
    assert out["vwap"] == 24000.5


def test_corrected_indicators_leaves_structure_unset_when_struct_bars_missing():
    """Fixtures without a market_data table can't supply struct_cur/struct_prev —
    must not crash, just leave structure_type as whatever was already there."""
    ind = {"timestamp": "2026-07-01T10:00:00", "st_5min_direction": None,
           "st_15min_direction": None, "struct_cur": None, "struct_prev": None,
           "structure_type": "HH"}
    out = recompute.corrected_indicators(ind, day_vwap=None)
    assert out["structure_type"] == "HH"           # unchanged, not crashed
