# Module 1 — Market Data

## 1.0 Module Overview

**Whole-module responsibility.** Acquire the raw market picture from upstream feed(s) each cycle and supply a single, coherent, freshness-tagged **market snapshot** that downstream consumers can trust without re-validating. This module is the system's *single source of truth* for "what the market looks like right now." It owns acquisition, normalization, assembly, derivation (greeks/IV where not vended), multi-timeframe aggregation, and quality/freshness labeling. It does **not** decide trades, size positions, or interpret signals — it only describes the market truthfully and flags when it cannot.

**Inputs (plain data).**
- An index identifier (which underlying market to describe — e.g., a NIFTY-family or SENSEX-family index).
- A request/tick: a cue to produce a fresh snapshot (either a clock-driven cycle or an event-driven tick arrival).

**Outputs (plain data).** A market snapshot containing:
- Underlying spot price (with its own timestamp).
- The option chain: per strike → call price, put price, implied volatility, greeks (delta, gamma, theta, vega).
- Multi-timeframe OHLC candles of the underlying.
- Cross-cutting metadata: snapshot timestamp, per-field freshness/staleness flags, data-quality flags, source provenance, and an overall "usable / degraded / unusable" verdict.

**Design stance for this document.** Breadth-first discovery. Where a behavior could be sourced *or* computed (greeks, IV, candles), both paths are enumerated rather than chosen. Parameter values (staleness thresholds, strike-window width, candle timeframes) are deliberately left symbolic — they belong to system-level configuration, not to this module's structure.

---

## Sub-Module Tree (map)

- **1.1 Feed / Source Management** — 1.1.1 Connection lifecycle · 1.1.2 Subscription management · 1.1.3 Tick ingestion & normalization · 1.1.4 Source failover & redundancy · 1.1.5 Clock & time-sync
- **1.2 Underlying Spot Acquisition** — 1.2.1 Spot capture · 1.2.2 Spot sanity & last-good-value guard
- **1.3 Option Chain Construction** — 1.3.1 Strike-universe selection · 1.3.2 Per-strike quote assembly · 1.3.3 Implied-volatility resolution · 1.3.4 Greeks resolution · 1.3.5 Expiry & contract resolution
- **1.4 Multi-Timeframe OHLC Aggregation** — 1.4.1 Base-candle construction · 1.4.2 Timeframe rollup · 1.4.3 Bar-boundary & time alignment · 1.4.4 Candle gap handling
- **1.5 Snapshot Assembly & Freshness** — 1.5.1 Snapshot composition · 1.5.2 Freshness / staleness tagging · 1.5.3 Missing-data & partial-snapshot policy
- **1.6 Data Quality & Sanity** — 1.6.1 Range & sanity checks · 1.6.2 Cross-field consistency · 1.6.3 Quality flagging & quarantine
- **1.7 Cadence & Throttling** — 1.7.1 Cycle pacing & coalescing · 1.7.2 Caching & dedupe

---

## 1.1 Feed / Source Management

Owns the live relationship with whatever upstream supplies market data. Everything below assumes "a feed exists"; this branch makes that assumption true and observable.

### 1.1.1 Connection lifecycle
- **Responsibility** — Establish, maintain, and tear down the connection(s) to the market-data source.
- **Behavior / Actions** — Open connection on start; authenticate/handshake if required; send keepalives/heartbeats; detect drops; reconnect with backoff; emit connection-state transitions (DOWN→CONNECTING→UP→DEGRADED).
- **Scenarios & Possibilities** — Cold start before market open; mid-session silent disconnect (socket open but no data); auth-token expiry mid-session; rapid flapping connect/disconnect; server-side rate-limit / forced disconnect; connection up but subscription silently dropped; reconnect storm after a network blip.
- **Functional Test Case(s)** — *Given* an established UP connection, *When* the socket emits no data for longer than the heartbeat-miss threshold, *Then* state transitions to DEGRADED and a reconnect is initiated with backoff. *Given* reconnection succeeds, *When* the link returns, *Then* state returns to UP and subscriptions are re-asserted (see 1.1.2).
- **Clear Outcome** — Connection state is always known and observable; no silent "zombie" connection is ever treated as healthy; reconnects are bounded (backoff, not a tight loop).

