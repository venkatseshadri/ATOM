#!/usr/bin/env python3
"""ATOM status + P&L digest — mirrors the PENGUIN/KALKI health-report style (terse,
single message, safe to run anytime). Read-only against atom_state.sqlite; running P&L
for an open position re-uses phase1.check_exit() against live Penguin prices.

Usage: python3 atom_report.py
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "src")

DB = "data/atom_state.sqlite"
LIVE = "/home/trading_ceo/python-trader/varaha/data/capture_nifty.sqlite"
FLATTRADE_TOKENS = "/home/trading_ceo/python-trader/tokens.json"
SHOONYA_CRED = "/home/trading_ceo/python-trader/Shoonya_oAuthAPI-py/cred.yml"


def _age_str(dt: datetime) -> str:
    hrs = (datetime.now() - dt).total_seconds() / 3600
    return f"{hrs:.1f}h ago" if hrs < 48 else f"{hrs / 24:.1f}d ago"


def _broker_token_status() -> list[str]:
    """Read-only — never re-triggers a refresh. Shows raw age, not a GO/NOGO: refresh
    only runs 07:00 Mon-Fri, so a naive freshness threshold would false-alarm on
    weekends/holidays the same way PENGUIN's health report already does."""
    lines = []
    try:
        ft = json.load(open(FLATTRADE_TOKENS))
        last_login = datetime.strptime(ft["last_login"], "%Y-%m-%d %H:%M:%S")
        ok = "✅" if ft.get("exchange_ok") else "⚠️"
        lines.append(f"Flattrade token: {ok} last_login={_age_str(last_login)} "
                     f"(user={ft.get('user_id', '?')})")
    except Exception as e:
        lines.append(f"Flattrade token: ⚠️ unreadable ({e})")

    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(SHOONYA_CRED))
        lines.append(f"Shoonya token:   ✅ cred file updated {_age_str(mtime)}")
    except Exception as e:
        lines.append(f"Shoonya token:   ⚠️ unreadable ({e})")

    return lines


def _rows():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    fsm_state, last_bar_ts = conn.execute(
        "select fsm_state,last_bar_ts from atom_state").fetchone()
    all_trades = conn.execute("select * from paper_trades order by ts").fetchall()
    conn.close()
    return fsm_state, last_bar_ts, all_trades


def _running_pnl(open_row):
    """Unrealized P&L for the currently open position, via the same check_exit() the
    live pipeline uses — not a separate calculation that could silently drift from it."""
    from atom import phase1
    from atom.penguin import PenguinReader

    position = {
        "ts": open_row["ts"], "bar_ts": open_row["bar_ts"], "structure": open_row["structure"],
        "net_credit": open_row["net_credit"], "max_loss": open_row["max_loss"],
        "lot": open_row["lot"], "legs": json.loads(open_row["legs"]), "expiry": open_row["expiry"],
    }
    reader = PenguinReader(LIVE)
    snap = reader.latest_snapshot()
    if snap is None:
        return None
    return phase1.check_exit(position, reader, snap.ts).current_pnl


def _pnl_since(closed, since_iso):
    vals = [r["realized_pnl"] for r in closed if r["exit_ts"] >= since_iso
            and r["realized_pnl"] is not None]
    return sum(vals), len(vals)


def main() -> None:
    now = datetime.now()
    today = now.date()
    week_start = today - timedelta(days=today.weekday())       # Monday
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)

    fsm_state, last_bar_ts, all_trades = _rows()
    closed = [r for r in all_trades if r["exit_ts"] is not None]
    open_rows = [r for r in all_trades if r["exit_ts"] is None]

    day_pnl, day_n = _pnl_since(closed, today.isoformat())
    week_pnl, week_n = _pnl_since(closed, week_start.isoformat())
    month_pnl, month_n = _pnl_since(closed, month_start.isoformat())
    ytd_pnl, ytd_n = _pnl_since(closed, year_start.isoformat())

    priced_closed = [r for r in closed if r["realized_pnl"] is not None]
    wins = [r for r in priced_closed if r["realized_pnl"] > 0]
    losses = [r for r in priced_closed if r["realized_pnl"] < 0]
    win_rate = (len(wins) / len(priced_closed) * 100) if priced_closed else None

    reasons: dict = {}
    for r in closed:
        reasons[r["exit_reason"]] = reasons.get(r["exit_reason"], 0) + 1

    # flash/churn flag — held under 5 minutes (same signature as the EOD-entry-gate bug)
    flash = []
    for r in closed:
        try:
            dur_min = (datetime.fromisoformat(r["exit_ts"])
                       - datetime.fromisoformat(r["bar_ts"])).total_seconds() / 60
        except (ValueError, TypeError):
            continue
        if dur_min < 5:
            flash.append((r["ts"], round(dur_min, 1), r["realized_pnl"]))

    status_emoji = "🟢" if fsm_state == "FLAT" else "🟡"
    print(f"🔬 ATOM {status_emoji} {fsm_state} — {now.strftime('%Y-%m-%d %H:%M')} IST")

    for line in _broker_token_status():
        print(line)

    if open_rows:
        pos = open_rows[-1]
        running = _running_pnl(pos)
        running_str = f"₹{running:,.0f}" if running is not None else "unknown (no live price)"
        print(f"Open: {pos['structure']} (bar {pos['bar_ts']})  running P&L: {running_str}")
    else:
        print("Open: none")

    print(f"Trades: {len(all_trades)} total  ({len(closed)} closed, {len(open_rows)} open)")
    print(f"P&L  today: ₹{day_pnl:,.0f} ({day_n})   week: ₹{week_pnl:,.0f} ({week_n})   "
          f"month: ₹{month_pnl:,.0f} ({month_n})   YTD: ₹{ytd_pnl:,.0f} ({ytd_n})")

    if win_rate is not None:
        print(f"Win rate: {len(wins)}/{len(priced_closed)} ({win_rate:.0f}%)   "
              f"Failures: {len(losses)}")
        if losses:
            worst = min(losses, key=lambda r: r["realized_pnl"])
            print(f"  worst: {worst['ts']}  {worst['structure']}  ₹{worst['realized_pnl']:,.0f}"
                  f"  ({worst['exit_reason']})")
        if wins:
            best = max(wins, key=lambda r: r["realized_pnl"])
            print(f"  best:  {best['ts']}  {best['structure']}  ₹{best['realized_pnl']:,.0f}"
                  f"  ({best['exit_reason']})")
    else:
        print("Win rate: n/a (no closed trades with priced P&L yet)")

    print(f"Exit reasons: {reasons}")
    if flash:
        print(f"⚠ {len(flash)} flash trade(s) held <5min (possible churn, check the cause): "
              f"{[f[0] for f in flash]}")


if __name__ == "__main__":
    main()
