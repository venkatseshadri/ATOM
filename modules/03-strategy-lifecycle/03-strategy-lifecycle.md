# Module 3 — Strategy Lifecycle

> Discovery document. Breadth-first decomposition of the decision brain that governs a single options position from birth to forced-flat. Reasoned from first principles, single-module visibility only. Parameter values, downstream builders, and sibling modules are deliberately NOT assumed.

---

## 3.0 Module Overview

**Responsibility.** Own the *temporal logic* of an options position: given a market-state signal and the current position status, emit exactly one lifecycle decision — `open`, `adjust/morph`, `hold`, or `exit` — with enough intent for a downstream builder to realize it. This module is the **state machine + policy**, not the order router and not the contract constructor.

**Inputs (plain data).**
- `market_state`: a label (e.g. RANGE / TREND_UP / TREND_DOWN / VOL_EXPANDING / VOL_CRUSHING / CHOPPY / UNKNOWN — labels are not fixed here) plus a numeric `confidence ∈ [0,1]`.
- `position_status`: either `FLAT`, or an `OPEN` structure described by its legs, the structure type/shape, entry time, and live P&L (and any derived metrics the upstream chose to attach, treated as opaque).
- Ambient clock context: current time-of-day / time-to-close, and time-to-expiry are needed to reason; treat as available scalars even though their thresholds live elsewhere.

**Outputs (plain data).** A single decision object:
- `decision ∈ {open, adjust/morph, hold, exit}`
- `intent`: structured hints the builder needs (e.g. desired structure family, directional bias, which leg(s) to roll, urgency/exit-reason). No exact strikes/lots — that is the builder's job.
- `rationale` + `signal_snapshot` for audit.

**Non-responsibilities.** No order placement. No strike/lot selection. No P&L *calculation* (consumes it). No market-state *derivation* (consumes it). No persistence guarantees beyond emitting the decision.

**Core invariant.** At-or-after the end-of-day flat horizon, the only legal decision for any OPEN position is `exit`. Theta-harvest, intraday-only.

---

## Decomposition map

