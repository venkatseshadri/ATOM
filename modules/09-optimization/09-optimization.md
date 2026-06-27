# Module 9 — Optimization

## 9.0 Module Overview

**Mandate.** Given scored post-trade findings and historical market data, propose a *better* candidate strategy-parameter set for the next session. "Better" is defined primarily by **drawdown-adjusted P&L and survival** — the strategy must contain its losers and survive to compound — *not* by raw return. The output is always concrete numbers plus a defensible rationale and supporting evidence. Never a black box.

**Boundary (what this module is and is not).**
- **Is:** an offline/between-session *proposer*. It reads findings + history, searches a bounded parameter space, evaluates candidates against an explicit objective with guardrails, and emits ONE recommended candidate set (optionally with runners-up) and the reasoning/evidence behind it.
- **Is not:** the live trader, the risk gate, the executor, or the data pipeline. It does not place trades, does not decide if a proposal is *adopted* (some downstream authority owns that), and does not assume what the current live parameters are beyond what arrives as input. It treats the rest of the system as opaque.

**Core inputs (plain data).**
1. Scored post-trade findings — per-trade, per-session, and per-regime metrics already attributed by upstream (entry/exit quality, slippage, which signal fired, greek state at entry, realized P&L, MFE/MAE, holding time, regime tag).
2. Historical market data — for re-evaluating candidate parameters out of sample (price/IV/greeks series sufficient to replay the strategy's decisions).
3. The current/incumbent parameter set (so proposals are *deltas*, comparable, and bounded by change limits).

**Core outputs (plain data).**
- A **candidate parameter set**: signal weights, entry/exit thresholds, greek bands (e.g. delta/theta/vega entry windows), stop levels, profit targets, sizing/exposure rules.
- A **rationale**: why each changed parameter moved, tied to specific findings and evidence.
- **Supporting evidence**: out-of-sample metric deltas, distributions, sensitivity, and the sanity/degeneracy checks that passed.

**Design tensions this module must hold.**
- *Adapt vs. overfit* — react to recent findings without chasing noise from a tiny sample or one regime.
- *Improve vs. survive* — never trade a small expected-return gain for a fat-tail blowup; the objective is asymmetric toward survival.
- *Search power vs. explainability* — whatever the search method, the output must reduce to concrete numbers and a human-auditable why.
- *Stability vs. responsiveness* — large parameter jumps are suspicious; favor bounded, defensible deltas unless evidence is overwhelming.

**Sub-tree map.**
- **9.1** Objective function, guardrails & survival metrics
- **9.2** Search method (statistical search + AI/LLM trace reasoning, ensembled)
- **9.3** Parameter space definition & bounds
- **9.4** Overfitting controls (validation discipline)
- **9.5** Sanity & degeneracy checks
- **9.6** Candidate-set assembly & selection
- **9.7** Rationale generation & evidence packaging

---

## 9.1 Objective Function, Guardrails & Survival Metrics

Defines *what "better" means* in numbers, before any search runs. Everything downstream optimizes this.

### 9.1.1 Drawdown-adjusted return objective

#### 9.1.1.1 Primary scalar objective
- **Responsibility** — Reduce a candidate's out-of-sample performance to a single drawdown-aware score for ranking.
- **Behavior / Actions** — Compute a survival-weighted metric (e.g. a Calmar/MAR-style ratio = annualized/period P&L ÷ max drawdown, and a downside-deviation ratio à la Sortino) on the candidate's replayed equity curve. Combine into one scalar with documented weights that *penalize drawdown more than they reward upside* (asymmetric). Score must be reproducible from stored inputs.
- **Scenarios & Possibilities** — High raw P&L with a deep single drawdown ranks *below* a lower-P&L, shallow-drawdown candidate (intended). Zero or near-zero drawdown over the window (too few trades) → ratio explodes to +∞; must be capped/flagged, not trusted. Negative-P&L candidate → score must stay monotonic (less negative is better) and never divide-by-zero. Equity curve with one giant winner masking a bad process.
- **Functional Test Case(s)** — Given two candidates A (P&L 100, maxDD 50) and B (P&L 70, maxDD 10); When scored; Then B outranks A. Given a candidate with maxDD = 0 from only 2 trades; When scored; Then return a capped score flagged `insufficient_drawdown_sample`.
- **Clear Outcome** — A finite, ordered, drawdown-penalizing score per candidate; degenerate denominators are capped and flagged, never silently passed.

#### 9.1.1.2 Drawdown decomposition (path, not just depth)
- **Responsibility** — Characterize *how* drawdown accrues, not only its max depth.
- **Behavior / Actions** — Compute max drawdown, average drawdown, drawdown duration (time underwater), recovery factor, and longest losing streak. Surface these as objective components / tie-breakers so a "death by a thousand cuts" curve is distinguishable from one clean dip.
- **Scenarios & Possibilities** — Two curves share identical max DD but one stays underwater 5× longer (worse for survival/compounding). Slow grind-down with no single bad day evades a max-DD-only metric. Very short window → duration metrics unreliable.
- **Functional Test Case(s)** — Given two curves with equal max DD but underwater durations 3 days vs 15 days; When ranked on the duration tie-breaker; Then the 3-day curve ranks higher.
- **Clear Outcome** — Drawdown is described as a vector (depth, duration, frequency, recovery), feeding both ranking and rationale.

### 9.1.2 Survival & ruin guardrails (hard constraints)

#### 9.1.2.1 Ruin / capital-preservation constraints
- **Responsibility** — Reject any candidate whose tail risk threatens survival, regardless of its score.
- **Behavior / Actions** — Enforce hard caps as *constraints, not objectives*: max single-trade loss, max per-session loss, max peak-to-trough drawdown, and an estimated probability-of-ruin / risk-of-large-loss bound from the replayed loss distribution. A candidate that violates any cap is disqualified before ranking.
- **Scenarios & Possibilities** — A candidate posts the best score but its worst replayed session breaches the session-loss cap → disqualified. Defined-risk spreads cap theoretical loss, but slippage/gap on exit can exceed it → use realized worst-case, not theoretical. Fat-tail event absent from the eval window → constraint should lean on assumed-worst, not observed-worst alone.
- **Functional Test Case(s)** — Given a top-scored candidate with one replayed session loss exceeding the per-session cap; When filtered; Then it is disqualified with reason `session_loss_cap_breach` and never appears in the candidate set.
- **Clear Outcome** — No proposal can violate a survival constraint; violations are hard-filtered and logged, not weighted away.

#### 9.1.2.2 Exposure & concurrency guardrails
- **Responsibility** — Keep proposed sizing/exposure within survivable bounds for an intraday theta book.
- **Behavior / Actions** — Bound proposed position size, total greek exposure (net delta/vega), and concurrent-position count so a single adverse move can't exceed the capital-preservation limits. Cross-check sizing proposals against worst-case greek excursions in the history.
- **Scenarios & Possibilities** — Optimizer learns "bigger size = more theta = more P&L" and pushes sizing up unboundedly → exposure cap must stop it. A vega-heavy proposal looks great in a calm window but is ruinous on an IV spike. Intraday-flat assumption violated by a halted/illiquid exit.
- **Functional Test Case(s)** — Given a candidate proposing size above the exposure cap; When validated; Then size is rejected (or clamped with a flag) and the candidate cannot rely on the over-cap exposure for its score.
- **Clear Outcome** — Exposure/concurrency stays within survivable limits; runaway-sizing proposals are blocked.

### 9.1.3 Objective configuration & change-cost penalty

#### 9.1.3.1 Objective weight configuration
- **Responsibility** — Make the objective's weights explicit, versioned, and externally set — never hard-coded magic.
- **Behavior / Actions** — Read objective weights (drawdown penalty strength, return weight, duration tie-break weight) from a versioned config. Stamp the active objective version onto every candidate so results are comparable across runs.
- **Scenarios & Possibilities** — Weights change between sessions → old and new scores not comparable unless version-stamped. Missing/corrupt config → fail closed to a conservative default, flag loudly.
- **Functional Test Case(s)** — Given objective config v2 with a higher drawdown penalty; When the same candidate is scored under v1 and v2; Then scores differ and each output carries its objective version.
- **Clear Outcome** — Objective is transparent, versioned, and reproducible; missing config fails safe.

#### 9.1.3.2 Change-cost / churn penalty
- **Responsibility** — Penalize large deviations from the incumbent parameter set absent strong evidence.
- **Behavior / Actions** — Add a regularization term proportional to the magnitude of parameter change vs. the incumbent, so the optimizer prefers the smallest defensible delta. Penalty scales down only when out-of-sample evidence for the change is strong.
- **Scenarios & Possibilities** — Two candidates score equally; one is a tiny tweak, one is a wholesale rewrite → prefer the tweak. Incumbent unknown/absent on first run → penalty disabled, flagged as `no_incumbent_baseline`. Genuine regime break needs a big move → strong evidence must be able to overcome the penalty.
- **Functional Test Case(s)** — Given two equal-scoring candidates, deltas of 5% and 60% from incumbent; When ranked; Then the 5% candidate wins on the churn penalty.
- **Clear Outcome** — Proposals bias toward minimal, defensible change; big jumps require proportionally bigger evidence.

---

## 9.2 Search Method

How the parameter space is explored. Module deliberately runs **two complementary engines** — statistical/numeric search and AI/LLM reasoning over traces — and reconciles them, rather than betting on one.

### 9.2.1 Statistical / numeric search engine

#### 9.2.1.1 Grid / coarse sweep
- **Responsibility** — Exhaustively map a coarse grid over the bounded space for a baseline landscape.
- **Behavior / Actions** — Evaluate the objective on a discretized grid within 9.3 bounds; record the full response surface (not just the best point) to expose flat regions, cliffs, and multi-modality.
- **Scenarios & Possibilities** — Space too large → combinatorial blow-up; must cap grid resolution or fall back to sampling. Best grid point sits on a cliff edge (fragile) → flag for plateau preference in 9.5. Coarse grid misses a narrow optimum.
- **Functional Test Case(s)** — Given a 3-parameter space with set bounds; When the coarse sweep runs; Then it returns a complete scored surface and identifies the top plateau region, not just one point.
- **Clear Outcome** — A reproducible response surface with best *regions* (preferred) over best *points*.

#### 9.2.1.2 Bayesian / adaptive search
- **Responsibility** — Spend evaluation budget efficiently on promising regions the grid flagged.
- **Behavior / Actions** — Run a sample-efficient optimizer (Bayesian/TPE-style) seeded by the coarse surface; refine around promising plateaus; stop on convergence or budget exhaustion.
- **Scenarios & Possibilities** — Optimizer collapses onto a noisy spike (overfit) → must respect plateau/robustness preference, not raw peak. Non-stationary objective (regime mix in eval set) misleads the surrogate. Budget exhausted before convergence → return best-so-far, flagged not-converged.
- **Functional Test Case(s)** — Given the coarse surface as a prior and a fixed eval budget; When adaptive search runs; Then it converges to a robust region or returns best-so-far flagged `not_converged`.
- **Clear Outcome** — Efficient refinement toward robust optima, with convergence state reported.

#### 9.2.1.3 Sensitivity & local-robustness probe
- **Responsibility** — Measure how the objective moves under small perturbations of each proposed parameter.
- **Behavior / Actions** — Jitter each parameter ±a small step around a candidate; record score gradient/variance. Prefer candidates sitting in low-sensitivity plateaus over sharp peaks.
- **Scenarios & Possibilities** — A peak with steep walls = overfit/fragile → demote. Flat-everywhere parameter = inert/degenerate (see 9.5). Interaction effects: two params jointly sensitive though each looks flat alone.
- **Functional Test Case(s)** — Given a candidate on a sharp peak and one on a plateau with equal center scores; When perturbed ±step; Then the plateau candidate shows lower score variance and is preferred.
- **Clear Outcome** — Each proposed parameter carries a sensitivity rating; fragile peaks are demoted.

### 9.2.2 AI/LLM reasoning over traces

#### 9.2.2.1 Finding-driven hypothesis generation
- **Responsibility** — Read scored post-trade findings and propose *targeted* parameter hypotheses with causal stories.
- **Behavior / Actions** — Summarize recurring failure/success patterns in the findings (e.g. "losers cluster when entered at low theta in high-IV regime"), and emit concrete, bounded parameter-change hypotheses with a stated mechanism. Hypotheses are *candidates for evaluation*, never adopted on the LLM's say-so.
- **Scenarios & Possibilities** — LLM hallucinates a pattern not in the data → every hypothesis must be falsifiable and is gated by 9.2.1/9.4 numeric eval. LLM proposes an out-of-bounds value → clamped by 9.3. Confident narrative on a 3-trade sample → must be tagged low-confidence. Contradicts the numeric search → reconcile in 9.2.3.
- **Functional Test Case(s)** — Given findings where losers concentrate in one greek/regime cell; When hypotheses are generated; Then output is a bounded, numeric parameter change with a cited mechanism and a confidence tag — and it is sent to numeric eval, not directly to output.
- **Clear Outcome** — Human-readable, falsifiable, in-bounds hypotheses tied to evidence, always subject to numeric verification.

#### 9.2.2.2 Trace narrative & counterfactual reasoning
- **Responsibility** — Explain *why* specific trades went wrong and what parameter would plausibly have helped — without inventing data.
- **Behavior / Actions** — Walk representative win/loss traces; produce counterfactuals grounded only in supplied data ("had the stop been X tighter, this −Y trade caps at −Z" — verifiable on the replay). Flag anything not checkable against history.
- **Scenarios & Possibilities** — Counterfactual not testable on available data → label `unverifiable`, exclude from evidence. Cherry-picking one trade to justify a broad change → require the pattern to hold across the trace set. Fabricated P&L numbers — explicitly forbidden; every number traces to a record.
- **Functional Test Case(s)** — Given a losing trace and a proposed tighter stop; When the counterfactual is computed; Then the improved P&L is recomputed from the actual replay and matches, or the claim is dropped as `unverifiable`.
- **Clear Outcome** — Counterfactuals are data-grounded and reproducible; unverifiable claims are excluded, never fabricated.

### 9.2.3 Ensemble reconciliation

- **Responsibility** — Combine numeric-search winners and LLM hypotheses into one vetted candidate pool.
- **Behavior / Actions** — Run LLM hypotheses through the same numeric objective + guardrails as grid/Bayesian points; keep only those that survive. Where the two engines agree, raise confidence; where they conflict, prefer the numerically validated result and record the disagreement for the rationale.
- **Scenarios & Possibilities** — LLM hypothesis fails numeric eval → dropped (LLM never overrides numbers). Numeric optimum has no causal story → keep but flag `no_mechanism`, lower confidence. Both engines empty (no improvement found) → return incumbent (see 9.6.3).
- **Functional Test Case(s)** — Given an LLM hypothesis that worsens the objective on replay; When reconciled; Then it is excluded and the conflict is noted in the evidence log.
- **Clear Outcome** — A single candidate pool where every member passed numeric evaluation; agreements boost and conflicts are recorded, never hidden.

---

## 9.3 Parameter Space Definition & Bounds

Defines exactly which knobs are tunable, their legal ranges, and their relationships — the search cannot leave this box.

### 9.3.1 Parameter inventory & typing

- **Responsibility** — Enumerate every tunable parameter with type, unit, and semantics.
- **Behavior / Actions** — Maintain an explicit registry: signal weights (continuous), entry/exit thresholds (continuous), greek bands (interval: delta/theta/vega entry windows), stop levels & profit targets (continuous/ratio), sizing/exposure rules (continuous/integer lots). Each carries type, unit, direction-of-effect note, and a "tunable / frozen" flag.
- **Scenarios & Possibilities** — A parameter exists live but is absent from the registry → it can't be tuned and must be flagged as a gap, not silently ignored. Integer-only params (lot counts) proposed as fractions → must round/clamp to legal increments. A frozen/safety parameter must never be proposed for change.
- **Functional Test Case(s)** — Given the registry and an incoming incumbent set; When cross-checked; Then any incumbent parameter missing from the registry is reported as `untunable_unknown_param`, and frozen params are excluded from the search.
- **Clear Outcome** — A complete, typed, authoritative list of what may move and what may not.

### 9.3.2 Bounds & legality constraints

- **Responsibility** — Attach hard min/max and step to each parameter and enforce inter-parameter legality.
- **Behavior / Actions** — Define per-parameter bounds (domain-sane: e.g. stop ≥ a floor, greek bands within tradable ranges) and relational constraints (e.g. profit target > breakeven distance; lower band < upper band; weights normalize). Clamp or reject any proposal outside the box.
- **Scenarios & Possibilities** — A proposal sets stop wider than max-loss cap → reject (conflicts with 9.1.2). Greek lower band ≥ upper band → illegal interval, reject. Weights that don't sum/normalize → renormalize or reject. Bounds too tight → search has no room, flag `bounds_saturated` if optimum sits on a bound.
- **Functional Test Case(s)** — Given a candidate with greek lower band above its upper band; When legality is checked; Then it is rejected `illegal_band_order`. Given an optimum landing exactly on a max bound; When assembled; Then output flags `at_bound` for review.
- **Clear Outcome** — Every emitted parameter is in-range, legal, and inter-consistent; boundary-hugging optima are flagged.

### 9.3.3 Regime-conditioning of the space

- **Responsibility** — Allow parameter sets/bounds to be conditioned on market regime where justified.
- **Behavior / Actions** — Optionally partition the space by regime tag (e.g. high-IV vs low-IV, trending vs range) so proposals can differ by regime — but only when per-regime sample size supports it; otherwise collapse to a single shared set.
- **Scenarios & Possibilities** — Per-regime tuning with 4 trades in a regime → too thin, must fall back to pooled. Regime tags missing/unreliable in findings → disable conditioning, flag. Over-segmentation = overfitting (bubble-up).
- **Functional Test Case(s)** — Given a regime with sample size below the minimum threshold; When conditioning is attempted; Then that regime reuses the pooled parameter set and is flagged `insufficient_regime_sample`.
- **Clear Outcome** — Regime-specific parameters appear only when statistically supportable; otherwise a single robust set is used.

---

## 9.4 Overfitting Controls

Discipline that keeps proposals generalizing beyond the data they were tuned on. The module's credibility lives here.

### 9.4.1 Train/validation/holdout partitioning

- **Responsibility** — Never score a candidate on the same data used to find it.
- **Behavior / Actions** — Split history into search (train), tuning (validation), and an untouched out-of-sample holdout. Optima are selected on validation; the *final* reported metric is the holdout metric. Use time-ordered (walk-forward) splits — never random shuffling of a time series.
- **Scenarios & Possibilities** — Leakage from look-ahead (future data in features) → must enforce strict time ordering. Holdout too small to be meaningful → flag low-confidence. Reusing holdout across many runs erodes it (multiple-comparisons) → rotate/refresh holdout, track usage.
- **Functional Test Case(s)** — Given a candidate selected on train+validation; When reported; Then its headline metric is computed on the previously untouched holdout, and any look-ahead in features fails a leakage assertion.
- **Clear Outcome** — Reported performance is genuinely out-of-sample and leak-free.

### 9.4.2 Walk-forward / cross-validation robustness

- **Responsibility** — Confirm a candidate works across multiple, sequential time windows, not one lucky slice.
- **Behavior / Actions** — Run walk-forward (rolling-origin) evaluation; require the candidate to beat the incumbent on a majority of folds and to show stable parameters across folds. Report per-fold dispersion.
- **Scenarios & Possibilities** — Candidate wins overall but loses most folds (one fold carried it) → reject as unstable. Parameters that swing wildly fold-to-fold → unstable, demote. Too few folds (short history) → widen windows or flag low-confidence.
- **Functional Test Case(s)** — Given a candidate that beats incumbent on aggregate but on only 2 of 6 folds; When robustness-tested; Then it is rejected `fold_instability` despite the aggregate win.
- **Clear Outcome** — Only candidates with consistent cross-fold edge and stable parameters survive.

### 9.4.3 Multiple-comparisons / data-snooping control

- **Responsibility** — Discount apparent edge that arises from trying many parameter combinations.
- **Behavior / Actions** — Track number of configurations evaluated; apply a deflation/penalty (e.g. deflated performance metric, higher significance bar with more trials) so a "winner" found among thousands must clear a higher hurdle. Prefer effect sizes that survive the deflation.
- **Scenarios & Possibilities** — 10,000 grid points → best one is likely noise; deflation must shrink its apparent edge. Tiny eval sample + huge search = near-guaranteed spurious winner → may return "no confident improvement." Reporting raw best-of-N without deflation = the classic overfit trap.
- **Functional Test Case(s)** — Given 5,000 evaluated configs and a marginal best; When the deflation penalty is applied; Then the marginal edge fails the significance bar and is not proposed.
- **Clear Outcome** — Edge claims are corrected for search breadth; marginal best-of-N winners are not shipped.

### 9.4.4 Sample-sufficiency gating

- **Responsibility** — Block tuning when the underlying data is too thin to support it.
- **Behavior / Actions** — Compute trade/session counts per parameter and per regime; require minimum sample thresholds before allowing a change to that parameter. Below threshold → freeze the parameter at incumbent and emit a `need_more_data` note.
- **Scenarios & Possibilities** — A whole new session with 6 trades → almost nothing is tunable, output is mostly "hold." A rarely-firing signal has 2 observations → its weight stays frozen. Pressure to "do something" each session → module must be willing to recommend no change.
- **Functional Test Case(s)** — Given a parameter informed by fewer than the minimum required trades; When tuning is attempted; Then that parameter is held at incumbent with reason `insufficient_sample`.
- **Clear Outcome** — Parameters move only when backed by enough data; thin evidence yields explicit "hold."

---

## 9.5 Sanity & Degeneracy Checks

Catches proposals that are technically optimal but practically broken, inert, or absurd — the last gate before assembly.

### 9.5.1 Degenerate-solution detection

- **Responsibility** — Reject "optimal" sets that win by not trading or by exploiting artifacts.
- **Behavior / Actions** — Detect candidates that achieve high scores via near-zero activity (e.g. thresholds so tight almost no trade fires), via a single lucky outlier, or via parameters that make a signal inert. Require a minimum trade count and contribution-spread before a candidate is valid.
- **Scenarios & Possibilities** — "Best" candidate takes 1 trade in the whole window (huge ratio, meaningless). Thresholds set beyond any observed value → zero trades, perfect drawdown, garbage. A weight driven to zero silently disables a signal — sometimes valid, must be surfaced not hidden. Score driven by one 10σ winner.
- **Functional Test Case(s)** — Given a top candidate that fires on a single trade across the window; When degeneracy-checked; Then it is rejected `degenerate_low_activity` and excluded from assembly.
- **Clear Outcome** — High scores from inactivity, single outliers, or inert signals are caught and rejected with reasons.

### 9.5.2 Economic / domain plausibility checks

- **Responsibility** — Verify each proposed value is sensible for an intraday theta options book.
- **Behavior / Actions** — Assert domain rules: profit target and stop are economically ordered, greek bands sit in tradable/liquid ranges, sizing respects liquidity, exit logic keeps the book flat by EOD. Flag values that are mathematically allowed but economically implausible.
- **Scenarios & Possibilities** — A stop tighter than typical bid-ask noise → guaranteed whipsaw. Entry greek band in an illiquid wing → unfillable. Profit target below round-trip cost → structurally unprofitable. Sizing implies more contracts than the strike's liquidity.
- **Functional Test Case(s)** — Given a proposed stop smaller than the instrument's typical spread; When plausibility-checked; Then it is flagged `stop_below_noise_floor` and demoted/blocked.
- **Clear Outcome** — Every proposed value passes economic common-sense for this market, or is flagged.

### 9.5.3 Consistency & reproducibility check

- **Responsibility** — Ensure the same inputs reproduce the same proposal and the candidate is internally consistent.
- **Behavior / Actions** — Re-run scoring on the chosen candidate from stored inputs and confirm the metric matches; verify the candidate's parameters are mutually consistent (no contradictions vs 9.3 relations) and reproduce deterministically (seeded search, fixed data snapshot).
- **Scenarios & Possibilities** — Non-deterministic search yields a different "best" each run → must seed and snapshot. LLM stochasticity changes hypotheses run-to-run → numeric gate makes the *final* output stable even if hypotheses vary. Stored evidence doesn't reproduce the headline number → block the proposal.
- **Functional Test Case(s)** — Given the chosen candidate and its stored input snapshot; When re-scored; Then the metric reproduces within tolerance, else the candidate is blocked `irreproducible`.
- **Clear Outcome** — Proposals are deterministic, reproducible from stored evidence, and internally consistent.

---

## 9.6 Candidate-Set Assembly & Selection

Turns the vetted pool into the actual deliverable: one recommended set (plus optional alternatives), all guardrail-clean.

### 9.6.1 Pareto ranking & alternatives
- **Responsibility** — Rank survivors on the objective while exposing return-vs-drawdown trade-offs.
- **Behavior / Actions** — Build a Pareto front over (drawdown-adjusted score, robustness, churn). Select the recommended candidate as the survival-leaning point on the front; optionally emit 1–2 alternatives (e.g. a more conservative and a more aggressive option) clearly labeled.
- **Scenarios & Possibilities** — Front collapses to one point → emit just it. Recommended and alternatives are near-identical → say so, don't fabricate diversity. User/downstream wants the conservative end by default → survival-leaning selection rule.
- **Functional Test Case(s)** — Given a Pareto front of 4 survivors; When selecting; Then the recommended pick is the drawdown-favoring point and up to two labeled alternatives accompany it.
- **Clear Outcome** — One clearly-recommended set plus optional, labeled trade-off alternatives.

### 9.6.2 Final guardrail re-validation
- **Responsibility** — Re-assert all hard constraints on the *final assembled* set before emit.
- **Behavior / Actions** — Re-run 9.1.2 survival constraints, 9.3.2 legality, and 9.5 sanity on the exact set to be output (not just on intermediate points), since assembly/rounding can shift values. Block emit on any failure.
- **Scenarios & Possibilities** — Rounding lots to integers pushes exposure over cap → caught here. Combining individually-legal params yields an illegal relation → caught. Everything passed earlier but final compose breaks one rule.
- **Functional Test Case(s)** — Given a final set whose integer-rounded sizing exceeds the exposure cap; When re-validated; Then emit is blocked and the set is sent back for clamping.
- **Clear Outcome** — The emitted set has freshly re-passed every hard guardrail in its final form.

### 9.6.3 "No-change" / fail-safe path
- **Responsibility** — Recommend keeping the incumbent when no candidate is confidently better.
- **Behavior / Actions** — If no survivor beats the incumbent after deflation/robustness, or data is too thin (9.4.4), output the incumbent unchanged with a `hold` recommendation and reasons. Never invent a change to look productive.
- **Scenarios & Possibilities** — Flat session, no signal → hold. All candidates fail guardrails → hold + escalate. Incumbent itself unknown → emit conservative defaults flagged, not a confident change.
- **Functional Test Case(s)** — Given no candidate clears the significance bar over incumbent; When assembling; Then output = incumbent with recommendation `hold` and a stated reason.
- **Clear Outcome** — A defensible "hold" is a valid, first-class output; no change is manufactured.

---

## 9.7 Rationale Generation & Evidence Packaging

Makes the proposal auditable: concrete numbers + why + proof. A black box is a failure condition.

### 9.7.1 Per-parameter rationale
- **Responsibility** — Explain each changed parameter: old → new, why, and which findings drove it.
- **Behavior / Actions** — For every delta, state the incumbent value, proposed value, direction, the finding(s)/mechanism behind it, and the expected effect on drawdown-adjusted P&L. Unchanged-but-considered parameters get a one-line "held because…".
- **Scenarios & Possibilities** — A change with a numeric edge but no causal story → label `empirical_only, no_mechanism`. A held parameter someone expected to move → explain the hold. Rationale must never contradict the evidence pack.
- **Functional Test Case(s)** — Given a stop tightened from X to Y; When rationale is generated; Then it cites the loss-cluster finding, the counterfactual P&L improvement, and the expected drawdown effect, with old/new values explicit.
- **Clear Outcome** — Every delta (and notable hold) has a traceable, finding-linked reason.

### 9.7.2 Evidence pack (metrics, distributions, sensitivity)
- **Responsibility** — Attach the quantitative proof behind the recommendation.
- **Behavior / Actions** — Bundle: holdout/walk-forward metric deltas vs incumbent, equity-curve and drawdown comparison, P&L distribution (not just mean), per-fold dispersion, sensitivity ratings, and the list of guardrail/sanity checks passed. All numbers trace to stored records.
- **Scenarios & Possibilities** — Mean improves but distribution fattens left tail → must be visible, not hidden by a single number. Evidence references data no longer retrievable → mark `evidence_incomplete`. Fabricated/placeholder numbers — forbidden; every figure recomputable.
- **Functional Test Case(s)** — Given a recommended set; When the evidence pack is built; Then it contains out-of-sample metric deltas, the loss distribution, sensitivity, and the passed-check list, each reproducible from stored inputs.
- **Clear Outcome** — A self-contained, reproducible evidence bundle accompanies every proposal.

### 9.7.3 Confidence & caveats statement
- **Responsibility** — State how much to trust the proposal and under what conditions it could fail.
- **Behavior / Actions** — Emit a confidence level driven by sample size, fold consistency, search breadth (deflation), and regime coverage; list explicit caveats (e.g. "validated only in low-IV regime", "thin sample on signal X", "at parameter bound"). Confidence must move with the evidence, not sentiment.
- **Scenarios & Possibilities** — High score on tiny sample → confidence must be *low* despite the score. Only one regime in the eval window → caveat about regime fragility. Confidence inflation = a serious failure mode; keep it data-driven.
- **Functional Test Case(s)** — Given a proposal validated on a single regime with a small sample; When the confidence statement is built; Then confidence is `low` with caveats `single_regime` and `small_sample`.
- **Clear Outcome** — Each proposal carries an honest, evidence-driven confidence and named failure conditions.

---

## Suggestions (for bubble-up)

These recur across the module and likely deserve **system-wide** treatment, not just a Module 9 local fix. Flagged separately for later review.

1. **Regime overfitting is a cross-module risk.** Module 9 can only flag/penalize tuning to one regime; it cannot *know* the true forward regime mix. The system should own a canonical regime taxonomy + a regime-detector so optimization, risk, and execution all agree on "what regime are we in" and so proposals can be stress-tested against under-represented regimes.

2. **Parameter drift / change governance.** Even with a churn penalty, session-by-session proposals can slowly walk parameters far from any validated baseline (ratchet drift). The system needs a longitudinal guardrail: track cumulative parameter movement over N sessions, periodically re-anchor to a from-scratch full-history re-validation, and require escalation when drift exceeds a band.

3. **Small-sample instability is structural, not local.** Intraday weekly-options sessions yield few trades; many sessions will be sub-threshold for confident tuning. The system should decide a policy: accumulate across sessions before tuning, weight by recency, or run on pooled history — rather than each module independently coping with thin data.

4. **Holdout exhaustion / multiple-runs decay.** Repeatedly testing on the same holdout across many sessions silently erodes its out-of-sample value (data-snooping at the *process* level). A system-level data-management policy for rotating/refreshing holdout periods and tracking holdout reuse is warranted.

5. **Objective-of-objectives consistency.** Module 9 optimizes a drawdown-adjusted survival objective, but the live risk module and sizing module enforce their own limits. If those objectives disagree, proposals can be perpetually clamped or contradicted. The system should reconcile a single, shared definition of "survival" and risk appetite that all modules inherit.

6. **Fabrication / hallucination defense as a system property.** This module forbids invented numbers and gates all LLM output through numeric verification, but the same discipline (every figure traces to a stored record; LLM never overrides numbers) should be a system-wide invariant with automated provenance checks, not re-implemented per module.
