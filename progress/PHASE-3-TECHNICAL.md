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

## Live wiring (2026-07-05, same session — user asked to validate TSL/SENSEX live)
`runner.run_once()` gained two opt-in flags, both defaulting `False` (every existing
caller — replay.py, the harness, all Phase 1/2 tests — is byte-identical unless it
explicitly asks):
- `use_tsl=True` — routes exit-checking through `phase1.check_exit(levels_state=...)`
  (Module 6's real TSL, not the old static-SL/TP-only path). Ratchet state (`tsl`,
  `tsl_armed`, `high_water_pnl`) persists via new `AtomState` columns
  (`update_stop_state()`/`last_open_position()`) so a fresh cron process reloads the
  tight stop instead of recomputing from scratch.
- `risk_gate=True` — after `build_order()` proposes an OPEN, gates it through Module 5
  (`risk.evaluate()`) using `AtomState.derive_account()` (real realized P&L / open-count
  / re-entry-count derived from `paper_trades`, not a separately-tracked ledger). A
  `REJECTED` verdict downgrades the cycle to `STAND_DOWN` — no trade recorded.

`run_live_once.py` (the actual cron entrypoint) now passes both `True` for every cycle,
both fixture and live, and prints the risk verdict + TSL floor/high-water when active.
`AtomState.paper_trades` also gained an `index_name` column (Phase 2's known gap —
SENSEX trades were never persisted with their index) — NULL-safe, defaults to NIFTY for
pre-existing rows.

**Not wired:** Module 7's calendar-based entry-window/day-type check is NOT used to gate
`decide()` yet — `decide()` still uses its own hardcoded EOD-cutoff string comparison
(unchanged from Phase 1). Session Lifecycle's richer day-type/expiry/halt awareness is
built and tested standalone but not yet the thing deciding whether `decide()` may open.
Module 11's session-health check is also not consulted in the live cycle yet (it's a
secondary signal per its own design — bar-freshness already gates on stale data).

## Real broker margin (2026-07-05, same session — user asked whether login captures margin)
Module 11 gained `read_broker_margin()`: reads antariksh's `data/broker_limits.json`
(refreshed weekdays 08:30 by a live broker-API call in `margin_calculator.py` — a REAL
account snapshot: `total_margin_available`, `used_margin`, `free_margin`,
`margin_multiplier`), fail-safe on missing/malformed/future-dated/stale (>4 days,
covers a Fri-08:30-to-Mon-15:30 gap) data. `AtomState.derive_account()` folds this in as
`broker_margin_available`/`broker_free_margin`/`broker_margin_reason`. `risk.py` adds an
opt-in gate: a **confirmed** low reading (`broker_free_margin` < `risk.broker_margin_floor_inr`,
default ₹50k) hard-blocks (`BROKER_MARGIN_LOW`); an **unknown** reading (stale/missing) is
informational only (`BROKER_MARGIN_UNKNOWN:<reason>`), never a hard block — the existing
capital/deployment/margin gates are the real safety net, this is an independent sanity
check on the REAL shared account (covers every system on the box, not just ATOM), not a
substitute for ATOM's own strategy-scoped budget. Same no-new-login stance as the session
heartbeat check — read-only, no broker call from ATOM itself.

12 new tests (`test_connectivity.py` +8, `test_risk.py` +4, `test_atom_state_phase3.py`
+1). Live-verified against the real file: ₹579,918.15 free margin read and passed cleanly.

## Known gaps (honest, not fixed here)
- **Greek-based SL (6.1.3) and greek-driven strike selection remain N/A** — same root
  cause as Phase 2: Penguin has no per-strike delta/IV, only ltp/oi/volume.
- **§13.4 (modify), §13.9 (broker reconciliation), §13.10 (ack/TTL timeouts) are
  thin/deferred** — fundamentally about a live broker round-trip that doesn't exist
  yet. Documented, not silently skipped.
- **`derive_account()`'s `peak_equity` is today's starting capital, not a real
  multi-day high-water mark** — no historical equity curve is persisted anywhere, so
  the DD-floor gate only reacts to *today's* realized losses, not a true peak-to-trough
  drawdown across days. Flagged, not silently glossed over.
- **`deployed` is always reported as 0** — ATOM doesn't simulate real margin-blocking,
  so the deployment-cap gate never actually binds in practice; only the at-risk and
  margin-sufficiency gates (which use `max_loss`, real) are load-bearing today.
- Module 7 (entry-window authority) and Module 11 (session health) are built + tested
  but not yet consulted by the live cycle (see "Live wiring" above).

## Tests
110 new tests across 8 files (199 total):
- `test_session_lifecycle.py` (27), `test_risk.py` (25), `test_stop_management.py` (17),
  `test_order_execution.py` (22), `test_connectivity.py` (9),
  `test_phase3_integration.py` (2), `test_atom_state_phase3.py` (6 — index/TSL
  persistence, account derivation), plus 4 new tests in `test_phase1.py` covering
  `run_once(use_tsl=, risk_gate=)`'s wiring behavior and 3 covering `check_exit`'s
  `levels_state` param directly.
- `logs/phase3/*.log` — real pytest output per module + `narrated_expected_vs_actual.log`,
  a human-readable GIVEN/WHEN/EXPECT/ACTUAL walkthrough of the key acceptance scenarios,
  regenerated via `tools/phase3_demo_log.py` (zero mismatches).

## Not done
- BUILD_PLAN.md Phase 3 DoD checkboxes / GATES.md GATE 3 row — Board sign-off, not mine
  to check.
- Module 7/11 live consultation (see "Live wiring" above).
- Real multi-day equity-curve tracking for a true drawdown-floor gate.
