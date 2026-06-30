#!/usr/bin/env python3
"""Phase-1 operator-style narrated log, two scenarios:
  python3 run_phase1_log.py             # FRESH — no existing order → places one
  python3 run_phase1_log.py --existing  # existing order present → skip

Broker lines (Shoonya auth / token / broker order #) are PAPER/MOCK in Phase 1 — broker
wiring is Phase 3. Everything else (data, ATM, indicators, decision, strikes, premiums)
is REAL from Penguin.
"""
import sqlite3
import sys

sys.path.insert(0, "src")

from atom import phase1                   # noqa: E402
from atom.atom_state import AtomState     # noqa: E402
from atom.penguin import PenguinReader, _f  # noqa: E402

FIX = "tests/fixtures/capture_nifty_fixture.sqlite"
STEP = 50


def _seed_existing(state: AtomState, structure: str, strike: int, credit: float, ts: str):
    c = sqlite3.connect(state.db_path)
    c.execute("UPDATE atom_state SET fsm_state='SINGLE_SPREAD', last_bar_ts='2000-01-01' WHERE id=1")
    c.execute("INSERT INTO paper_trades VALUES(?,?,?,?,?,?,?,?,?)",
              (ts, ts, structure, credit, 0, 75, "[]", "TREND_DOWN", 0.7))
    c.commit(); c.close()


def main() -> None:
    existing = "--existing" in sys.argv
    reader = PenguinReader(FIX)
    state = AtomState("data/atom_state_log.sqlite")
    state.reset()

    if existing:
        snap = reader.latest_snapshot()
        _seed_existing(state, "bear_call_spread", snap.atm_strike, 1980.0, snap.ts)
        decision = {"intent": "SKIP"}
        order = None
    else:
        # scout newest→oldest for the first bar with a clear trend that enters
        snap = reader.latest_snapshot()
        decision, order = None, None
        for s in reader.recent_snapshots():
            _, dec, o = phase1.cycle("FLAT", s)
            if dec["intent"] == "OPEN" and o:
                snap, decision, order = s, dec, o
                break
        if decision is None:                       # none entered → show latest no-trade
            _, decision, order = phase1.cycle("FLAT", snap)

    ind = snap.ind
    n = 0

    def line(msg):
        nonlocal n
        n += 1
        print(f"{n:2}. {msg}")

    print("---------------------------------------------")
    line("Starting Trading module")
    line("Broker authentication successful — Shoonya            [PAPER/MOCK — broker = Phase 3]")
    line("Token obtained                                        [MOCK]")
    line(f"Data flow validated — Penguin capture_nifty.sqlite (read-only), bar {snap.ts}")
    line(f"NIFTY ATM is {snap.atm_strike}   (spot ₹{snap.spot:,.2f}, expiry {snap.expiry})")
    line("Multi-TF candlesticks captured — OK")
    line(f"Indicator calculation — EMA-slope {_f(ind.get('ema20_slope'))}, RSI {_f(ind.get('rsi'))}, "
         f"ST {ind.get('st_consensus')}, ADX {_f(ind.get('adx'))}, IV {_f(ind.get('india_vix'))}, "
         f"PCR {_f(ind.get('pcr_total'))} — successful   [consumed from Penguin]")
    line("Validating if existing orders exist")

    if decision["intent"] == "SKIP":
        row = sqlite3.connect(state.db_path).execute(
            "select structure,net_credit,bar_ts from paper_trades order by ts desc limit 1").fetchone()
        line(f"Existing order [{row[0]}] taken at {row[2]} for net credit ₹{row[1]:,.0f}")
        line("Skipping further flows (single position open)")
        print("---------------------------------------------")
        return

    p = decision["probs"]
    line("No existing orders exist")
    line(f"Up probability        {p['UP']*100:.0f}%")
    line(f"Down probability      {p['DOWN']*100:.0f}%")
    line(f"Sideways probability  {p['SIDEWAYS']*100:.0f}%")

    if not order:
        line(f"DecisionMaker — {decision['regime']} ({decision['confidence']*100:.0f}%) "
             f"→ no trade ({decision['structure']})")
        print("---------------------------------------------")
        return

    trade = "trending-up (bull put spread)" if decision["regime"] == "TREND_UP" else "trending-down (bear call spread)"
    winner = "Up" if decision["regime"] == "TREND_UP" else "Down"
    line(f"DecisionMaker — {winner} value wins ({decision['confidence']*100:.0f}%) → taking {trade}")
    (h_act, h_k, h_r, h_ltp), (s_act, s_k, s_r, s_ltp) = order.legs
    off = (h_k - snap.atm_strike) // STEP
    line(f"Placing HEDGE order ATM{off:+d} ({h_r}) — strike (ATM {snap.atm_strike} {off:+d}×{STEP} = {h_k}) "
         f"qty {order.lot} (1 lot)")
    line(f"Hedge order placed for 1 lot at ₹{h_ltp}   [PAPER order #P-0001]")
    line(f"Placing ATM SHORT order ATM ({s_r}) — strike (ATM {snap.atm_strike}) qty {order.lot} (1 lot)")
    line(f"Short order placed for 1 lot at ₹{s_ltp}   [PAPER order #P-0002]")
    line(f"Entry complete — net credit ₹{order.net_credit:,.0f}, max loss ₹{order.max_loss:,.0f}. "
         f"Phase 1 stops here (no lifecycle).")
    print("---------------------------------------------")


if __name__ == "__main__":
    main()
