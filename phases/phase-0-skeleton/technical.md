# Phase 0 — Skeleton · Technical Scope

**Goal:** Freeze every contract object; stand up all 16 modules as stubs; wire the
orchestrator into one logged pass.

## Modules + maturity
| # | Module | Maturity | Source |
|---|--------|----------|--------|
| 1–16 | all modules | **STUB** | [../../modules/README.md](../../modules/README.md) |

## Contracts (all frozen this phase)
`Session`, `Instrument`, `MarketSnapshot`, `RegimeState`, `StrategyDecision`,
`StructurePlan`, `RiskVerdict`, `OrderRequest`, `Fill`, `PositionState`, `TraceEvent`,
`ParameterSet`. Definitions: [../../TECHNICAL_DESIGN.md](../../TECHNICAL_DESIGN.md) §2.

## Build order (within phase)
Per TECHNICAL_DESIGN.md §5:
1. Config → 2. Contract objects → 3. Telemetry → 4. Auth & Session → 5. Instrument →
6. Market Data → 7. Regime → 8. Strategy FSM → 9. Structure Builder → 10. Risk Engine →
11. Order/Trade → 12. Ledger → 13. Orchestrator.

## Dependencies on prior phases
None — this is the first executable phase.
