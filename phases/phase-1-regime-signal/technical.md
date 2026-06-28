# Phase 1 — Regime + Signal · Technical Scope

**Goal:** Modules 1, 2, 3 go REAL; produce decisions from a replay data source.

## Modules + maturity
| # | Module | Maturity | Source |
|---|--------|----------|--------|
| 1 | Market Data | **REAL** (replay/live source) | [01](../../modules/01-market-data/01-market-data.md) |
| 2 | Regime | **REAL** (7-family) | [02](../../modules/02-regime/02-regime.md) |
| 3 | Strategy FSM | **REAL** | [03](../../modules/03-strategy-lifecycle/03-strategy-lifecycle.md) |
| 12 | Instrument & Symbol | minimal REAL (enough to label the chain) | [12](../../modules/12-instrument-symbol/12-instrument-symbol.md) |
| 16 | Config | minimal REAL (params feed) | [16](../../modules/16-config-parameterset/16-config-parameterset.md) |
| 15 | Telemetry | STUB+ (capture decisions for review) | [15](../../modules/15-telemetry-audit/15-telemetry-audit.md) |

## Contracts exercised
Consumes: `MarketSnapshot`. Produces: `RegimeState`, `StrategyDecision`.

## Build order (within phase)
1. Market Data (real source) → 2. Regime (7-family) → 3. Strategy FSM.

## Dependencies on prior phases
GATE 0 signed (contracts frozen, skeleton runs).
