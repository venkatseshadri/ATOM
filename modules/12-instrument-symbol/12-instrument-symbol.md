# Module 12 — Instrument & Symbol Master

## 12.0 Module Overview

**Role.** Module 12 is the authoritative source of option-contract *metadata* for the
intraday index-options system. Given an index identifier (NIFTY or SENSEX), the
underlying spot, and an expiry-selection rule, it must:

1. Load and keep fresh a broker **instrument master** (the dump of every tradable contract).
2. Resolve the **weekly expiry date** (handling holiday shifts and expiry-day rollover).
3. Build the **strike ladder** around spot (interval, ATM, OTM/ITM offsets).
4. Provide **lot size** and **tick size** per index.
5. Emit the exact broker **tradingsymbol** for each contract — and NIFTY vs SENSEX use
   *different* symbol formats, lot sizes, and expiry conventions.
6. Validate that a requested contract **actually exists** in the master.
7. Hand back a **reference to a greeks/IV source** for those contracts.

**Boundary / contract.** This module is *pure metadata resolution*. It does not price,
does not decide strategy, does not place orders, does not stream ticks. Inputs are plain
data; outputs are plain contract objects + the resolved ladder + a greeks/IV source ref.
Everything downstream trusts that a contract object emitted here is real, tradable,
correctly formatted, and current for *today's* session. A single wrong character in a
tradingsymbol, or a stale expiry, silently routes an order to the wrong (or non-existent)
contract — so this module's correctness is load-bearing for the whole system.

**Design tensions surfaced (explored across the tree):**
- *Source of truth* for expiry/strike-interval/lot: derive locally (rules) vs. trust the
  broker master (data) vs. cross-check both. We favour **master-as-truth, rules-as-validator**.
- *Refresh model:* load-once-at-startup vs. periodic vs. event-driven (corporate action /
  intraday new-strike). Index options need at least daily refresh + intraday top-up.
- *Failure posture:* fail-closed (no contract → emit nothing, let caller skip) vs.
  best-effort. For a money path, **fail-closed and loud**.

**Decomposition map:**
- 12.1 Instrument Master Loading & Refresh
- 12.2 Weekly Expiry Resolution
- 12.3 Strike Ladder Construction
- 12.4 Lot & Tick Lookup
- 12.5 Per-Index Tradingsymbol Formatting
- 12.6 Contract Validity & Existence Checks
- 12.7 Greeks/IV Source Reference
- 12.8 Staleness, Health & Self-Audit

---

## 12.1 Instrument Master Loading & Refresh

### 12.1.1 Master Fetch / Acquisition
- **Responsibility** — Obtain the raw broker instrument dump (the universe of contracts) for the day.
- **Behavior / Actions**
  - Pull the instrument master from the broker source (download/API/local cache) at session warm-up.
  - Apply timeout + bounded retries with backoff; record fetch timestamp and source URI.
  - On success, hand the raw payload to the parser (12.1.2). On hard failure, surface an error and fall back to the last-known-good cache (flagged stale).
- **Scenarios & Possibilities**
  - Source reachable, fresh dump → normal.
  - Source 5xx / network timeout → retry, then fall back to cached copy.
  - Partial/truncated download → checksum/row-count sanity must reject it.
  - Empty file or HTML error page served as the "dump" → must not be parsed as data.
  - Two indices share one combined dump vs. separate per-segment dumps.
- **Functional Test Case(s)**
  - Given a reachable source, When fetch runs, Then a non-empty payload with a recorded timestamp is returned.
  - Given the source times out on all retries, When a valid cache exists, Then the cache is returned and marked `stale=true`.
  - Given a truncated payload (row count << expected), When validated, Then fetch is rejected (no silent accept).
- **Clear Outcome** — A complete raw master payload (or an explicitly-flagged stale fallback) with provenance metadata; never a silently-empty or corrupt accept.

### 12.1.2 Parsing & Normalization
- **Responsibility** — Convert the raw dump into a uniform internal contract record set.
- **Behavior / Actions**
  - Parse rows; filter to the relevant segment (index options for NIFTY/SENSEX).
  - Normalize fields: index, expiry (→ date), strike (→ int/number), option type (CE/PE), lot size, tick size, broker tradingsymbol, instrument token/id.
  - Coerce types defensively (strike as number not string; expiry parsed with explicit format/timezone = IST).
  - Drop non-option / non-target rows; keep a count of accepted vs. dropped.
