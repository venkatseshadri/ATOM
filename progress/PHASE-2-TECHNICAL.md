# Phase 2 — Technical Detail

**Status: built, 12 new tests green (82 total). Awaiting GATE 2.**

Goal met: instrument resolution and structure construction now use the **real broker
contract list** (antariksh `scrip_master`) instead of hardcoded expiry/lot/step constants
— and work for **both NIFTY and SENSEX**, not just NIFTY.

**Correction (2026-07-05, same session):** initially reported "no SENSEX capture fixture
exists" and tested SENSEX with a hand-built `Snapshot`. User caught this — real SENSEX
capture (`python-trader/varaha/data/capture_sensex.sqlite`, 8,025 enriched rows, 80,287
option_prices rows) already exists and is live. Checking it surfaced a real, live bug:
`penguin.py`'s option-chain queries hardcoded `f"NIFTY{tok}%"` — SENSEX's chain read
**zero rows**, always, silently (confirmed: chain size 0 against the real live DB before
the fix). Worse, `_expiry_to_tsym`'s date-token format (`DD+Mon+YY`) is NIFTY-only —
SENSEX's real grammar is `YY+M+DD` (confirmed against real tsyms like
`SENSEX2661173400CE`), so even parameterizing the index wouldn't have been enough. Fixed
both in `penguin.py` (new `_expiry_to_tok(expiry, index)`, `STEP_BY_INDEX`, `index` params
threaded through `_option_chain`/`_option_chain_asof`/`latest_price_for`/
`latest_price_for_asof`) — verified live: SENSEX chain went from 0 to 22 real entries.
Built a real `tests/fixtures/capture_sensex_fixture.sqlite` (1hr excerpt of the live DB,
same shape as the NIFTY fixture) and rewrote the SENSEX Phase 2 tests to use it end-to-end
instead of a hand-built snapshot. Also fixed a consistency bug this surfaced: when `im` is
passed, `build_order` now stores `inst.expiry` (Module 12's real answer, what the legs
were actually resolved against) rather than `snap.expiry` (Penguin's own, possibly stale)
— otherwise exit-time price requery would target the wrong contract. `PaperOrder` gained
an `index` field (default `"NIFTY"`), threaded into `check_exit`/`replay.py` so exit
price-lookups query the right index's chain too.

## What's REAL now (vs Phase 1)
| Module | Phase 2 | Source |
|--------|---------|--------|
| Instrument & Symbol (12) | **REAL** | `atom.instrument.InstrumentMaster` — reads antariksh `static_metadata.db:scrip_master` **read-only**; real expiry (nearest listed, holiday-shift by construction — no weekday math), real strike ladder/step, real lot size, real round-trip-verified tradingsymbol per index |
| Trade Construction (4) | **REAL (extended)** | `phase1.build_order(..., im=InstrumentMaster(...))` — same construction as Phase 1, now index-generic: real per-index step/lot instead of the NIFTY-only `STEP=50` constant, real leg tradingsymbols via `im.lookup()` |

`modules/instrument.py` / `modules/structure_builder.py` (Phase-0 stubs, feed the
orchestrator's illustrative skeleton pass) are untouched — same pattern as Phase 1: real
logic lives outside the Phase-0 module files (see phase1.py's own docstring).

## Backward compatibility
`build_order()` / `cycle()` take an **optional** `im` param. Omitted → byte-identical to
Phase 1 (NIFTY-only STEP constant, CFG lot default, no symbols) — every existing Phase 1
caller (replay.py, harness_phase1.py, live cron path) is unaffected until explicitly wired
to pass a real `InstrumentMaster`.

## Known gaps (honest, not fixed here)
- **T2.3 (greek-driven strikes) is N/A.** Penguin's `option_prices` table has no per-strike
  delta/IV — only ltp/oi/volume (checked penguin.py + capture schema). Construction uses
  the distance-offset method (Module 4 doc §4.3.3.2), the documented fallback when greeks
  are unavailable (§4.3.3.4) — not a fabricated delta.
- **tick_size is a constant (0.05), not master-sourced.** antariksh's `scrip_master` schema
  (`bootstrap_scrip_master.py`) never captured the broker file's `TickSize` column, even
  though the raw NFO/BFO dump has it. 0.05 is correct today for NSE/BSE index options but
  is the same class of risk as the T25 lot-size bug if it ever changes. Residual: add
  `tick_size` to antariksh's scrip_master schema.
- **`atom_state.py`'s `paper_trades` table has no `index` column** — the live-state
  persistence layer (used by `runner.py`'s real paper-trading loop) is still NIFTY-only
  by omission. `check_exit` defaults to `"NIFTY"` if the key is absent, so today's
  NIFTY-only live paper-trading is unaffected, but SENSEX live paper-trading would need
  this schema updated first. Not done — a live-storage schema change felt like a separate,
  higher-risk step than this session's scope.
- **Only two structure templates** (bull_put_spread / bear_call_spread), same as Phase 1 —
  iron fly / condor templates are not in this phase's DoD (BUILD_PLAN Phase 2 table lists
  modules 4/12 REAL, not new templates) and weren't added.
- **`InstrumentMaster` has its own staleness check** (fails loud via telemetry if the
  master is >1 trading day old) — independent of antariksh's own T25 refresh cron, per
  the Board's "belt and suspenders" call.

## Tests
`tests/test_phase2.py` (12 tests) against `tests/fixtures/scrip_master_fixture.duckdb` (a
small frozen snapshot of the real antariksh scrip_master schema — deterministic, not the
live daily-changing DB):
- T2.1 expiry resolution: nearest-listed, advances past current week, SENSEX
  holiday-shift (resolves to the actually-listed Wednesday, not a hardcoded Thursday),
  fail-closed when nothing listed.
- T2.2 symbol format: NIFTY vs SENSEX grammar, round-trip both ways, cross-index
  application correctly fails (the exact bug class in `fix_sensex_option_symbols.md`).
- T2.4 plan completeness: `build_order(..., im=...)` for NIFTY and SENSEX, both against
  real capture fixtures — real lot, real symbols, non-zero credit/loss, round-trip proof
  that the stored `o.expiry` matches the legs actually resolved.
- T2.5 refusal guard: strike walking off the listed ladder refuses (`SYMBOL_UNRESOLVED`),
  never emits a partial/guessed leg.
- Legacy-path regression: no `im` passed → identical to pre-Phase-2 behaviour.
- Day-of-week index routing: Fri/Mon/Tue → NIFTY, Wed/Thu → SENSEX (matches the exact
  weekday split checked in).

## Wired into the live path (2026-07-05, same session)
`run_live_once.py` (and hence `cron/run_atom_paper.sh`) now:
- picks NIFTY vs SENSEX by day-of-week (`phase1.index_for_weekday()` — 0-1 DTE rule: NIFTY
  weekly expires Tuesday so Fri/Mon/Tue are its 0-1 DTE days, SENSEX expires Thursday so
  Wed/Thu are its 0-1 DTE days), overridable with `--index NIFTY|SENSEX`;
- reads that day's real `capture_{index}.sqlite` and passes a real `InstrumentMaster()` to
  every cycle (`runner.run_once(..., im=im)` — `im` is no longer just an optional test-path
  param, it's the live default for both indices);
- keeps **separate FSM/position state per index** (`data/atom_state.sqlite` for NIFTY,
  unchanged path; new `data/atom_state_sensex.sqlite` for SENSEX) — the two indices' open
  positions can never collide or overwrite each other's state.

`runner.run_once()` gained an optional `im` param (default `None`, threaded straight to
`phase1.cycle()`), backward-compatible with every existing caller.

## Not done
- BUILD_PLAN.md Phase 2 DoD checkboxes / GATES.md GATE 2 row — Board sign-off, not mine to
  check.
- `replay.py` (backtest driver) still doesn't pass `im=` — replay tests use the legacy path.
  Same fix, not done yet.
- `atom_state.py`'s `paper_trades` schema doesn't record `index` — see the gap noted above;
  now that SENSEX writes to its own state file this matters less (no cross-index mixing),
  but a single combined ledger view (if ever wanted) would need it.
