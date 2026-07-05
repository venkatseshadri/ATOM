"""Module 6 — Stop Management (Phase 3, real).

Computes and maintains SL / TSL / TP for one open position and raises an exit trigger
on breach. Reference frame: MONEY P&L (not raw premium) — matches what Phase 1's
`check_exit` already computes (`current_pnl`), so this module is the direction-mirror of
the doc's premium-ceiling convention: PnL rises as profit grows, so the trailed stop is a
rising PnL FLOOR that only ever moves up (tighter), never down — same monotonic-tightening
invariant (6.3.3), just algebraically inverted from premium-ceiling framing.

Greek-based (6.1.3) and underlying-based (6.1.2) SL frames are N/A — Penguin has no
per-strike greeks (same honest gap as Phase 2's Module 4). Premium/PnL-based (6.1.1) is
the only frame with real data, matching Phase 1's existing SL/TP.

Ratchet state (`high_water_pnl`, `tsl`, `tsl_armed`) must persist across cron invocations
(same stateless-per-invocation architecture as Module 7) — callers pass in the prior
cycle's `Levels` (loaded from AtomState) and get back the next cycle's; this module never
holds state itself (6.7.1's "reload state, trust it over recomputation" is satisfied by
the caller, not an in-memory cache here).
"""
from __future__ import annotations

from dataclasses import dataclass

from . import config


@dataclass(frozen=True)
class Levels:
    sl: float                    # static SL, money PnL floor (negative), fixed at entry
    tp: float                    # static TP, money PnL ceiling (positive), fixed at entry
    tsl: float | None            # armed trailed stop (PnL floor), monotonically rising
    tsl_armed: bool
    high_water_pnl: float | None  # best PnL seen since arm


def initial_levels(net_credit_money: float, max_loss_money: float, cfg: dict,
                   is_expiry_today: bool = False) -> Levels:
    """6.1.1/6.1.4 + 6.6.2 — static SL/TP at entry, defined-risk-clamped, tighter on
    expiry day (gamma risk)."""
    sl_pct = cfg.get("risk.sl.pct", 35)
    if is_expiry_today:
        sl_pct *= cfg.get("stop.expiry_tighten.factor", 0.7)
    tp_pct = cfg.get("risk.tp.pct", 50)
    sl = -round(sl_pct / 100.0 * max_loss_money, 2)
    # 6.1.4 defined-risk clamp: SL can never imply a loss worse than the position's own
    # defined max loss — the spread itself hard-caps the downside regardless of config.
    sl = max(sl, -max_loss_money)
    tp = round(tp_pct / 100.0 * net_credit_money, 2)
    return Levels(sl=sl, tp=tp, tsl=None, tsl_armed=False, high_water_pnl=None)


def update_levels(prior: Levels, current_pnl: float, net_credit_money: float,
                  cfg: dict) -> Levels:
    """6.3.1/6.3.2/6.3.3 — arm once activation reached (sticky), trail the high-water
    mark, ratchet-merge so the floor only ever rises. Downside bad-tick guard: a
    single-cycle PnL collapse can't un-arm or lower high_water below its own persisted
    value — the ratchet-merge (max) makes that structurally impossible.

    Upside bad-tick guard (PORCUPINE-caught 2026-07-05): a credit spread's mathematical
    max profit is the net credit collected (cost-to-close can't go below 0) — any PnL
    reading above that is a bad tick, not real profit. Without clamping it, a single
    implausible print (e.g. a stale/erroneous quote) would poison high_water_pnl
    PERMANENTLY: the ratchet-merge (max) that protects against loosening also means it
    NEVER un-poisons even after the price reverts to something sane — the position's
    stop silently becomes unreachable, disabling protection for the rest of its life."""
    plausible_ceiling = net_credit_money * cfg.get("tsl.max_plausible_credit_pct", 100) / 100.0
    plausible_pnl = min(current_pnl, plausible_ceiling)
    activation = cfg.get("tsl.activation.pct", 30) / 100.0 * net_credit_money
    tsl_armed = prior.tsl_armed or plausible_pnl >= activation
    if not tsl_armed:
        return prior
    high_water = max(prior.high_water_pnl if prior.high_water_pnl is not None else plausible_pnl,
                     plausible_pnl)
    trail_gap = cfg.get("tsl.trail_gap.pct", 50) / 100.0 * net_credit_money
    candidate = high_water - trail_gap
    new_tsl = candidate if prior.tsl is None else max(prior.tsl, candidate)
    new_tsl = max(new_tsl, 0.0)   # never let the locked floor go negative (worse than flat)
    return Levels(sl=prior.sl, tp=prior.tp, tsl=new_tsl, tsl_armed=True,
                 high_water_pnl=high_water)


@dataclass(frozen=True)
class ExitTrigger:
    triggered: bool
    reason: str | None            # SL | TSL | TP | TIME | EDGE_EXHAUSTED
    pnl: float


def check_breach(current_pnl: float, levels: Levels, is_eod: bool, net_credit_money: float,
                 cfg: dict) -> ExitTrigger:
    """6.5.1/6.5.4 + 6.6.1/6.6.3 — precedence: TIME (hard, unconditional) > loss
    protection (TSL if armed else SL) > TP > edge-exhausted. Loss protection is checked
    before TP — protect capital first, consistent with Module 5's risk-gate stance —
    when both would fire the same cycle (no intrabar data to arbitrate more finely)."""
    if is_eod:
        return ExitTrigger(True, "TIME", current_pnl)

    effective_stop = levels.tsl if levels.tsl is not None else levels.sl
    if current_pnl <= effective_stop:
        return ExitTrigger(True, "TSL" if levels.tsl_armed else "SL", current_pnl)

    if current_pnl >= levels.tp:
        return ExitTrigger(True, "TP", current_pnl)

    edge_pct = cfg.get("stop.edge_exhausted.pct", 90)
    if net_credit_money > 0 and current_pnl >= edge_pct / 100.0 * net_credit_money:
        return ExitTrigger(True, "EDGE_EXHAUSTED", current_pnl)

    return ExitTrigger(False, None, current_pnl)
