# Phase 1 — Regime + Signal · Functional Scope

**Goal:** The system reads the market and *decides* — on replayed data, no orders.

## In-scope functional modules
| FM | Behaviour delivered this phase | Source module doc |
|----|--------------------------------|-------------------|
| FM-MarketData | Real/replayed market snapshot: spot, chain, IV/greeks, multi-TF OHLC | [01-market-data](../../modules/01-market-data/01-market-data.md) |
| FM-Regime | 7-family consensus → regime label + confidence | [02-regime](../../modules/02-regime/02-regime.md) |
| FM-Lifecycle | FSM emits open / morph / hold / exit decisions | [03-strategy-lifecycle](../../modules/03-strategy-lifecycle/03-strategy-lifecycle.md) |

Supporting (minimal): FM-InstrumentResolution, FM-Configuration (enough to feed data).

## Functional outcome of this phase
- A regime is genuinely classified from the seven indicator families (anti-bias ensemble).
- The lifecycle FSM produces real decisions and walks all transitions on replay.
- **Still no money, no orders** — decisions are observed, not acted on.

## Out of scope
- Building concrete legs (Phase 2), risk/execution (Phase 3), learning (Phase 5).

Refs: [../../FUNCTIONAL_DESIGN.md](../../FUNCTIONAL_DESIGN.md) §1–§3 (lifecycle, 7 families, regimes).
