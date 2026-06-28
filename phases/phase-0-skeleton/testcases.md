# Phase 0 — Skeleton · Acceptance / Test Cases (DoD)

## Definition of Done (checklist) — sign all before GATE 0
- [ ] Every contract object (TECHNICAL_DESIGN §2) defined and **frozen**.
- [ ] All 16 modules exist as STUB: accept input contract, emit a trace, return canned output.
- [ ] Orchestrator runs one full pass end-to-end:
      `Session → Instrument → MarketSnapshot → RegimeState → StrategyDecision →
      StructurePlan → RiskVerdict → Fill → PositionState`.
- [ ] Each module emits a trace line; pipeline runnable and logged.
- [ ] Stub test suite green in CI.

## Phase acceptance tests (Given / When / Then)
- **T0.1 Pipeline pass** — *Given* all stubs wired; *When* the orchestrator runs one cycle;
  *Then* every contract object is produced in order and one trace per module is logged.
- **T0.2 Contract conformance** — *Given* the frozen contract schemas; *When* each stub
  returns its output; *Then* the shape matches the schema (no missing/extra fields).
- **T0.3 No-logic guard** — *Given* a stub; *When* called twice with different inputs;
  *Then* it returns its canned output (proves wiring, not behaviour).

## Detailed line-item tests
Per-module Given/When/Then for every leaf are in each `modules/NN-*/NN-*.md`. In Phase 0
only the *contract/stub* level applies; real per-leaf tests activate in the module's REAL phase.

## Board Gate 0
Board reviews frozen contracts + skeleton pass. **Sign-off unlocks Phase 1.**