- **Scenarios & Possibilities**
  - Schema drift: broker adds/renames/reorders a column → parser must key by name, not position.
  - Locale/format surprises: strike with thousands separators; expiry in a new date format.
  - Mixed weekly + monthly contracts in same dump → both retained, distinguished by expiry.
  - Duplicate rows for same contract → dedupe.
  - Encoding issues (BOM, non-UTF8).
- **Functional Test Case(s)**
  - Given a dump with columns reordered, When parsed, Then fields resolve by name and records are correct.
  - Given a row with strike `"24,500"`, When normalized, Then strike == 24500 (number).
  - Given duplicate identical rows, When parsed, Then a single record remains.
  - Given an unexpected new column, When parsed, Then known fields still resolve and the unknown column is ignored without crash.
- **Clear Outcome** — A typed, deduped, segment-filtered set of contract records with explicit IST-parsed expiries; parse drops are counted and logged.

### 12.1.3 Indexing & Lookup Structures
- **Responsibility** — Build fast lookup indexes over the normalized records.
- **Behavior / Actions**
  - Build keyed maps: by tradingsymbol; by (index, expiry, strike, CE/PE); by instrument token.
  - Precompute the set of distinct expiries per index and distinct strikes per (index, expiry).
  - Keep the structure immutable per refresh; swap atomically on reload.
- **Scenarios & Possibilities**
  - Key collision (two records same composite key) → must be impossible post-dedupe; assert if seen.
  - Lookup miss for a requested key → return "not found" cleanly (feeds 12.6).
  - Concurrent read during a refresh swap → atomic pointer swap avoids torn reads.
- **Functional Test Case(s)**
  - Given indexes built, When looking up (NIFTY, expiry, 24500, CE), Then the matching contract returns in O(1).
  - Given a swap mid-read, When a reader holds the old map, Then it reads a consistent snapshot (no partial map).
- **Clear Outcome** — Constant-time, collision-free lookups by symbol and by composite key, with atomic refresh swaps.

### 12.1.4 Refresh Cadence & Reload Trigger
- **Responsibility** — Decide *when* to reload the master.
- **Behavior / Actions**
  - Scheduled daily reload before session start (pre-open).
  - Optional intraday top-up reloads (for newly-listed strikes — see 12.3 / Suggestions).
  - Event triggers: explicit invalidate (corporate action, contract-list change), or staleness detector (12.8) firing.
  - Guard against reload storms (min interval between reloads).
- **Scenarios & Possibilities**
  - Process started mid-session (restart) → must load immediately, not wait for next pre-open.
  - Reload fails → keep serving previous good snapshot, flagged stale, alert.
  - Spot moves far intraday beyond the loaded ladder; new strikes listed → top-up needed.
  - Holiday: no new dump published → yesterday's may be acceptable IF expiry logic still correct.
- **Functional Test Case(s)**
  - Given a cold start at 11:00, When the module initializes, Then it loads the master immediately.
  - Given a scheduled pre-open reload, When the dump is unchanged, Then the snapshot updates timestamp without disrupting readers.
  - Given two reload requests 1s apart, When the min-interval guard is set, Then only one reload executes.
- **Clear Outcome** — The master is current for the trading day, reloads at defined cadence/events, and never reloads so often it thrashes.

---

## 12.2 Weekly Expiry Resolution

### 12.2.1 Base Weekly Expiry Computation (per index)
- **Responsibility** — Compute the nominal weekly expiry weekday for the given index.
- **Behavior / Actions**
  - Apply the index's weekly-expiry weekday convention (NIFTY and SENSEX differ; conventions also change over time — treat the weekday as configurable/data-driven, not hardcoded).
  - From "today", compute the nearest upcoming nominal weekly expiry date for that index.
  - Prefer to *confirm* the computed date against the distinct-expiries set from the master (12.1.3) rather than trust the calendar blindly.
- **Scenarios & Possibilities**
  - Exchange changes the weekly expiry weekday (has happened repeatedly) → config/data must absorb it without code change.
  - Computed weekday not present in master (because shifted/holiday) → must reconcile to an actual listed expiry.
  - Index with no weekly (only monthly) in some regime → fall back to monthly per rule.
