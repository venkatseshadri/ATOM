# Module 10 — Feedback Gate

## 10.0 Module Overview

**Mission.** The Feedback Gate is the single chokepoint between *a proposed change to how the system trades* and *that change actually driving capital*. Nothing about the trading logic, sizing, or risk parameters reaches a live session unless it has passed through this gate. The gate is deliberately conservative: its bias is to **reject and keep the last-known-good set running**, because the cost of a bad parameter set driving real money for one day dwarfs the cost of skipping a day's "improvement."

**What it governs.** A *candidate parameter set* — the bundle of knobs that shape a session's behavior (entry thresholds, spread widths, stop/target multipliers, max positions, sizing, time windows, etc.). The gate does not invent these; it receives them with a rationale, then decides go/no-go.

**The gate is a ladder, not a switch.** A candidate climbs rungs: (1) automated EOD safety/sanity checks → (2) backtest pass on historical data → (3) human morning approval → (4) a promotion ladder (simulated → paper → live) where each rung requires N days of successful out-of-sample P&L before the next. A candidate can be parked on any rung; only a set that has cleared every rung is *frozen* and stamped for a live session.

**Core invariants (hold at all times):**
1. **Fail-closed.** Any ambiguity, missing input, expired approval, or internal error ⇒ the gate rejects and the previous frozen set continues. The gate never "defaults to the new thing."
2. **Exactly one frozen set per session.** At market open there is one, and only one, immutable parameter set stamped for the day. No mid-session swaps from this module.
3. **Every decision is auditable.** Each pass/reject, each approval, each promotion, each rollback is written to an append-only log with inputs, reasons, versions, and timestamps — reconstructable after the fact.
4. **Human-in-the-loop is mandatory for live.** Automated checks can *reject*, but only a human approval can *authorize* a candidate to advance toward real capital. Silence ≠ approval.
5. **The last-good set always exists.** There is always a valid, previously-frozen set to fall back to. The gate's "no" is never "nothing runs"; it is "yesterday's set runs."

**Inputs:** candidate parameter set + rationale; backtest/simulation result artifacts; a human approval decision (captured, timestamped, identity-bound).
**Outputs:** an approved-and-frozen, versioned parameter set for the session — OR a structured rejection carrying the gate's reasons and the fallback set that will run instead.

**Non-goals (explicitly out of scope for this module):** generating/optimizing candidates; running the live trading loop; computing P&L; the broker connection; market-data ingestion. The gate consumes results and decisions as plain data and emits a verdict.

---

## 10.1 EOD Automated Safety & Sanity Checks

Gatekeeping rung 1. Runs after the close, unattended. Cheap, deterministic, fast-failing. Purpose: catch obviously-broken or dangerous candidates *before* a human ever spends attention on them, and before any expensive backtest. Pure functions of the candidate + static config; no market dependency.

### 10.1.1 Schema & Completeness Validation

- **Responsibility** — Confirm the candidate is a well-formed, complete parameter set the rest of the gate can reason about.
- **Behavior / Actions** — Parse the candidate; assert every required knob is present; assert types/units (e.g. integer lots, percentage as fraction); reject unknown/extra keys (typo guard); confirm a rationale string is attached; confirm a parent/base version is referenced.
- **Scenarios & Possibilities** — Missing key; null where a number is required; string "0.5" vs float 0.5; an extra misspelled key (`stop_mult` vs `stoploss_mult`) that would otherwise be silently ignored; truncated/corrupt file; duplicate keys; candidate identical to current frozen set (no-op promotion).
- **Functional Test Case(s)** —
  - *Given* a candidate missing `max_positions`, *When* validated, *Then* reject with `INCOMPLETE: max_positions`.
  - *Given* a candidate with an unknown key `stop_mult`, *When* validated, *Then* reject with `UNKNOWN_KEY: stop_mult` (do not silently drop).
  - *Given* a candidate byte-identical to the current frozen set, *When* validated, *Then* flag `NO_OP_CANDIDATE` (skip downstream rungs, keep current).
- **Clear Outcome** — Only structurally complete, correctly-typed, fully-keyed candidates with rationale + parent version proceed; everything else is rejected here with a precise reason.

### 10.1.2 Bounds & Range Checks

