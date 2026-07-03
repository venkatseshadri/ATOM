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
        """Add exit-tracking columns to an existing paper_trades table (the live DB
        already had one real open trade under the old 9-column schema before SL/TP/EOD
        existed — ALTER TABLE ADD COLUMN preserves that row, just backfills NULLs)."""
        existing = {row[1] for row in c.execute("PRAGMA table_info(paper_trades)")}
        for col, coltype in [("expiry", "TEXT"), ("exit_ts", "TEXT"),
                              ("exit_reason", "TEXT"), ("realized_pnl", "REAL"),
                              ("exit_legs", "TEXT")]:
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
                    "(ts,bar_ts,structure,net_credit,max_loss,lot,legs,regime,confidence,expiry) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (now, last_bar_ts, order.structure, order.net_credit,
                     order.max_loss, order.lot, json.dumps(order.legs),
                     decision["regime"], decision["confidence"], order.expiry))
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
        FLAT (which trade is blocking new entries) and for exit-checking."""
        c = self._c()
        try:
            row = c.execute(
                "select ts, bar_ts, structure, net_credit, max_loss, lot, legs, expiry "
                "from paper_trades where exit_ts is null order by ts desc limit 1").fetchone()
            if not row:
                return None
            ts, bar_ts, structure, net_credit, max_loss, lot, legs, expiry = row
            return {"ts": ts, "bar_ts": bar_ts, "structure": structure,
                    "net_credit": net_credit, "max_loss": max_loss, "lot": lot,
                    "legs": json.loads(legs), "expiry": expiry}
        finally:
            c.close()