- **Functional Test Case(s)**
  - Given today is mid-week and the index weekly weekday is configured, When base expiry computed, Then it equals the next occurrence of that weekday.
  - Given the computed date is absent from the master's expiry set, When resolving, Then the module reconciles to the closest actually-listed expiry (12.2.2).
- **Clear Outcome** — A candidate weekly expiry weekday/date per index, derived from data-driven config and reconciled against listed expiries.

### 12.2.2 Holiday-Shift Adjustment
- **Responsibility** — Adjust the nominal expiry when the expiry day is a market holiday.
- **Behavior / Actions**
  - Check the nominal expiry date against the trading-holiday calendar.
  - If holiday, shift per exchange rule (typically to the previous trading day) — and re-check that the shifted day isn't also a holiday (multi-day holiday clusters).
  - Cross-validate the final date exists in the master's expiry set.
- **Scenarios & Possibilities**
  - Single holiday on expiry day → shift to prior trading day.
  - Holiday cluster (expiry day + prior day both holidays) → shift back multiple days.
  - Special/ad-hoc trading day or unscheduled holiday (e.g. muhurat, sudden closure) → calendar must be authoritative and current.
  - Calendar stale/missing the current year → must fail-closed, not silently use a wrong date.
  - Holiday calendar disagrees with broker master → trust the master's listed expiry, alert on mismatch.
- **Functional Test Case(s)**
  - Given the nominal expiry is a holiday, When adjusted, Then the resolved expiry is the previous trading day.
  - Given both the nominal day and the prior day are holidays, When adjusted, Then it shifts to the first earlier trading day.
  - Given the holiday calendar lacks the current year, When resolving, Then the module errors/fails-closed rather than guessing.
  - Given calendar-derived date ≠ any master expiry, When reconciling, Then the master's listed expiry wins and a mismatch alert is raised.
- **Clear Outcome** — Resolved expiry is always an actual tradable session date, holiday-correct, and consistent with the master; ambiguity fails closed.

### 12.2.3 Expiry-Selection Rule Application
- **Responsibility** — Apply the caller's expiry-selection rule (e.g. nearest / next / specific) to the candidate set.
- **Behavior / Actions**
  - Take the ordered set of upcoming expiries for the index from the master.
  - Apply the rule: nearest weekly, next weekly (skip current), Nth, or an explicit date.
  - Return the single chosen expiry date.
- **Scenarios & Possibilities**
  - "Nearest" on expiry day before cutoff → still today's expiry; after rollover → next (see 12.2.4).
  - "Next weekly" requested but only monthly remains in a given week → rule must define fallback.
  - Explicit-date rule for a date not in the master → not-found error.
- **Functional Test Case(s)**
  - Given rule="nearest" and three upcoming expiries, When applied, Then the soonest is returned.
  - Given rule="next" on a non-expiry day, When applied, Then the second-soonest weekly is returned.
  - Given rule=explicit-date absent from master, When applied, Then a not-found error is raised.
- **Clear Outcome** — Exactly one resolved expiry matching the rule, drawn from real listed expiries.

### 12.2.4 Expiry-Day Rollover Logic
- **Responsibility** — On expiry day, switch "nearest" from the expiring contract to the next series at the right moment.
- **Behavior / Actions**
  - Detect that today == resolved nearest expiry.
  - Apply a rollover policy: for an intraday-flat system, the current expiry is typically still valid intraday until square-off; "nearest for new entries" may roll to next series near/after close.
  - Make the rollover boundary explicit and deterministic (date/phase-driven, not wall-clock-fuzzy).
- **Scenarios & Possibilities**
  - Mid-day on expiry day: should new entries use today's expiry (0-DTE) or next week? Policy must be explicit.
  - Just after close on expiry day: "nearest" must already mean next week to avoid resolving a dead contract tomorrow.
  - Restart on expiry day → rollover state must be recomputed from the date, not from in-memory flags.
- **Functional Test Case(s)**
  - Given today is the resolved expiry and it's an active session, When "nearest" is requested, Then today's expiry is returned (0-DTE) per policy.
  - Given it is after the rollover boundary on expiry day, When "nearest" is requested, Then next week's expiry is returned.
  - Given a restart after the boundary, When resolving, Then rollover is correctly derived from the date alone.
- **Clear Outcome** — "Nearest" never resolves to an already-expired contract; rollover is deterministic and restart-safe.

---

## 12.3 Strike Ladder Construction

