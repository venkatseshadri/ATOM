# ATOM — Seam Reconciliation

The 16 module docs in `modules/` were written by **isolated** analysts (each saw only its
own module). They therefore overlap at boundaries and leave a few gaps. This document is
the **authoritative boundary ruling**: where a module doc claims something this doc assigns
elsewhere, **this doc wins**. Build to these boundaries.

Convention used below: **OWNS** = the single source of truth / decision authority.
**CONSUMES** = uses another module's output, must not re-derive it.

---

## 1. Resolved seams (overlaps)

| # | Seam | Modules | Ruling (who owns what) | Crosses via |
|---|------|---------|------------------------|-------------|
| A | Strike universe vs ladder vs trade-strike | 1.3.1, 12.3, 4.3.3 | **12 OWNS** the valid strike ladder (intervals, ATM, snapping). **1** selects a data *window* of that ladder for the snapshot. **4** selects the *trade* strikes from the snapshot, snapped to 12's ladder. | `Instrument`, `MarketSnapshot`, `StructurePlan` |
| B | Expiry calendar vs expiry choice | 12.2, 4.2, 1.3.5 | **12 OWNS** the expiry calendar (weekly, holiday-shift, rollover). **4** picks *which* available expiry to trade (DTE policy). **1** consumes the resolved expiry. | `Instrument` |
| C | Order-type & price | 4.4, 13.1.1, 13.1.2 | **4 OWNS** pricing *intent* (target price, order-type policy, slippage tolerance). **13** OWNS execution *tactics* (submit, chase, protective limit) **bounded by 4's tolerance** — 13 never re-decides the price target. | `StructurePlan` (price/type fields) |
| D | Lot sizing | 4.3.5, 5.5 | **4** proposes a *desired* size to strategy intent. **5 OWNS** the binding size — it may resize down or reject. Final qty = 5's verdict. No double-authority. | `StructurePlan` → `RiskVerdict` |
| E | Max-loss / defined-risk | 4.5.2, 5.2 | **4** checks the structure *shape* is defined-risk (net-greek/shape sanity). **5 OWNS** the authoritative max-loss *number* used for capital gating. | `StructurePlan` → `RiskVerdict` |
| F | Exit authority | 3.5, 5.6, 6.5, 7.5 | Multiple modules may **raise** an exit; exactly one path **executes** it. Raisers: **5** (DD/daily-loss kill), **6** (SL/TSL/TP breach), **7** (EOD time), **3** (thesis-invalidation). **Precedence:** protective (5 → 6 → 7) outranks discretionary (3). All funnel to the single exit path (seam G). | exit-trigger → `StrategyDecision`(EXIT) |
| G | Exit/square-off execution & flat truth | 3.9, 6.6.1, 7.5, 13.11, 14.9 | **7 OWNS** *when* EOD square-off fires (time authority, single source). **3** stops new entries on 7's signal. **13** OWNS *placing* the exit/square-off orders. **14 OWNS** the authoritative "are we flat" truth. 13 confirms orders done; 14 confirms position flat; 7 closes the session on 14's confirmation. | session events, `Fill`, `PositionState` |
| H | Reconciliation with broker | 13.9, 14.8 | **13** reconciles **order** state (working/filled/orphan orders). **14** reconciles **position & P&L/cash**. Different objects — no overlap. | broker state |
| I | Slippage | 4.4.3, 13.8, 8.2.3 | **4** sets tolerance (ex-ante). **13** measures realized slippage (at fill). **8** analyses slippage patterns (post-hoc). Three different times, no conflict. | `StructurePlan`, `Fill`, traces |
| J | Reversal detection vs response | 2.7.2, 3.7.2 | **2 OWNS** *labelling* a reversal (market-state truth from the 7 families). **3** OWNS the *response* (close threatened leg → runner). 2 detects, 3 decides. | `RegimeState` → `StrategyDecision` |
| K | "Session" terminology clash | 11 (broker), 7 (market) | **Two distinct concepts.** **11 OWNS** the *broker* session (auth token/connection). **7 OWNS** the *market* session (trading-day phases/time). Never conflate. 1 & 13 consume 11's session. | `Session` (broker) vs session events (market) |
| L | Greeks/IV: value vs source | 1.3.3/1.3.4, 12.7 | **12** declares the greeks/IV *source & convention* (reference/metadata). **1 OWNS** the actual greek/IV *values* placed in the snapshot. | `Instrument` (ref) vs `MarketSnapshot` (values) |
| M | ParameterSet: decide vs store | 9, 10.5/10.6, 16.3/16.4/16.5/16.6 | **9** produces a *candidate*. **10 OWNS** the *governance decision* (approve, promotion ladder, set/clear the live pointer, decide rollback). **16 OWNS** the *storage mechanics* (freeze immutability, version store, atomic swap, serve, execute rollback). 10 decides "should"; 16 does "how/store". | `ParameterSet` |
| N | Post-mortem data source | 8.1, 14.10, 15 | **14 OWNS** trade/position/P&L records; **15 OWNS** decision/event traces. **8 CONSUMES** both (joins them) and owns no raw data. | records + traces |
| O | Backtest / PORCUPINE harness | 10.2, Phase 6 | The sim/replay harness is **shared infra** (PORCUPINE). **10** *invokes* it as a gate step; **Phase 6** *invokes* it for validation. Neither owns the harness; it is a common tool. | harness API |
| P | Multi-TF OHLC vs indicators | 1.4, 2.1.4/2.3 | **1 OWNS** the multi-TF OHLC bars (canonical candles in the snapshot). **2 CONSUMES** them and computes indicators on top — it must not re-aggregate raw ticks into bars. | `MarketSnapshot` (OHLC) |
| Q | Margin: estimate vs reject | 5.4, 13.7.3 | **5** estimates required margin *pre-trade* (gate). **13** handles a broker margin *reject* at placement (mechanics). Pre vs post submission. | `RiskVerdict`, reject events |

