# Phase 0 — Technical Detail

Full technical record of the built skeleton. Status: **built, awaiting GATE 0.**

## 1. Goal recap

Prove the pipeline *shape* and **freeze the contracts** before any trading logic. Every
module exists as a stub that accepts its input contract, emits a trace, and returns a
canned output. No regime really classified, no order really placed.

## 2. Stack & layout

- **Language:** Python ≥ 3.10, standard library only (no runtime deps).
- **Tests:** pytest (`pyproject.toml` sets `pythonpath = ["src"]`).
- **Layout:**

```
atom/
├── pyproject.toml              # pytest config (pythonpath=src)
├── run_phase0.py               # entry point — prints one pass
├── src/atom/
│   ├── contracts.py            # 15 frozen dataclass contracts
│   ├── telemetry.py            # Module 15 — trace collector (minimal real)
│   ├── config.py               # Module 16 — params + ParameterSet + AccountState
│   ├── util.py                 # now()
│   ├── orchestrator.py         # wires one full pass; EXPECTED_SOURCES (16)
│   └── modules/                # 16 stubs, one file per module
│       ├── market_data.py      # 1
│       ├── regime.py           # 2
│       ├── strategy_fsm.py     # 3
│       ├── structure_builder.py# 4
│       ├── risk.py             # 5
│       ├── stops.py            # 6
│       ├── market_session.py   # 7
│       ├── post_mortem.py      # 8
│       ├── optimization.py     # 9
│       ├── feedback_gate.py    # 10
│       ├── auth.py             # 11
│       ├── instrument.py       # 12
│       ├── order.py            # 13
│       └── ledger.py           # 14
└── tests/test_phase0_skeleton.py
```

(22 tracked code files.)

## 3. Contracts (frozen)

`src/atom/contracts.py` — all `@dataclass(frozen=True)`, so they are literally immutable.

| Contract | Producer (owner) | Key fields |
|----------|------------------|------------|
| `TraceEvent` | every module | source, type, payload, ts |
| `Session` | Auth (11) | broker, token, state, expires_at |
| `Instrument` | Instrument (12) | tradingsymbol, index, expiry, strike, right, lot_size, tick_size |
| `OptionQuote` | Market Data (1) | strike, right, ltp, iv, delta, gamma, theta, vega |
| `MarketSnapshot` | Market Data (1) | index, ts, spot, chain, ohlc |
| `RegimeState` | Regime (2) | index, ts, regime, confidence |
| `StrategyDecision` | Strategy FSM (3) | intent, structure, rationale |
| `Leg` / `StructurePlan` | Trade Construction (4) | legs, net_credit, max_loss |
| `AccountState` | *gap G1 — TBD* | equity, available_funds, used_margin, realized_pnl_today, trades_today, reentries_today |
| `RiskVerdict` | Risk (5) | approved, adjusted_qty, breached, sl, tsl, tp |
| `OrderRequest` / `Fill` | Order (13) | order_id, leg_symbol, fill_price, qty, status, ts |
| `PositionState` | Ledger (14) | fsm_state, legs, live_pnl, realized_pnl |
| `ParameterSet` | Optimization/FeedbackGate (9/10), stored by Config (16) | version, valid_for, params, evidence_ref, approval_state |

`ALL_CONTRACTS` registry drives the conformance test.

### Gap handling baked in
- **G1** — `AccountState` shape frozen now; *provider* unassigned (Config serves a
  placeholder for Phase 0 only). Real provider decided before Phase 3.
- **G3** — `ParameterSet` is the single connect-back contract; the old `ResearchCache`
  term is purged.

## 4. Module stubs — walking skeleton

Each stub takes `telemetry` in its constructor, emits a `TraceEvent` per call (with a
human-readable narration), and returns a contract object carrying **illustrative,
hard-coded values** — a *walking skeleton*. The values are representative, not computed:
e.g. NIFTY spot ₹23,412.5, a bull put spread SELL 23400 PE @ ₹123.40 / BUY 23300 PE @
₹45.60 (hedge), net credit ₹5,835, max loss ₹1,665. This lets a human read the intended
session flow end-to-end. Canned-not-behavioural is still deliberate (T0.3): e.g.
`Regime.classify` returns `SIDEWAYS` regardless of input. Real computation arrives at each
module's REAL phase.

**ATM derivation is the one piece of real math:** Module 12 computes
`ATM = round(spot / step) × step` from the captured spot (e.g. 23,412.5 → 23,400), and
Module 4 picks short = ATM, hedge = ATM ± wing, snapping to the ladder. The order then
places the concrete tradingsymbol (`SELL NIFTY03JUL2623400PE`). The **premiums** and the
selection *policy* (which delta/offset) are still illustrative — they become real in
Phase 2. So "how does it know the ATM" is answered in-log; "what premium / which delta"
is still mock.

The narrated pass (see `logs/phase0_run.log`):
`SESSION open → login SUCCESS → data capture/ticks flowing → 7-family read → ENTRY
criteria met → CONSTRUCT spread → RISK APPROVED → orders FILLED (short + hedge) →
POSITION open → research-loop preview`.