### 12.3.1 Strike-Interval Determination (per index)
- **Responsibility** — Determine the strike spacing for the index/expiry.
- **Behavior / Actions**
  - Prefer deriving the interval empirically from sorted distinct strikes in the master for that (index, expiry); fall back to configured interval if data sparse.
  - NIFTY and SENSEX have different spacings; spacing can also differ near vs. far from ATM, and can change over time — derive, don't hardcode.
- **Scenarios & Possibilities**
  - Wider spacing in deep wings than near ATM → interval is not globally constant.
  - Sparse listed strikes early in a new series → empirical derivation unreliable; use config fallback.
  - Exchange changes interval → empirical derivation absorbs it automatically.
- **Functional Test Case(s)**
  - Given dense listed strikes, When interval derived, Then it equals the modal gap between adjacent strikes near ATM.
  - Given only two listed strikes, When derivation is unreliable, Then the configured fallback interval is used and flagged.
- **Clear Outcome** — A correct near-ATM strike interval per (index, expiry), data-derived with safe fallback.

### 12.3.2 ATM Resolution from Spot
- **Responsibility** — Map the underlying spot to the at-the-money strike.
- **Behavior / Actions**
  - Round spot to the nearest listed strike using the derived interval.
  - Tie-breaking rule explicit (e.g. round half up / to nearest listed).
  - Snap result to an actually-listed strike, not a computed-but-unlisted one.
- **Scenarios & Possibilities**
  - Spot exactly halfway between two strikes → deterministic tie-break required.
  - Computed ATM strike not listed (gap in ladder) → snap to nearest listed.
  - Spot value stale/zero/negative (bad feed) → reject, do not emit a garbage ATM.
  - Spot far outside listed ladder (gap up/down) → ATM at ladder edge; flag need for ladder top-up.
- **Functional Test Case(s)**
  - Given spot 24,512 and interval 50, When ATM resolved, Then ATM = 24500.
  - Given spot exactly on a half-boundary, When resolved, Then the documented tie-break is applied deterministically.
  - Given spot = 0 (bad feed), When resolving, Then ATM resolution errors out, emitting no contract.
  - Given the rounded ATM isn't listed, When resolving, Then it snaps to the nearest listed strike.
- **Clear Outcome** — ATM is a single, listed, deterministically-chosen strike; invalid spot fails closed.

### 12.3.3 Ladder Generation Around ATM
- **Responsibility** — Build the ordered strike ladder spanning a requested range around ATM.
- **Behavior / Actions**
  - Generate strikes ATM ± N intervals (CE and PE as required).
  - Intersect generated strikes with listed strikes; only include contracts that actually exist.
  - Return ladder ordered, with ATM marked.
- **Scenarios & Possibilities**
  - Requested range exceeds listed strikes (wings not listed yet) → return the available subset, flag truncation.
  - Asymmetric listing (more strikes above than below) → ladder may be lopsided.
  - Non-uniform spacing in wings → generation must walk listed strikes, not blindly add a constant.
  - Intraday new strikes appear as spot trends → ladder should expand on top-up.
- **Functional Test Case(s)**
  - Given ATM=24500, interval 50, N=5, When ladder built, Then it contains the 11 listed strikes 24250..24750 (CE/PE) centered on ATM.
  - Given the top 2 requested wing strikes aren't listed, When built, Then the ladder returns the available strikes and flags truncation.
  - Given non-uniform wing spacing, When built, Then ladder follows actual listed strikes, not arithmetic ATM±k·interval.
- **Clear Outcome** — An ordered ladder of real, listed contracts around ATM, truncation made explicit, never inventing unlisted strikes.

### 12.3.4 OTM/ITM Offset Resolution
- **Responsibility** — Resolve a strike by directional offset (e.g. "ATM+3 OTM call", "2 strikes ITM put").
- **Behavior / Actions**
  - Translate an offset request into a concrete strike using listed strikes and correct CE/PE directionality (OTM call = higher strike; OTM put = lower strike).
  - Validate the resolved strike exists; return the contract.
- **Scenarios & Possibilities**
  - Sign/direction confusion: OTM vs ITM inverted between calls and puts → classic bug.
  - Offset walks off the listed ladder → not-found / clamp per policy (explicit).
  - Counting by index position vs. by price step when spacing is non-uniform → must be unambiguous (count listed strikes).
