"""ATOM Phase 1 — real pipeline: scout (7-family regime) → decide (FSM entry) →
construct (real strikes + real premiums) → place paper order. Stops at order placed
(no lifecycle / morph / SL — those are later phases).

Pure functions over a `Snapshot` (penguin.py). Indicators are CONSUMED from Penguin's
enriched row — ATOM does not recompute them; it only adds the anti-bias consensus and the
lifecycle decision. Thresholds here are the Phase-1 DEFAULTS (‹TBD›, research-loop tunes).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import config
from .penguin import Snapshot, _f

STEP = 50

# active config (dotted keys) — overridable via configure(); defaults from config.py
CFG = dict(config.DEFAULTS)


def index_for_weekday(now: datetime | None = None) -> str:
    """0-1 DTE index selection (Board rule, 2026-07-05): NIFTY's weekly expires
    Tuesday, so Fri/Mon/Tue are its 0-1 DTE days; SENSEX's weekly expires Thursday,
    so Wed/Thu are its 0-1 DTE days. Weekend inputs (market closed, cron doesn't
    fire) fall back to NIFTY harmlessly."""
    now = now or datetime.now()
    return "SENSEX" if now.weekday() in (2, 3) else "NIFTY"


def configure(cfg: dict) -> None:
    CFG.clear()
    CFG.update(cfg)


# ---- 7-family regime (consensus over Penguin's enriched indicators) ----------

def seven_family_vote(ind: dict) -> dict:
    """Each family votes +1 (bull) / -1 (bear) / 0 (neutral/abstain). Directional
    families only; volatility/participation inform confidence, not direction."""
    v = {}
    # 1 Trend — SuperTrend consensus
    stc = (ind.get("st_consensus") or ind.get("supertrend_direction") or "").lower()
    on = CFG.get("indicator.supertrend.enabled", True)
    v["trend"] = (1 if "bull" in stc else -1 if "bear" in stc else 0) if on else 0
    # 2 Momentum — RSI (thresholds from config)
    rsi = _f(ind.get("rsi"))
    bull, bear = CFG.get("indicator.rsi.bull", 55), CFG.get("indicator.rsi.bear", 45)
    if rsi is None or not CFG.get("indicator.rsi.enabled", True):
        v["momentum"] = 0
    else:
        v["momentum"] = 1 if rsi >= bull else -1 if rsi <= bear else 0
    # 3 Price-action — EMA20 slope
    slope = _f(ind.get("ema20_slope"))
    v["price_action"] = 0 if slope is None else 1 if slope > 0 else -1 if slope < 0 else 0
    # 4 Market structure — HH/HL bull, LH/LL bear
    st = (ind.get("structure_type") or "").upper()
    on = CFG.get("indicator.structure.enabled", True)
    v["structure"] = (1 if st in ("HH", "HL") else -1 if st in ("LH", "LL") else 0) if on else 0
    # 5 Options sentiment — PCR + sentiment tag (PCR<0.9 call-heavy ~ bullish lean)
    pcr = _f(ind.get("pcr_total"))
    sent = (ind.get("sentiment") or "").lower()
    sv = 0
    if pcr is not None and CFG.get("indicator.pcr.enabled", True):
        sv += 1 if pcr < 0.9 else -1 if pcr > 1.1 else 0
    sv += 1 if "bull" in sent else -1 if "bear" in sent else 0
    v["sentiment"] = 1 if sv > 0 else -1 if sv < 0 else 0
    # 6 Volume/participation — VWAP side (often null intraday-early → abstain)
    vwap, spot = _f(ind.get("vwap")), _f(ind.get("spot"))
    v["participation"] = 0 if (vwap is None or spot is None) else 1 if spot >= vwap else -1
    # 7 Volatility — non-directional; abstains on direction (used in confidence)
    v["volatility"] = 0
    return v


def classify_regime(ind: dict) -> tuple[str, float, dict, dict]:
    """Return (label, confidence, probs{UP,DOWN,SIDEWAYS}, votes).

    Three-way probability: ADX gives trend *strength* (directional mass) via a smooth
    ramp `min(ADX/ramp_cap, 1)` — no threshold cliff. The rest is sideways mass. Within
    the directional mass, bull/bear split by the family votes. DecisionMaker =
    argmax(probs). (Defaults ‹TBD›; research-loop tunes.)
    """
    votes = seven_family_vote(ind)
    adx = _f(ind.get("adx")) or 0.0
    bull = sum(1 for k, v in votes.items() if k != "volatility" and v > 0)
    bear = sum(1 for k, v in votes.items() if k != "volatility" and v < 0)
    total = bull + bear
    ramp_cap = CFG.get("regime.adx.ramp_cap", 40)
    strength = min(adx / ramp_cap, 1.0) if ramp_cap else 1.0
    if total == 0:
        p_up, p_down, p_side = 0.0, 0.0, 1.0
    else:
        p_up = strength * bull / total
        p_down = strength * bear / total
        p_side = 1 - strength
    s = p_up + p_down + p_side or 1
    probs = {"UP": round(p_up / s, 3), "DOWN": round(p_down / s, 3),
             "SIDEWAYS": round(p_side / s, 3)}
    label_map = {"UP": "TREND_UP", "DOWN": "TREND_DOWN", "SIDEWAYS": "SIDEWAYS"}
    best = max(probs.values())
    tied = [k for k, v in probs.items() if v == best]
    # exact tie (most commonly UP==DOWN on an even bull/bear split) has no honest
    # winner — default to SIDEWAYS rather than silently picking UP via dict order
    winner = tied[0] if len(tied) == 1 else "SIDEWAYS"
    return label_map[winner], round(probs[winner], 2), probs, votes


# ---- presentation views over the vote/regime (operator log) ------------------

# 7 families in canonical order; volatility is non-directional (informs confidence only).
FAMILY_NAMES = {
    "trend": "Trend (SuperTrend)", "momentum": "Momentum (RSI)",
    "price_action": "Price-action (EMA slope)", "structure": "Market structure",
    "sentiment": "Options sentiment (PCR)", "participation": "Participation (VWAP)",
    "volatility": "Volatility (non-directional)",
}


def family_view(votes: dict) -> list[tuple[str, str]]:
    """[(family display name, direction word)] for the per-family log line.
    +1 → Up, -1 → Down, 0 → Neutral (volatility always Neutral by design)."""
    word = {1: "Up", -1: "Down", 0: "Neutral"}
    return [(FAMILY_NAMES[k], word[votes.get(k, 0)]) for k in FAMILY_NAMES]


FSM_MEANING = {
    "FLAT": "no position open — eligible to enter",
    "SINGLE_SPREAD": "one credit spread open — Phase 1 has no exit/SL/TP/EOD-close, "
                      "so new entries stay blocked until Phase 3 closes it",
}


def explain_votes(ind: dict, votes: dict) -> list[str]:
    """One line per family: raw indicator value(s), the threshold applied, the vote.
    Mirrors seven_family_vote()'s logic exactly — for the operator log, not re-decided."""
    stc = (ind.get("st_consensus") or ind.get("supertrend_direction") or "None")
    rsi, slope = ind.get("rsi"), ind.get("ema20_slope")
    r_bull, r_bear = CFG.get("indicator.rsi.bull", 55), CFG.get("indicator.rsi.bear", 45)
    st = ind.get("structure_type") or "None"
    pcr, sent = ind.get("pcr_total"), ind.get("sentiment") or "None"
    vwap, spot = ind.get("vwap"), ind.get("spot")
    cur, prev = ind.get("struct_cur"), ind.get("struct_prev")
    if cur and prev:
        (c_ts, c_h, c_l), (p_ts, p_h, p_l) = cur, prev
        struct_basis = (f"  ({c_ts[11:16]} h={c_h} l={c_l}  vs  {p_ts[11:16]} h={p_h} l={p_l}"
                         f" [1min bars]: high{'<' if c_h < p_h else '>='}prev & "
                         f"low{'<' if c_l < p_l else '>='}prev)")
    else:
        struct_basis = "  (insufficient bar history)"
    return [
        f"  Trend        SuperTrend consensus(5m+15m)={stc}"
        f" [bull->+1 / bear->-1]                     -> vote {votes['trend']:+d}",
        f"  Momentum     RSI-14={rsi}"
        f" [>={r_bull} bull / <={r_bear} bear]                     -> vote {votes['momentum']:+d}",
        f"  Price-action EMA20 slope={slope}"
        f" [>0 bull / <0 bear]                                -> vote {votes['price_action']:+d}",
        f"  Structure    swing structure_type={st}"
        f" [HH/HL bull / LH/LL bear]                     -> vote {votes['structure']:+d}"
        f"{struct_basis}",
        f"  Sentiment    PCR-total={pcr} tag={sent}"
        f" [PCR<0.9 bull / >1.1 bear, blended w/ tag]  -> vote {votes['sentiment']:+d}",
        f"  Participation spot={spot} vs VWAP={vwap}"
        f" [spot>=VWAP bull / else bear]               -> vote {votes['participation']:+d}",
        "  Volatility   non-directional — informs confidence strength only,"
        " never votes         -> vote  0",
        _vote_rollup(votes),
    ]


