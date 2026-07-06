"""Module 16 — Config & ParameterSet (Phase 4, real).

Fixes a real bug found 2026-07-06: `run_live_once.py` called `config.load_config()`
fresh every single cron tick — config could silently change mid-day, violating this
module's own core invariant ("one truth, frozen for the day"). This module makes
freezing a real, persisted, idempotent operation: the first cycle of a trading day
freezes the day's ParameterSet; every later cycle that day gets back the IDENTICAL
sealed object, re-validated but never recomputed from a possibly-different config file.

Scope note (honest, not fabricated): ATOM is single-operator with no separate offline
producer/approval pipeline — the module doc's §16.2.4 (cryptographic approval
signatures, producer identity) assumes a multi-party workflow that doesn't exist here.
`approval_state` defaults to APPROVED at freeze time (the operator IS the approver);
there's no PKI/signature to verify. Everything else — schema/bounds validation,
freeze/immutability, versioning/history, atomic session-boundary swap, rollback to
last-known-good, secrets-vs-params separation, fail-safe no-set posture — is real.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
from dataclasses import replace
from datetime import date
from types import MappingProxyType

from .contracts import ParameterSet

DB_PATH = "data/parameter_sets.sqlite"

# 16.2.2 — declared bounds for ATOM's real risk-relevant keys. Unlisted keys pass
# through unchecked (permissive-by-default for non-risk knobs, per 16.1.3's
# "unclassified defaults to no-hot-reload" stance applied here as "no bounds check").
SCHEMA_BOUNDS = {
    "risk.deploy.inr": (1000, 10_000_000),
    "risk.sl.pct": (1, 100),
    "risk.tp.pct": (1, 200),
    "risk.dd.floor.pct": (1, 100),
    "risk.deployment.pct": (1, 100),
    "risk.concentration.pct": (1, 100),
    "risk.daily_loss.inr": (0, 10_000_000),
    "risk.reentry.max": (0, 20),
    "risk.concurrent.max": (0, 10),
    "risk.trades_per_day.max": (0, 100),
    "tsl.activation.pct": (0, 100),
    "tsl.trail_gap.pct": (0, 100),
    "tsl.max_plausible_credit_pct": (10, 500),
    "stop.expiry_tighten.factor": (0.1, 1.0),
    "stop.edge_exhausted.pct": (0, 200),
    "strategy.lot.size": (1, 100_000),
    "strategy.wing.strikes": (1, 50),
}

# 16.2.3 — cross-field checks: ATOM's real config has no pair of independently-valid
# keys that need a joint check beyond per-key bounds today (e.g. SL vs TP ordering
# is enforced downstream by stop_management's own defined-risk clamp, not duplicated
# here). Left as a documented gap, not a fabricated rule with nothing to check.

# 16.8.1 — credential-smuggling defense: reject any key/value shaped like a secret.
# "key" alone (not just "api_key") catches auth_key/private_key/secret_key-style names
# — none of ATOM's real SCHEMA_BOUNDS keys contain "key", so this isn't over-broad today.
_SECRET_KEY_PATTERN = re.compile(r"(token|secret|password|passwd|auth|key|credential)",
                                 re.IGNORECASE)


def _content_hash(params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def validate(params: dict) -> list[str]:
    """16.2.1/16.2.2/16.2.3/16.8.1 — returns ALL violations (not just the first), so
    a rejected candidate carries full diagnostics for the producer (here: the operator
    editing config/atom.conf)."""
    violations = []
    for key, value in params.items():
        if _SECRET_KEY_PATTERN.search(key):
            violations.append(f"SECRET_SHAPED_KEY:{key}")
            continue
        if key in SCHEMA_BOUNDS:
            lo, hi = SCHEMA_BOUNDS[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                violations.append(f"TYPE_ERROR:{key} expected number, got {type(value).__name__}")
                continue
            if value != value or value in (float("inf"), float("-inf")):  # NaN/Inf
                violations.append(f"NAN_OR_INF:{key}")
                continue
            if not (lo <= value <= hi):
                violations.append(f"OUT_OF_BOUNDS:{key}={value} not in [{lo},{hi}]")
    return violations


class ConfigFreezeStore:
    """16.3-16.7 — freeze/version/history/rollback/serve. One row per accepted
    ParameterSet; `is_last_known_good` tracks 16.4.3 independently of "latest"."""

    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = db_path
        c = self._c()
        c.execute("""CREATE TABLE IF NOT EXISTS parameter_sets (
            version INTEGER PRIMARY KEY AUTOINCREMENT,
            valid_for TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            params_json TEXT NOT NULL,
            approval_state TEXT NOT NULL,
            evidence_ref TEXT,
            accepted_at TEXT NOT NULL,
            frozen_at TEXT,
            rejected_reasons TEXT,
            is_last_known_good INTEGER NOT NULL DEFAULT 0,
            rolled_back INTEGER NOT NULL DEFAULT 0
        )""")
        c.commit()
        c.close()

    def _c(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def stage(self, params: dict, valid_for: str, evidence_ref: str = "operator-edit",
             now_iso: str = "") -> tuple[int | None, list[str]]:
        """16.2 + 16.4.1 — validate and version-assign a candidate. Returns
        (version, []) on success or (None, violations) on rejection — a rejected
        candidate is NEVER written as accepted (16.9.1: fully isolated, can't reach
        staged/frozen/last-known-good)."""
        violations = validate(params)
        c = self._c()
        try:
            content_hash = _content_hash(params)
            if violations:
                c.execute(
                    "INSERT INTO parameter_sets (valid_for, content_hash, params_json, "
                    "approval_state, evidence_ref, accepted_at, rejected_reasons) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (valid_for, content_hash, json.dumps(params), "REJECTED", evidence_ref,
                     now_iso, json.dumps(violations)))
                c.commit()
                return None, violations
            c.execute(
                "INSERT INTO parameter_sets (valid_for, content_hash, params_json, "
                "approval_state, evidence_ref, accepted_at) VALUES (?,?,?,?,?,?)",
                (valid_for, content_hash, json.dumps(params), "APPROVED", evidence_ref, now_iso))
            c.commit()
            return c.execute("SELECT last_insert_rowid()").fetchone()[0], []
        finally:
            c.close()

    def freeze_for_session(self, valid_for: str, raw_params: dict,
                           now_iso: str) -> tuple[ParameterSet | None, list[str]]:
        """16.3.1 — the ONE entrypoint callers should use. Idempotent per `valid_for`:
        if a frozen set already exists for this date, return that SAME sealed object
        (re-validated as a defense-in-depth check, never recomputed from a possibly-
        different current config). Otherwise stage+freeze `raw_params` now. Fixes the
        real bug: no more 'reload every cron tick' — one freeze per day, period."""
        c = self._c()
        try:
            existing = c.execute(
                "SELECT version, content_hash, params_json, approval_state, evidence_ref "
                "FROM parameter_sets WHERE valid_for=? AND frozen_at IS NOT NULL "
                "ORDER BY version DESC LIMIT 1", (valid_for,)).fetchone()
        finally:
            c.close()
        if existing:
            version, content_hash, params_json, approval_state, evidence_ref = existing
            params = json.loads(params_json)
            revalidation = validate(params)   # 16.3.1 defense-in-depth re-check
            if revalidation:
                return None, [f"FROZEN_SET_NOW_INVALID:{r}" for r in revalidation]
            return self._to_parameterset(version, content_hash, params, valid_for,
                                         approval_state, evidence_ref), []

        version, violations = self.stage(raw_params, valid_for, now_iso=now_iso)
        if violations:
            return None, violations
        c = self._c()
        try:
            c.execute("UPDATE parameter_sets SET frozen_at=? WHERE version=?", (now_iso, version))
            c.commit()
            row = c.execute(
                "SELECT content_hash, params_json, approval_state, evidence_ref "
                "FROM parameter_sets WHERE version=?", (version,)).fetchone()
        finally:
            c.close()
        content_hash, params_json, approval_state, evidence_ref = row
        return self._to_parameterset(version, content_hash, json.loads(params_json),
                                     valid_for, approval_state, evidence_ref), []

    def _to_parameterset(self, version, content_hash, params, valid_for, approval_state,
                         evidence_ref) -> ParameterSet:
        """16.3.2/16.7.1 — serve a deep-copied, structurally read-only view. A
        MappingProxyType can't be mutated even by a careless consumer; ParameterSet
        itself is already frozen (attribute reassignment blocked), this closes the
        remaining gap (mutating the dict IN PLACE)."""
        sealed = MappingProxyType(copy.deepcopy(params))
        return ParameterSet(version=f"v{version}:{content_hash}", valid_for=valid_for,
                            params=sealed, evidence_ref=evidence_ref,
                            approval_state=approval_state)

    def mark_last_known_good(self, version: int) -> None:
        """16.4.3 — promote only after a session has run cleanly; caller decides
        'clean' (this module doesn't know if trading actually went well)."""
        c = self._c()
        try:
            c.execute("UPDATE parameter_sets SET is_last_known_good=0")
            c.execute("UPDATE parameter_sets SET is_last_known_good=1 WHERE version=?", (version,))
            c.commit()
        finally:
            c.close()

    def last_known_good(self) -> ParameterSet | None:
        c = self._c()
        try:
            row = c.execute(
                "SELECT version, content_hash, params_json, valid_for, approval_state, "
                "evidence_ref FROM parameter_sets WHERE is_last_known_good=1 "
                "ORDER BY version DESC LIMIT 1").fetchone()
        finally:
            c.close()
        if not row:
            return None   # 16.4.3: explicit "none", never silently the latest
        version, content_hash, params_json, valid_for, approval_state, evidence_ref = row
        return self._to_parameterset(version, content_hash, json.loads(params_json),
                                     valid_for, approval_state, evidence_ref)

    def rollback(self, valid_for: str, now_iso: str) -> tuple[ParameterSet | None, str | None]:
        """16.6 — roll back to last-known-good for a NEW valid_for date, re-validating
        before activating (schema may have evolved, storage may have rotted)."""
        lkg = self.last_known_good()
        if lkg is None:
            return None, "NO_LAST_KNOWN_GOOD"   # 16.6.1: escalate, never guess
        revalidation = validate(dict(lkg.params))
        if revalidation:
            return None, f"LAST_KNOWN_GOOD_NOW_INVALID:{revalidation}"
        version, violations = self.stage(dict(lkg.params), valid_for,
                                         evidence_ref=f"rollback-from-{lkg.version}",
                                         now_iso=now_iso)
        if violations:
            return None, f"ROLLBACK_REVALIDATION_FAILED:{violations}"
        c = self._c()
        try:
            c.execute("UPDATE parameter_sets SET frozen_at=?, rolled_back=1 WHERE version=?",
                     (now_iso, version))
            c.commit()
            row = c.execute("SELECT content_hash, params_json, approval_state, evidence_ref "
                           "FROM parameter_sets WHERE version=?", (version,)).fetchone()
        finally:
            c.close()
        content_hash, params_json, approval_state, evidence_ref = row
        return self._to_parameterset(version, content_hash, json.loads(params_json),
                                     valid_for, approval_state, evidence_ref), None

    def history(self, valid_for: str | None = None) -> list[dict]:
        """16.4.2 — append-only lineage; never overwritten/deleted."""
        c = self._c()
        try:
            q = "SELECT version, valid_for, content_hash, approval_state, accepted_at, " \
                "frozen_at, rejected_reasons, rolled_back FROM parameter_sets"
            params = ()
            if valid_for:
                q += " WHERE valid_for=?"
                params = (valid_for,)
            q += " ORDER BY version"
            cols = ["version", "valid_for", "content_hash", "approval_state", "accepted_at",
                   "frozen_at", "rejected_reasons", "rolled_back"]
            return [dict(zip(cols, row)) for row in c.execute(q, params).fetchall()]
        finally:
            c.close()
