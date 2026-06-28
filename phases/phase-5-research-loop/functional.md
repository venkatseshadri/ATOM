# Phase 5 — Research Loop · Functional Scope

**Goal:** Learn after hours — score what happened, propose better numbers, gate them in —
without ever putting AI in the live loop.

## In-scope functional modules
| FM | Behaviour delivered this phase | Source module doc |
|----|--------------------------------|-------------------|
| FM-PostMortem | Trade/session/regime autopsy + scoring | [08-post-mortem](../../modules/08-post-mortem/08-post-mortem.md) |
| FM-Optimization | Drawdown-adjusted/survival objective → candidate ParameterSet | [09-optimization](../../modules/09-optimization/09-optimization.md) |
| FM-FeedbackGate | EOD safety + backtest + morning approval + promotion | [10-feedback-gate](../../modules/10-feedback-gate/10-feedback-gate.md) |

## Functional outcome of this phase
- A full learn→propose→approve cycle: post-mortem → optimization → ParameterSet →
  PORCUPINE backtest → morning human approval → frozen set for next day.
- **AI is proven out of the trading loop** (NFR-4): research writes a cache only, risk-gated.

## Out of scope
- Proving overall expectancy (Phase 6); live capital (Phase 7).

Refs: [../../FUNCTIONAL_DESIGN.md](../../FUNCTIONAL_DESIGN.md) §7 (post-mortem/optimization/feedback).
