"""Phase 2 acceptance tests (BUILD_PLAN / phases/phase-2-strike-structure/testcases.md).

T2.1 expiry resolution (incl. holiday-shift, no weekday-guessing) · T2.2 symbol format
(NIFTY vs SENSEX, round-trip) · T2.4 plan completeness · T2.5 liquidity/missing-data guard.
Against `tests/fixtures/scrip_master_fixture.duckdb` — a small frozen snapshot of the real
antariksh scrip_master schema, not the live daily-changing DB (deterministic tests). It
carries two non-overlapping SENSEX windows: a past synthetic holiday-shift week (tested
with an explicit `now=`) and the real week matching `capture_sensex_fixture.sqlite`
(tested with `now=None`, i.e. today).

T2.3 (greek-driven strikes) is N/A: Penguin's real option_prices table has no per-strike
delta/IV (only ltp/oi) — see penguin.py. Module 4 here uses the distance-offset method
(4.3.3.2), the honest fallback per the module's own fallback-chain spec (4.3.3.4), not a
fabricated greek.
"""
import os
from datetime import date, datetime

from atom import phase1
from atom.instrument import InstrumentMaster
from atom.penguin import PenguinReader, Snapshot
from atom.telemetry import Telemetry

FIX_NIFTY = os.path.join(os.path.dirname(__file__), "fixtures", "capture_nifty_fixture.sqlite")
FIX_SENSEX = os.path.join(os.path.dirname(__file__), "fixtures", "capture_sensex_fixture.sqlite")
FIX_MASTER = os.path.join(os.path.dirname(__file__), "fixtures", "scrip_master_fixture.duckdb")
NOW = date(2026, 7, 6)                  # picks the NIFTY window (07-Jul/14-Jul)
SHIFT_NOW = date(2026, 6, 23)           # picks the synthetic SENSEX holiday-shift window


def _im():
    return InstrumentMaster(Telemetry(echo=False), db_path=FIX_MASTER)


# ---- T2.1 Expiry resolution --------------------------------------------------

def test_nifty_expiry_is_nearest_listed():
    inst = _im().resolve("NIFTY", 24512, now=NOW)
    assert inst.expiry == "07-JUL-2026"


def test_sensex_expiry_is_holiday_shifted_not_calendar_guessed():
    """Fixture lists this week on Wednesday (24-Jun), not the nominal Thursday (25-Jun) —
    proves resolve() reads the master's actual listed date, never its own weekday
    rule (it has none)."""
    inst = _im().resolve("SENSEX", 75830, now=SHIFT_NOW)
    assert inst.expiry == "24-JUN-2026"


def test_expiry_advances_past_current_week():
    inst = _im().resolve("NIFTY", 24512, now=date(2026, 7, 8))  # after the 07-Jul expiry
    assert inst.expiry == "14-JUL-2026"


def test_no_listed_expiry_fails_closed():
    im = _im()
    try:
        im.resolve("NIFTY", 24512, now=date(2031, 1, 1))
        assert False, "should have raised — no contracts listed that far out"
    except ValueError as e:
        assert "NO_LISTED_EXPIRY" in str(e)


# ---- T2.2 Symbol format (NIFTY vs SENSEX, round-trip) ------------------------

def test_nifty_symbol_format_and_roundtrip():
    im = _im()
    inst = im.resolve("NIFTY", 24512, now=NOW)
    assert inst.tradingsymbol == "NIFTY07JUL26P24500"
    # round-trip: the emitted symbol must re-resolve to the SAME contract
    back = im.lookup("NIFTY", inst.expiry, inst.strike, inst.right)
    assert back is not None and back.tradingsymbol == inst.tradingsymbol


def test_sensex_symbol_format_and_roundtrip():
    """SENSEX grammar is YY+M+DD+strike+CE/PE — completely different from NIFTY's
    DD+Mon+YY+C/P+strike (fix_sensex_option_symbols.md)."""
    im = _im()
    inst = im.resolve("SENSEX", 75830, now=SHIFT_NOW)
    assert inst.tradingsymbol == "SENSEX2662475800PE"
    back = im.lookup("SENSEX", inst.expiry, inst.strike, inst.right)
    assert back is not None and back.tradingsymbol == inst.tradingsymbol


def test_nifty_and_sensex_formats_are_not_interchangeable():
    """Applying the wrong index's grammar must not silently resolve — the exact bug
    class in fix_sensex_option_symbols.md."""
    im = _im()
    assert im.lookup("SENSEX", "07-JUL-2026", 24500, "PE") is None
    assert im.lookup("NIFTY", "24-JUN-2026", 75800, "PE") is None


