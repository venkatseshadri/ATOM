# ATOM-Lights — multi-timeframe candle-color entry layer

A **second** entry layer on top of the 7-family regime (it does not replace indicators).
Status: **SHADOW** — computed + logged every cycle, does **not** gate entries yet. Promote
to a hard AND-gate only after P(profit | state) beats base rate (spec §9).

## Decision (Board)
- **AND-gate** with the 7-family: enter only when 7-family direction AND 60m permission
  agree AND a pullback→resumption trigger fires. → fewer, better-timed entries.
- **AMBER → iron fly** (direct neutral entry permitted).
- **Shadow-first**: log to `lights_shadow` table, measure edge, then promote.
- Fragile v1 rules fixed now (below).

## Roles (per spec §3)
60m = PERMISSION (instrument) · 240m/1D = CONVICTION (size) · 5m/15m = TRIGGER (pullback)
· 30m = context · Gap = veto/modifier.

## Fixes applied to the v1 spec
- **A — deterministic colour.** Dropped the clock-based `<50%` handover. Each candle is
  GREEN/RED by close-vs-open; a timeframe is **AMBER** when current vs previous candle
  disagree (handover/chop).
- **B — body filter.** Body must be ≥ `lights.body.min_frac` of the candle range, else
  AMBER (kills doji churn).
- **C — resumption trigger.** Pullback = prior 5m/15m against trend; resumption = current
  5m re-aligned close. No anticipation while still moving against.
- **D — 240m bucketing.** Consumed straight from Penguin's multitf (Penguin owns bucketing).

## Data source (Penguin, read-only)
`market_data_multitf` timeframes [5,15,30,60,240,1440] + enriched `gap_pct`/`swing_low`.
No new capture, no duplication.

> ⚠️ **Data caveat:** as of 2026-06-30, `market_data_multitf` is **stale (last row 06-10)**
> and the **240m/1440m rows mirror 60m** (not truly aggregating) — same class of gap the
> option feed had. The Lights *engine* is built + tested on crafted candles and shadow-runs
> on the fixture, but it can only produce **trustworthy live colours once multitf is
> fresh + 240/1D fixed** (Penguin side), OR if ATOM resamples 5/15/30/60/240/1D from the
> fresh 1-min `market_data` (mild duplication — needs a Board call).

## Config (`config/atom.conf`)
`lights.enabled`, `lights.shadow`, `lights.body.min_frac`, `lights.gap.threshold_pct`,
`lights.time_gate` (no entries before 10:15 IST).

## Files
`src/atom/lights.py` · shadow persisted to `lights_shadow` (atom_state) every cycle via
`runner.run_once` · `tests/test_lights.py` (9 tests) · operator log prints the traffic
pattern + 7-family direction + 60m permission + AND-gate verdict.

## Measurement (before promotion)
`lights_shadow(bar_ts, lights, gap, permission, size, trigger, family_dir, family_conf,
candidate_enter, candidate_instrument, reason)` → join with forward N-min outcome to get
P(profit | state) vs base rate. Promote to a hard gate only if the edge is proven.
