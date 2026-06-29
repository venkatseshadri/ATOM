"""Scenario smoke tests — every scenario runs and ends in the expected state."""
from atom.orchestrator import Orchestrator
from atom.scenarios import SCENARIOS
from atom.telemetry import Telemetry


def _run(name):
    sc = next(s for s in SCENARIOS if s.name == name)
    return Orchestrator(Telemetry(echo=False)).run_scenario(sc)


def test_all_scenarios_run():
    for sc in SCENARIOS:
        Orchestrator(Telemetry(echo=False)).run_scenario(sc)


def test_exit_scenarios_end_flat():
    for name in ("A_morph_to_eod", "B_stop_loss", "C_take_profit", "D_trailing_stop"):
        assert _run(name).fsm_state == "FLAT"


def test_stop_loss_realizes_loss():
    assert _run("B_stop_loss").realized_pnl < 0


def test_take_profit_realizes_gain():
    assert _run("C_take_profit").realized_pnl > 0


def test_risk_reject_stays_flat_no_pnl():
    pos = _run("E_risk_reject")
    assert pos.fsm_state == "FLAT" and pos.realized_pnl == 0
