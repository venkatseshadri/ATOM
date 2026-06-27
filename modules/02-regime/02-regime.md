# Module 2 — Regime

> Discovery document. Breadth-first decomposition of the Regime module for later grilling.
> Scope: this module ONLY. The broader strategy, parameter values, and sibling modules are out of scope and must not be assumed.

---

## 2.0 Module Overview

**Whole-module responsibility.** Read a market snapshot and emit a single classification of the *current* market state with a calibrated confidence, derived from a forced consensus across **seven independent indicator families** so that no one signal — and no directional bias — can dominate the label.

**Inputs (plain data).**
- Underlying spot (last price; intraday tick or last-bar close).
- Option chain per strike: call/put price, IV, greeks (delta/gamma/theta/vega), OI, volume.
- Multi-timeframe OHLC candles (e.g. several short and medium intraday timeframes), with volume.
- Implicit context the snapshot carries: timestamp, symbol, session phase (open / mid / close), and whatever staleness markers the feed provides.

**Outputs (plain data).**
- `regime_label` ∈ a fixed enumeration — at minimum: `TREND_UP`, `TREND_DOWN`, `SIDEWAYS`, `REVERSAL`, plus `UNKNOWN`/`WARMUP` for the degraded path.
- `confidence` ∈ [0,1].
- A structured rationale block: per-family votes, timeframe breakdown, dissent flags, and any penalties applied. (Rationale is data, not a side effect — downstream and audit both consume it.)

**Hard design constraints.**
- Seven families vote independently; aggregation must be domination-resistant.
- The module *classifies*; it does not decide trades, size, or direction-to-take. It must not smuggle a trading opinion into the label.
- Deterministic and reproducible for a given snapshot (no hidden state beyond the explicit hysteresis/history buffer it owns).

**Sub-module map.**
- 2.1 Data Intake & Warm-up
- 2.2 Per-Family Evaluation (the seven families + a common vote contract)
- 2.3 Multi-Timeframe Aggregation
- 2.4 Consensus / Voting
- 2.5 Confidence Scoring
- 2.6 Conflict / Disagreement Handling
- 2.7 Regime-Change Detection
- 2.8 Hysteresis / Flip-flap Avoidance
- 2.9 Output Assembly

---

## 2.1 Data Intake & Warm-up

Owns the boundary between raw snapshot and the clean, sufficient, fresh data the rest of the module assumes.

### 2.1.1 Snapshot Schema Validation
- **Responsibility** — Confirm the incoming snapshot has the structural shape every downstream node depends on.
- **Behavior / Actions** — Check presence and types of spot, option-chain rows, and per-timeframe candle arrays; verify greeks/IV/OI fields exist per strike; reject NaN/negative-price/zero-where-impossible values; tag which families' inputs are present vs missing.
- **Scenarios & Possibilities** — Missing option chain entirely (feed partial); some strikes missing greeks; spot present but candles empty; duplicate strikes; negative IV; price = 0 from an lp-less tick; extra/unknown fields (forward-compat). Failure: malformed JSON / wrong nesting.
- **Functional Test Case(s)** — *Given* a snapshot with no `option_chain` key, *When* validated, *Then* it flags `options_sentiment` inputs unavailable and proceeds (does not crash) with that family marked absent. *Given* a strike row with IV = -3, *When* validated, *Then* that row is dropped and logged.
- **Clear Outcome** — A normalized, type-clean snapshot plus a per-family availability map; never raises on bad input — always returns a structured validity verdict.

### 2.1.2 Data Sufficiency / Warm-up Gate
- **Responsibility** — Decide whether enough history exists to classify at all.
- **Behavior / Actions** — Compare available candle counts per timeframe against the lookback each family needs (longest window across requested indicators); compute a sufficiency ratio; if below threshold, force `WARMUP`/`UNKNOWN` label with confidence 0 and short-circuit heavy computation.
- **Scenarios & Possibilities** — First minutes after market open (few bars); a newly added timeframe with no history; partial sufficiency (short TFs ready, long TF not); gap after a feed outage mid-session leaving a hole; sufficient bars but all identical (flat dead feed).
- **Functional Test Case(s)** — *Given* only 3 bars when the trend family needs 20, *When* gated, *Then* output is `WARMUP`, confidence 0, reason="insufficient_history". *Given* all timeframes meet minimum, *When* gated, *Then* it passes through with `sufficient=true`.
- **Clear Outcome** — A binary sufficiency verdict per timeframe and overall; classification only proceeds when the overall gate passes (or proceeds in a documented degraded mode for partially-ready TFs).

