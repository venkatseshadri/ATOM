# ATOM — Adaptive Theta Options Machine

Intraday weekly-options system for NIFTY / SENSEX that harvests theta decay via an
adaptive credit-spread structure that morphs with the market regime
(spread → iron fly → runner).

Design is **functional and implementation-agnostic** — built later by coding agents in
phases (Phase 0 = runnable skeleton, contracts frozen).

## Documents

| Doc | Scope |
|-----|-------|
| **[PROJECT_DOCUMENT.md](PROJECT_DOCUMENT.md)** | Charter — purpose, scope, principles, phases, risk, requirements catalog (FR/RR/PR/NFR). |
| **[FUNCTIONAL_DESIGN.md](FUNCTIONAL_DESIGN.md)** | *What & why* — strategy lifecycle (state + sequence diagram), functional modules. |
| **[TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md)** | *How* — technical modules, contracts, Phase 0 skeleton, requirements traceability matrix. |
| **[BUILD_PLAN.md](BUILD_PLAN.md)** | *When* — gated phase execution: phase → modules → Definition of Done → Board gate. |
| **[modules/](modules/README.md)** | Per-module behavioral decomposition (the build unit): line items + Given/When/Then tests. |
| **[phases/](phases/README.md)** | Execution view — 7 phase folders (0–6), each with functional / technical / testcases docs + Board gate. |

Requirements (PROJECT_DOCUMENT) → functional modules (FUNCTIONAL_DESIGN) → technical
modules (TECHNICAL_DESIGN) are linked by the RTM in TECHNICAL_DESIGN.md §7.
