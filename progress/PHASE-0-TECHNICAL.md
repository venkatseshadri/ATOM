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

## 4. Module stubs

Each stub takes `telemetry` in its constructor, emits one `TraceEvent` per call, and
returns a canned contract object. Canned-not-behavioural is deliberate (T0.3): e.g.
`Regime.classify` returns `SIDEWAYS` regardless of input.

Each file's docstring states the module's **ownership** per `SEAM_RECONCILIATION.md` (e.g.
Trade Construction "owns price intent + trade-strike choice"; Order "owns placement
mechanics"). This pre-wires the boundaries for the REAL phases.

## 5. Orchestrator — one pass

`Orchestrator.run_cycle(index)` executes, in order:

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

**Result:** `5 passed in 0.03s`.

## 7. How to run

```bash
python3 run_phase0.py      # prints the traced pass + contract objects + 16/16
python3 -m pytest          # 5 passed
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
