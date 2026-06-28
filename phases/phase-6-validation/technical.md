# Phase 6 — Validation · Technical Scope

**Goal:** Run the full system under the sim/replay harness and measure expectancy. No new
modules; harden + measure.

## Modules + maturity
| # | Module | Maturity | Note |
|---|--------|----------|------|
| 1–16 | all | REAL | exercised together, paper/backtest |

## What runs
- The deterministic trading loop on historical/replayed data.
- The PORCUPINE-style sim/replay harness across the scenario catalogue (incl. fault injection).
- The research loop feeding daily ParameterSets through the gate.

## Metrics produced
Drawdown-adjusted PnL, max drawdown, survival/equity-curve stats, win/loss distribution,
promotion-bar results (1–2 days successful PnL, LR-5).

## Dependencies on prior phases
GATE 5 signed (the learning loop and the full pipeline are real and gated).

## Note on Phase 7 (Live)
Live is an **operational** step, not a doc-build phase — wire modules 11 & 13 to real
capital after GATE 6 GO. Tracked in [../../BUILD_PLAN.md](../../BUILD_PLAN.md), no folder here.
