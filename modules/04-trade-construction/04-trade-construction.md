# Module 4 — Trade Construction

## 4.0 Module Overview

**Mission.** Module 4 is the *translator*. It receives an **abstract intent** ("open a short-premium, defined-risk, neutral structure" or "adjust the existing position toward delta-neutral") plus a **market snapshot** (spot, full option chain with prices/IV/greeks/liquidity), and emits a **concrete, executable order plan** — a set of legs, each fully specified down to contract symbol, side, quantity, and price/order-type. It does *not* decide *whether* to trade (that is upstream), and it does *not* place orders or manage fills (that is the downstream execution layer). It is the deterministic-but-judgment-laden bridge between strategy and execution.

**Boundary discipline.** Inputs are plain data; outputs are plain data. Module 4 owns no broker connection, no order-state, no P&L, no position book of record. It is *pure*: same intent + same snapshot ⇒ same plan (modulo explicitly-seeded randomness, which we avoid). This purity is what makes it testable and what makes its output auditable.

**Why this module is hard.** The intent is abstract ("neutral, defined-risk, this much capital, this much width-appetite"); the chain is discrete (strikes come in fixed steps, prices in fixed ticks, quantity in fixed lots) and *imperfect* (wide spreads, stale quotes, missing greeks, zero-bid wings). Trade Construction must map a continuous wish onto a discrete, noisy reality and either emit a *valid* plan or *refuse cleanly* with a reason. The cardinal sin is emitting a plan that looks fine on paper but is unfillable, mispriced, or risk-undefined.

**Design stance taken throughout.** Breadth over commitment: for each decision (which strike, what price, how many lots) we enumerate *multiple* selection philosophies — by delta/greek, by absolute distance, by premium target, by liquidity — because Module 4 should support a *policy* chosen upstream rather than hard-code one religion. Where the intent under-specifies, Module 4 applies *conservative, defined-risk-preserving* defaults and records that it did so.

**Top-level decomposition.**
- **4.1 Exchange / Underlying selection** — which market(s) the intent maps to (NSE NIFTY vs BSE SENSEX vs others), and how to resolve when the intent is underlying-agnostic.
- **4.2 Day / Expiry selection** — which expiry the structure lives on; expiry-day vs non-expiry-day; DTE policy.
- **4.3 Strategy / Structure selection & construction** — pick the structure template, then materialize it: legs, strike selection, wing width, lot sizing, rounding, symbol resolution.
- **4.4 Price selection** — per-leg and net order pricing: market vs limit, mid/credit targeting, slippage tolerance, tick rounding.
- **4.5 Plan assembly, validity checks & emission** — leg ordering, net-risk validation, budget/margin sanity, contract resolution, and the final gate before handing off.
- **4.6 Cross-cutting concerns** — snapshot freshness/quality, units & conventions, determinism, observability, refusal protocol.

**Output contract (the "order plan").** A structured object: `{ underlying, expiry, structure_type, legs:[{symbol, expiry, strike, right(C/P), side(B/S), qty_lots, qty_units, price_type, limit_price?}], net_order:{type, net_limit?, slippage_budget}, defined_risk:{max_loss, max_profit, breakevens}, rationale:{strike_method, sizing_method, fallbacks_applied[]}, validity:{ok, reasons[]} }`. If `validity.ok == false`, no legs are emitted for execution; the reasons explain the refusal.

---

## 4.1 Exchange / Underlying selection

Decide which exchange + underlying the intent is realized on. The seed names NSE NIFTY vs BSE SENSEX; we generalize to "the set of tradable index-option underlyings" and the policy for choosing among them.

### 4.1.1 Resolve target underlying from intent

**Responsibility** — Map the (possibly underlying-agnostic) intent to one concrete underlying.
**Behavior / Actions**
- If intent names an underlying explicitly (e.g. "SENSEX iron fly"), honor it; skip ranking.
- If intent is agnostic ("open a neutral structure"), pass candidates to 4.1.2 for ranking.
- Validate the named underlying is in the supported set and is currently tradable today (has a snapshot, not a holiday for that exchange).
**Scenarios & Possibilities**
- Intent names an underlying not in the snapshot (no chain) → refuse with `NO_CHAIN_FOR_UNDERLYING`.
- Intent names an underlying that is on exchange holiday while the other exchange is open (BSE/NSE differ on rare days) → refuse or, if intent allows substitution, defer to 4.1.2.
- Intent names a delisted/renamed product → symbol-resolution failure surfaced here, not later.
**Functional Test Case(s)**
- Given intent `{underlying: SENSEX}` and a snapshot containing a SENSEX chain, When resolving, Then target = SENSEX, no ranking performed.
- Given intent `{underlying: NIFTY}` and a snapshot with *no* NIFTY chain, When resolving, Then refuse with `NO_CHAIN_FOR_UNDERLYING`.
**Clear Outcome** — Exactly one tradable underlying selected, or a clean refusal with a coded reason. Never a silent substitution unless the intent explicitly permits it.

### 4.1.2 Rank candidate underlyings (agnostic intent)