- **Responsibility** — Confirm every parameter sits inside its hard sane envelope.
- **Behavior / Actions** — For each knob, check against a static `[min,max]` envelope (e.g. stop multiplier ∈ [0.5, 5], max positions ∈ [1, N], sizing ≤ capital cap, time window inside session hours). Separate **hard bounds** (reject) from **soft bounds** (warn + flag for human attention).
- **Scenarios & Possibilities** — Value at exact boundary (inclusive vs exclusive); negative width; zero stop (= no stop); sizing that exceeds defined-risk capital; entry-time window outside market hours or zero-width; a value just past a soft bound (should warn, not block); envelope config itself missing a knob's bound (must fail-closed, not pass-through).
- **Functional Test Case(s)** —
  - *Given* `stop_mult = 0`, *When* bounds-checked, *Then* reject `OUT_OF_BOUNDS: stop_mult=0 < min 0.5`.
  - *Given* a knob with no configured envelope, *When* checked, *Then* reject `NO_ENVELOPE: <knob>` (fail-closed, never auto-pass).
  - *Given* a value inside hard but outside soft bounds, *When* checked, *Then* pass with `SOFT_WARN` surfaced to the human rung.
- **Clear Outcome** — No candidate with a parameter outside its hard envelope can proceed; soft-bound deviations advance but are flagged for human eyes.

### 10.1.3 Degeneracy & Internal-Consistency Checks

- **Responsibility** — Catch combinations that are individually in-bounds but jointly nonsensical or self-defeating.
- **Behavior / Actions** — Cross-field rules: stop ≥ target (inverted risk/reward); entry window opens after it closes; short-strike inside long-strike (inverted spread); sizing × max_positions > capital; filter thresholds so tight they admit ~no trades; so loose they admit everything. Flag "degenerate" (would never trade or always trade) distinctly from "dangerous."
- **Scenarios & Possibilities** — Each knob legal but the *set* implies zero tradeable signals (silent no-trade day); a combo that removes the defined-risk cap; target < transaction cost (guaranteed-loss); window so narrow no entry can fire; mutually exclusive flags both set.
- **Functional Test Case(s)** —
  - *Given* `entry_start=14:30, entry_end=09:30`, *When* consistency-checked, *Then* reject `DEGENERATE_WINDOW`.
  - *Given* `target < round_trip_cost`, *When* checked, *Then* reject `NEGATIVE_EXPECTANCY_BY_CONSTRUCTION`.
  - *Given* filters implying 0 expected entries, *When* checked, *Then* flag `DEGENERATE_NO_TRADE` (reject or escalate per policy).
- **Clear Outcome** — Self-contradictory or structurally-doomed sets are stopped before backtest, with the specific contradiction named.

### 10.1.4 Delta-from-Current Magnitude Check

- **Responsibility** — Bound how far a single candidate may move from the currently-frozen set in one step.
- **Behavior / Actions** — Diff candidate vs current frozen set; compute per-knob and aggregate change magnitude; reject or escalate changes exceeding a max-step (e.g. no knob jumps >X% and no more than K knobs change at once). Encourages small, attributable steps.
- **Scenarios & Possibilities** — Huge single-day swing (overfit risk); many knobs changing at once (un-attributable if it fails); a legitimately large but human-justified change (needs override path); first-ever candidate with no current set to diff against (bootstrap case).
- **Functional Test Case(s)** —
  - *Given* sizing changes 3×, *When* delta-checked, *Then* reject `STEP_TOO_LARGE: sizing 3x > 1.5x cap` unless override flag present.
  - *Given* 9 of 12 knobs changed, *When* checked, *Then* escalate `WIDE_DIFF` to human with full diff.
  - *Given* no current frozen set (bootstrap), *When* checked, *Then* skip delta check, tag `BOOTSTRAP`.
- **Clear Outcome** — Changes are incremental and attributable, or explicitly justified/overridden; runaway single-step swings are blocked.

### 10.1.5 EOD Check Orchestration & Verdict

- **Responsibility** — Sequence the EOD checks, aggregate results, emit one rung-1 verdict.
- **Behavior / Actions** — Run 10.1.1→10.1.4 in order, short-circuiting on hard fail; collect all warnings; emit `PASS / PASS_WITH_WARN / REJECT` plus the full reason list; persist to audit (10.7). Runs unattended on an EOD trigger; must complete or self-report failure before the morning window.
- **Scenarios & Possibilities** — A check itself throws (must fail-closed → REJECT, not skip); EOD job never fires (timer dead) → no rung-1 verdict exists by morning; multiple candidates queued; partial completion then crash.
- **Functional Test Case(s)** —
  - *Given* check 10.1.2 raises an exception, *When* orchestrating, *Then* overall verdict = `REJECT: CHECK_ERROR` (never pass-through).
  - *Given* all checks pass with one soft warn, *When* orchestrating, *Then* verdict = `PASS_WITH_WARN` carrying the warn.
  - *Given* the EOD job did not run, *When* the morning rung queries it, *Then* it sees `NO_EOD_VERDICT` and treats the candidate as not-ready.