- **Functional Test Case(s)**
  - Given ATM=24500 and "OTM+2 call", When resolved, Then strike = 24600 (CE).
  - Given ATM=24500 and "OTM+2 put", When resolved, Then strike = 24400 (PE).
  - Given an offset beyond the listed ladder, When resolved, Then a not-found (or documented clamp) result, never a wrong-direction strike.
- **Clear Outcome** — Offset → correct, listed strike with correct CE/PE directionality; direction errors are impossible by construction.

---

## 12.4 Lot & Tick Lookup

### 12.4.1 Lot-Size Lookup
- **Responsibility** — Provide the contract lot size for the index/expiry.
- **Behavior / Actions**
  - Read lot size from the master record for the specific contract (master-as-truth), not a hardcoded constant.
  - NIFTY and SENSEX differ; lot size changes over time → must come from the live master.
  - Expose lot size on every emitted contract object.
- **Scenarios & Possibilities**
  - Exchange revises lot size (effective from a date) → master reflects it; a hardcoded value would silently mis-size every order.
  - Different lot size for different expiries during a transition → per-contract, not per-index, lookup.
  - Master row missing lot size → fail-closed (don't default to a guess that mis-sizes risk).
- **Functional Test Case(s)**
  - Given a contract record with lot size L, When queried, Then the emitted contract carries lot size L.
  - Given a lot-size change effective this series, When queried for that series, Then the new value is returned (not the old constant).
  - Given a record missing lot size, When queried, Then an error is raised (no silent default).
- **Clear Outcome** — Every contract carries the correct, master-sourced, per-contract lot size; missing data fails closed.

### 12.4.2 Tick-Size Lookup
- **Responsibility** — Provide the price tick size for the contract.
- **Behavior / Actions**
  - Read tick size from the master per contract; attach to the contract object.
  - Default sanity-bound (tick > 0) check.
- **Scenarios & Possibilities**
  - Tick size differs by segment/index or changes → must be data-driven.
  - Missing/zero tick → reject (downstream price rounding would divide-by-zero or mis-round).
- **Functional Test Case(s)**
  - Given a record with tick T, When queried, Then the contract carries tick T.
  - Given tick == 0 or missing, When queried, Then an error/flag is raised.
- **Clear Outcome** — Every contract carries a valid positive tick size from the master.

---

## 12.5 Per-Index Tradingsymbol Formatting

### 12.5.1 NIFTY Tradingsymbol Formatter
- **Responsibility** — Produce the exact broker tradingsymbol for a NIFTY option contract.
- **Behavior / Actions**
  - Compose per the broker's NIFTY weekly convention (underlying root + expiry encoding + strike + CE/PE), matching the broker master's exact spelling/casing/padding.
  - Prefer to *reuse the master's own tradingsymbol* for the resolved (expiry, strike, CE/PE); use the formatter only as a constructor/validator, since brokers' weekly encodings (month letter / day / week markers) are notoriously fiddly.
- **Scenarios & Possibilities**
  - Weekly vs. monthly encodings differ (e.g. month-letter vs. full-month).
  - Year/month/day zero-padding and casing pitfalls.
  - Broker changes the encoding scheme → reused-from-master approach is resilient; pure-constructor approach breaks.
  - Strike formatting (integer vs. with decimals).
- **Functional Test Case(s)**
  - Given a resolved NIFTY weekly contract, When formatted, Then the symbol exactly equals the master's tradingsymbol for that contract (round-trip).
  - Given a constructed symbol, When looked up in the master, Then it resolves to exactly one contract.
- **Clear Outcome** — NIFTY tradingsymbol is byte-for-byte what the broker expects; constructor output always round-trips against the master.

### 12.5.2 SENSEX Tradingsymbol Formatter
- **Responsibility** — Produce the exact broker tradingsymbol for a SENSEX option contract.
- **Behavior / Actions**
  - Compose per the SENSEX convention, which differs from NIFTY (different root and encoding; recall the known SENSEX symbol-format pitfall where the naive format is wrong).
  - Same master-reuse-and-validate discipline as 12.5.1.
- **Scenarios & Possibilities**
  - Applying the NIFTY format to SENSEX (or vice-versa) → produces a plausible-but-wrong symbol that may resolve to a different/non-existent contract — high-severity, silent.
  - SENSEX-specific root/encoding quirks.
  - Different weekly weekday/expiry encoding than NIFTY.
- **Functional Test Case(s)**
  - Given a resolved SENSEX weekly contract, When formatted, Then the symbol exactly equals the master's tradingsymbol (round-trip).
  - Given the NIFTY formatter is mistakenly applied to a SENSEX contract, When validated against the master, Then it fails the existence check (caught, not traded).
- **Clear Outcome** — SENSEX tradingsymbol is exactly broker-correct; cross-format application is detected and rejected.

### 12.5.3 Format Validation / Round-Trip Check
- **Responsibility** — Guarantee any emitted symbol resolves back to the intended contract.
- **Behavior / Actions**
  - For every formatted symbol, look it up in the master; assert it maps to exactly one contract with matching (index, expiry, strike, CE/PE).
  - Reject emission on any mismatch (wrong index format, wrong expiry encoding, unlisted strike).
- **Scenarios & Possibilities**
  - Symbol resolves to a *different* expiry/strike than intended → must be caught by field-equality, not just existence.
  - Symbol resolves to nothing → not-found.
  - Symbol resolves to multiple (ambiguous) → assert single match.
- **Functional Test Case(s)**
  - Given a formatted symbol whose intended strike ≠ resolved strike, When validated, Then emission is rejected with a clear error.
  - Given a well-formed symbol, When validated, Then it resolves to exactly one contract with all fields matching.
- **Clear Outcome** — No tradingsymbol leaves the module unless it provably resolves to the exact intended contract.

---

## 12.6 Contract Validity & Existence Checks

### 12.6.1 Existence Against Master
- **Responsibility** — Confirm a requested/derived contract actually exists and is tradable today.
- **Behavior / Actions**
  - Look up the composite key (index, expiry, strike, CE/PE) in the master index.
  - Verify the contract's expiry is the resolved/active one (not an expired or far-month accident).
  - Return a validated contract object or a clean not-found.
- **Scenarios & Possibilities**
  - Strike/expiry combination simply not listed → not-found (caller must skip, not retry forever).
  - Contract exists but for a different (stale) master snapshot → re-validate against current snapshot.
  - Suspended/illiquid-but-listed contract → exists in master; liquidity is out of this module's scope (flag, don't block — or expose a tradable flag if master provides it).
