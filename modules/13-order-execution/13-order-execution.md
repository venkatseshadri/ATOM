# Module 13 — Order/Execution

## 13.0 Module Overview

**Role.** Module 13 is the *mechanics of execution*. It receives an already-approved
order plan — a set of option legs, each with contract, side (buy/sell), quantity, and
price/order-type — plus an authenticated broker session. Its job is to make the broker's
book reflect that plan as faithfully as possible, then report back exactly what happened.

It does **not** decide *what* to trade, *whether* to trade, position sizing, risk limits,
or strategy. Those are upstream. Module 13's universe is: place, modify, cancel, track,
reconcile, and report. Garbage-in / garbage-out is acceptable at the decision layer; this
module's contract is **faithful, observable, idempotent execution of a given plan**.

**Inputs.**
- An approved order plan: ordered list of legs `{contract, side, qty, order_type, limit_price?, meta}` plus a plan-level id and intent (entry vs exit/square-off).
- An authenticated session/handle to the broker API.

**Outputs.**
- Per-leg fill records: `{leg_id, broker_order_id, client_order_id, status ∈ {filled, partial, rejected, cancelled, working}, filled_qty, avg_fill_price, expected_price, slippage, timestamps{submitted, acked, first_fill, terminal}}`.
- A plan-level execution summary: aggregate status, basket slippage, and a flag for *structural integrity* (did all legs complete, or is the position lopsided/legged-in).