- **Clear Outcome** — Exactly one rung-1 verdict per candidate, fail-closed on any internal error, persisted and available before the human window.

---

## 10.2 Backtest / Simulation Orchestration & Pass Criteria

Gatekeeping rung 2. A candidate that survived EOD checks must demonstrate it would not have been ruinous over history *and* clears defined performance bars. The gate **consumes** backtest results as data; it owns the *criteria* and the *integrity* of the run, not the engine.

### 10.2.1 Backtest Request Construction

- **Responsibility** — Turn an EOD-passed candidate into a fully-specified, reproducible backtest request.
- **Behavior / Actions** — Bind the candidate to a fixed dataset id, date range, and engine version; pin seeds/config; stamp a request hash so the run is reproducible and cache-keyable. Choose in-sample vs out-of-sample windows per policy.
- **Scenarios & Possibilities** — Dataset version drift (yesterday's run not reproducible today); requested range includes today (look-ahead / incomplete data); holidays/half-days in range; insufficient history for the candidate's longest lookback.
- **Functional Test Case(s)** —
  - *Given* a passed candidate, *When* a request is built, *Then* it carries dataset id + date range + engine version + request hash.
  - *Given* the range includes the current (incomplete) day, *When* building, *Then* trim to last complete session and flag `RANGE_TRIMMED`.
  - *Given* history shorter than the candidate's lookback, *When* building, *Then* reject `INSUFFICIENT_HISTORY`.
- **Clear Outcome** — A deterministic, reproducible, look-ahead-free backtest request, or a rejection naming the data gap.

### 10.2.2 Backtest Execution Orchestration & Liveness

- **Responsibility** — Dispatch the run, track it to completion, handle non-completion.
- **Behavior / Actions** — Submit request; poll/await; enforce a wall-clock timeout; on timeout/crash mark `BACKTEST_FAILED`; never block the morning window indefinitely. Idempotent: a given request hash is run once and reused.
- **Scenarios & Possibilities** — Engine hangs; engine crashes mid-run; run exceeds the morning deadline; duplicate submissions; engine returns success but empty result; resource contention with live capture.
- **Functional Test Case(s)** —
  - *Given* the engine hangs past timeout, *When* orchestrating, *Then* mark `BACKTEST_TIMEOUT` and reject the candidate.
  - *Given* the same request hash submitted twice, *When* orchestrating, *Then* the second reuses the first's result (no re-run).
  - *Given* the engine returns an empty result, *When* orchestrating, *Then* treat as `BACKTEST_NO_RESULT` (reject, do not pass).
- **Clear Outcome** — Every backtest either yields a complete result artifact or a definite failure verdict — never an indefinite wait, never a silent pass.

### 10.2.3 Result Integrity & Plausibility Validation

- **Responsibility** — Verify the returned result is trustworthy before any criteria are applied.
- **Behavior / Actions** — Check the result is for *this* request hash (no stale/mismatched artifact); trade count > 0; P&L internally consistent (sum of trades = reported total); no impossible values (NaN, infinite Sharpe, win-rate >100%, returns implying look-ahead); equity curve well-formed.
- **Scenarios & Possibilities** — Stale cached result from a different candidate; suspiciously perfect curve (overfit/leakage signal); zero trades (degenerate set slipped through); P&L total ≠ trade sum (engine bug); fabricated/placeholder numbers.
- **Functional Test Case(s)** —
  - *Given* a result whose hash ≠ request hash, *When* validated, *Then* reject `RESULT_MISMATCH` (never score it).
  - *Given* win-rate = 100% over 500 trades, *When* validated, *Then* flag `IMPLAUSIBLE_RESULT` for human scrutiny.
  - *Given* reported total ≠ Σ trade P&L, *When* validated, *Then* reject `RESULT_INCONSISTENT`.
- **Clear Outcome** — Only self-consistent, plausibly-real, correctly-attributed results reach the scoring rung; too-good-to-be-true is surfaced, not trusted.

### 10.2.4 Pass-Criteria Evaluation

- **Responsibility** — Apply the explicit numeric bar a candidate must clear to advance.
- **Behavior / Actions** — Evaluate result against defined thresholds: min net expectancy > 0 after costs, max drawdown ≤ floor, min trade count for significance, no single-day catastrophic loss, and **not worse than the current frozen set's backtest on the same window** (relative bar). Emit pass/fail per criterion + overall.
- **Scenarios & Possibilities** — Passes absolute bar but underperforms incumbent (reject — don't regress); marginal pass within noise (escalate, don't auto-advance); great average but one ruinous day; too few trades to be significant; criteria thresholds themselves missing (fail-closed).
- **Functional Test Case(s)** —
  - *Given* candidate expectancy < current frozen set's on same window, *When* scored, *Then* `REJECT_REGRESSION`.
  - *Given* max DD exceeds the floor, *When* scored, *Then* `REJECT_DRAWDOWN` even if net positive.
  - *Given* a missing threshold config, *When* scored, *Then* fail-closed `NO_CRITERIA`.
- **Clear Outcome** — Candidate advances only if it clears every absolute bar *and* does not regress versus the incumbent; marginal/insignificant results are escalated, not auto-passed.

### 10.2.5 Multi-Regime / Robustness Evaluation (breadth)

- **Responsibility** — Stress the candidate beyond a single average to expose fragility.
- **Behavior / Actions** — Optionally evaluate across sub-windows (trend vs chop, high vs low VIX), worst-window performance, and simple perturbation (±small jitter on key knobs to test cliff-edge sensitivity). Reward robust, penalize knife-edge.
- **Scenarios & Possibilities** — Strong overall but collapses in one regime; performance that swings wildly under tiny knob jitter (overfit cliff); only one regime present in the data window; perturbation runs blow the time budget.
- **Functional Test Case(s)** —
  - *Given* a candidate negative in the high-VIX sub-window, *When* robustness-checked, *Then* flag `REGIME_FRAGILE` for human review.
  - *Given* a ±5% knob jitter flips expectancy negative, *When* perturbed, *Then* flag `OVERFIT_CLIFF`.
  - *Given* the data covers only one regime, *When* checked, *Then* tag `LIMITED_REGIME_COVERAGE` (lower confidence, not a hard pass).
- **Clear Outcome** — Robustness signal accompanies the verdict so fragile-but-high-average sets are caught rather than rewarded.

---

## 10.3 Human Approval Workflow

Gatekeeping rung 3. Automated rungs can only *reject*; advancing a candidate toward real capital requires an affirmative, identity-bound human decision captured before the open. Silence is rejection.

### 10.3.1 Approval Packet Presentation

- **Responsibility** — Present the human everything needed to decide, and nothing that misleads.
- **Behavior / Actions** — Assemble a packet: candidate diff vs current frozen set, the attached rationale, rung-1 verdict + warnings, rung-2 backtest summary (expectancy, DD, trade count, regime/robustness flags), current promotion rung, and a clear recommended action. Deliver via the approval channel.
- **Scenarios & Possibilities** — Packet too noisy to read at a glance; warnings buried; diff misrepresents the change; stale packet from a prior day; multiple candidates needing disambiguation; packet delivery fails.
- **Functional Test Case(s)** —
  - *Given* a candidate with a soft-bound warn and a regime-fragile flag, *When* the packet renders, *Then* both flags appear prominently, not hidden.
  - *Given* packet assembly references a backtest, *When* rendered, *Then* it shows the backtest's date range + dataset id (no ambiguity about what was tested).
  - *Given* delivery to the approval channel fails, *When* presenting, *Then* mark `APPROVAL_NOT_DELIVERED` (treated as no-approval downstream).
- **Clear Outcome** — The human sees a faithful, decision-complete, flag-forward summary tied to specific evidence — or the system knows the packet never arrived.

### 10.3.2 Approval Capture & Identity Binding

- **Responsibility** — Record an affirmative/negative decision bound to who made it, when, and on what exact candidate.
- **Behavior / Actions** — Capture decision (approve/reject/approve-with-conditions), approver identity, timestamp, and the candidate hash the decision applies to. Reject decisions that don't reference the exact presented hash (prevents approving a since-changed candidate).
- **Scenarios & Possibilities** — Approval arrives for an older candidate version; ambiguous reply ("ok"/thumbs-up — is that yes?); two approvers disagree; approval with conditions ("only at half size"); replayed/duplicated approval message; approver not authorized.
- **Functional Test Case(s)** —
  - *Given* an approval referencing hash A but the current candidate is hash B, *When* captured, *Then* reject `STALE_APPROVAL` (B remains unapproved).
  - *Given* an ambiguous reply, *When* captured, *Then* do not infer yes; mark `APPROVAL_AMBIGUOUS` (= no approval).
  - *Given* an approve-with-conditions, *When* captured, *Then* store the condition and only the conditioned set may freeze.
- **Clear Outcome** — Approval is unambiguous, attributable, and pinned to an exact candidate version; anything fuzzy counts as not-approved.

### 10.3.3 Approval Window & Timeout (fail-closed)

- **Responsibility** — Enforce that approval exists *before* the open or it doesn't count.
- **Behavior / Actions** — Define a morning approval window closing safely before market open; if no valid approval by cutoff, finalize `NO_APPROVAL` → fall back to current frozen set. Approvals after cutoff do not auto-apply to today.
- **Scenarios & Possibilities** — Approval arrives 30s before open (race against freeze); arrives after open (too late for today); human asleep / unavailable; timezone/DST drift moves the cutoff; approval given for "tomorrow."
- **Functional Test Case(s)** —
  - *Given* no approval by cutoff, *When* the window closes, *Then* `NO_APPROVAL`, current frozen set runs, event audited.
  - *Given* approval arrives after the cutoff, *When* received, *Then* it does not change today's session (may queue for next).
  - *Given* DST shift, *When* computing the window, *Then* the cutoff stays a fixed lead-time before *actual* open, not a wall-clock constant.
- **Clear Outcome** — A live session is driven by a new candidate only if a valid approval landed inside the window; otherwise the incumbent runs, deterministically.

### 10.3.4 Override & Veto Path

- **Responsibility** — Let a human deliberately override a soft-fail flag or veto an auto-passed candidate.
- **Behavior / Actions** — Allow an explicit, logged override of `SOFT_WARN`/`WIDE_DIFF`/`REGIME_FRAGILE` flags (with a reason string); allow a veto that rejects a candidate the automation would have passed. Overrides cannot bypass **hard** rejects.
- **Scenarios & Possibilities** — Human overrides a soft warn legitimately; human tries to override a hard bound (must be refused); veto of a clean candidate; override without a reason (must be refused).
- **Functional Test Case(s)** —
  - *Given* a `SOFT_WARN` and a human override with reason, *When* processed, *Then* candidate advances, override + reason audited.
  - *Given* a hard `OUT_OF_BOUNDS` reject, *When* a human attempts override, *Then* refuse `CANNOT_OVERRIDE_HARD`.
  - *Given* a veto on a passed candidate, *When* processed, *Then* candidate rejected `HUMAN_VETO`, incumbent runs.
- **Clear Outcome** — Humans can relax soft flags or block anything, with a logged reason; hard safety floors remain non-overridable.

---

## 10.4 Promotion Ladder (Simulated → Paper → Live)

Even an approved, backtested candidate is not trusted with real money on day one. It must earn live status by accruing successful *out-of-sample, forward* sessions on each rung. This sub-tree owns rung state and rung-advancement gates.

### 10.4.1 Rung State Model & Tracking

- **Responsibility** — Define the rungs and track each candidate's position and accrued evidence.
- **Behavior / Actions** — Maintain per-candidate state: rung ∈ {SIM, PAPER, LIVE}, consecutive-success count, history of daily forward results, and freeze version it derives from. Persist durably across restarts.
- **Scenarios & Possibilities** — State lost on crash (candidate "forgets" it was on paper rung); two candidates competing for promotion; candidate edited mid-ladder (must reset, not inherit progress); manual rung jump request.
- **Functional Test Case(s)** —
  - *Given* a candidate on PAPER with 1/2 successes, *When* the process restarts, *Then* state reloads as PAPER 1/2 (not reset, not lost).
  - *Given* a candidate's parameters are edited, *When* tracked, *Then* it becomes a new candidate at SIM 0/N (no inherited progress).
  - *Given* a request to skip SIM→LIVE directly, *When* evaluated, *Then* refuse unless an explicit override (10.3.4) is present.
- **Clear Outcome** — Each candidate's rung and earned evidence are durable, tamper-evident, and reset on any change to the candidate.

### 10.4.2 Forward-Result Ingestion & Attribution

- **Responsibility** — Take each day's forward (sim/paper/live) result and attribute it to the candidate that drove it.
- **Behavior / Actions** — Receive daily P&L/result tagged with the frozen version that produced it; confirm the version matches the rung's candidate; mark the day success/fail per the rung's success rule.
- **Scenarios & Possibilities** — Result tagged with a version that isn't the tracked candidate (mis-attribution); missing day (session didn't run / holiday — should not count as fail or success); partial-day result; result arrives late.
- **Functional Test Case(s)** —
  - *Given* a daily result tagged version V but the rung candidate is V', *When* ingested, *Then* reject `ATTRIBUTION_MISMATCH` (do not credit progress).
  - *Given* a market holiday (no session), *When* ingesting, *Then* the day is skipped — neither success nor fail, streak unbroken.
  - *Given* a result that never arrives, *When* the day closes, *Then* mark `RESULT_MISSING` (no auto-credit).
