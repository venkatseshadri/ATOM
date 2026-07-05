"""Recompute st_consensus / structure_type / vwap fresh from raw historical data,
using TODAY's fixed logic instead of trusting the stored market_data_enriched columns
(which reflect whatever logic was live when each row was written — see the 2026-07-02
SuperTrend/structure/VWAP fixes). This is what makes historical replay trustworthy
across the retained window instead of silently mixing pre-fix and post-fix signals.

Standalone reimplementation (not importing antariksh) — same anti-coupling reasoning
as docs/PORCUPINE.md: ATOM stays a public repo, doesn't reach into the private pipeline.

Data-availability limits, checked against real retention (not assumed):
  st_consensus:   full window (raw st_5min/15min_direction sub-components were never
                  buggy — only the combining rule was — and are stored back to 05-29).
  structure_type: full window (needs raw 1-min OHLC, stored back to 05-29).
  vwap:           only from 2026-06-12 onward (futures volume db starts there — no
                  workaround, the data simply doesn't exist before that).
"""
from __future__ import annotations

import sqlite3

VWAP_AVAILABLE_FROM = "2026-06-12"
FUT_DB = "/home/trading_ceo/python-trader/varaha/data/capture_nifty-fut.sqlite"


def fixed_st_consensus(st_5min: str | None, st_15min: str | None) -> str | None:
    """Same rule as antariksh/enrichers/lib/supertrend.py post-fix: 15m wins outright,
    5m only as fallback when 15m has no data."""
    if st_15min is not None:
        return st_15min
    if st_5min is not None:
        return st_5min
    return None


def fixed_structure_type(cur_high: float, cur_low: float,
                          prev_high: float, prev_low: float) -> str:
    """Same rule as antariksh/enrichers/lib/smc.py post-fix: genuine MIXED for
    inside/outside bars instead of forcing HH/LL off the high comparison alone."""
    if cur_high > prev_high and cur_low > prev_low:
        return "HH"
    if cur_high < prev_high and cur_low < prev_low:
        return "LL"
    return "MIXED"


def day_vwap_series(instrument: str, date: str, spot_db: str) -> dict[str, float]:
    """Session-cumulative VWAP for one trading day, using real futures volume — the
    exact live formula (vwap = cum_vp/cum_vol, reset at session start), replayed
    offline. Returns {} for dates before VWAP_AVAILABLE_FROM (no futures volume data
    exists then — not computed as zero/fabricated, simply absent)."""
    if date < VWAP_AVAILABLE_FROM:
        return {}

    spot_conn = sqlite3.connect(f"file:{spot_db}?mode=ro", uri=True)
    fut_conn = sqlite3.connect(f"file:{FUT_DB}?mode=ro", uri=True)
    try:
        spot_rows = spot_conn.execute(
            "select timestamp, close from market_data where instrument = ? "
            "and timestamp like ? order by timestamp asc",
            (instrument, f"{date}%")).fetchall()
        fut_rows = dict(fut_conn.execute(
            "select timestamp, volume from market_data where instrument = ? "
            "and timestamp like ? order by timestamp asc",
            (f"{instrument}-FUT", f"{date}%")).fetchall())
    except sqlite3.OperationalError:
        # spot_db missing market_data (e.g. test fixtures only carry
        # market_data_enriched) or the futures db is unreachable — degrade to "no
        # VWAP available" rather than crash the whole replay.
        return {}
    finally:
        spot_conn.close()
        fut_conn.close()

    cum_vol, cum_vp = 0.0, 0.0
    out = {}
    for ts, close in spot_rows:
        vol = fut_rows.get(ts) or 0.0
        cum_vol += vol
        cum_vp += (close or 0.0) * vol
        out[ts] = round(cum_vp / cum_vol, 2) if cum_vol > 0 else None
    return out


def corrected_indicators(ind: dict, day_vwap: dict[str, float] | None) -> dict:
    """Patch one snapshot's indicator dict with recomputed st_consensus/structure_type/
    vwap. Reuses struct_cur/struct_prev and st_5min/15min_direction already present in
    the snapshot (from penguin.py's historical_snapshots()) — no extra queries needed
    for the first two; day_vwap is the precomputed per-day series from day_vwap_series()
    (None if that date predates VWAP_AVAILABLE_FROM)."""
    out = dict(ind)
    out["st_consensus"] = fixed_st_consensus(ind.get("st_5min_direction"),
                                              ind.get("st_15min_direction"))
    cur, prev = ind.get("struct_cur"), ind.get("struct_prev")
    if cur and prev:
        (_, c_h, c_l), (_, p_h, p_l) = cur, prev
        if None not in (c_h, c_l, p_h, p_l):
            out["structure_type"] = fixed_structure_type(c_h, c_l, p_h, p_l)
    out["vwap"] = day_vwap.get(ind["timestamp"]) if day_vwap else None
    return out
