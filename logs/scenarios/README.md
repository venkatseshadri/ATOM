# ATOM — Scenario Logs

One log per scenario. Each begins with a **MOCK/REAL legend** (per-module status for the
current phase) so the same logs stay meaningful as phases turn modules real. Regenerate
with `python3 run_scenarios.py`.

| Scenario | Exit path | Expected result |
|----------|-----------|-----------------|
| [A_morph_to_eod](A_morph_to_eod.log) | morph → EOD | open → iron fly → runner → EOD square-off, ₹+4,200 |
| [B_stop_loss](B_stop_loss.log) | SL breach | open → SL hit → exit at max loss, ₹-1,665 |
| [C_take_profit](C_take_profit.log) | TP hit | open → 50% credit captured → exit, ₹+2,918 |
| [D_trailing_stop](D_trailing_stop.log) | TSL hit | open → rides → TSL triggers → exit, ₹+1,460 |
| [E_risk_reject](E_risk_reject.log) | none | drawdown floor breached → no order, stays FLAT |

Status (Phase 0): all modules **MOCK** except telemetry (REAL); values illustrative. The
legend at the top of each log is the source of truth — update `src/atom/status.py` as
phases land.
