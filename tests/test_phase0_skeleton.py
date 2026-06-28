"""Phase 0 acceptance tests (BUILD_PLAN / phases/phase-0-skeleton/testcases.md).

T0.1 pipeline pass · T0.2 contract conformance · T0.3 no-logic guard.
"""
import dataclasses

import pytest

from atom.contracts import (
    ALL_CONTRACTS, MarketSnapshot, RegimeState, RiskVerdict, StrategyDecision,
    StructurePlan, PositionState, Session, Instrument,
)
from atom.orchestrator import EXPECTED_SOURCES, Orchestrator
from atom.telemetry import Telemetry


# --- T0.1 Pipeline pass --------------------------------------------------------

def test_pipeline_runs_end_to_end():
    orch = Orchestrator(Telemetry(echo=False))
    r = orch.run_cycle("NIFTY")
    # every contract object in the pass is present and correctly typed
    assert isinstance(r.session, Session)
    assert isinstance(r.instrument, Instrument)
    assert isinstance(r.snapshot, MarketSnapshot)
    assert isinstance(r.regime, RegimeState)
    assert isinstance(r.decision, StrategyDecision)
    assert isinstance(r.plan, StructurePlan)
    assert isinstance(r.verdict, RiskVerdict)
    assert isinstance(r.position, PositionState)
    assert isinstance(r.fills, list) and r.fills


def test_all_sixteen_modules_emit_a_trace():
    orch = Orchestrator(Telemetry(echo=False))
    orch.run_cycle("NIFTY")
    missing = EXPECTED_SOURCES - orch.t.sources()
    assert not missing, f"modules with no trace: {missing}"
    assert len(EXPECTED_SOURCES) == 16


# --- T0.2 Contract conformance -------------------------------------------------

def test_contracts_are_frozen():
    orch = Orchestrator(Telemetry(echo=False))
    r = orch.run_cycle("NIFTY")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.regime.regime = "TREND_UP"  # type: ignore[misc]


def test_contract_registry_complete():
    # every contract is a frozen dataclass
    for c in ALL_CONTRACTS:
        assert dataclasses.is_dataclass(c)
        assert c.__dataclass_params__.frozen, f"{c.__name__} not frozen"


# --- T0.3 No-logic guard -------------------------------------------------------

def test_regime_stub_is_canned_not_behavioural():
    orch = Orchestrator(Telemetry(echo=False))
    s1 = orch.market_data.snapshot("NIFTY", orch.auth.login())
    s2 = orch.market_data.snapshot("SENSEX", orch.auth.login())
    # different inputs -> same canned label (proves wiring, not behaviour)
    assert orch.regime.classify(s1).regime == orch.regime.classify(s2).regime == "SIDEWAYS"
