# Phase 1 — Technical Detail

**Status: built, runs on live + fixture Penguin data, 27 tests green. Awaiting GATE 1.**

Goal met: ATOM **scouts → decides → places a paper order with REAL numbers**, all sourced
from Penguin. Stops at order-placed — no lifecycle/morph/SL (later phases).

## What's REAL now (vs Phase 0 stubs)
| Module | Phase 1 | Source |
|--------|---------|--------|
| Market Data (1) | **REAL** | `penguin.PenguinReader` — reads `capture_nifty.sqlite` **read-only** |
| Instrument (12) | **REAL (consume)** | uses Penguin `atm_strike`, `expiry_weekly`, `days_to_weekly` — does **not** recompute |
| Regime (2) | **REAL** | `phase1.classify_regime` — 7-family consensus over enriched indicators |
| Strategy FSM (3) | **REAL (entry)** | `phase1.decide` — FLAT + confirmed trend → open; else stand-down/skip |
| Construction (4) | **REAL** | `phase1.build_order` — real strikes (ATM±wing) + **real premiums** from `option_prices` |
| Order (13) | **PAPER** | recorded to `atom_paper_trades`; **no broker** (Phase 3) |
| Risk (5), Stops (6) | not in Phase 1 | Phase 3 |

## Data sources (Penguin, read-only — don't duplicate)
- `market_data_enriched` (100 cols) — spot, atm_strike, expiry, and all 7-family inputs
  (supertrend/adx/rsi/ema-slope/india_vix/iv_rank/bb_width/vwap/structure/pcr/oi_skew/sentiment).
- `option_prices` — real per-strike LTP/OI for the live weekly expiry (premiums).
- ATOM **never writes** Penguin; ignores `decision_trace`/`trade_outcomes`.

## Process model (locked through discussion)
Timer-fired / per-bar, **pull + freshness gate** (no Penguin-side events; no daemon poll):
```
load atom_state → read latest enriched bar (ro)
  → same bar?  → NO_OP (idempotent on last_bar_ts)
  → stale > 90s? → STAND_DOWN + (would) alert   ← detects feed death, never trades stale
  → fresh new bar → cycle() → checkpoint
```
SL is **not** polled — Phase 3 enforces stops via the trader's websocket callback (the
trader is the persistent process; ATOM stays stateless per cycle).

## Broker — ATOM has NO login of its own
There is **one** Shoonya login: Penguin's (for capture). ATOM **leverages that session** —
never a second broker login. Phase 1 makes **no broker call at all** (data comes from
Penguin's DB). Phase 3 routes orders **through** Penguin's session/order path, not an ATOM
broker connection. So ATOM holds zero broker credentials.

## 7-family consensus (Phase-1 defaults, ‹TBD› — research-loop tunes)
Each directional family votes +1/−1/0; equal weight; `adx` gates trend-present and boosts
confidence. Entry only when confidence ≥ 0.45 and regime ∈ {TREND_UP, TREND_DOWN}.
Conflicts (e.g. bearish trend vs bullish sentiment) correctly **lower confidence** —
anti-bias by design. Votes are logged every cycle (full transparency).

## State (`atom_state.sqlite`, ATOM-owned, single writer)
- `atom_state(fsm_state, last_bar_ts)` — reloaded each cycle; atomic checkpoint.
- `paper_trades(...)` — real paper orders (separate from the future real order-ledger).

## Files
`src/atom/penguin.py` · `src/atom/phase1.py` · `src/atom/atom_state.py` ·
`src/atom/runner.py` · `run_live_once.py` · `tests/test_phase1.py` ·
`tests/fixtures/capture_nifty_fixture.sqlite` (real rows, 61 KB).

## Run
```bash
python3 run_live_once.py            # live capture_nifty.sqlite
python3 run_live_once.py --fixture  # deterministic fixture (27-test backed)
python3 -m pytest tests/test_phase1.py
```

## Real output (fixture, 2026-06-30 12:12)
```
SCOUT  spot ₹23,928.35  ATM 23950  expiry 30-JUN-2026
       regime=TREND_DOWN conf=0.48  votes={trend:0,momentum:-1,price_action:-1,structure:-1,sentiment:+1,...}
DECIDE OPEN bear_call_spread
ORDER  SELL 23950CE @ ₹36.5 | BUY 24050CE @ ₹10.1  → net credit ₹1,980  max loss ₹5,520  lot 75   (PAPER)
```

## Known / deferred
- Premium uses real `option_prices` LTP (option feed was stale 06-12→06-30, **fixed**).
- Per-strike greeks still absent → delta-based selection deferred (Phase 2; currently ATM±wing).
- Live continuous run (systemd timer) **not deployed** — Phase 1 is fixture-harness + manual
  live smoke; timer + risk + real orders arrive Phase 3.
