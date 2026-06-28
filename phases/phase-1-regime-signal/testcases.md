# Phase 1 — Regime + Signal · Acceptance / Test Cases (DoD)

## Definition of Done (checklist) — sign all before GATE 1
- [ ] Module 1 supplies a real/replayed `MarketSnapshot` (spot, chain, IV/greeks, multi-TF OHLC).
- [ ] Module 2 produces a regime label + confidence from the **7 indicator families**.
- [ ] Module 3 FSM emits open/morph/hold/exit decisions; all transition cases pass.
- [ ] Runs on replayed data with **no orders placed**.
- [ ] Line-item Given/When/Then tests for modules 1, 2, 3 pass.

## Phase acceptance tests (Given / When / Then)
- **T1.1 Snapshot integrity** — *Given* a replay feed; *When* a cycle runs; *Then* the
  snapshot has spot, a non-empty chain with IV/greeks, and aligned multi-TF OHLC.
- **T1.2 Seven-family consensus** — *Given* a snapshot where families disagree; *When*
  regime is computed; *Then* no single family dominates and confidence reflects the split.
- **T1.3 Regime labels** — *Given* engineered trend / range / reversal replays; *Then* the
  expected regime is produced with sensible confidence.
- **T1.4 FSM transitions** — *Given* a sequence of regimes; *Then* the FSM walks
  FLAT→SINGLE_SPREAD→IRON_FLY→RUNNER→FLAT exactly per FUNCTIONAL_DESIGN §1.2.
- **T1.5 No-order guard** — *When* any decision is emitted; *Then* nothing is sent to execution.

## Detailed line-item tests
Exhaustive per-leaf Given/When/Then: modules
[01](../../modules/01-market-data/01-market-data.md),
[02](../../modules/02-regime/02-regime.md),
[03](../../modules/03-strategy-lifecycle/03-strategy-lifecycle.md).

## Board Gate 1
Board reviews regime calls + decision quality on replay. **Sign-off unlocks Phase 2.**