- **Clear Outcome** — Only correctly-attributed, real forward sessions move a candidate's success counter; gaps don't fabricate progress.

### 10.4.3 Rung-Advancement Gate

- **Responsibility** — Decide when accrued evidence is sufficient to promote to the next rung.
- **Behavior / Actions** — On each ingested success, check the rung's bar (e.g. ≥N consecutive non-losing sessions, no single-day breach); if met, advance rung (SIM→PAPER→LIVE) — *but* a promotion onto LIVE still requires a fresh human confirmation (re-enters 10.3). Reset counter to 0 on the new rung.
- **Scenarios & Possibilities** — Exactly N-1 successes then a loss (must reset, not promote); promotion to LIVE without re-approval (forbidden); promotion criteria config missing (fail-closed = no promotion); two candidates both eligible.
- **Functional Test Case(s)** —
  - *Given* N=2 and results [win, loss, win, win], *When* gated, *Then* promote only after the final two consecutive wins (the loss reset the count).
  - *Given* a candidate meets the SIM→PAPER→LIVE chain, *When* it reaches LIVE eligibility, *Then* require a fresh human approval before live capital.
  - *Given* missing promotion config, *When* gated, *Then* `NO_PROMOTION_CRITERIA`, candidate holds rung.
- **Clear Outcome** — Promotion happens only on a clean, consecutive evidence streak, and crossing into LIVE always re-touches the human gate.

