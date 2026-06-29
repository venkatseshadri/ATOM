# ATOM — Run Logs

Captured outputs from running the built code. Each log header records the timestamp,
command, Python version, and commit it was produced at.

| Log | Command | What it shows |
|-----|---------|---------------|
| [phase0_run.log](phase0_run.log) | `python3 run_phase0.py` | one full skeleton pass — 16/16 module traces + contract objects produced |
| [phase0_pytest.log](phase0_pytest.log) | `python3 -m pytest -v` | full test suite (skeleton + scenarios + harness) |
| [harness.log](harness.log) | `python3 run_harness.py` | PORCUPINE-style harness report — per-scenario PASS/FAIL vs expect-DSL |
| [scenarios/](scenarios/) | `python3 run_scenarios.py` | one detailed trace log per scenario (+ MOCK/REAL legend) |

Regenerate:
```bash
python3 run_phase0.py    > logs/phase0_run.log
python3 -m pytest -v     > logs/phase0_pytest.log
python3 run_harness.py                 # writes logs/harness.log
python3 run_scenarios.py               # writes logs/scenarios/*.log
```
