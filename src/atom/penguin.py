"""Penguin reader — ATOM's read-only window into the live capture DB.

Reads `capture_{index}.sqlite` (Penguin, single-writer, WAL) strictly read-only. ATOM
never writes here. Consumes the already-computed `market_data_enriched` (don't duplicate
Penguin) + real `option_prices` (per-strike LTP/OI). See SEAM_RECONCILIATION / the
Phase-1 design.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

# enriched columns ATOM consumes (regime + instrument)
ENRICHED_COLS = [
    "timestamp", "instrument", "spot", "atm_strike", "expiry_weekly", "days_to_weekly",
    "supertrend_direction", "st_consensus", "st_5min_direction", "st_15min_direction",
    "adx", "rsi", "ema20_slope", "india_vix",
    "iv_rank", "bb_width", "vwap", "structure_type", "pcr_total", "pcr_atm", "oi_skew",
    "sentiment", "gap_pct", "session_phase",
]

_MONTHS = {1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
           7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC"}


def _f(v):
    """Safe float (fixture stores TEXT; live stores native); '', None -> None."""
    if v in (None, "", "None"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _expiry_to_tsym(expiry: str) -> str:
    """'30-JUN-2026' -> '30JUN26' (the tsym expiry token)."""
    m = re.match(r"(\d{2})-([A-Z]{3})-(\d{4})", expiry or "")
    if not m:
        return ""
    return f"{m.group(1)}{m.group(2)}{m.group(3)[2:]}"


@dataclass(frozen=True)
class Snapshot:
    ts: str
    spot: float
    atm_strike: int
    expiry: str
    days_to_expiry: int
    ind: dict          # enriched indicators (raw, consumed by regime)
    chain: dict        # {(strike:int, right:'CE'/'PE'): {'ltp':float,'oi':float}}


class PenguinReader:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        # read-only; never writes the live capture DB
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)

    def _build(self, c, ind: dict, strike_window: int) -> Snapshot:
        atm = int(_f(ind["atm_strike"]) or 0)
        expiry = ind["expiry_weekly"] or ""
        step = 50  # NIFTY
        strikes = [atm + step * k for k in range(-strike_window, strike_window + 1)]
        chain = self._option_chain(c, expiry, strikes)
        ind = dict(ind)
        ind.update(self._structure_bars(c, ind["instrument"], ind["timestamp"]))
        return Snapshot(ts=ind["timestamp"], spot=_f(ind["spot"]) or 0.0, atm_strike=atm,
                        expiry=expiry, days_to_expiry=int(_f(ind["days_to_weekly"]) or 0),
                        ind=ind, chain=chain)

    def _structure_bars(self, c, instrument: str, ts: str) -> dict:
        """Raw 1-min high/low for the bar and the one before it — the exact two
        candles structure_type's HH/LL call is based on. For log transparency only,
        does not feed the vote (structure_type already carries the decision)."""
        try:
            rows = c.execute(
                "select timestamp, high, low from market_data where instrument = ? "
                "and timestamp <= ? order by timestamp desc limit 2", (instrument, ts)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if len(rows) < 2:
            return {"struct_cur": None, "struct_prev": None}
        (cur_ts, cur_h, cur_l), (prev_ts, prev_h, prev_l) = rows
        return {"struct_cur": (cur_ts, _f(cur_h), _f(cur_l)),
                "struct_prev": (prev_ts, _f(prev_h), _f(prev_l))}

    def latest_snapshot(self, strike_window: int = 6) -> Snapshot | None:
        c = self._connect()
        try:
            row = c.execute(
                f"select {','.join(ENRICHED_COLS)} from market_data_enriched "
                f"order by timestamp desc limit 1").fetchone()
            return self._build(c, dict(zip(ENRICHED_COLS, row)), strike_window) if row else None
        finally:
            c.close()

    def recent_snapshots(self, limit: int = 60, strike_window: int = 6) -> list[Snapshot]:
        """Newest-first snapshots for scanning (each uses the live option chain)."""
        c = self._connect()
        try:
            rows = c.execute(
                f"select {','.join(ENRICHED_COLS)} from market_data_enriched "
                f"order by timestamp desc limit ?", (limit,)).fetchall()
            return [self._build(c, dict(zip(ENRICHED_COLS, r)), strike_window) for r in rows]
        finally:
            c.close()

    def _option_chain(self, c, expiry: str, strikes: list[int]) -> dict:
        tok = _expiry_to_tsym(expiry)
        if not tok:
            return {}
        tmax = c.execute(
            "select max(timestamp) from option_prices where tsym like ?",
            (f"NIFTY{tok}%",)).fetchone()[0]
        if not tmax:
            return {}
        out = {}
        q = ("select strike, option_type, ltp, oi from option_prices "
             "where timestamp=? and tsym like ?")
        for strike, otype, ltp, oi in c.execute(q, (tmax, f"NIFTY{tok}%")):
            s = int(_f(strike) or 0)
            if s in strikes:
                out[(s, otype)] = {"ltp": _f(ltp), "oi": _f(oi)}
        return out

    def latest_price_for(self, expiry: str, strike: int, option_type: str) -> tuple | None:
        """Independent per-leg latest price — for exit-checking an EXISTING position,
        not for a fresh chain snapshot. _option_chain() filters to one shared max
        timestamp across all strikes, which silently drops a leg whose last tick is
        older than another leg's (confirmed live: a deep-OTM hedge leg can go a full
        day+ without a fresh tick while the near-ATM short leg updates every minute).
        Returns (ltp, timestamp) or None if this strike/expiry has no price at all."""
        tok = _expiry_to_tsym(expiry)
        if not tok:
            return None
        c = self._connect()
        try:
            row = c.execute(
                "select ltp, timestamp from option_prices where strike=? and option_type=? "
                "and tsym like ? order by timestamp desc limit 1",
                (strike, option_type, f"NIFTY{tok}%")).fetchone()
            return (_f(row[0]), row[1]) if row and row[0] is not None else None
        finally:
            c.close()

    def multitf(self, tf: int, limit: int = 3) -> list[dict]:
        """Newest-first candles for a timeframe (minutes) from market_data_multitf."""
        c = self._connect()
        try:
            rows = c.execute(
                "select timestamp,open,high,low,close from market_data_multitf "
                "where timeframe_min=? and close is not null order by timestamp desc limit ?",
                (tf, limit)).fetchall()
            return [{"ts": r[0], "open": _f(r[1]), "high": _f(r[2]),
                     "low": _f(r[3]), "close": _f(r[4])} for r in rows]
        finally:
            c.close()

    def multitf_latest_ts(self) -> str | None:
        c = self._connect()
        try:
            return c.execute("select max(timestamp) from market_data_multitf").fetchone()[0]
        finally:
            c.close()

    def option_ts(self) -> str | None:
        c = self._connect()
        try:
            return c.execute("select max(timestamp) from option_prices").fetchone()[0]
        finally:
            c.close()
