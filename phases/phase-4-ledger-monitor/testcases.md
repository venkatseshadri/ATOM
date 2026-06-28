# Phase 4 — Ledger + Monitor · Acceptance / Test Cases (DoD)

## Definition of Done (checklist) — sign all before GATE 4
- [ ] Module 14 is single source of truth: applies fills, correct live + realized P&L, restart recovery.
- [ ] Module 15 audit trail reconstructs any trade end-to-end.
- [ ] Module 16 serves the day's frozen config/ParameterSet.
- [ ] Ledger reconciles against execution; no split-brain.

## Phase acceptance tests (Given / When / Then)
- **T4.1 P&L accuracy** — *Given* a known sequence of fills + marks; *Then* live and realized
  P&L match a hand-computed expected value.
- **T4.2 Restart recovery** — *Given* an open position; *When* the process restarts; *Then*
  position state and P&L are recovered intact.
- **T4.3 Audit reconstruction** — *Given* a completed trade; *When* its trace is queried;
  *Then* the full lifecycle (decision→structure→risk→fills→exit) is reconstructable.
- **T4.4 Frozen config** — *Given* an approved ParameterSet; *When* served mid-session;
  *Then* it is immutable for the day.
- **T4.5 Reconciliation** — *Given* ledger vs broker positions; *When* they diverge; *Then*
  the divergence is detected and flagged.

## Detailed line-item tests
Modules [14](../../modules/14-ledger-persistence/14-ledger-persistence.md),
[15](../../modules/15-telemetry-audit/15-telemetry-audit.md),
[16](../../modules/16-config-parameterset/16-config-parameterset.md).

## Board Gate 4
Board reviews P&L accuracy + audit reconstruction. **Sign-off unlocks Phase 5.**
