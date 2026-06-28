# ATOM — Changelog

Chronological history. Newest first.

## v1.1 — Phase 0 skeleton (first code)
- Python/stdlib skeleton under `src/atom/`: 15 frozen contract dataclasses, 16 module
  stubs (1 file each), orchestrator running one full pass (16/16 modules trace).
- Phase 0 tests T0.1 (pipeline) / T0.2 (frozen-contract conformance) / T0.3 (no-logic
  guard) → 5 passed.
- Confirmed gaps G1–G4 don't block Phase 0. Awaiting GATE 0.
- Stack chosen: Python + stdlib + pytest, `src/atom/` layout.
- commit `070eebb`.

## v1.0 — Seam reconciliation
- `SEAM_RECONCILIATION.md`: 17 boundary rulings (A–Q) over the isolated module docs +
  4 open gaps (G1–G4). Module docs stay discovery; seam doc governs.
- commit `8b48d68`.

## v0.9 — Phase execution view
- `phases/` — 7 phase folders (0–6), each with functional / technical / testcases docs
  (curated views referencing modules/ + BUILD_PLAN). Phase 7 Live = operational.
- commit `55f0894`.

## v0.8 — Gated execution map
- `BUILD_PLAN.md`: phase → modules (maturity STUB/REAL/LIVE) → FM-* → requirements →
  Definition of Done → Board gate. Module→phase index. Clarified `modules/` as the bridge
  build unit.
- commit `7bd7a7f`.

## v0.7 — Module decompositions
- 16 per-module discovery docs (`modules/`), each by an isolated analyst agent seeing
  only its module + market arena. ~400 leaf nodes; each with Responsibility / Behavior /
  Scenarios / Given-When-Then test / Outcome. Market scenarios parked under Suggestions.
- commit `713d37c`.

## v0.6 — Learning loop
- FUNCTIONAL §7: post-mortem (3 granularities), objective = drawdown-adjusted PnL +
  survival, optimization scope (risk params as overnight proposals), connect-back = daily
  approved ParameterSet via morning human gate; promotion = backtest + 1–2 days PnL.
- PROJECT: LR-1..5 + NFR-4. TECHNICAL: ParameterSet contract, two-loop, RTM LR rows.
- commit `f47e7aa`.

## v0.5 — Three-doc rescope
- PROJECT = high-level + dependency-sequenced phases (no dates) + OUROBOROS build approach.
- FUNCTIONAL = trading spec: 7 indicator families (anti-bias), greeks framework,
  qualitative regimes — values left open (‹TBD›).
- TECHNICAL = two-loop architecture (AI out of hot path), build feedback loop, test
  strategy, implementation flowchart.
- commit `5deff0d`.

## v0.4 — Split functional vs technical
- Separated FUNCTIONAL_DESIGN.md (what) and TECHNICAL_DESIGN.md (how); added the
  lifecycle mermaid sequence diagram; README index.
- commit `be0cdc8`.

## v0.3 — Requirements + traceability
- Requirements catalog (FR/RR/PR/NFR) with stable IDs; functional modules; RTM
  (requirement → functional → technical).
- commit `c9d2fb2`.

## v0.2 — Technical module catalog
- Reframed functional concepts into buildable technical modules; added Auth & Session and
  Instrument & Symbol Master; SL/TSL made an explicit Risk sub-engine.
- commit `5babf61`.

## v0.1 — Initial design
- Project + module functional design. Adaptive theta lifecycle FSM
  (FLAT→SINGLE_SPREAD→IRON_FLY→RUNNER). Public repo created.
- commit `b9431a8`.