### 10.4.4 Demotion / Strike Rule

- **Responsibility** — Pull a candidate *back* down the ladder when forward evidence sours.
- **Behavior / Actions** — On a failing forward session (loss beyond tolerance, or a safety breach), demote a rung or eject the candidate entirely; a LIVE candidate that breaches triggers rollback (10.6). Define strike policy (single catastrophic strike vs cumulative).
- **Scenarios & Possibilities** — One bad live day on an otherwise-promoted set; slow bleed (many small losses, no single breach); demotion oscillation (promote/demote/promote churn); a breach so severe it must skip demotion and go straight to rollback.
- **Functional Test Case(s)** —
  - *Given* a LIVE candidate has a catastrophic-loss day, *When* the strike rule fires, *Then* immediate rollback (10.6) + eject candidate.
  - *Given* a PAPER candidate underperforms its bar, *When* struck, *Then* demote PAPER→SIM, reset streak.
  - *Given* repeated promote/demote within a window, *When* detected, *Then* flag `LADDER_CHURN` for human review (stop re-promoting automatically).
- **Clear Outcome** — Souring candidates lose status promptly; severe live breaches bypass slow demotion and trigger rollback; churn is surfaced.

---

## 10.5 Freeze & Versioning of the Approved Set

Once a candidate clears its rung for the day, the gate must produce *the* immutable artifact the session runs on, and make it unambiguous which version is live.