### 1.1.2 Subscription management
- **Responsibility** — Track exactly which instruments (underlying + the required option contracts) are subscribed and ensure that set is the set we actually need.
- **Behavior / Actions** — Compute the desired subscription set from the active index + selected strike universe + active expiries; subscribe/unsubscribe as that set changes; re-subscribe after any reconnect; reconcile "requested vs. confirmed" subscriptions.
- **Scenarios & Possibilities** — Strike window shifts as spot moves → new strikes must be subscribed before they're needed; expiry rollover changes the contract set; subscription cap on the feed (can't subscribe to whole chain); partial subscription confirmation; phantom subscription that confirms but never ticks; over-subscription causing feed throttling.
- **Functional Test Case(s)** — *Given* spot moves enough that the strike window shifts by N strikes, *When* the next desired-set is computed, *Then* the newly-in-window strikes are subscribed and far-out strikes unsubscribed, and the confirmed set matches the desired set. *Given* a reconnect, *When* the link returns, *Then* the full desired set is re-subscribed (none silently lost).
- **Clear Outcome** — At any cycle, every instrument the snapshot needs is subscribed and confirmed, or its absence is explicitly flagged (not silently missing).

### 1.1.3 Tick ingestion & normalization
- **Responsibility** — Receive raw upstream messages and convert them into a single internal canonical tick representation.
- **Behavior / Actions** — Parse each message; map vendor fields → canonical fields (symbol, ltp, bid/ask, volume, OI, IV, greeks if vended, timestamp); normalize units/scaling (paise vs. rupees, tick size, lot vs. per-share); reject/repair malformed messages; attach an arrival timestamp.
- **Scenarios & Possibilities** — Tick with last-price-less payload (e.g., an OI-only or depth-only update) — must **not** clobber a good last price [cf. known feed bugs in this domain]; out-of-order ticks (older timestamp arrives after newer); duplicate ticks; unit/scale mismatch between indices; a tick whose symbol doesn't map to anything subscribed; NaN/null fields; sudden schema change from the vendor.
- **Functional Test Case(s)** — *Given* a good last price is held for an option, *When* a tick arrives lacking a last price (only OI/depth), *Then* the last price is preserved (only the present fields update) and last-price freshness is unchanged. *Given* an out-of-order tick (timestamp older than the held value), *When* it is ingested, *Then* it does not overwrite the newer value.
- **Clear Outcome** — Every accepted tick is canonical, correctly scaled, and monotonic per field; partial ticks update only the fields they carry; malformed ticks are dropped with a counter, never silently corrupting state.

### 1.1.4 Source failover & redundancy
- **Responsibility** — Decide which source is authoritative when more than one exists, and degrade gracefully when the primary fails.
- **Behavior / Actions** — Rank sources (primary/secondary); detect primary unhealthy (via 1.1.1 / staleness); fail over to secondary; mark provenance on resulting data; fail back when primary recovers and stabilizes.
- **Scenarios & Possibilities** — Only one source exists (failover is a no-op but must not crash); primary and secondary disagree on price; secondary is stale-but-present while primary is dead; flapping between sources; both sources down simultaneously; secondary has a different strike/expiry coverage than primary.
- **Functional Test Case(s)** — *Given* a primary and secondary source, *When* the primary goes stale beyond threshold, *Then* output switches to secondary and each affected field is tagged with secondary provenance. *Given* both sources are down, *Then* the snapshot is marked **unusable** rather than emitting last-known values as if live.
- **Clear Outcome** — There is always a defined authoritative source or an explicit "no source" verdict; provenance is always recorded; no flapping (hysteresis on failback).

