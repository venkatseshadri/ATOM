"""Module 16 — Config & ParameterSet (Phase 0: minimal working store).

Serves params and the day's frozen ParameterSet. Decision authority over what
is approved lives in Module 10 (seam M); this module only stores/serves.
"""
from __future__ import annotations

from .contracts import AccountState, ParameterSet
from .util import now


class Config:
    def __init__(self, telemetry) -> None:
        self.t = telemetry
        self.params = {"index": "NIFTY", "deploy_inr": 200000, "dd_floor_pct": 10}

    def parameter_set(self) -> ParameterSet:
        self.t.emit("config", "serve_parameter_set", {"version": "phase0-canned"})
        return ParameterSet(
            version="phase0-canned",
            valid_for=now()[:10],
            params=dict(self.params),
            evidence_ref="none",
            approval_state="APPROVED",
        )

    def account_state(self) -> AccountState:
        # GAP G1: placeholder provider for Phase 0 only; real provider TBD (Phase 3).
        self.t.emit("config", "serve_account_state", {"note": "G1 placeholder"})
        return AccountState(
            equity=200000.0, available_funds=200000.0, used_margin=0.0,
            realized_pnl_today=0.0, trades_today=0, reentries_today=0,
        )
