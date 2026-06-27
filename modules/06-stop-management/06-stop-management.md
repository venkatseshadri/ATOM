# Module 6 — Stop Management

## 6.0 Module Overview

**Role.** For a single open position (one or more option legs of a defined-risk
spread), this module computes and continuously maintains the three protective
exit levels — stop-loss (SL), trailing stop-loss (TSL), and take-profit (TP) —
and raises an *exit trigger* when any level is breached. It is the position's
safety governor: once an entry exists, Module 6 is responsible for ensuring the
position cannot bleed past its risk budget and that accrued theta profit is
defended.

**Scope boundary (what this module is NOT).** It does not decide *whether* to
enter, *what* to enter, position sizing, or re-entry. It does not place orders —
it emits a *trigger* (plain data); some downstream executor acts on it. It does
not know the broader portfolio, capital, or other open positions. It governs the
one position handed to it.

**Operating model.** Event/cycle driven. Each cycle it receives the position
state (legs, entry prices, current marks, derived P&L) plus live prices, and
returns the *current* SL/TSL/TP levels and a boolean (with reason code) exit
trigger. It is deterministic and stateless-by-input where possible, but it must
persist a small amount of internal *ratchet state* (the high-water mark of the
trailed stop, TSL-armed flag, partial-exit progress) across cycles, because
ratcheting requires memory of past favourable excursion.

**First-principles design tensions this tree must resolve.**
1. *Reference frame* — should levels be expressed on **net premium of the
   spread**, on the **underlying index**, or on **greeks** (delta/gamma)? Each
   has different gap, liquidity and expiry behaviour. The module should support
   premium-based as primary (matches a theta-harvest defined-risk spread, whose
   P&L *is* premium change) with underlying-based and greek-based as alternative
   or confirming frames.
2. *Monotonicity* — a trailed stop must **never loosen**. This is the single
   most important invariant and gets its own ratchet sub-tree.
3. *Discreteness of price vs. continuity of risk* — markets gap; a stop is a
   *level*, but fills happen at *traded prices*. Breach detection must separate
   "level crossed" from "exit achievable", and handle gap-through.
4. *Time* — theta positions decay toward expiry; gamma explodes near expiry.
   Levels that are safe at 10:00 are reckless at 15:15 on expiry day. Time-based
   and expiry-aware behaviour are first-class, not afterthoughts.

**Reference-frame convention used throughout.** For a *short-premium* defined-
risk spread (the canonical theta harvest), the position is *short* net premium:
you sold the spread for a credit `C0` and profit when net premium to buy it back
falls toward 0. So:
- **Loss** grows as current net premium `P` rises above `C0`.
- **SL** = a premium ceiling `P_sl > C0` (or an underlying move, or a money
  loss). Breached when `P >= P_sl`.
- **TP** = a premium floor `P_tp < C0` (buy back cheap). Breached when
  `P <= P_tp`.
- **TSL** = a ratcheting premium ceiling that descends as `P` falls (profit
  grows), locking in gains; it can only move *down* (tighter), never up.
The tree is written in this frame but every leaf notes the long-premium / debit
mirror so the module is direction-agnostic.

### Sub-tree map
- **6.1 Initial SL Placement** — set the first protective stop at/after entry.
  - 6.1.1 Premium-based SL
  - 6.1.2 Underlying-based SL
  - 6.1.3 Greek-based SL
  - 6.1.4 Defined-risk / max-loss clamp
  - 6.1.5 SL reference-frame selection & reconciliation
- **6.2 Take-Profit (TP) Targets** — set and maintain the profit exit.
  - 6.2.1 Premium-decay TP
  - 6.2.2 Percent-of-max-profit / R-multiple TP
  - 6.2.3 Partial / scaled TP
- **6.3 Trailing Stop (TSL)** — activation, trailing, ratchet.
  - 6.3.1 TSL activation threshold
  - 6.3.2 Trailing rule & step
  - 6.3.3 Ratchet invariant (never loosen)
  - 6.3.4 TSL ⇄ SL ⇄ TP precedence
- **6.4 Per-Cycle Level Update Engine** — recompute & publish levels each cycle.
  - 6.4.1 Input validation & staleness gate
  - 6.4.2 Recompute & ratchet-merge
  - 6.4.3 Level publication / output contract
- **6.5 Breach Detection & Trigger Emission** — decide and emit exit.
  - 6.5.1 Level crossing detection
  - 6.5.2 Gap-through-stop handling
  - 6.5.3 Trigger emission, dedup & idempotency
  - 6.5.4 Partial vs full exit selection
- **6.6 Time- & Expiry-Aware Behaviour** — clock- and expiry-driven tightening.
  - 6.6.1 Hard time stop (EOD flat)
  - 6.6.2 Expiry-day gamma tightening
  - 6.6.3 Theta-budget / no-edge-left stop
- **6.7 State, Persistence & Safety** — internal memory and fail-safety.
  - 6.7.1 Ratchet/high-water-mark state store
  - 6.7.2 Fail-safe defaults & degraded mode

---

## 6.1 Initial SL Placement

Sets the *first* protective stop the instant a position becomes open, before any
profit exists to trail. This is the floor of last resort.

### 6.1.1 Premium-based SL

- **Responsibility** — Place initial SL as a net-premium ceiling derived from the
  entry credit/debit.
