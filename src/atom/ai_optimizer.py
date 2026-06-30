"""Module 10 (mock) — AI / research-loop optimizer.

Phase 1 is a **MOCK**. The real research loop (Phase 5) post-mortems closed trades and
proposes a tuned `ParameterSet`, gated by morning human approval, that overrides
`config/atom.conf`. Until then this module returns a small, STATIC set of "optimized"
deltas so the operator log has the `Load optimized values from AI module` step wired —
the seam is real, the optimization is not.

Honesty contract: every value here is labelled MOCK. No backtest, no learning, no PnL.
"""
from __future__ import annotations

# Static stand-in for what Phase-5 would learn. Keys mirror config.DEFAULTS so the merge
# is type-safe. Deltas are illustrative ("as if tuned"), NOT evidence-backed.
_MOCK_OVERRIDES = {
    "regime.entry.min_confidence": 0.48,   # was 0.45 — "tuned" slightly stricter
    "indicator.rsi.bull": 57,              # was 55
    "indicator.rsi.bear": 43,              # was 45
}

VERSION = "mock-phase1-static"


def load_optimized(cfg: dict) -> dict:
    """Return {version, source, overrides, applied}. `applied` = cfg with overrides
    merged on top (caller may adopt it). Phase 1 logs it but still runs on base cfg
    unless told otherwise — the merge is exposed, adoption is the caller's choice."""
    overrides = {k: v for k, v in _MOCK_OVERRIDES.items() if k in cfg}
    applied = dict(cfg)
    applied.update(overrides)
    return {
        "version": VERSION,
        "source": "MOCK (Phase 5 = real research loop + human-gated ParameterSet)",
        "overrides": overrides,
        "applied": applied,
    }
