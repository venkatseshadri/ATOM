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

## Data source for Module 12 (real, not reinvented)
Module 12 does NOT build its own expiry/symbol/lot-size logic. `antariksh/config/token_resolver.py`
+ `antariksh/tools/bootstrap_scrip_master.py` already implement this (real Shoonya NFO/BFO
master, holiday-aware expiry, per-index symbol grammar, lot size) and feed live capture
(`feed.py:144`). Module 12 reads `antariksh/data/static_metadata.db:scrip_master` as the
master-as-truth source, but adds its OWN staleness gate (12.1.4/12.8.1 — required regardless
of upstream) rather than trusting the file blindly: the antariksh cache was found 38-54 days
stale with no refresh cron (2026-07-05 review) — filed as
[antariksh T25](../../../antariksh/docs/DAMBUILDER_STATE.md) for DS to fix the refresh
cadence. Module 12 must fail-closed / flag if the master it reads is older than 1 trading day,
independent of whether T25 has landed yet.
