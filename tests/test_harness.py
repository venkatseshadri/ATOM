"""The PORCUPINE-style harness must be green across all scenarios."""
from atom.harness import run, run_all
from atom.scenarios import SCENARIOS


def test_all_scenarios_pass_their_expectations():
    results = run_all()
    failed = [r.name for r in results if not r.passed]
    assert not failed, f"harness failures: {failed}"


def test_every_scenario_has_a_spec():
    for sc in SCENARIOS:
        assert sc.spec is not None, f"{sc.name} missing Expect spec"


def test_individual_checks_present():
    r = run(next(s for s in SCENARIOS if s.name == "B_stop_loss"))
    descs = [d for d, _ in r.checks]
    assert any("exit reason == SL" in d for d in descs)
