# ATOM — Build Plan (gated phase execution)

**The execution map.** Ties phases → modules → maturity → functional/requirement mapping
→ **Definition of Done (DoD)** → **Board approval gate**. An implementer (or the OUROBOROS
build loop) works one phase at a time; a phase is "done" only when its DoD checklist
passes and the Board signs the gate. **Phase N+1 must not start until GATE N is approved.**

## How the documents relate

| Layer | Doc | Answers |
|-------|-----|---------|
| Charter + schedule | `PROJECT_DOCUMENT.md` | why, scope, phase sequence, requirements (FR/RR/PR/LR/NFR) |
| Trading spec | `FUNCTIONAL_DESIGN.md` | what a good trader does (regime, greeks, lifecycle) — functional modules `FM-*` |
| Architecture | `TECHNICAL_DESIGN.md` | buildable technical modules, contracts, two-loop, RTM |
| **Bridge / build unit** | `modules/NN-*/NN-*.md` | per-module behavioral decomposition + line items `NN.x` + Given/When/Then tests |
| **Execution** | `BUILD_PLAN.md` (this) | which modules in which phase, DoD, gates |

**Build unit = a module's line items.** Within `modules/NN-*.md`, each `NN.x[.y]` node is a
work ticket; its **Functional Test Case (Given/When/Then)** is the ticket's acceptance
test. A module is "done for a phase" when its in-scope line items pass at the phase's
required maturity.

**Maturity levels:** `STUB` (contract in/out + trace, no logic) → `REAL` (logic implemented,
tested) → `LIVE` (wired to broker/real money).

---

## Phase → Module map

| Phase | Goal | Modules in scope (maturity) | Functional (`FM-*`) | Requirements |
|-------|------|------------------------------|---------------------|--------------|
| **0 Skeleton** | Freeze contracts; stub all | **ALL 1–16 → STUB** | all | NFR-1,2,3 |
| **1 Regime + Signal** | Read market, classify, decide (dry) | 1,2,3 → REAL; 12,16 → minimal REAL; 15 → STUB+ | FM-MarketData, FM-Regime, FM-Lifecycle | FR-1,2,3,4,6; PR-3 |
| **2 Strike + Structure** | Decisions → concrete weekly legs (paper) | 4 → REAL; 12 → REAL | FM-StructureSelection, FM-InstrumentResolution | FR-5; PR-2 |
| **3 Risk + Execution** | Hard gate + stops + order placement (paper) | 5,6,13,7 → REAL; 11 → REAL | FM-RiskControl, FM-StopManagement, FM-Execution, FM-SessionLifecycle, FM-Connectivity | RR-1..6; PR-1,4,8 |
| **4 Ledger + Monitor** | Position truth, P&L, audit, config | 14,15 → REAL; 16 → REAL | FM-Bookkeeping, FM-Audit, FM-Configuration | PR-5,6,7 |
| **5 Research Loop** | Offline learning → ParameterSet (gated) | 8,9,10 → REAL | FM-PostMortem, FM-Optimization, FM-FeedbackGate | LR-1..5; NFR-4 |
| **6 Validation** | Prove expectancy on paper/backtest | (no new modules) | — | go-live blocker |
| **7 Live** | Wire real capital | 11,13 → LIVE | — | post-validation |

> Modules appear in more than one phase by maturity (e.g. 12 minimal in P1, full in P2; 16
> minimal in P1, full in P4). A later phase may harden an earlier module — note it in that
> phase's DoD.

---

## Per-phase Definition of Done + Gate

Check every box, then the Board signs the gate. Unchecked = phase not done.

### Phase 0 — Skeleton  ☐ GATE 0
- [ ] Every contract object (`TECHNICAL_DESIGN.md §2`) defined and frozen.
- [ ] All 16 modules exist as STUB: accept input contract, emit a trace, return canned output.
- [ ] Orchestrator runs one full pass end-to-end: `Session → Instrument → MarketSnapshot →
      RegimeState → StrategyDecision → StructurePlan → RiskVerdict → Fill → PositionState`.
- [ ] Each module emits a trace line; pipeline is runnable and logged.
- [ ] Stub test suite green in CI.
- **Gate 0 (Board):** approve frozen contracts + skeleton. → unlocks Phase 1.

### Phase 1 — Regime + Signal  ✅ GATE 1
- [ ] Module 1 supplies a real/replayed `MarketSnapshot` (spot, chain, IV/greeks, multi-TF OHLC).
- [ ] Module 2 produces a regime label + confidence from the **7 indicator families** (line items 2.x pass).
- [ ] Module 3 FSM emits open/morph/hold/exit decisions; all transition test cases pass.
- [ ] Runs on replayed data, **no orders placed**.
- [ ] Line-item Given/When/Then tests for modules 1,2,3 pass.
- **Gate 1 (Board):** review regime calls + decisions on replay. → unlocks Phase 2.