- **Functional Test Case(s)**
  - Given a listed (NIFTY, expiry, 24500, CE), When existence-checked, Then a validated contract returns.
  - Given an unlisted strike, When checked, Then not-found is returned cleanly.
  - Given a contract from a previous snapshot, When the master refreshed, Then it is re-validated against the current snapshot.
- **Clear Outcome** — Only contracts present in the current master are emitted; absent ones return an unambiguous not-found.

### 12.6.2 Output Contract Object Assembly
- **Responsibility** — Assemble the canonical output contract object.
- **Behavior / Actions**
  - Bundle: tradingsymbol, expiry, strike, CE/PE, lot size, tick size, instrument token/id, index.
  - Attach the greeks/IV source reference (12.7).
  - Mark snapshot provenance (which master refresh produced it) and staleness flag.
- **Scenarios & Possibilities**
  - A partially-resolved contract (e.g. missing tick) → must not be emitted as "complete".
  - Field consistency: strike/expiry in the object must equal those encoded in the tradingsymbol.
- **Functional Test Case(s)**
  - Given all fields resolved, When assembled, Then the object contains every required field plus provenance and a greeks/IV ref.
  - Given any required field missing, When assembled, Then assembly fails rather than emitting a partial object.
- **Clear Outcome** — A complete, self-consistent, provenance-stamped contract object — or nothing.

---

## 12.7 Greeks / IV Source Reference

### 12.7.1 Greeks/IV Source Resolution
- **Responsibility** — Provide a reference (handle/key) to where greeks/IV for a contract can be obtained.
- **Behavior / Actions**
  - Map the contract (token/symbol) to the identifier the greeks/IV provider expects.
  - Return a reference object/key — not the greeks themselves (this module does not compute or fetch values).
- **Scenarios & Possibilities**
  - Greeks source keyed by instrument token vs. tradingsymbol → must hand back the correct key type.
  - Contract has no greeks source (newly listed, illiquid) → return a clearly-null reference, not a fabricated one.
  - Source identifier scheme differs NIFTY vs. SENSEX.
- **Functional Test Case(s)**
  - Given a valid contract, When the greeks ref is resolved, Then it carries the exact key the provider expects.
  - Given a contract with no available greeks source, When resolved, Then a null/absent reference is returned (not invented).