### 1.1.5 Clock & time-sync
- **Responsibility** — Provide the single trusted notion of "now" used to timestamp ticks, build bars, and judge staleness.
- **Behavior / Actions** — Maintain a monotonic local clock; reconcile against exchange/feed timestamps; measure feed-vs-local skew; decide whether bar boundaries and staleness use exchange time or arrival time.
- **Scenarios & Possibilities** — Local clock drift / NTP step backward (monotonic source must absorb this); large feed-vs-local skew; exchange timestamps in a different timezone/format; daylight or session-boundary edge moments; using arrival time vs. event time changes which bar a tick lands in near a boundary.
- **Functional Test Case(s)** — *Given* the system clock steps backward, *When* the next tick arrives, *Then* monotonic ordering and staleness math are unaffected (durations stay non-negative). *Given* feed-vs-local skew exceeds a threshold, *Then* the snapshot is flagged with a clock-skew warning.
- **Clear Outcome** — One consistent, monotonic clock underpins all timestamps and staleness; skew is measured and surfaced, never silently distorting freshness.

---

## 1.2 Underlying Spot Acquisition

### 1.2.1 Spot capture
- **Responsibility** — Maintain the current underlying spot price for the active index and stamp it with its own timestamp.
- **Behavior / Actions** — Update spot from each authoritative underlying tick; keep the latest value + its source timestamp + arrival timestamp.
- **Scenarios & Possibilities** — Underlying ticks slower than options (index ticks may be throttled/snapshotted); spot from synthetic vs. official index value; pre-open / no-trade spot; first tick of the day; spot updates but option chain doesn't (or vice versa).
- **Functional Test Case(s)** — *Given* an authoritative underlying tick, *When* it is ingested, *Then* the stored spot and its timestamps update to that tick. *Given* no new spot tick for one cycle, *When* a snapshot is produced, *Then* spot carries its last timestamp and a freshness age (see 1.5.2).
- **Clear Outcome** — Spot is always present with a truthful timestamp, or explicitly flagged absent; spot timestamp is independent of the chain's.