- **Behavior / Actions**
  - Read entry net premium `C0` (credit received for the spread).
  - Compute `P_sl = C0 + k·C0` (loss = multiple of credit) or `P_sl = C0 + A`
    (absolute premium add), per a configured rule; the actual constant is
    unknown to this module and is supplied as a parameter.
  - Cap `P_sl` at the spread's structural max premium (width of the spread) — you
    cannot lose more than max loss, so an SL beyond it is meaningless.
  - Emit `SL = P_sl` in premium frame; mark frame = PREMIUM.
- **Scenarios & Possibilities**
  - Normal: credit 100, k=1 → SL at 200 net premium (lose 1× credit).
  - Edge: `k·C0` exceeds spread width → clamp to width (max loss already capped).
  - Edge: entry credit near 0 (deep theta, almost no premium) → multiplicative SL
    collapses to ~0 width; fall back to absolute/structural SL.
  - Failure: entry price missing/zero → cannot compute; must refuse and signal
    fail-safe (see 6.7.2), never default to "no stop".
  - Long-premium mirror: SL is a premium *floor* `P_sl = D0 − k·D0`.
- **Functional Test Case(s)**
  - Given credit `C0=100`, k=1, width=300; When initial SL computed; Then
    `SL=200` (premium), below width cap.
  - Given credit `C0=100`, k=4, width=250; When computed; Then `SL` clamped to
    `250` (width), not 500.
  - Given entry price = 0/None; When computed; Then no SL emitted + fail-safe flag.
- **Clear Outcome** — A finite premium-frame SL strictly between `C0` and the
  spread's max-loss boundary, or an explicit fail-safe if inputs invalid.

### 6.1.2 Underlying-based SL

- **Responsibility** — Place initial SL as an underlying-index move from the
  level at entry.
- **Behavior / Actions**
  - Record underlying `U0` at entry.
  - Define adverse direction(s). A short straddle/strangle/iron-fly is short
    gamma → adverse move is *either* direction beyond a band: `U0 ± d`.
  - SL breached when underlying exits the band `[U0−d, U0+d]` (or the directional
    threshold for a directional spread).
  - Emit `SL` as underlying band edges; frame = UNDERLYING.
- **Scenarios & Possibilities**
  - Normal: iron fly, band ±60 pts; underlying touches edge → SL armed.
  - Edge: two-sided band — must track *both* edges and breach on nearer one.
  - Edge: underlying band still intact but premium already blew out (vol spike,
    skew) → underlying frame *under*-protects; reconcile with premium frame
    (6.1.5).
  - Failure: underlying feed stale while option feed live → underlying SL frozen;
    staleness gate (6.4.1) must catch.
- **Functional Test Case(s)**
  - Given `U0=22000`, band ±60; When underlying=22061; Then SL breach (upper).
  - Given `U0=22000`, band ±60; When underlying=21939; Then SL breach (lower).
  - Given underlying inside band but premium = 2× credit; When reconciled; Then
    premium-frame SL fires first (tighter wins, 6.1.5).
- **Clear Outcome** — Breach raised the moment underlying leaves the defined band;
  both edges honoured for two-sided positions.

### 6.1.3 Greek-based SL

- **Responsibility** — Place/adjust SL by position greeks (net delta / gamma /
  vega exposure) rather than price.
- **Behavior / Actions**
  - Compute net position delta. For a delta-neutral entry, SL arms when |net
    delta| exceeds a threshold (position has become directional → thesis broken).
  - Optionally cap vega loss: if IV expansion pushes mark-to-market loss via vega,
    treat as adverse even if underlying flat.
  - Emit greek-frame breach condition (e.g., |Δ| ≥ Δ_max).
- **Scenarios & Possibilities**
  - Normal: net delta drifts from 0 to threshold as underlying trends → arm.
  - Edge: gamma near expiry makes delta swing violently for tiny moves → greek SL
    becomes hair-trigger; must coordinate with expiry tightening (6.6.2) or be
    disabled intraday-late.
  - Edge: greeks unavailable (no live IV/greek feed) → frame unusable; degrade to
    premium frame, do not fail open.
  - Failure: stale IV → delta/gamma wrong → false arm or false safety. Treat
    greek inputs as low-trust; never the *sole* stop.
- **Functional Test Case(s)**
  - Given entry net Δ≈0, Δ_max=0.30; When net Δ=0.31; Then greek SL breach.
  - Given greek feed missing; When SL computed; Then greek frame skipped, premium
    SL used, warning flagged.
- **Clear Outcome** — Greek frame contributes a breach only when greeks are
  fresh; otherwise silently yields to price frames (never disables protection).

### 6.1.4 Defined-risk / max-loss clamp

- **Responsibility** — Guarantee no SL is ever placed beyond the spread's
  structural maximum loss.
- **Behavior / Actions**
  - Compute structural max loss = (spread width − net credit) × lot/qty for a
    credit spread; for the premium frame, the absolute ceiling is the width.
  - Clamp every candidate SL (from 6.1.1–6.1.3) so the implied loss ≤ max loss.
  - This clamp is also the *true* hard stop if all other frames fail: at max loss
    the position is fully realised-risk, exit unconditionally.
