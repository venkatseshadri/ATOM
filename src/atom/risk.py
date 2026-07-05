"""Module 5 — Risk & Sizing (Phase 3, real).

The hard, deterministic, non-overridable gate every proposed order plan must clear.
Pure function of (plan, account, references, cfg) — no LLM, no randomness, no clock read,
no network call. Fails CLOSED: any ambiguity, missing input, or internal error → REJECT,
never APPROVE. No caller-supplied "force"/"override" field is honored (there isn't one in
the input contract at all — that's how non-overridability is enforced by construction).

Boundary: this module does not pick strategy, strikes, or place orders. It receives a
finished plan (Module 4's `PaperOrder`, via `plan_from_paper_order`) and account facts as
plain data, and returns a `RiskVerdict` as plain data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import floor

from . import config

APPROVED, RESIZED, REJECTED = "APPROVED", "RESIZED", "REJECTED"


@dataclass(frozen=True)
class RiskVerdict:
    verdict: str                  # APPROVED | RESIZED | REJECTED
    permitted_qty: int            # lots; 0 iff REJECTED
    variant: str | None
    max_loss_total: float
    max_loss_per_lot: float
    margin_blocked: float
    sizing_basis: str
    reasons: tuple[str, ...]      # machine-readable, complete, deterministic order


def _reject(reason: str, *more: str) -> RiskVerdict:
    return RiskVerdict(REJECTED, 0, None, 0.0, 0.0, 0.0, "n/a", (reason, *more))


# ---- 5.1 Input intake, validation & normalization -----------------------------

def _validate(plan: dict, account: dict) -> str | None:
    """5.1.1/5.1.2 — schema completeness + sanity bounds. Returns a reject reason or
    None. No defaulting of risk-relevant fields; NaN/negative/missing → reject."""
    req = ("legs", "requested_qty", "variant", "family_id", "index")
    for k in req:
        if k not in plan or plan[k] in (None, ""):
            return "INPUT_INCOMPLETE"
    if not plan["legs"] or len(plan["legs"]) < 2:
        return "INPUT_INCOMPLETE"
    qty = plan["requested_qty"]
    if not isinstance(qty, int) or qty <= 0:
        return "INPUT_INVALID"
    acct_req = ("capital", "deployed", "realized_pnl_today", "reserved_risk_open",
               "open_count", "trades_today", "peak_equity", "current_equity")
    for k in acct_req:
        v = account.get(k)
        if v is None or (isinstance(v, float) and (v != v)):   # NaN check
            return "INPUT_INVALID"
    if account["deployed"] > account["capital"] * 1.0000001:
        return "STATE_INCONSISTENT"   # 5.1.2: deployed must never exceed capital
    if account["open_count"] < 0 or account["trades_today"] < 0:
        return "STATE_INCONSISTENT"
    # 5.1.2 mixed-expiry check: PaperOrder's (action, strike, right, price) tuples carry
    # no per-leg expiry at all — expiry lives at the plan/PaperOrder level instead, so
    # every tuple-shaped leg is definitionally single-expiry. Only dict-shaped legs
    # (future callers) can even express a per-leg expiry to check.
    if legs_are_dicts := all(isinstance(leg, dict) for leg in plan["legs"]):
        expiries = {leg.get("expiry") for leg in plan["legs"]}
        if len(expiries) > 1:
            return "STRUCTURE_UNSUPPORTED"
    return None


def _leg_tuple(leg) -> tuple[str, float, str, float]:
    """Accept either PaperOrder's (action, strike, right, price) tuples or dict legs."""
    if isinstance(leg, (tuple, list)):
        return leg[0], float(leg[1]), leg[2], float(leg[3])
    return leg["action"], float(leg["strike"]), leg["right"], float(leg["price"])


# ---- 5.2 Max-loss computation --------------------------------------------------

