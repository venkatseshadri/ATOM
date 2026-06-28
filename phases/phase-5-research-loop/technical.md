# Phase 5 — Research Loop · Technical Scope

**Goal:** Modules 8, 9, 10 go REAL — the slow, offline, gated learning loop.

## Modules + maturity
| # | Module | Maturity | Source |
|---|--------|----------|--------|
| 8 | Post-Mortem | **REAL** | [08](../../modules/08-post-mortem/08-post-mortem.md) |
| 9 | Optimization | **REAL** | [09](../../modules/09-optimization/09-optimization.md) |
| 10 | Feedback Gate | **REAL** | [10](../../modules/10-feedback-gate/10-feedback-gate.md) |

## Contracts exercised
Consumes: completed trades, `TraceEvent`, historical data. Produces: `ParameterSet`
(candidate → approved). Two-loop separation per TECHNICAL_DESIGN §8.

## Build order (within phase)
1. Post-Mortem → 2. Optimization → 3. Feedback Gate (backtest + approval).

## Dependencies on prior phases
GATE 4 signed (trade history, P&L, and audit traces exist to learn from).