- **Scenarios & Possibilities**
  - Normal: SL well inside max loss → no change.
  - Edge: configured SL == max loss → SL coincides with structural cap; fine, but
    means the defined-risk wing *is* the stop (no early exit).
  - Edge: spread is *un*defined risk (naked leg slipped in) → clamp cannot bound
    loss; must flag as out-of-arena / refuse (module assumes defined-risk).
  - Failure: width misread (wrong strikes) → wrong clamp. Sanity-check legs.
- **Functional Test Case(s)**
  - Given width=300, credit=100 → max loss premium 300; candidate SL=350; When
    clamped; Then SL=300.
  - Given a leg with no protective wing detected; When clamp computed; Then refuse
    + flag undefined-risk.
- **Clear Outcome** — Every emitted SL implies a loss ≤ defined max loss; an
  undefined-risk position is rejected rather than mis-stopped.

### 6.1.5 SL reference-frame selection & reconciliation

- **Responsibility** — When multiple frames are active, decide which SL governs.
- **Behavior / Actions**
  - Collect candidate breach conditions from premium/underlying/greek frames.
  - Apply **tightest-wins** for *protection*: the position should exit on the
    *first* frame to signal danger (logical OR of breach conditions) — protection
    is conservative.
  - Record which frame fired for the trigger reason code.
  - Keep frames independently computed so one stale feed can't mask another.