### 10.5.1 Freeze (Immutability) Operation

- **Responsibility** — Snapshot the approved set into an immutable, content-addressed artifact for the session.
- **Behavior / Actions** — Serialize the exact approved set; compute a content hash = version id; write read-only; this artifact is what the live session reads. No edits after freeze; changes require a new candidate next cycle.
- **Scenarios & Possibilities** — Attempt to mutate a frozen set mid-session (must be impossible/refused); two freezes for the same session (must be exactly one); freeze write fails (no live artifact → fall back); hash collision (astronomically unlikely but must not silently overwrite a different set).
- **Functional Test Case(s)** —
  - *Given* a frozen set, *When* anything tries to modify it, *Then* the write is refused; the session reads the original.
  - *Given* a freeze write failure, *When* freezing, *Then* `FREEZE_FAILED` → keep prior frozen set live (10.6).
  - *Given* two freeze attempts for one session, *When* the second runs, *Then* refuse `ALREADY_FROZEN` (one set per session invariant).
- **Clear Outcome** — Each session has exactly one immutable, hash-identified parameter artifact; it cannot change once stamped.

### 10.5.2 Version Lineage & Provenance

- **Responsibility** — Record how this version was derived and on what evidence.
- **Behavior / Actions** — Store lineage: parent version, the diff applied, rationale, backtest result hash, approver id + timestamp, and rung at freeze. Enables "why is this running / what produced today's set" answers.
- **Scenarios & Possibilities** — Orphan version (no parent recorded); rationale missing; two versions claim the same parent (branch); needing to reconstruct months later for a post-mortem.
- **Functional Test Case(s)** —
  - *Given* a frozen version, *When* provenance is queried, *Then* parent + diff + backtest hash + approver are all retrievable.
  - *Given* a freeze with no recorded parent, *When* validated, *Then* reject `ORPHAN_VERSION` (every set except bootstrap has a parent).
  - *Given* a post-mortem months later, *When* tracing a version, *Then* the full lineage chain reconstructs from the log.