**Core invariants this module must protect.**
1. **No silent loss.** Every leg submitted is tracked to a terminal state or explicitly flagged unknown — never dropped.
2. **Idempotency.** A retry of "the same plan" must not double-place. Network ambiguity (we don't know if the broker got it) must resolve to *at most one* live order per leg.
3. **Structural awareness.** This is a defined-risk multi-leg strategy; a half-executed basket (one leg live, its hedge not) is a *naked* risk and must be surfaced loudly, even if 13 cannot itself decide the fix.
4. **Truth from the broker.** Local optimism is never authoritative; the broker's order book is the source of truth, reconciled continuously.
5. **Time-boundedness.** Intraday-only: orders have TTLs, and there is a hard end-of-day square-off obligation that overrides normal pacing.

**Design tensions surfaced (explored, not resolved here).**
- *Atomic basket vs sequential leg-in* — broker basket/GTT-OCO support vs control/observability.
- *Limit (price-protective, fill-risk) vs market (fill-certain, slippage-risk)* — and when to convert one to the other.
- *Aggressive completion (chase) vs passive (wait)* on residual/partial.
- *Speed of retry vs duplicate risk* under ambiguous acks.

---

## 13.1 Order Placement

The atom of the module: getting one well-formed leg onto the broker's book and capturing
its acknowledgment.

### 13.1.1 Order-type selection (market vs limit vs limit-with-protection)
- **Responsibility.** Given a leg's requested order_type and current context, emit the concrete order-type the broker call will use.
- **Behavior / Actions.** Honor the plan's explicit order_type. Where the plan says "marketable", choose between pure MARKET, LIMIT-at-touch, or LIMIT-with-protection (IOC marketable limit capped N ticks through the book) per a configured policy. Reject/flag order types the broker does not support for that instrument (e.g., market orders disallowed on illiquid weekly far strikes, or market orders banned by exchange for options — many Indian brokers convert option MARKET to a protected limit internally).
- **Scenarios & Possibilities.**
  - Broker silently converts MARKET→protected-LIMIT; module must anticipate and not treat the converted order as anomalous.
  - Exchange in pre-open / no market depth → MARKET would fill at absurd price; should refuse or cap.
  - Plan requests LIMIT but provides no price → error, do not default to MARKET silently.
  - Very wide bid/ask (illiquid strike) → market order = severe slippage; policy should prefer protected limit.
- **Functional Test Case(s).**
  - *Given* a leg with order_type=MARKET on an instrument where the broker bans market orders, *When* placement runs, *Then* it is converted to a protected marketable limit (or rejected with a clear reason), never sent as raw MARKET.
  - *Given* order_type=LIMIT with limit_price=null, *When* placement runs, *Then* the leg is rejected pre-submission with `INVALID_PLAN`.
- **Clear Outcome.** A concrete, broker-acceptable order spec; unsupported/ambiguous specs fail fast pre-submission with a typed reason, never guessed.

### 13.1.2 Limit-price computation & order-parameter assembly
- **Responsibility.** Build the exact broker payload (price, qty, product=intraday/MIS, validity, disclosed qty, exchange/segment, tradingsymbol) for one leg.
- **Behavior / Actions.** Round price to instrument tick size (mandatory — off-tick = reject). Set product type to intraday (so the position is margin-correct and not delivery). Set validity (DAY / IOC) per policy. Map the abstract contract to the broker's exact symbol convention (note: SENSEX weekly options use a distinct symbol format vs NIFTY — wrong format = reject). Attach the client_order_id/tag (see 13.3.1).
- **Scenarios & Possibilities.**
  - Off-tick price (e.g., 12.07 where tick=0.05) → broker reject; must round.
  - Quantity not a multiple of lot size → reject; must validate against lot size.
  - Qty exceeds exchange per-order freeze limit → must split into child orders (freeze-quantity slicing) or it rejects.
  - Symbol format drift (expiry-day code, strike padding) → reject `INVALID_SYMBOL`.
  - Price = 0 or negative → reject.
- **Functional Test Case(s).**
  - *Given* limit_price 12.07 and tick 0.05, *When* assembled, *Then* price is snapped (to 12.05 or 12.10 per rounding policy) before send.
  - *Given* qty 1800 and exchange freeze qty 900, *When* assembled, *Then* two child orders of ≤900 are produced under one logical leg.
- **Clear Outcome.** A tick-valid, lot-valid, freeze-compliant, correctly-symboled payload, or a pre-submission validation error.

### 13.1.3 Single-order submission
- **Responsibility.** Issue the place-order call and obtain a broker order id.
- **Behavior / Actions.** Call broker place-order with the assembled payload and the authenticated session. Record submit timestamp before the call. Capture returned broker_order_id and map it to leg_id + client_order_id. Treat the call as *possibly-ambiguous* (see timeouts 13.10.1) — a thrown exception does NOT mean "not placed".
- **Scenarios & Possibilities.**
  - Success with order id → normal.
  - HTTP 200 but body indicates reject → not an exception; must parse body.
  - Network timeout / 5xx → unknown state; must NOT blindly resubmit (idempotency 13.3).
  - Session expired mid-flight → auth error; surface for re-auth, do not retry blindly.
  - Rate-limit (429) → backoff.
- **Functional Test Case(s).**
  - *Given* the broker returns an order id, *When* submit completes, *Then* a working order record exists keyed by both client_order_id and broker_order_id.
  - *Given* the call times out, *When* submit returns, *Then* state is `UNKNOWN_SUBMIT` (not `placed`, not `failed`) and reconciliation is scheduled.
- **Clear Outcome.** Either a tracked working order with a broker id, or an explicit UNKNOWN state that triggers reconciliation — never an untracked send.

### 13.1.4 Acknowledgment capture & order-record creation
- **Responsibility.** Turn the broker ack into a durable local order record (the unit of truth for everything downstream).
- **Behavior / Actions.** Persist `{leg_id, client_order_id, broker_order_id, requested{side,qty,price,type}, status=working, submitted_ts, acked_ts}`. Persistence must survive a process crash (so a restart can reconcile, not re-place). Emit an event for monitoring.
- **Scenarios & Possibilities.**
  - Crash between submit and persist → on restart, an order exists at broker with no local record → orphan (13.9.3) must find it via client_order_id tag.
  - Broker ack lacks order id but indicates accepted → record with null broker id, resolve via reconciliation.
  - Duplicate ack (broker re-sends) → dedupe on broker_order_id.
- **Functional Test Case(s).**
  - *Given* a successful submit followed by a process kill before persistence, *When* the module restarts, *Then* reconciliation locates the broker order by client_order_id tag and rebuilds the record (no re-placement).
  - *Given* two acks with the same broker_order_id, *When* captured, *Then* one record exists.
- **Clear Outcome.** A crash-durable, dedup'd order record exists for every accepted order; tag-based recovery prevents orphans.

---

## 13.2 Multi-Leg Sequencing & Atomicity (Leg-In Risk)

The defining hazard of a defined-risk spread: between placing leg A and leg B, the market
moves, B becomes unfillable or expensive, and the position is momentarily *naked*. This
sub-tree explores the spectrum from "broker-atomic basket" to "carefully sequenced manual
legging" plus the unwind path when atomicity fails.

### 13.2.1 Leg-ordering / sequencing strategy
- **Responsibility.** Decide the order in which legs are submitted when not submitting atomically.
- **Behavior / Actions.** Apply a sequencing policy. Two opposing philosophies to weigh: (a) **protective-leg-first** — place the long/hedge legs (the ones that cap risk and free margin) before the short legs, so the account is never short without its hedge; vs (b) **hard-to-fill-first** — place the least-liquid leg first so you're not left chasing it after committing the liquid leg. For a short-premium defined-risk structure (e.g., iron fly/condor), protective-first is the risk-conservative default: buy wings before selling the body.
- **Scenarios & Possibilities.**
  - Buy-wings-first: wings fill, body sell rejects → position is long premium (defined, paid debit) — benign-ish, just unwanted; unwind cheaply.
  - Sell-body-first: body fills, wing buy rejects → **naked short** — the catastrophic case this ordering avoids.
  - Both legs liquid → ordering matters less; latency dominates.
- **Functional Test Case(s).**
  - *Given* a 4-leg iron fly, *When* sequenced under protective-first policy, *Then* both long wings are submitted (and confirmed working/filled per gating policy) before either short leg is submitted.
- **Clear Outcome.** Sequencing provably never leaves the account net-short its hedge as an intended ordering; any naked exposure is an exception, not the design.

### 13.2.2 Atomic basket / simultaneous submission
- **Responsibility.** Where the broker supports it, submit all legs as one atomic basket / multi-leg order so the broker accepts all-or-none.
- **Behavior / Actions.** Detect broker capability for basket/multi-leg/strategy orders. If supported, build the basket payload and submit once; consume a single basket-level ack and per-leg child statuses. Fall back to sequential (13.2.3) if unsupported or if the basket is rejected as a unit.
- **Scenarios & Possibilities.**
  - True all-or-none basket → cleanest; eliminates leg-in risk at submission.
  - "Basket" that is really just convenience batching (legs still independent at exchange) → NOT atomic; must not be trusted as all-or-none.
  - Basket partially accepted (some legs rejected) → must detect and trigger unwind (13.2.4) — the "basket" gave false safety.
  - Basket margin computed as spread (lower) vs legs computed independently (higher, may reject) → margin behavior differs from sequential.
- **Functional Test Case(s).**
  - *Given* a broker that exposes only convenience-batch (non-atomic) baskets, *When* a basket is submitted and one leg rejects, *Then* the module treats it as a legged-in partial and invokes unwind, NOT as a clean failure.
  - *Given* a truly atomic basket rejected as a unit, *When* submitted, *Then* zero orders are live and the plan fails cleanly.
- **Clear Outcome.** Atomic baskets are used when genuinely all-or-none; pseudo-baskets are never trusted as atomic and always reconciled leg-by-leg.

### 13.2.3 Sequential submission with inter-leg gating
- **Responsibility.** Submit legs one/group at a time, gating each step on the prior step's outcome.
- **Behavior / Actions.** Define the gate: does leg N+1 wait for leg N to be *acked/working*, or fully *filled*? Filled-gating is safest against leg-in but slowest (and a passive limit may never fill, stalling the basket). Acked-gating is faster but the prior leg could still reject/not-fill. Policy likely: gate short legs on confirmed-working hedges, but use marketable/IOC on the legs that must complete to keep the structure valid.
- **Scenarios & Possibilities.**
  - Gate-on-fill with passive limit hedge that never fills → basket stalls; need a timeout that either chases the hedge or aborts before placing shorts.
  - Gate-on-ack but prior leg later rejects → shorts already in flight → naked window; need rapid detection + unwind.
  - Slow market data between legs → stale prices on later legs.
- **Functional Test Case(s).**
  - *Given* gate-on-fill and a hedge limit that does not fill within its TTL, *When* the TTL expires, *Then* the module either escalates the hedge to marketable or aborts the basket *before* any short leg is placed.
- **Clear Outcome.** No short leg is committed unless its protective precondition holds; stalls resolve deterministically (chase or abort), never hang.

### 13.2.4 Leg-in failure detection & basket unwind/rollback
- **Responsibility.** When a basket ends up partially executed, return the account to a safe (flat or fully-hedged) structural state.
- **Behavior / Actions.** Detect structural incompleteness (filled legs ≠ planned legs after sequencing/TTL). Choose remedy per policy: (a) **complete** — aggressively finish the missing legs (convert to marketable) to realize the intended structure; or (b) **unwind** — close the already-filled legs to return flat. Prefer *complete* if the missing leg is the cheap protective wing (restores defined risk); prefer *unwind* if the missing leg is the premium-collecting short and completing it would be chasing into a moved market. Emit a loud STRUCTURAL_INCOMPLETE event regardless (bubble-up).
- **Scenarios & Possibilities.**
  - Naked short remains (worst): immediate priority — buy the protective wing at market, even at slippage, to cap risk before anything else.
  - Unwind order itself rejects/can't fill (the same illiquidity that caused the leg-in) → escalate, alert human; this is a genuine trapped-risk state.
  - Double-action race: completion and unwind both triggered → must be mutually exclusive / state-locked.
  - Market closing during unwind → square-off priority overrides.
- **Functional Test Case(s).**
  - *Given* a short leg filled and its protective wing rejected, *When* leg-in is detected, *Then* a protective buy is dispatched first (risk-capping) before any further action, and STRUCTURAL_INCOMPLETE is emitted.
  - *Given* a filled long wing and an unfilled short body past TTL, *When* unwind policy = revert, *Then* the long wing is closed and the plan ends flat with a recorded reason.
- **Clear Outcome.** The account never *remains* in an unintended naked/lopsided state silently; it is either completed to the intended structure or unwound flat, with the residual-risk case escalated.

---

## 13.3 Idempotency & Dedupe

Under network ambiguity the single most dangerous failure is double-placement. This
sub-tree makes "place this leg" safely repeatable.

### 13.3.1 Client-order-id / tag generation
- **Responsibility.** Stamp every order with a deterministic, plan-derived unique id before submission.
- **Behavior / Actions.** Generate a client_order_id (or broker "tag"/"user_id" field) that is a pure function of (plan_id, leg_id, attempt-epoch) such that *the same logical leg* maps to a stable id, while a *deliberate* re-place (new attempt) gets a new id. Pass it on every place/modify so reconciliation and orphan-recovery can match broker orders back to legs without relying on the broker id.
- **Scenarios & Possibilities.**
  - Broker tag field length/charset limits → id must fit; truncation must not collide.
  - Broker does not echo the tag in order-book queries → fallback dedupe key needed (symbol+side+qty+price+time-window heuristic) — weaker, flag it.
  - Two distinct plans with overlapping symbols → ids must not collide.
- **Functional Test Case(s).**
  - *Given* the same leg submitted twice with no intervening "new attempt" decision, *When* ids are generated, *Then* both carry the identical client_order_id.
  - *Given* a broker that echoes tags, *When* the order book is queried, *Then* every order is attributable to a leg via its tag.
- **Clear Outcome.** Every order is traceable to its leg independent of the broker id; same-leg retries share an id, intended re-places do not.

### 13.3.2 Duplicate-submission detection on retry
- **Responsibility.** Before (re)submitting a leg, ensure no live/working order for that same logical leg already exists.
- **Behavior / Actions.** On any retry of a leg whose prior submit was ambiguous (UNKNOWN_SUBMIT), first query the broker order book filtered by client_order_id; if a matching order exists in any non-terminal or filled state, adopt it instead of placing anew. Only place if confirmed absent. This is the core anti-double-place guard.
- **Scenarios & Possibilities.**
  - Original did go through; retry without check → duplicate position (double the intended size — a real-money loss/over-exposure).
  - Order book query itself is delayed/eventually-consistent → a just-placed order may not appear yet → conservative wait/re-query before deciding absent.
  - Original was rejected; retry is legitimate → must place.
  - Tag not echoed by broker → fall back to fuzzy match within a tight time/price/qty window; if uncertain, prefer NOT placing and escalate (over-exposure worse than a missed entry).
- **Functional Test Case(s).**
  - *Given* a leg in UNKNOWN_SUBMIT whose original order is actually live, *When* retry runs, *Then* the existing order is adopted and NO second order is placed.
  - *Given* a leg whose original was rejected, *When* retry runs and the order book shows nothing live, *Then* exactly one new order is placed.
  - *Given* eventual-consistency lag where the order book shows nothing yet but the order exists, *When* retry's pre-check runs, *Then* a bounded re-query/wait occurs before concluding "absent".
- **Clear Outcome.** At most one live order per logical leg, always; ambiguity resolves toward *not* duplicating, with escalation when undecidable.

---

## 13.4 Modify Flow

### 13.4.1 Price/qty modification submission
- **Responsibility.** Amend a working order's price or quantity in place.
- **Behavior / Actions.** Issue broker modify-order with broker_order_id and new params (tick/lot validated as in 13.1.2). Update the local record's requested fields and a modify timestamp; keep the original expected_price for slippage-vs-original (13.8). Handle the case where the order filled between the read and the modify (modify on a complete order → reject, treat as already-done).
- **Scenarios & Possibilities.**
  - Modify rejected because order already filled/cancelled → benign; refresh state, no error.
  - Reducing qty below already-filled qty → invalid; broker rejects.
  - Modify changes price into a self-cross or off-tick → validate first.
  - Broker assigns a new order id on modify (some do) → must re-key the record.
- **Functional Test Case(s).**
  - *Given* a working limit order, *When* its price is modified to a tick-valid value, *Then* the broker reflects the new price and the local record updates while preserving original expected_price.
  - *Given* an order that filled a millisecond before modify, *When* modify is sent, *Then* the reject is interpreted as `ALREADY_TERMINAL`, not a failure.
- **Clear Outcome.** Working orders are amended correctly; modify on terminal orders is handled idempotently; id re-keying (if any) is captured.

### 13.4.2 Modify race / price-chase logic
- **Responsibility.** Drive an unfilled working order toward fill by stepping its price (the "chase").
- **Behavior / Actions.** On a fill-timeout (13.10.2), step the limit price toward/through the market by a bounded number of ticks, up to a max chase distance / max attempts, then escalate to marketable or abort. Each step is a modify (or cancel-replace). Guard against runaway chasing past a configured worst-acceptable price.
- **Scenarios & Possibilities.**
  - Market runs away faster than chase steps → never fills; hits max distance → escalate decision.
  - Modify race: a fill lands during a chase step → must not over-fill or place a phantom replacement (esp. with cancel-replace, where cancel succeeds but the order already partially filled).
  - Chase that crosses the protective price bound → must stop, not blindly fill at a loss.
  - Rapid modifies → broker rate-limit / order-modification-count limits.
- **Functional Test Case(s).**
  - *Given* an unfilled limit and a chase policy of 3 steps × 2 ticks then marketable, *When* it remains unfilled across the steps, *Then* it ends as a marketable order or an explicit abort, never chasing past the max-price bound.
  - *Given* a partial fill arriving mid-chase using cancel-replace, *When* the replace is built, *Then* it is sized to the *remaining* qty only (no over-fill).
- **Clear Outcome.** Chase converges to fill, marketable escalation, or bounded abort — never unbounded price drift, never over-fill.

---

## 13.5 Cancel Flow

### 13.5.1 Single-order cancel
- **Responsibility.** Cancel one working order.
- **Behavior / Actions.** Issue broker cancel by order id; confirm terminal status via reconciliation, not just the cancel ack. Account for qty already filled at cancel time (cancel only stops the *unfilled* remainder).
- **Scenarios & Possibilities.**
  - Cancel ack received but order actually filled the remainder first (race) → final state is filled, not cancelled; must reconcile, not assume.
  - Cancel of an already-terminal order → benign no-op.
  - Cancel rejected (order in a non-cancellable transient state) → retry after brief wait.
- **Functional Test Case(s).**
  - *Given* a working order with 0 filled, *When* cancelled and confirmed, *Then* status=cancelled, filled_qty=0.
  - *Given* a cancel that races a fill, *When* reconciled, *Then* the recorded terminal status matches the broker (filled/partial), not an optimistic "cancelled".
- **Clear Outcome.** Cancel results are taken from reconciled broker truth; partial-fill-then-cancel is recorded accurately.

### 13.5.2 Cancel-all / bulk cancel
- **Responsibility.** Cancel every working order for the plan (or all module-owned orders) — used on abort, error, or pre-square-off.
- **Behavior / Actions.** Enumerate this module's working orders (by tag/plan) and cancel each; prefer a broker bulk-cancel if available, else iterate. Must only cancel *its own* orders (tag-scoped), never another module's/manual orders. Confirm each reaches terminal via reconciliation; report any that resist cancellation.
- **Scenarios & Possibilities.**
  - Partial success: some cancel, some fill during the sweep → mixed terminal states; report all.
  - Bulk-cancel cancels orders outside this plan's scope → unacceptable; must be tag-scoped.
  - New fills arriving during the sweep change the position → downstream/exit must re-read.
  - Cancel-all during exchange freeze window → some cancels rejected; retry/escalate.
- **Functional Test Case(s).**
  - *Given* this plan has 3 working orders and another actor has 1, *When* cancel-all runs, *Then* exactly the 3 plan-scoped orders are cancelled and the foreign order is untouched.
  - *Given* one order fills during the sweep, *When* cancel-all completes, *Then* its true filled status is reported, not "cancelled".
- **Clear Outcome.** All and only the module's working orders are driven terminal; scope is never violated; mixed outcomes are reported faithfully.

---

## 13.6 Partial-Fill Handling & Completion

### 13.6.1 Partial-fill tracking & remaining-qty accounting
- **Responsibility.** Maintain accurate filled vs remaining quantity for each order/leg.
- **Behavior / Actions.** Consume fill events (stream or poll); accumulate filled_qty and recompute avg_fill_price as a qty-weighted average across partial fills; derive remaining = requested − filled. Treat fills as possibly out-of-order/duplicated (idempotent on fill/exec id).
- **Scenarios & Possibilities.**
  - Multiple partials at different prices → avg price must be correctly weighted, not last-price.
  - Duplicate fill event (stream replay) → must not double-count; key on exchange exec id.
  - Filled_qty exceeds requested (broker bug / double order) → anomaly alarm, never silently accept.
  - Out-of-order fill events → state must be monotonic in filled_qty.
- **Functional Test Case(s).**
  - *Given* partials of 50@12.0 and 25@12.4 on a 100-qty order, *When* accounted, *Then* filled=75, avg=12.133…, remaining=25.
  - *Given* a duplicated fill event, *When* processed, *Then* filled_qty is unchanged (idempotent on exec id).
- **Clear Outcome.** Filled/remaining/avg-price are always exact, idempotent, and monotonic; over-fill is alarmed.

### 13.6.2 Residual completion strategy
- **Responsibility.** Decide what to do with the unfilled remainder of a partially-filled leg.
- **Behavior / Actions.** Per policy and time-of-day: (a) re-quote/chase the residual (13.4.2) to complete the intended size; (b) accept the partial and proportionally adjust the *other* legs so the structure stays balanced (13.6.3); or (c) cancel the residual and keep a smaller-but-balanced position. Near EOD, bias to completing-or-flattening fast.
- **Scenarios & Possibilities.**
  - Residual too small to be worth chasing (below min lot / negligible) → cancel residual.
  - Residual chase causes imbalance vs other legs → must coordinate with 13.6.3.
  - Liquidity gone (the reason it only partially filled) → completion impossible; accept-and-rebalance or unwind.
- **Functional Test Case(s).**
  - *Given* a leg 75/100 filled with low residual liquidity near EOD, *When* completion runs, *Then* it either completes via marketable within the time budget or cancels-and-rebalances, never leaves an unmanaged residual.
- **Clear Outcome.** No leg is left with an unmanaged working residual; the resolution keeps the multi-leg structure balanced.

### 13.6.3 Cross-leg fill-imbalance reconciliation
- **Responsibility.** Keep the *ratio* of filled quantities across legs consistent with the planned structure.
- **Behavior / Actions.** After fills settle, compare filled qty per leg against the plan's intended ratio (often 1:1:1:1 or defined multiples). If imbalanced (e.g., 100 short / 75 long-wing), either top up the lagging leg or trim the leading leg so the realized position matches a *scaled-down but structurally intact* version of the plan. The protective/risk-defining ratio takes precedence (never leave more short than hedged).
- **Scenarios & Possibilities.**
  - Short over-filled vs long wing → partially naked → trim short or complete long urgently (risk-capping priority).
  - Long over-filled vs short → over-hedged → benign (paid extra debit); trim if cheap, else accept.
  - Trimming itself partially fills → iterate to convergence with a max-iteration guard.
- **Functional Test Case(s).**
  - *Given* 100 short filled and only 75 protective-long filled, *When* imbalance reconciliation runs, *Then* it prioritizes restoring the hedge (top up long to 100 or trim short to 75), ending net-not-under-hedged.
- **Clear Outcome.** Realized fills form a structurally valid (possibly scaled) version of the plan; the account is never under-hedged due to imbalance.

---

## 13.7 Reject Handling & Retries

### 13.7.1 Reject classification (transient vs terminal)
- **Responsibility.** Map a broker reject reason to a handling class.
- **Behavior / Actions.** Parse the reject code/text into categories: TRANSIENT (rate-limit, gateway/timeout, broker-momentary) → retryable; PRICING (off-tick, price out of circuit/band, freeze-qty) → fix-and-retry; STRUCTURAL/PLAN (invalid symbol, lot mismatch, product not allowed) → not retryable, bounce to plan as invalid; CAPITAL (insufficient margin) → see 13.7.3; SESSION (auth) → re-auth path. Maintain a maintainable mapping table with an UNKNOWN→treat-as-non-retryable-and-alert default.
- **Scenarios & Possibilities.**
  - Free-text reject reasons vary by broker/version → mapping drifts; unknown reasons must fail safe (no blind retry).
  - A "transient" that is actually persistent (e.g., circuit limit) → bounded retries prevent infinite loop.
  - Misclassifying terminal as transient → wasteful/duplicate-risky retries.
- **Functional Test Case(s).**
  - *Given* a reject "Order price out of allowed range", *When* classified, *Then* class=PRICING (fix price, then retry), not blind-retry.
  - *Given* an unrecognized reject string, *When* classified, *Then* class=UNKNOWN → no auto-retry + alert.
- **Clear Outcome.** Every reject is classified; only genuinely transient/fixable rejects retry; unknowns fail safe with an alert.

### 13.7.2 Retry policy & backoff
- **Responsibility.** Govern how retryable rejects/transient errors are re-attempted.
- **Behavior / Actions.** Apply bounded retries with backoff (and jitter) and a max-attempts cap and a wall-clock deadline. CRITICAL: every retry passes through duplicate-detection (13.3.2) first. Stop retrying past the leg's relevance window (e.g., entry no longer valid, or near EOD).
- **Scenarios & Possibilities.**
  - Retry storm under broker outage → backoff + cap prevents hammering / rate-limit lockout.
  - Retry after an ambiguous timeout without dedupe → duplicate order (the cardinal sin).
  - Deadline passes mid-retry (e.g., entry window closed) → abort, report not-filled.
- **Functional Test Case(s).**
  - *Given* repeated transient gateway errors, *When* retried, *Then* attempts are spaced by backoff, capped at max-attempts, and each is preceded by a dedupe check.
  - *Given* the entry deadline elapses during retries, *When* the next attempt is due, *Then* it is abandoned with status not-filled + reason.
- **Clear Outcome.** Retries are bounded, backed-off, dedupe-guarded, and time-bounded; no storms, no duplicates, no zombie retries after relevance.

### 13.7.3 Margin / circuit / freeze reject handling
- **Responsibility.** Handle rejects rooted in capital or exchange microstructure constraints.
- **Behavior / Actions.** Insufficient-margin → do NOT silently retry (margin won't appear); bubble up to the capital/risk layer (this module can't size). For an *exit* that fails on margin, escalate hard (closing should not need margin; if it does, something is wrong). Freeze-quantity reject → re-slice into child orders (13.1.2). Circuit/price-band reject → re-price within band or wait for band reset. Exchange order-throttle/freeze-window → backoff and retry within the cancel/modify rules.
- **Scenarios & Possibilities.**
  - Margin reject on an *entry* short leg after wings filled → leg-in risk → must trigger unwind (13.2.4), not just report.
  - Margin reject on an *exit* → dangerous; escalate immediately, retry as plain close.
  - Repeated circuit rejects → instrument is locked; cannot trade; escalate.
- **Functional Test Case(s).**
  - *Given* a freeze-qty reject on a 1800-lot order, *When* handled, *Then* it is re-sliced into compliant child orders and resubmitted.
  - *Given* a margin reject on a short leg whose protective wing is already filled, *When* handled, *Then* leg-in unwind is triggered and the event bubbles up — not a silent retry.
- **Clear Outcome.** Capital/microstructure rejects route to the correct remedy (re-slice, re-price, or bubble-up); margin rejects never silently retry; exit-margin failures escalate.

---

## 13.8 Slippage Measurement

### 13.8.1 Per-leg slippage capture
- **Responsibility.** Quantify execution quality for each leg: expected vs realized price.
- **Behavior / Actions.** Record an expected/reference price at decision time (the plan's price, or arrival mid/touch) and compute slippage = signed (avg_fill_price − expected), normalized by side (a buy filled above expected = negative slippage; a sell filled below = negative). Capture for partial fills using the qty-weighted avg. Tag each with the order-type used (market vs limit) for later attribution.
- **Scenarios & Possibilities.**
  - No clean reference price (placed pre-open / no quote) → mark slippage as undefined rather than fabricate.
  - Market order in wide spread → large but *expected* slippage; still measured, just attributed to order-type.
  - Mid vs touch vs last as the benchmark → choose a documented, consistent benchmark; mixing them corrupts comparisons.
- **Functional Test Case(s).**
  - *Given* a sell leg expected 12.00 filled avg 11.90, *When* slippage computed, *Then* slippage = −0.10 (adverse) with correct sign for a sell.
  - *Given* no reference price available, *When* computed, *Then* slippage = undefined/null, not 0.
- **Clear Outcome.** Each leg has a correctly-signed, benchmark-consistent slippage figure (or an honest null), attributable to order-type.

### 13.8.2 Basket-level slippage aggregation
- **Responsibility.** Roll per-leg slippage into a plan-level execution-cost figure.
- **Behavior / Actions.** Aggregate signed per-leg slippage into a net basket slippage (in price and in rupees: × lot size × qty), expressing the total cost of execution vs the plan's theoretical entry. Surface it on the execution summary for monitoring and for upstream expectancy accounting.
- **Scenarios & Possibilities.**
  - One leg's favorable slippage masking another's adverse → report both gross and net.
  - Incomplete basket → aggregate only over executed legs and flag incompleteness (don't pretend full).
  - Rupee conversion must use correct lot size per index (NIFTY vs SENSEX differ).
- **Functional Test Case(s).**
  - *Given* legs with slippage +0.05 and −0.20, *When* aggregated, *Then* net = −0.15 reported alongside gross components, converted to rupees with the correct lot size.
- **Clear Outcome.** A faithful net + component execution-cost figure on the summary, lot-size-correct, with incompleteness flagged.

---

## 13.9 Order-State Reconciliation with Broker

### 13.9.1 Order-status acquisition (poll / stream)
- **Responsibility.** Continuously obtain authoritative order/fill status from the broker.
- **Behavior / Actions.** Subscribe to the broker order-update stream if available; additionally poll the order book/trade book on an interval as a backstop (streams drop). Normalize broker-specific status vocab into the module's canonical states. Drive all local records from this feed.
- **Scenarios & Possibilities.**
  - Stream disconnect → silently stale local state → poll backstop must catch it.
  - Status vocab mismatch (broker "COMPLETE"/"REJECTED"/"TRIGGER PENDING" etc.) → mapping must be exhaustive; unknown status → alert.
  - Poll rate too low near close → misses fast fills before square-off.
- **Functional Test Case(s).**
  - *Given* the order-update stream drops, *When* the poll interval elapses, *Then* the latest broker status is still ingested and local records updated.
  - *Given* an unmapped broker status string, *When* ingested, *Then* it is flagged unknown and alerted, not silently dropped.
- **Clear Outcome.** Local state never drifts undetectably from the broker; stream gaps are covered by polling; unknown statuses alert.

### 13.9.2 Local-vs-broker reconciliation
- **Responsibility.** Detect and resolve divergence between local records and the broker's truth.
- **Behavior / Actions.** Periodically diff local working/terminal records against the broker order book + trade book + positions. On divergence, broker wins: update local to match. Reconcile filled_qty, status, and the resulting *position* (net qty per contract) — the position is the ultimate check that fills add up.
- **Scenarios & Possibilities.**
  - Local says working, broker says filled → update + propagate fill (and slippage).
  - Local says filled 100, broker position shows 75 → fills mis-accounted → alarm and correct.
  - Local has an order the broker doesn't (never placed / cancelled) → mark terminal.
  - Clock skew on timestamps → reconcile on ids, not times.
- **Functional Test Case(s).**
  - *Given* local=working but broker=COMPLETE, *When* reconciliation runs, *Then* local becomes filled with the broker's fill price/qty and slippage is computed.
  - *Given* local filled_qty disagrees with broker position, *When* reconciled, *Then* an alarm fires and local is corrected to broker truth.
- **Clear Outcome.** Local state and the broker (orders + position) converge every cycle; the broker is authoritative; mismatches alarm before they compound.

### 13.9.3 Orphan / unknown-order detection
- **Responsibility.** Find broker orders/positions the module didn't expect, and module orders the broker never registered.
- **Behavior / Actions.** On startup/restart and periodically, scan the broker order book for orders carrying this module's tag namespace that have no local record (orphans from a crash mid-submit, per 13.1.4) and adopt them. Conversely flag local records with no broker counterpart. Surface any tagged order or position that doesn't map to a known plan.
- **Scenarios & Possibilities.**
  - Crash between submit and persist → live orphan order → must be adopted, else it executes unmanaged (naked risk).
  - Stale order from a previous day still working (shouldn't happen intraday, but defensive) → cancel/flag.
  - An untagged order/position appears (manual intervention) → out of module scope; report, do not touch.
- **Functional Test Case(s).**
  - *Given* a tagged broker order with no local record after a restart, *When* orphan detection runs, *Then* it is adopted into a local record and managed (or cancelled per policy), never left unmanaged.
  - *Given* an untagged manual position, *When* scanned, *Then* it is reported as out-of-scope and not acted upon.
- **Clear Outcome.** No tagged broker order ever runs unmanaged; foreign/untagged orders are reported but untouched.

---

## 13.10 Timeouts

### 13.10.1 Acknowledgment timeout (submit ambiguity)
- **Responsibility.** Bound how long a place/modify/cancel call may hang before its state is declared UNKNOWN.
- **Behavior / Actions.** Wrap each broker call in a timeout. On timeout, set state UNKNOWN_SUBMIT and schedule reconciliation (13.9) / dedupe (13.3.2) rather than throwing it away or blindly retrying. Never interpret an ack-timeout as "not placed".
- **Scenarios & Possibilities.**
  - Broker received it but the response was lost → order is live but locally UNKNOWN → reconciliation/orphan-scan recovers it.
  - Timeout too short → false UNKNOWNs and unnecessary reconciliation churn.
  - Timeout on a *cancel* → the order may or may not be cancelled → reconcile before any dependent action.
- **Functional Test Case(s).**
  - *Given* a place call that exceeds the ack timeout, *When* it fires, *Then* state=UNKNOWN_SUBMIT and a reconciliation/dedupe pass is queued (no blind resubmit).
- **Clear Outcome.** Hanging broker calls resolve to a defined UNKNOWN state that triggers recovery, never to a lost or duplicated order.

### 13.10.2 Working-order fill timeout (TTL)
- **Responsibility.** Bound how long a working (unfilled/partial) order may rest before action.
- **Behavior / Actions.** Assign each working order a TTL. On expiry, invoke the configured remedy: chase (13.4.2), convert-to-marketable, cancel-and-rebalance (13.6), or abort. TTLs tighten as EOD approaches and are overridden by square-off (13.11).
- **Scenarios & Possibilities.**
  - Passive limit never fills → TTL is the only thing that prevents an indefinitely-pending leg (and a stalled basket).
  - TTL fires exactly as a fill arrives → race; reconcile actual fill before acting on timeout.
  - Different TTLs for entry legs vs protective legs (protective should complete faster).
- **Functional Test Case(s).**
  - *Given* an unfilled limit reaching its TTL, *When* it expires, *Then* the configured remedy runs (chase/marketable/cancel), and the order does not remain indefinitely working.
  - *Given* a fill that lands at the TTL boundary, *When* the timeout handler runs, *Then* it checks reconciled state first and skips action if already filled.
- **Clear Outcome.** No working order rests beyond its TTL; expiry deterministically triggers a remedy; fill/timeout races resolve to broker truth.

---

## 13.11 Exit / Square-Off Order Handling

Intraday-only ⇒ a non-negotiable obligation to be flat by close. Exit execution is
higher-priority and lower-tolerance than entry.

### 13.11.1 Position-derived exit-order construction
- **Responsibility.** Build closing orders that exactly offset the *actual current position*, not the original plan.
- **Behavior / Actions.** Read the realized position (per contract, from reconciliation 13.9.2) and construct opposite-side orders for exactly the held qty per leg. Derive from broker position truth (handles partials/imbalance), never assume the plan filled fully. Order type per policy (limit-then-escalate, or marketable for protective exits).
- **Scenarios & Possibilities.**
  - Position differs from plan (partial entry) → exit must match *position*, else leaves a residual or over-closes (flips to a new naked position!).
  - Position already partly closed (a protective exit fired) → exit only the remainder.
  - Zero position for a leg → no exit order (don't place a phantom that opens a new position).
- **Functional Test Case(s).**
  - *Given* an actual position of short 75 (plan was 100), *When* exit is constructed, *Then* a buy-to-close of exactly 75 is built — not 100 (which would over-close into a long).
  - *Given* a leg with zero net position, *When* exit is constructed, *Then* no order is created for it.
- **Clear Outcome.** Exit orders precisely flatten the real position — never over-close into a new exposure, never leave a residual.

### 13.11.2 Forced square-off (market) escalation
- **Responsibility.** Guarantee flatness when passive exits won't complete and the deadline looms.
- **Behavior / Actions.** As the EOD cutoff (or broker auto-square-off time) approaches, escalate any unfilled exit to marketable/aggressive to ensure completion, accepting slippage as the cost of certainty. Sequence escalation to flatten *short* (risk) legs first if structure must be partially closed. Prioritize over all entry/normal activity.
- **Scenarios & Possibilities.**
  - Broker auto-square-off fires first (at its own cutoff) at potentially worse prices → module should flatten *before* the broker's forced cutoff.
  - Illiquid leg won't fill even at market near close → escalate/alert; genuine trapped position (last-resort human alert).
  - Closing a hedged structure leg-by-leg transiently nakeds it → flatten the riskier short side first / accept brief exposure consciously.
- **Functional Test Case(s).**
  - *Given* an exit limit unfilled near the square-off cutoff, *When* the escalation trigger fires, *Then* it is converted to a marketable order to force completion before the cutoff.
  - *Given* a leg that cannot fill even at market, *When* the deadline passes, *Then* a hard alert fires (trapped position) and the state is recorded.
- **Clear Outcome.** Positions are flat before the cutoff in all fillable cases; unfillable cases raise a hard, unmissable alert.

### 13.11.3 End-of-day flat verification
- **Responsibility.** Confirm, from broker truth, that nothing is left open at session end.
- **Behavior / Actions.** After square-off, re-read the broker positions and order book; assert all module positions = 0 and no working orders remain. Any non-zero residual or lingering working order → escalate immediately (don't carry overnight risk silently). Produce the final per-leg fill report and execution summary.
- **Scenarios & Possibilities.**
  - A late fill after the verification snapshot reopens a position → verification must be late enough / re-checked.
  - A working order survived cancel-all → still live into close → must be caught and cancelled.
  - Broker auto-squared-off a leg at its price → reconcile that fill into the report (it's still a real exit).
- **Functional Test Case(s).**
  - *Given* end of session, *When* flat verification runs, *Then* it confirms zero positions and zero working orders for the module, or escalates with the exact residual.
  - *Given* the broker auto-squared a leg, *When* verification runs, *Then* that auto-exit fill is reconciled into the final report.
- **Clear Outcome.** Session ends provably flat (verified against the broker), or a residual is loudly escalated; the final report is complete and reconciled.

---

## Suggestions (for bubble-up)

These are scenarios where Module 13 can *detect and mitigate* mechanically, but the
*decision* or *policy* properly belongs to the wider system. Flagged separately for
later review.

1. **One leg fills, the other rejects (leg-in / naked exposure).** Module 13 can risk-cap
   (buy the missing protective wing at market) and unwind, but the *policy* — complete vs
   revert, and the acceptable slippage to do so — is a risk/strategy decision. Needs a
   system-wide rule and a guaranteed escalation channel. The naked-short case is the single
   highest-severity event this module emits.

2. **Partial fill near the close.** The trade-off between completing a residual (chasing
   into thin EOD liquidity) and accepting a scaled/imbalanced position interacts with
   strategy expectancy and risk limits. Module 13 needs an authoritative EOD time budget
   and a policy for "minimum viable structure" vs flatten.

3. **Broker freeze / circuit / exchange halt.** When an instrument or the whole exchange
   freezes, the module cannot place/modify/cancel/exit. This is a trapped-risk condition
   spanning risk, capital, and ops. Needs a system-level contingency (hedge elsewhere?
   alert human? capital reserve?) and a defined escalation, since 13 alone cannot resolve it.

4. **Duplicate order on retry (over-exposure).** 13 guards via client-order-id + dedupe,
   but brokers that don't echo tags force a fuzzy fallback that can be wrong. The *risk
   appetite* for "place-and-maybe-duplicate" vs "skip-and-maybe-miss" under ambiguity is a
   system policy. Over-exposure (real money beyond sized risk) should default to skip +
   escalate; confirm that bias system-wide.

5. **Position truth ownership / split-brain.** 13 reconciles to broker truth, but if other
   modules (or manual action) also touch positions, "whose order is this" needs a
   system-wide tagging/ownership contract. Untagged orders are reported but untouched —
   confirm that boundary is acceptable.

6. **Margin reject on entry after wings filled.** Capital exhaustion mid-basket is both an
   execution event (unwind) and a sizing/capital failure. The capital layer should prevent
   it; 13 should never have to unwind for margin. Bubble up as a sizing-layer defect when
   it occurs.

7. **Exit requiring margin / exit reject.** A close failing for capital reasons is a
   system-level red flag (closing should free margin). Define the guaranteed escalation and
   a last-resort manual-intervention path.
