# Module 5 — Risk & Sizing

## 5.0 Module Overview

**Role.** Module 5 is the hard, deterministic gate that every proposed order plan must clear before it can become a live order. It does three jobs, in this order:

1. **Veto.** Decide whether an order is *allowed at all* given the current state of the account (capital deployed, drawdown, daily losses, re-entry counts, concurrent positions).
2. **Size.** If allowed, compute *how big* the position may be (lots / contract variant), from a capital budget and a margin/risk budget, never exceeding any limit.
3. **Stamp.** Attach the *risk envelope* to the approved order — permitted quantity, max theoretical loss, and the per-position limits that downstream monitoring will enforce.

**Three possible verdicts:** `APPROVED` (as requested), `RESIZED` (smaller than requested but non-zero), `REJECTED` (zero — no order).

**Core design stance.** This module is *deterministic and non-overridable*. Given identical inputs it must return an identical verdict; no LLM, no randomness, no discretionary "just this once." It is the last line of defense against the rest of the system (including any model-driven signal source) over-committing capital. It fails **closed**: any ambiguity, missing input, or internal error resolves to REJECT, never APPROVE.

**Boundary discipline (what this module is NOT).** It does not pick the strategy, choose strikes, time entries, or monitor open positions. It receives a *finished plan* and account *facts* as plain data, and returns a *verdict* as plain data. It assumes nothing about the broader architecture beyond these contracts. It does not place orders, talk to the broker, or move money — it only authorizes.

**Inputs (plain data).**
- *Order plan:* list of legs (right CE/PE, buy/sell, strike, expiry), requested quantity (lots), the contract variant available (full / mini / micro), and a claimed max-theoretical-loss-per-lot (to be re-derived, not trusted).
- *Account/position status:* total capital, capital currently deployed / margin blocked, realized P&L today, open positions and their reserved risk, count of trades today, count of re-entries on this signal/family today, current drawdown vs peak/floor.
- *Reference data:* lot size per variant, current margin requirement per lot for the structure.

**Outputs (plain data).**
- `verdict ∈ {APPROVED, RESIZED, REJECTED}`
- `permitted_quantity` (lots; 0 iff REJECTED)
- `contract_variant` chosen (full / mini / micro)
- `risk_envelope`: `{ max_loss_total, max_loss_per_lot, margin_blocked, sizing_basis }`
- `reasons[]`: machine-readable codes for every binding constraint (auditability)

**Decomposition map.**
- **5.1** Input intake, validation & normalization (fail-closed front door)
- **5.2** Max-loss computation for the structure (the risk denominator)
- **5.3** Capital & deployment-cap checks
- **5.4** Margin sufficiency check
- **5.5** Position sizing (budget → lots, contract-variant selection)
- **5.6** Loss & drawdown gates (daily-loss cap, drawdown floor)
- **5.7** Count gates (re-entry limits, concurrent-position limits)
- **5.8** Verdict assembly: resize-vs-reject arbitration & envelope stamping
- **5.9** Non-overridability, determinism & audit guarantees

---

## 5.1 Input intake, validation & normalization

Front door. Every downstream check assumes clean, complete, internally-consistent data. This sub-tree guarantees that or rejects.

### 5.1.1 Schema & completeness validation *(leaf)*
- **Responsibility** — Confirm the order plan and account status contain every required field with correct types/ranges.
- **Behavior / Actions** — Check presence and type of: legs[], requested_qty, variant, capital, deployed, realized_pnl_today, counts, drawdown state. Reject on any missing/malformed field. No defaulting of risk-relevant fields.
- **Scenarios & Possibilities** — Missing capital figure; null realized P&L; negative quantity; quantity = 0; non-integer lots; unknown contract variant string; legs[] empty; NaN/Inf in a numeric field; duplicated leg objects.
- **Functional Test Case(s)** —
  - *Given* a plan with `requested_qty` absent, *When* validated, *Then* REJECT with `INPUT_INCOMPLETE`.
  - *Given* `capital = NaN`, *When* validated, *Then* REJECT with `INPUT_INVALID`.
  - *Given* a well-formed plan, *When* validated, *Then* pass through unchanged.
- **Clear Outcome** — Only structurally valid, fully-populated inputs proceed; everything else → REJECT, no partial processing.