- **Scenarios & Possibilities**
  - Normal: premium and underlying agree → either fires, same outcome.
  - Edge: vol spike — premium breaches while underlying calm → premium fires
    (correct, you're losing money).
  - Edge: flash dip in option mark (bad print) breaches premium but underlying
    fine and greeks fine → 2-of-3 confirmation could suppress a bad-tick exit;
    tension between "exit fast" and "don't exit on a bad print" — surface as a
    config choice, default to *exit* (safety) but log near-miss for review.
  - Failure: all frames stale → no frame can fire → must escalate to time/EOD and
    fail-safe, not silently hold.
- **Functional Test Case(s)**
  - Given premium-breach=true, underlying-breach=false; When reconciled (OR);
    Then exit, reason=PREMIUM.
  - Given single bad option print breaches premium for 1 cycle then reverts; When
    bad-tick filter on; Then no exit, near-miss logged. (filter is optional)
- **Clear Outcome** — Exactly one governing decision per cycle (OR of frames),
  with a frame-attributed reason code; no frame's staleness hides another's
  breach.

---

## 6.2 Take-Profit (TP) Targets

Defines the profit-side exit — capturing decayed premium before reversal risk
erodes it.

### 6.2.1 Premium-decay TP

- **Responsibility** — Set TP as a net-premium floor representing captured decay.
- **Behavior / Actions**
  - `P_tp = C0 − g·C0` (buy back after capturing fraction g of the credit) or an
    absolute floor; clamp `P_tp ≥ 0`.
  - Breach when current net premium `P ≤ P_tp`.
  - Emit `TP` in premium frame.
- **Scenarios & Possibilities**
  - Normal: credit 100, g=0.6 → TP at 40; when buy-back premium ≤ 40, exit with
    60 profit.
  - Edge: premium collapses to ~0 fast (huge decay / deep OTM) → TP hit almost
    immediately; fine, harvest done.
  - Edge: bid/ask very wide near 0 → "premium ≤ TP" on a stale/mid print but not
    actually fillable → coordinate with liquidity check (bubble-up).
  - Long-premium mirror: TP is a premium *ceiling* `D0 + g`.
- **Functional Test Case(s)**
  - Given `C0=100`, g=0.6; When `P=40`; Then TP breach.
  - Given `C0=100`, g=0.6; When `P=41`; Then no TP.
- **Clear Outcome** — TP breach exactly at/under the premium floor; profit locked
  equals captured decay.

### 6.2.2 Percent-of-max-profit / R-multiple TP

- **Responsibility** — Express TP as % of max achievable profit or as a multiple
  of risked amount (R).
- **Behavior / Actions**
  - Max profit (credit spread) = net credit. TP at `pct·max_profit` captured.
  - Or TP at `R·(risk)` where risk = distance to SL; gives a reward:risk target.
  - Convert to premium-frame floor and reuse 6.2.1 breach logic.
- **Scenarios & Possibilities**
  - Normal: 50% of max profit → standard theta-harvest take.
  - Edge: R-multiple TP with a very tight SL → tiny premium move triggers TP;
    could exit before theta meaningfully accrues — note time interplay (6.6.3).
  - Edge: pct=100% → TP only at full credit captured (premium→0), which may never
    fill before EOD; ensure EOD stop (6.6.1) still flattens.
- **Functional Test Case(s)**
  - Given max_profit=100, pct=0.5; When captured=50 (P=50); Then TP breach.
  - Given pct=1.0; When P never reaches 0 by 15:20; Then EOD stop flattens (no
    orphan).
- **Clear Outcome** — TP equivalently expressed across % / R / premium frames,
  same breach instant; unreachable TP never strands a position past EOD.

### 6.2.3 Partial / scaled TP

- **Responsibility** — Take profit on part of the position at a first target,
  hold the rest with a trailed stop.
- **Behavior / Actions**
  - Define tiers: e.g., close X% at TP1, move stop to break-even, trail remainder.
  - Track remaining quantity in ratchet state; recompute levels on the residual.
  - Emit a *partial* exit trigger (qty-scoped), not a full exit.
- **Scenarios & Possibilities**
  - Normal: TP1 hit → close half, arm TSL/break-even on rest.
  - Edge: spread legs can't be partially closed symmetrically (defined-risk spread
    must stay balanced) → partial means closing *whole spreads* from a multi-lot
    position, never a single naked leg. Enforce balance.
  - Edge: residual after partial is below min lot/qty → treat next trigger as full
    exit (can't sub-divide).
  - Failure: partial fill of the partial → reconcile actual remaining qty from
    position input next cycle, don't assume.
- **Functional Test Case(s)**
  - Given 4 lots, TP1 closes 50%; When TP1 breached; Then partial trigger qty=2
    lots (whole spreads), TSL armed on remaining 2.
  - Given 1 lot (indivisible); When partial requested; Then degrade to full exit
    at TP1.
- **Clear Outcome** — Partial triggers always close *balanced whole spreads*,
  residual levels recomputed on actual remaining qty.

---

## 6.3 Trailing Stop (TSL)

The profit-defence mechanism: once enough profit exists, convert a static SL into
a ratcheting stop that follows favourable movement and never retreats.

### 6.3.1 TSL activation threshold

- **Responsibility** — Decide when the trailing stop "arms" (before which only the
  static SL applies).
- **Behavior / Actions**
  - Arm when profit reaches an activation level — e.g., captured ≥ a fraction of
    credit, or premium fallen by ≥ X, or R-multiple ≥ some value.
  - On arm: set initial trailed stop (often break-even or a small locked profit),
    flip `tsl_armed=true` in state.
  - Before arm: TSL inactive, only 6.1 SL governs.
- **Scenarios & Possibilities**
  - Normal: profit hits activation → TSL arms at break-even.
  - Edge: profit spikes past activation *and* toward TP in one cycle → arm and
    possibly TP in same cycle; resolve precedence (6.3.4) — TP (full harvest)
    typically wins over newly-armed TSL.
  - Edge: profit reaches activation then immediately reverses within same cycle's
    data → must have armed on the favourable mark (high-water), so the locked
    stop already protects.
  - Failure: oscillation around activation threshold → arming should be sticky
    (once armed, stays armed even if profit dips) to avoid flip-flop.
- **Functional Test Case(s)**
  - Given activation=30% credit captured; When captured=30%; Then `tsl_armed=true`,
    stop set to break-even.
  - Given armed then profit falls below activation; When next cycle; Then remains
    armed (sticky), stop unchanged (ratchet).
- **Clear Outcome** — TSL arms exactly once when activation first reached, stays
  armed, and immediately establishes a non-loosening protective stop.

### 6.3.2 Trailing rule & step

- **Responsibility** — Move the armed stop in the favourable direction as profit
  improves.
- **Behavior / Actions**
  - Maintain high-water mark of favourable premium `P_min` (lowest net premium
    seen since arm, for short-premium).
  - New trailed stop candidate = `P_min + trail_gap` (stop sits a gap above the
    best premium). As `P_min` falls, candidate falls (tightens).
  - Optionally step-wise: only move stop in discrete steps to avoid churn.
  - Pass candidate to ratchet (6.3.3) before adopting.
- **Scenarios & Possibilities**
  - Normal: premium 100→70→50; trail_gap=15; stop trails 115→85→65.
  - Edge: trail_gap larger than current profit → candidate would imply loss; cap
    at break-even at minimum (locked stop never worse than entry once armed —
    config may allow small give-back, but never below activation floor).
  - Edge: percent-trail vs absolute-trail — percent tightens automatically as
    premium shrinks (good near full decay); offer both, note interplay with TP.
  - Failure: noisy ticks lower `P_min` to a bad print → high-water poisoned → stop
    over-tightens → premature exit. Guard `P_min` updates with same bad-tick
    discipline as breach (6.5).
- **Functional Test Case(s)**
  - Given armed, P_min=70, trail_gap=15; When P=50; Then P_min=50, stop=65.
  - Given P ticks to a bad print 5 then reverts to 60; When bad-tick guard on;
    Then P_min unchanged (not 5), stop stable.
- **Clear Outcome** — Stop follows genuine favourable excursion by the configured
  gap/step, immune to single bad prints.

### 6.3.3 Ratchet invariant (never loosen)

- **Responsibility** — Enforce that a trailed stop only ever tightens, never
  loosens, for the life of the position.
- **Behavior / Actions**
  - Persist `stop_locked` = tightest stop ever set.
  - Each cycle: `stop = tighten(stop_locked, candidate)` — for short-premium that
    is `min(stop_locked, candidate)` (lower premium ceiling = tighter); for
    long-premium, `max`. Adopt only if strictly tighter; else keep `stop_locked`.
  - Any computed loosening is discarded (and logged as a suppressed loosen).
- **Scenarios & Possibilities**
  - Normal: candidate tighter → adopt; candidate looser → ignore.
  - Edge: input reset / module restart mid-trade → must reload `stop_locked` from
    persistence (6.7.1), NOT recompute from scratch (which could loosen).
  - Edge: parameter change mid-trade (someone widens trail_gap) → must not loosen
    an already-locked stop; new params affect only further tightening.
  - Failure: clock skew / out-of-order cycles → a stale cycle proposes a looser
    stop → ratchet rejects it inherently (idempotent, order-independent).
- **Functional Test Case(s)**
  - Given stop_locked=65; When candidate=80; Then stop stays 65 (loosen
    suppressed).
  - Given stop_locked=65; When candidate=60; Then stop=60.
  - Given restart, persisted stop_locked=60; When recompute yields 90; Then stop
    stays 60 after reload.
- **Clear Outcome** — Stop level is monotonic-tightening across the entire
  position lifetime, restart- and order-safe.

### 6.3.4 TSL ⇄ SL ⇄ TP precedence

- **Responsibility** — When more than one level could fire in a cycle, decide the
  outcome deterministically.
- **Behavior / Actions**
  - Effective stop = tightest of {static SL (6.1), trailed stop (6.3.3)} →
    typically the trailed stop once armed (it's tighter by construction).
  - If both an adverse (stop) and favourable (TP) breach occur in the same cycle
    (gap straddling both, or wide bar): resolve by *which was hit first within
    the bar* if intrabar data exists; else apply a conservative tie-break — for a
    profit-side TP vs stop, prefer the **adverse** (stop) outcome only if the gap
    direction is adverse; otherwise prefer TP. Document the tie-break; never emit
    two conflicting triggers.
- **Scenarios & Possibilities**
  - Normal: only one of SL/TSL/TP active → trivial.
  - Edge: armed TSL is tighter than static SL → static SL is dormant but retained
    as backstop if TSL state lost.
  - Edge: same-cycle TP and stop both breached (volatile bar) → single decision
    via tie-break; flag the ambiguity for review.
  - Failure: TSL state lost but armed earlier → static SL still protects (never
    unprotected).
- **Functional Test Case(s)**
  - Given static SL=200, trailed stop=65 (armed); When P=66; Then no exit (above
    trailed? for premium ceiling, P<stop is safe); When P=65; Then exit reason
    TSL.
  - Given a bar spanning both TP and stop; When resolved; Then exactly one
    trigger emitted + ambiguity flag.
- **Clear Outcome** — One unambiguous exit decision per cycle with a deterministic
  tie-break; static SL always exists as backstop beneath the TSL.

---

## 6.4 Per-Cycle Level Update Engine

The heartbeat: each cycle ingest inputs, recompute candidate levels, ratchet-
merge, and publish.

### 6.4.1 Input validation & staleness gate

- **Responsibility** — Reject or quarantine bad/stale inputs before they move any
  level.
- **Behavior / Actions**
  - Validate presence and sanity of: position legs, entry prices, current marks,
    underlying, timestamp. Check mark timestamps fresh (within max-age).
  - On stale/missing: do NOT update high-water mark, do NOT loosen anything;
    optionally hold last good levels and increment a stale-cycle counter; if
    stale persists beyond a bound, escalate to fail-safe (6.7.2 / 6.6.1).
- **Scenarios & Possibilities**
  - Normal: fresh inputs → proceed.
  - Edge: option mark stale but underlying fresh → can still evaluate underlying-
    frame breach; partial evaluation allowed, frame-by-frame freshness.
  - Edge: negative/zero/crossed bid-ask, NaN → quarantine that field.
  - Failure: clock stuck → every cycle "fresh" by timestamp but value frozen →
    add value-change watchdog, not just timestamp.
- **Functional Test Case(s)**
  - Given mark timestamp older than max-age; When cycle runs; Then high-water not
    updated, levels held, stale-counter++.
  - Given NaN mark; When cycle runs; Then field quarantined, breach not evaluated
    on it.
- **Clear Outcome** — No level ever moves on stale/invalid data; protection holds
  at last good state; persistent staleness escalates rather than silently rides.

### 6.4.2 Recompute & ratchet-merge

- **Responsibility** — Produce this cycle's candidate levels and merge them
  through the ratchet.
- **Behavior / Actions**
  - Recompute static SL (if input-dependent), TP, and trailed-stop candidate.
  - Update high-water mark from validated favourable marks only.
  - Apply ratchet (6.3.3) to the trailed stop; TP/static SL set per rules.
  - Produce the effective level set + active frame attributions.
- **Scenarios & Possibilities**
  - Normal: small favourable move → trailed stop tightens a step.
  - Edge: first cycle post-entry → no profit yet, TSL not armed, only SL+TP.
  - Edge: simultaneous arm + tighten in one cycle (big favourable jump) → arm
    first, then tighten from arm baseline.
  - Failure: recompute throws → must not publish partial/garbage levels; keep last
    good and flag.
- **Functional Test Case(s)**
  - Given favourable jump crossing activation; When recompute; Then arm + trailed
    stop set in same cycle.
  - Given recompute exception; When caught; Then last-good levels retained, error
    flagged.
- **Clear Outcome** — Every cycle yields a consistent, ratchet-respecting level
  set, or cleanly retains the prior set on error.

### 6.4.3 Level publication / output contract

- **Responsibility** — Emit the current SL/TSL/TP and any trigger as a stable
  plain-data contract.
- **Behavior / Actions**
  - Output: `{ sl, tsl, tp, frames, tsl_armed, trigger: {fired, type, qty,
    reason}, as_of_ts, data_quality }`.
  - Always include `as_of_ts` and a data-quality/degraded flag so consumers can
    judge trust.
  - Levels expressed in their native frame plus, where possible, a money/P&L
    equivalent for human/audit readability.
- **Scenarios & Possibilities**
  - Normal: no breach → trigger.fired=false, levels current.
  - Edge: degraded mode → levels = last good, data_quality=STALE flagged.
  - Failure: never emit nulls silently for SL — absent SL must surface as a loud
    fail-safe flag, because "no stop" is the worst state.
- **Functional Test Case(s)**
  - Given a normal cycle; When published; Then all of sl/tp present, tsl present
    iff armed, trigger.fired=false.
  - Given SL uncomputable; When published; Then data_quality=FAILSAFE and trigger
    escalation per 6.7.2.
- **Clear Outcome** — A complete, timestamped, quality-tagged level packet every
  cycle; missing protection is always explicit, never silent.

---

## 6.5 Breach Detection & Trigger Emission

Turns levels + live price into an exit decision.

### 6.5.1 Level crossing detection

- **Responsibility** — Detect when the live mark has crossed a governing level.
- **Behavior / Actions**
  - For short-premium: SL/TSL breach when `P ≥ stop`; TP breach when `P ≤ tp`.
  - Use the *adverse-conservative* price for stops (e.g., the ask you'd pay to
    close, not mid) so a stop isn't dodged by an optimistic mid.
  - Compare against the *published* (ratcheted) levels, not raw candidates.
- **Scenarios & Possibilities**
  - Normal: P touches stop → breach.
  - Edge: exactly equal to level → treat `≥`/`≤` as breach (inclusive) for safety.
  - Edge: mid says safe, ask says breached → use ask for stop (conservative),
    bid for TP (don't claim profit you can't get).
  - Failure: single bad tick crosses then reverts → optional N-tick / dwell
    confirmation for *stops only if* it doesn't risk slippage; default may be
    immediate. Surface as config; log near-misses either way.
- **Functional Test Case(s)**
  - Given stop=65 (premium ceiling), ask=65; When evaluated; Then breach (>=).
  - Given mid=64 but ask=66, stop=65; When evaluated on ask; Then breach.
  - Given one-cycle bad print crossing then revert, dwell=2; When evaluated; Then
    no breach (if dwell enabled).
- **Clear Outcome** — Breach decided on the conservative executable price against
  published levels; inclusive comparison; optional dwell to filter bad ticks.

### 6.5.2 Gap-through-stop handling

- **Responsibility** — Handle the case where price jumps *past* a stop between
  cycles (no touch at the level).
- **Behavior / Actions**
  - Detect when `P` is already beyond the stop on the first observed cycle after a
    gap (open gap, halt-resume, illiquid jump).
  - Still emit the exit trigger immediately (breach is breach), but tag
    `gapped=true` and report the *gap distance* (realised loss likely worse than
    the stop level — the stop was not a guaranteed fill price).
  - Do not "wait for a pullback to the stop" — exit at market on the gap.
- **Scenarios & Possibilities**
  - Normal-gap: stop 65, first cycle after gap shows P=90 → trigger, gapped, slip
    25 over stop.
  - Edge: expiry/event gap blows past *max loss* clamp → exit immediately,
    realised loss bounded only by defined-risk wings (this is why defined-risk
    matters — bubble-up).
  - Edge: gap *through TP favourably* (premium gapped to ~0) → take the windfall,
    full harvest.
  - Failure: gap on a single bad print vs a real gap → corroborate with underlying
    move / multiple legs before declaring a catastrophic gap exit; but never
    suppress a real adverse gap waiting for confirmation it's "real".
- **Functional Test Case(s)**
  - Given stop=65; When first post-gap mark=90; Then trigger fired, gapped=true,
    slip≈25 reported.
  - Given premium gaps to 2 (TP=40); When observed; Then TP trigger, full exit.
- **Clear Outcome** — A gap beyond any level fires the appropriate trigger on the
  first cycle, flagged as gapped with slip estimate; the module never holds
  hoping price returns to the stop.

### 6.5.3 Trigger emission, dedup & idempotency

- **Responsibility** — Emit the exit trigger once and exactly once per breach,
  idempotently.
- **Behavior / Actions**
  - On breach, emit trigger with type (SL/TSL/TP/TIME/EXPIRY), qty (full/partial),
    reason, level, observed price, gapped flag.
  - Set an internal `exit_in_progress` latch; subsequent cycles for the same
    position/qty do not re-emit (avoid duplicate exits).
  - Clear latch only on confirmation that the position (or that qty) is closed
    (from next cycle's position input).
  - If position still shows open after emit + grace period → re-assert trigger
    (the executor may have missed it) — emission must be safely repeatable, but
    deduped at the consumer via an idempotency key.
- **Scenarios & Possibilities**
  - Normal: one breach → one trigger → position closes → latch clears.
  - Edge: executor slow → position still open next cycle → re-assert (not a new
    exit) with same idempotency key.
  - Edge: partial close confirmed → latch scoped to remaining qty re-opens for the
    residual's own levels.
  - Failure: trigger emitted but never acted on (downstream dead) → after grace,
    escalate severity / alarm; do not silently assume closed.
- **Functional Test Case(s)**
  - Given breach at cycle N; When cycles N..N+3 still show position open; Then
    trigger re-asserted with identical idempotency key (no duplicate exits at
    consumer).
  - Given position confirmed closed; When next cycle; Then latch cleared, no
    trigger.
- **Clear Outcome** — One logical exit per breach (idempotent, re-assertable),
  latch tied to confirmed position state; stuck-open positions escalate.

### 6.5.4 Partial vs full exit selection

- **Responsibility** — Decide whether a breach closes the whole position or a
  scoped quantity.
- **Behavior / Actions**
  - SL / TSL / EXPIRY / TIME → full exit of remaining qty (protect, don't
    half-protect on the loss side).
  - TP tiers (6.2.3) → scoped partial qty.
  - Always close *balanced whole spreads* (never leave a naked leg / break defined
    risk).
- **Scenarios & Possibilities**
  - Normal: stop breach → full exit.
  - Edge: stop breach while a partial TP exit is mid-flight → exit the *remaining*
    qty fully; reconcile against confirmed residual.
  - Edge: defined-risk balance would be broken by a partial → round to whole
    spreads or go full.
  - Failure: ambiguous remaining qty (split-brain with position feed) → prefer
    *full* exit (safer) and flag reconciliation.
- **Functional Test Case(s)**
  - Given TSL breach with 2 lots remaining; When selected; Then full exit qty=2.
  - Given TP1 tier with 4 lots; When breached; Then partial qty=2 (whole spreads).
  - Given uncertain residual qty on a stop; When selected; Then full exit + flag.
- **Clear Outcome** — Loss-side breaches always fully flat the remaining balanced
  position; only TP tiers produce partials; defined-risk balance preserved.

---

## 6.6 Time- & Expiry-Aware Behaviour

Intraday-only, theta-harvest, weekly options → the clock and expiry dominate risk
late in the day and on expiry day.

### 6.6.1 Hard time stop (EOD flat)

- **Responsibility** — Guarantee the position is flat by end of day regardless of
  P&L.
- **Behavior / Actions**
  - Maintain a hard cutoff time; when reached, emit a full-exit trigger
    (type=TIME) unconditionally, overriding SL/TSL/TP non-firing.
  - Begin a *soft* pre-cutoff window earlier where exit becomes more eager (e.g.,
    tighten stops / accept worse TP) so the hard cutoff rarely fires at market.
- **Scenarios & Possibilities**
  - Normal: position still open at cutoff → forced flat.
  - Edge: cutoff coincides with illiquid last minutes → accept slippage; being
    flat is mandatory (intraday-only). Bubble-up illiquid-exit.
  - Edge: already in exit_in_progress → don't double-fire; TIME just re-asserts.
  - Failure: clock feed wrong → use an independent wall-clock source; never miss
    EOD because one clock drifted.
- **Functional Test Case(s)**
  - Given cutoff=15:20, position open; When clock=15:20; Then full-exit trigger
    type=TIME regardless of P&L.
  - Given soft window from 15:10; When in window; Then stops tightened / exit
    eagerness raised.
- **Clear Outcome** — No position survives past the hard cutoff; soft window
  reduces forced-market exits at the bell.

### 6.6.2 Expiry-day gamma tightening

- **Responsibility** — On expiry day, tighten protection to account for exploding
  gamma (small underlying moves → large premium swings).
- **Behavior / Actions**
  - Detect expiry-day (and intraday-late on expiry).
  - Scale stops tighter and/or activation lower; widen the "danger" interpretation
    of small underlying moves; optionally cap max time-in-trade.
  - Coordinate with greek frame (6.1.3) which becomes most reactive here.
- **Scenarios & Possibilities**
  - Normal: expiry afternoon, ATM gamma high → stops tightened, quicker exits.
  - Edge: pin risk near a strike → premium oscillates around the short strike;
    avoid whipsaw churn while still protecting (step/dwell tuning).
  - Edge: a tiny underlying move = huge % premium move → underlying-frame band
    must shrink on expiry or it will under-protect badly.
  - Failure: not recognising expiry day → catastrophic under-protection; expiry
    detection must be robust (instrument expiry date vs today).
- **Functional Test Case(s)**
  - Given expiry-day flag true; When levels computed; Then stop gaps/bands tighter
    than a non-expiry day for same position.
  - Given near-strike pin oscillation; When dwell applied; Then no rapid
    flip-flop exits, protection still intact.
- **Clear Outcome** — Expiry-day positions carry measurably tighter, gamma-aware
  protection; pin whipsaw is damped without removing protection.

### 6.6.3 Theta-budget / no-edge-left stop

- **Responsibility** — Exit when the remaining theta to harvest no longer justifies
  the remaining risk (edge exhausted), independent of SL/TP.
- **Behavior / Actions**
  - Estimate remaining harvestable premium vs remaining risk/time. When captured
    decay is near-complete (premium ≈ floor) or remaining theta < a threshold,
    emit a take-it exit even if TP's strict floor not touched.
  - Or a max-time-in-trade stop: if neither SL nor TP hit within a time bound,
    flatten (don't hold dead risk).
- **Scenarios & Possibilities**
  - Normal: 90% of credit captured, little left → exit, redeploy capital
    elsewhere (decision is downstream; module just signals "edge gone").
  - Edge: premium grinds sideways all day → max-time stop flattens before EOD
    crunch.
  - Edge: distinguishing "no edge left" from "give it more time" is genuinely
    fuzzy → expose as parameters, default conservative (harvest the bird in hand).
- **Functional Test Case(s)**
  - Given captured ≥ near-full threshold; When evaluated; Then edge-exhausted exit
    even if TP floor (e.g. premium 0) not literally touched.
  - Given time-in-trade > max with no SL/TP; When evaluated; Then time-out exit.
- **Clear Outcome** — Positions don't sit holding fully-decayed or stagnant risk;
  an edge-exhausted/time-out exit fires before EOD and before risk outweighs the
  scraps of remaining theta.

---

## 6.7 State, Persistence & Safety

Internal memory and fail-safety that the rest of the tree relies on.

### 6.7.1 Ratchet/high-water-mark state store

- **Responsibility** — Persist the small per-position state needed for ratcheting
  and idempotency across cycles and restarts.
- **Behavior / Actions**
  - Persist: `stop_locked`, `P_min` (high-water), `tsl_armed`, partial-progress /
    remaining qty, `exit_in_progress` latch + idempotency key, per position id.
  - Reload on startup; if state present, trust it over recomputation (prevents
    loosening / double-exit after restart).
  - Key by a stable position identity; clear on confirmed full close.
- **Scenarios & Possibilities**
  - Normal: persist each cycle, reload on restart → seamless.
  - Edge: state file present but position no longer open (closed during downtime)
    → reconcile against position input; if gone, retire state.
  - Edge: two module instances → must avoid split ratchet; single-writer / lock
    (mirrors known single-writer discipline elsewhere).
  - Failure: corrupt state → must fail *safe*: assume protective (tightest known)
    values, never reconstruct a looser stop; alarm.
- **Functional Test Case(s)**
  - Given persisted stop_locked=60; When module restarts mid-trade; Then reload
    60, do not recompute looser.
  - Given persisted state for a now-closed position; When reconciled; Then state
    retired, no phantom levels.
- **Clear Outcome** — Ratchet and latch survive restarts intact and bias to the
  tightest/safest known state on any corruption.

### 6.7.2 Fail-safe defaults & degraded mode

- **Responsibility** — Define module behaviour when it cannot compute trustworthy
  levels.
- **Behavior / Actions**
  - If SL cannot be established (bad inputs, missing entry) → do NOT run
    unprotected: emit a loud FAILSAFE flag and, per policy, escalate toward exit
    (a position with no computable stop is a position to close, not to hold).
  - If feeds stale beyond bound → hold last-good levels briefly, then escalate to
    time/fail-safe exit rather than ride blind.
  - Always prefer *flat* over *blind* for an intraday theta position.
- **Scenarios & Possibilities**
  - Normal: transient stale → hold last-good, recover.
  - Edge: prolonged outage → escalate to flatten (can't manage what you can't
    see).
  - Edge: partial capability (underlying live, options dead) → run reduced frame,
    flag degraded, lean conservative.
  - Failure: module crash → external supervisor + persisted state (6.7.1) lets a
    fresh instance resume with the tightest stop; meanwhile position is exposed —
    so crashes must alarm immediately.
- **Functional Test Case(s)**
  - Given entry price unrecoverable; When SL requested; Then FAILSAFE flag +
    escalate-to-exit per policy (never silent no-stop).
  - Given feeds stale > bound; When evaluated; Then degraded mode → escalate to
    time-exit rather than hold blind.
- **Clear Outcome** — The module never silently runs a position with no stop;
  ambiguity resolves toward flat/conservative, loudly flagged.

---

## Suggestions (for bubble-up)

These are market-condition scenarios that exceed a single module's authority and
deserve **system-wide** treatment. Listed for later review, not handled here.

1. **Gap through stop (overnight is N/A intraday, but intraday gaps on news /
   halt-resume).** A stop is a *level*, not a fill. Realised loss can exceed the
   SL. System-wide: rely on defined-risk wings as the true backstop, model
   expected slippage in sizing, and consider event-calendar–aware entry
   suppression. Module 6 flags `gapped` + slip; the *system* must own slippage
   budgeting.

2. **Expiry-day gamma spike.** Near expiry, tiny underlying moves cause outsized
   premium swings and pin risk around the short strike. System-wide: expiry-day
   sizing/entry policy, tighter global risk, possibly avoiding new entries late
   on expiry. Module 6 tightens its own stops (6.6.2) but cannot decide whether
   to be in the trade at all.

3. **Illiquid exit (wide spreads / no bid at the bell or in a fast market).** The
   module can emit an exit trigger, but fills may be far from the level or absent.
   System-wide: execution module needs marketable-limit / slice logic, and risk
   policy should consider liquidity at entry. Module 6 must still mandate EOD-flat
   (6.6.1) even into illiquidity — being flat is non-negotiable intraday — but the
   *cost* of that is a system concern.

4. **Fast reversal / whipsaw.** A sharp move that hits a tightened TSL then
   reverses (or oscillates around a level) can cause premature/churned exits.
   System-wide: tension between protection and whipsaw is partly a strategy/entry-
   timing and re-entry-policy question. Module 6 offers dwell/step/sticky-arm
   knobs locally; the system must decide re-entry rules and whether whipsaw-prone
   regimes should be traded at all.

5. **Bad-tick vs real-move ambiguity.** Several leaves face the same dilemma: a
   single bad print can poison the high-water mark or fire a false breach;
   filtering it risks missing a real adverse gap. System-wide: a shared, trusted
   data-quality / tick-validation layer would let every module reason
   consistently instead of each inventing its own filter.

6. **Split-brain position state.** Several leaves (6.5.3/6.5.4/6.7.1) assume a
   trustworthy "what is actually open" feed. If the ledger and broker disagree,
   stop management can mis-scope exits. System-wide: a single source of truth for
   open-position state.
