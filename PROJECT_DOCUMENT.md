# ATOM — Adaptive Theta Options Machine

**Project Document (Functional Design)**
Status: Draft v0.1 · Owner: Board (venkatseshadri) · Builders: coding agents

---

## 1. Purpose

ATOM is an intraday options trading system for the Indian weekly-options market
(NIFTY and SENSEX). Its single job is to **harvest theta decay** while staying on
the right side of trend, using an **adaptive credit-spread structure** that morphs
as the market regime changes within the session.

This document is a **functional design**. It defines *what* the system does and the
*contracts between its parts*. It deliberately avoids implementation choices
(language, libraries, agentic vs. deterministic, reuse of existing infra). Those are
deferred to later phases and captured in the module document and per-phase specs.

---

## 2. Design Principles

1. **Implementation-agnostic.** The functional design must hold whether a module is
   later built as a pure rules engine, an LLM agent, or a wrapper over existing
   infrastructure on this box. Modules talk only through frozen contracts.
2. **Contracts before code.** Phase 0 freezes every inter-module data object. No
   module knows another module's internals — only its inputs and outputs.
3. **Skeleton first.** Phase 0 is a runnable skeleton: every module is a stub that
   emits print/log lines and passes the contract objects through the pipeline. This
   proves the wiring and the seams before any real logic exists.
4. **One position at a time.** ATOM manages a single live structure per index. No
   pyramiding beyond the defined morph lifecycle.
5. **Risk is non-negotiable and deterministic.** Stop-loss, trailing stop, daily-loss
   cap, and drawdown floor are hard rules. Any advisory/LLM layer is read-only over
   risk — it may inform, never override.
6. **Auditability.** Every state transition and every decision emits a trace. The
   system can explain *why* it did what it did.

---

## 3. Market & Instrument Scope

| Dimension        | Scope (v1)                                            |
|------------------|-------------------------------------------------------|
| Indices          | NIFTY, SENSEX (weekly expiries)                       |
| Instrument       | Index weekly options (CE/PE)                          |
| Horizon          | Intraday only — flat by end of session (EOD square-off)|
| Structure        | Credit spreads → iron fly → directional runner        |
| Session          | Regular market hours; entry windows configurable      |

---

## 4. Strategy — The Adaptive Theta Lifecycle

ATOM is a **state machine** over the live structure. It enters with-trend to collect
directional theta, morphs into a range structure when trend stalls, and sheds the
threatened leg when trend reverses — keeping the now-aligned spread as a trailing
runner.

### 4.1 States & Transitions

```
            ┌──────────────────────────────────────────────────────┐
            │                       FLAT                            │
            │                  (no position)                        │
            └───────────────┬──────────────────────────────────────┘
                            │  Regime = TREND (up/down)
                            ▼
            ┌──────────────────────────────────────────────────────┐
            │                  SINGLE_SPREAD                        │
            │  Uptrend   → Bull Put Spread   (sell PE / buy PE)     │
            │  Downtrend → Bear Call Spread  (sell CE / buy CE)     │
            │  → with-trend credit, theta + mild directional        │
            └──────┬─────────────────────────────┬──────────────────┘
                   │ Regime → SIDEWAYS            │ SL/TSL/TP/EOD
                   ▼                              ▼
            ┌──────────────────────────┐      ┌───────────┐
            │        IRON_FLY          │      │   FLAT    │
            │  add opposing credit     │      └───────────┘
            │  spread → range theta    │
            │  both sides              │
            └──────┬───────────────────┘
                   │ Regime → TREND reverses (opposite side)
                   ▼
            ┌──────────────────────────────────────────────────────┐
            │                     RUNNER                            │
            │  Close the original (now-threatened) spread.          │
            │  Keep the spread aligned with new trend.              │
            │  Manage with SL / trailing-SL until exit.             │
            └───────────────┬──────────────────────────────────────┘
                            │ SL/TSL/TP/EOD
                            ▼
                          FLAT
```

### 4.2 Transition Rules (functional)

| From          | Trigger                          | Action                                                                 | To            |
|---------------|----------------------------------|------------------------------------------------------------------------|---------------|
| FLAT          | Trend confirmed (up/down)        | Open with-trend credit spread (Bull Put if up, Bear Call if down)      | SINGLE_SPREAD |
| SINGLE_SPREAD | Regime turns sideways            | Add opposing credit spread → structure becomes iron fly/condor         | IRON_FLY      |
| SINGLE_SPREAD | SL / TSL / TP / EOD              | Close spread                                                           | FLAT          |
| IRON_FLY      | Trend resumes in opposite dir    | Close the threatened (original) spread; retain aligned spread          | RUNNER        |
| IRON_FLY      | SL / TSL / TP / EOD              | Close both spreads                                                     | FLAT          |
| RUNNER        | SL / TSL / TP / EOD              | Close remaining spread                                                 | FLAT          |

Notes:
- "With-trend credit spread" = the spread whose max-profit zone is *in the direction
  of, or above/below the current trend* so that trend + theta both pay (Bull Put for
  up, Bear Call for down).
- The **opposing spread** added at the sideways transition is the mirror leg that
  completes the iron fly, so the structure earns premium from both sides while the
  market ranges.
- On reversal, the leg that is now in the path of price is the **threatened** leg and
  is closed; the surviving leg is already aligned with the new trend and becomes the
  runner.

### 4.3 Open Questions for the strategy (to resolve in review)
- Exact regime-classification inputs (which indicators / timeframes confirm
  trend vs. sideways vs. reversal). Placeholder: multi-TF trend + range filter.
- Strike/wing selection rule for weekly spreads (delta-based vs. fixed-distance vs.
  premium-target). Placeholder: delta-band selection.
