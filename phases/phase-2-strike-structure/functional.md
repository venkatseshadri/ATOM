# Phase 2 — Strike + Structure · Functional Scope

**Goal:** Turn an abstract decision into a concrete weekly option structure (paper).

## In-scope functional modules
| FM | Behaviour delivered this phase | Source module doc |
|----|--------------------------------|-------------------|
| FM-StructureSelection | Greek-driven choice of strikes/wings; build the leg list | [04-trade-construction](../../modules/04-trade-construction/04-trade-construction.md) |
| FM-InstrumentResolution | Weekly expiry, strike ladder, lots, correct tradingsymbol | [12-instrument-symbol](../../modules/12-instrument-symbol/12-instrument-symbol.md) |

## Functional outcome of this phase
- A decision (open/morph) becomes a concrete `StructurePlan`: real legs with valid
  contracts, strikes, wings, quantity and price/order-type — on paper.
- Correct per-index symbol handling (NIFTY vs SENSEX formats).

## Out of scope
- Risk gating + actual order placement (Phase 3).

Refs: [../../FUNCTIONAL_DESIGN.md](../../FUNCTIONAL_DESIGN.md) §4 (greeks), §5 (structure/strike).
