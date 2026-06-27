# Module 8 — Post-Mortem

## 8.0 Module Overview

**Mandate.** After trading concludes, Module 8 studies what happened and scores it at three granularities — per individual **trade**, per **session** (day), and per **market-regime** across many days — to surface what worked and what didn't, and to attribute outcomes to causes.

**Why it exists.** A theta-harvesting, defined-risk, intraday options system lives or dies on edge that is *small, recurring, and easily masked by noise*. Single days tell you almost nothing; the signal only emerges in aggregation and disciplined attribution. Post-Mortem is the system's epistemic organ: it converts raw outcomes + decision traces into structured, comparable findings so the rest of the system (and humans) can learn what is real edge vs. what is luck, slippage, or a mislabeled regime.

**Posture (first principles).**
- **Read-only / non-authoritative.** Post-Mortem touches no live position and changes no live decision. It observes, scores, and reports. It must never have a side effect on trading state.
- **Deterministic and reproducible.** Same inputs ⇒ same findings. Re-running last week's post-mortem must reproduce last week's numbers byte-for-byte (modulo explicitly versioned metric definitions).
- **Attribution over verdict.** A P&L number is not a finding. A finding ties an outcome to a *cause dimension* (entry timing, strike/greek choice, slippage, exit selection, regime call) with enough evidence to be challenged.
- **Honesty about insufficiency.** Most days lack the sample size for confident claims. The module must label confidence and refuse to over-claim. "Not enough data" is a valid, first-class output — never a silent gap, never an invented number.
- **No P&L fabrication.** Every number traces to a record or a defined computation over records. If a field is missing, the metric is `null` with a reason, not a guess.

**Inputs (plain data, assumed available).**
- Completed **trade records**: entry/exit timestamps, legs (strike, right, qty, side), fills, fees, realized P&L, and the exit-reason tag the executor stamped.
- **Telemetry / decision traces**: time-stamped events captured during the day — what signal fired, what the regime was called, what adjustments/SLs were considered or fired, intended vs. achieved prices, greeks/IV snapshots at decision points.

**Outputs (plain data).**
- **Per-trade autopsies** — one scored record per closed trade with cause attribution.
- **Per-session reports** — daily roll-up: regime-call accuracy, adjustment timeliness, stop efficacy, P&L vs. theta captured, plus session-level flags.
- **Per-regime aggregates** — cross-day groupings: which signals read true vs. misfired, where edge concentrates, with confidence.
- **Findings** are typed, versioned, machine-readable records (stable schema) so downstream consumers and humans can diff them over time.

**What is explicitly out of scope.** No live intervention; no parameter optimization (it *measures*, it does not *tune*); no model training; no assumption about the rest of the architecture. It consumes records and emits findings.

**Sub-tree map.**
- **8.1 Ingestion & Reconciliation** — pull, validate, align trades↔traces, assess data sufficiency.
- **8.2 Per-Trade Autopsy** — entry, strike/greek, slippage, exit-fired, lifecycle scoring.
- **8.3 Per-Session Analysis** — regime-call accuracy, adjustment timeliness, stop efficacy, P&L-vs-theta, session flags.
- **8.4 Per-Regime Aggregation** — signal truth/misfire, edge concentration, confidence & sufficiency.
- **8.5 Attribution Engine** — shared cause-attribution mechanics across granularities.
- **8.6 Metric Definitions & Registry** — single source of truth for every metric, versioned.
- **8.7 Findings Structuring & Output** — schema, persistence, bubble-up of systemic flags.

---

## 8.1 Ingestion & Reconciliation

Post-Mortem can only be as honest as its inputs. This branch loads, validates, joins, and grades the data *before* any scoring runs.

### 8.1.1 Trade-record loader & schema validation
**Responsibility** — Load completed trade records and validate each against the expected schema before downstream use.
**Behavior / Actions**
- Read all trade records for the requested scope (a day, a date range).
- Validate required fields per record: trade id, entry/exit timestamps, every leg (strike, right, side, qty), fills/fees, realized P&L, exit-reason tag.
- Type-check and range-check (timestamps monotonic entry≤exit; qty>0; strikes>0; P&L numeric).
- Quarantine malformed records into a rejects list with a reason code; never silently drop.
**Scenarios & Possibilities**
- Happy path: all records well-formed.
- Missing exit timestamp on a trade the executor never closed (forced-flat at EOD not stamped) → quarantine with `MISSING_EXIT`.
- Duplicate trade id (re-emitted record) → keep first, flag duplicate.
- P&L present but legs missing → cannot autopsy; quarantine `INCOMPLETE_LEGS`.
- Empty input (no trades placed all day) → valid, produces a "no-trade session" marker downstream, not an error.
**Functional Test Case(s)**
- Given a record missing `exit_ts`; When loaded; Then it lands in rejects with code `MISSING_EXIT` and is excluded from autopsy.
- Given two records sharing a trade id; When loaded; Then the second is flagged `DUPLICATE` and only one is scored.
- Given zero trade records; When loaded; Then loader returns an empty-but-valid set and a `NO_TRADES` session marker.
**Clear Outcome** — A clean, typed set of accepted trades + an explicit rejects list with reason codes. No malformed record ever reaches scoring; emptiness is represented, not errored.

