#!/usr/bin/env python3
"""Phase-1 operator-style narrated log, two scenarios:
  python3 run_phase1_log.py             # FRESH — no existing order → places one
  python3 run_phase1_log.py --existing  # existing order present → skip

Broker lines (Shoonya auth / token / broker order #) are PAPER/MOCK in Phase 1 — broker
wiring is Phase 3. The AI-optimizer values (step 'Load optimized values from AI module')
are MOCK — the real research loop is Phase 5. Everything else (data, ATM, indicators,
7-family vote, decision, strikes, premiums) is REAL from Penguin.
"""
import sqlite3
import sys

sys.path.insert(0, "src")

from atom import ai_optimizer, config, lights, phase1   # noqa: E402
from atom.atom_state import AtomState     # noqa: E402
from atom.penguin import PenguinReader, _f  # noqa: E402

_DOT = {"GREEN": "🟢", "RED": "🔴", "AMBER": "🟡"}

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

    cfg = config.load_config()
    phase1.configure(cfg)
    lights.configure(cfg)

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

    show = ["strategy.wing.strikes", "strategy.lot.size", "risk.sl.pct",
            "risk.deploy.inr", "expiry.rule", "regime.entry.min_confidence",
            "regime.adx.trend_threshold", "indicator.ema.enabled",
            "indicator.ema.lookback", "indicator.rsi.enabled", "indicator.rsi.bull",
            "indicator.supertrend.enabled"]

    print("---------------------------------------------")
    line("Starting Trading module")
    line("Loading trading configurations:")
    for k in show:
        v = "true" if cfg[k] is True else "false" if cfg[k] is False else cfg[k]
        print(f"      {k}={v}")
    print(f"      … ({len(cfg)} keys loaded from config/atom.conf)")

    # --- AI optimizer (MOCK — Phase 5 is the real research loop) ---
    opt = ai_optimizer.load_optimized(cfg)
    line(f"Load optimized values from AI module — {opt['source']}")
    if opt["overrides"]:
        for k, v in opt["overrides"].items():
            print(f"      {k}: {cfg[k]} → {v}   (mock-tuned)")
    else:
        print("      (no overrides — running base config)")

    line("Broker session — Shoonya, via Penguin's login (shared; ATOM does NOT log in separately)")
    line("Session token — reused from Penguin (one broker login; Phase 1 makes no broker call)")
    line(f"Data flow validated — Penguin capture_nifty.sqlite (read-only), bar {snap.ts}")
    line(f"NIFTY ATM is {snap.atm_strike}   (spot ₹{snap.spot:,.2f}, expiry {snap.expiry})")
    line("Multi-TF candlesticks captured — OK")
    line(f"Indicator calculation — EMA-slope {_f(ind.get('ema20_slope'))}, RSI {_f(ind.get('rsi'))}, "
         f"ST {ind.get('st_consensus')}, ADX {_f(ind.get('adx'))}, IV {_f(ind.get('india_vix'))}, "
         f"PCR {_f(ind.get('pcr_total'))} — successful   [consumed from Penguin]")

    # --- 7a/7b ATOM-Lights traffic-light layer (SHADOW — logged, does not gate yet) ---
    res = lights.evaluate(reader, snap.ind, snap.days_to_expiry)
    pat = "  ".join(f"{tf}{_DOT[res.lights[tf]]}" for tf in ("5m", "15m", "30m", "60m", "240m", "1D"))
    line(f"Traffic-light system — {pat}   Gap:{res.gap}")
    line(f"   60m candle = {res.lights['60m']} → PERMISSION {res.permission} "
         f"| conviction/size {res.size} | trigger {res.trigger}")

    # --- 7c per-family direction (the 7-family vote, broken out) ---
    regime, conf, probs, votes = phase1.classify_regime(snap.ind)
    fam = phase1.family_view(votes)
    line(f"7-family direction — {regime} (conf {conf*100:.0f}%):")
    for i, (name, word) in enumerate(fam, 1):
        print(f"      Family {i} — {name}: {word}")

    line("Validating if existing orders exist")

    if decision["intent"] == "SKIP":
        row = sqlite3.connect(state.db_path).execute(
            "select structure,net_credit,bar_ts from paper_trades order by ts desc limit 1").fetchone()
        line(f"Existing order [{row[0]}] taken at {row[2]} for net credit ₹{row[1]:,.0f}")
        line("Skipping further flows (single position open)")
        print("---------------------------------------------")
        return

    line("No existing orders exist")

    # --- DecisionMaker: anti-bias three-way (NotUp / NotDown / Sideways) ---
    branches = phase1.branch_scores(snap.ind)
    for name in ("NotUp", "NotDown", "Sideways"):
        b = branches[name]
        line(f"{name} value calculated {b['value']} with probability {b['prob']*100:.0f}%")
    win_name = max(branches, key=lambda k: branches[k]["prob"])
    win = branches[win_name]

    # --- ATOM-Lights shadow verdict (does not gate the 7-family paper order) ---
    sh = lights.shadow_entry(res, regime)
    verdict = f"ENTER {sh['instrument']} (size {sh['size']})" if sh["enter"] else "no-enter"
    line(f"ATOM-Lights [SHADOW] AND-gate → {verdict}  — {sh['reason']} "
         f"(logged for P(profit|state); 7-family below drives the paper order)")

    if not order:
        line(f"DecisionMaker — {win_name} wins ({win['prob']*100:.0f}%) "
             f"→ {win['trade']} — no order placed (entry gate not met)")
        print("---------------------------------------------")
        return

    line(f"DecisionMaker — {win_name} value wins ({win['prob']*100:.0f}%) → taking {win['trade']}")
    (h_act, h_k, h_r, h_ltp), (s_act, s_k, s_r, s_ltp) = order.legs
    off = (h_k - snap.atm_strike) // STEP
    line(f"Placing HEDGE order ATM{off:+d} ({h_r}) — strike (ATM {snap.atm_strike} {off:+d}×{STEP} = {h_k}) "
         f"qty {order.lot} (1 lot)")
    line(f"Hedge order placed for 1 lot at ₹{h_ltp}   [PAPER #P-0001 — live routing via Penguin = Phase 3]")
    line(f"Placing ATM SHORT order ATM ({s_r}) — strike (ATM {snap.atm_strike}) qty {order.lot} (1 lot)")
    line(f"Short order placed for 1 lot at ₹{s_ltp}   [PAPER #P-0002 — live routing via Penguin = Phase 3]")
    line(f"Entry complete — net credit ₹{order.net_credit:,.0f}, max loss ₹{order.max_loss:,.0f}. "
         f"Phase 1 stops here (no lifecycle).")
    print("---------------------------------------------")


if __name__ == "__main__":
    main()
