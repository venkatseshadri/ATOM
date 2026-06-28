# Phase 0 — Skeleton · Functional Scope

**Goal:** No trading behaviour yet. Establish the *shape* of the system — every module
present as a stub — so the contracts and the pipeline are proven before any logic.

## In-scope functional modules
**All** functional modules appear, but only as pass-through stubs (no real behaviour).

| FM | Phase-0 behaviour | Source module doc |
|----|-------------------|-------------------|
| all FM-* | stubbed: accept input, emit a trace, return canned output | [../../modules/README.md](../../modules/README.md) |

## Functional outcome of this phase
- The end-to-end *path* a trade would take exists and is runnable.
- No regime is really classified, no structure really chosen, no order really placed.

## Out of scope (later phases)
- Any real regime / strategy / risk / execution / learning logic (Phases 1–5).

See [../../FUNCTIONAL_DESIGN.md](../../FUNCTIONAL_DESIGN.md) for the full trading spec and
[../../BUILD_PLAN.md](../../BUILD_PLAN.md) for the gate.