### 8.1.2 Telemetry/trace loader & normalization
**Responsibility** — Load decision/telemetry traces and normalize them into a uniform time-ordered event stream.
**Behavior / Actions**
- Read trace events for scope; normalize timestamps to one timezone/clock and sort.
- Canonicalize event types (signal-fired, regime-call, adjustment-considered, adjustment-fired, SL-considered, SL-fired, intended-price, greek/IV snapshot).
- Preserve raw payload alongside normalized fields for audit.
- Detect clock anomalies (out-of-order, gaps) and tag them.
**Scenarios & Possibilities**
- Traces use a different clock than trade fills (broker vs. local) → must reconcile offset or flag.
- Gap in traces (capture process died mid-session) → mark a coverage hole for the affected window.
- Unknown event type from a newer producer → keep raw, tag `UNKNOWN_EVENT`, don't crash.
- Burst/duplicate events → de-dupe on (ts,type,payload-hash).
**Functional Test Case(s)**
- Given trace timestamps in two timezones; When normalized; Then all events share one monotonic clock and any inferred offset is recorded.
- Given a 30-minute trace gap; When loaded; Then a `COVERAGE_HOLE` spanning that window is emitted.
- Given an unrecognized event type; When loaded; Then it is retained with `UNKNOWN_EVENT` and processing continues.
**Clear Outcome** — A single, time-ordered, canonicalized event stream with coverage holes and clock anomalies explicitly tagged.

### 8.1.3 Trade↔trace reconciliation (join)
**Responsibility** — Join each trade to the trace events that explain its lifecycle.
**Behavior / Actions**
- For each trade, gather the decision events within its [entry−ε, exit+ε] window keyed by id/leg/symbol where available, else by time+symbol proximity.
- Bind the exit to the specific trace event that triggered it (matched SL-fired / adjustment / EOD-flat).
- Flag trades with no explaining trace (orphan trade) and traces with no trade (orphan signal that never became a position).
**Scenarios & Possibilities**
- Clean 1:1 join.
- Trade exists, exit-reason tag present, but no corresponding trace exit event → reconciliation conflict (`EXIT_TAG_NO_TRACE`).
- Multiple candidate exit events in window → choose by id match first, then nearest-prior in time; record ambiguity.
- Signal fired but no trade (filtered/blocked elsewhere) → orphan-signal, useful for regime analysis, not an error.
- Time-only matching across two trades on the same symbol/strike → ambiguity flag; degrade attribution confidence.
**Functional Test Case(s)**
- Given a trade with `exit_reason=SL` and a matching SL-fired trace; When reconciled; Then exit is bound to that event with high match confidence.
- Given a trade whose exit tag has no trace event; When reconciled; Then conflict `EXIT_TAG_NO_TRACE` is raised and the trade proceeds with reduced attribution confidence.
- Given a signal-fired event with no trade; When reconciled; Then an orphan-signal record is produced (not a reject).
**Clear Outcome** — Each trade carries its explaining events with a match-confidence score; orphans (both directions) and conflicts are explicitly catalogued for downstream weighting.

### 8.1.4 Data-sufficiency assessor
**Responsibility** — Grade how much can be honestly concluded from the available, reconciled data for this scope.
**Behavior / Actions**
- Compute coverage metrics: % trades cleanly reconciled, % with greek/IV snapshots, % with intended-price events, trace coverage fraction, sample counts per regime/signal.
- Emit a confidence tier per analysis target (per-session, per-regime cell) e.g. `SUFFICIENT / THIN / INSUFFICIENT`.
- Define minimum-sample thresholds (parameterizable, owned by registry 8.6) below which claims are suppressed or labeled.
**Scenarios & Possibilities**
- A regime cell has 3 trades over the window → `INSUFFICIENT`; aggregate emitted but flagged, no edge claim.
- Greeks missing for half the trades → strike/greek attribution downgraded but slippage/exit attribution may stay `SUFFICIENT`.
- A single huge-coverage day vs. many sparse days → sufficiency is per-target, not global.
- Everything missing (capture outage) → whole scope `INSUFFICIENT`; module still emits a structured "insufficient" report.
**Functional Test Case(s)**
- Given a regime cell with sample below threshold; When assessed; Then it is tagged `INSUFFICIENT` and downstream edge claims for it are suppressed.
- Given 50% missing greeks; When assessed; Then strike/greek attribution confidence is `THIN` while exit attribution remains `SUFFICIENT`.
- Given total trace outage; When assessed; Then a complete `INSUFFICIENT` report is emitted, never a crash or a fabricated metric.
**Clear Outcome** — Every analysis target carries a confidence tier; no downstream finding can claim more certainty than the data supports.

---

## 8.2 Per-Trade Autopsy

One scored record per closed trade, decomposing its lifecycle into the decision points that determined its P&L. This is where outcomes get tied to entry, strike/greek, slippage, and which exit fired.

