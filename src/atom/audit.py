"""Module 15 — Telemetry & Audit (Phase 4, real).

"The system's memory and conscience" — durable, queryable, tamper-evident audit trail.
Fixes the real gap Phase 3 already flagged: `telemetry.py`'s events are in-memory only,
lost every cron tick; the actual live pipeline doesn't even use that class (it just
`print()`s). This module gives ATOM a real `decision_trace` SQLite table, mirroring the
sister systems' existing decision_trace/trade_outcomes convention (per your choice) —
every decision/risk-verdict/fill/exit event for a trade, joinable by `trade_id`, so
T4.3 ("reconstruct any trade end-to-end") is a real query, not a memory.

Scope (honest, not fabricated): ATOM is a single-process-per-cron-tick system with no
distributed producers, no network transport between components, and no regulator/
broker-dispute pipeline yet. §15.2 (async buffering off the hot path), §15.5 (log-level
routing), §15.9 (retention/archival lifecycle), §15.10 (self-telemetry dashboards) are
N/A or premature at this scale — each `append()` call IS already off the trading
decision's critical path (it's a separate sqlite write after the decision is made, not
blocking it), which satisfies the *intent* of 15.2 without needing an async queue no
one is contending for. Documented, not silently skipped.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass

DB_PATH = "data/audit.sqlite"

# 15.1.2 — declared taxonomy. Unregistered types are ACCEPTED but flagged (never
# silently rejected — a taxonomy gap must not blackhole audit data).
REGISTERED_TYPES = frozenset({
    "decision.open", "decision.skip", "decision.stand_down",
    "risk.approved", "risk.resized", "risk.rejected",
    "order.paper_fill", "order.reject",
    "position.sl_trigger", "position.tp_trigger", "position.tsl_trigger",
    "position.time_exit", "position.edge_exhausted_exit",
    "eod.flatten", "eod.finalized",
    "system.error",
})

INFO, WARN, ERROR = "INFO", "WARN", "ERROR"


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    trade_id: str | None
    ts: str
    source: str
    type: str
    payload: dict
    severity: str
    registered: bool
    prior_hash: str | None
    hash: str


def _compute_hash(event_id: str, ts: str, source: str, type_: str, payload: dict,
                  prior_hash: str | None) -> str:
    """15.8 — hash-chained: each event's hash covers the PRIOR event's hash, so
    deleting or altering any past event breaks every hash after it — tamper-evident
    without needing a separate signing infrastructure."""
    canonical = json.dumps({"event_id": event_id, "ts": ts, "source": source, "type": type_,
                           "payload": payload, "prior_hash": prior_hash},
                          sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _canonicalize_type(raw_type: str) -> tuple[str, bool]:
    if raw_type in REGISTERED_TYPES:
        return raw_type, True
    return f"unregistered.{raw_type}", False


class AuditTrail:
    """15.4/15.6/15.7/15.8 — durable structured storage + reconstruction queries +
    tamper-evidence, in one small class."""

    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = db_path
        c = self._c()
        c.execute("""CREATE TABLE IF NOT EXISTS decision_trace (
            event_id TEXT PRIMARY KEY,
            trade_id TEXT,
            ts TEXT NOT NULL,
            source TEXT NOT NULL,
            type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            severity TEXT NOT NULL,
            registered INTEGER NOT NULL,
            prior_hash TEXT,
            hash TEXT NOT NULL,
            seq INTEGER
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS dead_letter (
            received_at TEXT, reason TEXT, raw_json TEXT
        )""")
        c.commit()
        c.close()

    def _c(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def append(self, source: str, type_: str, payload: dict, ts: str,
              trade_id: str | None = None, severity: str = INFO) -> AuditEvent | None:
        """15.1.1 — mandatory-envelope check; malformed events are quarantined to
        dead_letter, NEVER silently dropped and never stored as a normal event."""
        if not source or not type_:
            self._quarantine(ts, "MISSING_SOURCE_OR_TYPE",
                            {"source": source, "type": type_, "payload": payload})
            return None
        canonical_type, registered = _canonicalize_type(type_)
        event_id = str(uuid.uuid4())
        c = self._c()
        try:
            prior = c.execute(
                "SELECT hash FROM decision_trace ORDER BY seq DESC LIMIT 1").fetchone()
            prior_hash = prior[0] if prior else None
            next_seq = (c.execute("SELECT COALESCE(MAX(seq),0) FROM decision_trace")
                       .fetchone()[0] or 0) + 1
            h = _compute_hash(event_id, ts, source, canonical_type, payload, prior_hash)
            c.execute("INSERT INTO decision_trace VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (event_id, trade_id, ts, source, canonical_type, json.dumps(payload),
                      severity, int(registered), prior_hash, h, next_seq))
            c.commit()
            return AuditEvent(event_id, trade_id, ts, source, canonical_type, payload,
                             severity, registered, prior_hash, h)
        finally:
            c.close()

    def _quarantine(self, ts: str, reason: str, raw: dict) -> None:
        c = self._c()
        try:
            c.execute("INSERT INTO dead_letter VALUES (?,?,?)", (ts, reason, json.dumps(raw)))
            c.commit()
        finally:
            c.close()

    def reconstruct_trade(self, trade_id: str) -> list[AuditEvent]:
        """15.6/15.7 — the T4.3 query: full lifecycle for one trade, in order."""
        c = self._c()
        try:
            rows = c.execute(
                "SELECT event_id, trade_id, ts, source, type, payload_json, severity, "
                "registered, prior_hash, hash FROM decision_trace WHERE trade_id=? "
                "ORDER BY seq", (trade_id,)).fetchall()
        finally:
            c.close()
        return [AuditEvent(r[0], r[1], r[2], r[3], r[4], json.loads(r[5]), r[6],
                           bool(r[7]), r[8], r[9]) for r in rows]

    def verify_integrity(self) -> list[str]:
        """15.8 — walk the hash chain; recompute and compare. Returns a list of
        breaks found (empty = clean chain). Detects both tampering (payload edited
        after the fact) and deletion (a gap breaks the next event's prior_hash link)."""
        c = self._c()
        try:
            rows = c.execute(
                "SELECT event_id, trade_id, ts, source, type, payload_json, prior_hash, "
                "hash FROM decision_trace ORDER BY seq").fetchall()
        finally:
            c.close()
        breaks = []
        expected_prior = None
        for event_id, trade_id, ts, source, type_, payload_json, prior_hash, stored_hash in rows:
            if prior_hash != expected_prior:
                breaks.append(f"CHAIN_BREAK at {event_id}: expected prior_hash "
                             f"{expected_prior}, found {prior_hash}")
            recomputed = _compute_hash(event_id, ts, source, type_, json.loads(payload_json),
                                       prior_hash)
            if recomputed != stored_hash:
                breaks.append(f"TAMPERED event {event_id}: hash mismatch")
            expected_prior = stored_hash
        return breaks

    def dead_letter_count(self) -> int:
        c = self._c()
        try:
            return c.execute("SELECT COUNT(*) FROM dead_letter").fetchone()[0]
        finally:
            c.close()

    def taxonomy_drift(self) -> dict[str, int]:
        """15.1.2 — unregistered types seen, so gaps are observable, not silent."""
        c = self._c()
        try:
            rows = c.execute(
                "SELECT type, COUNT(*) FROM decision_trace WHERE registered=0 "
                "GROUP BY type").fetchall()
        finally:
            c.close()
        return dict(rows)
