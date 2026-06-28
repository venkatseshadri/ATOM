# Phase 2 — Strike + Structure · Acceptance / Test Cases (DoD)

## Definition of Done (checklist) — sign all before GATE 2
- [ ] Module 12 resolves weekly expiry, strike ladder, lot/tick, correct per-index tradingsymbol.
- [ ] Module 4 turns a decision into concrete legs (strike/wing/qty/price), greek-driven, paper only.
- [ ] Construction + instrument line-item tests pass (incl. NIFTY vs SENSEX symbol formats).

## Phase acceptance tests (Given / When / Then)
- **T2.1 Expiry resolution** — *Given* a date near a holiday-shifted expiry; *When* expiry is
  resolved; *Then* the correct weekly expiry is chosen.
- **T2.2 Symbol format** — *Given* NIFTY and SENSEX targets; *When* tradingsymbols are built;
  *Then* each matches its exchange's format and resolves to a real contract.
- **T2.3 Greek-driven strikes** — *Given* an OPEN decision + a chain with greeks; *When* the
  structure is built; *Then* short/wing strikes fall in the configured delta bands.
- **T2.4 Plan completeness** — *Then* the `StructurePlan` has all legs, net credit, max loss.
- **T2.5 Liquidity guard** — *Given* an illiquid strike; *Then* it is rejected/adjusted, not used.

## Detailed line-item tests
Modules [04](../../modules/04-trade-construction/04-trade-construction.md),
[12](../../modules/12-instrument-symbol/12-instrument-symbol.md).

## Board Gate 2
Board reviews generated structures for sanity. **Sign-off unlocks Phase 3.**
