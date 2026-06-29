"""Per-module implementation status — the single source of MOCK vs REAL.

Update entries from MOCK to REAL as each phase lands. Every scenario log prints this
legend so a reader always knows what is real and what is still stubbed.
"""
from __future__ import annotations

PHASE = 0

MODULE_STATUS = {
    "auth": "MOCK",
    "instrument": "MOCK",
    "market_data": "MOCK",
    "regime": "MOCK",
    "strategy_fsm": "MOCK (scripted tape)",
    "structure_builder": "MOCK",
    "risk": "MOCK",
    "stop_management": "MOCK",
    "market_session": "MOCK",
    "order": "MOCK (paper)",
    "ledger": "MOCK",
    "telemetry": "REAL",
    "config": "MOCK",
    "post_mortem": "MOCK",
    "optimization": "MOCK",
    "feedback_gate": "MOCK",
}


def legend_lines() -> list[str]:
    out = [f"# MOCK/REAL status — Phase {PHASE} (update as phases land):"]
    for k in sorted(MODULE_STATUS):
        out.append(f"#   {k:<18} {MODULE_STATUS[k]}")
    return out


def print_legend() -> None:
    for line in legend_lines():
        print(line)