**Responsibility** — When intent is agnostic, choose the best underlying by a transparent score.
**Behavior / Actions**
- Build candidate list = supported underlyings with a fresh chain today.
- Score each on policy-relevant axes, *all from the snapshot* (no external data): chain liquidity (median bid/ask spread, OI/volume at relevant strikes), expiry availability matching 4.2's need, IV level/theta richness for a premium-selling intent, and lot-notional vs budget (does even one lot fit?).
- Emit ranked list; pick top; record runners-up for auditability.
**Scenarios & Possibilities**
- Two underlyings tie → deterministic tiebreak (e.g. stable priority order), never random.
- Best-liquidity underlying's one-lot notional exceeds budget while a smaller-lot underlying fits → sizing feasibility must feed back into ranking (don't pick a market we can't afford even one lot of).
- All candidates fail liquidity floor → refuse `NO_LIQUID_UNDERLYING` rather than pick the "least bad".
- Expiry-day on one index but not the other changes the theta/gamma profile materially → ranking must be expiry-aware (couples to 4.2).
**Functional Test Case(s)**
- Given agnostic intent, NIFTY median spread ₹0.5 and SENSEX median spread ₹3.0 (others equal), When ranking, Then NIFTY ranked above SENSEX.
- Given agnostic intent where only SENSEX has an expiry matching the requested DTE band, When ranking, Then SENSEX selected even if NIFTY is marginally more liquid (policy-weight dependent; assert the chosen weighting produces SENSEX).
**Clear Outcome** — A deterministic ranked list with the top candidate selected, every score component logged, or a coded refusal if none clear the floors.

### 4.1.3 Per-exchange convention binding

**Responsibility** — Attach exchange-specific conventions (lot size, strike step, tick size, symbol format, trading hours, expiry calendar) to the chosen underlying.
**Behavior / Actions**
- Look up the underlying's contract spec: lot size (units/lot), strike interval, price tick, option-symbol template (NIFTY vs SENSEX symbol grammar differ).
- Bind these constants so downstream sub-modules (rounding, sizing, symbol resolution) use the *right* numbers for the *right* exchange.
**Scenarios & Possibilities**
- Stale/incorrect spec (e.g. exchange revised lot size at a series rollover) → spec must come from the snapshot/contract master, not a hard-coded constant; flag if snapshot spec conflicts with a cached one.
- SENSEX symbol grammar differs from NIFTY (per system memory, SENSEX uses a distinct symbol layout); using NIFTY grammar for a SENSEX leg yields an unresolvable contract.
- New product whose conventions are unknown → refuse `UNKNOWN_CONTRACT_SPEC`.
**Functional Test Case(s)**
- Given underlying = SENSEX, When binding conventions, Then symbol template = SENSEX grammar, strike step and lot size = SENSEX values from spec.
- Given a snapshot lot size that differs from the cached default, When binding, Then snapshot value used and a `SPEC_DRIFT` note recorded.
**Clear Outcome** — A fully-bound convention set; any leg later built uses exchange-correct lot/tick/strike/symbol rules. Conflicts flagged, not silently resolved.

---

## 4.2 Day / Expiry selection

Choose which expiry the structure occupies. Intraday-only flat-by-EOD means we are *not* holding to expiry, but the chosen expiry still governs theta/gamma profile, liquidity, and how violent the day's decay/risk is.

### 4.2.1 Enumerate available expiries

**Responsibility** — From the snapshot, list valid expiries for the chosen underlying with their DTE.
**Behavior / Actions**
- Read all expiries present in the chain; compute DTE = (expiry_date − today) in trading-day and calendar-day terms.
- Tag the nearest expiry; tag whether *today* is an expiry day for this underlying.
**Scenarios & Possibilities**
- Only one expiry present (thin chain) → no choice; pass it on, but record reduced optionality.
- Expiry calendar quirks (shifted expiry due to holiday) → DTE must use the actual expiry date from the chain, not a weekday assumption.
- Missing expiry metadata → refuse `NO_EXPIRY_METADATA`.
**Functional Test Case(s)**
- Given a chain with expiries today, +7d, +14d, When enumerating, Then three entries with DTE {0, 7, 14} and "today is expiry day" = true.
**Clear Outcome** — A clean list of `{expiry_date, dte_calendar, dte_trading, is_today_expiry}` for selection.

### 4.2.2 DTE policy selection (which expiry to use)

**Responsibility** — Pick the expiry that matches the intent's decay/risk appetite.
**Behavior / Actions**
- Apply the intent's DTE preference if given; else apply default policy. For a theta-harvest, intraday-flat strategy the natural choice is the *nearest* expiry (max theta), but nearest = expiry-day = max gamma risk.
- Support multiple philosophies and pick per intent: (a) **nearest** (max theta, max gamma), (b) **next-out** (lower gamma, smaller decay, calmer), (c) **DTE-band** (e.g. "1–3 DTE") with liquidity tiebreak.
- Couple to 4.1.2: if expiry availability differed across underlyings, ensure consistency.
**Scenarios & Possibilities**
- Today is expiry day and intent is theta-harvest: nearest expiry maximizes decay but pins/gamma risk is extreme near the money → may prefer next expiry, or keep nearest but force wider wings / tighter risk (hand a flag to 4.3). This is a key *bubble-up* candidate.
- Nearest expiry is illiquid intraday (e.g. just-listed far expiry only) → fall to next viable.
- Intent demands a DTE no expiry satisfies → choose closest available and record the deviation, or refuse if intent marks DTE as hard.
**Functional Test Case(s)**
- Given intent "nearest expiry, theta-harvest" and DTE list {0,7,14}, When selecting, Then expiry with DTE 0 chosen and `is_today_expiry` flag propagated to 4.3.
- Given intent "DTE band 2–5" and list {0,7}, When selecting, Then neither is in-band; if DTE soft → pick 7 with `DTE_DEVIATION` note; if hard → refuse `NO_EXPIRY_IN_BAND`.
**Clear Outcome** — Exactly one expiry chosen with DTE rationale; expiry-day risk flag set when relevant; deviations recorded or cleanly refused.

### 4.2.3 Expiry-day special handling hook