def _derive_max_loss_per_lot(legs) -> tuple[float | None, str | None]:
    """5.2.1/5.2.2 — recognize a 2-leg vertical (the only structure Module 4 builds
    today) and re-derive its worst-case loss from leg geometry; NEVER trust the plan's
    self-reported figure. Returns (max_loss_per_lot, reject_reason)."""
    if len(legs) != 2:
        return None, "UNDEFINED_RISK"   # only the 2-leg vertical shape is recognized
    (a1, k1, r1, p1), (a2, k2, r2, p2) = (_leg_tuple(legs[0]), _leg_tuple(legs[1]))
    if r1 != r2:
        return None, "UNDEFINED_RISK"           # not a same-right vertical
    actions = {a1, a2}
    if actions != {"BUY", "SELL"}:
        return None, "UNDEFINED_RISK"           # a short with no protective long
    long_k, long_p = (k1, p1) if a1 == "BUY" else (k2, p2)
    short_k, short_p = (k1, p1) if a1 == "SELL" else (k2, p2)
    width = abs(long_k - short_k)
    if width <= 0:
        return None, "STRUCTURE_UNSUPPORTED"    # degenerate (same strike)
    net_credit_per_lot = short_p - long_p
    if net_credit_per_lot > width:
        return None, "QUOTE_IMPLAUSIBLE"        # credit can't exceed the width
    return width - net_credit_per_lot, None


# ---- 5.3/5.4 Capital, deployment, concentration & margin gates ----------------

def _headroom_lots(headroom: float, per_lot: float) -> int:
    if per_lot <= 0:
        return 0
    return max(0, floor(headroom / per_lot))


# ---- 5.5 Position sizing --------------------------------------------------------

def _reconcile(ceilings: dict[str, int], requested: int) -> tuple[int, str]:
    """5.5.1 — the binding minimum across every lot ceiling, requested included."""
    binding = min(ceilings, key=lambda k: ceilings[k])
    lots = min(min(ceilings.values()), requested)
    if lots == requested:
        binding = "requested"
    return lots, binding


# ---- Public entrypoint ----------------------------------------------------------

