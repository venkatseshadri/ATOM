"""The Phase-3 PORCUPINE harness (real pipeline risk-gate faults + direct Module 6/7/11
fault injection) must be green across all scenarios. Closes the "still open" Phase 3
gap docs/PORCUPINE.md flagged. Companion to test_harness_phase1.py."""
from atom.harness_phase3 import run, run_all
from atom.scenarios_phase3 import DIRECT_CHECKS, SCENARIOS


def test_all_scenarios_pass_their_expectations():
    results = run_all()
    failed = [r.name for r in results if not r.passed]
    assert not failed, f"harness failures: {failed}"


def test_every_scenario_has_a_spec():
    for sc in SCENARIOS:
        assert sc.expect is not None, f"{sc.name} missing Expect spec"


def test_daily_loss_cap_scenario_checks_the_right_reason():
    r = run(next(s for s in SCENARIOS if s.name == "P3_risk_gate_blocks_daily_loss_cap"))
    descs = [d for d, _ in r.checks]
    assert any("DAILY_LOSS_CAP_HIT" in d for d in descs)


def test_all_direct_fault_checks_pass():
    failed = [name for name, _, fn in DIRECT_CHECKS if not fn()]
    assert not failed, f"direct fault-injection failures: {failed}"
