"""Phase-1 cycle runner: pull + freshness gate around the pure `cycle()`.

One invocation = load state → read latest Penguin bar → freshness/idempotency gate →
cycle → checkpoint → (paper) record. Timer-fired/per-bar friendly; stateless between runs.
"""
from __future__ import annotations

from datetime import datetime

from . import lights, phase1
from .atom_state import AtomState
from .penguin import PenguinReader


def _age_sec(bar_ts: str, now: datetime) -> float:
    # Penguin timestamps are IST-naive; compare naive-to-naive (box runs IST).
    s = bar_ts.replace("Z", "")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return 1e9
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    return (now - dt).total_seconds()


def run_once(reader: PenguinReader, state: AtomState, now: datetime | None = None,
             max_stale_sec: float = 90.0) -> dict:
    now = now or datetime.now()
    fsm_state, last_bar_ts = state.load()

    snap = reader.latest_snapshot()
    if snap is None:
        return {"action": "STAND_DOWN", "reason": "no_data", "fsm_state": fsm_state}

    if snap.ts == last_bar_ts:
        return {"action": "NO_OP", "reason": "no_new_bar", "bar_ts": snap.ts,
                "fsm_state": fsm_state}

    age = _age_sec(snap.ts, now)
    if age > max_stale_sec:
        return {"action": "STAND_DOWN", "reason": "stale_feed", "bar_ts": snap.ts,
                "age_sec": round(age), "fsm_state": fsm_state}

    new_state, decision, order = phase1.cycle(fsm_state, snap)

    state.checkpoint(new_state, snap.ts)
    if order is not None:
        state.record_paper_trade(now.isoformat(), snap.ts, order, decision)

    # ATOM-Lights SHADOW — log every cycle for later P(profit|state); does NOT gate
    shadow = None
    if lights.CFG.get("lights.enabled", True):
        try:
            res = lights.evaluate(reader, snap.ind, snap.days_to_expiry)
            shadow = lights.shadow_entry(res, decision["regime"])
            state.record_lights_shadow(snap.ts, res, shadow, decision)
        except Exception:
            shadow = None

    ik = ("ema20_slope", "rsi", "st_consensus", "adx", "india_vix", "pcr_total")
    return {"action": decision["intent"], "bar_ts": snap.ts, "spot": snap.spot,
            "atm": snap.atm_strike, "expiry": snap.expiry,
            "regime": decision["regime"], "confidence": decision["confidence"],
            "probs": decision["probs"], "votes": decision["votes"],
            "indicators": {k: snap.ind.get(k) for k in ik},
            "structure": decision["structure"], "order": order, "fsm_state": new_state}