### 2.1.3 Staleness & Clock Check
- **Responsibility** — Ensure the snapshot reflects *now*, not a frozen or future feed.
- **Behavior / Actions** — Compare snapshot timestamp to current clock and to the previous snapshot; detect non-advancing timestamps, large gaps, and out-of-order arrivals; flag stale and apply downstream confidence penalty rather than silently classifying.
- **Scenarios & Possibilities** — Feed frozen (same ts repeated) but module keeps ticking; clock skew between feed and host; pre-open / post-close snapshot arriving; daylight/holiday session where "now" has no live market; one timeframe stale while others fresh.
- **Functional Test Case(s)** — *Given* a snapshot ts identical to the prior 5 snapshots, *When* checked, *Then* `stale=true` and a staleness flag is attached for 2.5.2. *Given* ts 90s behind a 1-min cadence feed, *When* checked, *Then* flagged stale.
- **Clear Outcome** — Every snapshot carries an explicit freshness verdict; stale data never produces a high-confidence label.

### 2.1.4 Per-Timeframe Candle Assembly
- **Responsibility** — Present each family with a clean, aligned per-timeframe candle series.
- **Behavior / Actions** — Group/normalize candles by timeframe; sort by time; de-duplicate; repair or flag obviously broken bars (low=0, high<low, close outside [low,high]); expose a uniform candle accessor so families don't each re-parse.
- **Scenarios & Possibilities** — `low=0` from lp-less ticks poisoning min-based indicators (known prior failure class); a bar with volume but zero range (auction); overlapping/duplicated bars from two writers; missing intermediate bar (hole); timeframe with only the forming (incomplete) current bar.
- **Functional Test Case(s)** — *Given* a series where one bar has low=0 while OHLC otherwise valid, *When* assembled, *Then* the bar is flagged/repaired and the anomaly count surfaced. *Given* duplicate bars for the same minute, *When* assembled, *Then* one is kept deterministically.
- **Clear Outcome** — Each timeframe yields a monotonic, gap-annotated, anomaly-flagged candle series; no family sees a raw broken bar without a flag.

---

## 2.2 Per-Family Evaluation

The seven independent voters plus a shared vote contract. Each family consumes the cleaned per-timeframe data and emits a vote in a **common normalized form**: a soft distribution over the regime labels (or up/down/neutral lean) with a per-family confidence and a "ready" flag. No family may emit a trade decision.

### 2.2.1 Trend Family
- **Responsibility** — Judge directional persistence vs flatness of price.
- **Behavior / Actions** — Compute trend descriptors (e.g. moving-average slope/stack, higher-highs/lower-lows, directional-movement style measures, regression slope) per timeframe; map to a lean toward `TREND_UP`/`TREND_DOWN`/`SIDEWAYS`.
- **Scenarios & Possibilities** — Strong clean trend; gentle drift below noise floor; whipsaw (slope sign flips bar to bar); post-gap dislocation skewing MAs; flat MAs in a tight range; conflicting slope across timeframes.
- **Functional Test Case(s)** — *Given* monotonically rising closes across the lookback, *When* evaluated, *Then* vote leans `TREND_UP` with high family confidence. *Given* closes oscillating within a narrow band, *When* evaluated, *Then* vote leans `SIDEWAYS`.
- **Clear Outcome** — A normalized trend vote per timeframe; flatness produces `SIDEWAYS` lean, not a weak directional lean.

