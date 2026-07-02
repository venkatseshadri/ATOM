"""Module 16 — Config / ParameterSet loader (dotted-key properties).

Loads `config/atom.conf`, type-coerced against DEFAULTS. A later phase swaps the active
set via the research-loop ParameterSet (decide=Module 10, store/serve=here).
"""
from __future__ import annotations

import os

DEFAULTS = {
    "strategy.lot.size": 75,
    "strategy.wing.strikes": 4,
    "risk.deploy.inr": 200000,
    "risk.sl.pct": 35,
    "risk.dd.floor.pct": 10,
    "expiry.rule": "current_week",
    "regime.entry.min_confidence": 0.45,
    "regime.adx.ramp_cap": 40,
    "indicator.ema.enabled": True,
    "indicator.ema.lookback": 20,
    "indicator.rsi.enabled": True,
    "indicator.rsi.bull": 55,
    "indicator.rsi.bear": 45,
    "indicator.supertrend.enabled": True,
    "indicator.adx.enabled": True,
    "indicator.india_vix.enabled": True,
    "indicator.pcr.enabled": True,
    "indicator.structure.enabled": True,
    "lights.enabled": True,
    "lights.shadow": True,
    "lights.body.min_frac": 0.15,
    "lights.gap.threshold_pct": 0.3,
    "lights.time_gate": "10:15",
}

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                            "config", "atom.conf")


def _coerce(key: str, raw: str):
    proto = DEFAULTS.get(key)
    if isinstance(proto, bool):
        return raw.strip().lower() in ("true", "1", "yes")
    if isinstance(proto, int):
        return int(float(raw))
    if isinstance(proto, float):
        return float(raw)
    # unknown key → infer
    s = raw.strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return s


def load_config(path: str | None = None) -> dict:
    cfg = dict(DEFAULTS)
    p = path or DEFAULT_PATH
    if os.path.exists(p):
        for line in open(p):
            line = line.split("#", 1)[0].strip()
            if not line or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = _coerce(k.strip(), v)
    return cfg


def format_lines(cfg: dict) -> list[str]:
    def fmt(v):
        return "true" if v is True else "false" if v is False else v
    return [f"{k}={fmt(cfg[k])}" for k in sorted(cfg)]


# --- Phase 0 skeleton stub (kept for the scenario harness) --------------------
from .contracts import AccountState, ParameterSet  # noqa: E402
from .util import now  # noqa: E402


class Config:
    """Phase 0 stub — serves params + the day's frozen ParameterSet/account state."""
    def __init__(self, telemetry) -> None:
        self.t = telemetry
        self.params = {"index": "NIFTY", "deploy_inr": 200000, "dd_floor_pct": 10}

    def parameter_set(self) -> ParameterSet:
        self.t.emit("config", "serve_parameter_set", {"version": "phase0-canned"},
                    msg="CONFIG → loaded day's frozen ParameterSet (version phase0-canned)")
        return ParameterSet(version="phase0-canned", valid_for=now()[:10],
                            params=dict(self.params), evidence_ref="none",
                            approval_state="APPROVED")

    def account_state(self) -> AccountState:
        self.t.emit("config", "serve_account_state", {"note": "G1 placeholder"},
                    msg="CONFIG → account state ₹2,00,000 funds (G1 placeholder provider)")
        return AccountState(equity=200000.0, available_funds=200000.0, used_margin=0.0,
                            realized_pnl_today=0.0, trades_today=0, reentries_today=0)
