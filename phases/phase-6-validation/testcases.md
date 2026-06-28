# Phase 6 — Validation · Acceptance / Test Cases (DoD)

## Definition of Done (checklist) — sign all before GATE 6 (GO/NO-GO)
- [ ] Positive **drawdown-adjusted expectancy** on paper/backtest over the agreed window.
- [ ] PORCUPINE harness green across the scenario catalogue.
- [ ] Promotion bar met (1–2 days successful simulated PnL, LR-5).

## Phase acceptance tests (Given / When / Then)
- **T6.1 Expectancy** — *Given* the agreed historical window; *When* the full system runs;
  *Then* drawdown-adjusted PnL is positive and meets the agreed bar.
- **T6.2 Survival** — *Given* the worst stretch in the window; *Then* max drawdown stays within
  the floor and the account survives.
- **T6.3 Scenario coverage** — *Given* the PORCUPINE scenario catalogue (gap, expiry-gamma,
  reversal mid-fly, fault injection); *Then* the system behaves correctly on each.
- **T6.4 Promotion bar** — *Given* a freshly approved ParameterSet; *Then* it shows 1–2 days
  of successful simulated PnL before it would be allowed live.
- **T6.5 No-regression** — *Then* results are reproducible (deterministic loop) across reruns.

## Detailed line-item tests
This phase aggregates the per-module tests from all of `modules/` exercised together; see
[../../modules/README.md](../../modules/README.md).

## Board Gate 6 — GO / NO-GO
Board decides whether ATOM may trade **real capital**. GO unlocks Phase 7 (Live, operational).
