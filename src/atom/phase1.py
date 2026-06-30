"""ATOM Phase 1 — real pipeline: scout (7-family regime) → decide (FSM entry) →
construct (real strikes + real premiums) → place paper order. Stops at order placed
(no lifecycle / morph / SL — those are later phases).

Pure functions over a `Snapshot` (penguin.py). Indicators are CONSUMED from Penguin's
enriched row — ATOM does not recompute them; it only adds the anti-bias consensus and the
lifecycle decision. Thresholds here are the Phase-1 DEFAULTS (‹TBD›, research-loop tunes).
"""
from __future__ import annotations

from dataclasses import dataclass

from .penguin import Snapshot, _f

LOT = 75
WING_STRIKES = 2            # hedge = ATM ± 2 strikes (NIFTY 50 step => ₹100 wing)
STEP = 50
ADX_TREND = 22.0           # trend-present gate
CONF_ENTRY = 0.45          # min confidence to enter


# ---- 7-family regime (consensus over Penguin's enriched indicators) ----------

def seven_family_vote(ind: dict) -> dict:
    """Each family votes +1 (bull) / -1 (bear) / 0 (neutral/abstain). Directional
    families only; volatility/participation inform confidence, not direction."""
    v = {}
    # 1 Trend — SuperTrend consensus
    stc = (ind.get("st_consensus") or ind.get("supertrend_direction") or "").lower()
    v["trend"] = 1 if "bull" in stc else -1 if "bear" in stc else 0
    # 2 Momentum — RSI
    rsi = _f(ind.get("rsi"))
    v["momentum"] = 0 if rsi is None else 1 if rsi >= 55 else -1 if rsi <= 45 else 0
    # 3 Price-action — EMA20 slope
    slope = _f(ind.get("ema20_slope"))
    v["price_action"] = 0 if slope is None else 1 if slope > 0 else -1 if slope < 0 else 0
    # 4 Market structure — HH/HL bull, LH/LL bear
    st = (ind.get("structure_type") or "").upper()
    v["structure"] = 1 if st in ("HH", "HL") else -1 if st in ("LH", "LL") else 0
    # 5 Options sentiment — PCR + sentiment tag (PCR<0.9 call-heavy ~ bullish lean)
    pcr = _f(ind.get("pcr_total"))
    sent = (ind.get("sentiment") or "").lower()
    sv = 0
    if pcr is not None:
        sv += 1 if pcr < 0.9 else -1 if pcr > 1.1 else 0
    sv += 1 if "bull" in sent else -1 if "bear" in sent else 0
    v["sentiment"] = 1 if sv > 0 else -1 if sv < 0 else 0
    # 6 Volume/participation — VWAP side (often null intraday-early → abstain)
    vwap, spot = _f(ind.get("vwap")), _f(ind.get("spot"))
    v["participation"] = 0 if (vwap is None or spot is None) else 1 if spot >= vwap else -1
    # 7 Volatility — non-directional; abstains on direction (used in confidence)
    v["volatility"] = 0
    return v


def classify_regime(ind: dict) -> tuple[str, float, dict]:
    votes = seven_family_vote(ind)
    adx = _f(ind.get("adx")) or 0.0
    directional = [v for k, v in votes.items() if k != "volatility"]
    voting = [v for v in directional if v != 0]
    score = sum(directional)
    n = len(voting) or 1

    if adx < ADX_TREND:
        return "SIDEWAYS", round(abs(score) / n, 2), votes
    label = "TREND_UP" if score > 0 else "TREND_DOWN" if score < 0 else "SIDEWAYS"
    # confidence = vote margin, boosted by trend strength (ADX), capped 1.0
    margin = abs(score) / n
    strength = min(adx / 50.0, 1.0)
    conf = round(min(1.0, 0.5 * margin + 0.5 * strength), 2) if label != "SIDEWAYS" else round(margin, 2)
    return label, conf, votes


# ---- FSM entry decision (Phase 1: entry only) --------------------------------

def decide(fsm_state: str, regime: str, conf: float) -> tuple[str, str]:
    """(intent, structure). Phase 1 only opens with-trend on confirmed trend."""
    if fsm_state != "FLAT":
        return "SKIP", "single_position_open"          # already in a trade
    if conf < CONF_ENTRY:
        return "STAND_DOWN", "low_confidence"
    if regime == "TREND_UP":
        return "OPEN", "bull_put_spread"
    if regime == "TREND_DOWN":
        return "OPEN", "bear_call_spread"
    return "STAND_DOWN", regime.lower()                # sideways / reversal: no entry


# ---- construct order with REAL strikes + REAL premiums -----------------------

@dataclass(frozen=True)
class PaperOrder:
    structure: str
    legs: tuple        # (action, strike, right, ltp)
    net_credit: float
    max_loss: float
    lot: int


def build_order(structure: str, snap: Snapshot) -> PaperOrder | None:
    atm = snap.atm_strike
    wing = WING_STRIKES * STEP
    if structure == "bull_put_spread":
        short_k, hedge_k, right = atm, atm - wing, "PE"
    elif structure == "bear_call_spread":
        short_k, hedge_k, right = atm, atm + wing, "CE"
    else:
        return None
    sp = snap.chain.get((short_k, right))
    hp = snap.chain.get((hedge_k, right))
    if not sp or not hp or sp["ltp"] is None or hp["ltp"] is None:
        return None                                    # premiums unavailable → no fabrication
    short_ltp, hedge_ltp = sp["ltp"], hp["ltp"]
    credit_per = short_ltp - hedge_ltp
    net_credit = round(credit_per * LOT, 2)
    max_loss = round((wing - credit_per) * LOT, 2)
    legs = (("SELL", short_k, right, short_ltp), ("BUY", hedge_k, right, hedge_ltp))
    return PaperOrder(structure, legs, net_credit, max_loss, LOT)


# ---- the cycle (pure): state + snapshot -> new_state, decision, paper_order ---

def cycle(fsm_state: str, snap: Snapshot) -> tuple[str, dict, PaperOrder | None]:
    regime, conf, votes = classify_regime(snap.ind)
    intent, structure = decide(fsm_state, regime, conf)
    order = build_order(structure, snap) if intent == "OPEN" else None
    new_state = "SINGLE_SPREAD" if order else fsm_state
    if intent == "OPEN" and order is None:
        intent, structure = "STAND_DOWN", "premiums_unavailable"
    decision = {"regime": regime, "confidence": conf, "votes": votes,
                "intent": intent, "structure": structure}
    return new_state, decision, order