**Responsibility** — When today is the chosen expiry, surface the heightened-gamma context to downstream construction.
**Behavior / Actions**
- Set `expiry_day_mode = true` and attach guidance hints consumable by 4.3/4.4: prefer farther strikes, wider wings, cap size, tighter price tolerance, and watch pin risk.
- Do *not* itself change strategy — it *informs* downstream so the policy stays in one place.
**Scenarios & Possibilities**
- Expiry-day + ATM short structure = max pin/gamma → 4.3 should push strikes out; this hook is the carrier of that signal.
- Late-in-day expiry-day construction (little time left) where intraday-flat means we'd close almost immediately → may warrant refusal upstream, but Module 4 should at least flag `LATE_EXPIRY_DAY`.
**Functional Test Case(s)**
- Given selected expiry DTE = 0, When running the hook, Then `expiry_day_mode = true` and gamma-caution hints present in the plan context.
**Clear Outcome** — Downstream sub-modules receive an explicit expiry-day signal; no silent reliance on date math later.

---

## 4.3 Strategy / Structure selection & construction

The heart of the module: pick a structure template consistent with the intent, then materialize it into concrete legs. We separate *template choice* (4.3.1) from *materialization* (4.3.2–4.3.7) so the same construction engine serves many templates.

### 4.3.1 Structure template selection

**Responsibility** — Choose the option structure that satisfies the intent's directional + risk shape.
**Behavior / Actions**
- Map intent shape → template. Examples (defined-risk, premium-selling family): iron fly (ATM short straddle + protective wings), iron condor (OTM short strangle + wings), credit vertical (directional defined-risk), broken-wing variants. For an *adjust* intent: roll a leg, add a hedge wing, convert strangle→condor.
- Honor explicit template in intent; else infer from {direction: neutral/bullish/bearish, risk: defined, vol: sell} and choose the canonical template for that cell.
- Reject undefined-risk templates (naked short) — the arena is *defined-risk only*; if intent somehow implies one, refuse `UNDEFINED_RISK_TEMPLATE`.
**Scenarios & Possibilities**
- Neutral + defined-risk + sell-vol → iron fly or iron condor; tiebreak by intent's width/credit appetite and by expiry-day mode (condor's OTM shorts safer on expiry day than fly's ATM shorts).
- Adjust intent on an existing position: template = the *delta* between current and target shape (e.g. "buy a put wing to cap downside"), which constrains 4.3.3 to specific legs.
- Intent under-specifies (just "neutral") → default to the more conservative condor over the fly, record the default.
**Functional Test Case(s)**
- Given intent {neutral, defined-risk, sell-vol, no template}, non-expiry-day, When selecting, Then a four-leg short-vol defined-risk template chosen (fly or condor per policy) and recorded.
- Given intent implying a naked short call, When selecting, Then refuse `UNDEFINED_RISK_TEMPLATE`.
**Clear Outcome** — A named template with a fixed leg skeleton (count, sides, rights) ready for strike assignment; never an undefined-risk skeleton.

#### 4.3.1.1 Adjust-intent template delta

