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
    "supertrend_direction", "st_consensus", "adx", "rsi", "ema20_slope", "india_vix",
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

    def latest_snapshot(self, strike_window: int = 6) -> Snapshot | None:
        c = self._connect()
        try:
            row = c.execute(
                f"select {','.join(ENRICHED_COLS)} from market_data_enriched "
                f"order by timestamp desc limit 1").fetchone()
            if not row:
                return None
            ind = dict(zip(ENRICHED_COLS, row))
            atm = int(_f(ind["atm_strike"]) or 0)
            expiry = ind["expiry_weekly"] or ""
            step = 50  # NIFTY
            strikes = [atm + step * k for k in range(-strike_window, strike_window + 1)]
            chain = self._option_chain(c, expiry, strikes)
            return Snapshot(
                ts=ind["timestamp"], spot=_f(ind["spot"]) or 0.0, atm_strike=atm,
                expiry=expiry, days_to_expiry=int(_f(ind["days_to_weekly"]) or 0),
                ind=ind, chain=chain)
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

    def option_ts(self) -> str | None:
        c = self._connect()
        try:
            return c.execute("select max(timestamp) from option_prices").fetchone()[0]
        finally:
            c.close()
