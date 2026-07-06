"""The Phase-4 PORCUPINE harness (direct fault injection for Modules 14/15/16) must
be green across all scenarios. Companion to test_harness_phase3.py."""
from atom.scenarios_phase4 import DIRECT_CHECKS


def test_all_direct_fault_checks_pass():
    failed = [name for name, _, fn in DIRECT_CHECKS if not fn()]
    assert not failed, f"direct fault-injection failures: {failed}"


def test_every_check_has_a_name_and_title():
    for name, title, fn in DIRECT_CHECKS:
        assert name and title and callable(fn)
