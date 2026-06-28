# Phase 4 — Ledger + Monitor · Functional Scope

**Goal:** Know the truth — positions, P&L, a full audit trail, and the day's config.

## In-scope functional modules
| FM | Behaviour delivered this phase | Source module doc |
|----|--------------------------------|-------------------|
| FM-Bookkeeping | Single source of truth: positions + live/realized P&L | [14-ledger-persistence](../../modules/14-ledger-persistence/14-ledger-persistence.md) |
| FM-Audit | Decision/transition trace; reconstruct any trade | [15-telemetry-audit](../../modules/15-telemetry-audit/15-telemetry-audit.md) |
| FM-Configuration | Serve the day's frozen config/ParameterSet | [16-config-parameterset](../../modules/16-config-parameterset/16-config-parameterset.md) |

## Functional outcome of this phase
- P&L is correct and reconciled against execution; no split-brain.
- Any trade's full lifecycle can be reconstructed from the audit trail.
- The system runs a versioned, frozen parameter set for the session.

## Out of scope
- The learning loop that *produces* parameter sets (Phase 5).

Refs: [../../TECHNICAL_DESIGN.md](../../TECHNICAL_DESIGN.md) (contracts, two-loop).