### 5.1.2 Internal consistency & sanity bounds *(leaf)*
- **Responsibility** — Catch logically impossible or absurd values that pass type checks.
- **Behavior / Actions** — Assert deployed ≤ capital; open exposure ≥ 0; counts ≥ 0; requested_qty within an absolute hard ceiling; expiry not in the past; all legs share the same expiry/underlying for a defined-risk structure (or flag mixed-expiry as out-of-scope → reject).
- **Scenarios & Possibilities** — Deployed > capital (state corruption); realized P&L today a fantastical magnitude; requested 10,000 lots; expired contract; legs spanning two expiries; mismatched underlying across legs.
- **Functional Test Case(s)** —
  - *Given* `deployed = 1.2 × capital`, *When* checked, *Then* REJECT `STATE_INCONSISTENT` (do not silently clamp).
  - *Given* requested_qty above the absolute lot ceiling, *When* checked, *Then* flag for resize-down (not outright reject) — pass to sizing with a cap.
  - *Given* legs with two different expiries, *When* checked, *Then* REJECT `STRUCTURE_UNSUPPORTED`.
- **Clear Outcome** — Impossible states reject; merely-too-large requests are marked for clamping downstream.

### 5.1.3 Normalization to canonical units *(leaf)*
- **Responsibility** — Convert all inputs to one internal unit system (lots ↔ contracts, per-lot ↔ total, currency).
- **Behavior / Actions** — Resolve variant → lot_size; express quantities consistently; carry both per-lot and total forms; round only at defined points (lots are integers, money to paise). No floating rounding that *increases* risk.
- **Scenarios & Possibilities** — Mini/micro lot-size differs from full; rounding a fractional lot up vs down (must round *down* for risk); currency precision drift; variant declared but lot_size reference missing.
- **Functional Test Case(s)** —
  - *Given* variant = micro with lot_size L, *When* normalizing 3 lots, *Then* contracts = 3×L exactly.
  - *Given* a sizing result of 4.7 lots, *When* normalizing, *Then* floor to 4 (never 5).
  - *Given* variant present but no lot_size reference, *When* normalizing, *Then* REJECT `REFERENCE_MISSING`.
- **Clear Outcome** — One unambiguous canonical representation; all rounding is risk-decreasing.

---

## 5.2 Max-loss computation for the structure

The risk denominator. Every budget/limit check divides by, or compares against, the structure's true worst-case loss. This must be **re-derived**, never trusted from the plan.

### 5.2.1 Defined-risk structure recognition *(leaf)*
- **Responsibility** — Classify the leg set as a known defined-risk structure (vertical, iron fly, iron condor, etc.) or reject as unsupported.
- **Behavior / Actions** — Pattern-match legs (rights, buy/sell, strike ordering) to a whitelist of defined-risk shapes. Reject any structure with theoretically unbounded loss (naked/uncovered short).
- **Scenarios & Possibilities** — A short leg with no protective long → unbounded → reject. Iron fly recognized. Broken-wing variant. Calendar (mixed expiry, already excluded in 5.1). A "spread" whose long is farther OTM than the short (inverted, not actually capped).
- **Functional Test Case(s)** —
  - *Given* a lone short CE with no long, *When* classified, *Then* REJECT `UNDEFINED_RISK`.
  - *Given* a balanced iron fly, *When* classified, *Then* recognized → proceed.
  - *Given* a short with a long on the *wrong* side of the strike, *When* classified, *Then* REJECT `RISK_NOT_CAPPED`.
- **Clear Outcome** — Only structures with a finite, computable worst case proceed.

### 5.2.2 Worst-case loss-per-lot derivation *(leaf)*
- **Responsibility** — Compute the maximum theoretical loss per lot from leg geometry and net credit/debit.
- **Behavior / Actions** — For each defined-risk shape, apply its closed-form worst case: e.g. vertical = (width of strikes − net credit) × lot_size; iron fly/condor = (widest wing − net credit) × lot_size. Use the *worst* side for asymmetric structures. Include known per-lot transaction/cost loading if provided; otherwise compute gross and flag.
- **Scenarios & Possibilities** — Net credit exceeds width (impossible/mispriced quote → reject). Asymmetric wings (take the larger). Zero-width (same strikes → degenerate → reject). Plan's claimed max-loss disagrees with derived → trust derived, log discrepancy. Fees turning a tiny credit negative.
- **Functional Test Case(s)** —
  - *Given* a 100-wide vertical with ₹30 credit, lot_size 75, *When* derived, *Then* max_loss_per_lot = (100−30)×75 = ₹5,250.
  - *Given* plan claims ₹3,000 but derivation says ₹5,250, *When* compared, *Then* use ₹5,250, emit `PLAN_UNDERSTATED_RISK`.
  - *Given* credit > width, *When* derived, *Then* REJECT `QUOTE_IMPLAUSIBLE`.