- Whether the runner re-arms a new structure after exit, or stays flat to next signal.

---

## 5. Risk Framework

ATOM inherits the existing house risk discipline (same parameters as current iron-fly
trading on this desk):

- **Deploy size:** ₹2,00,000 per active structure.
- **Drawdown floor:** 10% — hard stop on structure / day.
- **Stop-loss & trailing-SL:** per-structure SL; TSL activates and trails per existing
  rules; protective on every state including RUNNER.
- **Re-entries:** up to 2 per session (existing rule).
- **Sizing:** lot count derived from deploy size and contract margin; prefer
  MINI/MICRO contracts where available to fit liquidity.
- **EOD square-off:** mandatory — no overnight risk.

Risk Manager is a **hard, deterministic gate**. No advisory layer can loosen it.

---

## 6. Phased Delivery

| Phase | Name              | Deliverable                                                                 |
|-------|-------------------|-----------------------------------------------------------------------------|
| **0** | Skeleton          | Every module is a stub: prints/logs, passes contract objects end-to-end. Wiring + contracts frozen. No real logic. Runnable. |
| 1     | Regime + Signal   | Real regime classifier + strategy state machine producing decisions (dry).  |
| 2     | Strike + Structure| Strike/wing selection; structure builder emits concrete leg orders (paper). |
| 3     | Risk + Execution  | Hard risk gate live; broker-agnostic execution adapter; paper fills.        |
| 4     | Ledger + Monitor  | Position truth store, P&L, decision/audit traces, EOD square-off.           |
| 5     | Validation        | Backtest/paper expectancy proof. **No live money until expectancy proven.** |
| 6+    | Live / Agentic    | Live broker wiring; optional agentic advisory overlay (read-only on risk).  |

The module-build sequence within phases is driven by the contract dependency graph in
the module document.

---

## 7. Success Criteria

- Phase 0: full pipeline runs end-to-end on stubs; every contract object flows from
  data → regime → strategy → structure → risk → execution → ledger and is logged.
- Strategy: state machine demonstrably executes all transitions in §4.1 on replayed
  data.
- Risk: no path can exceed deploy size, DD floor, or skip EOD square-off.
- Go-live gate: positive, validated expectancy on paper/backtest before any real
  capital (mirrors house go-live discipline).

---

## 8. Non-Goals (v1)

- Positional / overnight options.
- Equities, futures, or non-index options.
- More than one live structure per index simultaneously.
- Any live-money trading before Phase 5 validation passes.

---

## 9. Requirements Catalog (traceability IDs)

Stable IDs for every requirement, so design, code, and tests can trace back to a need.
Functional modules and technical modules map onto these in MODULE_DESIGN.md §7.

### Strategy (FR)
| ID    | Requirement                                                                 |
|-------|-----------------------------------------------------------------------------|
| FR-1  | Classify regime: TREND_UP / TREND_DOWN / SIDEWAYS / REVERSAL                 |
| FR-2  | On confirmed trend, open with-trend credit spread (Bull Put up / Bear Call down) |
| FR-3  | On turn to sideways, add opposing credit spread → morph to iron fly          |
| FR-4  | On reversal, close threatened original spread; retain aligned spread as runner |
| FR-5  | Select weekly-expiry strikes and wings for each structure                   |
| FR-6  | Manage exactly one live structure per index                                 |

### Risk (RR)
| ID    | Requirement                                                                 |
|-------|-----------------------------------------------------------------------------|
| RR-1  | Cap deploy at ₹2,00,000 per structure                                       |
| RR-2  | 10% drawdown floor — hard stop on structure/day                             |
| RR-3  | Per-structure SL and trailing-SL, active in every state incl. RUNNER        |
| RR-4  | Daily-loss cap; max 2 re-entries per session                                |
| RR-5  | Mandatory EOD square-off — no overnight risk                                |
| RR-6  | Risk gate is deterministic and non-overridable by any advisory/LLM layer    |

### Platform (PR)
| ID    | Requirement                                                                 |
|-------|-----------------------------------------------------------------------------|
| PR-1  | Broker authentication and session lifecycle (login, keep-alive, reconnect)  |
| PR-2  | Instrument resolution: strike ladder, lot size, correct tradingsymbol format |
| PR-3  | Market-data snapshot: spot, option chain (CE/PE LTP, IV), multi-TF OHLC      |
| PR-4  | Order place / modify / cancel with fills; idempotent, partial-fill safe      |
| PR-5  | Position and P&L truth store (single source)                                |
| PR-6  | Audit trace of every decision and state transition                          |
| PR-7  | Central configuration of all strategy and risk params                       |
| PR-8  | Session/cycle orchestration: entry windows, cadence, EOD square-off          |

### Design constraints (NFR)
| ID    | Constraint                                                                  |
|-------|-----------------------------------------------------------------------------|
| NFR-1 | Implementation-agnostic functional design (no stack/broker/agentic lock-in) |
| NFR-2 | Contracts frozen before logic (Phase 0)                                     |
| NFR-3 | Skeleton-first: runnable stub pipeline before any real module              |

---

## 10. Glossary

- **Theta** — option time decay; ATOM's primary profit source.
- **Credit spread** — sell nearer option, buy farther same-type option; net premium received.
- **Bull Put Spread** — sell higher-strike PE, buy lower-strike PE; profits if market up/flat.
- **Bear Call Spread** — sell lower-strike CE, buy higher-strike CE; profits if market down/flat.
- **Iron Fly / Condor** — both a put spread and a call spread; range-bound theta structure.
- **Runner** — the single surviving spread after a reversal, managed with SL/TSL.
- **Regime** — current market state: TREND_UP / TREND_DOWN / SIDEWAYS / REVERSAL.
