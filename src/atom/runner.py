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
             max_stale_sec: float = 90.0, im=None) -> dict:
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

    # Exit check (SL/TP/EOD — minimal slice, no TSL/morph yet) runs BEFORE any new-entry
    # decision. A stuck position must close before we ever ask "should we open."
    exit_check, position = None, None
    if fsm_state == "SINGLE_SPREAD":
        position = state.last_open_position()
        if position is not None:
            exit_check = phase1.check_exit(position, reader, snap.ts)
            if exit_check.triggered:
                exit_legs = {"hedge_ltp": exit_check.hedge_ltp, "short_ltp": exit_check.short_ltp}
                state.record_exit_and_checkpoint(position["ts"], now.isoformat(),
                                                  exit_check.reason, exit_check.current_pnl,
                                                  exit_legs, snap.ts)
                return {"action": "EXIT", "reason": exit_check.reason, "bar_ts": snap.ts,
                        "fsm_state": "FLAT", "position": position, "exit_check": exit_check}

    new_state, decision, order = phase1.cycle(fsm_state, snap, im)

    state.checkpoint_and_record(new_state, snap.ts, order, now.isoformat(), decision)

    # ATOM-Lights SHADOW — log every cycle for later P(profit|state); does NOT gate
    shadow, res = None, None
    if lights.CFG.get("lights.enabled", True):
        try:
            res = lights.evaluate(reader, snap.ind, snap.days_to_expiry)
            shadow = lights.shadow_entry(res, decision["regime"])
            state.record_lights_shadow(snap.ts, res, shadow, decision)
        except Exception:
            shadow, res = None, None

    ik = ("ema20_slope", "rsi", "st_consensus", "st_5min_direction", "st_15min_direction",
          "adx", "india_vix", "pcr_total", "sentiment", "structure_type", "vwap", "spot")
    return {"action": decision["intent"], "bar_ts": snap.ts, "spot": snap.spot,
            "atm": snap.atm_strike, "expiry": snap.expiry,
            "regime": decision["regime"], "confidence": decision["confidence"],
            "probs": decision["probs"], "votes": decision["votes"],
            "basis": phase1.branch_scores(snap.ind),
            "explain": {
                "votes": phase1.explain_votes(snap.ind, decision["votes"]),
                "regime": phase1.explain_regime(snap.ind, decision["votes"], decision["probs"]),
                "decision": phase1.explain_decision(fsm_state, decision["regime"],
                                                      decision["confidence"], snap.ts,
                                                      decision["intent"]),
                "fsm_meaning": phase1.FSM_MEANING.get(new_state, new_state),
                "open_position": position,
                "exit_check": exit_check,
            },
            "lights": None if res is None else {
                "lights": res.lights, "gap": res.gap, "permission": res.permission,
                "size": res.size, "trigger": res.trigger,
                "shadow_enter": shadow["enter"] if shadow else None,
                "shadow_reason": shadow["reason"] if shadow else None,
                "conviction_note": res.conviction_note},
            "indicators": {k: snap.ind.get(k) for k in ik},
            "structure": decision["structure"], "order": order, "fsm_state": new_state}
