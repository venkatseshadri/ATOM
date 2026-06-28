# ATOM — Phases (execution view)

Seven build-phase folders (Phase 0–6). Each folder holds three docs scoped to that phase:
- **functional.md** — which functional behaviours (`FM-*`) this phase delivers
- **technical.md** — which technical modules go to which maturity, contracts, build order
- **testcases.md** — the phase's Definition of Done + acceptance tests (Given/When/Then) + Board gate

These are the **execution view** — curated, by-phase. They reference the authoritative
sources (no copied content): per-module detail in [`../modules/`](../modules/README.md),
the gated map in [`../BUILD_PLAN.md`](../BUILD_PLAN.md), and the design in
[`../FUNCTIONAL_DESIGN.md`](../FUNCTIONAL_DESIGN.md) / [`../TECHNICAL_DESIGN.md`](../TECHNICAL_DESIGN.md).

| Phase | Folder | Modules → REAL | Gate |
|-------|--------|----------------|------|
| 0 Skeleton | [phase-0-skeleton](phase-0-skeleton/) | all 1–16 → STUB | GATE 0 |
| 1 Regime + Signal | [phase-1-regime-signal](phase-1-regime-signal/) | 1, 2, 3 | GATE 1 |
| 2 Strike + Structure | [phase-2-strike-structure](phase-2-strike-structure/) | 4, 12 | GATE 2 |
| 3 Risk + Execution | [phase-3-risk-execution](phase-3-risk-execution/) | 5, 6, 7, 11, 13 | GATE 3 |
| 4 Ledger + Monitor | [phase-4-ledger-monitor](phase-4-ledger-monitor/) | 14, 15, 16 | GATE 4 |
| 5 Research Loop | [phase-5-research-loop](phase-5-research-loop/) | 8, 9, 10 | GATE 5 |
| 6 Validation | [phase-6-validation](phase-6-validation/) | all (measure) | GATE 6 GO/NO-GO |

**Phase 7 (Live)** is operational, not a doc folder — wire modules 11 & 13 to real capital
after GATE 6 GO. See BUILD_PLAN.md.

Each phase is complete only when its `testcases.md` DoD is fully checked and the Board signs
the gate. **Phase N+1 must not start until GATE N is signed.**
