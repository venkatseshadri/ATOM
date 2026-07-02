"""ATOM-Lights — multi-timeframe candle-color entry layer (SHADOW).

A SECOND entry layer on top of the 7-family regime (it does not replace indicators).
Reads Penguin `market_data_multitf` (5/15/30/60/240/1440) + enriched gap/swing — does not
recompute candles (don't duplicate Penguin).

Fixes applied to the v1 spec (see ATOMLightsEntrySpec §2/§6):
- **A (rule clarity):** dropped the clock-based <50% handover. Deterministic:
  colour each candle by close-vs-open with a **body filter** (B); a timeframe is AMBER
  when the current and previous candle disagree (handover/chop).
- **B (noise):** body must be ≥ `lights.body.min_frac` of the candle range, else AMBER.
- **C (resumption):** explicit trigger — pullback = last 5m/15m RED; resumption =
  current 5m re-GREEN (close). 0DTE identical (confirmed re-GREEN close).
- **D (240m bucketing):** consumed straight from Penguin's multitf (Penguin owns bucketing).
"""
from __future__ import annotations

from dataclasses import dataclass

from . import config
from .penguin import PenguinReader, _f

TIMEFRAMES = [5, 15, 30, 60, 240, 1440]
TF_NAME = {5: "5m", 15: "15m", 30: "30m", 60: "60m", 240: "240m", 1440: "1D"}

CFG = dict(config.DEFAULTS)


def configure(cfg: dict) -> None:
    CFG.clear()
    CFG.update(cfg)


def time_ok(bar_ts: str) -> bool:
    """bar time-of-day >= lights.time_gate (no entries before 10:15 IST)."""
    gate = str(CFG.get("lights.time_gate", "10:15"))
    hhmm = bar_ts[11:16] if len(bar_ts) >= 16 else "00:00"
    return hhmm >= gate


def shadow_entry(res: "LightsResult", fam_dir: str) -> dict:
    """AND-gate combine of 7-family direction + Lights (SHADOW candidate only)."""
    perm = res.permission
    if perm == "IRON_FLY":
        return {"enter": True, "instrument": "IRON_FLY",
                "size": res.size if res.size != "SKIP" else "MIN",
                "reason": "60m AMBER → neutral (iron fly)"}
    want = "TREND_UP" if perm == "PUT_CREDIT_SPREAD" else "TREND_DOWN"
    if fam_dir != want:
        return {"enter": False, "reason": f"7-family {fam_dir} ≠ 60m {perm}"}
    if not res.trigger:
        return {"enter": False, "reason": "no pullback→resumption trigger"}
    if res.size == "SKIP":
        return {"enter": False, "reason": "conviction SKIP"}
    return {"enter": True, "instrument": perm, "size": res.size, "reason": "AND-gate pass"}


def _candle_color(c: dict, body_min_frac: float) -> str:
    o, cl, hi, lo = c["open"], c["close"], c["high"], c["low"]
    if None in (o, cl, hi, lo):
        return "AMBER"
    rng = (hi - lo) or 1e-9
    if abs(cl - o) < body_min_frac * rng:        # indecisive body → no colour (B)
        return "AMBER"
    return "GREEN" if cl >= o else "RED"


def light_for(candles: list[dict], body_min_frac: float) -> str:
    """candles newest-first; AMBER if current vs previous disagree (A)."""
    if not candles:
        return "AMBER"
    cur = _candle_color(candles[0], body_min_frac)
    prev = _candle_color(candles[1], body_min_frac) if len(candles) > 1 else cur
    if cur == "AMBER" or prev == "AMBER":
        return "AMBER"
    return cur if cur == prev else "AMBER"


def gap_state(ind: dict, lights: dict) -> str:
    gp = _f(ind.get("gap_pct")) or 0.0
    thr = CFG.get("lights.gap.threshold_pct", 0.3)
    if gp >= thr:
        # gapped up; faded if short TFs already flipped RED
        return "GAP_UP_FADED" if "RED" in (lights["5m"], lights["15m"]) else "GAP_UP_HELD"
    if gp <= -thr:
        return "GAP_DOWN_IN_TREND" if lights["60m"] == "GREEN" else "GAP_NEUTRAL"
    return "GAP_NEUTRAL"


