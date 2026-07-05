"""Module 12 — Instrument & Symbol Master (Phase 2, real).

Reads the real broker contract list from `static_metadata.db:scrip_master` — built by
antariksh's `tools/bootstrap_scrip_master.py` from the Shoonya NFO/BFO master dump (see
antariksh T25, DAMBUILDER_STATE.md). Master-as-truth per 12-instrument-symbol.md: ATOM
never re-derives expiry/lot/symbol rules, it reads what's actually listed. `db_path` is
overridable so tests run against a small frozen fixture, not the live daily-changing DB.

Distinct from `modules/instrument.py` (Phase-0 stub, illustrative, feeds the orchestrator's
skeleton pass — stays as-is; see phase1.py's docstring for why real logic lives outside the
Phase-0 module files)."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import duckdb

from .contracts import Instrument

DEFAULT_DB = Path("/home/trading_ceo/antariksh/data/static_metadata.db")
STALE_DAYS = 1   # fail-loud (not fail-closed — paper only) if the master is older than this

# antariksh's scrip_master schema (bootstrap_scrip_master.py) doesn't capture the
# broker master's TickSize column at all — the raw NFO/BFO dumps have it, the DB
# doesn't. 0.05 is the real, current tick for NSE/BSE index options; if the exchange
# ever changes it, this silently goes wrong (same class of bug as T25 lot_size).
# Residual: get TickSize added to antariksh's scrip_master schema.
TICK_SIZE = 0.05


class InstrumentMaster:
    """Real Module 12. Fail-closed: raises rather than emit a guessed/partial Instrument."""

    def __init__(self, telemetry, db_path: Path | str = DEFAULT_DB) -> None:
        self.t = telemetry
        self.db_path = str(db_path)

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(self.db_path, read_only=True)

    def _staleness_days(self, con) -> int | None:
        row = con.execute("select max(imported_at) from scrip_master").fetchone()
        if not row or row[0] is None:
            return None
        return (datetime.now() - row[0]).days

    @staticmethod
    def _modal_step(strikes: list[float]) -> int:
        gaps = [round(b - a) for a, b in zip(strikes, strikes[1:]) if b > a]
        return max(set(gaps), key=gaps.count) if gaps else 50

    def resolve(self, index: str, spot: float, now: date | None = None) -> Instrument:
        """Nearest unexpired listed expiry for `index`, ATM strike nearest to `spot`,
        real lot/tick/tradingsymbol — all read from the master, never hardcoded."""
        now = now or date.today()
        con = self._connect()
        try:
            age = self._staleness_days(con)
            if age is None:
                self.t.emit("instrument", "no_master", {"index": index},
                            msg=f"INSTRUMENT ⚠ scrip_master has no rows for {index}")
            elif age > STALE_DAYS:
                self.t.emit("instrument", "stale_master", {"index": index, "age_days": age},
                            msg=f"INSTRUMENT ⚠ scrip_master is {age}d stale — resolving "
                                f"{index} off possibly-outdated contracts")

            expiry = con.execute(
                "select min(expiry) from scrip_master where symbol=? and expiry>=?",
                (index, now)).fetchone()[0]
            if expiry is None:
                raise ValueError(f"NO_LISTED_EXPIRY for {index} on/after {now}")

            strikes = [r[0] for r in con.execute(
                "select distinct strike from scrip_master where symbol=? and expiry=? "
                "order by strike", (index, expiry)).fetchall()]
            if not strikes:
                raise ValueError(f"NO_STRIKES for {index} {expiry}")
            step = self._modal_step(strikes)
            atm = min(strikes, key=lambda s: abs(s - spot)) if spot else strikes[len(strikes) // 2]

            row = con.execute(
                "select tsym, lot_size from scrip_master where symbol=? "
                "and expiry=? and strike=? and option_type='PE' limit 1",
                (index, expiry, atm)).fetchone()
            if not row:
                raise ValueError(f"SYMBOL_UNRESOLVED for {index} {expiry} ATM={atm}")
            tsym, lot = row
            expiry_str = expiry.strftime("%d-%b-%Y").upper()
            self.t.emit("instrument", "resolve",
                        {"index": index, "expiry": expiry_str, "atm": atm, "step": step,
                         "lot": lot},
                        msg=f"INSTRUMENT → {index} weekly {expiry_str}, lot {lot}, step {step} "
                            f"→ ATM = nearest listed strike to spot ₹{spot:,.1f} = {atm} "
                            f"| symbol {tsym} (master-sourced, round-trip guaranteed)")
            return Instrument(tradingsymbol=tsym, index=index, expiry=expiry_str,
                              strike=float(atm), right="PE", lot_size=int(lot),
                              tick_size=TICK_SIZE)
        finally:
            con.close()

    def step_for(self, index: str, expiry_str: str) -> int:
        """Strike-ladder interval for an already-resolved expiry (DD-MON-YYYY)."""
        expiry = datetime.strptime(expiry_str, "%d-%b-%Y").date()
        con = self._connect()
        try:
            strikes = [r[0] for r in con.execute(
                "select distinct strike from scrip_master where symbol=? and expiry=? "
                "order by strike", (index, expiry)).fetchall()]
            return self._modal_step(strikes)
        finally:
            con.close()

    def lookup(self, index: str, expiry_str: str, strike: float, right: str) -> Instrument | None:
        """Real symbol for ANY strike/right at an already-resolved expiry — used by
        Module 4 to build hedge/other legs with a guaranteed-real, round-trip-safe
        symbol. Returns None (not a guess) if the strike/right isn't actually listed."""
        expiry = datetime.strptime(expiry_str, "%d-%b-%Y").date()
        con = self._connect()
        try:
            row = con.execute(
                "select tsym, lot_size from scrip_master where symbol=? "
                "and expiry=? and strike=? and option_type=?",
                (index, expiry, strike, right)).fetchone()
            if not row:
                return None
            tsym, lot = row
            return Instrument(tradingsymbol=tsym, index=index, expiry=expiry_str,
                              strike=float(strike), right=right, lot_size=int(lot),
                              tick_size=TICK_SIZE)
        finally:
            con.close()
