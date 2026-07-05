# Phase 3 — Technical Detail

**Status: all 5 modules built, 102 new tests green (186 total). Awaiting GATE 3.**

Goal met: a complete paper-trade lifecycle exists and is proven end-to-end — decision →
risk-gated → placed (paper fill, real chain data) → stops managed (SL/TSL/TP) → forced
flat at end of day. The risk gate is deterministic and non-overridable (RR-6, proven by a
500-iteration property test).

## What's REAL now (vs Phase 2)
| Module | Phase 3 | Source | Tests |
|--------|---------|--------|-------|
| Session Lifecycle (7) | **REAL** | `session_lifecycle.py` — day-type off the shared holiday calendar, close-relative deadlines, phase FSM, entry-window one-way latch, EOD square-off escalation ladder | 27 |
| Risk & Sizing (5) | **REAL** | `risk.py` — deterministic non-overridable gate, max-loss re-derived from leg geometry, capital/deployment/concentration/margin caps, daily-loss/DD-floor hard stops, count gates, resize-vs-reject arbitration | 25 |
| Stop Management (6) | **REAL** | `stop_management.py` — premium/PnL-frame SL+TP+TSL, ratchet-proven never-loosen, expiry-day tightening, TIME>loss-protection>TP>edge-exhausted precedence | 17 |
| Order/Execution (13) | **REAL (paper)** | `order_execution.py` — tick-rounding, idempotent client-order-ids, protective-leg-first sequencing, leg-in detection (COMPLETE vs UNWIND), partial-fill tracking, reject classification, exit/square-off construction | 22 |
| Connectivity & Auth (11) | **REAL (read-only)** | `connectivity.py` — shared-session health via antariksh's live feed heartbeat, no independent broker login | 9 |
| **Integration** | proven | `test_phase3_integration.py` — all 5 modules chained over a real Phase 1/2 construction | 2 |

Same pattern as Phase 1/2: real logic lives in its own module file, not the Phase-0 stub
files (`modules/risk.py`, `modules/stop_management.py`, etc. — untouched, still feed the
orchestrator's illustrative skeleton pass).

## Key design decisions made this phase
- **Module 11: shared session, not independent login.** Investigated before writing any
  auth code: antariksh's live feed already holds one Shoonya session under the same
  credentials; a second independent login commonly invalidates the first token (most
  broker APIs allow one active app-session per account). Board decision 2026-07-05:
  share, don't duplicate. ATOM never touches the broker token at all — it reads
  antariksh's feed heartbeat files as a read-only health proxy, since ATOM doesn't call
  the broker API directly (paper-only through Phase 6) and only needs to know whether
  the *data* it reads is trustworthy.
- **Module 7's cadence engine (§7.4) doesn't apply.** ATOM is stateless-per-cron-invocation
  (confirmed in `runner.py`'s own docstring), not a long-running process — the external
  cron tick already is the cadence. Every function recomputes fresh from `(now, calendar)`,
  which also means §7.6.2's "restart recovery" isn't a special case, it's the default path.
- **Module 5/6 reference frame is money PnL, not raw premium.** Matches what Phase 1's
  `check_exit` already computed (`current_pnl`) rather than the module docs' premium-
  ceiling convention — same monotonic-tightening invariant, algebraically mirrored.
- **Module 13's "broker call" is a paper-fill simulator** against the same real Penguin
  chain data Module 4 already consumes (no fabrication) — there's no live broker
  connection to place real orders on yet.

## Known gaps (honest, not fixed here)
- **Greek-based SL (6.1.3) and greek-driven strike selection remain N/A** — same root
  cause as Phase 2: Penguin has no per-strike delta/IV, only ltp/oi/volume.
- **§13.4 (modify), §13.9 (broker reconciliation), §13.10 (ack/TTL timeouts) are
  thin/deferred** — fundamentally about a live broker round-trip that doesn't exist
  yet. Documented, not silently skipped.
- **Not wired into `runner.py`/the live cron path yet.** Same phased-rollout discipline
  as Phase 2 (Module 12/4 were built + proven before being wired into `run_live_once.py`
  in a separate, reviewable step). `test_phase3_integration.py` proves the modules compose
  correctly; wiring them into the actual per-minute cycle is the next concrete step once
  GATE 3 is reviewed.
- **`atom_state.py` has no persistence for Module 6's ratchet state or Module 7's
  entry-latch/square-off-level** — the pure functions are correct and tested (including a
  simulated restart-reload scenario), but nothing yet writes/reads them to/from
  `AtomState` across real cron invocations. Needed before live wiring.

## Tests
102 new tests across 6 files (186 total):
- `test_session_lifecycle.py` (27), `test_risk.py` (25), `test_stop_management.py` (17),
  `test_order_execution.py` (22), `test_connectivity.py` (9),
  `test_phase3_integration.py` (2 — the full lifecycle + risk-gate-blocks-before-fill).
- `logs/phase3/*.log` — real pytest output per module + `narrated_expected_vs_actual.log`,
  a human-readable GIVEN/WHEN/EXPECT/ACTUAL walkthrough of the key acceptance scenarios,
  regenerated via `tools/phase3_demo_log.py` (zero mismatches).

## Not done
- BUILD_PLAN.md Phase 3 DoD checkboxes / GATES.md GATE 3 row — Board sign-off, not mine
  to check.
- Live wiring into `runner.py`/`run_live_once.py`/cron (see gaps above).
- `atom_state.py` schema additions for Module 6/7 persisted state.
