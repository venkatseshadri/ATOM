#!/usr/bin/env python3
"""Run ONE Phase-1 cycle against live (or fixture) Penguin NIFTY and print the result.

Usage:
  python3 run_live_once.py            # live capture_nifty.sqlite
  python3 run_live_once.py --fixture  # deterministic test fixture
"""
import sys

sys.path.insert(0, "src")

from datetime import datetime   # noqa: E402

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
        state = AtomState("data/atom_state_fixture.sqlite")
        state.reset()
        snap = reader.latest_snapshot()
        now = datetime.fromisoformat(snap.ts)
        max_stale = 1e9
    else:
        state = AtomState("data/atom_state.sqlite")

    print(f"=== ATOM Phase 1 — one cycle ({'fixture' if fixture else 'LIVE'}) ===")
    r = run_once(reader, state, now=now, max_stale_sec=max_stale)

    print(f"\nSCOUT  bar {r.get('bar_ts')}  spot ₹{r.get('spot')}  ATM {r.get('atm')}  "
          f"expiry {r.get('expiry')}")
    if "votes" in r:
        print(f"       regime={r['regime']} conf={r['confidence']}  votes={r['votes']}")
    print(f"DECIDE intent={r['action']}  ({r.get('structure')})  fsm={r['fsm_state']}")
    order = r.get("order")
    if order:
        print("ORDER  (PAPER — no broker):")
        for action, strike, right, ltp in order.legs:
            print(f"         {action} {strike}{right} @ ₹{ltp}")
        print(f"       net credit ₹{order.net_credit:,.0f}  max loss ₹{order.max_loss:,.0f}  "
              f"lot {order.lot}")
    else:
        print(f"       no order ({r.get('reason', r.get('structure'))})")


if __name__ == "__main__":
    main()
