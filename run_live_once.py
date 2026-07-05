#!/usr/bin/env python3
"""Run ONE Phase-1/2 cycle against live (or fixture) Penguin and print the result.

Index is chosen by the day-of-week 0-1 DTE rule (Board, 2026-07-05): NIFTY's weekly
expires Tuesday (Fri/Mon/Tue = 0-1 DTE), SENSEX's expires Thursday (Wed/Thu = 0-1 DTE).
Real per-index step/lot/symbol come from Module 12 (InstrumentMaster) on every cycle,
for both indices — not just an option, the live default now.

Usage:
  python3 run_live_once.py              # live capture DB for today's index
  python3 run_live_once.py --fixture     # deterministic test fixture, same index rule
  python3 run_live_once.py --index NIFTY # force an index (override the weekday rule)
"""
import sys

sys.path.insert(0, "src")

from datetime import datetime   # noqa: E402

from atom import config, lights, phase1       # noqa: E402
from atom.atom_state import AtomState      # noqa: E402
from atom.instrument import InstrumentMaster  # noqa: E402
from atom.penguin import PenguinReader     # noqa: E402
from atom.runner import run_once           # noqa: E402
from atom.telemetry import Telemetry       # noqa: E402

INDEX_CONFIG = {
    "NIFTY": {
        "live_db": "/home/trading_ceo/python-trader/varaha/data/capture_nifty.sqlite",
        "fixture_db": "tests/fixtures/capture_nifty_fixture.sqlite",
        "state": "data/atom_state.sqlite",              # unchanged path — live NIFTY history
        "state_fixture": "data/atom_state_fixture.sqlite",
    },
    "SENSEX": {
        "live_db": "/home/trading_ceo/python-trader/varaha/data/capture_sensex.sqlite",
        "fixture_db": "tests/fixtures/capture_sensex_fixture.sqlite",
        "state": "data/atom_state_sensex.sqlite",       # separate FSM/position state
        "state_fixture": "data/atom_state_sensex_fixture.sqlite",
    },
}


def _forced_index() -> str | None:
    if "--index" in sys.argv:
        return sys.argv[sys.argv.index("--index") + 1].upper()
    return None