### 8.2.1 Entry-timing analysis
**Responsibility** — Score whether the trade was entered at a good moment relative to the signal and intraday context.
**Behavior / Actions**
- Measure latency: signal-fired → order → fill.
- Compare entry conditions vs. session benchmark (e.g., underlying level, IV, time-of-day bucket) reconstructed from traces.
- Classify entry as early/on-time/late/chased relative to the signal trigger.
- For theta strategies, note time-of-day vs. decay profile (e.g., entered too late to harvest much theta).
**Scenarios & Possibilities**
- Large signal→fill latency on a fast-moving underlying → entry-slippage risk flagged.
- Entry near EOD leaving little theta runway → low-theta-window flag.
- Signal timestamp missing (orphan-ish) → entry-timing scored `N/A` with reason.
- Entered before signal fully confirmed (jumped) vs. confirmed-late.
**Functional Test Case(s)**
- Given signal at 09:20 and fill at 09:21 in calm tape; When scored; Then entry = on-time, low latency penalty.
- Given a 12-minute signal→fill gap during a fast move; When scored; Then entry tagged `LATE`/`CHASED` with quantified adverse drift.
- Given no signal timestamp; When scored; Then entry-timing = `N/A` with `MISSING_SIGNAL_TS`.
**Clear Outcome** — Each trade has an entry-timing classification + quantified latency and adverse-drift figures, or an explicit N/A.

### 8.2.2 Strike & greek selection analysis
**Responsibility** — Score whether the chosen strikes/legs and their greeks fit the intended theta-harvest under the day's conditions.
**Behavior / Actions**
- Reconstruct each leg's greeks/IV/distance-from-spot at entry from snapshots.
- Assess delta exposure vs. intended directional-neutrality; theta captured vs. risk taken; width of the defined-risk structure vs. realized move.
- Compare strike distance vs. realized underlying range (too tight → breached; too wide → little premium).
**Scenarios & Possibilities**
- Strikes too tight → structure breached by an ordinary move; flag `TOO_TIGHT`.
- Strikes too wide → minimal premium captured for capital at risk; flag `LOW_YIELD`.
- Net delta materially off-neutral at entry → directional bet masquerading as theta; flag.
- Greeks snapshot missing → score `N/A` (depends on 8.1.4 coverage).
- IV crush vs. expansion during hold reframes whether selection was good ex-ante vs. ex-post (keep both views).
**Functional Test Case(s)**
- Given strikes whose breakeven sat inside the day's realized range; When scored; Then `TOO_TIGHT` with the breach margin quantified.
- Given entry net delta beyond a neutrality band; When scored; Then a `DIRECTIONAL_SKEW` flag with the delta value.
- Given no greek snapshot; When scored; Then strike/greek analysis = `N/A`, reason `NO_GREEKS`.
**Clear Outcome** — Per-trade verdict on strike/greek fit, separating ex-ante appropriateness from ex-post luck, or explicit N/A.

### 8.2.3 Slippage & execution-quality analysis
**Responsibility** — Quantify the gap between intended and achieved prices on entry and exit, per leg and per trade.
**Behavior / Actions**
- Compute slippage = achieved fill − intended/quoted reference at decision time, for each leg, entry and exit.
- Separate slippage from fees; express in points and currency; aggregate to per-trade.
- Attribute slippage drivers where possible: spread width, latency (link to 8.2.1), size vs. liquidity, EOD/illiquid strike.
- Detect slippage that flipped a would-be winner to a loser.
**Scenarios & Possibilities**
- Wide bid/ask on far OTM leg → large slippage on that leg only.
- Intended-price event missing → slippage = `N/A` (cannot fabricate a reference).
- Negative slippage (price improvement) → recorded, not suppressed.
- Slippage cluster across many trades same time-window → candidate systemic flag (bubble-up).
- Exit slippage on a fast SL-fired exit much worse than entry → execution-under-stress signal.
**Functional Test Case(s)**
- Given intended 10.0 and fill 10.8 on entry; When scored; Then entry slippage = 0.8 pts (×qty×multiplier in currency) attributed with spread context.
- Given no intended-price trace; When scored; Then slippage = `N/A`, reason `NO_INTENDED_PRICE`, never assumed zero.
- Given a trade green on mid-prices but red after slippage; When scored; Then a `SLIPPAGE_FLIPPED_SIGN` flag is raised.
**Clear Outcome** — Per-leg and per-trade slippage in points and currency, with drivers attributed and sign-flips flagged; missing references yield honest N/A.

