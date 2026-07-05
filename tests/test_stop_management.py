"""Module 6 — Stop Management tests (Phase 3 acceptance T3.4 TSL ratchet + supporting)."""
from atom import config
from atom.stop_management import ExitTrigger, Levels, check_breach, initial_levels, update_levels

CFG = dict(config.DEFAULTS)
NET_CREDIT = 5000.0       # money terms, matches Phase 1's net_credit_money
MAX_LOSS = 9000.0


def _levels():
    return initial_levels(NET_CREDIT, MAX_LOSS, CFG)


# ---- 6.1 Initial SL/TP ----------------------------------------------------------

def test_initial_sl_is_pct_of_max_loss():
    lv = _levels()
    assert lv.sl == -round(0.35 * MAX_LOSS, 2)


def test_initial_tp_is_pct_of_net_credit():
    lv = _levels()
    assert lv.tp == round(0.50 * NET_CREDIT, 2)


def test_defined_risk_clamp_never_exceeds_max_loss():
    """6.1.4 — even with an aggressive sl.pct config, SL can never imply a loss worse
    than the position's own defined max loss."""
    cfg = {**CFG, "risk.sl.pct": 500}
    lv = initial_levels(NET_CREDIT, MAX_LOSS, cfg)
    assert lv.sl == -MAX_LOSS


def test_expiry_day_tightens_sl():
    normal = initial_levels(NET_CREDIT, MAX_LOSS, CFG, is_expiry_today=False)
    expiry = initial_levels(NET_CREDIT, MAX_LOSS, CFG, is_expiry_today=True)
    assert abs(expiry.sl) < abs(normal.sl)


# ---- 6.3 TSL activation, trailing, ratchet --------------------------------------

def test_tsl_arms_at_activation_threshold():
    lv = _levels()
    activation_pnl = CFG["tsl.activation.pct"] / 100.0 * NET_CREDIT
    lv2 = update_levels(lv, activation_pnl, NET_CREDIT, CFG)
    assert lv2.tsl_armed is True
    assert lv2.tsl is not None


def test_tsl_inactive_below_activation():
    lv = _levels()
    lv2 = update_levels(lv, 100.0, NET_CREDIT, CFG)   # well below 30% of 5000
    assert lv2.tsl_armed is False and lv2.tsl is None


def test_tsl_sticky_once_armed_even_if_pnl_dips():
    lv = _levels()
    lv2 = update_levels(lv, 0.30 * NET_CREDIT, NET_CREDIT, CFG)
    assert lv2.tsl_armed
    lv3 = update_levels(lv2, 0.05 * NET_CREDIT, NET_CREDIT, CFG)   # profit dips hard
    assert lv3.tsl_armed is True   # stays armed (sticky)
    assert lv3.tsl == lv2.tsl      # ratchet: candidate looser (lower) -> suppressed


def test_tsl_trails_favorable_movement():
    lv = _levels()
    lv2 = update_levels(lv, 0.30 * NET_CREDIT, NET_CREDIT, CFG)     # arm
    lv3 = update_levels(lv2, 0.60 * NET_CREDIT, NET_CREDIT, CFG)    # profit improves
    assert lv3.tsl > lv2.tsl                                        # floor rose (tighter)
    assert lv3.high_water_pnl == 0.60 * NET_CREDIT


def test_tsl_never_loosens_on_a_worse_cycle():
    """T3.4 core invariant: a favorable-then-adverse sequence must never lower tsl."""
    lv = _levels()
    lv = update_levels(lv, 0.30 * NET_CREDIT, NET_CREDIT, CFG)
    lv = update_levels(lv, 0.80 * NET_CREDIT, NET_CREDIT, CFG)
    tight = lv.tsl
    lv = update_levels(lv, 0.35 * NET_CREDIT, NET_CREDIT, CFG)   # reverses hard
    assert lv.tsl == tight   # unchanged, never loosened


def test_restart_reload_preserves_locked_stop_not_recompute_looser():
    """6.3.3/6.7.1 — the caller reloads persisted Levels and passes them back in;
    a stale/looser recompute must never win over the persisted tight value."""
    lv = _levels()
    lv = update_levels(lv, 0.80 * NET_CREDIT, NET_CREDIT, CFG)
    persisted = lv   # simulates AtomState reload after a restart
    reloaded = update_levels(persisted, 0.10 * NET_CREDIT, NET_CREDIT, CFG)
    assert reloaded.tsl == persisted.tsl