def _vote_rollup(votes: dict) -> str:
    bull = sum(1 for k, v in votes.items() if k != "volatility" and v > 0)
    bear = sum(1 for k, v in votes.items() if k != "volatility" and v < 0)
    neutral = 6 - bull - bear
    net = sum(v for k, v in votes.items() if k != "volatility")
    return (f"  => TOTAL  bull={bull}  bear={bear}  neutral={neutral}  "
            f"(of 6 directional families)  net={net:+d}")


def explain_regime(ind: dict, votes: dict, probs: dict) -> str:
    """Plain-language walkthrough of classify_regime()'s arithmetic — turns the vote
    count into a probability, weighted by how strongly ADX says the market is trending.
    Smooth ramp, no cliff: strength scales continuously with ADX/ramp_cap."""
    adx = _f(ind.get("adx")) or 0.0
    ramp_cap = CFG.get("regime.adx.ramp_cap", 40)
    bull = sum(1 for k, v in votes.items() if k != "volatility" and v > 0)
    bear = sum(1 for k, v in votes.items() if k != "volatility" and v < 0)
    total = bull + bear
    if total == 0:
        return ("  Step A — no family voted a direction at all (bull=0, bear=0), so\n"
                 f"           there's nothing to weight by trend strength (ADX={adx} is moot).\n"
                 "  => SIDEWAYS by default (100%) — this is the only case that bypasses ADX.")
    strength = round(min(adx / ramp_cap, 1.0), 3)
    p_up, p_down, p_side = strength * bull / total, strength * bear / total, 1 - strength
    trend_word = "strongly trending" if strength >= 0.8 else \
        "moderately trending" if strength >= 0.4 else "weak/choppy"
    return (
        "  Step A — how strongly is the market trending right now? (0=flat/choppy, ADX>=ramp_cap=fully trending)\n"
        f"    ADX={adx}, ramp_cap={ramp_cap}  ->  trend-strength = min(ADX/ramp_cap, 1) "
        f"= min({adx}/{ramp_cap}, 1) = {strength}  ({trend_word})\n"
        "  Step B — split that trend-strength across the vote lean (from the TOTAL line above)\n"
        f"    {bull} of {total} directional votes were bullish, {bear} were bearish\n"
        f"    P(UP)   = trend-strength * bull/total = {strength} * {bull}/{total} = {round(p_up, 3)}\n"
        f"    P(DOWN) = trend-strength * bear/total = {strength} * {bear}/{total} = {round(p_down, 3)}\n"
        f"    P(SIDEWAYS) = 1 - trend-strength = 1 - {strength} = {round(p_side, 3)}"
        "  (mass left over when the market isn't trending enough to call)\n"
        "  Step C — normalise to guard rounding (should already sum to ~1.0)\n"
        f"    -> UP={probs['UP']}  DOWN={probs['DOWN']}  SIDEWAYS={probs['SIDEWAYS']}\n"
        "  Step D — pick the winner (exact UP==DOWN tie defaults to SIDEWAYS, not "
        f"dict order)\n    -> {_tie_note(probs)}")


