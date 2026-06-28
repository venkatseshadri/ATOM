# Phase 5 — Research Loop · Acceptance / Test Cases (DoD)

## Definition of Done (checklist) — sign all before GATE 5
- [ ] Module 8 produces trade/session/regime post-mortem scores.
- [ ] Module 9 emits a candidate `ParameterSet` optimizing drawdown-adjusted PnL + survival, with rationale.
- [ ] Module 10 runs EOD safety + PORCUPINE backtest + morning human approval before a set goes live.
- [ ] Proven: **AI never in the trading loop** (NFR-4) — research writes cache only, risk-gated.

## Phase acceptance tests (Given / When / Then)
- **T5.1 Post-mortem scores** — *Given* a day of trades + traces; *Then* per-trade,
  per-session, per-regime scores are produced.
- **T5.2 Candidate set** — *Given* post-mortem findings; *When* optimization runs; *Then* a
  `ParameterSet` of fixed numbers + rationale is emitted, scored on drawdown-adjusted/survival.
- **T5.3 Safety bounds** — *Given* a degenerate candidate; *Then* EOD safety checks reject it.
- **T5.4 Promotion gate** — *Given* a candidate; *When* it fails the PORCUPINE backtest or the
  1–2-day PnL bar; *Then* it is **not** promoted.
- **T5.5 Morning approval** — *Given* a passing candidate; *When* the human approves; *Then* it
  becomes the next session's frozen set; without approval, the last-good set is kept.
- **T5.6 AI-out-of-loop** — *Given* the live trading cycle; *Then* no LLM/AI call occurs in it.

## Detailed line-item tests
Modules [08](../../modules/08-post-mortem/08-post-mortem.md),
[09](../../modules/09-optimization/09-optimization.md),
[10](../../modules/10-feedback-gate/10-feedback-gate.md).

## Board Gate 5
Board reviews a full learn→propose→approve cycle. **Sign-off unlocks Phase 6.**
