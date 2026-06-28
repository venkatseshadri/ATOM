# Phase 2 — Strike + Structure · Technical Scope

**Goal:** Module 4 REAL, Module 12 REAL — emit concrete legs from a decision.

## Modules + maturity
| # | Module | Maturity | Source |
|---|--------|----------|--------|
| 4 | Trade Construction | **REAL** (paper) | [04](../../modules/04-trade-construction/04-trade-construction.md) |
| 12 | Instrument & Symbol | **REAL** | [12](../../modules/12-instrument-symbol/12-instrument-symbol.md) |

## Contracts exercised
Consumes: `StrategyDecision`, `MarketSnapshot`, `Instrument`. Produces: `StructurePlan`.

## Build order (within phase)
1. Instrument & Symbol (expiry/strike/lot/symbol) → 2. Trade Construction (legs/price).

## Dependencies on prior phases
GATE 1 signed (decisions are produced to construct from).