def _tie_note(probs: dict) -> str:
    best = max(probs.values())
    tied = [k for k, v in probs.items() if v == best]
    if len(tied) > 1:
        return f"TIE between {' and '.join(tied)} at {best} -> forced to SIDEWAYS"
    return f"clear winner: {tied[0]} at {best} (no tie)"


def explain_decision(fsm_state: str, regime: str, conf: float, bar_ts: str,
                      final_intent: str | None = None) -> str:
    """Full decision-tree walkthrough of decide() — every branch it checks, in the exact
    order it checks them, marking which one actually fired (x) vs never reached (skipped
    because an earlier branch already returned).

    decide() itself only ever says OPEN/SKIP/STAND_DOWN — but cycle() can still overrule
    an OPEN afterward if build_order() finds no real premium (STAND_DOWN premiums_
    unavailable). Pass the real post-cycle intent so that override is visible here
    instead of silently contradicting the RESULT line at the bottom of the log."""
    thr = CFG.get("regime.entry.min_confidence", 0.45)
    eod_cutoff = CFG.get("session.eod.cutoff", "15:20")
    lines = ["  decide() checks these in order, stops at the first match:"]

    def _construction_note(would_open: str) -> str:
        if final_intent is not None and final_intent != "OPEN":
            return (f"\n  => OVERRULED after decide(): build_order() found no real "
                    f"premium for {would_open} -> final result is "
                    f"{final_intent} (premiums_unavailable), not the OPEN shown above")
        return ""

    b1 = fsm_state != "FLAT"
    lines.append(f"   [{'x' if b1 else ' '}] 1. Is a position already open? (fsm={fsm_state} != FLAT) "
                 f"-> {b1}")
    if b1:
        lines.append("        => STOPS HERE: SKIP (single_position_open)")
        lines.append("   [-] 2. past EOD cutoff?                     not reached")
        lines.append("   [-] 3. confidence < min_confidence?         not reached")
        lines.append("   [-] 4. regime == TREND_UP?                  not reached")
        lines.append("   [-] 5. regime == TREND_DOWN?                not reached")
        lines.append("   [-] 6. else (SIDEWAYS / reversal)?          not reached")
        lines.append(f"  => regime={regime} confidence={conf} computed in Step 3 above, "
                      "but NEVER USED — branch 1 already decided the outcome")
        return "\n".join(lines)

    b2 = bar_ts[11:16] >= eod_cutoff
    lines.append(f"   [{'x' if b2 else ' '}] 2. Is bar time past EOD cutoff? "
                 f"({bar_ts[11:16]} >= {eod_cutoff}) -> {b2}")
    if b2:
        lines.append("        => STOPS HERE: STAND_DOWN (after_eod_cutoff) — no NEW entries "
                     "this late, same cutoff that force-closes existing ones")
        lines.append("   [-] 3. confidence < min_confidence?         not reached")
        lines.append("   [-] 4. regime == TREND_UP?                  not reached")
        lines.append("   [-] 5. regime == TREND_DOWN?                not reached")
        lines.append("   [-] 6. else (SIDEWAYS / reversal)?          not reached")
        return "\n".join(lines)

    b3 = conf < thr
    lines.append(f"   [{'x' if b3 else ' '}] 3. Is confidence < min_confidence? "
                 f"({conf} < {thr}) -> {b3}")
    if b3:
        lines.append("        => STOPS HERE: STAND_DOWN (low_confidence)")
        lines.append("   [-] 4. regime == TREND_UP?                  not reached")
        lines.append("   [-] 5. regime == TREND_DOWN?                not reached")
        lines.append("   [-] 6. else (SIDEWAYS / reversal)?          not reached")
        return "\n".join(lines)

    b4 = regime == "TREND_UP"
    lines.append(f"   [{'x' if b4 else ' '}] 4. Is regime == TREND_UP? -> {b4}")
    if b4:
        lines.append("        => STOPS HERE: OPEN bull_put_spread")
        lines.append("   [-] 5. regime == TREND_DOWN?                not reached")
        lines.append("   [-] 6. else (SIDEWAYS / reversal)?          not reached")
        return "\n".join(lines) + _construction_note("bull_put_spread")

    b5 = regime == "TREND_DOWN"
    lines.append(f"   [{'x' if b5 else ' '}] 5. Is regime == TREND_DOWN? -> {b5}")
    if b5:
        lines.append("        => STOPS HERE: OPEN bear_call_spread")
        lines.append("   [-] 6. else (SIDEWAYS / reversal)?          not reached")
        return "\n".join(lines) + _construction_note("bear_call_spread")

    lines.append(f"   [x] 6. else (regime={regime}, neither UP nor DOWN survived) -> True")
    lines.append(f"        => STOPS HERE: STAND_DOWN ({regime.lower()})")
    return "\n".join(lines)


