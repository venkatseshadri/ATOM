# Phase 3 — Risk + Execution · Functional Scope

**Goal:** Gate every structure through hard risk, place it (paper), and manage stops +
the session.

## In-scope functional modules
| FM | Behaviour delivered this phase | Source module doc |
|----|--------------------------------|-------------------|
| FM-RiskControl | Deploy cap, DD floor, daily-loss, re-entries, sizing — hard gate | [05-risk-sizing](../../modules/05-risk-sizing/05-risk-sizing.md) |
| FM-StopManagement | SL / TSL / TP set + trail; breach → exit | [06-stop-management](../../modules/06-stop-management/06-stop-management.md) |
| FM-SessionLifecycle | Entry windows, cadence, mandatory EOD square-off | [07-session-lifecycle](../../modules/07-session-lifecycle/07-session-lifecycle.md) |
| FM-Connectivity | Broker auth + session lifecycle | [11-connectivity-auth](../../modules/11-connectivity-auth/11-connectivity-auth.md) |
| FM-Execution | Place/modify/cancel orders, capture fills (paper) | [13-order-execution](../../modules/13-order-execution/13-order-execution.md) |

## Functional outcome of this phase
- A complete **paper trade lifecycle**: decision → structure → risk-approved → placed →
  stops managed → squared off at EOD.
- Risk gate is **deterministic and non-overridable** (RR-6).

## Out of scope
- Position truth/P&L store + audit (Phase 4); learning (Phase 5); real money (Phase 7).

Refs: [../../PROJECT_DOCUMENT.md](../../PROJECT_DOCUMENT.md) §5 (risk framework).
