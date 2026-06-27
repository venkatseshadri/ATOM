# ATOM — Module Design

**Companion to PROJECT_DOCUMENT.md** · Draft v0.1

This document lists ATOM's key modules, what each one does, the contract objects it
consumes and produces, and the dependency order for the Phase 0 skeleton. It stays
functional — no language, framework, or storage decisions here.

---

## 1. Pipeline Overview

One pass through the system, per cycle, per index:

```
  Market Data ──► Regime Classifier ──► Strategy Engine ──► Structure Builder
                                                                   │
                                                                   ▼
        Ledger ◄── Execution Adapter ◄── Risk Manager ◄───────────┘
            ▲                                                       │
            └────────────── Monitoring / Audit ◄────────────────────┘

  Orchestrator drives the cycle. Config feeds every module. Agentic Overlay
  (later, optional) sits read-only beside Strategy and Risk.
```

Data flows left→right; the Risk Manager is the last gate before any order leaves the
system. Every module also emits trace events to Monitoring.

---

## 2. Contract Objects (frozen in Phase 0)

These are the only things modules exchange. Field lists are functional, not final
schemas — they get pinned during Phase 0.

| Object            | Produced by         | Key fields (functional)                                                            |
|-------------------|---------------------|------------------------------------------------------------------------------------|
| `MarketSnapshot`  | Market Data         | index, timestamp, spot, option chain (strikes, CE/PE LTP, IV), multi-TF OHLC       |
| `RegimeState`     | Regime Classifier   | index, timestamp, regime ∈ {TREND_UP, TREND_DOWN, SIDEWAYS, REVERSAL}, confidence  |
| `StrategyDecision`| Strategy Engine     | intent ∈ {OPEN, MORPH_ADD, MORPH_CLOSE_LEG, HOLD, EXIT}, target structure, rationale|
| `StructurePlan`   | Structure Builder   | list of legs (right CE/PE, strike, action BUY/SELL, qty), net credit, max loss     |
| `RiskVerdict`     | Risk Manager        | approved bool, adjusted qty, breached rules, SL/TSL/TP levels                       |
| `OrderRequest`    | Execution Adapter   | per-leg broker-agnostic order (symbol, side, qty, type)                             |
| `Fill`            | Execution Adapter   | order id, leg, fill price, qty, status, timestamp                                  |
| `PositionState`   | Ledger              | current FSM state, open legs, entry prices, live P&L, realized P&L                  |
| `TraceEvent`      | every module        | source, type, payload, timestamp                                                   |

---

## 3. Modules

### 3.1 Market Data Provider
- **Does:** Supplies a `MarketSnapshot` each cycle — index spot, weekly option chain
  (strikes, CE/PE prices, IV), and multi-timeframe OHLC for regime detection.
- **In:** index, cycle tick. **Out:** `MarketSnapshot`.
- **Agnostic note:** later may be live capture, a replay file, or existing infra. The
  rest of the system never knows the source.

### 3.2 Regime Classifier
- **Does:** Reads `MarketSnapshot`, decides current regime — TREND_UP / TREND_DOWN /
  SIDEWAYS / REVERSAL — with a confidence. This is the trigger source for the strategy
  state machine (§4 of Project Document).
- **In:** `MarketSnapshot`. **Out:** `RegimeState`.
- **Open:** indicator set + timeframes that define trend vs. sideways vs. reversal.

### 3.3 Strategy Engine (Adaptive Theta State Machine)
- **Does:** Holds the live FSM (FLAT → SINGLE_SPREAD → IRON_FLY → RUNNER → FLAT).
  Combines `RegimeState` + current `PositionState` and emits a `StrategyDecision`
  (open with-trend spread, add opposing spread to morph into iron fly, close the
  threatened leg into a runner, hold, or exit).
- **In:** `RegimeState`, `PositionState`. **Out:** `StrategyDecision`.
- **Core of ATOM.** All lifecycle logic from Project Document §4 lives here.

### 3.4 Structure Builder / Strike Selector
- **Does:** Turns an abstract `StrategyDecision` into a concrete `StructurePlan` —
  picks strikes and wings for the weekly expiry, builds the leg list, computes net
  credit and max loss.
- **In:** `StrategyDecision`, `MarketSnapshot`. **Out:** `StructurePlan`.
- **Open:** strike selection rule (delta-band vs. fixed-distance vs. premium-target).