def branch_scores(ind: dict) -> dict:
    """Anti-bias three-way DecisionMaker view. Each branch = a 'permission' the market
    has NOT invalidated:
        NotDown  = up-permission   (bullish thesis survives) → TREND_UP trade
        NotUp    = down-permission (bearish thesis survives) → TREND_DOWN trade
        Sideways = neither survives
    `value` = raw directional mass (family votes that way); `prob` = normalised
    probability from classify_regime. Winner = argmax(prob), matches classify_regime.
    Mapping (NotDown↔up) is one constant — flip if the desk wants the inverse label.
    """
    _, _, probs, votes = classify_regime(ind)
    directional = {k: v for k, v in votes.items() if k != "volatility"}
    bull_names = [FAMILY_NAMES[k] for k, v in directional.items() if v > 0]
    bear_names = [FAMILY_NAMES[k] for k, v in directional.items() if v < 0]
    neutral_names = [FAMILY_NAMES[k] for k, v in directional.items() if v == 0]
    bull, bear, neutral = len(bull_names), len(bear_names), len(neutral_names)
    return {
        "NotDown": {"value": bull, "prob": probs["UP"], "regime": "TREND_UP",
                    "trade": "trending-up (bull put spread)", "families": bull_names},
        "NotUp": {"value": bear, "prob": probs["DOWN"], "regime": "TREND_DOWN",
                  "trade": "trending-down (bear call spread)", "families": bear_names},
        "Sideways": {"value": neutral, "prob": probs["SIDEWAYS"], "regime": "SIDEWAYS",
                     "trade": "no-trade (sideways)", "families": neutral_names},
    }