- **Clear Outcome** — A trustworthy, conservative max_loss_per_lot; the plan's self-reported figure is never load-bearing.

### 5.2.3 Total max-loss scaling & worst-case overlay *(leaf)*
- **Responsibility** — Scale per-lot loss to the proposed quantity and combine with already-reserved risk from open positions.
- **Behavior / Actions** — total_new_risk = max_loss_per_lot × qty. portfolio_risk_after = reserved_risk_open + total_new_risk. Optionally apply a correlation/assumption overlay (e.g. same-underlying same-side concentration), conservatively additive — never assume offsetting hedges across independent positions.
- **Scenarios & Possibilities** — Two positions on the same index assumed to net out (do NOT — treat additively unless provably a single defined structure). Integer-overflow on absurd qty (already capped in 5.1). Reserved risk stale/missing → treat as worst case (assume fully reserved).
- **Functional Test Case(s)** —
  - *Given* per-lot ₹5,250 and qty 4, *When* scaled, *Then* total_new_risk = ₹21,000.
  - *Given* reserved_open missing, *When* combining, *Then* assume max prior commitment, do not assume 0.
  - *Given* two same-index shorts, *When* combining, *Then* sum risks (no netting).
- **Clear Outcome** — A single conservative portfolio-risk-after-this-trade figure feeds all budget gates.

---

## 5.3 Capital & deployment-cap checks

Caps total capital at risk / margin deployed across the book.

### 5.3.1 Absolute capital-at-risk cap *(leaf)*
- **Responsibility** — Ensure portfolio risk after this trade ≤ the capital-at-risk ceiling.
- **Behavior / Actions** — Compare portfolio_risk_after (from 5.2.3) to the at-risk ceiling. If over, compute the *headroom* and pass it to sizing (resize) rather than reject outright when ≥ 1 lot fits; reject only if not even one lot fits.
- **Scenarios & Possibilities** — Exactly at the cap (boundary — allow vs deny; define inclusive ≤). One lot fits but requested 4 → resize to headroom. Zero headroom → reject. Negative headroom because open risk already exceeds cap (state drift) → reject all new.
- **Functional Test Case(s)** —
  - *Given* ceiling ₹50k, open risk ₹40k, per-lot ₹5,250, *When* checked, *Then* headroom ₹10k → max 1 lot → RESIZE.
  - *Given* open risk already ₹52k > ceiling, *When* checked, *Then* REJECT `OVER_CAP_ALREADY`.
  - *Given* portfolio_risk_after exactly = ceiling, *When* checked, *Then* APPROVE (≤ inclusive).
- **Clear Outcome** — New risk can never push the book past the at-risk ceiling.

### 5.3.2 Deployment / buying-power cap *(leaf)*
- **Responsibility** — Ensure capital/margin *deployed* (not just at-risk) stays within the allowed deployment fraction.
- **Behavior / Actions** — Compute deployed_after = deployed + margin_for_this_trade. Compare to deployment ceiling (a fraction of capital). Distinguish *deployed* (margin blocked) from *at-risk* (max loss) — both caps apply independently; the binding one wins.
- **Scenarios & Possibilities** — At-risk fine but margin-deployment maxed (credit spread with high margin) → deployment binds. Free cash buffer must remain (never deploy 100%). Deployment cap and at-risk cap give different lot counts → take the smaller.
- **Functional Test Case(s)** —
  - *Given* deployment ceiling 80% of ₹2L = ₹1.6L, deployed ₹1.5L, margin/lot ₹40k, *When* checked, *Then* only ₹10k headroom → 0 lots → REJECT or RESIZE-to-0.
  - *Given* at-risk allows 4 lots but deployment allows 2, *When* combined, *Then* cap at 2.
- **Clear Outcome** — Buying-power exhaustion and a minimum cash buffer are both respected.