@dataclass(frozen=True)
class LightsResult:
    lights: dict          # {'5m':'GREEN',...}
    gap: str
    permission: str       # PUT_CREDIT_SPREAD | CALL_CREDIT_SPREAD | IRON_FLY
    size: str             # FULL | MIN | SKIP
    trigger: bool         # pullback→resumption present
    pullback_swing_low: float | None
    conviction_note: str | None = None   # log-only caveat, does not change size/gating


def _permission(c60: str) -> str:
    return {"GREEN": "PUT_CREDIT_SPREAD", "RED": "CALL_CREDIT_SPREAD"}.get(c60, "IRON_FLY")


def _conviction(c240: str, c1d: str, dte: int) -> str:
    """FULL needs a clean, unanimous read. On dte==0 there's no time for the daily
    candle to matter, so conviction is c240 alone — checked once, not counted twice
    dressed up as '2 signals agreed' (that was the old bug: same output, but the
    greens==2 check made it look like an independent second confirmation existed)."""
    if dte == 0:
        if c240 == "RED":
            return "MIN"
        return "FULL" if c240 == "GREEN" else "MIN"
    if c240 == "RED" or c1d == "RED":
        return "MIN"
    return "FULL" if c240 == "GREEN" and c1d == "GREEN" else "MIN"


def _trigger(c5_hist: list[str], c15: str, permission: str) -> bool:
    """pullback (prior 5m/15m against trend) + resumption (current 5m re-aligned)."""
    if len(c5_hist) < 2:
        return False
    cur5, prev5 = c5_hist[0], c5_hist[1]
    if permission == "PUT_CREDIT_SPREAD":        # bullish: dip RED then re-GREEN
        pullback = prev5 == "RED" or c15 == "RED"
        return pullback and cur5 == "GREEN"
    if permission == "CALL_CREDIT_SPREAD":       # bearish: pop GREEN then re-RED
        pullback = prev5 == "GREEN" or c15 == "GREEN"
        return pullback and cur5 == "RED"
    return False                                 # iron fly handled separately


def evaluate(reader: PenguinReader, ind: dict, dte: int) -> LightsResult:
    bmf = CFG.get("lights.body.min_frac", 0.15)
    cnd = {tf: reader.multitf(tf, 3) for tf in TIMEFRAMES}
    lights = {TF_NAME[tf]: light_for(cnd[tf], bmf) for tf in TIMEFRAMES}
    gap = gap_state(ind, lights)
    perm = _permission(lights["60m"])
    size = _conviction(lights["240m"], lights["1D"], dte)
    c5_hist = [_candle_color(c, bmf) for c in cnd[5][:2]]
    trig = _trigger(c5_hist, lights["15m"], perm)
    if gap == "GAP_UP_FADED":                    # exhaustion veto
        trig = False
    swing = _f(ind.get("swing_low"))
    note = _conviction_note(cnd.get(240, []))
    return LightsResult(lights, gap, perm, size, trig, swing, note)


def _conviction_note(c240: list[dict]) -> str | None:
    """NIFTY's ~6.25hr session fits well under two 240m buckets, so for most of every
    session the 'current vs previous 240m candle' comparison that feeds CONVICTION
    sizing is actually comparing today against YESTERDAY's candle, not an intraday
    signal. Log-only flag — does not change size/gating (Board discussion 2026-07-02)."""
    if len(c240) < 2:
        return None
    cur_ts, prev_ts = c240[0].get("ts"), c240[1].get("ts")
    if not cur_ts or not prev_ts:
        return None
    if cur_ts[:10] != prev_ts[:10]:
        return (f"240m conviction candle is comparing TODAY ({cur_ts[:10]}) vs "
                f"YESTERDAY ({prev_ts[:10]}) — NIFTY's session is too short for two "
                "same-day 240m candles this early; conviction size is a multi-day "
                "filter right now, not intraday")
    return None
