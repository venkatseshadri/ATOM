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
- `src/atom/scenarios.py` — scenario tapes + `Expect` blocks (end state, P&L sign, FSM
  state path, exit reason).
- `src/atom/harness.py` — runs a scenario, reads telemetry, checks the `Expect`, plus a
  global invariant (every day ends FLAT — no overnight risk).
- `run_harness.py` — runs all scenarios, prints + writes `logs/harness.log`, exits non-zero
  on any failure (CI-ready).
- `tests/test_harness.py` — the harness must stay green.

## Run
```bash
python3 run_harness.py     # report to stdout + logs/harness.log
python3 -m pytest          # includes the harness
```

## How it grows with the build
Phase 0 drives the walking skeleton (scripted tapes); the harness already locks the
state machine, exit paths, P&L direction, and the no-overnight invariant. As modules turn
MOCK → REAL (see the legend printed in each report), **add to the same harness**:
- **Phase 1** — feed a replay/fixture market stream; assert regime labels + FSM decisions.
- **Phase 2** — assert ATM/strike/symbol correctness and structure shape.
- **Phase 3** — fault injection (missing greeks, bad tick, partial fill, reject); assert
  risk invariants (no breach, EOD square-off) hold under faults.
- **Phase 5** — assert the research loop produces a gated ParameterSet; AI stays offline.

The MOCK/REAL legend in every report makes clear which assertions are exercising real code
vs still-stubbed behaviour.