**Responsibility** — For "adjust" intents, compute the minimal leg-set change vs current position.
**Behavior / Actions**
- Read current-position descriptor from the intent (Module 4 doesn't own the book; the intent must carry it). Diff target shape vs current → emit only the legs that change (add wing, roll short, close-and-reopen).
- Preserve defined-risk at all times: never emit an adjustment that transiently removes a protective wing before its replacement.
**Scenarios & Possibilities**
- Roll a short strike out: must keep risk defined across the roll → order legs so protection is never naked (couples to 4.5.1 multi-leg ordering).
- Adjustment that would *increase* max-loss beyond the intent's risk budget → refuse `ADJUST_BREACHES_RISK`.
- Current-position data missing/stale in intent → refuse `NO_POSITION_CONTEXT` rather than guess.
**Functional Test Case(s)**
- Given current = short strangle and target = iron condor (add wings), When computing delta, Then plan = buy two protective wings only (no touch to shorts), risk strictly reduced.
- Given adjust that widens max-loss past budget, When computing, Then refuse `ADJUST_BREACHES_RISK`.
**Clear Outcome** — A minimal, risk-non-increasing leg delta, or a clean refusal; existing legs untouched unless the delta requires it.

### 4.3.2 Anchor / reference selection

**Responsibility** — Establish the reference point strikes are measured from (ATM anchor).
**Behavior / Actions**
- Compute ATM: nearest valid strike to spot (or to forward/synthetic-future if the chain implies one via put-call parity). Decide spot vs forward anchor per policy; record which.
- Snap anchor to a *valid* listed strike using the bound strike step (4.1.3).
**Scenarios & Possibilities**
- Spot exactly between two strikes → deterministic round (e.g. round-half-to-even or toward-OTM per policy); never ambiguous.
- Forward differs materially from spot (cost-of-carry / dividend / expiry-day basis) → using spot mis-centers a fly; prefer synthetic forward when chain supports it.
- Missing ATM strike in chain (gap) → choose nearest available, flag `ANCHOR_GAP`.
**Functional Test Case(s)**
- Given spot 22,037 and 50-pt strikes, When anchoring (round-to-nearest), Then ATM = 22,050.
- Given spot exactly 22,025 (midpoint, 50-pt step), When anchoring, Then deterministic tie rule applied and recorded.
**Clear Outcome** — A single valid listed anchor strike with the anchor method recorded; ties resolved deterministically.

### 4.3.3 Strike selection — multiple philosophies

This is the richest decision; Module 4 must support several and apply the one the intent/policy names.

#### 4.3.3.1 Strike by delta / greek target

**Responsibility** — Pick short/long strikes whose option delta (or other greek) hits a target.
**Behavior / Actions**
- For each leg with a delta target (e.g. short ≈ 0.16Δ, wing ≈ 0.05Δ), scan the chain's greeks, find the strike whose delta is closest to target on the correct side.
- Validate the greek is present and sane (monotone in strike); if greeks missing, fall back (4.3.3.4) or refuse.
**Scenarios & Possibilities**
- Snapshot greeks missing/zero/NaN (common in thin chains) → cannot use delta method; fall back to distance or premium method, record `GREEK_FALLBACK`.
- Non-monotone/garbage deltas (bad IV surface) → reject the method for this leg; fall back.
- Two strikes equidistant in delta → tiebreak toward more-OTM (more conservative) deterministically.
- Expiry-day: deltas swing violently; small spot moves relocate the 0.16Δ strike → method is jittery; consider distance method on expiry day.
**Functional Test Case(s)**
- Given target short delta 0.16 and chain deltas {…0.20@X, 0.15@Y…}, When selecting, Then strike with delta closest to 0.16 chosen (Y if |0.15−0.16|<|0.20−0.16|).
- Given all chain deltas = 0/NaN, When selecting by delta, Then `GREEK_FALLBACK` and hand off to distance method.
**Clear Outcome** — Strikes whose greeks best match targets, on valid listed strikes, or a recorded fallback.

#### 4.3.3.2 Strike by absolute distance / offset

**Responsibility** — Pick strikes at a fixed distance (points or %) from the anchor.
**Behavior / Actions**
- short = anchor ± offset; wing = short ± width. Offset/width from intent (e.g. shorts 200 pts out, wings 100 pts beyond).
- Snap each to a valid listed strike (4.3.6); ensure ordering (long wing strictly beyond short on the protective side).
**Scenarios & Possibilities**
- Offset not a multiple of strike step → snap and record the realized (vs requested) distance.
- Distance pushes strike beyond the listed chain range (deep OTM not listed) → clamp to farthest listed strike, flag `STRIKE_RANGE_CLAMP`; this *narrows protection* — must re-validate defined risk.
- % offset on a fast-moving spot → compute against the recorded anchor, not a moving spot, for determinism.
**Functional Test Case(s)**
- Given anchor 22,050, 50-pt step, short offset 200, wing width 100, When selecting, Then shorts at 21,850 / 22,250 and wings at 21,750 / 22,350.
- Given wing offset beyond listed range, When selecting, Then clamp to farthest strike and flag `STRIKE_RANGE_CLAMP`.
**Clear Outcome** — Symmetric (or intentionally asymmetric) strikes on valid steps; realized distances recorded; clamps flagged and risk re-checked.

#### 4.3.3.3 Strike by premium / credit target

**Responsibility** — Pick strikes so the structure collects a target credit (or each short collects a target premium).
**Behavior / Actions**
- Walk strikes from ATM outward, accumulating credit until the net target is met (or each short's premium ≈ target).
- Use the *executable* price side (bid for shorts we sell, ask for longs we buy) not mid, so the target reflects fillable credit (couples to 4.4).
**Scenarios & Possibilities**
- Target credit unattainable even at ATM (low IV / cheap options) → cannot meet; either widen to closest feasible and flag `CREDIT_TARGET_MISS`, or refuse if intent marks credit hard.
- Using mid instead of bid overstates achievable credit → must use conservative side.
- Zero-bid wings far out → premium method on the *long* side is fine (we pay ~0) but defines almost no extra credit; ensure the short legs carry the credit.
**Functional Test Case(s)**
- Given target net credit 120 and accumulating from ATM, When the running credit first ≥120 at a strike pair, Then those shorts selected; wings added per width rule.
- Given max achievable credit 80 < target 120, When selecting, Then `CREDIT_TARGET_MISS` (or refuse if hard).
**Clear Outcome** — Strikes delivering ≥ target *executable* credit, or a recorded miss/refusal — never an inflated mid-based promise.

#### 4.3.3.4 Strike-method fallback chain

**Responsibility** — Define the deterministic order of fallback when the primary strike method is infeasible.
**Behavior / Actions**
- Primary (intent) → secondary → tertiary, e.g. delta → distance → premium, or as policy dictates. Each fallback records why the prior failed.
- If all methods fail (no usable chain data), refuse `NO_STRIKE_METHOD_FEASIBLE`.
**Scenarios & Possibilities**
- Greeks missing (delta fails) but prices fine → distance/premium succeed.
- Chain so thin that distance clamps and premium misses → cascade to refusal, not a garbage plan.
- Fallback must not silently change risk shape (e.g. premium method ending far closer to ATM than delta would) → re-validate defined risk after any fallback.
**Functional Test Case(s)**
- Given delta method fails (no greeks), distance method feasible, When cascading, Then distance result returned with `GREEK_FALLBACK` recorded.
- Given all three infeasible, When cascading, Then refuse `NO_STRIKE_METHOD_FEASIBLE`.
**Clear Outcome** — Either a strike set from the highest-priority feasible method (with fallback breadcrumbs) or a clean refusal.

### 4.3.4 Wing-width / protection sizing

**Responsibility** — Set the protective wing distance that defines max loss.
**Behavior / Actions**
- Wing width from intent (points, or as a function of credit, or to cap max-loss at a budget). Compute max-loss = (wing width × lot multiplier × lots) − net credit; ensure ≤ risk budget.
- If width is given but resulting max-loss > budget, *narrow* the wing (more protection) until within budget, or reduce lots (defer to 4.3.5), per policy.
**Scenarios & Possibilities**
- Wing strike not listed → snap; realized width differs → recompute max-loss on realized width.
- Very wide wing → larger max-loss but larger credit; very narrow wing → tiny credit barely covering costs. Edge: wing so narrow credit < transaction costs → flag `CREDIT_BELOW_COST`.
- Asymmetric wings (broken wing) intentionally → support; ensure each side independently defined-risk.
- Expiry-day mode → bias wider on the strikes but possibly *narrower* wings to cap gamma-blowout loss; carry the tension explicitly.
**Functional Test Case(s)**
- Given short 21,850 / 22,250, wing width 100, lot mult 50, 2 lots, net credit 120×50×2, When sizing, Then max-loss = (100×50×2) − credit, asserted ≤ budget.
- Given that max-loss > budget, When sizing, Then wing narrowed (or lots cut) until ≤ budget, with the action recorded.
**Clear Outcome** — Wings that make max-loss explicit and ≤ budget on *realized* (snapped) strikes; degenerate-credit cases flagged.

### 4.3.5 Lot sizing within budget / margin

**Responsibility** — Choose quantity (in lots) that fits capital/margin and risk budget.
**Behavior / Actions**
- Compute per-structure defined max-loss and (if available in snapshot) margin estimate. lots = floor(min(risk_budget / max_loss_per_lot, capital_or_margin_budget / margin_per_lot)).
- Enforce lots ≥ 1 minimum-viability; if floor = 0, the structure can't fit → refuse `BUDGET_TOO_SMALL` or signal upstream to shrink wings (per system memory: prefer shrinking lots/width to fit liquidity, don't pause the build).
- Respect any min-lot floor from intent (e.g. "2-lot floor" per a strategy) — if floor can't be afforded, refuse rather than under-size.
**Scenarios & Possibilities**
- max_loss_per_lot tiny (narrow wings) → risk budget allows many lots, but *liquidity* caps fillable size → cap lots by available OI/quote depth at the chosen strikes (couples to 4.3.7 liquidity).
- Budget allows 1.7 lots → floor to 1 (never fractional lots).
- Margin data absent from snapshot → size on defined max-loss only and flag `MARGIN_UNKNOWN` (conservative).
- Prefer MINI/MICRO contracts where the underlying offers them to fit small budgets (per system memory) — if a smaller-lot variant exists in the snapshot and budget is tight, route sizing there (couples to 4.1.2).
**Functional Test Case(s)**
- Given risk budget 200k, max-loss/lot 9k, margin/lot 60k, capital 200k, When sizing, Then lots = floor(min(22.2, 3.33)) = 3.
- Given budget affording 0 lots at a hard 2-lot floor, When sizing, Then refuse `BUDGET_TOO_SMALL`.
**Clear Outcome** — Integer lot count ≥ floor that satisfies risk *and* margin *and* liquidity caps, or a clean refusal; never fractional, never over-budget.

### 4.3.6 Strike rounding / valid-strike snapping

**Responsibility** — Ensure every chosen strike is an actually-listed, tradable strike.
**Behavior / Actions**
- Snap any computed strike to the nearest valid strike on the bound step; then verify it *exists in the snapshot chain* (listed ≠ merely on-grid). Adjust ordering invariants (long beyond short) after snapping.
- Record requested-vs-snapped for every leg.
**Scenarios & Possibilities**
- On-grid but not listed (gaps in far OTM) → step outward to nearest listed; if none, clamp + flag.
- Snapping two legs onto the *same* strike (e.g. short and wing collide after snap when width < step) → invalid structure; widen by one step or refuse `DEGENERATE_LEGS`.
- Step value wrong for the exchange (NIFTY vs SENSEX differ) → wrong snapping; relies on 4.1.3 correctness.
**Functional Test Case(s)**
- Given computed strike 22,233 and 50-pt listed grid, When snapping, Then 22,250 (nearest listed) and requested/snapped recorded.
- Given short and wing snap to identical strike, When validating, Then `DEGENERATE_LEGS` raised or wing pushed out one step per policy.
**Clear Outcome** — Every leg references a strike that exists in the chain and preserves structural ordering; collisions resolved or refused.

### 4.3.7 Liquidity / spread filtering of chosen legs

**Responsibility** — Confirm each chosen leg is liquid enough to fill at a sane price.
**Behavior / Actions**
- For each leg, check from snapshot: bid/ask both present, relative spread ≤ threshold, OI/volume ≥ floor, quote not stale. If a leg fails, attempt a nearby alternative strike (re-enter 4.3.3 with a nudge) before refusing.
- Aggregate: if the *net* structure's combined spread cost exceeds a fraction of expected credit, flag `SPREAD_EATS_EDGE`.
**Scenarios & Possibilities**
- Zero-bid deep wing → can't be sold (but wings are *bought*, so zero-bid is fine for a long wing; zero-*ask* would be the problem) — direction-aware checks matter.
- One leg illiquid while neighbors fine → shift that single strike, keeping structure shape, re-validate symmetry/risk.
- Whole region illiquid (far OTM on a quiet underlying) → refuse `ILLIQUID_STRUCTURE`.
- Stale quote (timestamp old) → treat as missing; couples to 4.6 snapshot freshness.
**Functional Test Case(s)**
- Given chosen short strike with bid/ask 100/103 (spread 3%) under a 5% threshold, OI above floor, When filtering, Then leg passes.
- Given chosen short with no bid, When filtering a *sell* leg, Then attempt adjacent strike; if none liquid, `ILLIQUID_STRUCTURE`.
**Clear Outcome** — Every emitted leg passes direction-aware liquidity/spread/freshness checks, possibly after a strike nudge; otherwise a coded refusal. Net spread-vs-edge flagged.

---

## 4.4 Price selection

Turn the chosen contracts into priced orders the execution layer can work, balancing fill probability vs slippage. Module 4 sets *targets*; the execution layer may re-peg — but Module 4's price must be sane and fillable.

### 4.4.1 Order-type policy (market vs limit)

**Responsibility** — Decide market vs limit (vs limit-with-protection) per leg / for the net order.
**Behavior / Actions**
- Default to *limit* for defined-risk premium structures (slippage on multi-leg market orders compounds). Use marketable-limit (cross by a tick or two) when fill-urgency is high. Avoid pure market on illiquid legs.
- Choose net-combo pricing if the venue supports multi-leg orders, else per-leg with an ordering plan (4.5.1).
**Scenarios & Possibilities**
- Wide spreads → market order = severe slippage; force limit.
- Urgent adjust (risk reduction) → marketable-limit or market acceptable to *guarantee* protection on first (couples to 4.3.1.1 / 4.5.1).
- Venue lacks combo orders → per-leg limits with a careful sequence and a re-peg budget.
**Functional Test Case(s)**
- Given a 4-leg condor on liquid strikes, When choosing order type, Then net limit at credit target (not market).
- Given an urgent protective-wing buy, When choosing, Then marketable-limit/market so protection fills first.
**Clear Outcome** — A justified order-type per leg/net; market reserved for urgent or deeply-liquid cases; documented.

### 4.4.2 Reference-price computation (mid / credit targeting)

**Responsibility** — Compute the target price from the chain (mid, weighted, or executable-side).
**Behavior / Actions**
- Per leg: derive mid = (bid+ask)/2; but for the *plan's* credit, value sells at bid-leaning and buys at ask-leaning to be conservative, or target mid with a defined cross. Net credit target = Σ(sell prices) − Σ(buy prices).
- Expose both an *aggressive* (mid) and *conservative* (executable-side) net price so execution has a band.
**Scenarios & Possibilities**
- Crossed/locked market (bid>ask from stale legs) → mid is nonsense; reject leg price, treat as stale (4.6).
- One leg's mid dominated by a wide spread → mid unreliable; widen the cross or down-weight.
- Targeting mid that's below realistic fill → execution never fills; the conservative band prevents an unfillable plan.
**Functional Test Case(s)**
- Given legs with bids/asks, When computing, Then net mid credit and net conservative credit both produced and conservative ≤ mid.
- Given a crossed quote on one leg, When computing, Then that leg flagged stale and price not trusted.
**Clear Outcome** — A net price *band* (aggressive→conservative) plus per-leg references; degenerate quotes excluded.

### 4.4.3 Slippage tolerance / limit offset

**Responsibility** — Set how far from reference the limit may sit (and the execution re-peg budget).
**Behavior / Actions**
- Define limit = reference ± tolerance, where tolerance scales with spread width and urgency (e.g. cross 25–50% of the spread). Provide a `slippage_budget` the execution layer may consume before giving up.
- Ensure worst-case fill (reference + full tolerance) still preserves the trade's edge (net credit still > costs, max-loss still ≤ budget).
**Scenarios & Possibilities**
- Tolerance too tight on a wide spread → no fill; too loose → edge eaten. Scale to spread.
- Worst-case slippage flips the structure to negative expected edge → cap tolerance or refuse `SLIPPAGE_KILLS_EDGE`.
- Expiry-day fast market → wider tolerance needed but higher adverse-selection risk; bound it.
**Functional Test Case(s)**
- Given net spread 4 and policy cross 50%, When setting, Then limit offset = 2 from mid and slippage_budget recorded.
- Given that worst-case fill makes net credit < costs, When checking, Then `SLIPPAGE_KILLS_EDGE` raised.
**Clear Outcome** — A limit price + slippage budget whose worst case still leaves the trade edge-positive; otherwise refusal.

### 4.4.4 Tick rounding / valid-price snapping

**Responsibility** — Round every limit price to a valid exchange tick.
**Behavior / Actions**
- Snap each limit and the net to the bound tick (4.1.3). Round *conservatively per side*: round sells down to the tick, buys up to the tick (so we don't post an impossible fraction and don't overstate credit).
- Re-verify post-rounding that net credit ≥ minimum and max-loss ≤ budget (rounding can shift both).
**Scenarios & Possibilities**
- Price below one tick (deep OTM wing worth ~0.05) → ensure ≥ min tick; a wing priced at 0 is unsellable but it's *bought*, so 0.05 ask is the constraint.
- Cumulative rounding across 4 legs erodes credit below target → re-check and, if breached, re-peg or flag `ROUNDING_BREACH`.
**Functional Test Case(s)**
- Given tick 0.05 and computed sell price 100.13, When snapping a sell, Then 100.10 (rounded down).
- Given post-rounding net credit < target, When re-checking, Then `ROUNDING_BREACH` and re-peg/flag.
**Clear Outcome** — All prices on valid ticks, side-correct rounding, net invariants re-verified after rounding.

---

## 4.5 Plan assembly, validity checks & emission

Compose the legs into one coherent plan and gate it before handoff. This is the last line of defense against emitting a broken plan.

### 4.5.1 Multi-leg ordering / sequencing

**Responsibility** — Decide the order legs are sent (and grouping) to keep risk defined throughout.
**Behavior / Actions**
- If venue supports atomic combo orders, emit as one net order (preferred — no legging risk). If per-leg, sequence so *protective longs fill before/with shorts* (buy wings first), and on *adjust*, never strip protection before its replacement is on.
- Tag legs with a sequence index and any "must-fill-before" dependency for the execution layer.
**Scenarios & Possibilities**
- Per-leg legging on a fast market → shorts fill, wings don't → momentarily naked/undefined risk. Sequencing buys protection first to prevent this.
- Combo order rejected by venue → fall back to safe per-leg sequence, not a free-for-all.
- Partial fills mid-sequence → Module 4 can't manage fills (that's execution), but it *declares* the safe order and dependencies.
**Functional Test Case(s)**
- Given a 4-leg iron condor, per-leg venue, When sequencing, Then both long wings indexed before both shorts with must-fill-before dependencies.
- Given atomic combo supported, When sequencing, Then single net order emitted, no per-leg sequence needed.
**Clear Outcome** — A leg order (or atomic combo) that never leaves risk undefined if executed in sequence; dependencies explicit.