### 1.2.2 Spot sanity & last-good-value guard
- **Responsibility** — Reject implausible spot updates and protect the last good value.
- **Behavior / Actions** — Bound-check each spot update (non-negative, within a plausible per-tick move band, within day's circuit band); on violation, reject the update and retain last-good while flagging.
- **Scenarios & Possibilities** — Zero / negative spot (bad tick); a 1-tick spike then revert (fat-finger print on the index proxy); legitimate gap/limit move that *looks* implausible (must distinguish via circuit band, not just delta); first tick has no prior to compare against.
- **Functional Test Case(s)** — *Given* a stored spot of S, *When* a spot tick of 0 (or negative) arrives, *Then* it is rejected, last-good S is retained, and a spot-anomaly flag is raised. *Given* a move within the day's circuit band, *Then* it is accepted even if large.
- **Clear Outcome** — Spot is never poisoned by a single bad print; rejections are flagged, not hidden; legitimate large moves still pass.

---

## 1.3 Option Chain Construction

Assembles the per-strike picture. The hard questions here are *which* strikes, *where* IV/greeks come from, and *which* expiry.

### 1.3.1 Strike-universe selection
- **Responsibility** — Decide the set of strikes to include in the chain for this cycle.
- **Behavior / Actions** — Center a window of strikes around current spot/ATM; include both calls and puts; widen/narrow per a configured window; recenter as spot moves; align to the instrument's strike step.
- **Scenarios & Possibilities** — Spot exactly between two strikes (ATM ambiguity); window edge crosses where liquidity dies (deep OTM strikes exist but never trade); strike step differs by index; spot moves fast so window must recenter mid-cycle; requested strikes not listed by exchange; very wide window stresses subscription cap (ties to 1.1.2).
- **Functional Test Case(s)** — *Given* spot S and window width W, *When* selecting strikes, *Then* the set is the W strikes nearest S on each side, snapped to the strike step, calls and puts both. *Given* spot moves past a strike, *Then* the window recenters and the set shifts by the appropriate number of strikes.
- **Clear Outcome** — The chain always covers a defined, ATM-centered, both-sides strike set aligned to the real strike grid; the set is deterministic given spot + window.

### 1.3.2 Per-strike quote assembly
- **Responsibility** — For each selected strike, assemble the call and put quote (price; bid/ask if available; volume/OI as carried).
- **Behavior / Actions** — Pull the latest canonical tick per call and per put; record price + freshness per leg; carry bid/ask/OI when present; mark legs with no data.
- **Scenarios & Possibilities** — One leg of a strike ticks, the other is stale; a strike with no trades all session (price never seen); wide bid/ask (illiquid) — mid vs. last divergence; crossed/locked market (bid ≥ ask); last price exists but is hours old; price present but zero (genuine vs. bad).
- **Functional Test Case(s)** — *Given* a selected strike whose call ticked recently and whose put has no tick this session, *When* assembling, *Then* the call carries a fresh price and the put is marked no-data (not defaulted to 0 or to the call). *Given* a crossed quote (bid ≥ ask), *Then* the leg is flagged as a quality anomaly.
- **Clear Outcome** — Every selected strike yields a call+put record where each leg is either a fresh/aged price with provenance or an explicit no-data marker — never a silent fabrication.

### 1.3.3 Implied-volatility resolution
- **Responsibility** — Provide per-strike IV, whether vended by the feed or computed locally.
- **Behavior / Actions** — Prefer feed-vended IV when present and sane; otherwise compute IV from option price + spot + strike + time-to-expiry + rate via an inversion (e.g., Black-style solver); record which path produced it.
- **Scenarios & Possibilities** — Feed vends IV but it's stale/zero/absurd; solver fails to converge (deep ITM/OTM, near-zero extrinsic, near-expiry T→0 instability); price below intrinsic → no real IV; bid/ask vs. last choice changes IV materially; rate/dividend assumption matters; expiry-day T→0 makes IV explode/ill-conditioned.
- **Functional Test Case(s)** — *Given* the feed vends a sane IV, *When* resolving, *Then* that IV is used and tagged source=feed. *Given* no vended IV and a convergent solve, *Then* computed IV is used and tagged source=computed. *Given* an option priced below intrinsic or a non-converging solve, *Then* IV is marked unavailable (not a garbage number).
- **Clear Outcome** — Each strike has an IV with a clear feed-vs-computed provenance, or an explicit "IV unavailable" — never a silently wrong or NaN IV passed downstream.

### 1.3.4 Greeks resolution
- **Responsibility** — Provide per-strike delta, gamma, theta, vega, whether vended or computed.
- **Behavior / Actions** — Prefer feed-vended greeks when present and sane; otherwise compute from the same model/inputs as IV (consistent IV in → greeks out); ensure internal consistency (greeks correspond to the IV actually used).
- **Scenarios & Possibilities** — Feed greeks computed on a different IV than ours (inconsistency); greeks present but IV missing or vice-versa; near-expiry gamma/theta blow-up; deep ITM/OTM degeneracies (delta→0/±1, gamma→0); sign/convention mismatch (theta per-day vs per-year; put delta sign); greeks requested for a leg with no usable price.
- **Functional Test Case(s)** — *Given* IV was computed locally for a strike, *When* greeks are resolved, *Then* the greeks are derived from that same IV (consistent), or if feed greeks are used they pass a consistency check against our IV. *Given* a leg with no usable price/IV, *Then* greeks are marked unavailable.
- **Clear Outcome** — Every strike's greeks are internally consistent with its IV and provenance-tagged; unavailable greeks are explicit; conventions (signs, per-day theta) are normalized to one documented standard.

### 1.3.5 Expiry & contract resolution
- **Responsibility** — Resolve which expiry/expiries the chain represents and the correct contract symbols per strike.
- **Behavior / Actions** — Determine active weekly (and any required additional) expiry for the index; map (index, expiry, strike, type) → exact tradable contract symbol; compute time-to-expiry used by IV/greeks.
- **Scenarios & Possibilities** — Symbol format differs by index/family [cf. domain note that SENSEX-family option symbols differ from NIFTY-family]; expiry rollover day (old expiry dying, new one thin); holiday-shifted expiry; multiple expiries needed; T-to-expiry definition (calendar vs. trading time, intraday decay) materially affects greeks on expiry day; a strike listed for one expiry but not another.
- **Functional Test Case(s)** — *Given* an index and target expiry, *When* resolving a strike+type, *Then* the produced contract symbol matches that index family's exact symbology and is one the feed accepts. *Given* an expiry-rollover day, *Then* the chain uses the correct active expiry and time-to-expiry reflects the holiday-adjusted calendar.
- **Clear Outcome** — Every chain row maps to a real, correctly-formatted, currently-listed contract with a correct time-to-expiry; symbology is never assumed uniform across indices.

---

## 1.4 Multi-Timeframe OHLC Aggregation

### 1.4.1 Base-candle construction
- **Responsibility** — Build the finest-grain OHLC candle of the underlying from the tick stream.
- **Behavior / Actions** — For each base interval, open at first tick, track high/low, close at last tick, accumulate volume; seal the bar at the interval boundary.
- **Scenarios & Possibilities** — A last-price-less tick must not set low=0 or high=0 [cf. known low=0 poisoning bug in this domain]; an interval with zero ticks (no trade) — emit a flat/synthetic bar vs. a gap (policy in 1.4.4); first bar of the session; a single-tick interval (O=H=L=C); out-of-order tick within the interval.
- **Functional Test Case(s)** — *Given* a stream of underlying ticks within one base interval, *When* the boundary is reached, *Then* O=first, H=max, L=min, C=last over only valid-priced ticks. *Given* a tick with no valid price arrives, *Then* it does not contribute to H/L/O/C (no 0-poisoning).
- **Clear Outcome** — Base candles are correct and never poisoned by priceless ticks; each sealed bar has a definite, monotonic boundary timestamp.

### 1.4.2 Timeframe rollup
- **Responsibility** — Aggregate base candles into all required higher timeframes.
- **Behavior / Actions** — Roll N base bars into a higher bar (O=first.O, H=max H, L=min L, C=last.C, V=sum); keep each requested timeframe in sync; recompute the in-progress (partial) higher bar each cycle.
- **Scenarios & Possibilities** — A base bar is missing in the middle of a higher bar (partial rollup); timeframe not an integer multiple of base; the current higher bar is incomplete when a snapshot is requested (must be labeled in-progress); a late base bar arrives after the higher bar was sealed; large number of timeframes → cost.
- **Functional Test Case(s)** — *Given* N complete base bars composing one higher bar, *When* rolled up, *Then* the higher OHLCV equals the correct aggregation. *Given* a snapshot mid-higher-bar, *Then* the partial higher bar is emitted but flagged in-progress (not as a closed bar).
- **Clear Outcome** — Every requested timeframe is consistent with the base series; partial bars are clearly labeled; rollups are derived, not independently captured (single source of truth = base bars).

### 1.4.3 Bar-boundary & time alignment
- **Responsibility** — Define exactly when each bar opens/closes and align all timeframes to a common grid.
- **Behavior / Actions** — Anchor bar boundaries to session-aligned clock marks (not to arbitrary start time); ensure higher-TF boundaries are supersets of base boundaries; decide event-time vs arrival-time placement (ties to 1.1.5); handle session start/end partial bars.
- **Scenarios & Possibilities** — Session does not start on a round boundary (first bar shorter); a tick straddling a boundary (event vs arrival time decides its bar); timezone/DST edge; a higher-TF boundary that doesn't land on a base boundary (misconfiguration); pre-open/post-close ticks leaking into the first/last bar.
- **Functional Test Case(s)** — *Given* a tick whose event timestamp is just before a boundary but which arrives just after, *When* placed, *Then* it lands in the bar dictated by the documented (event-time) policy, consistently across timeframes. *Given* session start, *Then* the first base bar is aligned to the session anchor.
- **Clear Outcome** — All timeframes share one aligned, session-anchored grid; bar placement is deterministic and documented; no tick lands in two bars or none.

### 1.4.4 Candle gap handling
- **Responsibility** — Decide what a bar looks like when no ticks occurred in its interval.
- **Behavior / Actions** — Apply a defined no-trade policy: emit a flat bar (O=H=L=C=prior close, V=0) **or** mark a gap; never fabricate movement; flag synthetic bars.
- **Scenarios & Possibilities** — Illiquid period with sparse ticks; a feed outage masquerading as "no trades" (must be distinguishable — outage is a data problem, no-trade is a market fact); halt/circuit period; first bar with no prior close to flatten from; long gap spanning many bars.
- **Functional Test Case(s)** — *Given* a base interval with zero ticks while the feed is healthy, *When* the bar seals, *Then* a flat bar (carry prior close, V=0) is emitted and flagged synthetic. *Given* the same gap but the feed is DOWN/DEGRADED, *Then* the bar is marked gap/unreliable rather than a clean flat bar.
- **Clear Outcome** — Empty intervals are represented by an explicit, flagged policy; "no trade" and "no feed" are never conflated; downstream can tell real flatness from missing data.

---

## 1.5 Snapshot Assembly & Freshness

### 1.5.1 Snapshot composition
- **Responsibility** — Compose spot + chain + candles + metadata into one immutable, internally-timestamped snapshot per cycle.
- **Behavior / Actions** — Gather latest spot (1.2), chain (1.3), candles (1.4); attach snapshot timestamp, per-section provenance, and an overall verdict; freeze it so all consumers in the cycle see identical data.
- **Scenarios & Possibilities** — Sections captured at slightly different instants (spot newer than chain) — must record each section's own time; a section entirely missing; concurrent request during assembly (must not emit a half-built snapshot); very large chain → assembly cost.
- **Functional Test Case(s)** — *Given* spot, chain, and candles are available, *When* a snapshot is composed, *Then* it contains all three with their independent timestamps plus a single snapshot timestamp, and is immutable. *Given* the chain is missing, *Then* the snapshot still composes with the chain marked absent and the verdict downgraded.
- **Clear Outcome** — Exactly one coherent, immutable snapshot per cycle; every section carries its own timestamp; consumers never see a partially-mutating snapshot.

### 1.5.2 Freshness / staleness tagging
- **Responsibility** — Attach an age and a fresh/stale verdict to every field, against (configurable) thresholds.
- **Behavior / Actions** — Compute age = snapshot-clock − field's last-update; compare to per-field-type thresholds; tag FRESH / AGING / STALE; aggregate to a section and snapshot verdict.
- **Scenarios & Possibilities** — Spot fresh but chain stale (or one strike stale, rest fresh); everything stale (feed dead) vs. a single illiquid strike stale; threshold differs by field type (spot vs deep-OTM option vs candle); near-open warm-up where nothing is "fresh" yet; clock skew (1.1.5) distorting age.
- **Functional Test Case(s)** — *Given* a field last updated longer ago than its staleness threshold, *When* tagged, *Then* it is STALE and its section/snapshot verdict reflects the worst included field. *Given* one deep-OTM strike is stale while spot+ATM are fresh, *Then* only that strike is STALE and the snapshot remains usable-with-flags.
- **Clear Outcome** — Every field has a truthful age and freshness tag; the snapshot's overall verdict honestly reflects its worst load-bearing field; staleness is surfaced, never hidden.

### 1.5.3 Missing-data & partial-snapshot policy
- **Responsibility** — Define what the module emits when parts of the picture are unavailable.
- **Behavior / Actions** — Distinguish "absent" from "stale" from "zero"; decide per-section whether a partial snapshot is usable; set the overall verdict (USABLE / DEGRADED / UNUSABLE); never substitute fabricated values for missing ones.
- **Scenarios & Possibilities** — Chain present but spot missing (greeks unanchored); candles missing but chain+spot fine; only ATM strikes available; everything missing at cold start; transient single-cycle miss vs. persistent outage.
- **Functional Test Case(s)** — *Given* spot is missing but chain and candles are present, *When* the verdict is set, *Then* it is DEGRADED (or UNUSABLE if spot is load-bearing) with spot explicitly marked absent — not defaulted. *Given* all three sections are absent, *Then* verdict = UNUSABLE.
- **Clear Outcome** — Missing data is always explicit and typed (absent vs stale vs zero); the verdict communicates usability; no fabricated fill-ins ever leave the module.

---

## 1.6 Data Quality & Sanity

### 1.6.1 Range & sanity checks
- **Responsibility** — Validate each value lies in a plausible range before it enters a snapshot.
- **Behavior / Actions** — Check non-negativity (prices, IV, vega, gamma), bounds (|delta| ≤ 1, IV within a sane band, theta sign), and per-tick move plausibility vs. circuit bands; flag or reject violators.
- **Scenarios & Possibilities** — Zero price (real for far-OTM vs. bad tick); IV of 0 or 1000%; delta outside [−1,1] from a bad solve/feed; negative theta convention vs. positive; a legit limit move flagged as implausible; near-expiry greeks legitimately extreme.
- **Functional Test Case(s)** — *Given* a strike whose vended delta is 1.4, *When* range-checked, *Then* it is flagged invalid and either recomputed or marked unavailable. *Given* an IV of 0% on a clearly-extrinsic option, *Then* it is flagged.
- **Clear Outcome** — Out-of-range values never silently enter a snapshot; each is flagged/rejected with a reason; legitimate extremes (expiry, limit moves) are distinguished from corruption.

### 1.6.2 Cross-field consistency
- **Responsibility** — Verify relationships *between* fields hold.
- **Behavior / Actions** — Check candle invariants (L ≤ O,C ≤ H; L ≤ H); option price ≥ intrinsic; put-call parity sanity at ATM; greeks consistent with the IV used (1.3.4); spot vs ATM-strike coherence; chain ordering monotonic where expected.
- **Scenarios & Possibilities** — H < L from a 0-poisoned bar (links to 1.4.1); price below intrinsic (arb or bad data); parity violated by stale one leg; greeks from a different IV than stored; spot far from any chain strike (wrong expiry/strike map).
- **Functional Test Case(s)** — *Given* a candle with low=0 while open/close are positive, *When* consistency-checked, *Then* the invariant L ≤ O,C is violated → bar flagged and excluded/repaired. *Given* an option last price below its intrinsic value, *Then* the leg is flagged.
- **Clear Outcome** — Internally contradictory snapshots are caught before emission; violations are flagged with the specific invariant broken; obviously-corrupt rows are quarantined (1.6.3).

### 1.6.3 Quality flagging & quarantine
- **Responsibility** — Carry quality verdicts on the snapshot and isolate bad data rather than discarding the whole snapshot.
- **Behavior / Actions** — Attach per-field/section quality flags; quarantine individual bad strikes/bars while keeping the rest; count and rate-track anomalies; escalate to UNUSABLE only when load-bearing data is bad.
- **Scenarios & Possibilities** — One bad strike in an otherwise good chain (quarantine the strike, keep the chain); systemic corruption (many fields bad → whole snapshot suspect); intermittent vs. persistent bad strike; quarantine that would remove an ATM strike the strategy needs (escalate vs. degrade).
- **Functional Test Case(s)** — *Given* one corrupt deep-OTM strike among many good ones, *When* quarantined, *Then* that strike is excluded/flagged and the snapshot remains USABLE-with-flags. *Given* corruption across most ATM strikes, *Then* the snapshot verdict escalates to UNUSABLE.
- **Clear Outcome** — Bad data is isolated at the finest granularity possible; the rest of the snapshot survives; the verdict escalates only when truly necessary; an anomaly trail exists for audit.

---

## 1.7 Cadence & Throttling

### 1.7.1 Cycle pacing & coalescing
- **Responsibility** — Control how often snapshots are produced and coalesce bursts.
- **Behavior / Actions** — Produce on the cycle cue (clock or tick); if requests/ticks arrive faster than the target cadence, coalesce to the latest; if slower, still emit on schedule with freshness reflecting the gap; backpressure when upstream floods.
- **Scenarios & Possibilities** — Tick storm (thousands/sec) → must not emit thousands of snapshots; quiet period → cadence still ticks, snapshots just age; downstream slower than production (backpressure); a forced/manual refresh request mid-cadence; cadence faster than feed updates (repeated identical snapshots → dedupe in 1.7.2).
- **Functional Test Case(s)** — *Given* ticks arrive at 10× the target cadence, *When* producing, *Then* only one snapshot per cadence interval is emitted, built from the latest data (coalesced). *Given* no ticks for several intervals, *Then* snapshots still emit on schedule with rising staleness.
- **Clear Outcome** — Snapshot production rate is bounded and predictable regardless of feed burstiness; bursts are coalesced to latest; quiet periods produce honestly-aged snapshots.

### 1.7.2 Caching & dedupe
- **Responsibility** — Reuse computed results and avoid redundant work/emission.
- **Behavior / Actions** — Cache expensive derivations (IV/greeks solves, rollups) keyed by inputs; reuse when inputs unchanged; suppress or mark duplicate identical snapshots; invalidate cache on new ticks or expiry/strike-set change.
- **Scenarios & Possibilities** — Stale cache served after inputs changed (invalidation bug); cache key omits a relevant input (e.g., time-to-expiry) → wrong reuse on expiry day; memory growth from unbounded cache; recomputation thrash when inputs flap; duplicate snapshot emitted as "new" misleading freshness.
- **Functional Test Case(s)** — *Given* an IV/greeks solve cached for a strike, *When* the underlying option price is unchanged but time-to-expiry advanced past the cache's tolerance, *Then* the cache is invalidated and greeks recomputed (no stale-T reuse). *Given* two consecutive cycles with identical inputs, *Then* the second reuses cache and the snapshot is marked unchanged.
- **Clear Outcome** — Expensive work is reused safely; cache invalidation correctly accounts for time-to-expiry and tick changes; no stale or wrongly-keyed result is ever served; memory is bounded.

---

## Suggestions (for bubble-up)

Market-condition scenarios this module *touches* but which likely need **system-wide** policy (raised here, not decided here):

1. **Gap-open day.** First prints far from prior close; warm-up period where nothing is "fresh" and candles have no prior close to anchor. System should define how the broader strategy treats a snapshot that is structurally young at open.
2. **Expiry-day behavior.** Time-to-expiry → 0 makes IV/greeks ill-conditioned (gamma/theta blow-up, solver instability). Whether to compute, suppress, or special-case greeks near expiry is a cross-module decision; this module can only flag it.
3. **Low-IV vs. high-IV regimes.** Affects solver conditioning and the plausibility bands used in 1.6.1. A regime-aware threshold set may belong system-wide.
4. **Circuit / halt / limit moves.** A halt looks like "no ticks" (1.4.4) and a limit move looks like a bad spot (1.2.2). The system needs a halt/circuit signal so this module can label flatness as halt rather than no-trade or feed-outage.
5. **Illiquid / no-trade strikes.** Deep-OTM strikes may never trade; the module flags them stale/no-data, but whether the strategy should even request them (and how wide the strike window is) is a system-level liquidity question.
6. **Data-feed outage vs. quiet market.** Persistent staleness across all fields = outage; sparse ticks = quiet. The module distinguishes them, but the system must decide trading-halt / safe-mode policy on UNUSABLE snapshots.
7. **Source disagreement / failover provenance.** When primary and secondary disagree (1.1.4), which is canonical and how much divergence is tolerable is a risk/governance decision above this module.
8. **Symbology drift across index families.** SENSEX-family vs. NIFTY-family option symbol formats differ; a vendor schema change (1.1.3) is a system-resilience concern worth central monitoring.
9. **Clock-skew / time-source trust.** Large feed-vs-local skew (1.1.5) silently corrupts freshness and bar placement everywhere; a system-wide time-trust policy is warranted.
10. **Rate/dividend assumptions for IV/greeks.** The risk-free rate used in local IV inversion is a shared input; centralizing it avoids per-module drift.
