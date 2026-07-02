#!/usr/bin/env python3
"""Run ONE Phase-1 cycle against live (or fixture) Penguin NIFTY and print the result.

Usage:
  python3 run_live_once.py            # live capture_nifty.sqlite
  python3 run_live_once.py --fixture  # deterministic test fixture
"""
import sys

sys.path.insert(0, "src")

from datetime import datetime   # noqa: E402

from atom import config, lights, phase1       # noqa: E402
from atom.atom_state import AtomState      # noqa: E402
from atom.penguin import PenguinReader     # noqa: E402
from atom.runner import run_once           # noqa: E402

LIVE = "/home/trading_ceo/python-trader/varaha/data/capture_nifty.sqlite"
FIXTURE = "tests/fixtures/capture_nifty_fixture.sqlite"


def main() -> None:
    fixture = "--fixture" in sys.argv
    db = FIXTURE if fixture else LIVE
    reader = PenguinReader(db)
    # fixture is off-hours → its own fresh state + pin 'now' to the bar so it's "fresh"
    now = None
    max_stale = 90.0
    if fixture:
        # deterministic demo path stays pinned to code defaults, not live-tunable config
        state = AtomState("data/atom_state_fixture.sqlite")
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
        state = AtomState("data/atom_state.sqlite")

    print(f"=== ATOM Phase 1 — one cycle ({'fixture' if fixture else 'LIVE'}) ===")
    r = run_once(reader, state, now=now, max_stale_sec=max_stale)

    if "explain" not in r:
        # early exit before a real cycle ran — no_data / no_new_bar / stale_feed
        age = f"  age={r['age_sec']}s (max {max_stale}s)" if "age_sec" in r else ""
        print(f"\n{r['action']} ({r.get('reason')})  bar={r.get('bar_ts')}  fsm={r['fsm_state']}{age}")
        return

    RULE = "=" * 78
    ex, ind, basis, lg = r["explain"], r["indicators"], r["basis"], r.get("lights")

    print(f"\n{RULE}")
    print(f"bar {r['bar_ts']}  —  NIFTY spot ₹{r['spot']}  ATM {r['atm']}  expiry {r['expiry']}")
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