### 3.5 Risk Manager
- **Does:** Hard, deterministic gate. Checks `StructurePlan` against deploy size
  (₹2L), 10% DD floor, daily-loss cap, sizing, re-entry count; sets SL/TSL/TP levels;
  approves, resizes, or rejects. Also monitors open positions every cycle for SL/TSL/
  TP/EOD breaches and forces EXIT decisions.
- **In:** `StructurePlan`, `PositionState`. **Out:** `RiskVerdict`.
- **Rule:** never overridable by any advisory/LLM layer.

### 3.6 Execution Adapter
- **Does:** Broker-agnostic order interface. Converts approved `StructurePlan` legs
  into `OrderRequest`s, sends them, returns `Fill`s. Idempotent; handles partial fills.
- **In:** `StructurePlan` + `RiskVerdict`. **Out:** `Fill[]`.
- **Agnostic note:** real broker, paper simulator, or existing order layer behind one
  interface.

### 3.7 Ledger / State Store
- **Does:** Single source of truth for position state and P&L. Applies `Fill`s,
  maintains the FSM `PositionState`, computes live and realized P&L. Everything that
  needs "what do we hold right now" reads here.
- **In:** `Fill[]`. **Out:** `PositionState`.

### 3.8 Orchestrator / Session Manager
- **Does:** Drives the cycle loop per index; owns session lifecycle — entry windows,
  cycle cadence, and mandatory EOD square-off. Sequences the modules in pipeline order.
- **In:** clock, config. **Out:** cycle invocations.

### 3.9 Monitoring / Audit
- **Does:** Collects `TraceEvent`s from every module into a decision/audit trail. Lets
  the system explain every state transition and order. Console + structured event log.
- **In:** `TraceEvent`. **Out:** logs / traces.

### 3.10 Config
- **Does:** Single place for strategy params (regime thresholds, strike rules) and risk
  params (deploy size, DD floor, SL/TSL, re-entries, entry windows). Feeds every module.

### 3.11 Agentic Overlay *(later, optional — Phase 6+)*
- **Does:** Read-only advisory layer beside Strategy and Risk. May annotate decisions
  or surface context to the operator. **Cannot override risk.** Purely a technical
  implementation choice deferred out of the functional core.

---

## 4. Phase 0 Skeleton — Build Order

Phase 0 = every module above as a **stub**: it accepts its input contract, emits a
print/log line, and returns a hard-coded/passthrough output contract. Goal: prove the
full pipeline runs end-to-end and freeze the seams.

Build order follows the dependency chain:

1. **Config** — params object every module reads.
2. **Contract objects** (§2) — define all dataclasses/interfaces first.
3. **Monitoring/Audit** — so every later stub can emit traces.
4. **Market Data** stub — emits a canned `MarketSnapshot`.
5. **Regime Classifier** stub — returns a fixed `RegimeState`.
6. **Strategy Engine** stub — returns a fixed `StrategyDecision`, FSM scaffold present.
7. **Structure Builder** stub — returns a canned `StructurePlan`.
8. **Risk Manager** stub — approves with canned `RiskVerdict`.
9. **Execution Adapter** stub — returns canned `Fill`s.
10. **Ledger** stub — accumulates `Fill`s into a `PositionState`.
11. **Orchestrator** — wires 1–10 into one logged cycle loop.

**Exit criterion for Phase 0:** running the orchestrator prints one full pass:
`MarketSnapshot → RegimeState → StrategyDecision → StructurePlan → RiskVerdict →
Fill → PositionState`, with a trace line from each module, and the contracts are
frozen.

---

## 5. Module → Contract Matrix

| Module             | Consumes                          | Produces           |
|--------------------|-----------------------------------|--------------------|
| Market Data        | tick                              | `MarketSnapshot`   |
| Regime Classifier  | `MarketSnapshot`                  | `RegimeState`      |
| Strategy Engine    | `RegimeState`, `PositionState`    | `StrategyDecision` |
| Structure Builder  | `StrategyDecision`,`MarketSnapshot`| `StructurePlan`   |
| Risk Manager       | `StructurePlan`, `PositionState`  | `RiskVerdict`      |
| Execution Adapter  | `StructurePlan`, `RiskVerdict`    | `Fill[]`           |
| Ledger             | `Fill[]`                          | `PositionState`    |
| Orchestrator       | clock, config                     | cycle invocations  |
| Monitoring         | `TraceEvent` (all)                | logs               |
| Config             | —                                 | params             |