- **3.1 Position State Machine** — canonical states, legal transitions, single source of truth for "where are we."
- **3.2 Entry Decision (FLAT → OPEN)** — when to open, and refusal to open.
- **3.3 Adjustment / Morph Decision (OPEN → OPEN')** — when and how to mutate a live structure.
- **3.4 Hold / Ride Decision (OPEN → OPEN)** — the do-nothing-on-purpose branch.
- **3.5 Exit Decision (OPEN → FLAT)** — all the ways a position dies before EOD.
- **3.6 Signal Conflict & Confidence Arbitration** — low-confidence, contradictory, stale, or UNKNOWN signals.
- **3.7 New-Signal-While-Open Reconciliation** — a fresh signal arrives mid-life; reconcile vs the open structure.
- **3.8 Re-entry Policy (FLAT-after-exit → OPEN?)** — cooldowns, re-arm, churn control.
- **3.9 End-of-Day Forced Flat** — the terminal, non-negotiable horizon.
- **3.10 Decision Output & Arbitration Bus** — collapse competing branch verdicts into ONE decision.

Two competing lifecycle *philosophies* are explored throughout and called out where they diverge:
- **Design A — Single-Structure / Close-and-Reopen:** one position at a time; respond to change by exiting and (maybe) re-entering fresh. Simple, fewer states, more churn/slippage.
- **Design B — Staged-Adjustment / Morph-in-Place:** keep the position alive and mutate it (roll untested side, convert fly→condor, add a wing). Fewer round-trips, richer state, harder to reason about and to test.

---

## 3.1 Position State Machine

### 3.1.1 State definition & ownership
**Responsibility.** Define the canonical set of lifecycle states and hold the single authoritative "current state."

**Behavior / Actions.**
- States: `FLAT`, `PENDING_OPEN` (decision emitted, builder not yet confirmed), `OPEN`, `PENDING_ADJUST`, `PENDING_EXIT`, `COOLDOWN` (flat but blocked from re-entry), `LOCKED_EOD` (flat, no more entries today).
- Derive current state purely from `position_status` + last-emitted-decision + clock; never guess.
- Expose a read-only "what state am I" to all sibling branches so they don't each re-derive.

**Scenarios & Possibilities.**
- Builder never confirms a `PENDING_OPEN` (order rejected/timeout) → must fall back to `FLAT`, not hang.
- `position_status` says OPEN but module has no record of opening it (restart / external fill) → adopt-or-disown decision; default = treat as OPEN and manage to safety (exit), never ignore.
- Two `PENDING_*` in flight (race) → state machine must serialize; only one pending transition at a time.

**Functional Test Case(s).**
- Given last decision `open` and `position_status=FLAT` after the builder-confirm window elapsed, When state is queried, Then state = `FLAT` (reverted) and a warning is surfaced.
- Given `position_status=OPEN` with no prior open record, When queried, Then state = `OPEN` (adopted) and flagged `orphan_adopted`.

**Clear Outcome.** Exactly one well-defined state at all times; no orphan/hung pending states; unknown reality resolves toward "manage to safety."

### 3.1.2 Legal transition table
**Responsibility.** Encode which transitions are permitted; reject illegal ones.

**Behavior / Actions.**
- Allowed: FLAT→PENDING_OPEN→OPEN; OPEN→PENDING_ADJUST→OPEN; OPEN→PENDING_EXIT→FLAT; FLAT→COOLDOWN; any→LOCKED_EOD (for flats) / OPEN→PENDING_EXIT (for opens at EOD); COOLDOWN→FLAT (timer elapsed).
- Forbidden: FLAT→adjust, FLAT→hold-with-intent, OPEN→open (no stacking — unless Design allows multi-position; default single-position ⇒ forbidden), LOCKED_EOD→PENDING_OPEN.
- Any forbidden request is downgraded to the safe nearest-legal decision (usually `hold` if OPEN, `no-op` if FLAT).

**Scenarios & Possibilities.**
- Adjust requested while FLAT (signal-branch bug) → reject, emit nothing or `no-op`.
- Open requested while already OPEN (3.7 should have caught it) → reject open; route to 3.7 reconciliation.
- Exit requested while already FLAT → idempotent no-op.

**Functional Test Case(s).**
- Given state `FLAT`, When an `adjust/morph` verdict arrives, Then it is rejected and logged `illegal_transition`.
- Given state `OPEN`, When an `open` verdict arrives, Then it is rerouted to reconciliation (3.7), not stacked.

**Clear Outcome.** Only table-legal transitions execute; illegal requests degrade safely, never crash or stack unintended positions.

### 3.1.3 Single-position vs multi-position posture (Design fork)
**Responsibility.** Decide whether the machine tracks at most one structure (Design A/B default) or a small portfolio.

**Behavior / Actions.**
- Default posture: **one active structure**. New entry forbidden until current is FLAT.
- Alt posture (explore-only): allow N≤cap concurrent independent structures, each with its own sub-state-machine keyed by id.
- Whichever posture, the transition table and EOD flat apply per-structure.

**Scenarios & Possibilities.**
- Multi-position posture risks correlated NIFTY+SENSEX exposure double-counting → must surface aggregate, but capital/limits live in another module (bubble up).
- Single-position misses a clearly better setup while occupied → accepted cost of simplicity.

**Functional Test Case(s).**
- Given single-position posture and one OPEN structure, When a second strong entry signal arrives, Then no second open is emitted (reconcile or ignore).
- Given multi-position posture cap=2 and 2 OPEN, When a third signal arrives, Then entry refused (`at_capacity`).

**Clear Outcome.** Posture is explicit and enforced; concurrency never exceeds the configured cap; default is the simplest single-structure machine.

---

## 3.2 Entry Decision (FLAT → OPEN)

### 3.2.1 Entry gating preconditions
**Responsibility.** Refuse to even consider opening unless hard preconditions hold.

**Behavior / Actions.**
- Require: state ∈ {FLAT}, not in COOLDOWN, not past entry-cutoff time-of-day, not LOCKED_EOD, signal confidence ≥ entry-floor (value lives elsewhere; treat as parameter), market_state ∈ entry-eligible set.
- If any precondition fails → emit no entry; fall through to `hold`/`no-op`.

**Scenarios & Possibilities.**
- Confidence exactly at floor → define inclusive/exclusive once and keep consistent.
- Signal eligible but time already inside the EOD no-new-entry window → refuse (a fresh theta position with too little life left can't harvest enough).
- Signal arrives during COOLDOWN → refuse, note "would-have-entered" for analytics.

**Functional Test Case(s).**
- Given FLAT, confidence below floor, When evaluated, Then decision ≠ open.
- Given FLAT, eligible signal, but clock past entry-cutoff, When evaluated, Then decision = `no-op`/`hold` with reason `late_session_no_entry`.

**Clear Outcome.** Entry is attempted only when every precondition passes; otherwise a clean non-entry, never a half-open.

### 3.2.2 Structure-family selection (intent, not contracts)
**Responsibility.** Map market_state → desired theta structure family and directional bias as *intent*.

**Behavior / Actions.**
- Map e.g. RANGE/neutral → symmetric short-premium (iron-fly / iron-condor family); mild directional bias → skewed credit spread; vol-crushing → short premium favored; vol-expanding → either stand aside or defined-risk debit (theta harvest is dubious here — likely refuse).
- Output family + bias + "width/aggression" qualitative hint only. No strikes, no lots.
- If no family cleanly fits the state → refuse entry (don't force a structure onto an unsuitable regime).

**Scenarios & Possibilities.**
- Ambiguous state (range-ish but trending) → prefer the more defensive family or refuse.
- VOL_EXPANDING with high confidence → theta seller's trap; default intent = stand aside, flag for review.
- Expiry-day state where theta is hyper-concentrated → family choice may differ (bubble up; expiry mechanics are system-wide).

**Functional Test Case(s).**
- Given market_state=RANGE high-confidence, When selecting, Then intent.family = neutral short-premium, bias = neutral.
- Given market_state=VOL_EXPANDING, When selecting, Then decision = refuse-or-defensive, not naked short premium.

**Clear Outcome.** Every emitted entry carries a coherent family+bias intent justified by the state; unsuitable regimes produce a refusal, not a forced trade.

### 3.2.3 Entry timing / trigger discipline
**Responsibility.** Decide *now vs wait* once an eligible setup exists.

**Behavior / Actions.**
- Options: (a) immediate on first eligible signal; (b) require persistence (signal holds N evaluations) to avoid one-tick noise; (c) wait for a pullback/IV condition.
- Default: light persistence guard to dodge single-bar flicker; never wait so long that EOD horizon eats the trade.
- Emit `open` the instant the timing rule is satisfied.

**Scenarios & Possibilities.**
- Signal flickers eligible/ineligible each evaluation → persistence guard prevents whipsaw entry.
- Persistence requirement pushes entry past cutoff → abandon for the day.
- Single very-high-confidence spike → optionally bypass persistence (fast-mover path).

**Functional Test Case(s).**
- Given an eligible signal present for only 1 of N required evaluations, When timing-checked, Then no open yet.
- Given the signal persists N evaluations within session, When checked, Then `open` emitted once.

**Clear Outcome.** Entries are deliberate, not noise-triggered, and never fire so late they can't harvest theta meaningfully.

---

## 3.3 Adjustment / Morph Decision (OPEN → OPEN')

> This whole sub-tree is **Design B**. Under **Design A** it collapses to "never adjust; exit and let 3.8 re-enter." Both are documented; 3.10 picks one per config.

### 3.3.1 Adjustment trigger detection
**Responsibility.** Detect that the live structure is stressed enough to warrant a change short of full exit.

**Behavior / Actions.**
- Triggers (any): underlying approaching/breaching a short strike (tested side); P&L crossing an intra-trade threshold (down, not yet stop); market_state shifting away from entry thesis but not violently; greeks drift (delta runaway) — treated as opaque metrics if provided.
- Distinguish "stress → adjust" from "thesis-broken → exit" (3.5 owns exit).

**Scenarios & Possibilities.**
- Tested side breached but state still range-consistent → adjust (roll) rather than exit.
- Both sides quiet, just time passing → no trigger; defer to 3.4 hold.
- Stress AND thesis broken simultaneously → exit wins over adjust (priority handled in 3.10).

**Functional Test Case(s).**
- Given OPEN structure with underlying touching the short call strike and state still RANGE, When evaluated, Then an adjust trigger fires (not exit).
- Given OPEN with no leg threatened, When evaluated, Then no adjust trigger.

**Clear Outcome.** Adjustment fires only on genuine, recoverable stress; clean and thesis-broken cases route elsewhere.

### 3.3.2 Morph type selection
**Responsibility.** Choose *which* mutation to request.

**Behavior / Actions.**
- Catalogue of morphs as intent: roll tested side out/away; roll untested side in (collect more credit / re-center); widen a wing (defined-risk preserved); convert fly→condor or condor→fly; add a hedge leg; recenter the whole structure.
- Emit `adjust/morph` with the chosen morph + which leg(s).
- Guard: morph must preserve defined-risk; never request a mutation that uncaps risk.

**Scenarios & Possibilities.**
- Repeated rolls chasing the same runaway move → cap number of adjustments per position to avoid "rolling into a loss."
- Recenter requested but recentering would breach EOD horizon → downgrade to exit.
- Morph that would invert the structure (bias flip) → likely should be exit+reopen, not morph.

**Functional Test Case(s).**
- Given tested-side breach with state intact, When morph-selecting, Then intent = roll tested side, defined-risk preserved.
- Given adjustment count already at cap, When another trigger fires, Then decision = exit, not adjust.

**Clear Outcome.** A risk-preserving, bounded mutation is selected; runaway adjustment loops are capped and convert to exit.

### 3.3.3 Adjustment budget & cost awareness
**Responsibility.** Bound how often/expensively a position may be adjusted.

**Behavior / Actions.**
- Track adjustments-this-position vs a cap; track cumulative credit given back.
- If adjusting would turn a theta-positive trade net-negative beyond tolerance → prefer exit.
- This module reasons qualitatively about cost; actual fills/slippage are downstream (bubble up if cost model needed).

**Scenarios & Possibilities.**
- Each roll collects less credit than the last → diminishing returns; stop adjusting.
- Choppy market causing rapid back-to-back triggers → budget exhausts fast, forcing exit (good — prevents bleed).

**Functional Test Case(s).**
- Given 1 adjustment already used and cap=1, When a new trigger fires, Then no further adjust; route to exit.
- Given net credit-given-back exceeds tolerance, When evaluated, Then exit preferred over further morph.

**Clear Outcome.** Adjustments are a finite, accounted budget; exhaustion deterministically yields exit, preventing endless roll-for-a-loss.

---

## 3.4 Hold / Ride Decision (OPEN → OPEN)

### 3.4.1 Thesis-still-valid confirmation
**Responsibility.** Confirm the original entry thesis remains intact so doing nothing is correct.

**Behavior / Actions.**
- Compare current market_state to entry thesis; if consistent and no exit/adjust trigger active → emit `hold`.
- Hold is the *default* OPEN decision; it must be explicitly chosen, not a fall-through accident.

**Scenarios & Possibilities.**
- State unchanged, position mildly profitable on theta → textbook hold.
- State drifted but within tolerance band → still hold, log "watching."
- No new signal at all (signal source quiet) → hold (absence ≠ exit).

**Functional Test Case(s).**
- Given OPEN, state matches entry thesis, no triggers, When evaluated, Then decision = `hold`.
- Given OPEN and no fresh signal arrived this cycle, When evaluated, Then decision = `hold` (not exit).

**Clear Outcome.** A profitable/quiet position is intentionally left to harvest theta; silence never causes an exit.

### 3.4.2 Profit-ride vs profit-protect posture
**Responsibility.** Decide whether to keep riding gains or tighten toward a protective exit.

**Behavior / Actions.**
- If P&L reaches a "most of max profit captured" zone → bias toward exit (3.5.3) rather than risk giving it back for marginal remaining theta.
- Below that zone with valid thesis → ride.
- Optionally arm a trailing protection intent (hint to 3.5), without itself exiting.

**Scenarios & Possibilities.**
- Near-max profit early in session → take it; remaining theta isn't worth tail risk.
- Profit plateau, lots of session left → ride but watch.
- Whipsaw: profit hit target then reversed before decision applied → 3.5 trailing should have captured; note race.

**Functional Test Case(s).**
- Given P&L in capture-most zone, When evaluated, Then decision biases to exit (hand to 3.5.3).
- Given modest profit, valid thesis, ample session, When evaluated, Then decision = `hold`.

**Clear Outcome.** Winners are ridden while worthwhile and harvested before round-trip risk dominates; the line between ride and protect is explicit.

---

## 3.5 Exit Decision (OPEN → FLAT)

### 3.5.1 Stop-loss / max-adverse exit
**Responsibility.** Force exit when loss breaches the defined-risk stop.

**Behavior / Actions.**
- If live loss ≥ stop threshold (e.g. multiple of credit, value elsewhere) → emit `exit` with reason `stop_loss`, highest urgency.
- This overrides hold and adjust (3.10 priority).

**Scenarios & Possibilities.**
- Gap/spike blows through the stop level between evaluations → exit at first observation; can't prevent the gap (bubble up: gap risk is system-wide).
- Loss oscillating around the stop → first definitive breach triggers; add tiny hysteresis to avoid flip-flop, but never *delay* a real stop.
- Defined-risk structure already at max loss (can't lose more) → exit still emitted to free capital / avoid pin risk.

**Functional Test Case(s).**
- Given loss ≥ stop, When evaluated, Then decision = `exit`, reason `stop_loss`, urgency=high.
- Given loss just under stop, When evaluated, Then not a stop exit (may still hold/adjust).

**Clear Outcome.** Stop breaches always exit promptly; defined risk is the ceiling, and the module never sits in a breached position.

### 3.5.2 Thesis-invalidation exit
**Responsibility.** Exit when the market regime that justified the trade is gone, even if not yet at stop.

**Behavior / Actions.**
- If market_state flips to a regime incompatible with the structure (e.g. neutral fly but state→strong TREND with high confidence) and adjustment can't rescue it (or Design A) → `exit`, reason `thesis_broken`.

**Scenarios & Possibilities.**
- Slow drift vs hard flip → only hard, high-confidence flips exit; soft drift → adjust/hold.
- State flips to UNKNOWN/low-confidence → don't panic-exit on uncertainty alone (see 3.6); thesis-broken needs a *confident contrary* state.

**Functional Test Case(s).**
- Given neutral structure and state flips to high-confidence TREND, no rescue available, When evaluated, Then `exit` reason `thesis_broken`.
- Given state flips to UNKNOWN low-confidence, When evaluated, Then not a thesis exit (hold/handle via 3.6).

**Clear Outcome.** Positions don't outlive their rationale, but uncertainty alone never forces an exit.

### 3.5.3 Profit-target / theta-captured exit
**Responsibility.** Exit to bank gains once enough theta/profit is captured.

**Behavior / Actions.**
- If P&L ≥ profit-take threshold (% of max profit) → `exit`, reason `profit_target`.
- Cooperates with 3.4.2's protect posture.

**Scenarios & Possibilities.**
- Hit target very early → still take it (intraday, no overnight to wait for).
- Profit target and EOD horizon both near → either path exits; reason precedence: profit_target if reached first.
- Trailing variant: lock a fraction once a peak is passed, exit on give-back.

**Functional Test Case(s).**
- Given P&L ≥ profit-take, When evaluated, Then `exit` reason `profit_target`.
- Given P&L below target and rising, When evaluated, Then hold (not premature exit).

**Clear Outcome.** Gains are banked at the configured capture level; winners are not held greedily into reversal or EOD.

### 3.5.4 Time-based / no-progress exit
**Responsibility.** Exit positions that are going nowhere and just consuming risk-budget/time.

**Behavior / Actions.**
- If position has been open beyond a max-hold and P&L is flat/marginal (theta not accruing as hoped) → `exit`, reason `time_stop`.
- Also catches "dead" structures where IV collapsed and little premium remains to harvest.

**Scenarios & Possibilities.**
- Position flat for long stretch then thesis still valid → judgment: time_stop vs keep harvesting; default lean exit to recycle capital late session.
- Almost-worthless short premium (little left to gain, all tail risk) → exit even if technically "winning."

**Functional Test Case(s).**
- Given open > max-hold and P&L marginal, When evaluated, Then `exit` reason `time_stop`.
- Given residual premium negligible, When evaluated, Then `exit` (risk/reward exhausted).

**Clear Outcome.** Stagnant or exhausted positions are recycled rather than carried for negligible remaining edge.

---

## 3.6 Signal Conflict & Confidence Arbitration

### 3.6.1 Low-confidence handling
**Responsibility.** Decide behavior when confidence is below actionable thresholds.

**Behavior / Actions.**
- FLAT + low confidence → no entry.
- OPEN + low confidence on a contrary signal → do NOT act on it for entry/thesis-exit; protective exits (stop, EOD) remain unconditional.
- Low confidence never *creates* an action; it only fails to authorize discretionary ones.

**Scenarios & Possibilities.**
- Confidence collapses while OPEN → hold (don't churn on noise), but keep stops armed.
- Persistent low confidence all session → likely no trades; acceptable.

**Functional Test Case(s).**
- Given FLAT and confidence below entry-floor, When evaluated, Then no open.
- Given OPEN and a low-confidence contrary signal, When evaluated, Then no thesis-exit; stops still active.

**Clear Outcome.** Uncertainty suppresses discretionary action but never disables protective exits.

### 3.6.2 Contradictory-signal arbitration
**Responsibility.** Resolve when the signal contradicts the current position's thesis.

**Behavior / Actions.**
- Weigh contrary-signal confidence against a higher bar than entry (exiting a working trade should require conviction).
- Confident contrary → route to thesis-exit (3.5.2) or adjust (3.3). Weak contrary → hold.
- Encode an explicit asymmetry: it's harder to flip a position than to open one.

**Scenarios & Possibilities.**
- Signal oscillates pro/contra each cycle → hysteresis prevents whipsaw exits.
- Contrary signal that's actually just noise around a regime boundary → treat as low-conviction.

**Functional Test Case(s).**
- Given OPEN and a contrary signal above the exit-conviction bar, When evaluated, Then exit/adjust considered.
- Given OPEN and oscillating contrary signals each below the bar, When evaluated, Then hold (no whipsaw).

**Clear Outcome.** Open positions are defended against noise; only high-conviction contradiction moves them.

### 3.6.3 Stale / missing / UNKNOWN signal handling
**Responsibility.** Behave safely when the signal is absent, stale, or explicitly UNKNOWN.

**Behavior / Actions.**
- Missing/stale signal (timestamp too old): FLAT → no entry; OPEN → hold but keep protective exits live; never enter on stale data.
- UNKNOWN state label → treat like low-confidence: suppress discretionary action.
- Define staleness by signal timestamp vs now (threshold elsewhere).

**Scenarios & Possibilities.**
- Signal feed dies mid-position → module must not freeze; defaults to hold + protective-exits + EOD flat still guaranteed.
- Stale signal that looks eligible → must be rejected precisely because it's stale (avoid acting on a frozen view).

**Functional Test Case(s).**
- Given a signal older than staleness limit while OPEN, When evaluated, Then hold; stop/EOD still enforced.
- Given UNKNOWN state while FLAT, When evaluated, Then no entry.

**Clear Outcome.** Data gaps degrade to a safe hold-and-protect posture; protective and EOD paths never depend on a fresh signal.

---

## 3.7 New-Signal-While-Open Reconciliation

### 3.7.1 Same-direction (confirming) signal
**Responsibility.** Handle a fresh signal that agrees with the open thesis.

**Behavior / Actions.**
- Default: `hold` (already positioned correctly); do not stack a second structure (single-position posture).
- Optionally refresh internal thesis-confidence/clock so 3.5.4 time-stop is recalibrated.

**Scenarios & Possibilities.**
- Confirming signal tempts a "double down" → refused under single-position; logged.
- Confirming but now near EOD → still no new exposure.

**Functional Test Case(s).**
- Given OPEN and a confirming signal, When reconciled, Then decision = hold, no new open.

**Clear Outcome.** Confirmation reinforces holding; it never silently stacks risk.

### 3.7.2 Opposing signal while open
**Responsibility.** Handle a fresh signal contradicting the open structure.

**Behavior / Actions.**
- Route through 3.6.2 conviction test → hold / adjust / exit.
- If exit chosen and the new signal is *also* entry-eligible the other way, hand to 3.8 re-entry (don't auto-flip in one step; exit first, re-arm, then maybe enter).

**Scenarios & Possibilities.**
- Strong reversal mid-position → exit current, consider fresh opposite entry next cycle (avoids fragile atomic flip).
- Opposing signal right before EOD → exit only, no re-entry (no life left).

**Functional Test Case(s).**
- Given OPEN long-biased structure and a strong opposite signal, When reconciled, Then exit current; re-entry deferred to 3.8.

**Clear Outcome.** Reversals are handled as exit-then-maybe-reenter, never as a brittle single-step position inversion.

### 3.7.3 Duplicate / repeated entry-signal suppression
**Responsibility.** Prevent the same standing condition from re-triggering entry while already positioned for it.

**Behavior / Actions.**
- De-dup: if currently OPEN on a thesis, identical fresh entry signals are absorbed (hold), not re-acted.
- Track "what am I already positioned for" to compare against incoming entry intents.

**Scenarios & Possibilities.**
- Signal source re-emits every cycle → without suppression you'd spam opens (caught by 3.1 too, but suppress earlier for cleanliness).

**Functional Test Case(s).**
- Given OPEN for thesis X and repeated entry signals for X each cycle, When reconciled, Then zero additional opens.

**Clear Outcome.** Standing conditions don't generate duplicate entries; one condition, one position.

---

## 3.8 Re-entry Policy (FLAT-after-exit → OPEN?)

### 3.8.1 Cooldown / churn control
**Responsibility.** Block immediate re-entry after an exit to prevent thrash.

**Behavior / Actions.**
- On exit, enter COOLDOWN for a timer (value elsewhere). During COOLDOWN, 3.2 refuses entry.
- Cooldown may differ by exit reason: stop_loss → longer cooldown; profit_target → shorter/none; EOD → no re-entry at all.

**Scenarios & Possibilities.**
- Stop out then instantly re-enter into the same adverse move → cooldown prevents the classic re-loss.
- Profit-take then a fresh clean setup appears → short/zero cooldown lets it re-engage.

**Functional Test Case(s).**
- Given an exit reason=stop_loss, When a new eligible signal arrives within cooldown, Then no re-entry.
- Given exit reason=profit_target and cooldown elapsed/zero with eligible signal, Then re-entry allowed.

**Clear Outcome.** Re-entry churn is throttled, harder after losses, easier after wins; no instant re-loss loops.

### 3.8.2 Re-entry cap & conviction bar
**Responsibility.** Limit number of re-entries per day and require a clean fresh setup.

**Behavior / Actions.**
- Track entries-today vs a daily cap; beyond cap → no more entries even if eligible.
- Re-entry must clear the same (or higher) confidence bar as a first entry; a stopped-out thesis shouldn't re-enter on a marginal signal.

**Scenarios & Possibilities.**
- Choppy day generating many setups → cap prevents over-trading / fee bleed.
- Genuine new high-conviction regime late morning after two earlier stops → allowed if under cap.

**Functional Test Case(s).**
- Given entries-today at daily cap, When an eligible signal arrives, Then no entry (`daily_cap`).
- Given under cap and a high-conviction fresh signal post-cooldown, Then entry allowed.

**Clear Outcome.** Daily entry count is bounded; re-entry requires real conviction, not revenge trading.

---

## 3.9 End-of-Day Forced Flat

### 3.9.1 EOD square-off enforcement
**Responsibility.** Guarantee every OPEN position exits before the intraday flat horizon.

**Behavior / Actions.**
- At/after the EOD flat time, any OPEN → emit `exit`, reason `eod_flat`, highest priority, overriding hold/adjust/profit/thesis branches.
- Non-negotiable: this path must fire even if signals are stale/missing/UNKNOWN.

**Scenarios & Possibilities.**
- Signal feed down at EOD → exit still fires (must not depend on signal).
- Position already in PENDING_EXIT → idempotent; ensure it actually closes.
- Builder fails to close → escalate (retry/urgent flag); carrying overnight violates the core mandate (bubble up if close fails).

**Functional Test Case(s).**
- Given OPEN and clock ≥ EOD flat time, When evaluated, Then decision = `exit` reason `eod_flat` regardless of P&L or signal.
- Given OPEN, EOD reached, and signal missing, When evaluated, Then still `exit`.

**Clear Outcome.** No position survives past the flat horizon under any signal condition; the intraday-only invariant holds absolutely.

### 3.9.2 EOD no-new-entry lockout
**Responsibility.** Stop opening new positions once too little session remains to harvest.

**Behavior / Actions.**
- After the entry-cutoff (earlier than the square-off), transition flats toward LOCKED_EOD; 3.2 refuses all new opens.
- Adjustments that would extend exposure near EOD are also refused (downgrade to exit).

**Scenarios & Possibilities.**
- Strong signal 10 min before close → refused; not enough theta runway, and forced exit looms.
- Entry-cutoff vs square-off gap defines a "manage-only" window where holds/exits allowed but no opens.

**Functional Test Case(s).**
- Given FLAT and clock past entry-cutoff, When an eligible signal arrives, Then no open (`eod_lockout`).
- Given OPEN in the manage-only window, When evaluated, Then hold/adjust/exit allowed but no new open.

**Clear Outcome.** Late-session new exposure is prevented; only management and exit happen in the run-up to flat.

---

## 3.10 Decision Output & Arbitration Bus

### 3.10.1 Branch priority arbitration
**Responsibility.** Collapse all branch verdicts into exactly one decision via a fixed priority order.

**Behavior / Actions.**
- Priority (highest→lowest): `EOD exit` > `stop_loss exit` > `profit_target exit` > `thesis_broken exit`/`adjust` (per Design A/B) > `time_stop exit` > `hold` > `open` (only if FLAT). Open never competes with an OPEN-state branch.
- Exactly one decision emitted per evaluation cycle.

**Scenarios & Possibilities.**
- Stop and profit both technically near in a volatile bar → stop wins (safety first).
- Adjust and exit both triggered → exit wins by default unless Design B + adjustment budget remains and stress is recoverable.
- Conflicting branch outputs with equal priority → deterministic tiebreak (most conservative).

**Functional Test Case(s).**
- Given EOD reached and a profit_target also met, When arbitrated, Then decision = `exit` reason `eod_flat` (EOD outranks).
- Given both adjust trigger and stop breach, When arbitrated, Then `exit` reason `stop_loss`.

**Clear Outcome.** One unambiguous, deterministic decision per cycle, with safety-first precedence.

### 3.10.2 Intent payload assembly
**Responsibility.** Package the winning decision with the minimum intent a builder needs.

**Behavior / Actions.**
- For `open`: family, bias, aggression hint. For `adjust`: morph type + target leg(s) + risk-preserved flag. For `exit`: reason + urgency. For `hold`: reason only.
- Never emit strikes/lots/contracts (builder's domain). Include `signal_snapshot` + `state` for audit.

**Scenarios & Possibilities.**
- Builder needs a field this module shouldn't decide → surface as a gap (bubble up), don't fabricate a strike.
- Empty/under-specified intent → reject internally rather than ship an unactionable decision.

**Functional Test Case(s).**
- Given an `adjust/morph` decision, When assembled, Then payload names morph type + leg(s) + risk-preserved=true, and contains no explicit strikes.
- Given an `exit`, When assembled, Then payload contains reason + urgency.

**Clear Outcome.** Every emitted decision is complete, builder-actionable at the intent level, and free of out-of-scope contract details.

### 3.10.3 Idempotency & duplicate-decision suppression
**Responsibility.** Avoid re-emitting the same decision while a prior one is still pending/unconfirmed.

**Behavior / Actions.**
- If a decision is already PENDING (open/adjust/exit) and unconfirmed, don't emit a duplicate each cycle; wait for confirm/timeout (3.1.1).
- Exception: EOD/stop exits may re-assert (escalate urgency) if the prior exit hasn't taken effect — safety overrides idempotency.

**Scenarios & Possibilities.**
- Builder slow to confirm an open → don't fire 10 opens; one pending.
- Exit pending but position still open as EOD passes → re-assert exit with higher urgency.

**Functional Test Case(s).**
- Given a pending `open` unconfirmed, When re-evaluated next cycle with same signal, Then no second open emitted.
- Given a pending `exit` still unfilled at EOD, When re-evaluated, Then exit re-asserted with escalated urgency.

**Clear Outcome.** No decision spam; pending actions are respected, while safety-critical exits are allowed to escalate.

---

## Suggestions (for bubble-up)

Market-condition scenarios that exceed this module's single-position, signal-plus-status view and likely need **system-wide** treatment. Flagged here, not solved here.

1. **Expiry-day mechanics.** On weekly-expiry day, theta and gamma are hyper-concentrated and pin risk dominates. Whether to trade at all, structure choice, tighter EOD, and settlement/exercise handling are system-level policies, not lifecycle-local. *Recommend a dedicated expiry-day mode.*
2. **Overnight/opening gaps.** A gap can blow through stops before this module ever evaluates (no intraday continuity to defend). Gap risk, max-loss-per-gap, and whether to enter near events are portfolio/risk concerns. *Recommend a gap-risk policy upstream.*
3. **Fast reversal mid-position.** Sudden high-velocity regime flips stress the exit-vs-adjust arbitration and may outrun evaluation cadence. *Recommend a fast-market detector feeding an "urgent exit only" mode and a faster evaluation tick.*
4. **News / event spikes (RBI, budget, global shocks).** Step-change vol and liquidity holes; theta selling is hazardous. *Recommend an event calendar + trading-halt/standdown signal system-wide.*
5. **Liquidity / spread blowouts.** Defined-risk math assumes fillable legs; wide spreads break adjust and exit economics. *Recommend a liquidity guard before any open/adjust.*
6. **Correlated NIFTY+SENSEX exposure.** If multi-position posture is ever enabled, aggregate index correlation can double real risk. *Recommend portfolio-level exposure netting in the risk module.*
7. **Builder-confirm / close-failure escalation.** Lifecycle assumes its `exit` actually closes. A failed EOD close = overnight carry = mandate violation. *Recommend a system-level close-failure alarm + forced-flat fallback.*
8. **Cooldown/cap parameter tuning by regime.** Re-entry caps and cooldowns probably should vary with realized volatility/day-type; that calibration is a system concern fed back into this module.