### 5.3.3 Single-trade concentration cap *(leaf)*
- **Responsibility** — Forbid any single position from consuming more than a fixed share of capital/risk.
- **Behavior / Actions** — Cap per-trade risk at a max fraction (e.g. no one trade may risk more than X% of capital). Resize down to the per-trade ceiling even if portfolio headroom is larger.
- **Scenarios & Possibilities** — Book is empty so portfolio cap is generous, but a single jumbo order would over-concentrate → clamp. Tiny capital makes even 1 lot exceed the per-trade %; then 1-lot floor vs concentration cap conflict (escalate to 5.8 arbitration).
- **Functional Test Case(s)** —
  - *Given* per-trade cap 10% of ₹2L = ₹20k, requested risk ₹40k, *When* checked, *Then* RESIZE to ≤ ₹20k of lots.
  - *Given* 1 lot already exceeds the per-trade cap, *When* checked, *Then* defer to 5.8 (reject vs floor-override decision).
- **Clear Outcome** — No single position dominates the book regardless of free headroom.

---

## 5.4 Margin sufficiency check

Distinct from at-risk: can the account actually *post* the margin to open and hold the position intraday?

### 5.4.1 Required-margin estimation *(leaf)*
- **Responsibility** — Estimate margin required to carry the defined-risk structure per lot and total.
- **Behavior / Actions** — Use provided margin/lot reference for the structure; for defined-risk spreads margin ≈ max loss + costs, but use the reference figure when supplied (broker SPAN+exposure can differ). Multiply by qty. Add a margin safety buffer for intraday variation.
- **Scenarios & Possibilities** — Margin reference stale or missing → fail closed (assume high / reject). Spread margin benefit not granted by broker (legs not recognized as a hedge) → margin = sum of naked legs (much higher). Buffer too thin → MTM spike triggers shortfall later.
- **Functional Test Case(s)** —
  - *Given* margin/lot ₹40k, qty 3, buffer 10%, *When* estimated, *Then* required ≈ ₹132k.
  - *Given* margin reference missing, *When* estimated, *Then* REJECT `MARGIN_REF_MISSING` (fail closed).
- **Clear Outcome** — A conservative total-margin requirement, buffered for intraday MTM.

### 5.4.2 Available-margin comparison & shortfall handling *(leaf)*
- **Responsibility** — Ensure free margin ≥ required; resize or reject on shortfall.
- **Behavior / Actions** — free_margin = capital − deployed (− buffer reserve). If required > free, compute max lots that fit, resize; if < 1 lot fits, reject. Never approve an order the account cannot margin.
- **Scenarios & Possibilities** — Exactly enough for requested (boundary). Enough for 2 of 4 → resize. Not enough for 1 → reject. Free margin negative (over-deployed) → reject all. Margin call risk if buffer consumed.
- **Functional Test Case(s)** —
  - *Given* free ₹100k, margin/lot ₹40k, *When* compared, *Then* max 2 lots → RESIZE if requested > 2.
  - *Given* free ₹30k, margin/lot ₹40k, *When* compared, *Then* REJECT `INSUFFICIENT_MARGIN`.
- **Clear Outcome** — Every approved position is fully marginable with buffer intact.

---

## 5.5 Position sizing (budget → lots, contract-variant selection)

Turns the surviving budget headrooms into a concrete integer lot count and contract variant.

### 5.5.1 Budget reconciliation (minimum of all constraints) *(leaf)*
- **Responsibility** — Take the lot ceilings from every prior gate and pick the binding minimum.
- **Behavior / Actions** — lots_allowed = min(at-risk headroom lots, deployment headroom lots, concentration-cap lots, margin-fit lots, absolute hard cap, requested qty). Always floor to integer. Record which constraint bound (for reasons[]).
- **Scenarios & Possibilities** — Multiple constraints tie at the same lot count. Requested is already the smallest → APPROVE as-is. All constraints generous but requested small. One constraint yields 0 → propagate 0 (reject path).
- **Functional Test Case(s)** —
  - *Given* ceilings [risk 4, deploy 2, margin 3, requested 5], *When* reconciled, *Then* lots_allowed = 2, binding = deployment.
  - *Given* requested 1 and all ceilings ≥ 1, *When* reconciled, *Then* APPROVE 1.
  - *Given* any ceiling = 0, *When* reconciled, *Then* lots_allowed = 0 → reject path.
- **Clear Outcome** — A single integer lot count = the most conservative binding limit, with the binding reason captured.