### 4.5.2 Defined-risk / net-greek validation

**Responsibility** — Prove the assembled plan is genuinely defined-risk and matches intent shape.
**Behavior / Actions**
- Recompute from the *final* legs: max-loss, max-profit, breakevens, net delta (≈ neutral for neutral intent within tolerance). Assert max-loss is finite and ≤ budget; assert every short has a corresponding long protection on its side.
- Reject if any side is unprotected (would be undefined risk) or net shape contradicts intent.
**Scenarios & Possibilities**
- A fallback/clamp earlier left one side's wing missing or inverted → caught here, `UNDEFINED_RISK_FINAL`.
- Net delta far from neutral on a "neutral" intent (asymmetric snapping) → flag `SHAPE_DEVIATION`; refuse if beyond tolerance.
- Max-profit < transaction costs → `NO_EDGE_AFTER_COSTS`.
**Functional Test Case(s)**
- Given final legs forming a balanced condor, When validating, Then max-loss finite ≤ budget, both sides protected, net delta within tolerance → pass.
- Given a missing call-side wing after a clamp, When validating, Then `UNDEFINED_RISK_FINAL`, plan refused.
**Clear Outcome** — A plan certified defined-risk, budget-compliant, intent-consistent — or refused with the exact failed invariant.

### 4.5.3 Budget / margin / exposure final check