Each file's docstring states the module's **ownership** per `SEAM_RECONCILIATION.md` (e.g.
Trade Construction "owns price intent + trade-strike choice"; Order "owns placement
mechanics"). This pre-wires the boundaries for the REAL phases.

## 5. Orchestrator — two entry points

### `run_session(index)` — full-day walking skeleton (what `run_phase0.py` runs)
Drives a scripted lifecycle **tape** (`SESSION_TAPE`, a lookup — not real strategy) so
the log shows the whole day:
1. **Mock cron schedule** printed (capture/session/cycle/lockout/squareoff/research/approval timers).
2. **09:14 pre-open** — auth, instrument, data capture warmup.
3. **Intraday cycles** walking the morph lifecycle, each a full module pass:
   `09:20 TREND_UP → OPEN bull put spread (SINGLE_SPREAD)` →
   `11:30 SIDEWAYS → MORPH_ADD bear call spread (IRON_FLY)` →
   `13:30 REVERSAL → MORPH_CLOSE_LEG, keep runner (RUNNER)` →
   `15:25 EOD → EXIT square-off (FLAT, realized ₹+4,200)`. Leg-shift/morph lines emitted by Builder + Order.
4. **15:30 cron square-off**, **15:45 cron EOD AI research loop**
   (AI post-mortem → AI/LLM optimizer → feedback gate + PORCUPINE backtest), **08:45+1 morning approval gate**.

### `run_scenario(scenario, index)` — one scenario, one log
Generic runner over a scenario tape (`src/atom/scenarios.py`) exercising distinct exit
paths. `python3 run_scenarios.py` writes one log per scenario to `logs/scenarios/`, each
headed by a **MOCK/REAL legend** (`src/atom/status.py`) so the logs stay meaningful as
phases turn modules real:

| Scenario | Exit path |
|----------|-----------|
| A_morph_to_eod | morph → iron fly → runner → EOD square-off |
| B_stop_loss | SL breach → exit at max loss |
| C_take_profit | TP target → exit in profit |
| D_trailing_stop | TSL trails → triggers → exit |
| E_risk_reject | risk gate rejects → no order |

Stops (Module 6) raise the SL/TSL/TP breach → the orchestrator funnels to a single exit
path (per SEAM_RECONCILIATION §F/G); risk (Module 5) can reject an entry.

### `run_cycle(index)` — single pass (used by the acceptance tests)
Executes, in order:

1. `market_session.tick()` (7)
2. `config.parameter_set()` + `config.account_state()` (16)
3. `auth.login()` → `Session` (11)
4. `instrument.resolve()` → `Instrument` (12)
5. `market_data.snapshot()` → `MarketSnapshot` (1)
6. `regime.classify()` → `RegimeState` (2)
7. `fsm.decide()` → `StrategyDecision` (3)
8. `builder.build()` → `StructurePlan` (4)
9. `risk.gate()` → `RiskVerdict` (5)
10. `order.execute()` → `Fill[]` (13)
11. `ledger.apply()` → `PositionState` (14)
12. `stops.manage()` (6)
13. research pass: `post_mortem.analyze()` → `optimization.propose()` →
    `feedback_gate.evaluate()` (8 → 9 → 10)

Returns a `CycleResult` carrying every contract object in pipeline order.
`EXPECTED_SOURCES` lists the 16 module trace-sources the pass must cover.

## 6. Tests (Phase 0 DoD)

`tests/test_phase0_skeleton.py`:

| Test | Maps to | Asserts |
|------|---------|---------|
| `test_pipeline_runs_end_to_end` | T0.1 | every contract object produced + correctly typed |
| `test_all_sixteen_modules_emit_a_trace` | T0.1 | `EXPECTED_SOURCES ⊆ telemetry.sources()`, count == 16 |
| `test_contracts_are_frozen` | T0.2 | mutating a contract raises `FrozenInstanceError` |
| `test_contract_registry_complete` | T0.2 | every `ALL_CONTRACTS` member is a frozen dataclass |
| `test_regime_stub_is_canned_not_behavioural` | T0.3 | different inputs → same canned label |

Plus `tests/test_scenarios.py` runs all 5 scenarios and checks end-states (FLAT, loss on
SL, gain on TP, reject stays flat). **Result: `10 passed`.**

## 7. How to run

```bash
python3 run_phase0.py      # scenario A to stdout (full day) + 16/16
python3 run_scenarios.py   # one log per scenario -> logs/scenarios/
python3 -m pytest          # 10 passed
```

## 8. DoD status (Phase 0)

- [x] Every contract object defined and **frozen**.
- [x] All 16 modules exist as STUB (trace + canned output).
- [x] Orchestrator runs one full pass end-to-end.
- [x] Each module emits a trace (16/16).
- [x] Stub test suite green (5 passed).
- [ ] **GATE 0 — Board sign-off** (not self-signed).

## 9. Not done by design (later phases)
Real market data, 7-family regime, greek-driven construction, the hard risk gate,
stop trailing, real fills, P&L truth, the research loop. Each arrives at its phase per
`../BUILD_PLAN.md`.
