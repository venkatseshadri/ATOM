# ATOM — Progress Dashboard

Single place to see where ATOM stands. Updated each milestone.

- **[CHANGELOG.md](CHANGELOG.md)** — chronological history of every iteration.
- **[PHASE-0-TECHNICAL.md](PHASE-0-TECHNICAL.md)** — full technical detail of the built skeleton.
- **[GATES.md](GATES.md)** — Board gate sign-off tracker.

---

## Current status — `2026-06-28`

| | |
|---|---|
| **Stage** | Phase 0 skeleton **built**, awaiting **GATE 0** sign-off |
| **Code** | Python / stdlib, `src/atom/` — 22 files |
| **Tests** | `python3 -m pytest` → **5 passed** (T0.1–T0.3) |
| **Run** | `python3 run_phase0.py` → one full pass, **16/16 modules trace** |
| **No live money** | correct — design + skeleton only |

## Phase ladder

| Phase | State | Gate |
|-------|-------|------|
| 0 Skeleton | ✅ built, awaiting sign-off | ⏳ GATE 0 |
| 1 Regime + Signal | ⬜ not started | — |
| 2 Strike + Structure | ⬜ | — |
| 3 Risk + Execution | ⬜ | — |
| 4 Ledger + Monitor | ⬜ | — |
| 5 Research Loop | ⬜ | — |
| 6 Validation | ⬜ | — |
| 7 Live | ⬜ | — |

## Document map

| Area | Doc |
|------|-----|
| Why + schedule | `../PROJECT_DOCUMENT.md` |
| Trading spec | `../FUNCTIONAL_DESIGN.md` |
| Architecture | `../TECHNICAL_DESIGN.md` |
| Gated execution | `../BUILD_PLAN.md` |
| Boundary rulings | `../SEAM_RECONCILIATION.md` |
| Per-module detail (16) | `../modules/README.md` |
| Per-phase views (7) | `../phases/README.md` |
| Built code | `../src/atom/` |

## Open items (need Board ruling)

- **GATE 0** — review contracts + skeleton, then sign to unlock Phase 1.
- **Gaps G1–G4** (`../SEAM_RECONCILIATION.md` §2): G1 account-state provider (→P3),
  G2 research trigger (→P5), G4 MTM feed (→P4). G3 resolved (`ParameterSet` canonical).
- **Build mode** — Phase 0 built inline; decide inline vs OUROBOROS loop for Phase 1+.
