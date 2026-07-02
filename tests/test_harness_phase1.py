"""The Phase-1 PORCUPINE harness (real pipeline) must be green across all scenarios.
Companion to test_harness.py, which covers the Phase-0 skeleton's scripted tape."""
from atom.harness_phase1 import run, run_all
from atom.scenarios_phase1 import SCENARIOS


def test_all_scenarios_pass_their_expectations():
    results = run_all()
    failed = [r.name for r in results if not r.passed]
    assert not failed, f"harness failures: {failed}"


def test_every_scenario_has_a_spec():
    for sc in SCENARIOS:
        assert sc.expect is not None, f"{sc.name} missing Expect spec"


def test_individual_checks_present():
    r = run(next(s for s in SCENARIOS if s.name == "P1_premiums_missing_overrules_open"))
    descs = [d for d, _ in r.checks]
    assert any("reason == premiums_unavailable" in d for d in descs)