# ---- FSM entry decision (Phase 1: entry only) --------------------------------

def decide(fsm_state: str, regime: str, conf: float, bar_ts: str) -> tuple[str, str]:
    """(intent, structure). Phase 1 only opens with-trend on confirmed trend.

    EOD cutoff blocks NEW entries the same way it forces existing ones closed
    (check_exit's is_eod) — without this, a position could open right at/after the
    cutoff and get force-closed 1-2 minutes later by the exit check, over and over,
    producing meaningless ~2min "flash" trades whose P&L is tick-timing noise, not a
    real trading outcome. Confirmed live 2026-07-03: 4 such trades in an 8-minute
    window after the entry side had no matching gate."""
    if fsm_state != "FLAT":
        return "SKIP", "single_position_open"          # already in a trade
    if bar_ts[11:16] >= CFG.get("session.eod.cutoff", "15:20"):
        return "STAND_DOWN", "after_eod_cutoff"
    if conf < CFG.get("regime.entry.min_confidence", 0.45):
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
    expiry: str        # persisted so exit-checking re-queries the SAME contract later,
                       # regardless of whether "current week" has since rolled forward
    tsyms: tuple = ()  # Phase 2: real per-leg tradingsymbols, same order as legs.
                       # Empty by default (legacy/no-InstrumentMaster callers unaffected).
    index: str = "NIFTY"  # which underlying these legs are on — needed so exit-checking
                          # queries the right index's option chain (SENSEX != NIFTY grammar)


def build_order(structure: str, snap: Snapshot, im=None) -> PaperOrder | None:
    """`im`: optional real Module 12 (InstrumentMaster, see instrument.py). Omitted =
    legacy Phase-1 behaviour (NIFTY-only STEP/lot constant, no symbols) — every existing
    caller keeps working unchanged. Passed = Phase-2 real path: per-index step/lot from
    the broker master, real round-trip-verified tradingsymbol per leg, works for SENSEX
    too (STEP=50 was NIFTY-only and would have silently mis-sized SENSEX wings)."""
    atm = snap.atm_strike
    index = snap.ind.get("instrument", "NIFTY")
    tsyms = ()
    if im is not None:
        inst = im.resolve(index, snap.spot)
        lot = inst.lot_size
        step = im.step_for(index, inst.expiry)
    else:
        lot = CFG.get("strategy.lot.size", 65)
        step = STEP
    wing = CFG.get("strategy.wing.strikes", 4) * step
    if structure == "bull_put_spread":
        short_k, hedge_k, right = atm, atm - wing, "PE"
    elif structure == "bear_call_spread":
        short_k, hedge_k, right = atm, atm + wing, "CE"
    else:
        return None
    if im is not None:
        hedge_inst = im.lookup(index, inst.expiry, hedge_k, right)
        short_inst = im.lookup(index, inst.expiry, short_k, right)
        if hedge_inst is None or short_inst is None:
            return None                                # SYMBOL_UNRESOLVED — refuse, don't guess
        tsyms = (hedge_inst.tradingsymbol, short_inst.tradingsymbol)
    sp = snap.chain.get((short_k, right))
    hp = snap.chain.get((hedge_k, right))
    if not sp or not hp or sp["ltp"] is None or hp["ltp"] is None:
        return None                                    # premiums unavailable → no fabrication
    short_ltp, hedge_ltp = sp["ltp"], hp["ltp"]
    credit_per = short_ltp - hedge_ltp
    net_credit = round(credit_per * lot, 2)
    max_loss = round((wing - credit_per) * lot, 2)
    # hedge leg placed FIRST (leg-in safety), then the short
    legs = (("BUY", hedge_k, right, hedge_ltp), ("SELL", short_k, right, short_ltp))
    # real path: the legs were resolved against inst.expiry (Module 12's real answer),
    # which can differ from Penguin's own snap.expiry (e.g. stale/unrolled) — the stored
    # expiry MUST match the legs' actual contract, or exit-time price requery targets the
    # wrong tsym token entirely.
    order_expiry = inst.expiry if im is not None else snap.expiry
    return PaperOrder(structure, legs, net_credit, max_loss, lot, order_expiry, tsyms, index)


