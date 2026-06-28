# Phase 3 — Risk + Execution · Acceptance / Test Cases (DoD)

## Definition of Done (checklist) — sign all before GATE 3
- [ ] Module 5 enforces deploy cap, DD floor, daily-loss, re-entries, sizing — **property tests prove no path breaches**.
- [ ] Module 6 sets and trails SL/TSL/TP; breach raises exit.
- [ ] Module 11 maintains an authenticated session; reconnect handled.
- [ ] Module 13 places/cancels orders and captures fills (paper); partial/reject handled.
- [ ] Module 7 enforces entry windows + mandatory EOD square-off.
- [ ] Risk gate is non-overridable (RR-6) — test proves it.

## Phase acceptance tests (Given / When / Then)
- **T3.1 Deploy cap** — *Given* a plan exceeding ₹2L; *When* gated; *Then* it is resized or rejected.
- **T3.2 DD floor** — *Given* day drawdown at the 10% floor; *When* a new entry is attempted;
  *Then* it is blocked.
- **T3.3 Risk invariant (property)** — *Given* any random plan/account state; *Then* no
  approved path exceeds deploy, DD, daily-loss, or re-entry limits.
- **T3.4 TSL ratchet** — *Given* a position moving favourably then back; *Then* the trailed
  stop never loosens and triggers an exit on breach.
- **T3.5 EOD square-off** — *Given* an open position at the cutoff; *Then* a forced
  square-off order is issued and the book is flat.
- **T3.6 Partial fill** — *Given* one leg partially fills; *Then* the system reconciles, no
  unhedged naked exposure is left silently.
- **T3.7 Non-override** — *Given* an advisory input; *When* it contradicts the gate; *Then*
  the gate's decision stands.

## Detailed line-item tests
Modules [05](../../modules/05-risk-sizing/05-risk-sizing.md),
[06](../../modules/06-stop-management/06-stop-management.md),
[07](../../modules/07-session-lifecycle/07-session-lifecycle.md),
[11](../../modules/11-connectivity-auth/11-connectivity-auth.md),
[13](../../modules/13-order-execution/13-order-execution.md).

## Board Gate 3
Board reviews full paper-trade lifecycle + risk invariants. **Sign-off unlocks Phase 4.**