### 8.2.4 Exit attribution (which exit fired)
**Responsibility** — Determine which exit mechanism actually closed the trade and whether it was the right one.
**Behavior / Actions**
- Classify the realized exit: stop-loss, profit-target, time/EOD-flat, adjustment-induced close, manual/other — bound from 8.1.3.
- Compare against counterfactual reference where reconstructable (e.g., did SL fire then price revert → premature; did EOD-flat catch a trade that should've been stopped earlier → laggy).
- Tag exit quality: timely / premature / late / correct.
**Scenarios & Possibilities**
- SL fired, price immediately reverted to target → `PREMATURE_STOP` (whipsaw) flag.
- No exit logic fired; closed only by EOD-flat at a loss → `NO_PROTECTION_FIRED` flag.
- Multiple exits contended (SL and adjustment same second) → record contention, attribute to first-effective.
- Exit tag present but unmatched in trace (from 8.1.3 conflict) → exit-quality confidence downgraded.
- Profit-target hit cleanly → `CORRECT`, baseline good case.
**Functional Test Case(s)**
- Given SL fired at 11:00 and underlying reverted to target band by 11:05; When scored; Then `PREMATURE_STOP` with the reversion magnitude.
- Given a losing trade closed only by EOD-flat with no prior SL event; When scored; Then `NO_PROTECTION_FIRED`.
- Given ambiguous exit tag (conflict from 8.1.3); When scored; Then exit attributed to best match with `LOW_CONFIDENCE`.
**Clear Outcome** — Each trade names the exit that fired and rates its appropriateness with a confidence level; pathological exits (premature/none) are flagged.

### 8.2.5 Trade-lifecycle scorecard assembler
**Responsibility** — Combine the per-trade sub-analyses into one coherent autopsy record with an overall attribution.
**Behavior / Actions**
- Assemble entry, strike/greek, slippage, exit findings into a single typed record per trade.
- Compute a primary-cause attribution for the trade's outcome (the dimension that most explains its P&L vs. expectation).
- Carry forward all confidence tags; never average away a `N/A`.
- Emit MAE/MFE (max adverse/favorable excursion) and theta-captured-vs-available for the trade where reconstructable.
**Scenarios & Possibilities**
- A winner that was actually a near-miss (deep MAE) → flag fragile-win.
- A loser whose primary cause is slippage, not strategy → attribution must not blame the signal.
- Conflicting sub-scores (good entry, bad exit) → record both; primary cause chosen by contribution magnitude, with rationale.
- All sub-analyses N/A (thin data) → emit a skeletal record marked `UNSCOREABLE`, still listed.
**Functional Test Case(s)**
- Given a loss with large measured slippage and otherwise-sound entry/strike/exit; When assembled; Then primary cause = `SLIPPAGE` with contribution share shown.
- Given a profitable trade with MAE near max loss; When assembled; Then `FRAGILE_WIN` flag set.
- Given all sub-scores N/A; When assembled; Then record marked `UNSCOREABLE`, included in output count.
**Clear Outcome** — One self-contained, typed autopsy per trade with a justified primary cause, excursion metrics, and preserved confidence — the atomic unit the session/regime layers aggregate.

---

## 8.3 Per-Session Analysis

Daily roll-up. Treats one trading day as the unit and asks: did we read the day right, act in time, protect correctly, and harvest the theta we should have?

### 8.3.1 Regime-call accuracy (intraday)
**Responsibility** — Score how well the session's declared market regime matched what the market actually did.
**Behavior / Actions**
- Extract the regime call(s) the system made during the day from traces (e.g., range-bound / trending / high-vol).
- Reconstruct the realized regime from market data (realized range, trend, realized vol vs. IV).
- Score match/mismatch over time; capture *when* a regime flipped and whether the call lagged.
- Produce a session regime-accuracy score and a mislabel timeline.
**Scenarios & Possibilities**
- Called range-bound, market trended hard → mislabel; explains theta-strategy losses.
- Regime changed midday; call updated late → partial credit + lag metric.
- No explicit regime call in traces → score `N/A`, flag missing instrumentation.
- Correct call but still lost (execution/slippage) → accuracy stays high; loss attributed elsewhere.
**Functional Test Case(s)**
- Given a "range-bound" call and a realized trending day beyond a threshold; When scored; Then session flagged `REGIME_MISLABEL` with the divergence quantified.
- Given a midday regime flip recognized 40 min late; When scored; Then a `REGIME_LAG` of ~40 min is recorded.
- Given no regime call in traces; When scored; Then accuracy = `N/A`, `MISSING_REGIME_TRACE`.
**Clear Outcome** — A per-session regime-accuracy score plus a timeline of mislabels and lags, decoupled from P&L so a right-call-wrong-result day is visible.

### 8.3.2 Adjustment timeliness
**Responsibility** — Evaluate whether intraday adjustments happened, and whether their timing helped or hurt.
**Behavior / Actions**
- Identify adjustment events (considered vs. fired) and the trigger conditions.
- Measure latency from trigger condition met → adjustment fired.
- Assess outcome delta: did the adjustment improve the position vs. the no-adjust counterfactual (reconstructable from path)?
- Flag missed adjustments (condition met, none fired) and over-adjustment (churn without benefit).
**Scenarios & Possibilities**
- Condition met early, adjustment fired late after damage done → `LATE_ADJUST`.
- Adjustment fired but worsened outcome → `HARMFUL_ADJUST`.
- No adjustment framework engaged on a day that needed it → `MISSED_ADJUST`.
- Excessive adjustments bleeding fees/slippage → `OVER_ADJUST`.
- No adjustment needed (calm range day) → neutral, baseline.
**Functional Test Case(s)**
- Given a breach condition met at 12:00 and adjustment fired 12:25; When scored; Then `LATE_ADJUST`, latency 25 min, with damage incurred in the gap.
- Given an adjustment whose post-path was worse than no-adjust; When scored; Then `HARMFUL_ADJUST`.
- Given a day where a trigger was met and nothing fired; When scored; Then `MISSED_ADJUST`.
**Clear Outcome** — Per-session adjustment scorecard: counts, latencies, benefit/harm vs. counterfactual, and timeliness flags.

### 8.3.3 Stop efficacy
**Responsibility** — Assess whether the day's stop-losses protected capital efficiently — neither too loose nor whipsaw-prone.
**Behavior / Actions**
- Aggregate per-trade exit findings (from 8.2.4) for the session.
- Compute stop metrics: % losses that hit a stop vs. ran to EOD, average loss vs. stop distance, premature-stop (whipsaw) rate, saved-loss estimate (stop vs. EOD counterfactual).
- Balance the two failure modes: stops too tight (whipsaw, many premature) vs. too loose (large EOD losses).
**Scenarios & Possibilities**
- High whipsaw rate → stops too tight for the day's noise; flag.
- Several large losses ran to EOD untouched → stops too loose / not firing.
- Stops fired correctly, capped tail → good; quantify saved loss.
- No stops configured/observed → `NO_STOPS` session flag.
**Functional Test Case(s)**
- Given 6 of 10 stop-fires reverted to profit within minutes; When scored; Then `STOPS_TOO_TIGHT` with whipsaw rate 60%.
- Given 3 large losses with no stop event; When scored; Then `STOPS_TOO_LOOSE`/`NO_PROTECTION` with estimated avoidable loss.
- Given stops that capped each loser near stop distance with low whipsaw; When scored; Then stop-efficacy = `HEALTHY`.
**Clear Outcome** — A session stop-efficacy verdict balancing whipsaw vs. tail-loss, with quantified saved/avoidable loss.

### 8.3.4 P&L vs. theta-captured
**Responsibility** — Compare realized session P&L against the theta (time-decay) that was theoretically available/intended to be harvested.
**Behavior / Actions**
- Estimate theta available across held positions over their hold windows (from greek snapshots / decay).
- Compare realized P&L to theta-captured; decompose the gap into directional/gamma moves, vega/IV change, slippage, and fees.
- Surface theta-capture efficiency = realized theta-attributable P&L ÷ available theta.
**Scenarios & Possibilities**
- Positive theta but net loss → a directional/gamma move overwhelmed decay; decomposition shows where.
- High theta-capture efficiency → strategy working as intended; baseline good.
- Theta data missing → efficiency `N/A`, fall back to raw P&L only.
- Profitable but mostly from a directional move, not theta → flag `OFF-THESIS_PROFIT` (lucky, not edge).
**Functional Test Case(s)**
- Given available theta +X and realized P&L −Y; When decomposed; Then the report attributes the −(X+Y) to named components (gamma/vega/slippage/fees).
- Given profit driven by directional move not decay; When scored; Then `OFF-THESIS_PROFIT` flag set.
- Given missing greek/theta data; When scored; Then efficiency = `N/A`, raw P&L still reported.
**Clear Outcome** — A decomposition of session P&L vs. intended theta harvest, distinguishing earned-edge from off-thesis luck, or honest N/A.

### 8.3.5 Session report assembler
**Responsibility** — Consolidate session sub-metrics into one daily report with session-level flags.
**Behavior / Actions**
- Roll up 8.3.1–8.3.4 plus per-trade primary-cause distribution for the day.
- Compute session aggregates: win rate, expectancy, gross/net, fee+slippage drag, # trades, # mislabels, # missed/late adjustments.
- Raise session flags: outlier-day, regime-mislabel-day, slippage-heavy-day, no-trade-day, insufficient-data-day.
- Attach the 8.1.4 confidence tier for the session.
**Scenarios & Possibilities**
- No-trade day → valid report stating no trades + why (no signals vs. blocked).
- Outlier P&L day (|z| beyond threshold vs. trailing distribution) → outlier flag for bubble-up.
- Insufficient data → report emitted but headline metrics labeled low-confidence.
- Normal day → clean report, no flags.
**Functional Test Case(s)**
- Given a day whose net P&L is >3σ from the trailing window; When assembled; Then `OUTLIER_DAY` flag is set and bubbled up.
- Given a no-trade day; When assembled; Then a valid `NO_TRADES` report with cause, not an error or empty file.
- Given a session graded `INSUFFICIENT`; When assembled; Then headline metrics carry a low-confidence label.
**Clear Outcome** — One typed per-session report combining regime/adjustment/stop/theta findings, aggregate stats, confidence tier, and session flags.

---

## 8.4 Per-Regime Aggregation

The cross-day learning layer. Groups many trades/sessions by regime (and signal) to find where edge actually lives and which signals tell the truth — the only layer with enough sample to claim "edge."

### 8.4.1 Regime cohorting & bucketing
**Responsibility** — Group trades and sessions into regime cohorts (and sub-cohorts) for comparable aggregation.
**Behavior / Actions**
- Bucket every reconciled trade by the *realized* regime of its window (from 8.3.1 reconstruction), not just the called regime.
- Maintain both called-regime and realized-regime buckets to enable misfire analysis.
- Support secondary dimensions: time-of-day, day-of-week, expiry-proximity, vol level.
- Keep cohort membership reproducible and auditable (which trades fell in which bucket).
**Scenarios & Possibilities**
- A trade spans a regime flip → assign by dominant regime of hold, record the straddle.
- Sparse cohort (few trades) → kept but inherits `INSUFFICIENT` from 8.1.4.
- Expiry-day trades behave differently → must be separable to avoid contaminating non-expiry stats.
- New/unseen regime label → create cohort dynamically, don't drop.
**Functional Test Case(s)**
- Given a trade whose hold straddles range→trend; When bucketed; Then assigned to the dominant-regime cohort with a `STRADDLE` note.
- Given expiry-day trades mixed in; When bucketed; Then they are isolable via the expiry-proximity dimension.
- Given a cohort with 4 trades; When bucketed; Then it carries `INSUFFICIENT` and is excluded from edge claims.
**Clear Outcome** — Reproducible, auditable cohorts on both called and realized regimes plus secondary dimensions, each carrying its sufficiency tier.

### 8.4.2 Signal truth/misfire scoring
**Responsibility** — For each signal, measure how often it read true vs. misfired, per regime.
**Behavior / Actions**
- For each signal type, join its firings (including orphan signals from 8.1.3) to subsequent realized outcomes by regime.
- Compute hit rate, expectancy-when-fired, false-positive rate, and conditional performance by regime.
- Distinguish "signal correct but trade lost on execution" from "signal wrong."
**Scenarios & Possibilities**
- A signal that works in range-bound but inverts in trending → regime-conditional edge revealed.
- A signal with high firing but near-zero expectancy → noise; candidate for retirement (report, don't act).
- Signal fired, no trade (orphan) → still counted for truth assessment.
- Too few firings → `INSUFFICIENT`, no verdict.
**Functional Test Case(s)**
- Given a signal positive in range-bound and negative in trending cohorts; When scored; Then a regime-conditional edge finding is emitted for both.
- Given a signal with many firings and ~0 expectancy; When scored; Then a `LOW_INFORMATION_SIGNAL` finding (no action, just flagged).
- Given a signal with 5 firings; When scored; Then verdict suppressed as `INSUFFICIENT`.
**Clear Outcome** — Per-signal, per-regime truth/misfire scorecard separating signal quality from execution, gated by sufficiency.

### 8.4.3 Edge-concentration mapping
**Responsibility** — Identify where in the regime/dimension space the system's profit edge concentrates (and where it bleeds).
**Behavior / Actions**
- Aggregate net expectancy across the cohort grid (regime × time-of-day × expiry-proximity × vol).
- Rank cells by expectancy and by contribution to total P&L; surface concentration (e.g., "80% of edge from range-bound mornings").
- Surface negative cells (consistent bleeders) symmetrically.
- Normalize for sample size; never rank a 3-trade cell above a 300-trade cell on raw mean.
**Scenarios & Possibilities**
- Edge concentrated in one cell → opportunity + fragility note (dependence risk).
- A cell that's positive on raw mean but `INSUFFICIENT` → shown but not ranked as edge.
- Bleeders hidden inside a net-positive regime → must be separable.
- Expiry-day cell dominating via distortion → flag for bubble-up rather than celebrate.
**Functional Test Case(s)**
- Given expectancy heavily skewed to range-bound mornings; When mapped; Then an `EDGE_CONCENTRATION` finding names the cell and its P&L share.
- Given a high-mean but tiny-sample cell; When ranked; Then it is excluded from the edge ranking and labeled `INSUFFICIENT`.
- Given a bleeder cell inside a positive regime; When mapped; Then it surfaces as a distinct negative finding.
**Clear Outcome** — A sample-aware map of where edge concentrates and where it bleeds, with concentration/fragility flags — the core strategic output.

### 8.4.4 Cross-day stability & confidence
**Responsibility** — Assess whether each regime finding is stable across days or driven by a few outlier sessions.
**Behavior / Actions**
- For each cohort finding, compute dispersion across constituent days and sensitivity to removing the top/bottom day(s).
- Attach statistical confidence (sample size, variance, jackknife sensitivity) and a stability label.
- Down-rank findings that collapse when one outlier day is removed.
**Scenarios & Possibilities**
- A "positive edge" cohort that goes negative if the single best day is removed → `UNSTABLE`, fragile.
- A consistently positive cohort across many days → `ROBUST`.
- Outlier-day contamination (links to 8.3.5 outlier flag) → recompute with/without and report both.
- Insufficient days → confidence capped low regardless of mean.
**Functional Test Case(s)**
- Given a cohort whose positive expectancy vanishes after removing the best day; When assessed; Then labeled `UNSTABLE` and down-ranked.
- Given a cohort positive across 30 of 35 days; When assessed; Then `ROBUST` with high confidence.
- Given a cohort spanning only 4 days; When assessed; Then confidence capped low irrespective of mean.
**Clear Outcome** — Every regime finding carries a stability label and confidence so downstream consumers never trust an outlier-driven mirage.

---

## 8.5 Attribution Engine

Shared cause-attribution mechanics reused by the trade, session, and regime layers — so attribution is consistent and comparable everywhere.

### 8.5.1 Cause taxonomy & contribution decomposition
**Responsibility** — Define the canonical cause dimensions and decompose any outcome's deviation-from-expectation into contributions across them.
**Behavior / Actions**
- Maintain a fixed, versioned taxonomy: entry-timing, strike/greek selection, slippage, fees, exit selection, regime call, adjustment, directional/gamma move, vega/IV.
- Given an outcome and its reconstructable path, allocate the P&L-vs-expected gap across dimensions (additive, summing to the total gap with a labeled residual).
- Produce a primary cause = largest-magnitude contributor, plus the full vector.
**Scenarios & Possibilities**
- Multiple comparable causes → report the vector, mark primary by magnitude, note closeness.
- Large unexplained residual → flag `LOW_ATTRIBUTION_CONFIDENCE` rather than force-fit to a dimension.
- Missing inputs for a dimension → that dimension's contribution is `N/A`, excluded from the sum with disclosure.
- Counterfactual not reconstructable → attribution degrades to coarse buckets, labeled.
**Functional Test Case(s)**
- Given an outcome whose gap is 70% slippage / 20% exit / 10% residual; When decomposed; Then primary = slippage and the full vector sums to the gap.
- Given a 60% residual; When decomposed; Then `LOW_ATTRIBUTION_CONFIDENCE` is flagged.
- Given missing greeks; When decomposed; Then the greek dimension = `N/A` and is excluded transparently.
**Clear Outcome** — A consistent, additive cause vector with a justified primary cause and an explicit residual — usable identically at trade/session/regime levels.

### 8.5.2 Counterfactual / benchmark reconstruction
**Responsibility** — Build the reference baselines that attribution measures against (intended price, no-adjust path, mid-price fill, alternative exit).
**Behavior / Actions**
- Reconstruct, from traces + market path, the relevant counterfactuals: fill-at-intended, hold-to-EOD, no-adjustment, stop-not-fired.
- Provide each with a coverage/confidence stamp.
- Expose baselines to 8.2–8.4 so slippage, adjustment-benefit, and stop-saved metrics share one source of truth.
**Scenarios & Possibilities**
- Path data sufficient → precise counterfactual.
- Sparse path (trace gap) → coarse/interpolated counterfactual, low confidence, clearly labeled.
- Counterfactual would require simulating beyond available data → return `UNAVAILABLE`, do not extrapolate.
- Multiple plausible baselines → expose all, let consumer pick, never silently choose one.
**Functional Test Case(s)**
- Given full intra-trade path; When reconstructing hold-to-EOD; Then an exact counterfactual P&L is produced with high confidence.
- Given a path gap over the relevant window; When reconstructing; Then a low-confidence/`UNAVAILABLE` baseline is returned, never an extrapolated number.
- Given two reasonable baselines; When requested; Then both are exposed with their assumptions.
**Clear Outcome** — Shared, confidence-stamped counterfactuals; attribution never invents a baseline and always discloses its assumptions.

---

## 8.6 Metric Definitions & Registry

The single source of truth for every metric name, formula, unit, and threshold. Prevents silent metric drift and makes findings reproducible and diffable.

### 8.6.1 Metric definition registry
**Responsibility** — Hold versioned, exact definitions (formula, unit, inputs) for every metric the module emits.
**Behavior / Actions**
- Maintain one registry entry per metric: id, formula, units, required inputs, valid range, owning sub-module.
- Version every definition; findings stamp the registry version used.
- Reject emission of any metric not in the registry (no ad-hoc metrics).
**Scenarios & Possibilities**
- A metric definition changes (e.g., theta-efficiency formula) → new version; old findings remain interpretable via their stamp.
- A sub-module tries to emit an unregistered metric → blocked, surfaced as a defect.
- Unit mismatch (points vs. currency) caught at registration.
**Functional Test Case(s)**
- Given a finding referencing metric `theta_capture_eff`; When emitted; Then it carries the registry version that defined the formula used.
- Given an attempt to emit an unregistered metric; When validated; Then emission is rejected with `UNKNOWN_METRIC`.
- Given a definition change; When re-running an old scope; Then results are reproducible under the stamped old version.
**Clear Outcome** — Every emitted number maps to a versioned, unambiguous definition; metric drift is impossible to do silently.

### 8.6.2 Threshold & parameter configuration
**Responsibility** — Centralize all tunable thresholds (sufficiency minimums, outlier σ, neutrality band, whipsaw window, latency cutoffs).
**Behavior / Actions**
- Store thresholds as named, versioned config consumed by 8.1–8.4.
- Make every flag's trigger threshold explicit and auditable; no magic numbers inline.
- Stamp findings with the threshold-set version.
**Scenarios & Possibilities**
- Tightening the outlier σ reclassifies past days → re-run reproducibly with version stamp.
- A threshold unset → safe default + a `DEFAULT_THRESHOLD_USED` notice, never an undefined comparison.
- Conflicting thresholds across consumers → registry enforces one source.
**Functional Test Case(s)**
- Given the outlier threshold changes from 3σ to 2.5σ; When re-run; Then reclassification is reproducible and version-stamped.
- Given a missing threshold; When consumed; Then a documented default applies with a `DEFAULT_THRESHOLD_USED` notice.
- Given two modules reading the same threshold; When run; Then both read the identical registry value.
**Clear Outcome** — All thresholds are named, versioned, and auditable; flag behavior is fully explained by config, not buried constants.

---

## 8.7 Findings Structuring & Output

How scored results are shaped, persisted, and made consumable downstream — including the bubble-up of systemic concerns.

### 8.7.1 Findings schema & serializer
**Responsibility** — Emit all findings in a stable, typed, machine-readable schema common across the three granularities.
**Behavior / Actions**
- Define one envelope: scope (trade/session/regime), ids, metrics (with values+units+registry version), flags, confidence tier, attribution vector, free-text rationale.
- Serialize deterministically (stable key order) so outputs diff cleanly day-over-day.
- Validate every record against the schema before write.
**Scenarios & Possibilities**
- A new flag type added → schema is extensible without breaking old consumers (additive fields).
- A record fails schema validation → blocked from output, raised as a defect, never half-written.
- Null/N/A metrics carry reason codes, not bare nulls.
**Functional Test Case(s)**
- Given trade, session, and regime findings; When serialized; Then all share the common envelope and validate against the schema.
- Given a record with a bare null metric; When validated; Then it is rejected unless the null carries a reason code.
- Given the same inputs twice; When serialized; Then byte-identical output (deterministic ordering).
**Clear Outcome** — Uniform, schema-valid, deterministically serialized findings that downstream consumers and humans can parse and diff reliably.

### 8.7.2 Persistence, versioning & reproducibility
**Responsibility** — Store findings immutably with the input snapshot and config versions needed to reproduce them.
**Behavior / Actions**
- Write findings keyed by scope + run timestamp; never overwrite a prior run (append/version).
- Record input data hashes, registry version, and threshold version with each run.
- Support re-run that reproduces a historical finding exactly from stamped versions.
**Scenarios & Possibilities**
- Re-running a past day after a metric change → produces a *new* versioned finding; the old one remains.
- Input data corrected/late-arriving → new run supersedes but does not erase prior.
- Storage write fails mid-run → atomic write or clean abort, no partial findings.
**Functional Test Case(s)**
- Given a finalized session finding; When the scope is re-run; Then a new versioned record is written and the original is preserved.
- Given stamped input-hash + config versions; When reproduced; Then the regenerated finding matches the stored one.
- Given a write failure; When persisting; Then no partial record is left (atomic semantics).
**Clear Outcome** — An immutable, reproducible, versioned findings store — any historical finding can be regenerated and audited.

### 8.7.3 Systemic-flag bubble-up emitter
**Responsibility** — Detect cross-cutting concerns that exceed this module's scope and emit them as separate, clearly-marked escalation findings.
**Behavior / Actions**
- Scan findings for systemic patterns: outlier days, repeated regime mislabels, slippage clusters, expiry-day distortions, unstable-but-tempting edge cells.
- Emit these as a distinct `BUBBLE_UP` finding type, separated from routine scores, with evidence pointers (which trades/sessions).
- Do not act on them — only surface for later human/system review.
**Scenarios & Possibilities**
- Slippage cluster recurring at a specific time-window across days → one bubble-up with linked evidence.
- A single noisy day → flagged but not escalated unless it recurs/exceeds threshold.
- Expiry-day distortions inflating a regime cell → bubble-up so the edge isn't trusted blindly.
- Nothing systemic → empty bubble-up set (valid, explicit).
**Functional Test Case(s)**
- Given slippage flags clustering in the same 10-minute window across ≥N days; When scanned; Then one `BUBBLE_UP` slippage-cluster finding with evidence is emitted.
- Given an edge cell driven by expiry-day distortion; When scanned; Then a `BUBBLE_UP` expiry-distortion flag is emitted and the cell's trust is annotated.
- Given no systemic pattern; When scanned; Then an explicit empty bubble-up set is produced.
**Clear Outcome** — Systemic concerns are separated from routine findings, evidence-linked, and escalated (not acted on) — feeding the suggestions below.

---

## Suggestions (for bubble-up)

These are scenarios that recur or cross module boundaries and deserve **system-wide** treatment beyond Module 8's per-trade/session/regime scoring. Surfaced here, separate, for later review — Post-Mortem detects and evidences them but must not act.

1. **Outlier days distort everything.** A handful of extreme-P&L days can dominate regime expectancy and win-rate stats. System-wide: a shared outlier policy (winsorize? report dual with/without? quarantine for separate study?) so every consumer treats outliers consistently, not ad hoc.

2. **Regime mislabels as a first-class failure.** Repeated "called range, market trended" patterns are likely a *signal/regime-detection* defect upstream, not a post-mortem artifact. Bubble up mislabel frequency + timing so the regime-detection module can be challenged — and so theta-strategy losses aren't mis-blamed on execution.

3. **Slippage clusters.** Recurrent slippage at specific times/strikes/sizes points to a liquidity/timing/sizing problem owned by execution, not strategy. Aggregate slippage-cluster evidence across days for an execution-quality review; flag when slippage alone flips net edge negative.

4. **Expiry-day distortions.** Expiry-day greeks, pin risk, and liquidity behave qualitatively differently and can inflate or wreck a regime cell. System-wide: decide whether expiry days are a *separate strategy regime* entirely, and ensure no edge claim silently rides on expiry-day distortion.

5. **Unstable edge mirages.** Edge cells that look great but collapse when one day is removed are a model-overfit/decision risk. Bubble up `UNSTABLE` edge findings so no sizing/parameter decision is made on a one-day fluke.

6. **Off-thesis profits.** Days/cohorts profitable from directional moves rather than theta capture mean the system is being paid for risk it didn't intend to take. Surface so the strategy isn't validated by luck — recurring off-thesis profit is a thesis-drift warning.

7. **Data-coverage gaps as a reliability metric.** Persistent missing greeks/intended-prices/trace gaps cap how much anything can be learned. Bubble up coverage trends so the *capture* layer is held accountable — post-mortem honesty is bounded by upstream instrumentation.

8. **Premature-stop / whipsaw recurrence.** A high cross-day whipsaw rate suggests stop distances are mis-tuned for prevailing noise — a parameter question spanning many sessions, not a single-trade verdict.