**Responsibility** — Final affordability and exposure gate against the snapshot/intent budget.
**Behavior / Actions**
- Recompute total margin/capital required for the final lots×legs; assert ≤ available. Assert no aggregate exposure rule in the intent is breached (Module 4 only knows what the intent carries; it doesn't see the wider book).
- If margin data is uncertain, keep the conservative `MARGIN_UNKNOWN` flag visible on the plan.
**Scenarios & Possibilities**
- Earlier sizing used per-lot margin; final net combo margin (spread margin benefit) differs → recompute on the assembled structure, may *free up* room or, rarely, require more.
- Budget breached only after price/rounding shifts → cut a lot and re-validate, or refuse `BUDGET_BREACH_FINAL`.
**Functional Test Case(s)**
- Given final structure margin 58k ≤ available 200k, When checking, Then pass.
- Given final margin 210k > available 200k, When checking, Then reduce lots and re-run 4.5.2/4.5.3, else `BUDGET_BREACH_FINAL`.
**Clear Outcome** — Final plan provably affordable on the data Module 4 has, or a coded refusal; uncertainty flagged not hidden.

### 4.5.4 Symbol / contract resolution

**Responsibility** — Resolve each leg to the exact, exchange-correct tradable contract symbol.
**Behavior / Actions**
- Build each symbol from the bound grammar (4.1.3): underlying + expiry + strike + right, using the exchange-specific layout (NIFTY vs SENSEX differ). Verify the constructed symbol exists in the snapshot chain (round-trip check), not just that it's well-formed.
- Attach lot multiplier and convert qty_lots → qty_units for the execution layer.
**Scenarios & Possibilities**
- Wrong grammar (SENSEX built with NIFTY layout) → symbol not found in chain → `SYMBOL_UNRESOLVED`, refuse (don't ship an unresolvable order).
- Expiry-date formatting (weekly vs monthly tag) edge cases → resolve from the chain's own expiry tokens, not a constructed guess.
- Strike formatting (no decimals / padded) mismatch → round-trip check catches it.
**Functional Test Case(s)**
- Given SENSEX leg expiry/strike/right, When resolving, Then symbol built in SENSEX grammar and found in chain; qty_units = lots × lot size.
- Given a leg whose constructed symbol is absent from the chain, When resolving, Then `SYMBOL_UNRESOLVED`, plan refused.
**Clear Outcome** — Every leg carries a verified, in-chain, exchange-correct symbol and unit quantity, or the plan is refused.

### 4.5.5 Final emission / refusal gate

**Responsibility** — Emit the validated plan, or a structured refusal — never a partial/ambiguous plan.
**Behavior / Actions**
- Only if *all* prior gates pass: assemble the full output contract (4.0) and emit `validity.ok = true` with legs. Otherwise emit `validity.ok = false`, zero executable legs, and the ordered list of reasons/flags collected throughout.
- Attach the full rationale (methods chosen, fallbacks, clamps, deviations) for audit.
**Scenarios & Possibilities**
- Mixed state (some legs valid, one unresolved) → *all-or-nothing*: refuse the whole plan; never emit a half-structure that would be undefined-risk.
- Soft flags present but no hard failure → emit `ok=true` but surface the warnings (e.g. `SPREAD_EATS_EDGE`, `DTE_DEVIATION`) for upstream to weigh.
- Determinism: same inputs ⇒ identical plan + identical reason list.
**Functional Test Case(s)**
- Given all gates pass, When emitting, Then `ok=true`, complete legs, defined_risk block, rationale present.
- Given any hard gate failed, When emitting, Then `ok=false`, no executable legs, reasons listed; partial structures never shipped.
**Clear Outcome** — A binary, auditable result: a complete executable plan or a complete refusal with reasons. No middle ground.

---

## 4.6 Cross-cutting concerns

### 4.6.1 Snapshot freshness & quality gate

**Responsibility** — Validate the input snapshot before any construction.
**Behavior / Actions**
- Check snapshot timestamp recency, presence of spot, chain completeness around the strikes of interest, and per-quote staleness. Mark stale/missing quotes so downstream treats them as absent (not zero).
- If the snapshot is too stale/sparse to construct safely, refuse `STALE_SNAPSHOT` / `SPARSE_CHAIN` up front.
**Scenarios & Possibilities**
- Whole chain stale (feed gap) → refuse early, don't waste downstream effort.
- Spot present but greeks/IV absent → allow distance/premium methods, block delta method.
- Per-system-memory ltp=0 / lp-less tick artifacts → must be filtered here so a 0 price never masquerades as a real quote.
**Functional Test Case(s)**
- Given snapshot age beyond threshold, When gating, Then `STALE_SNAPSHOT`, no construction.
- Given a leg quote with ltp 0 and no bid/ask, When gating, Then that quote marked missing, not used as a price.
**Clear Outcome** — Construction proceeds only on data good enough to trust; bad data is flagged/absent, never silently used as real.

### 4.6.2 Units, conventions & determinism

**Responsibility** — Keep premium-vs-rupee, per-unit-vs-per-lot, and points-vs-price conventions consistent and reproducible.
**Behavior / Actions**
- Fix conventions once: prices per unit, P&L = price × lot_size × lots, strikes in index points. Apply the bound exchange constants everywhere. Forbid hidden randomness; any tiebreak is deterministic.
**Scenarios & Possibilities**
- Mixing per-lot and per-unit (a classic bug) → max-loss off by lot_size → caught by unit-consistency assertions.
- Two valid choices tie (strikes, prices, underlyings) → stable deterministic rule, reproducible across runs.
**Functional Test Case(s)**
- Given identical intent + snapshot run twice, When constructing, Then byte-identical plans.
- Given a max-loss computation, When asserting units, Then it equals width × lot_size × lots − credit×lot_size×lots (consistent multipliers).
**Clear Outcome** — One consistent unit system, fully deterministic output, no per-lot/per-unit drift.

### 4.6.3 Observability / rationale trace

**Responsibility** — Record why every choice was made for later grilling.
**Behavior / Actions**
- Emit a structured rationale: chosen underlying + runners-up, expiry + DTE reason, template, strike method + fallbacks, sizing math, price band, all flags/clamps/deviations.
**Scenarios & Possibilities**
- Refusal with no reason → un-debuggable; every refusal must carry codes.
- Silent fallback → erodes trust; every deviation breadcrumbed (echoes the system's anti-hallucination / audit-trail priorities).
**Functional Test Case(s)**
- Given any emitted plan or refusal, When inspecting, Then a complete rationale/flag list is present.
**Clear Outcome** — Every plan and every refusal is fully explainable from its own trace.

---

## Suggestions (for bubble-up)

Market-condition scenarios that exceed a single module's remit and likely deserve *system-wide* policy. Flagged here for later review; Module 4 surfaces signals/flags for them but should not unilaterally own the policy.

1. **Expiry-day gamma / pin risk.** On DTE-0, ATM short structures carry violent gamma and pin risk; delta-based strike selection becomes jittery. System-wide policy needed: prefer condor over fly, push strikes out, cap size, or skip entry late in the day. Module 4 raises `expiry_day_mode`, `LATE_EXPIRY_DAY`.
2. **Illiquid / wide-spread strikes.** When spreads eat the edge, no clever construction helps. Needs a system-level liquidity floor and a "don't trade this underlying/region today" rule. Module 4 raises `SPREAD_EATS_EDGE`, `ILLIQUID_STRUCTURE`.
3. **Gap / fast-market open.** Stale or rapidly-moving quotes make snapshot-based construction unsafe; anchor and deltas move under our feet. Needs a system-level "wait for stability" gate. Module 4 raises `STALE_SNAPSHOT`.
4. **Low-IV regime.** Credit targets unattainable; theta-harvest edge thin or negative after costs. System should decide whether to stand down. Module 4 raises `CREDIT_TARGET_MISS`, `NO_EDGE_AFTER_COSTS`.
5. **High-IV / event regime.** Rich premium but fat tails; defined-risk wings may be too cheap relative to realized risk. System should size down / widen. Module 4 raises elevated max-loss vs budget tensions.
6. **Circuit limits / non-tradable bands.** Strikes at exchange price bands or frozen contracts can't fill; needs a system feed of circuit/halts. Module 4 can only see absence in the snapshot (`STRIKE_RANGE_CLAMP`, `SYMBOL_UNRESOLVED`).
7. **Cross-exchange holiday / hours divergence.** NSE vs BSE differ on rare days; agnostic-intent routing must respect a system calendar. Module 4 raises `NO_CHAIN_FOR_UNDERLYING`.
8. **Budget vs minimum-viable-size conflict.** When even one lot (or a strategy's lot-floor) can't fit, the system must decide shrink-wings vs shrink-lots vs skip (system memory favors shrinking to fit, not pausing). Module 4 raises `BUDGET_TOO_SMALL`.
9. **Margin model uncertainty.** Without a reliable margin estimate in the snapshot, sizing is conservative-only; a system-level margin oracle would tighten this. Module 4 raises `MARGIN_UNKNOWN`.
10. **Legging risk on non-combo venues.** If atomic combo orders aren't available, momentary undefined risk during sequencing is a system-level execution concern Module 4 can only mitigate by ordering, not eliminate.