---

## 2. Open gaps (no module clearly owns — assign before Phase 0)

- **G1 — Authoritative "account state" provider.** DD floor (5.6.2) and sizing (5.5) need
  current **equity, available funds, and used margin**. 14 owns realized P&L/equity; the
  broker (via 11) owns funds/margin. **Ruling to ratify:** introduce a thin *Account State*
  read that 5 consumes = `14 equity` + `11/broker funds&margin`. Decide if it lives in 14,
  16, or a small new provider.
- **G2 — Research-loop trigger.** Modules 8→9→10 assume something invokes them after close.
  **Ruling to ratify:** **7** (market-session authority) emits a *session-closed* event that
  triggers the research loop (or a scheduler does). Pick one.
- **G3 — `ResearchCache` vs `ParameterSet`.** Earlier drafts named both. **Ruling:**
  `ParameterSet` (the frozen daily numbers, seam M) is the canonical connect-back; any
  discretionary `ResearchCache` is folded into it or dropped. Confirm and purge the older term.
- **G4 — Live price feed for mark-to-market.** 14.3 needs live prices; 1 owns the snapshot.
  **Ruling:** 14 CONSUMES 1's prices for MTM — it must not open its own feed.

---

## 3. Cross-cutting conventions

- **Idempotency is per-module** on its own output (3.10.3, 5.9.3, 6.5.3, 13.3, 14.1.2,
  15.3.3). Correlation across modules uses the **trade-lifecycle ID from 15.3** — every
  module stamps it so a trade can be stitched end-to-end.
- **One writer per truth.** `PositionState` truth = 14 only (single-writer). `Session`
  truth = 11 only. Live `ParameterSet` pointer = 16 store, 10 decision.
- **Determinism boundary.** Everything in the trading loop is deterministic; only the
  research loop (8/9/10) may use AI, and only offline (NFR-4).

---

## 4. Status

First reconciliation pass over the isolated discovery docs. Rulings 1.A–1.Q are firm
boundaries for Phase 0 contract-freezing. Gaps 2.G1–G4 need a Board ruling before Phase 0.
Module docs (`modules/`) are not rewritten — they remain discovery; **this doc governs the
seams.**