- **Clear Outcome** — Each emitted contract carries a correct, provider-compatible greeks/IV reference, or an explicit none.

---

## 12.8 Staleness, Health & Self-Audit

### 12.8.1 Master Staleness Detection
- **Responsibility** — Detect when the loaded master is too old to trust.
- **Behavior / Actions**
  - Compare the snapshot's fetch date against the current trading date; flag if not today's (ex-holiday allowance).
  - Expose a `stale` flag and last-refresh timestamp on outputs.
  - Optionally block emission when stale beyond a threshold (fail-closed policy).
- **Scenarios & Possibilities**
  - Yesterday's master used today because reload failed → must be flagged; expiry/strike may be wrong.
  - Long weekend/holiday: a 1–3 day old master may be legitimately current → staleness must be holiday-aware.
  - Clock skew on the host → date checks must use a reliable time source.
- **Functional Test Case(s)**
  - Given a master fetched yesterday on a normal trading day, When checked, Then `stale=true` is flagged.
  - Given a master fetched on the last trading day before a holiday cluster, When checked today (still pre next session), Then it is not falsely flagged stale.
- **Clear Outcome** — Stale masters are reliably detected (holiday-aware) and surfaced; downstream can fail-closed.

### 12.8.2 Cross-Validation / Self-Audit
- **Responsibility** — Cross-check derived values (expiry, interval, lot) against the master to catch silent drift.
- **Behavior / Actions**
  - On each refresh, assert: resolved expiry ∈ master expiries; derived interval matches modal listed gap; lot size matches master; ATM strike is listed.
  - Emit a health report (counts, mismatches, last refresh) for the system health surface.
- **Scenarios & Possibilities**
  - Rule-derived expiry disagrees with master (exchange changed weekday) → audit catches it, master wins, alert.
  - Hardcoded lot vs. master lot mismatch → audit catches a config that would mis-size risk.
  - Symbol-format round-trip failures counted here as a regression signal.
- **Functional Test Case(s)**
  - Given a config weekday that no longer matches the listed expiries, When the self-audit runs, Then a mismatch is reported and the master value is used.
  - Given a stale hardcoded lot size, When audited against the master, Then the discrepancy is flagged.
- **Clear Outcome** — Silent metadata drift (expiry weekday, lot size, interval, symbol format) is caught at refresh time, not at order time.

---

## Suggestions (for bubble-up)

These scenarios exceed Module 12's boundary and deserve **system-wide** treatment / review:

1. **Holiday-shifted expiry** — The trading-holiday calendar (and ad-hoc closures) is a
   cross-cutting concern; expiry resolution here, square-off timing, and 0-DTE risk all
   depend on the *same* authoritative, current calendar. Recommend a single shared
   holiday-calendar service with a freshness SLA; this module should consume, not own it.

2. **Symbol-format mismatch causing the wrong contract** — Applying the NIFTY format to
   SENSEX (or stale broker encoding) produces a *plausible-but-wrong* symbol that can
   silently resolve to a different/non-existent contract — a high-severity, money-losing,
   silent failure. The module's round-trip-against-master guard (12.5.3) is the local
   defence, but the system should add an independent pre-trade assertion that the order's
   symbol re-resolves to the intended (index, expiry, strike, CE/PE) before placement.

3. **Lot-size change** — An exchange lot-size revision silently changes notional/position
   sizing and defined-risk math everywhere downstream. Must be master-sourced (never
   hardcoded) AND surfaced to risk/sizing modules with an explicit change event, not just
   absorbed quietly here.

4. **New strikes introduced intraday** — As spot trends, the exchange lists new strikes
   mid-session. The strike ladder can go stale within the day, capping the wings the
   strategy can reach. Needs an intraday master top-up (12.1.4 / 12.3.3) *and* a system
   decision on whether entries are allowed to depend on freshly-listed, possibly-illiquid
   strikes.

5. **Stale master fail-closed policy** — Whether a stale/last-known-good master may be used
   for live entries is a risk-policy decision above this module. Module 12 should expose the
   staleness flag; the system must decide the trading posture (block new entries vs. allow).

6. **Expiry-day rollover boundary** — The exact moment "nearest" flips to next week on
   expiry day (and whether 0-DTE entries are permitted) is a strategy/risk decision, not a
   metadata one. This module makes it deterministic; the policy value belongs to the system.
