"""ATOM-Lights — deterministic candle-color logic + AND-gate combine (crafted candles)."""
from atom import lights
from atom.lights import LightsResult


def _c(o, cl, hi=None, lo=None):
    hi = hi if hi is not None else max(o, cl) + 1
    lo = lo if lo is not None else min(o, cl) - 1
    return {"open": o, "close": cl, "high": hi, "low": lo}


def test_candle_color_body_filter():
    assert lights._candle_color(_c(100, 110), 0.15) == "GREEN"
    assert lights._candle_color(_c(110, 100), 0.15) == "RED"
    # tiny body within a big range → AMBER (indecisive)
    assert lights._candle_color(_c(100, 100.1, hi=110, lo=90), 0.15) == "AMBER"


def test_light_amber_on_disagreement():
    # current GREEN, previous RED → handover → AMBER
    assert lights.light_for([_c(100, 110), _c(110, 100)], 0.15) == "AMBER"
    # both GREEN → GREEN
    assert lights.light_for([_c(100, 110), _c(90, 100)], 0.15) == "GREEN"


def test_permission_routing():
    assert lights._permission("GREEN") == "PUT_CREDIT_SPREAD"
    assert lights._permission("RED") == "CALL_CREDIT_SPREAD"
    assert lights._permission("AMBER") == "IRON_FLY"


def test_trigger_requires_pullback_then_resumption():
    # bullish: prev 5m RED (dip) then cur 5m GREEN (resume) → trigger
    assert lights._trigger(["GREEN", "RED"], "GREEN", "PUT_CREDIT_SPREAD") is True
    # still falling (cur RED) → no trigger (no anticipation)
    assert lights._trigger(["RED", "RED"], "RED", "PUT_CREDIT_SPREAD") is False


def _res(perm, trigger=True, size="FULL", gap="GAP_NEUTRAL", c60="GREEN"):
    lt = {"5m": "GREEN", "15m": "GREEN", "30m": "GREEN", "60m": c60,
          "240m": "GREEN", "1D": "GREEN"}
    return LightsResult(lt, gap, perm, size, trigger, 23800.0)


def test_and_gate_pass_when_family_and_lights_agree():
    sh = lights.shadow_entry(_res("PUT_CREDIT_SPREAD"), "TREND_UP")
    assert sh["enter"] and sh["instrument"] == "PUT_CREDIT_SPREAD"


def test_and_gate_blocks_on_family_disagreement():
    sh = lights.shadow_entry(_res("PUT_CREDIT_SPREAD"), "TREND_DOWN")
    assert not sh["enter"] and "≠" in sh["reason"]


def test_and_gate_blocks_without_trigger():
    sh = lights.shadow_entry(_res("CALL_CREDIT_SPREAD", trigger=False), "TREND_DOWN")
    assert not sh["enter"] and "trigger" in sh["reason"]


def test_amber_routes_iron_fly():
    sh = lights.shadow_entry(_res("IRON_FLY", c60="AMBER"), "SIDEWAYS")
    assert sh["enter"] and sh["instrument"] == "IRON_FLY"


def test_time_gate():
    lights.configure({"lights.time_gate": "10:15"})
    assert lights.time_ok("2026-06-30T10:20:00") is True
    assert lights.time_ok("2026-06-30T09:45:00") is False
    lights.configure(dict(__import__("atom.config", fromlist=["DEFAULTS"]).DEFAULTS))