- **Clear Outcome** — Every frozen set carries a complete, queryable provenance chain back to its origin.

### 10.5.3 "Current Live Pointer" Management

- **Responsibility** — Maintain the single authoritative pointer to which version is live now.
- **Behavior / Actions** — Atomically swap a `current` pointer to the new frozen version at session start; the pointer move is the moment of going live; readers always resolve through the pointer.
- **Scenarios & Possibilities** — Pointer updated but session already started (mid-session swap — forbidden by 10.0 invariant 2); pointer points to a missing artifact; concurrent readers during the swap (must see old-or-new, never torn); pointer not updated (stale set runs unknowingly).
- **Functional Test Case(s)** —
  - *Given* a new frozen version, *When* the pointer swaps, *Then* it swaps atomically before open; a reader sees either the old or new whole version, never a mix.
  - *Given* the pointer targets a missing artifact, *When* resolved, *Then* error → fall back to last-good (10.6), audited.
  - *Given* the session has started, *When* a swap is attempted, *Then* refuse (no mid-session change).
- **Clear Outcome** — There is always exactly one resolvable "what's live now" answer, swapped atomically only between sessions.

---

## 10.6 Rollback to Last-Good Set

The safety net beneath every other rung. Whenever the forward path fails — bad freeze, failed approval, live breach, missing input — the gate must deterministically restore a known-good set rather than leave the session undefined.

### 10.6.1 Last-Good Set Maintenance

- **Responsibility** — Always keep a validated, previously-live "last-good" set available to revert to.
- **Behavior / Actions** — On every successful, breach-free live session, promote that session's frozen set to `last_good`; never overwrite `last_good` with an unproven candidate; keep ≥1 prior good set retained.
- **Scenarios & Possibilities** — First-ever run (no last-good yet → bootstrap conservative default); last-good itself later found flawed (need a deeper history, not just 1); last-good artifact deleted/corrupted.
- **Functional Test Case(s)** —
  - *Given* a clean live session, *When* it closes successfully, *Then* its set becomes `last_good`.
  - *Given* a session that breached, *When* it closes, *Then* `last_good` is **not** updated to that set.
  - *Given* no prior good set (bootstrap), *When* a rollback is needed, *Then* fall to a conservative built-in default, flagged `BOOTSTRAP_FALLBACK`.
- **Clear Outcome** — A trusted fallback set always exists and is only ever advanced by proven-clean sessions.

### 10.6.2 Rollback Trigger & Execution

- **Responsibility** — Detect rollback conditions and restore last-good deterministically.
- **Behavior / Actions** — Triggers: freeze failure, no-approval-by-cutoff, result-attribution failure, live strike/breach (from 10.4.4), missing/corrupt current artifact. On trigger, point `current` at `last_good`, audit the trigger + from/to versions; idempotent (re-firing changes nothing).
- **Scenarios & Possibilities** — Rollback fires mid-session vs between sessions (mid-session is an emergency — see Suggestions); last-good also unavailable (cascade → conservative default/halt); rollback fires repeatedly (flapping); rollback masks a deeper data problem.
- **Functional Test Case(s)** —
  - *Given* a `FREEZE_FAILED`, *When* rollback triggers, *Then* `current` resolves to `last_good`, event audited with from/to.
  - *Given* both current and last-good are corrupt, *When* rollback triggers, *Then* escalate `NO_SAFE_SET` (halt new entries, alarm) rather than guess.
  - *Given* the same trigger fires twice, *When* executed, *Then* the second is a no-op (idempotent).