### Phase 2 — Strike + Structure  ✅ GATE 2
- [x] Module 12 resolves weekly expiry, strike ladder, lot/tick, correct per-index tradingsymbol.
- [x] Module 4 turns a decision into concrete legs (strike/wing/qty/price), distance-method
      (greek-driven N/A — no per-strike greeks in Penguin data; see PHASE-2-TECHNICAL.md), paper only.
- [x] Construction + instrument line-item tests pass (incl. NIFTY vs SENSEX symbol formats) — 12/12, `tests/test_phase2.py`.
- **Gate 2 (Board):** review generated structures for sanity. → unlocks Phase 3.

### Phase 3 — Risk + Execution  ☐ GATE 3
- [x] Module 5 enforces deploy cap, DD floor, daily-loss, re-entries, sizing — **property tests prove no path breaches** (500-iteration property test, `test_risk.py`).
- [x] Module 6 sets and trails SL/TSL/TP; breach raises exit (ratchet-proven never-loosen, `test_stop_management.py`).
- [x] Module 11 maintains session awareness — reads antariksh's shared broker session
      (read-only heartbeat health check) rather than an independent login (collision risk
      with the live feed session, see PHASE-3-TECHNICAL.md); reconnect is antariksh's
      responsibility, ATOM just detects staleness.
- [x] Module 13 places orders and captures fills (paper, real chain data); partial/reject
      handled. Modify/cancel flows (§13.4/13.5) and live broker reconciliation (§13.9)
      deferred — no live broker round-trip exists yet.
- [x] Module 7 enforces entry windows + mandatory EOD square-off (escalation ladder + flat confirmation).
- [x] Risk gate is non-overridable (RR-6) — test proves it (`test_no_force_field_exists_to_honor`).
- [x] Full lifecycle proven end-to-end (`test_phase3_integration.py`) — not yet wired into
      the live cron path (see PHASE-3-TECHNICAL.md "Not done").
- **Gate 3 (Board):** review full paper trade lifecycle + risk invariants. → unlocks Phase 4.

### Phase 4 — Ledger + Monitor  ☐ GATE 4
- [ ] Module 14 is single source of truth: applies fills, correct live + realized P&L, restart recovery.
- [ ] Module 15 audit trail reconstructs any trade end-to-end.
- [ ] Module 16 serves the day's frozen config/ParameterSet.
- [ ] Ledger reconciles against execution; no split-brain.
- **Gate 4 (Board):** review P&L accuracy + audit reconstruction. → unlocks Phase 5.

### Phase 5 — Research Loop  ☐ GATE 5
- [ ] Module 8 produces trade/session/regime post-mortem scores.
- [ ] Module 9 emits a candidate `ParameterSet` optimizing drawdown-adjusted PnL + survival, with rationale.
- [ ] Module 10 runs EOD safety + PORCUPINE backtest + morning human approval before a set goes live.
- [ ] Proven: **AI never in the trading loop** (NFR-4) — research writes cache only, risk-gated.
- **Gate 5 (Board):** review a full learn→propose→approve cycle. → unlocks Phase 6.

### Phase 6 — Validation  ☐ GATE 6 (GO/NO-GO)
- [ ] Positive **drawdown-adjusted expectancy** on paper/backtest over the agreed window.
- [ ] PORCUPINE harness green across scenario catalogue.
- [ ] Promotion bar met (1–2 days successful simulated PnL, LR-5).
- **Gate 6 (Board): GO/NO-GO for real capital.** → unlocks Phase 7.

### Phase 7 — Live  ☐ GATE 7
- [ ] Live broker wiring; shadow run matches paper.
- [ ] Small-size live, monitored; rollback path proven.
- **Gate 7 (Board):** approve scale-up.

---

## Current status

**Phase 0 skeleton BUILT** (Python, `src/atom/`) — awaiting GATE 0 sign-off.
- Frozen contracts: `src/atom/contracts.py` (immutable dataclasses, incl. `AccountState`
  for gap G1, `ParameterSet` canonical for gap G3).
- 16 module stubs: `src/atom/modules/` (one file per module).
- Orchestrator: `src/atom/orchestrator.py` — one full pass, 16/16 modules emit a trace.
- Run it: `python3 run_phase0.py`. Tests: `python3 -m pytest` → **5 passed** (T0.1–T0.3).

Seam reconciliation is **done** ([SEAM_RECONCILIATION.md](SEAM_RECONCILIATION.md)); gaps
G1–G4 there still need a Board ruling before their REAL phase (G1→P3, G2→P5, G4→P4; G3
already standardized on `ParameterSet`).

**Next:** Board signs GATE 0 (review contracts + skeleton) → Phase 1 begins.

---

**Update 2026-07-05:** GATE 0, GATE 1, GATE 2 signed (see GATES.md). Phase 3 built — see
[PHASE-3-TECHNICAL.md](progress/PHASE-3-TECHNICAL.md). **Next:** Board reviews Phase 3 →
signs GATE 3 → Phase 4 (Ledger + Monitor) begins.