def test_lot_and_step_are_real_and_differ_per_index():
    im = _im()
    nifty = im.resolve("NIFTY", 24512, now=NOW)
    sensex = im.resolve("SENSEX", 75830, now=SHIFT_NOW)
    assert nifty.lot_size == 65 and im.step_for("NIFTY", nifty.expiry) == 50
    assert sensex.lot_size == 20 and im.step_for("SENSEX", sensex.expiry) == 100


# ---- T2.4 Plan completeness (build_order, real path) -------------------------

def test_build_order_real_path_nifty_has_real_symbols_and_lot():
    snap = PenguinReader(FIX_NIFTY).latest_snapshot()
    o = phase1.build_order("bear_call_spread", snap, im=_im())
    assert o is not None
    assert o.index == "NIFTY"
    assert o.lot == 65                       # real, not the Phase-1 CFG default
    assert len(o.tsyms) == 2
    assert all(t.startswith("NIFTY") for t in o.tsyms)
    assert o.net_credit != 0 and o.max_loss > 0
    # the position's stored expiry must match the legs it actually resolved against
    # (Module 12's real answer) — proven by re-looking-up a leg via o.expiry and
    # confirming it round-trips to the same symbol build_order emitted
    back = _im().lookup(o.index, o.expiry, o.legs[0][1], o.legs[0][2])
    assert back is not None and back.tradingsymbol == o.tsyms[0]


def test_build_order_real_path_sensex_uses_real_step_and_symbols():
    """Real SENSEX capture fixture (capture_sensex_fixture.sqlite, a 1hr excerpt of
    the live capture_sensex.sqlite) + real scrip_master row for the SAME week —
    proves build_order no longer hardcodes STEP=50 (NIFTY-only) end-to-end on real
    captured option premiums, not a hand-built snapshot."""
    snap = PenguinReader(FIX_SENSEX).latest_snapshot()
    assert snap.ind.get("instrument") == "SENSEX"
    o = phase1.build_order("bear_call_spread", snap, im=_im())
    assert o is not None
    assert o.index == "SENSEX"
    assert o.lot == 20
    assert all(t.startswith("SENSEX") for t in o.tsyms)
    assert o.net_credit != 0
    back = _im().lookup(o.index, o.expiry, o.legs[0][1], o.legs[0][2])
    assert back is not None and back.tradingsymbol == o.tsyms[0]


# ---- T2.5 Liquidity / missing-data guard -------------------------------------

def test_build_order_refuses_when_leg_symbol_unresolved():
    """Strike walks off the fixture's listed ladder — Module 4 must refuse
    (SYMBOL_UNRESOLVED), never emit a plan with a guessed/unresolved leg."""
    ind = {"instrument": "SENSEX"}
    chain = {(75800, "CE"): {"ltp": 210.0, "oi": 100},
             (99900, "CE"): {"ltp": 1.0, "oi": 100}}
    snap = Snapshot(ts="2026-06-23T10:00:00", spot=75830, atm_strike=75800,
                    expiry="24-JUN-2026", days_to_expiry=1, ind=ind, chain=chain)
    # force a wing far outside the listed ladder by using an extreme wing config
    phase1.CFG["strategy.wing.strikes"] = 241  # 241*100 -> strike not listed
    try:
        assert phase1.build_order("bear_call_spread", snap, im=_im()) is None
    finally:
        phase1.CFG["strategy.wing.strikes"] = phase1.config.DEFAULTS["strategy.wing.strikes"]


# ---- Day-of-week index routing (0-1 DTE rule, Board 2026-07-05) -------------

def test_index_routing_fri_mon_tue_is_nifty():
    for iso in ("2026-07-10", "2026-07-06", "2026-07-07"):  # Fri, Mon, Tue
        assert phase1.index_for_weekday(datetime.fromisoformat(iso)) == "NIFTY"


def test_index_routing_wed_thu_is_sensex():
    for iso in ("2026-07-08", "2026-07-09"):  # Wed, Thu
        assert phase1.index_for_weekday(datetime.fromisoformat(iso)) == "SENSEX"


def test_legacy_callers_without_instrumentmaster_unaffected():
    """No `im` passed = old Phase-1 behaviour, byte-identical to before Phase 2."""
    snap = PenguinReader(FIX_NIFTY).latest_snapshot()
    o = phase1.build_order("bear_call_spread", snap)
    assert o is not None
    assert o.tsyms == ()
    assert o.index == "NIFTY"
    assert o.expiry == snap.expiry           # legacy path: no Module 12 re-resolution
    assert o.lot == phase1.CFG.get("strategy.lot.size", 65)
