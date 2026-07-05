#!/usr/bin/env python3
"""Minimal REPLAY driver — proof of concept for backtesting phase1's decision logic
against real historical Penguin data. Every price lookup is asof-bounded (<= the
simulated bar's own timestamp) — no look-ahead. Simulated fsm/position state is fully
isolated from the live atom_state.sqlite; this never touches it.

Usage: python3 replay.py --start 2026-07-01 --end 2026-07-03
"""
import argparse
import sys

sys.path.insert(0, "src")

from atom import phase1               # noqa: E402
from atom.penguin import PenguinReader  # noqa: E402

LIVE = "/home/trading_ceo/python-trader/varaha/data/capture_nifty.sqlite"


class _AsofReader:
    """Redirects check_exit()'s price lookups to the asof-bounded variant, pinned to
    the replay's simulated clock instead of the real live moment."""
    def __init__(self, inner: PenguinReader):
        self._inner = inner
        self.as_of_ts = None

    def latest_price_for(self, expiry, strike, option_type):
        return self._inner.latest_price_for_asof(expiry, strike, option_type, self.as_of_ts)


def replay(start: str, end: str) -> list[dict]:
    reader = PenguinReader(LIVE)
    snaps = reader.historical_snapshots(start, end)
    asof_reader = _AsofReader(reader)

    fsm_state = "FLAT"
    open_position = None
    trades = []

    for snap in snaps:
        asof_reader.as_of_ts = snap.ts

        if fsm_state == "SINGLE_SPREAD" and open_position is not None:
            ec = phase1.check_exit(open_position, asof_reader, snap.ts)
            if ec.triggered:
                open_position.update(exit_ts=snap.ts, exit_reason=ec.reason,
                                      realized_pnl=ec.current_pnl)
                trades.append(open_position)
                open_position, fsm_state = None, "FLAT"
                continue    # don't also evaluate a new entry on the same bar

        new_state, decision, order = phase1.cycle(fsm_state, snap)
        fsm_state = new_state
        if order is not None:
            open_position = {
                "ts": snap.ts, "structure": order.structure, "net_credit": order.net_credit,
                "max_loss": order.max_loss, "lot": order.lot, "legs": order.legs,
                "expiry": order.expiry, "exit_ts": None, "exit_reason": None,
                "realized_pnl": None,
            }

    return trades


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    args = ap.parse_args()

    trades = replay(f"{args.start}T00:00:00", f"{args.end}T23:59:59")

    print(f"=== ATOM REPLAY — {args.start} to {args.end} (simulated, asof-bounded) ===\n")
    total = 0.0
    for t in trades:
        legs_str = "; ".join(f"{a} {k}{r} @₹{p}" for a, k, r, p in t["legs"])
        print(f"{t['ts']}  {t['structure']}")
        print(f"  legs: {legs_str}")
        print(f"  net credit ₹{t['net_credit']:,.0f}  max loss ₹{t['max_loss']:,.0f}")
        pnl = t["realized_pnl"]
        pnl_str = f"₹{pnl:,.0f}" if pnl is not None else "unknown"
        print(f"  exit: {t['exit_ts']}  reason={t['exit_reason']}  P&L: {pnl_str}\n")
        if pnl is not None:
            total += pnl

    print(f"{len(trades)} simulated trades, total P&L: ₹{total:,.0f}")


if __name__ == "__main__":
    main()