def evaluate(plan: dict, account: dict, cfg: dict | None = None) -> RiskVerdict:
    cfg = cfg or config.DEFAULTS
    reasons: list[str] = []          # hard gates — any entry here forces REJECTED
    info_reasons: list[str] = []     # informational annotations — never force reject

    err = _validate(plan, account)
    if err:
        return _reject(err)

    max_loss_per_lot, err = _derive_max_loss_per_lot(plan["legs"])
    if err:
        return _reject(err)
    if plan.get("claimed_max_loss_per_lot") is not None:
        if abs(plan["claimed_max_loss_per_lot"] - max_loss_per_lot) > 0.01:
            info_reasons.append("PLAN_UNDERSTATED_RISK" if plan["claimed_max_loss_per_lot"]
                                < max_loss_per_lot else "PLAN_OVERSTATED_RISK")

    lot_size = plan.get("lot_size")
    if not lot_size or lot_size <= 0:
        return _reject("REFERENCE_MISSING")
    max_loss_per_lot_money = max_loss_per_lot * lot_size

    # ---- 5.6 Loss & drawdown hard gates (evaluated first — hard stops precede sizing) --
    daily_cap = cfg.get("risk.daily_loss.inr", 20000)
    if account["realized_pnl_today"] <= -daily_cap:
        reasons.append("DAILY_LOSS_CAP_HIT")

    dd_floor_pct = cfg.get("risk.dd.floor.pct", 10) / 100.0
    floor_equity = account["peak_equity"] * (1 - dd_floor_pct)
    if account["current_equity"] <= floor_equity:
        reasons.append("DRAWDOWN_FLOOR_HIT")

    # ---- 5.7 Count gates ----------------------------------------------------------
    family = plan["family_id"]
    reentry_count = account.get("reentries_today_by_family", {}).get(family, 0)
    if reentry_count >= cfg.get("risk.reentry.max", 2):
        reasons.append("REENTRY_LIMIT_HIT")

    max_concurrent = cfg.get("risk.concurrent.max", 1)
    if account["open_count"] >= max_concurrent:
        reasons.append("MAX_CONCURRENT_HIT")

    if account["trades_today"] >= cfg.get("risk.trades_per_day.max", 10):
        reasons.append("DAILY_TRADE_LIMIT_HIT")

    if account.get("duplicate_suspected"):
        reasons.append("DUPLICATE_SUSPECTED")

    # ---- Real broker margin sanity check (optional — only when the caller supplies
    # it, e.g. AtomState.derive_account()'s antariksh broker_limits.json read). This is
    # the REAL shared account's headroom (covers every system on the box), an
    # independent sanity check — not a substitute for the capital/deployment gates
    # above, which are ATOM's own strategy-scoped budget. Absence/staleness is
    # informational only (fail-open on unknown, per Module 11's secondary-signal
    # stance) — only a CONFIRMED low reading hard-blocks.
    if "broker_margin_available" in account:
        if account["broker_margin_available"]:
            floor = cfg.get("risk.broker_margin_floor_inr", 50000)
            if account.get("broker_free_margin", 0) < floor:
                reasons.append("BROKER_MARGIN_LOW")
        else:
            info_reasons.append(f"BROKER_MARGIN_UNKNOWN:{account.get('broker_margin_reason')}")

    hard_gates_tripped = bool(reasons)   # every reason appended so far is a hard gate

    # ---- 5.2.3 Portfolio risk-after overlay (additive, no netting) ----------------
    reserved_open = account["reserved_risk_open"]

    # ---- 5.3 Capital & deployment caps → resize-style lot ceilings ----------------
    atrisk_ceiling = cfg.get("risk.deploy.inr", 200000)
    atrisk_headroom = atrisk_ceiling - reserved_open
    if atrisk_headroom < 0:
        reasons.append("OVER_CAP_ALREADY")
    lots_atrisk = _headroom_lots(max(atrisk_headroom, 0), max_loss_per_lot_money)

    concentration_ceiling = atrisk_ceiling * cfg.get("risk.concentration.pct", 50) / 100.0
    lots_concentration = _headroom_lots(concentration_ceiling, max_loss_per_lot_money)

    deploy_ceiling = account["capital"] * cfg.get("risk.deployment.pct", 80) / 100.0
    deploy_headroom = deploy_ceiling - account["deployed"]
    margin_per_lot = max_loss_per_lot_money * cfg.get("risk.margin.pct_of_maxloss", 100) / 100.0
    margin_per_lot *= 1 + cfg.get("risk.margin.buffer.pct", 10) / 100.0
    lots_deploy = _headroom_lots(max(deploy_headroom, 0), margin_per_lot)

    # ---- 5.4 Margin sufficiency (free capital, distinct check) --------------------
    free_margin = account["capital"] - account["deployed"]
    lots_margin = _headroom_lots(max(free_margin, 0), margin_per_lot)

    ceilings = {
        "at_risk": lots_atrisk, "concentration": lots_concentration,
        "deployment": lots_deploy, "margin": lots_margin,
    }
    lots_allowed, binding = _reconcile(ceilings, plan["requested_qty"])

    if lots_allowed == 0 and not hard_gates_tripped:
        reasons.append("BELOW_VIABLE_NO_FIT" if plan["requested_qty"] > 0 else "INPUT_INVALID")

    # ---- 5.8 Verdict assembly ------------------------------------------------------
    if hard_gates_tripped or lots_allowed == 0:
        return RiskVerdict(REJECTED, 0, None, 0.0, 0.0, 0.0, "n/a", tuple(reasons + info_reasons))

    final_qty = lots_allowed
    verdict = APPROVED if final_qty == plan["requested_qty"] else RESIZED
    if verdict == RESIZED:
        reasons.append(f"{binding.upper()}_BINDING")
    elif not info_reasons:
        reasons.append("APPROVED_CLEAN")
    reasons.extend(info_reasons)
    return RiskVerdict(
        verdict=verdict, permitted_qty=final_qty, variant=plan.get("variant", "full"),
        max_loss_total=round(max_loss_per_lot_money * final_qty, 2),
        max_loss_per_lot=round(max_loss_per_lot_money, 2),
        margin_blocked=round(margin_per_lot * final_qty, 2),
        sizing_basis=binding, reasons=tuple(reasons),
    )


def plan_from_paper_order(order, requested_qty: int = 1,
                          family_id: str | None = None, variant: str = "full") -> dict:
    """Adapter: build Module 5's plain-data plan from Module 4's real `PaperOrder`
    (phase1.py). Note `order.lot` is the contract LOT SIZE (units per lot, e.g. 65 for
    NIFTY) not a quantity — Phase 1/2 always build exactly 1 lot's worth, so
    `order.max_loss` (already lot-size-scaled) IS the per-lot figure Module 5 wants, and
    `requested_qty` defaults to 1. `family_id` defaults to the structure name (matches
    memory's per-signal-family re-entry counting when no finer family id is supplied)."""
    return {
        "legs": order.legs, "requested_qty": requested_qty,
        "variant": variant, "claimed_max_loss_per_lot": order.max_loss,
        "family_id": family_id or order.structure, "index": order.index,
        "lot_size": order.lot,
    }
