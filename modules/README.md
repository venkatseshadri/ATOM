# ATOM — Module Decomposition (discovery docs)

One folder per module. Each doc is a **breadth-first discovery decomposition** produced
by an isolated domain-analyst agent that saw **only that module + the market arena** (no
overall strategy, no other modules, no chosen values) — to surface scenarios without bias.

Each leaf node carries: **Responsibility · Behavior/Actions · Scenarios & Possibilities
(incl. edge/failure) · Functional Test Case (Given/When/Then) · Clear Outcome**.
Market-condition scenarios are parked per-module under `## Suggestions (for bubble-up)`
for later review.

> Status: first-pass discovery. **Cross-module seam reconciliation is deferred** (pending
> Board review & refine). Expect overlaps between adjacent modules (e.g. 4 vs 13).

| # | Module | Persona | Doc |
|---|--------|---------|-----|
| 1 | Market Data | financial analyst | [01-market-data](01-market-data/01-market-data.md) |
| 2 | Regime (7-family) | financial analyst | [02-regime](02-regime/02-regime.md) |
| 3 | Strategy Lifecycle | financial analyst | [03-strategy-lifecycle](03-strategy-lifecycle/03-strategy-lifecycle.md) |
| 4 | Trade Construction | financial analyst | [04-trade-construction](04-trade-construction/04-trade-construction.md) |
| 5 | Risk & Sizing | risk analyst | [05-risk-sizing](05-risk-sizing/05-risk-sizing.md) |
| 6 | Stop Management | risk analyst | [06-stop-management](06-stop-management/06-stop-management.md) |
| 7 | Session Lifecycle | ops analyst | [07-session-lifecycle](07-session-lifecycle/07-session-lifecycle.md) |
| 8 | Post-Mortem | quant analyst | [08-post-mortem](08-post-mortem/08-post-mortem.md) |
| 9 | Optimization | quant analyst | [09-optimization](09-optimization/09-optimization.md) |
| 10 | Feedback Gate | quant/governance | [10-feedback-gate](10-feedback-gate/10-feedback-gate.md) |
| 11 | Connectivity & Auth | systems | [11-connectivity-auth](11-connectivity-auth/11-connectivity-auth.md) |
| 12 | Instrument & Symbol | microstructure | [12-instrument-symbol](12-instrument-symbol/12-instrument-symbol.md) |
| 13 | Order/Execution | systems | [13-order-execution](13-order-execution/13-order-execution.md) |
| 14 | Ledger & Persistence | systems | [14-ledger-persistence](14-ledger-persistence/14-ledger-persistence.md) |
| 15 | Telemetry & Audit | observability | [15-telemetry-audit](15-telemetry-audit/15-telemetry-audit.md) |
| 16 | Config & ParameterSet | release/config | [16-config-parameterset](16-config-parameterset/16-config-parameterset.md) |

Modules 1–10 = trading + learning; 11–16 = platform. See ../FUNCTIONAL_DESIGN.md and
../TECHNICAL_DESIGN.md for how these roll up.