- **Clear Outcome** — Any forward-path failure deterministically lands on a known-good set, or, if none exists, halts loudly instead of trading blind.

---

## 10.7 Audit of Gate Decisions

Every rung's verdict, every human action, every freeze, every rollback is recorded append-only. Without this the gate is unaccountable and post-mortems are impossible.

### 10.7.1 Decision Event Logging

- **Responsibility** — Append an immutable record for every gate decision and state change.
- **Behavior / Actions** — Log: rung-1 verdicts + reasons, backtest pass/fail + result hash, approval/veto + identity, promotion/demotion, freeze + version, rollback + trigger. Each entry: timestamp, candidate hash, actor (human/auto), inputs-digest, outcome, reason. Append-only, tamper-evident.
- **Scenarios & Possibilities** — Log write fails (must the decision still proceed? — fail-closed: a live-affecting decision that can't be audited should not stand); clock skew on timestamps; sensitive data in rationale; log growth.
- **Functional Test Case(s)** —
  - *Given* any reject, *When* logged, *Then* the entry carries candidate hash + reason + actor + timestamp.
  - *Given* a freeze whose audit write fails, *When* logging, *Then* treat as `AUDIT_FAILED` and do not let the unaudited set go live (fail-closed).
  - *Given* an attempt to edit a past entry, *When* it occurs, *Then* it is refused/detectable (append-only).
- **Clear Outcome** — Every live-affecting gate decision has a durable, attributable, tamper-evident record; an un-loggable decision does not take effect.

### 10.7.2 Reconstruction & Reporting

- **Responsibility** — Reconstruct, for any past session, exactly what ran and why it was allowed.
- **Behavior / Actions** — Given a date/version, replay the chain: candidate → checks → backtest → approval → freeze → (rollback?) → live result. Produce a human-readable "why did X run on day D" report; surface streak/ladder history.
- **Scenarios & Possibilities** — Gap in the log (missing rung event); needing to prove to oneself (governance) that a loss-day set was properly approved; conflicting entries; reconstructing a rollback day.
- **Functional Test Case(s)** —
  - *Given* a date with a frozen version, *When* reconstructed, *Then* the full approval+backtest+freeze chain is reproduced.
  - *Given* a rollback day, *When* reconstructed, *Then* the report shows the trigger and the from/to versions.
  - *Given* a missing rung event in the chain, *When* reconstructing, *Then* flag `AUDIT_GAP` rather than imply completeness.
- **Clear Outcome** — Any past session's authorization path is fully reconstructable, and gaps are surfaced rather than glossed.

---

## Suggestions (for bubble-up)

*Scenarios that exceed this module and need system-wide treatment — listed for later review, not resolved here.*

1. **Approval missed before open.** The fail-closed default (run last-good) is safe but means a beneficial change is silently skipped. System-wide question: should there be a secondary approver, an escalation/alert path, or a pre-approved "approve unless I object" mode? This crosses the notification/HITL surface (Telegram) and scheduling — not the gate alone.

2. **Backtest ↔ live divergence.** A set that passed backtest but consistently underperforms live signals model/data drift, leakage, or unmodeled costs (slippage, fills). The gate can *detect* divergence via 10.4.2 attribution, but diagnosing and correcting it spans the backtest engine, market-data pipeline, and execution module. Needs a cross-module divergence monitor and a policy for auto-quarantining the whole candidate family.

3. **Emergency mid-session rollback.** This module enforces "no mid-session swaps" for determinism, but a live set causing real-time harm (e.g. a runaway loss) needs an *emergency* kill/flatten path that lives in the live-risk/execution layer, not the between-sessions gate. Define the boundary: gate owns *next-session* selection; a separate circuit-breaker owns *this-second* halt. The two must share the `last_good` artifact and the audit log.

4. **Single-approver key-person risk.** Identity-bound approval (10.3.2) concentrates authority in one human. System-wide: backup approver, quorum for large deltas, and what happens during operator unavailability (vacation/illness) given the 4-year-runway, single-operator context.

5. **Bootstrap / cold-start trust.** Several leaves fall back to a "conservative built-in default" when no history/last-good exists. The definition of that default, and the criteria to graduate from bootstrap to a trusted earned set, is a system-wide policy decision, not a per-leaf one.

6. **Clock / session-calendar authority.** Multiple rungs depend on "before open," holidays, half-days, DST. A single authoritative trading-calendar/clock service should feed this gate rather than each leaf computing windows independently.
