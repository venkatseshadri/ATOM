#!/usr/bin/env python3
"""Read-only P&L report over atom_state.sqlite's paper_trades. Pure reporting — does
not touch trading logic, does not mutate state. Realized P&L only counts CLOSED trades;
an open position contributes nothing to the total until it actually exits (SL/TP/EOD).

Usage:
  python3 pnl_report.py                  # today
  python3 pnl_report.py --date 2026-07-01
  python3 pnl_report.py --all            # every trade ever recorded
"""
import argparse
import json
import sqlite3
import sys
from datetime import date

DB = "data/atom_state.sqlite"


def _fmt_legs(legs_json: str) -> str:
    legs = json.loads(legs_json)
    return "; ".join(f"{a} {k}{r} @₹{p}" for a, k, r, p in legs)


def report(scope_date: str | None) -> None:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    if scope_date:
        rows = conn.execute(
            "select ts,bar_ts,structure,net_credit,max_loss,legs,exit_ts,exit_reason,"
            "realized_pnl,exit_legs from paper_trades "
            "where ts like ? or exit_ts like ? order by ts",
            (f"{scope_date}%", f"{scope_date}%")).fetchall()
        print(f"=== ATOM P&L report — {scope_date} ===")
    else:
        rows = conn.execute(
            "select ts,bar_ts,structure,net_credit,max_loss,legs,exit_ts,exit_reason,"
            "realized_pnl,exit_legs from paper_trades order by ts").fetchall()
        print("=== ATOM P&L report — all time ===")
    conn.close()

    if not rows:
        print("No trades recorded for this scope.")
        return

    closed_pnl = []
    open_count = 0
    for (ts, bar_ts, structure, net_credit, max_loss, legs, exit_ts, exit_reason,
         realized_pnl, exit_legs) in rows:
        print(f"\n{ts}  {structure}  (entered bar {bar_ts})")
        print(f"  legs: {_fmt_legs(legs)}")
        print(f"  net credit ₹{net_credit:,.0f}  max loss ₹{max_loss:,.0f}")
        if exit_ts is None:
            print("  status: OPEN (not yet closed — excluded from realized total)")
            open_count += 1
        else:
            sign = "+" if realized_pnl and realized_pnl > 0 else ""
            print(f"  status: CLOSED  reason={exit_reason}  exit_ts={exit_ts}")
            print(f"  realized P&L: {sign}₹{realized_pnl:,.0f}" if realized_pnl is not None
                  else "  realized P&L: unknown (forced close, no price data)")
            if realized_pnl is not None:
                closed_pnl.append(realized_pnl)

    total = sum(closed_pnl)
    print(f"\n{'-' * 60}")
    print(f"{len(closed_pnl)} closed  |  {open_count} still open  |  "
          f"realized total: {'+' if total >= 0 else ''}₹{total:,.0f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD, defaults to today")
    ap.add_argument("--all", action="store_true", help="every trade ever recorded")
    args = ap.parse_args()

    if args.all:
        report(None)
    else:
        report(args.date or date.today().isoformat())


if __name__ == "__main__":
    main()
