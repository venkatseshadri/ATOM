# ATOM — PORCUPINE Harness

A scenario-driven regression harness for ATOM, modelled on the house **PORCUPINE** E2E
sim harness. It runs each scenario through the real `Orchestrator` and asserts an
expectation block — so behaviour is locked as the build proceeds.

## Why ATOM-native (not the brahmand PORCUPINE)
ATOM is a standalone **public** repo with its own contracts. The existing PORCUPINE is
wired to the antariksh/brahmand pipeline. Reusing it directly would couple the repos (and
risk leaking private code). So ATOM grows its **own** harness with the same pattern:
scenario tape → run → assert → green/red. If true reuse is ever wanted, add an adapter
that maps a PORCUPINE driver onto ATOM's `Orchestrator` — the seam is `run_scenario`.

## Pieces
- `src/atom/scenarios.py` — Phase-0 skeleton scenario tapes + `Expect` blocks (end state,
  P&L sign, FSM state path, exit reason). Scripted — these exercise a morph/hold/exit
  lifecycle that isn't built in real code yet (Phase 3+).
- `src/atom/harness.py` — runs a Phase-0 scenario through `Orchestrator`, reads telemetry,
  checks the `Expect`, plus a global invariant (every day ends FLAT — no overnight risk).
- `run_harness.py` — runs all Phase-0 scenarios, prints + writes `logs/harness.log`.
- `tests/test_harness.py` — the Phase-0 harness must stay green.

- `src/atom/scenarios_phase1.py` — **real-pipeline** scenarios (added 2026-07-02, GATE 1
  review). Drives `runner.run_once()` directly against the shared fixture DB with targeted
  config/state forcing — not the `Orchestrator`/`modules/` skeleton, which forked away
  from the real `phase1.py` implementation and was never reconciled (see file docstring).
- `src/atom/harness_phase1.py` — runs a Phase-1 scenario, asserts action/reason/regime/
  fsm_state/paper_trades against the real output.
- `run_harness_phase1.py` — runs all Phase-1 scenarios, prints + writes
  `logs/harness_phase1.log`.
- `tests/test_harness_phase1.py` — the Phase-1 harness must stay green.

- `src/atom/scenarios_phase3.py` — fault-injection scenarios (added 2026-07-06). Two
  catalogues: `SCENARIOS` (pipeline-driven, seeds `paper_trades` to engineer risk-gate
  faults) and `DIRECT_CHECKS` (Modules 6/7/11 pure-function fault injection).
- `src/atom/harness_phase3.py` — runs a Phase-3 scenario, asserts action/fsm_state/
  paper_trades/risk_verdict against the real output.
- `run_harness_phase3.py` — runs all Phase-3 scenarios + direct checks, prints + writes
  `logs/harness_phase3.log`.
- `tests/test_harness_phase3.py` — the Phase-3 harness must stay green.

## Run
```bash
python3 run_harness.py            # Phase-0 skeleton: report to stdout + logs/harness.log
python3 run_harness_phase1.py     # Phase-1 real pipeline: stdout + logs/harness_phase1.log
python3 run_harness_phase3.py     # Phase-3 risk-gate + stop-mgmt faults: stdout + logs/harness_phase3.log
python3 -m pytest                 # includes all three harnesses
```

## How it grows with the build
Phase 0 drives the walking skeleton (scripted tapes); the harness already locks the
state machine, exit paths, P&L direction, and the no-overnight invariant. Phase 1 is now
covered by its own real-pipeline track (`scenarios_phase1.py`) instead of retrofitting the
Phase-0 stubs — see that file's docstring for why.

**Phase 3 closed (2026-07-06)**: `scenarios_phase3.py` + `harness_phase3.py` +
`run_harness_phase3.py`. Two catalogues — pipeline-driven (`runner.run_once(use_tsl=,
risk_gate=)` against the real fixture, seeding `paper_trades` to engineer daily-loss-cap/
re-entry-limit/tiny-capital faults) and direct fault-injection for Modules 6/7/11 (bad
tick, halted market at square-off, stale broker margin) since those aren't wired into
`run_once`'s gating yet — no pipeline path exists to drive them through, so the functions
are tested directly under the injected fault instead. **Caught a real bug**: a single
implausible tick (50x the credit collected) permanently poisoned the TSL high-water mark
— the ratchet-merge that protects against loosening also meant it never un-poisoned even
after the price reverted to something sane, silently disabling the stop for the rest of
the position's life. Fixed in `stop_management.py` (a credit spread's max profit is
capped at the credit collected — any reading above that is clamped before it can update
the ratchet).

Still open:
- **Phase 2** — assert ATM/strike/symbol correctness and structure shape.
- **Phase 5** — assert the research loop produces a gated ParameterSet; AI stays offline.

The MOCK/REAL legend in the Phase-0 report makes clear which assertions are exercising
real code vs still-stubbed behaviour. The Phase-1 track has no such legend — everything
it touches (`runner.py`, `phase1.py`, `penguin.py`, `lights.py`) is real.
