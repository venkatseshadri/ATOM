"""ATOM-owned state — separate DB, single writer (never the Penguin DB).

`atom_state`  : the FSM cursor (fsm_state, last_bar_ts) — reloaded each cycle.
`paper_trades`: paper orders placed (real numbers, no broker). The real order ledger is
a separate Phase-3 discussion.
"""
from __future__ import annotations

import json
import sqlite3


class AtomState:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        c = self._c()
        c.execute("CREATE TABLE IF NOT EXISTS atom_state "
                  "(id INTEGER PRIMARY KEY CHECK(id=1), fsm_state TEXT, last_bar_ts TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS paper_trades "
                  "(ts TEXT, bar_ts TEXT, structure TEXT, net_credit REAL, max_loss REAL, "
                  "lot INTEGER, legs TEXT, regime TEXT, confidence REAL)")
        c.execute("CREATE TABLE IF NOT EXISTS lights_shadow "
                  "(bar_ts TEXT, lights TEXT, gap TEXT, permission TEXT, size TEXT, "
                  "trigger INTEGER, family_dir TEXT, family_conf REAL, "
                  "candidate_enter INTEGER, candidate_instrument TEXT, reason TEXT)")
        c.execute("INSERT OR IGNORE INTO atom_state(id,fsm_state,last_bar_ts) "
                  "VALUES(1,'FLAT',NULL)")
        c.commit(); c.close()

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

    def checkpoint(self, fsm_state: str, last_bar_ts: str) -> None:
        c = self._c()
        try:
            c.execute("BEGIN")
            c.execute("UPDATE atom_state SET fsm_state=?, last_bar_ts=? WHERE id=1",
                      (fsm_state, last_bar_ts))
            c.commit()                      # atomic
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
        """Most recent paper trade — for log display when fsm != FLAT (which trade is
        blocking new entries). Not a claim it's still open; Phase 1 has no exit, so the
        latest row IS the open position whenever fsm_state is SINGLE_SPREAD."""
        c = self._c()
        try:
            row = c.execute(
                "select ts, bar_ts, structure, net_credit, max_loss, lot, legs "
                "from paper_trades order by ts desc limit 1").fetchone()
            if not row:
                return None
            ts, bar_ts, structure, net_credit, max_loss, lot, legs = row
            return {"ts": ts, "bar_ts": bar_ts, "structure": structure,
                    "net_credit": net_credit, "max_loss": max_loss, "lot": lot,
                    "legs": json.loads(legs)}
        finally:
            c.close()

    def record_paper_trade(self, now: str, bar_ts: str, order, decision: dict) -> None:
        c = self._c()
        try:
            c.execute("INSERT INTO paper_trades VALUES(?,?,?,?,?,?,?,?,?)",
                      (now, bar_ts, order.structure, order.net_credit, order.max_loss,
                       order.lot, json.dumps(order.legs), decision["regime"],
                       decision["confidence"]))
            c.commit()
        finally:
            c.close()