### 2.2.2 Momentum Family
- **Responsibility** — Judge the speed/acceleration and overbought-oversold state of the move.
- **Behavior / Actions** — Compute momentum oscillators (RSI/stochastic/ROC-style, MACD-style histogram); map extremes and divergences to lean (strong momentum → trend lean; exhaustion/divergence → reversal lean; mid-range → sideways).
- **Scenarios & Possibilities** — Momentum confirming trend; bearish/bullish divergence vs price (reversal hint); pinned-at-extreme in a strong trend (don't misread as reversal); flat mid-band chop; momentum spike from a single news bar.
- **Functional Test Case(s)** — *Given* price making new highs while momentum makes lower highs, *When* evaluated, *Then* vote carries a `REVERSAL` lean with a divergence flag. *Given* oscillator mid-range and flat, *When* evaluated, *Then* `SIDEWAYS` lean.
- **Clear Outcome** — A normalized momentum vote that distinguishes *confirmation* from *exhaustion/divergence*, with divergence exposed as a flag.

### 2.2.3 Volatility Family
- **Responsibility** — Characterize the volatility regime (expanding / contracting / stable) and its level.
- **Behavior / Actions** — Compute realized-volatility / range measures (ATR, Bollinger width, candle-range stats) and implied level (IV from chain); classify squeeze vs expansion; map contraction→`SIDEWAYS` lean, expansion+direction→trend, expansion w/o direction→`REVERSAL`/transition lean.
- **Scenarios & Possibilities** — Volatility squeeze preceding a break; expansion spike on event; high-IV crush intraday; ATR inflated by one gap bar; low realized but high implied (event pricing); stable mid-vol grind.
- **Functional Test Case(s)** — *Given* Bollinger width contracting to a multi-bar low, *When* evaluated, *Then* a `SIDEWAYS`/squeeze lean with an "expansion-watch" flag. *Given* ATR doubling with directional closes, *When* evaluated, *Then* trend lean reinforced.
- **Clear Outcome** — A vote that reports both volatility *level* and *direction-of-change*; squeezes are flagged for the regime-change node, not just folded into sideways.

### 2.2.4 Volume / Participation Family
- **Responsibility** — Judge whether price moves are backed by participation.
- **Behavior / Actions** — Compare current volume to rolling norms; assess up/down-volume balance and option-chain traded volume; map confirming volume→trend lean, thin/divergent volume→discount the move (sideways/reversal lean).
- **Scenarios & Possibilities** — Breakout on high volume (confirm); rally on thin volume (suspect); lunchtime volume lull misread as exhaustion; volume absent from feed (futures vs index — index has no volume); end-of-session surge; one fat print distorting average.
- **Functional Test Case(s)** — *Given* a price breakout accompanied by volume >> rolling average, *When* evaluated, *Then* trend lean with high family confidence. *Given* an index symbol with no volume field, *When* evaluated, *Then* family marks itself `ready=false` and abstains.
- **Clear Outcome** — A participation vote that *abstains cleanly* when volume data is structurally unavailable rather than emitting a misleading neutral.

### 2.2.5 Market-Structure Family
- **Responsibility** — Locate price relative to support/resistance and judge structure (range-bound vs broken).
- **Behavior / Actions** — Derive S/R levels (swing highs/lows, prior-day/open levels, round numbers, option max-pain/high-OI strikes); assess whether price is mid-range, testing, or breaking a level; map mid-range→sideways, clean break→trend, rejection at level→reversal.
- **Scenarios & Possibilities** — Clean range with defined edges; false break then reclaim (trap); price pinned at a high-OI strike near expiry; no clear levels (open-air drift); level cluster ambiguity; gap opening outside the prior range.
- **Functional Test Case(s)** — *Given* price oscillating between a well-defined support and resistance, *When* evaluated, *Then* `SIDEWAYS` lean with the range bounds reported. *Given* a decisive close beyond resistance, *When* evaluated, *Then* `TREND_UP` lean with break flag.
- **Clear Outcome** — A structure vote that names the active levels and whether price is contained, breaking, or rejecting them.

### 2.2.6 Candlestick / Price-Action Family
- **Responsibility** — Read short-horizon price-action patterns for continuation vs reversal cues.
- **Behavior / Actions** — Detect candlestick formations (engulfing, pin/hammer, doji, inside/outside bars) and micro price-action (impulse vs corrective); map reversal patterns at extremes→reversal lean, continuation patterns→trend lean, indecision (doji clusters)→sideways.
- **Scenarios & Possibilities** — Reversal candle at a structure level (strong); same candle mid-range (weak/noise); doji at squeeze; pattern on the incomplete current bar (premature); single-bar pattern overweighted; conflicting patterns across timeframes.
- **Functional Test Case(s)** — *Given* a bearish engulfing at a tested resistance, *When* evaluated, *Then* `REVERSAL`/`TREND_DOWN` lean with pattern named. *Given* a doji cluster in a tight range, *When* evaluated, *Then* `SIDEWAYS` lean.
- **Clear Outcome** — A price-action vote that weights patterns by *location/context* (pattern alone is low-confidence; pattern-at-level is high), and ignores patterns on incomplete bars unless flagged.

### 2.2.7 Options-Sentiment Family
- **Responsibility** — Read the option market's own positioning for directional/sentiment lean.
- **Behavior / Actions** — Compute PCR (OI and volume), OI build-up/unwind at key strikes, IV skew (put vs call), max-pain vs spot; map call-writing dominance→down lean, put-writing→up lean, balanced/pinning→sideways, skew spikes→reversal/event lean.
- **Scenarios & Possibilities** — Strong directional OI build; expiry-day pinning to max pain; stale OI (updates slowly intraday); skew distorted by far OTM illiquid strikes; PCR extremes (contrarian vs trend ambiguity); missing OI from feed; first-tick-of-day OI baseline.
- **Functional Test Case(s)** — *Given* heavy call OI build at the nearest resistance strike, *When* evaluated, *Then* a `TREND_DOWN`/capped lean. *Given* spot pinned at max-pain on expiry with balanced PCR, *When* evaluated, *Then* `SIDEWAYS` lean with pinning flag.
- **Clear Outcome** — A sentiment vote derived from positioning, explicitly flagging expiry-pinning and abstaining when OI/IV are absent or stale.

### 2.2.8 Family Vote Normalization (shared contract)
- **Responsibility** — Enforce one uniform vote schema so all seven families are comparable and no family can over-speak.
- **Behavior / Actions** — Define the vote object (label distribution or {up,down,neutral,reversal} weights summing to 1, a family confidence in [0,1], a `ready` flag, and flags list); clip/renormalize each family's raw output into this schema; reject or zero out any family attempting to exceed its weight budget.
- **Scenarios & Possibilities** — A family returns an unnormalized score; a family returns NaN; a family emits an out-of-enum label; a family confidently votes while `ready=false` (should be forced to abstain); a future eighth family added (schema must extend without breaking).
- **Functional Test Case(s)** — *Given* a family returns weights summing to 1.4, *When* normalized, *Then* renormalized to 1.0. *Given* a family with `ready=false` but nonzero weights, *When* normalized, *Then* weights zeroed and it abstains.
- **Clear Outcome** — Every family hands the consensus node an identical, bounded, renormalized contract — comparability is guaranteed structurally, not by convention.

---

## 2.3 Multi-Timeframe Aggregation

Owns the fact that each family is computed across several timeframes and must be reconciled before voting.

### 2.3.1 Per-Timeframe Family Orchestration
- **Responsibility** — Run each family on each available, sufficient timeframe and collect the grid of votes.
- **Behavior / Actions** — For the (family × timeframe) matrix, invoke evaluation only where 2.1 marked the timeframe sufficient; collect votes; mark cells skipped for insufficiency; keep computation independent (no cross-cell leakage).
- **Scenarios & Possibilities** — Some timeframes warm, others not (partial grid); a family unavailable globally (entire row absent); a single (family,TF) cell errors (isolate, don't fail whole grid); very many timeframes (compute cost).
- **Functional Test Case(s)** — *Given* 3 timeframes where the longest is not yet warm, *When* orchestrated, *Then* the grid has that column marked skipped and others populated. *Given* one cell raises, *When* orchestrated, *Then* that cell is null-flagged and the rest complete.
- **Clear Outcome** — A complete, explicitly-annotated (family × timeframe) vote grid where missing cells are marked, never silently zero.

### 2.3.2 Timeframe Weighting & Alignment
- **Responsibility** — Combine each family's per-timeframe votes into one vote per family, weighting timeframes.
- **Behavior / Actions** — Apply a timeframe-weighting scheme (e.g. higher timeframes carry structural weight, lower carry timeliness); reward cross-timeframe alignment (same lean across TFs → higher family confidence); collapse the grid row to a single per-family vote.
- **Scenarios & Possibilities** — All timeframes aligned (boost confidence); fully opposed (down-weight / route to 2.3.3); only one timeframe present (no alignment bonus); weighting scheme accidentally lets one TF dominate; expiry-day where the lowest TF is most informative.
- **Functional Test Case(s)** — *Given* trend family up on all timeframes, *When* aligned, *Then* a single up vote with an alignment-boosted family confidence. *Given* up on short TF, down on long TF, *When* aligned, *Then* a reduced-confidence vote flagged for TF-conflict.
- **Clear Outcome** — One vote per family with a timeframe-alignment quality score; alignment increases confidence, opposition decreases it.

### 2.3.3 Timeframe Conflict Handling
- **Responsibility** — Decide what a family "means" when its timeframes disagree.
- **Behavior / Actions** — Detect intra-family cross-timeframe contradiction (e.g. micro up vs macro down); choose a documented policy (defer to higher TF, mark neutral, or emit a `TRANSITION` lean) and attach a conflict flag for confidence penalty and regime-change consideration.
- **Scenarios & Possibilities** — Short-term pullback inside a longer uptrend (continuation, not reversal); genuine top forming (lower TF leads); both TFs noisy; conflict on every family simultaneously (whole-market transition).
- **Functional Test Case(s)** — *Given* a family with bullish lower TF and bearish higher TF, *When* resolved with "defer-to-higher" policy, *Then* vote leans with higher TF but carries a `tf_conflict` flag. *Given* aligned TFs, *When* resolved, *Then* no conflict flag.
- **Clear Outcome** — Intra-family timeframe disagreement is resolved by an explicit, testable policy and always surfaces a flag — it is never averaged into a misleading neutral silently.

---

## 2.4 Consensus / Voting

Owns turning seven comparable family votes into one label, domination-resistant by construction.

### 2.4.1 Vote Aggregation
- **Responsibility** — Combine the seven per-family votes into a single regime score distribution.
- **Behavior / Actions** — Weighted-sum (or rank/median) the family vote distributions over the label set; produce an aggregate score per candidate label; keep the contribution of each family auditable.
- **Scenarios & Possibilities** — Clear majority; even spread (no winner); two families missing (renormalize over present families); one family at max confidence trying to swing the result (must be capped upstream by weight budget); all families neutral (→sideways or low-confidence).
- **Functional Test Case(s)** — *Given* 5 families lean up, 2 sideways, *When* aggregated, *Then* `TREND_UP` has the top aggregate score. *Given* two families absent, *When* aggregated, *Then* weights renormalize over the five present and totals still sum to 1.
- **Clear Outcome** — An aggregate label-score distribution with a per-family contribution breakdown; absent families are renormalized, never treated as zero-votes-for-sideways by accident.

### 2.4.2 Family Weighting & Anti-Domination Guard
- **Responsibility** — Guarantee no single family (or directional pair) can unilaterally set the label.
- **Behavior / Actions** — Cap each family's maximum influence; verify the weight vector cannot let one family exceed a defined share; optionally require ≥N families to agree for a directional label; detect and damp correlated families voting in lockstep (e.g. trend+momentum always agreeing) so the "seven independent" property holds in effect.
- **Scenarios & Possibilities** — One family maxed-out and loud; two highly-correlated families effectively double-counting a single signal; a family stuck on a constant vote (bias); weight misconfiguration that re-introduces domination.
- **Functional Test Case(s)** — *Given* the options-sentiment family at confidence 1.0 and all others neutral, *When* guarded, *Then* the label cannot be a high-confidence directional call on that family alone. *Given* trend and momentum perfectly correlated, *When* guarded, *Then* their combined influence is damped below their nominal sum.
- **Clear Outcome** — Provable bound: no family's contribution exceeds its cap; a directional label requires genuine multi-family agreement.

### 2.4.3 Label Decision
- **Responsibility** — Pick the final regime label from the aggregate distribution.
- **Behavior / Actions** — Apply decision rule (argmax with a minimum-margin/threshold so a barely-leading label doesn't win); fall back to `SIDEWAYS` or `UNKNOWN` when no label clears the margin; pass the runner-up and margin downstream.
- **Scenarios & Possibilities** — Decisive winner; near-tie within margin (→sideways/low-confidence, not a coin-flip pick); reversal vs trend close call (route to 2.7.2); everything below threshold (→`UNKNOWN`).
- **Functional Test Case(s)** — *Given* `TREND_UP` 0.34 vs `SIDEWAYS` 0.33 (margin tiny), *When* decided, *Then* label = `SIDEWAYS`/low-confidence, not `TREND_UP`. *Given* `TREND_DOWN` 0.7 vs next 0.1, *When* decided, *Then* label = `TREND_DOWN`.
- **Clear Outcome** — A single label plus margin and runner-up; ambiguous distributions resolve to a conservative non-directional label rather than a noisy pick.

---

## 2.5 Confidence Scoring

Owns the [0,1] confidence that accompanies the label.

### 2.5.1 Agreement-Based Confidence
- **Responsibility** — Derive base confidence from how strongly families and timeframes agree.
- **Behavior / Actions** — Combine winning-label margin, fraction of families supporting it, their individual confidences, and timeframe-alignment quality into a base confidence; monotone in agreement.
- **Scenarios & Possibilities** — Unanimous high-confidence families (→high); slim majority (→moderate); split (→low); high family confidences but on opposing labels (→low despite individual certainty).
- **Functional Test Case(s)** — *Given* 7/7 families lean up at high confidence, *When* scored, *Then* base confidence near the top of range. *Given* 4-up/3-down, *When* scored, *Then* base confidence low.
- **Clear Outcome** — Base confidence rises with breadth and depth of agreement and falls with disagreement, before penalties.

### 2.5.2 Confidence Penalties & Calibration
- **Responsibility** — Discount confidence for data-quality and context risks, and keep the scale honest.
- **Behavior / Actions** — Apply multiplicative penalties for staleness (2.1.3), partial warm-up, abstaining families, timeframe conflict flags (2.3.3), and known fragile contexts (expiry pinning, gap dislocation); clamp to [0,1]; ensure the resulting number is interpretable/comparable across snapshots (calibration intent, not just a raw blend).
- **Scenarios & Possibilities** — Strong agreement but stale feed (must drop); agreement but 3 families abstained (thin basis → drop); over-penalizing to near-zero everywhere (mis-calibration); penalties stacking multiplicatively to implausibly low values; no penalties yet still over-confident in chop.
- **Functional Test Case(s)** — *Given* base confidence 0.8 with a staleness flag, *When* penalized, *Then* final confidence is materially lower and carries reason="stale". *Given* clean fresh full-family agreement, *When* penalized, *Then* confidence ≈ base (no penalty).
- **Clear Outcome** — Final confidence reflects both agreement and data trust; every penalty is itemized in the rationale; value always within [0,1].

---

## 2.6 Conflict / Disagreement Handling

Owns the cases where the families don't tell one clean story.

### 2.6.1 Split-Vote / Tie Resolution
- **Responsibility** — Resolve aggregate distributions with no clear winner.
- **Behavior / Actions** — Detect near-ties / multi-modal distributions; apply a documented tie-break (prefer the non-directional/`SIDEWAYS` or `UNKNOWN` label, never silently pick a direction); cap confidence; record that a tie occurred.
- **Scenarios & Possibilities** — Up vs down tie (genuine indecision → sideways, not a guess); up vs sideways tie (lean sideways); three-way split; oscillating tie across consecutive snapshots (hand to 2.8).
- **Functional Test Case(s)** — *Given* `TREND_UP` and `TREND_DOWN` within tie margin, *When* resolved, *Then* label = `SIDEWAYS` (or `UNKNOWN`) with low confidence and a `tie` flag. *Given* a clear winner, *When* checked, *Then* no tie path taken.
- **Clear Outcome** — Ties resolve conservatively and visibly — the module never invents a direction to break an even vote.

### 2.6.2 Contradiction Detection
- **Responsibility** — Flag *meaningful* cross-family contradictions even when a label still wins.
- **Behavior / Actions** — Detect signal-level contradictions (e.g. price trending up while options-sentiment strongly bearish, or volume not confirming a trend break, or momentum diverging from trend); raise a `contradiction` flag and feed it to confidence penalty and to reversal detection.
- **Scenarios & Possibilities** — Trend-up + bearish OI build (possible exhaustion/trap); breakout on no volume; momentum divergence at a structure level (reversal setup); benign disagreement (don't over-flag noise); contradictions that are actually the signature of a regime change.
- **Functional Test Case(s)** — *Given* `TREND_UP` label but options-sentiment leaning down at high confidence, *When* checked, *Then* a `contradiction` flag is attached and confidence is penalized. *Given* all families coherent, *When* checked, *Then* no flag.
- **Clear Outcome** — Latent contradictions are surfaced as flags and dampen confidence — a "winning" label that hides internal conflict is never reported as clean.

---

## 2.7 Regime-Change Detection

Owns comparing *now* to the recent past to spot transitions, distinct from steady-state classification.

### 2.7.1 Transition Detection
- **Responsibility** — Detect that the regime is shifting from its prior state.
- **Behavior / Actions** — Compare the current aggregate distribution / label to a short history buffer; detect drift (e.g. sideways→trending, trend strength fading); emit a transition signal with direction-of-change.
- **Scenarios & Possibilities** — Squeeze→expansion break (vol family leads); trend rolling into range; sudden event jump (abrupt transition); slow drift vs sharp flip; noise mimicking a transition (must not over-fire — feeds 2.8).
- **Functional Test Case(s)** — *Given* prior label `SIDEWAYS` for many snapshots then aggregate tips firmly to `TREND_UP` with a vol-expansion flag, *When* checked, *Then* a `regime_change` signal fires. *Given* one noisy snapshot flip, *When* checked, *Then* no change signal (debounced).
- **Clear Outcome** — A transition signal that fires on persistent, corroborated shifts and stays quiet on single-snapshot noise.

### 2.7.2 Reversal vs Continuation Distinction
- **Responsibility** — When change is detected, decide if it's a reversal of direction or a continuation/pause.
- **Behavior / Actions** — Combine momentum divergence, candlestick reversal patterns at structure levels, and sentiment skew shifts to separate `REVERSAL` from a mere pullback/continuation; assign the `REVERSAL` label only when reversal evidence is corroborated.
- **Scenarios & Possibilities** — Pullback within trend (continuation, not reversal); confirmed top/bottom (reversal); V-shape event reversal; failed reversal that resumes trend; reversal call right at expiry pin.
- **Functional Test Case(s)** — *Given* an uptrend with a single red bar but no divergence/level rejection, *When* distinguished, *Then* label stays `TREND_UP` (continuation). *Given* divergence + reversal candle at resistance + sentiment flip, *When* distinguished, *Then* label = `REVERSAL`.
- **Clear Outcome** — `REVERSAL` is reserved for corroborated turns; ordinary pullbacks remain trend-continuation, preventing reversal over-labeling.

---

## 2.8 Hysteresis / Flip-Flap Avoidance

Owns temporal stability of the emitted label.

### 2.8.1 Dwell-Time / Debounce
- **Responsibility** — Prevent rapid oscillation of the label across consecutive snapshots.
- **Behavior / Actions** — Require a new label to persist for a minimum dwell (N snapshots or T seconds) before it replaces the current emitted label; hold the prior label meanwhile (with an annotation that a candidate is pending).
- **Scenarios & Possibilities** — Genuine fast regime change being delayed (latency vs stability trade-off); boundary chatter between sideways and trend; a one-off spike snapshot; dwell so long it lags a real reversal dangerously.
- **Functional Test Case(s)** — *Given* current `SIDEWAYS` and a single snapshot computing `TREND_UP` then back to sideways, *When* debounced, *Then* emitted label stays `SIDEWAYS`. *Given* `TREND_UP` computed for N consecutive snapshots, *When* debounced, *Then* emitted label switches to `TREND_UP`.
- **Clear Outcome** — The emitted label changes only after a candidate persists past the dwell threshold; transient flips are suppressed, with the pending candidate visible.

### 2.8.2 Confirmation Threshold for Flips
- **Responsibility** — Make flips harder than holds (asymmetric switching cost).
- **Behavior / Actions** — Require a higher confidence / margin to *change* the label than to *maintain* it (a deadband around the current state); allow an override fast-path for strong corroborated regime-change signals (from 2.7) so safety-critical transitions aren't over-damped.
- **Scenarios & Possibilities** — Confidence hovering at the switch boundary (deadband prevents chatter); a strong abrupt event that *should* bypass dwell (fast-path); deadband set so wide the module ignores real change; competing requirements of 2.8.1 vs 2.7.1 (define precedence).
- **Functional Test Case(s)** — *Given* current `TREND_UP` and a new `SIDEWAYS` candidate just over plain threshold but under the higher flip threshold, *When* evaluated, *Then* label holds `TREND_UP`. *Given* a corroborated event-driven `REVERSAL` with a 2.7 fast-path flag, *When* evaluated, *Then* the flip is allowed before full dwell.
- **Clear Outcome** — Label flips require stronger evidence than holds; an explicit, tested fast-path exists for corroborated abrupt changes, with documented precedence over dwell.

---

## 2.9 Output Assembly

Owns producing the final, well-formed module output — including the degraded path.

### 2.9.1 Output Contract & Rationale Packaging
- **Responsibility** — Emit the final label, confidence, and structured rationale in the fixed output schema.
- **Behavior / Actions** — Assemble `regime_label`, `confidence`, per-family votes, timeframe breakdown, all flags (stale/warmup/tie/contradiction/tf_conflict/regime_change/pending-candidate), and applied penalties; validate the object against the schema before returning.
- **Scenarios & Possibilities** — Full healthy output; output with many flags; downstream expecting a field that's null in a degraded run (must still be schema-valid); schema evolution (new flag) without breaking consumers; ensuring rationale doesn't leak a trade recommendation.
- **Functional Test Case(s)** — *Given* a healthy classification, *When* assembled, *Then* the object validates against the output schema and contains the per-family contribution map. *Given* a degraded run, *When* assembled, *Then* the object still validates with flags populated.
- **Clear Outcome** — Every emission is schema-valid, fully audit-traceable to its family votes and penalties, and free of any trade directive.

### 2.9.2 Fail-Safe / Degraded Output
- **Responsibility** — Always return a safe, well-formed output even when inputs/compute fail.
- **Behavior / Actions** — On warm-up, total staleness, schema failure, or internal error, return `UNKNOWN`/`WARMUP` with confidence 0 and a reason code; never raise out of the module; ensure the degraded output is distinguishable from a confident `SIDEWAYS`.
- **Scenarios & Possibilities** — Empty/garbage snapshot; all families abstain; exception mid-pipeline; feed outage; pre-open call; partial grid with nothing sufficient. Failure to avoid: emitting a confident directional label off broken data.
- **Functional Test Case(s)** — *Given* a snapshot that fails schema validation, *When* assembled, *Then* output = `UNKNOWN`, confidence 0, reason="schema_invalid", no exception propagates. *Given* all families abstain, *When* assembled, *Then* `UNKNOWN`/low-confidence, clearly not `SIDEWAYS`.
- **Clear Outcome** — The module is total (never throws) and never disguises absence-of-signal as a confident regime; `UNKNOWN` ≠ `SIDEWAYS`.

---

## Suggestions (for bubble-up)

Market-condition scenarios touching this module that likely warrant system-wide treatment (separate, for later review):

1. **Gap-open dislocation.** Opening gaps wreck MA/ATR/structure lookbacks and leave price outside the prior range. May need a system-level "first N minutes" posture and a dedicated regime treatment, not just per-family repair.
2. **Expiry-day pinning.** Max-pain pinning makes price hug a strike with low realized vol but event-like option behavior; options-sentiment and structure families can conflict with trend/momentum. Likely deserves a system-wide expiry-aware mode (this module flags it; someone must consume the flag).
3. **News / event spikes.** Sudden single-bar shocks (policy, macro prints) produce momentum/volatility spikes and abrupt transitions; the dwell/flip fast-path here interacts with system-level event handling — needs coordinated policy.
4. **Low-IV grind vs high-IV regime.** The volatility *level* (not just direction) reshapes what "sideways" means for a theta strategy; a persistent IV-regime classification may belong system-wide rather than re-derived here each snapshot.
5. **Choppy whipsaw / boundary chatter.** Persistent oscillation around the sideways/trend boundary stresses hysteresis; if it recurs, the dwell/deadband parameters may need a system-owned tuning loop rather than fixed constants.
6. **Feed staleness / partial-data sessions.** Stale or partial feeds force degraded output; the system needs a clear contract for how it behaves when Regime returns `UNKNOWN` (pause? hold? last-good?).
7. **Index-without-volume structural gap.** Indices may lack true volume, permanently abstaining the participation family; system should know one of the seven votes is structurally weak on some symbols, affecting the "seven independent families" guarantee.
8. **Correlated-family redundancy.** Trend and momentum (and others) can move in lockstep, undermining true independence; whether to formally decorrelate or re-weight families is a system-design question beyond this module's local guard.
9. **Holiday / half-day / pre-open snapshots.** Sessions with no live market or shortened hours need a system-level calendar so Regime isn't asked to classify a non-trading state.
