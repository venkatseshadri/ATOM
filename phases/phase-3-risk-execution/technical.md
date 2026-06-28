# Phase 3 — Risk + Execution · Technical Scope

**Goal:** Modules 5, 6, 7, 11, 13 go REAL — full paper execution path under the risk gate.

## Modules + maturity
| # | Module | Maturity | Source |
|---|--------|----------|--------|
| 5 | Risk & Sizing | **REAL** (hard gate) | [05](../../modules/05-risk-sizing/05-risk-sizing.md) |
| 6 | Stop Management | **REAL** | [06](../../modules/06-stop-management/06-stop-management.md) |
| 7 | Session Lifecycle | **REAL** | [07](../../modules/07-session-lifecycle/07-session-lifecycle.md) |
| 11 | Connectivity & Auth | **REAL** (paper/broker session) | [11](../../modules/11-connectivity-auth/11-connectivity-auth.md) |
| 13 | Order/Execution | **REAL** (paper) | [13](../../modules/13-order-execution/13-order-execution.md) |

## Contracts exercised
Consumes: `StructurePlan`, `PositionState`, `Session`. Produces: `RiskVerdict`,
`OrderRequest`, `Fill`.

## Build order (within phase)
1. Connectivity & Auth → 2. Session Lifecycle → 3. Risk & Sizing → 4. Stop Management →
5. Order/Execution.

## Dependencies on prior phases
GATE 2 signed (concrete structures available to gate + execute).
