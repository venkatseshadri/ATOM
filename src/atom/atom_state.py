"""ATOM-owned state — separate DB, single writer (never the Penguin DB).

`atom_state`  : the FSM cursor (fsm_state, last_bar_ts) — reloaded each cycle.
`paper_trades`: paper orders placed (real numbers, no broker). Entry fields are written
at OPEN; exit_ts/exit_reason/realized_pnl/exit_legs stay NULL until SL/TP/EOD closes the
position (minimal slice — no TSL/morph yet, that's a real Phase 3 pass).
"""
from __future__ import annotations

import json
import sqlite3

PAPER_TRADES_COLS = [
    "ts", "bar_ts", "structure", "net_credit", "max_loss", "lot", "legs", "regime",
    "confidence", "expiry", "exit_ts", "exit_reason", "realized_pnl", "exit_legs",
]


class AtomState:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        c = self._c()
        c.execute("CREATE TABLE IF NOT EXISTS atom_state "
                  "(id INTEGER PRIMARY KEY CHECK(id=1), fsm_state TEXT, last_bar_ts TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS paper_trades "
                  "(ts TEXT, bar_ts TEXT, structure TEXT, net_credit REAL, max_loss REAL, "
                  "lot INTEGER, legs TEXT, regime TEXT, confidence REAL)")
        self._migrate_paper_trades(c)
        c.execute("CREATE TABLE IF NOT EXISTS lights_shadow "
                  "(bar_ts TEXT, lights TEXT, gap TEXT, permission TEXT, size TEXT, "
                  "trigger INTEGER, family_dir TEXT, family_conf REAL, "
                  "candidate_enter INTEGER, candidate_instrument TEXT, reason TEXT)")
        c.execute("INSERT OR IGNORE INTO atom_state(id,fsm_state,last_bar_ts) "
                  "VALUES(1,'FLAT',NULL)")
        c.commit(); c.close()

    def _migrate_paper_trades(self, c) -> None:
        """Add exit-tracking + Phase 3 ratchet columns to an existing paper_trades table
        (the live DB already had real trades under earlier schemas — ALTER TABLE ADD
        COLUMN preserves those rows, just backfills NULLs)."""
        existing = {row[1] for row in c.execute("PRAGMA table_info(paper_trades)")}
        for col, coltype in [("expiry", "TEXT"), ("exit_ts", "TEXT"),
                              ("exit_reason", "TEXT"), ("realized_pnl", "REAL"),
                              ("exit_legs", "TEXT"), ("index_name", "TEXT"),
                              ("tsl", "REAL"), ("tsl_armed", "INTEGER"),
                              ("high_water_pnl", "REAL")]:
            if col not in existing:
                c.execute(f"ALTER TABLE paper_trades ADD COLUMN {col} {coltype}")

    def _c(self):
        return sqlite3.connect(self.db_path)

    def reset(self) -> None:
        c = self._c()
        try:
            c.execute("UPDATE atom_state SET fsm_state='FLAT', last_bar_ts=NULL WHERE id=1")
            c.execute("DELETE FROM paper_trades")
            c.commit()
        finally:
            c.close()

    def load(self) -> tuple[str, str | None]:
        c = self._c()
        try:
            return c.execute("select fsm_state,last_bar_ts from atom_state where id=1").fetchone()
        finally:
            c.close()

    def checkpoint_and_record(self, fsm_state: str, last_bar_ts: str, order,
                               now: str, decision: dict) -> None:
        """Checkpoint the FSM cursor and (if present) record the paper trade in ONE
        transaction. These used to be two separate connections/commits, which left a
        crash window where fsm_state could flip to SINGLE_SPREAD with zero matching
        paper_trades row — a permanently stuck FSM with no visible position,
        unrecoverable except by manual DB edit."""
        c = self._c()
        try:
            c.execute("BEGIN")
            c.execute("UPDATE atom_state SET fsm_state=?, last_bar_ts=? WHERE id=1",
                      (fsm_state, last_bar_ts))
            if order is not None:
                c.execute(
                    "INSERT INTO paper_trades "
                    "(ts,bar_ts,structure,net_credit,max_loss,lot,legs,regime,confidence,"
                    "expiry,index_name) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (now, last_bar_ts, order.structure, order.net_credit,
                     order.max_loss, order.lot, json.dumps(order.legs),
                     decision["regime"], decision["confidence"], order.expiry,
                     getattr(order, "index", "NIFTY")))
            c.commit()
        finally:
            c.close()

    def record_exit_and_checkpoint(self, position_ts: str, exit_ts: str, reason: str,
                                    realized_pnl: float | None, exit_legs: dict,
                                    last_bar_ts: str) -> None:
        """Close the position (UPDATE its paper_trades row by entry ts) and flip fsm
        back to FLAT in ONE transaction — same atomicity reasoning as
        checkpoint_and_record: a crash between the two must never be possible."""
        c = self._c()
        try:
            c.execute("BEGIN")
            c.execute(
                "UPDATE paper_trades SET exit_ts=?, exit_reason=?, realized_pnl=?, "
                "exit_legs=? WHERE ts=?",
                (exit_ts, reason, realized_pnl, json.dumps(exit_legs), position_ts))
            c.execute("UPDATE atom_state SET fsm_state='FLAT', last_bar_ts=? WHERE id=1",
                      (last_bar_ts,))
            c.commit()
        finally:
            c.close()

    def record_lights_shadow(self, bar_ts: str, res, sh: dict, decision: dict) -> None:
        c = self._c()
        try:
            c.execute("INSERT INTO lights_shadow VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (bar_ts, json.dumps(res.lights), res.gap, res.permission, res.size,
                       int(res.trigger), decision["regime"], decision["confidence"],
                       int(sh["enter"]), sh.get("instrument"), sh["reason"]))
            c.commit()
        finally:
            c.close()

    def last_open_position(self) -> dict | None:
        """Most recent trade with no exit recorded yet — for log display when fsm !=
        FLAT (which trade is blocking new entries) and for exit-checking. Includes
        Phase 3's index + persisted TSL ratchet state (tsl/tsl_armed/high_water_pnl),
        all NULL-safe for rows written before those columns existed."""
        c = self._c()
        try:
            row = c.execute(
                "select ts, bar_ts, structure, net_credit, max_loss, lot, legs, expiry, "
                "index_name, tsl, tsl_armed, high_water_pnl "
                "from paper_trades where exit_ts is null order by ts desc limit 1").fetchone()
            if not row:
                return None
            (ts, bar_ts, structure, net_credit, max_loss, lot, legs, expiry, index_name,
             tsl, tsl_armed, high_water_pnl) = row
            return {"ts": ts, "bar_ts": bar_ts, "structure": structure,
                    "net_credit": net_credit, "max_loss": max_loss, "lot": lot,
                    "legs": json.loads(legs), "expiry": expiry,
                    "index": index_name or "NIFTY",
                    "tsl": tsl, "tsl_armed": bool(tsl_armed), "high_water_pnl": high_water_pnl}
        finally:
            c.close()

    def update_stop_state(self, position_ts: str, tsl: float | None, tsl_armed: bool,
                          high_water_pnl: float | None) -> None:
        """Persist Module 6's ratchet state after a cycle that didn't trigger an exit —
        so the NEXT cron invocation (a fresh process) reloads the tight stop rather than
        recomputing from scratch, which could loosen it (6.3.3/6.7.1)."""
        c = self._c()
        try:
            c.execute("UPDATE paper_trades SET tsl=?, tsl_armed=?, high_water_pnl=? "
                     "WHERE ts=?", (tsl, int(tsl_armed), high_water_pnl, position_ts))
            c.commit()
        finally:
            c.close()

    def derive_account(self, capital: float, today: str) -> dict:
        """Module 5's account facts, derived from real paper_trades — not tracked as a
        separate running balance (ATOM has no margin/deployment simulation of its OWN
        positions, so `deployed` is honestly 0; DD floor uses today's capital as
        `peak_equity` since no multi-day equity-curve history is persisted — a known
        limitation, see PHASE-3-TECHNICAL.md).

        Also folds in the REAL broker account margin (antariksh's `broker_limits.json`,
        refreshed daily 08:30 by a live broker-API call) as `broker_margin_available` /
        `broker_free_margin` — this is the actual shared account's headroom (covers
        every system on the box, not just ATOM), used as an independent sanity gate in
        risk.py, not as a substitute for ATOM's own strategy-scoped `deployed`/`capital`."""
        from . import connectivity
        bm = connectivity.read_broker_margin()
        c = self._c()
        try:
            realized_today = c.execute(
                "select coalesce(sum(realized_pnl),0) from paper_trades "
                "where exit_ts is not null and substr(exit_ts,1,10)=?", (today,)).fetchone()[0]
            trades_today = c.execute(
                "select count(*) from paper_trades where substr(ts,1,10)=?", (today,)).fetchone()[0]
            open_row = c.execute(
                "select structure, max_loss from paper_trades where exit_ts is null "
                "order by ts desc limit 1").fetchone()
            open_count = 1 if open_row else 0
            reserved_risk_open = open_row[1] if open_row else 0.0
            # today's entry count per family SO FAR (before any new candidate) — the
            # lookup risk.evaluate() needs keyed by whatever family_id the new plan uses
            reentries = dict(c.execute(
                "select structure, count(*) from paper_trades where substr(ts,1,10)=? "
                "group by structure", (today,)).fetchall())
            equity = capital + realized_today
            return {"capital": capital, "deployed": 0.0, "realized_pnl_today": realized_today,
                    "reserved_risk_open": reserved_risk_open, "open_count": open_count,
                    "trades_today": trades_today, "peak_equity": capital,
                    "current_equity": equity, "reentries_today_by_family": reentries,
                    "duplicate_suspected": False,
                    "broker_margin_available": bm.available,
                    "broker_free_margin": bm.free_margin,
                    "broker_margin_age_days": bm.age_days,
                    "broker_margin_reason": bm.reason}
        finally:
            c.close()