# ---- exit check: SL / TP / EOD only (minimal slice — no TSL, no morph) --------

@dataclass(frozen=True)
class ExitCheck:
    triggered: bool
    reason: str | None          # "SL" | "TP" | "EOD" | None
    current_pnl: float | None   # None if either leg has no price at all (never abstains
                                 # SL/TP on stale-but-present data — only on missing data)
    hedge_ltp: float | None
    hedge_ltp_ts: str | None
    short_ltp: float | None
    short_ltp_ts: str | None
    sl_threshold: float
    tp_threshold: float
    is_eod: bool


def check_exit(position: dict, reader, bar_ts: str) -> ExitCheck:
    """SL = risk.sl.pct of max_loss lost. TP = risk.tp.pct of net_credit captured.
    EOD = bar time-of-day >= session.eod.cutoff, forces exit regardless of P&L (a stale
    open position must not block the next session's entries — closes at best-available
    price even if that price is a day+ stale; still better than never closing).

    Per-leg prices come from PenguinReader.latest_price_for() — NOT the chain-snapshot's
    ATM-windowed chain, which won't contain a held strike once spot has drifted away, and
    NOT _option_chain()'s shared-max-timestamp filter, which silently drops a leg whose
    last tick is older than the other leg's (confirmed live: deep-OTM hedge legs can go
    a full day+ without a fresh tick)."""
    (_, h_k, h_r, h_entry), (_, s_k, s_r, s_entry) = position["legs"]
    expiry = position["expiry"]
    index = position.get("index", "NIFTY")
    max_loss, net_credit, lot = position["max_loss"], position["net_credit"], position["lot"]
    sl_threshold = -round(CFG.get("risk.sl.pct", 35) / 100.0 * max_loss, 2)
    tp_threshold = round(CFG.get("risk.tp.pct", 50) / 100.0 * net_credit, 2)
    is_eod = bar_ts[11:16] >= CFG.get("session.eod.cutoff", "15:20")

    hedge = reader.latest_price_for(expiry, h_k, h_r, index)
    short = reader.latest_price_for(expiry, s_k, s_r, index)
    hedge_ltp, hedge_ts = hedge if hedge else (None, None)
    short_ltp, short_ts = short if short else (None, None)

    current_pnl = None
    if hedge_ltp is not None and short_ltp is not None:
        entry_credit_per_unit = s_entry - h_entry
        cost_to_close_per_unit = short_ltp - hedge_ltp
        current_pnl = round((entry_credit_per_unit - cost_to_close_per_unit) * lot, 2)

    def _result(triggered: bool, reason: str | None) -> ExitCheck:
        return ExitCheck(triggered, reason, current_pnl, hedge_ltp, hedge_ts,
                         short_ltp, short_ts, sl_threshold, tp_threshold, is_eod)

    if is_eod:
        return _result(True, "EOD")
    if current_pnl is not None:
        if current_pnl <= sl_threshold:
            return _result(True, "SL")
        if current_pnl >= tp_threshold:
            return _result(True, "TP")
    return _result(False, None)


# ---- the cycle (pure): state + snapshot -> new_state, decision, paper_order ---

def cycle(fsm_state: str, snap: Snapshot, im=None) -> tuple[str, dict, PaperOrder | None]:
    regime, conf, probs, votes = classify_regime(snap.ind)
    intent, structure = decide(fsm_state, regime, conf, snap.ts)
    order = build_order(structure, snap, im) if intent == "OPEN" else None
    new_state = "SINGLE_SPREAD" if order else fsm_state
    if intent == "OPEN" and order is None:
        intent, structure = "STAND_DOWN", "premiums_unavailable"
    decision = {"regime": regime, "confidence": conf, "probs": probs, "votes": votes,
                "intent": intent, "structure": structure}
    return new_state, decision, order