# ---- 6.5 Breach detection + precedence ------------------------------------------

def test_no_breach_mid_range():
    lv = _levels()
    t = check_breach(500.0, lv, is_eod=False, net_credit_money=NET_CREDIT, cfg=CFG)
    assert t.triggered is False


def test_sl_breach_before_tsl_armed():
    lv = _levels()
    t = check_breach(lv.sl - 1, lv, is_eod=False, net_credit_money=NET_CREDIT, cfg=CFG)
    assert t.triggered and t.reason == "SL"


def test_tsl_breach_reason_once_armed():
    lv = _levels()
    lv = update_levels(lv, 0.30 * NET_CREDIT, NET_CREDIT, CFG)
    lv = update_levels(lv, 0.60 * NET_CREDIT, NET_CREDIT, CFG)
    t = check_breach(lv.tsl - 1, lv, is_eod=False, net_credit_money=NET_CREDIT, cfg=CFG)
    assert t.triggered and t.reason == "TSL"


def test_tp_breach():
    lv = _levels()
    t = check_breach(lv.tp + 1, lv, is_eod=False, net_credit_money=NET_CREDIT, cfg=CFG)
    assert t.triggered and t.reason == "TP"


def test_time_overrides_everything_even_mid_range_pnl():
    """6.6.1 — hard cutoff fires unconditionally regardless of P&L."""
    lv = _levels()
    t = check_breach(100.0, lv, is_eod=True, net_credit_money=NET_CREDIT, cfg=CFG)
    assert t.triggered and t.reason == "TIME"


def test_loss_protection_checked_before_tp_when_both_could_fire():
    """Precedence: if a plan's TP <= SL breach zone somehow overlapped (pathological
    config), loss protection wins — protect capital first."""
    lv = Levels(sl=-100.0, tp=-50.0, tsl=None, tsl_armed=False, high_water_pnl=None)
    t = check_breach(-100.0, lv, is_eod=False, net_credit_money=NET_CREDIT, cfg=CFG)
    assert t.reason == "SL"   # not TP, even though -100 <= -50 (tp) is also true


def test_edge_exhausted_fires_when_tp_threshold_set_high():
    """6.6.3 — with tp.pct raised above the edge-exhausted threshold, the
    edge-exhausted gate fires first (backstop for 'let winners run' configs)."""
    cfg = {**CFG, "risk.tp.pct": 99, "stop.edge_exhausted.pct": 90}
    lv = initial_levels(NET_CREDIT, MAX_LOSS, cfg)
    t = check_breach(0.92 * NET_CREDIT, lv, is_eod=False, net_credit_money=NET_CREDIT, cfg=cfg)
    assert t.triggered and t.reason == "EDGE_EXHAUSTED"


# ---- Bad-tick guard on the TSL high-water mark (PORCUPINE-caught 2026-07-05) ------

def test_implausible_upside_tick_does_not_poison_ratchet_permanently():
    """A credit spread's max profit is the full credit collected — a PnL reading way
    above that (50x credit) is a bad tick, not real profit. Without a ceiling this
    poisons high_water_pnl FOREVER (the ratchet-merge that protects against loosening
    also means it never un-poisons), silently disabling the stop."""
    lv = initial_levels(NET_CREDIT, MAX_LOSS, CFG)
    lv2 = update_levels(lv, 50 * NET_CREDIT, NET_CREDIT, CFG)
    assert lv2.high_water_pnl == NET_CREDIT          # clamped to the plausible ceiling
    assert lv2.tsl == NET_CREDIT - 0.5 * NET_CREDIT  # trail gap off the CLAMPED value

    # price reverts to something sane — the stop must still be a real, reachable level
    lv3 = update_levels(lv2, 0.30 * NET_CREDIT, NET_CREDIT, CFG)
    assert lv3.tsl == lv2.tsl                        # unchanged (ratchet), but SANE


def test_plausible_ceiling_configurable():
    cfg = {**CFG, "tsl.max_plausible_credit_pct": 120}   # allow up to 120% (fees/slippage buffer)
    lv = initial_levels(NET_CREDIT, MAX_LOSS, cfg)
    lv2 = update_levels(lv, 50 * NET_CREDIT, NET_CREDIT, cfg)
    assert lv2.high_water_pnl == 1.2 * NET_CREDIT