def main() -> None:
    fixture = "--fixture" in sys.argv
    index = _forced_index() or phase1.index_for_weekday()
    icfg = INDEX_CONFIG[index]
    db = icfg["fixture_db"] if fixture else icfg["live_db"]
    reader = PenguinReader(db)
    im = InstrumentMaster(Telemetry(echo=False))
    # fixture is off-hours → its own fresh state + pin 'now' to the bar so it's "fresh"
    now = None
    max_stale = 90.0
    if fixture:
        # deterministic demo path stays pinned to code defaults, not live-tunable config
        state = AtomState(icfg["state_fixture"])
        state.reset()
        snap = reader.latest_snapshot()
        now = datetime.fromisoformat(snap.ts)
        max_stale = 1e9
    else:
        # config/atom.conf was never actually wired to the live path before this —
        # editing it had zero effect. Reload fresh every cycle (new process each cron
        # tick anyway) so research-loop tuning takes effect within one minute.
        cfg = config.load_config()
        phase1.configure(cfg)
        lights.configure(cfg)
        state = AtomState(icfg["state"])

    print(f"=== ATOM Phase 1/2 — one cycle ({'fixture' if fixture else 'LIVE'}, {index}) ===")
    r = run_once(reader, state, now=now, max_stale_sec=max_stale, im=im)

    if r["action"] == "EXIT":
        ec, pos = r["exit_check"], r["position"]
        RULE = "=" * 78
        print(f"\n{RULE}")
        print(f"EXIT — {r['reason']}  bar={r['bar_ts']}  fsm now=FLAT")
        print(RULE)
        legs_str = "; ".join(f"{a} {k}{rt} @₹{p}" for a, k, rt, p in pos["legs"])
        print(f"Closing: {pos['structure']}  (opened bar {pos['bar_ts']})")
        print(f"  entry: {legs_str}  |  net credit ₹{pos['net_credit']:,.0f}  "
              f"max loss ₹{pos['max_loss']:,.0f}")
        print(f"  exit prices: hedge={ec.hedge_ltp} (as of {ec.hedge_ltp_ts})  "
              f"short={ec.short_ltp} (as of {ec.short_ltp_ts})")
        print(f"  SL threshold=₹{ec.sl_threshold:,.0f}  TP threshold=₹{ec.tp_threshold:,.0f}  "
              f"is_eod={ec.is_eod}")
        print(f"  => realized P&L = ₹{ec.current_pnl:,.0f}" if ec.current_pnl is not None
              else "  => realized P&L unknown (forced EOD close with no price data)")
        print(RULE)
        return

    if "explain" not in r:
        # early exit before a real cycle ran — no_data / no_new_bar / stale_feed
        age = f"  age={r['age_sec']}s (max {max_stale}s)" if "age_sec" in r else ""
        print(f"\n{r['action']} ({r.get('reason')})  bar={r.get('bar_ts')}  fsm={r['fsm_state']}{age}")
        return

    RULE = "=" * 78
    ex, ind, basis, lg = r["explain"], r["indicators"], r["basis"], r.get("lights")

    print(f"\n{RULE}")
    print(f"bar {r['bar_ts']}  —  {index} spot ₹{r['spot']}  ATM {r['atm']}  expiry {r['expiry']}")
    print(RULE)

    print("1) INDICATORS  (read from Penguin, real market data — ATOM does not recompute)")
    print(f"   EMA20-slope={ind['ema20_slope']}  RSI-14={ind['rsi']}  ADX={ind['adx']}  "
          f"IndiaVIX={ind['india_vix']}")
    print(f"   SuperTrend  5m={ind['st_5min_direction']}  15m={ind['st_15min_direction']}  "
          f"-> consensus={ind['st_consensus']}  "
          "(15m is primary and wins outright on disagreement; 5m used only if 15m missing)")
    print(f"   Structure={ind['structure_type']}  PCR-total={ind['pcr_total']}  "
          f"sentiment={ind['sentiment']}  VWAP={ind['vwap']}  spot={ind['spot']}")

    print("\n2) 7-FAMILY VOTE  (raw value -> threshold -> vote)")
    for line in ex["votes"]:
        print(line)

    print("\n3) REGIME  (turn the vote count into UP/DOWN/SIDEWAYS probabilities)")
    print(ex["regime"])
    print(f"  => Winner (highest probability): regime={r['regime']}  confidence={r['confidence']}")

    print("\n4) ANTI-BIAS CROSS-CHECK  (NotUp/NotDown/Sideways — logged only, does not gate)")
    print(f"   NotDown (bull thesis survives) = {basis['NotDown']['value']} votes / "
          f"{basis['NotDown']['prob'] * 100:.1f}%  [{', '.join(basis['NotDown']['families']) or 'none'}]")
    print(f"   NotUp   (bear thesis survives) = {basis['NotUp']['value']} votes / "
          f"{basis['NotUp']['prob'] * 100:.1f}%  [{', '.join(basis['NotUp']['families']) or 'none'}]")
    print(f"   Sideways(neither survives)     = {basis['Sideways']['value']} votes / "
          f"{basis['Sideways']['prob'] * 100:.1f}%  [{', '.join(basis['Sideways']['families']) or 'none'}]")

    print("\n5) ATOM-LIGHTS SHADOW  (multi-timeframe candle colour — logged only, does not gate)")
    if lg:
        print(f"   {lg['lights']}  gap={lg['gap']}")
        print(f"   60m-permission={lg['permission']}  size={lg['size']}  "
              f"pullback-trigger={lg['trigger']}")
        print(f"   shadow-would-enter={lg['shadow_enter']}  ({lg['shadow_reason']})")
        if lg.get("conviction_note"):
            print(f"   ⚠ CAVEAT: {lg['conviction_note']}")
    else:
        print("   unavailable this cycle")

    print("\n6) FSM ENTRY GATE  (Finite State Machine — tracks whether ATOM currently holds a position)")
    print("   States: FLAT (no position, can enter) -> SINGLE_SPREAD (one credit spread open)")
    print("           [IRON_FLY / RUNNER exist in the design doc but Phase 1 doesn't build them yet]")
    print(f"   Current state: {r['fsm_state']}  ({ex['fsm_meaning']})")
    pos = ex.get("open_position")
    if pos:
        legs_str = "; ".join(f"{a} {k}{rt} @₹{p}" for a, k, rt, p in pos["legs"])
        print(f"   Position blocking new entries: {pos['structure']}  (opened bar {pos['bar_ts']})")
        print(f"     {legs_str}  |  net credit ₹{pos['net_credit']:,.0f}  max loss ₹{pos['max_loss']:,.0f}")
        ec = ex.get("exit_check")
        if ec:
            pnl_str = f"₹{ec.current_pnl:,.0f}" if ec.current_pnl is not None else "unknown (missing leg price)"
            print(f"     live monitor: P&L={pnl_str}  SL@₹{ec.sl_threshold:,.0f}  "
                  f"TP@₹{ec.tp_threshold:,.0f}  is_eod={ec.is_eod}  "
                  f"hedge={ec.hedge_ltp}({ec.hedge_ltp_ts})  short={ec.short_ltp}({ec.short_ltp_ts})")
    print(ex["decision"])

    print(f"\n{RULE}")
    order = r.get("order")
    if order:
        print("RESULT: OPEN — paper order placed (PAPER — no broker)")
        for action, strike, right, ltp in order.legs:
            print(f"   {action} {strike}{right} @ ₹{ltp}")
        print(f"   net credit ₹{order.net_credit:,.0f}  max loss ₹{order.max_loss:,.0f}  "
              f"lot {order.lot}")
        if not fixture:
            _append_order(r, order)
    else:
        print(f"RESULT: {r['action']} ({r.get('structure')})  fsm now={r['fsm_state']}  "
              "— no order placed")
    print(RULE)


def _append_order(r: dict, order) -> None:
    """One row per attempted paper order → logs/atom_orders.csv (cumulative, for
    Phase-1 validation). Live only; fixture runs don't pollute it."""
    import csv
    import os

    path = "logs/atom_orders.csv"
    os.makedirs("logs", exist_ok=True)
    (h_act, h_k, h_r, h_ltp), (s_act, s_k, s_r, s_ltp) = order.legs
    row = {
        "placed_at": datetime.now().isoformat(timespec="seconds"),
        "bar_ts": r["bar_ts"], "structure": order.structure,
        "regime": r["regime"], "conf": r["confidence"],
        "hedge": f"{h_act} {h_k}{h_r} @{h_ltp}", "short": f"{s_act} {s_k}{s_r} @{s_ltp}",
        "net_credit": order.net_credit, "max_loss": order.max_loss, "lot": order.lot,
    }
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


if __name__ == "__main__":
    main()