### 5.5.2 Contract-variant selection (full / mini / micro) *(leaf)*
- **Responsibility** — Choose the contract variant that best fits the budget, preferring smaller granularity to fit constraints.
- **Behavior / Actions** — If full-size lots don't fit even 1, attempt mini, then micro (finer granularity → more precise sizing within budget). Prefer the variant that maximizes budget utilization without exceeding any cap, biased toward smaller contracts when capital/liquidity is tight. Re-run the relevant per-lot figures (max-loss, margin) for the chosen variant.
- **Scenarios & Possibilities** — Full doesn't fit, micro does → downsize variant rather than reject. Variant not offered for this underlying (no micro on SENSEX, etc.) → skip to next available. Mixing variants in one structure (disallow — single variant per position). Micro available but illiquid (out of this module's view; flag as suggestion).
- **Functional Test Case(s)** —
  - *Given* budget fits 0 full but 3 micro lots, *When* selecting, *Then* choose micro, qty 3, RESIZE.
  - *Given* requested full fits, *When* selecting, *Then* keep full (don't gratuitously downsize).
  - *Given* micro not listed for the underlying, *When* selecting, *Then* fall back to mini/full or reject if none fit.
- **Clear Outcome** — The finest variant needed to fit budget is chosen; per-lot risk/margin recomputed for it.

### 5.5.3 Minimum viable size & floor enforcement *(leaf)*
- **Responsibility** — Decide whether a sub-minimum result becomes a 1-lot floor or a reject.
- **Behavior / Actions** — If reconciled lots = 0 across all variants, the position cannot open → reject. If a non-zero result is below a "min viable" threshold, apply policy: open at the floor only if the floor still respects every hard cap; otherwise reject. Never breach a hard cap to honor a floor.
- **Scenarios & Possibilities** — Smallest possible (1 micro) still violates the at-risk cap → must reject, not floor. Floor honored because 1 micro fits all caps. Conflict between concentration cap (5.3.3) and a 1-lot floor → reject wins (cap is hard).
- **Functional Test Case(s)** —
  - *Given* even 1 micro breaches the at-risk cap, *When* floored, *Then* REJECT `BELOW_VIABLE_NO_FIT`.
  - *Given* 1 micro fits all caps, *When* floored, *Then* APPROVE/RESIZE to 1 micro.
- **Clear Outcome** — A floor is offered only when it violates nothing hard; otherwise clean reject.

---

## 5.6 Loss & drawdown gates

Time-based circuit breakers on the *account*, independent of the individual order's geometry.

### 5.6.1 Daily-loss cap (kill-switch) *(leaf)*
- **Responsibility** — Block all new orders once realized loss today reaches the daily cap.
- **Behavior / Actions** — If realized_pnl_today ≤ −daily_loss_cap, reject every new order regardless of size (hard stop for the day). If a *new* trade's worst case could push the *realized+worst* past the cap, optionally resize so worst-case stays within (configurable; conservative default = block once cap hit).
- **Scenarios & Possibilities** — Exactly at the cap (inclusive → blocked). Loss just under cap but a new max-loss would blow through it → resize/reject. Realized P&L positive → gate open. Mid-day cap reached then market reverses (still blocked — deterministic, no re-arming intraday).
- **Functional Test Case(s)** —
  - *Given* daily cap ₹10k and realized −₹10k, *When* gated, *Then* REJECT `DAILY_LOSS_CAP_HIT`.
  - *Given* realized −₹8k, new worst-case −₹5k, cap ₹10k, *When* gated, *Then* resize so realized+worst ≥ −₹10k (≤ 2k new worst → may reject if 1 lot exceeds).
  - *Given* realized +₹3k, *When* gated, *Then* pass.
- **Clear Outcome** — Once the day's loss budget is spent, no new risk is admitted for the rest of the session.

### 5.6.2 Drawdown floor (equity protection) *(leaf)*
- **Responsibility** — Halt new deployment when account equity falls to the protected floor.
- **Behavior / Actions** — Compare current equity (capital + realized + open MTM if provided) against the drawdown floor (e.g. a % below peak). At/below floor → reject all new. Approaching floor → tighten available budget so a new worst-case cannot breach the floor.
- **Scenarios & Possibilities** — Equity exactly at floor (blocked). Floor defined off peak equity vs off starting capital (use the stricter). Open MTM unknown → assume worst (use reserved risk). Floor breach should also imply downstream square-off (not this module's job — emit suggestion).
- **Functional Test Case(s)** —
  - *Given* floor = 90% of peak, equity at 90%, *When* gated, *Then* REJECT `DRAWDOWN_FLOOR_HIT`.
  - *Given* equity at 92%, a new worst-case would drop to 89%, *When* gated, *Then* resize so post-worst ≥ 90% or reject.
- **Clear Outcome** — Capital cannot be deployed in a way that risks breaching the protective floor.

### 5.6.3 Combined gate precedence *(leaf)*
- **Responsibility** — Resolve interaction order between daily-loss, drawdown, and capital gates.
- **Behavior / Actions** — Evaluate all account-level circuit breakers before sizing economics; any single hard-stop short-circuits to REJECT. Sizing-style (resize-down) gates combine via minimum. Record every triggered gate, not just the first.
- **Scenarios & Possibilities** — Both daily-cap and drawdown trip together. A resize gate and a hard gate co-occur (hard wins). Need full reason list for audit even when first gate already rejects.
- **Functional Test Case(s)** —
  - *Given* daily-cap hit AND drawdown OK, *When* combined, *Then* REJECT, reasons include `DAILY_LOSS_CAP_HIT`.
  - *Given* drawdown hard-stop AND a resize from margin, *When* combined, *Then* REJECT (hard precedence), reasons list both.
- **Clear Outcome** — Deterministic precedence; hard stops always dominate; complete reason set emitted.

---

## 5.7 Count gates (re-entry & concurrency limits)

Limits on *frequency* and *simultaneity*, independent of money.

### 5.7.1 Re-entry counting per signal/family *(leaf)*
- **Responsibility** — Enforce the max number of re-entries allowed on a given signal/family/day.
- **Behavior / Actions** — Read re-entry count for this signal family today; if ≥ allowed re-entries, reject. Increment only on actual approval (idempotent — counting is the caller's state, this module reads and judges). Distinguish fresh entry vs re-entry by plan metadata.
- **Scenarios & Possibilities** — Already at re-entry limit → reject. Off-by-one (does "2 re-entries" mean 3 total positions?) — define explicitly. Missing family id → treat conservatively (cannot prove it's a fresh entry → count against most-constrained family or reject). Rapid re-fire same second.
- **Functional Test Case(s)** —
  - *Given* re-entry limit 2 and count = 2, *When* gated, *Then* REJECT `REENTRY_LIMIT_HIT`.
  - *Given* count = 1, limit 2, *When* gated, *Then* pass (this would be the 2nd re-entry).
  - *Given* no family id, *When* gated, *Then* REJECT `FAMILY_UNRESOLVED` (fail closed).
- **Clear Outcome** — Re-entries never exceed the configured count; ambiguity fails closed.

### 5.7.2 Concurrent-position / max-open limit *(leaf)*
- **Responsibility** — Cap how many positions may be open at once (overall and per-underlying).
- **Behavior / Actions** — Compare current open count to the concurrency ceiling (global and per-index). At/over → reject new. Optionally enforce single-position mode (only one open at a time).
- **Scenarios & Possibilities** — One-position-at-a-time policy and a position already open → reject. Per-index cap reached on NIFTY but SENSEX free. Open count stale (a position closed but not yet reflected) → use provided count, fail closed if inconsistent. Pending/partially-filled counted or not.
- **Functional Test Case(s)** —
  - *Given* single-position mode and 1 open, *When* gated, *Then* REJECT `MAX_CONCURRENT_HIT`.
  - *Given* per-index cap 1, NIFTY open, SENSEX request, *When* gated, *Then* pass.
- **Clear Outcome** — Simultaneous exposure never exceeds the concurrency policy.

### 5.7.3 Total-trades-per-day & throttle *(leaf)*
- **Responsibility** — Cap total entries per day and prevent rapid-fire duplicate submissions.
- **Behavior / Actions** — Reject once daily trade count ≥ max trades/day. Detect near-duplicate plans within a short window (same legs, same family) and reject the duplicate to avoid double-fills.
- **Scenarios & Possibilities** — Trade count at max → reject. Legitimate different trade vs accidental resubmission. Retry after a transient downstream error looking like a duplicate (idempotency key needed — flag as suggestion). End-of-day flurry hitting the cap.
- **Functional Test Case(s)** —
  - *Given* max 5 trades/day and count = 5, *When* gated, *Then* REJECT `DAILY_TRADE_LIMIT_HIT`.
  - *Given* an identical plan 1s after an approval, *When* gated, *Then* REJECT `DUPLICATE_SUSPECTED`.
- **Clear Outcome** — Daily activity is bounded and accidental double-entries are screened.

---

## 5.8 Verdict assembly: resize-vs-reject & envelope stamping

Consolidates every gate's output into the single authoritative verdict and risk envelope.

### 5.8.1 Resize-vs-reject arbitration *(leaf)*
- **Responsibility** — Decide the final verdict from the collected constraints.
- **Behavior / Actions** — If any *hard* gate (counts, daily-cap, drawdown floor, undefined-risk, insufficient-margin-for-1-lot) tripped → REJECT. Else final_qty = reconciled lots (5.5). If final_qty = 0 → REJECT. If final_qty < requested → RESIZED. If final_qty = requested → APPROVED.
- **Scenarios & Possibilities** — Resize collides with a hard reject (hard wins). final_qty equals requested only because requested was already tiny. Resize to a variant change (RESIZED even if lot number same but variant downsized). Everything passes → APPROVED.
- **Functional Test Case(s)** —
  - *Given* reconciled 2, requested 4, no hard gate, *When* arbitrated, *Then* RESIZED qty 2.
  - *Given* reconciled 4 but re-entry limit hit, *When* arbitrated, *Then* REJECTED.
  - *Given* reconciled = requested = 3, *When* arbitrated, *Then* APPROVED.
- **Clear Outcome** — Exactly one verdict; hard gates always override sizing.

### 5.8.2 Risk-envelope stamping *(leaf)*
- **Responsibility** — Attach the permitted quantity, variant, and concrete risk limits to the approved/resized order.
- **Behavior / Actions** — Emit `{ permitted_qty, variant, max_loss_total, max_loss_per_lot, margin_blocked, sizing_basis }` computed for the *final* qty/variant (re-derived, not the requested figures). For REJECT, qty=0 and a null envelope.
- **Scenarios & Possibilities** — Envelope must reflect the resized (smaller) numbers, not the requested. Variant changed → recompute everything for that variant. Downstream monitoring relies on these exact limits (must be self-consistent).
- **Functional Test Case(s)** —
  - *Given* resize to 2 micro lots, *When* stamping, *Then* max_loss_total = per-micro-lot × 2, margin for micro × 2.
  - *Given* REJECT, *When* stamping, *Then* qty 0, envelope null, reasons populated.
- **Clear Outcome** — A complete, internally-consistent envelope keyed to the actually-permitted size.

### 5.8.3 Reason codes & decision trace *(leaf)*
- **Responsibility** — Emit a machine-readable, complete record of every binding constraint and the final decision.
- **Behavior / Actions** — Collect reasons[] from all gates (binding and tripped), the binding constraint id, input snapshot hash, and verdict. Deterministic ordering for reproducibility.
- **Scenarios & Possibilities** — Multiple binding constraints (list all). Audit needs to replay the decision. Empty reasons on a clean APPROVE (still emit an `APPROVED_CLEAN` marker).
- **Functional Test Case(s)** —
  - *Given* deployment-bound resize, *When* tracing, *Then* reasons include `DEPLOY_CAP_BINDING` with the headroom value.
  - *Given* a clean approve, *When* tracing, *Then* reasons = [`APPROVED_CLEAN`].
- **Clear Outcome** — Every verdict is fully explainable and replayable from its trace.

---

## 5.9 Non-overridability, determinism & audit guarantees

The meta-guarantees that make this module a trustworthy gate.

### 5.9.1 Determinism & purity *(leaf)*
- **Responsibility** — Same inputs → same verdict, always; no hidden state, no randomness, no clock-dependence in the decision math.
- **Behavior / Actions** — Pure function of (plan, status, references). No LLM, no RNG, no network call inside the gate. Any time-of-day logic comes from explicit input fields, not a live clock read mid-decision.
- **Scenarios & Possibilities** — A model/discretionary upstream tries to pass a "confidence" that nudges sizing (ignore — not a risk input). Hidden cache changing a second run. Float non-determinism across platforms (fix rounding rules).
- **Functional Test Case(s)** —
  - *Given* identical inputs run twice, *When* evaluated, *Then* byte-identical verdict + envelope.
  - *Given* an extra "override"/"confidence" field, *When* evaluated, *Then* it is ignored; verdict unchanged.
- **Clear Outcome** — The gate is a deterministic pure function; provably reproducible.

### 5.9.2 Non-overridability / fail-closed *(leaf)*
- **Responsibility** — No caller may bypass, soften, or force-approve the gate; all errors resolve to REJECT.
- **Behavior / Actions** — No "force" parameter honored. Any internal exception, missing reference, or unparseable input → REJECT (never APPROVE-on-error). Limits come from a trusted config source, not the order plan.
- **Scenarios & Possibilities** — Caller passes `force_approve=true` (ignored). Config load fails → reject all (don't run with default-permissive limits). Plan tries to supply its own (looser) limits → ignored, trusted config used. Partial internal failure mid-evaluation.
- **Functional Test Case(s)** —
  - *Given* `force_approve=true` on an over-cap order, *When* evaluated, *Then* still REJECTED.
  - *Given* config/reference load throws, *When* evaluated, *Then* REJECT `GATE_FAILSAFE`, never approve.
  - *Given* plan-supplied limit looser than config, *When* evaluated, *Then* config limit used.
- **Clear Outcome** — The gate cannot be coerced; every failure mode denies risk.

### 5.9.3 Audit log & idempotency of judgment *(leaf)*
- **Responsibility** — Persist an immutable record of each decision; judging twice doesn't double-count.
- **Behavior / Actions** — Write verdict + trace (5.8.3) to an append-only audit sink. Evaluation itself mutates no account state (counting/incrementing is the caller's job on actual placement) — so re-judging the same plan is safe and side-effect-free.
- **Scenarios & Possibilities** — Same plan judged, rejected, fixed, re-judged (two audit entries, no state corruption). Audit sink unavailable → still return verdict but flag (decision must not silently lose its record; policy: reject if audit cannot be written, configurable). Replay for post-mortem.
- **Functional Test Case(s)** —
  - *Given* the same plan judged twice, *When* evaluated, *Then* two audit entries, no account counters changed by the gate.
  - *Given* the audit sink is down, *When* evaluated, *Then* emit `AUDIT_UNAVAILABLE` per fail-closed policy.
- **Clear Outcome** — Every decision is recorded and replayable; judging is side-effect-free on account state.

---

## Suggestions (for bubble-up)

Market-condition scenarios that exceed a single order's gate logic and deserve **system-wide** treatment. Flagged here, not solved here.

1. **Overnight gap risk.** Intraday-flat assumption protects against held-overnight gaps, but a gap *into* the open can mark an about-to-open structure far worse than its modeled max-loss at entry. System should consider a no-entry window around the open and gap-aware sizing haircuts on high-event days.

2. **Expiry-day pin risk.** On expiry, ATM short premium decays fast but pin/gamma risk near the strike is extreme; defined-risk max-loss can be realized in minutes. Recommend expiry-day-specific caps (tighter sizing, earlier cutoff) governed centrally, since one module can't see the calendar context fully.

3. **Margin spikes on volatility.** Brokers raise SPAN/exposure margin intraday when VIX jumps; a position marginable at entry can face a shortfall later. Needs a system-level live-margin monitor and a buffer policy beyond this module's entry-time estimate.

4. **Circuit breaker / trading halt freezing exits.** If the index hits a circuit limit or the venue halts, open positions cannot be squared off — the "defined" risk can drift while exits are impossible. System-wide halt detection, pre-halt de-risking, and a hard rule against opening near known volatility-trigger levels.

5. **Liquidity / wide-spread on micro/mini variants.** Smaller contracts chosen for sizing precision may be thinly traded; slippage and unfillable exits aren't visible to a risk gate. Liquidity screening belongs upstream/centrally.

6. **Correlated concurrent positions.** This module conservatively sums risk additively. A portfolio-level view could recognize genuine offsets (or hidden concentration across NIFTY/SENSEX) more accurately than per-order logic.

7. **Stale account-state inputs.** The gate trusts the provided open-exposure/counts. A system-wide reconciliation (ledger vs broker truth) is needed so the gate isn't sizing off a split-brain view.

8. **Idempotency / duplicate-submit keys.** Cross-module idempotency keys would let the gate distinguish a legitimate re-entry from a retry-after-timeout more safely than heuristic duplicate detection.
