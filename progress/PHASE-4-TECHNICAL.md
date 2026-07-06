# Phase 4 — Technical Detail

**Status: all 3 modules built + live-wired + PORCUPINE fault harness, 286 tests green. Awaiting GATE 4.**

Goal met: a real ledger (position/P&L/reconciliation), a durable tamper-evident audit
trail (every trade reconstructable end-to-end), and a frozen daily ParameterSet exist and
are wired into the live cycle — replacing the mid-day-config-reload bug that motivated
starting this phase.

## What's REAL now (vs Phase 3)
| Module | Phase 4 | Source | Tests |
|--------|---------|--------|-------|
| Config & ParameterSet (16) | **REAL** | `config_freeze.py` — schema/bounds validation, freeze + structural immutability (`MappingProxyType`), monotonic versioning + append-only history, rollback to last-known-good, secrets-vs-params defense, fail-safe no-set posture | 23 |
| Ledger & Persistence (14) | **REAL** | `ledger.py` — idempotent fill application (weighted-avg cost basis), mark-to-market with a real flat/low-confidence/OK tri-state, realized-vs-unrealized split, broker reconciliation (MATCH/MISSED_FILL/PHANTOM_FILL/UNKNOWN_AT_BROKER), EOD finalization (flat assertion + idempotent daily rollup) | 28 |
| Telemetry & Audit (15) | **REAL** | `audit.py` — durable `decision_trace` SQLite table, hash-chained tamper-evidence, full trade-lifecycle reconstruction, event-taxonomy registry (unregistered types flagged not rejected), dead-letter quarantine for malformed events | 13 |
| **Integration** | wired | `runner.run_once(audit=)` logs decision/risk/exit events under one trade_id; `run_live_once.py` freezes config once/day, prints Module 14 P&L + Module 15 audit confirmation | 2 |
| **PORCUPINE harness** | closed | `scenarios_phase4.py` — 7 direct fault-injection checks | 7 |

Same pattern as every prior phase: real logic lives in its own module file, not the
Phase-0 stub files.

## The bug that motivated this phase
`run_live_once.py` called `config.load_config()` fresh every single cron tick — config
could silently change mid-day, violating this phase's own T4.4 ("frozen config,
immutable for the day"). Found by inspection before writing any Module 16 code, fixed by
`ConfigFreezeStore.freeze_for_session()`: freezes once per calendar day, idempotent —
every later cycle that day gets back the SAME sealed `ParameterSet` even if
`config/atom.conf` is edited in between. Live-verified against the real config file.

## Key design decisions
- **Module 14 is snapshot-of-record (Approach B), not event-sourced.**
  `AtomState.paper_trades` already IS the mutable position record; `ledger.py` adds the
  accounting math on top rather than building a parallel event-sourced store. Matches
  every prior module's discipline of extending real existing code, not duplicating it.
- **Recoverability (14.6) and single-writer integrity (14.7) needed no new code.**
  ATOM's stateless-per-cron-invocation architecture (same principle as Module 7) already
  satisfies both — tested directly (a fresh `AtomState` instance against the same DB file
  reconstructs position/FSM state correctly), not re-implemented.
- **Module 15's storage is a SQLite `decision_trace` table**, per your explicit choice —
  mirrors the sister systems' existing decision_trace/trade_outcomes convention, queryable
  and joinable, not an append-only JSONL log.
- **Module 16's approval workflow is simplified honestly.** ATOM is single-operator with
  no separate offline producer/approval pipeline — §16.2.4's cryptographic-signature
  verification is N/A (documented, not fabricated); `approval_state` defaults to APPROVED
  at freeze time since the operator IS the approver.
- **Module 15's hash-chain gives real tamper-evidence without a signing infrastructure** —
  each event's hash covers the prior event's hash, so both editing a past event AND
  deleting one are detectable (deletion breaks the NEXT event's prior_hash link even
  though the deleted row itself is gone).

## Known gaps / scope notes (honest, not fabricated)
- **Module 14's broker reconciliation (§14.8) has no live broker to reconcile against
  yet** (paper-only through Phase 6) — built generically against any externally-reported
  position list, ready for Phase 7, tested with synthetic divergence.
- **Module 15's §15.2 (async buffering), §15.5 (log-level routing), §15.9 (retention/
  archival), §15.10 (self-telemetry dashboards) are N/A or premature** at ATOM's
  single-process-per-cron-tick scale — each `append()` is already off the trading
  decision's critical path, satisfying 15.2's intent without an async queue no one
  contends for.
- **Module 16's §16.1 (multi-source config registry) is thin** — ATOM only ever has 2
  layers (DEFAULTS + `config/atom.conf` overlay), already handled by `config.py`; no
  enterprise multi-source precedence engine was built since there's nothing to layer.
- **Fill idempotency (`applied_fills` table) and Module 14's `apply_fill()` math are
  built for the real shape but not yet exercised live** — Phase 1/2's construction still
  produces a position in one synchronous step (no discrete fill-event stream from a real
  broker yet), same "built ahead of the live wiring" pattern as Module 13's paper-fill
  simulator in Phase 3.

## Tests
84 new tests across 5 files (286 total):
- `test_config_freeze.py` (23), `test_ledger.py` (28), `test_audit.py` (13),
  `test_harness_phase4.py` (2 — the PORCUPINE catalogue), plus 2 new tests in
  `test_phase1.py` for the runner-level audit wiring.
- `logs/phase4/*.log` — `porcupine_harness.log` (7/7 direct fault checks),
  `module_tests_output.log` (64 unit tests), `narrated_expected_vs_actual.log` (zero
  mismatches, `tools/phase4_demo_log.py`).

## Not done
- BUILD_PLAN.md Phase 4 DoD checkboxes / GATES.md GATE 4 row — Board sign-off, not mine
  to check.
- Module 7's calendar-based entry-window check still doesn't gate `decide()` (Phase 3
  gap, unchanged this phase).
- Real multi-day equity-curve tracking for a true drawdown-floor gate (Phase 3 gap,
  unchanged this phase).
