# Module 16 — Config & ParameterSet

## 16.0 Module Overview

**Mission.** Module 16 is the *single source of truth* for everything the trading
system treats as "settings": static configuration (operational knobs that change
rarely) and the **frozen parameter set** (the bundle of fixed numbers — signal
weights, thresholds, greek bands, stop levels, sizing — that the strategy runs for
exactly one trading session). It does **not** compute parameters; an external,
offline, human-approved process produces a candidate set and hands it over. Module
16's job is to **ingest, validate, version, freeze, serve, swap, and roll back**
those sets safely.

**Core invariant — "one truth, frozen for the day."** During a live session the
served parameter set is *immutable*. Every consumer that asks the same question at
two different times in the same session gets the *same* answer. No mid-session
mutation, ever. Changes take effect only across a session boundary via an atomic
swap.

**Why this matters in this arena.** Indian weekly index-option theta harvesting is
intraday and defined-risk. A silently shifting stop level, greek band, or sizing
number mid-session would mean the risk module, sizing module, and signal module are
each running against *different* assumptions — the classic split-brain failure. A
malformed or out-of-bounds set served at 09:15 could uncap risk on a defined-risk
strategy. So Module 16 is a **safety/correctness component**, not a convenience
store. Its default posture on any doubt is **refuse and fail closed**, not
"best-effort serve."

**Design stance.**
- **Validate before it can ever be active.** A set is proven good *before* it is
  eligible to be frozen; the active path never validates lazily at read time.
- **Freeze = copy + seal.** Freezing produces an immutable snapshot with a stable
  identity (content hash + version), not a live reference to mutable storage.
- **Atomicity at the seam.** Activation of a new set is a single pointer flip;
  consumers never observe a half-applied set.
- **Always have a fallback.** Last-known-good is retained so the system is never
  left with "no parameters" if the new set is bad or absent.
- **Secrets are not parameters.** Credentials/tokens live behind a different
  boundary with different handling and never appear in versioned param history.

**Boundaries (what this module does NOT do).** It does not decide trades, does not
fetch market data, does not run the approval workflow (only verifies the approval
stamp), and does not interpret the *meaning* of a parameter beyond schema/bounds.

**Decomposition map.**
- 16.1 Config sources & precedence
- 16.2 Schema & validation of incoming parameter sets
- 16.3 Freezing for the session (immutability)
- 16.4 Versioning & history
- 16.5 Atomic swap between sessions
- 16.6 Rollback to last-known-good
- 16.7 Serving / lookup to consumers
- 16.8 Secrets vs params separation
- 16.9 Malformed / out-of-bounds detection & refusal

---

## 16.1 Config Sources & Precedence

Owns *where* configuration comes from and *who wins* when sources disagree. This is
the static-config side (the param set is handled by 16.2+), but the two share a
precedence philosophy.

### 16.1.1 Source registry & layering

- **Responsibility** — Enumerate every config source and the layer it occupies
  (built-in defaults → packaged file → environment/deployment overlay → operator
  override).
- **Behavior / Actions**
  - Maintain an ordered, declared list of sources; each tagged with a layer rank.
  - Load each present source into a named layer; record which layer supplied each
    effective key (provenance trail).
  - Treat absent optional layers as empty, not as errors.
  - Refuse to start if a *required* base layer (defaults) is missing or unreadable.
- **Scenarios & Possibilities**
  - Two sources define the same key → resolved later by 16.1.2, but both recorded.
  - An overlay file is present but unreadable (permissions) → hard error, do not
    silently skip a layer that may carry risk-relevant overrides.
  - A new unknown source appears that isn't in the registry → ignored with a
    warning; only declared sources are honored (no ambient pickup).
  - Environment-variable overlay carries a typo'd key → surfaced as "unknown key"
    rather than silently creating a phantom setting.
- **Functional Test Case(s)**
  - *Given* defaults + one overlay both defining `max_open_positions`, *When*
    layers load, *Then* both values are retained with provenance and the registry
    reports two contributing layers for that key.
  - *Given* the required defaults layer is missing, *When* load runs, *Then* the
    module refuses to initialize and reports the missing base layer.
- **Clear Outcome** — Every effective key is traceable to the exact layer that set
  it; missing optional layers are tolerated, missing required base is fatal.

### 16.1.2 Precedence resolution

- **Responsibility** — Deterministically collapse the layered sources into one
  effective static-config view.
- **Behavior / Actions**
  - Apply highest-rank layer wins, key by key; lower layers fill gaps.
  - Produce a single resolved map plus a "winning layer per key" annotation.
  - Make resolution a pure function of the layer set (same inputs → same output).
  - Forbid partial merges of structured/nested values unless a documented
    deep-merge rule applies; default to whole-value override to avoid Frankenstein
    configs.
- **Scenarios & Possibilities**
  - Operator override intends to raise a stop but mistypes the key → no effect on
    the real key, and the bogus key flagged (see 16.1.1) — fail visible, not
    silent.
  - Conflicting types across layers (string vs number for same key) → resolution
    refuses and reports a type conflict rather than coercing.
  - Empty overlay → resolved view equals defaults exactly (idempotence check).
- **Functional Test Case(s)**
  - *Given* defaults `slippage=2` and overlay `slippage=5`, *When* resolved,
    *Then* effective `slippage=5` with winning layer = overlay.
  - *Given* a key typed as number in defaults and string in overlay, *When*
    resolved, *Then* a type-conflict error is raised, not a coerced value.
- **Clear Outcome** — A single deterministic effective config with per-key winner;
  conflicts surface loudly instead of being papered over.

### 16.1.3 Static config loading & reload policy

- **Responsibility** — Govern when static config may (re)load and guarantee it is
  also session-stable.
- **Behavior / Actions**
  - Load resolved static config once at session start and snapshot it alongside the
    param set freeze (16.3).
  - Disallow mid-session reload of risk-relevant static keys; permit only an
    explicit out-of-session reload.
  - Distinguish "operational/static" keys that *could* hot-reload (e.g. log
    verbosity) from those that must not (anything touching risk/sizing/limits) —
    default any unclassified key to no-hot-reload.
- **Scenarios & Possibilities**
  - Operator edits config file at 11:00 hoping to widen a limit → change is on disk
    but not served until next session boundary.
  - A benign log-level toggle requested mid-session → allowed only if explicitly on
    the hot-reload allowlist.
  - Reload requested while a session is open → refused with reason "session active."
- **Functional Test Case(s)**
  - *Given* an active session, *When* a reload of a risk key is requested, *Then*
    it is refused and the served value is unchanged.
  - *Given* no active session, *When* reload runs, *Then* the new static snapshot is
    prepared for the next freeze.
- **Clear Outcome** — Static config is as session-stable as the param set; only
  whitelisted keys can move intra-session, everything else waits for the boundary.

---

## 16.2 Schema & Validation of Incoming Parameter Sets

Gatekeeper for any candidate set before it can become eligible to freeze. Nothing
unvalidated ever reaches the active pointer.

### 16.2.1 Structural / schema validation

- **Responsibility** — Confirm the candidate set has the right shape: all required
  fields present, no unknown fields, correct nesting.
- **Behavior / Actions**
  - Check presence of every required parameter key against a declared schema.
  - Reject unknown/extra keys (typo defense, smuggled-field defense).
  - Verify container shapes (e.g. per-index sub-bundles for NIFTY and SENSEX both
    present when both are traded).
- **Scenarios & Possibilities**
  - Missing `stop_level` entirely → reject; a defined-risk strategy cannot run
    without it.
  - Extra unrecognized key `experimental_x` → reject (don't quietly ignore; it may
    be the producer's attempt to flip behavior).
  - Set covers NIFTY but omits SENSEX while SENSEX is in scope → structural reject.
  - Empty set / null / non-object payload → reject as malformed (handoff to 16.9).
- **Functional Test Case(s)**
  - *Given* a set missing one required key, *When* validated, *Then* rejected with
    the exact missing-key path.
  - *Given* a set with an unknown extra key, *When* validated, *Then* rejected
    naming the offending key.
- **Clear Outcome** — Only sets that exactly match the declared schema shape pass;
  every rejection names the precise field.

### 16.2.2 Type & domain (bounds) validation

- **Responsibility** — Confirm every value has the right type and lies inside its
  declared safe range.
- **Behavior / Actions**
  - Type-check each field (number/int/enum/etc.).
  - Range-check against per-field min/max and allowed-set bounds (e.g. weights in
    [0,1], stop multiplier in a sane band, sizing within capital limits, greek
    bands ordered low<high).
  - Reject NaN/Inf/negative-where-positive-required/zero-where-nonzero-required.
- **Scenarios & Possibilities**
  - `stop_multiplier = 50` (typo for 1.5) → out of bounds → reject; this is exactly
    the class of error that uncaps risk.
  - Sizing `lots = 0` → reject if zero means "do nothing"/ambiguous, or accept only
    if explicitly a valid "flat" value per schema.
  - Negative greek band, or band_low > band_high → reject (ordering rule).
  - Value at the exact boundary (== max) → accept (inclusive) per declared rule;
    just-over → reject. Boundary behavior must be explicit.
  - Numeric provided as string "1.5" → reject as type error (no silent coercion),
    or normalize only if schema declares it — default strict.
- **Functional Test Case(s)**
  - *Given* `stop_multiplier=50` with max 5, *When* validated, *Then* rejected with
    field, value, and bound reported.
  - *Given* `band_low=0.4, band_high=0.2`, *When* validated, *Then* rejected for
    ordering violation.
  - *Given* a value exactly equal to its inclusive max, *When* validated, *Then*
    accepted.
- **Clear Outcome** — No value outside its declared safe domain can ever be frozen;
  boundary semantics are deterministic and documented.

### 16.2.3 Cross-field / semantic consistency

- **Responsibility** — Validate relationships *between* parameters that are
  individually in-range but jointly incoherent.
- **Behavior / Actions**
  - Check inter-field invariants: e.g. take-profit vs stop relationship sane;
    short-strike vs long-strike width consistent with a defined-risk spread; sum of
    signal weights normalizes as required; sizing × max-positions stays within
    capital/margin budget.
  - Check internal consistency of the bundle as a whole (a "is this set
    self-coherent" pass), independent of any live market data.
- **Scenarios & Possibilities**
  - Each weight in [0,1] but they sum to 2.0 when they must sum to 1 → reject.
  - Long strike *inside* short strike → not a defined-risk spread → reject.
  - Stop tighter than expected slippage/spread → economically nonsensical → reject
    or warn per policy.
  - Per-position size fine, but size × max_positions exceeds capital cap → reject.
- **Functional Test Case(s)**
  - *Given* weights summing to 2.0 under a sum-to-1 rule, *When* validated, *Then*
    rejected for normalization violation.
  - *Given* a spread whose long strike sits inside the short strike, *When*
    validated, *Then* rejected as not defined-risk.
- **Clear Outcome** — Sets that are coherent field-by-field but contradictory as a
  whole are caught before freeze.

### 16.2.4 Provenance / approval / integrity verification

- **Responsibility** — Prove the candidate set is the genuine, approved artifact
  from the offline process and was not altered in transit.
- **Behavior / Actions**
  - Verify an approval marker/signature and an integrity digest over the payload.
  - Confirm metadata: producer identity, intended target session/date, and that the
    set is *for the upcoming* session (not a stale or future-dated one).
  - Reject unsigned, tampered, or unapproved sets regardless of schema validity.
- **Scenarios & Possibilities**
  - Schema-valid set but missing approval stamp → reject (a valid-looking but
    unapproved set must never trade).
  - Integrity digest mismatch → reject (corruption or tampering in handover).
  - Set targets yesterday's date or a date far in the future → reject as
    mis-targeted (see 16.5 readiness).
  - Two candidate sets both approved for the same session → ambiguity; require a
    single winner (latest approved) and flag the collision.
- **Functional Test Case(s)**
  - *Given* a schema-valid but unsigned set, *When* validated, *Then* rejected for
    missing approval.
  - *Given* a set whose digest doesn't match its content, *When* validated, *Then*
    rejected for integrity failure.
- **Clear Outcome** — Only authentic, approved, untampered, correctly-targeted sets
  pass; provenance is independent of and additional to schema/bounds checks.

---

## 16.3 Freezing for the Session (Immutability)

Turns a validated candidate into the sealed, session-constant object consumers read.

### 16.3.1 Freeze operation

- **Responsibility** — Convert the validated active candidate into an immutable
  session snapshot at session start.
- **Behavior / Actions**
  - Deep-copy the validated set (and the static-config snapshot) into a sealed
    object; sever any link to mutable storage.
  - Compute and attach a content hash + version + freeze timestamp/session id.
  - Re-run validation gates (16.2) at freeze time as a last guard (defense in
    depth) — refuse to freeze anything not currently valid.
  - Mark the snapshot read-only.
- **Scenarios & Possibilities**
  - Candidate passed validation yesterday but storage corrupted overnight →
    freeze-time re-validation catches it.
  - Freeze invoked when no valid candidate exists → refuse to freeze; trigger
    fallback (16.6 / 16.9.2), do not freeze an empty/partial set.
  - Freeze invoked twice for the same session → idempotent; second call returns the
    same sealed snapshot, never produces a second active set.
- **Functional Test Case(s)**
  - *Given* a valid candidate, *When* freeze runs, *Then* a read-only snapshot with
    hash+version+session id is produced and validation passed at freeze time.
  - *Given* no valid candidate, *When* freeze runs, *Then* freeze is refused and the
    no-set fallback path is signaled.
- **Clear Outcome** — Session begins with exactly one sealed, identifiable,
  validated snapshot — or with an explicit no-set refusal, never a partial one.

### 16.3.2 Immutability enforcement during trading

- **Responsibility** — Guarantee the frozen snapshot cannot change while a session
  is live.
- **Behavior / Actions**
  - Reject any write/mutate/swap attempt against the active snapshot during an open
    session.
  - Serve only deep copies or genuinely read-only views so a consumer cannot mutate
    shared state.
  - Detect tampering: re-verify the content hash on demand / periodically; alarm on
    mismatch.
- **Scenarios & Possibilities**
  - A consumer holds a returned object and mutates it → must not affect any other
    consumer (copy/readonly isolation).
  - An operator/process tries to "just nudge" a stop at 12:30 → rejected with
    "session frozen."
  - In-memory bit-flip / corruption of the active snapshot → hash recheck detects
    drift → escalate (likely halt + rollback decision upstream).
- **Functional Test Case(s)**
  - *Given* an open session, *When* a mutation of the active set is attempted,
    *Then* it is rejected and the served content hash is unchanged.
  - *Given* a consumer mutates its returned copy, *When* another consumer reads,
    *Then* the second consumer sees the original values.
- **Clear Outcome** — Zero successful mid-session mutations; cross-consumer
  isolation holds; corruption is detectable via hash.

### 16.3.3 Freeze metadata & snapshot identity

- **Responsibility** — Give every frozen set a stable, queryable identity.
- **Behavior / Actions**
  - Expose version, content hash, source set id, target session/date, and freeze
    time as read-only metadata.
  - Let consumers and logs reference "which exact set am I running" by a single id.
- **Scenarios & Possibilities**
  - Post-trade audit asks "what stop level was live at 13:00?" → answered by the
    snapshot id recorded in trade logs joined to this metadata.
  - Two sessions accidentally reference the same version number → identity check
    flags non-unique version (must be monotonic/unique — see 16.4.1).
- **Functional Test Case(s)**
  - *Given* a frozen snapshot, *When* metadata is queried, *Then* it returns a
    unique version + hash + session id consistent with the served content.
  - *Given* the content hash recomputed from served values, *When* compared to
    metadata, *Then* they match exactly.
- **Clear Outcome** — Every running set is uniquely and verifiably identifiable for
  audit and reconciliation.

---

## 16.4 Versioning & History

Keeps the full lineage of param sets so any past session is reconstructible and a
known-good predecessor always exists.

### 16.4.1 Version assignment

- **Responsibility** — Assign a unique, monotonic version id to each accepted set.
- **Behavior / Actions**
  - Allocate a new version on acceptance (not on freeze) so candidates are
    referenceable pre-session.
  - Guarantee monotonicity and uniqueness; never reuse a version even after
    rollback.
  - Bind version ↔ content hash so identical content can be detected (re-submission
    of the same bytes).
- **Scenarios & Possibilities**
  - Producer resubmits byte-identical set → same hash, new version, flagged as
    duplicate content (no functional change).
  - Clock/sequence source unavailable → refuse to assign rather than risk a
    collision.
  - Out-of-order arrival (a "newer" set arrives before an older one is processed) →
    version reflects acceptance order, content metadata reflects target date.
- **Functional Test Case(s)**
  - *Given* two accepted sets, *When* versions assigned, *Then* the second is
    strictly greater and never equal to the first.
  - *Given* identical content resubmitted, *When* accepted, *Then* a new version is
    issued but flagged as duplicate-of prior hash.
- **Clear Outcome** — Every set has a unique monotonic version; duplicates are
  visible; versions are never recycled.

### 16.4.2 History store & audit log

- **Responsibility** — Persist all accepted/rejected sets and lifecycle events.
- **Behavior / Actions**
  - Append-only record of: candidate received, validation result (with reasons),
    accepted, frozen, served-to-session, swapped, rolled back.
  - Store enough to fully reconstruct any past session's exact parameters.
  - Never overwrite or delete history (immutable ledger); rejections retained for
    forensics.
- **Scenarios & Possibilities**
  - Post-incident review needs the rejected set that *almost* went live → present
    in history with rejection reason.
  - Storage full / unwritable → treat as a serious fault: a session that can't be
    audited shouldn't trade (fail-closed policy decision — bubble up).
  - History query for a date with no session → returns "no session" cleanly.
- **Functional Test Case(s)**
  - *Given* a set is rejected, *When* history is queried, *Then* the rejection and
    its reason are recorded and retrievable.
  - *Given* a past session id, *When* queried, *Then* the exact frozen set (by hash)
    is reproducible from history.
- **Clear Outcome** — Complete, immutable, append-only lineage; any session is fully
  reconstructible; nothing is silently dropped.

### 16.4.3 Last-known-good tracking

- **Responsibility** — Maintain a pointer to the most recent set that *actually ran
  cleanly* (the rollback target).
- **Behavior / Actions**
  - Promote a set to last-known-good only after it has frozen and served a session
    without a config-level failure (definition of "good" must be explicit).
  - Keep last-known-good distinct from "latest accepted" — the newest set is not
    automatically good.
  - Retain last-known-good independently so it survives even if newer sets are
    purged/corrupt.
- **Scenarios & Possibilities**
  - Newest set is the one that failed → last-known-good still points at the prior
    healthy set, not the failed newest.
  - Very first deployment → no last-known-good exists yet; this state must be
    explicit and handled by 16.6 / 16.9.2.
  - A set ran but caused a config-level fault → must NOT be promoted to good.
- **Functional Test Case(s)**
  - *Given* set v10 served cleanly and v11 failed, *When* last-known-good is read,
    *Then* it is v10.
  - *Given* no prior clean session, *When* last-known-good is read, *Then* it
    reports "none" explicitly.
- **Clear Outcome** — Last-known-good always points at a genuinely healthy set or
  explicitly reports none; never silently the latest.

---

## 16.5 Atomic Swap Between Sessions

Moves the active pointer from the old set to the new one — only at a legal boundary,
all-or-nothing.

### 16.5.1 Pre-swap staging & readiness

- **Responsibility** — Confirm a fully-validated, correctly-targeted next set is
  staged and ready before any boundary swap is attempted.
- **Behavior / Actions**
  - Maintain a "staged next" slot holding the validated candidate for the upcoming
    session.
  - Verify target date == upcoming session and all 16.2 gates already passed.
  - Report readiness status (ready / not-ready / stale) ahead of the boundary so
    upstream can react before open.
- **Scenarios & Possibilities**
  - No staged set by readiness-check time → "not ready"; upstream decides (rollback
    vs no-trade) — bubble up.
  - Staged set targets the wrong date (yesterday's, or two sessions ahead) → "stale
    / mis-targeted," not eligible.
  - A newer approved set arrives between staging and swap → restage to the latest,
    re-run readiness.
- **Functional Test Case(s)**
  - *Given* a validated set for tomorrow staged, *When* readiness is checked, *Then*
    status = ready.
  - *Given* the staged set targets a past date, *When* readiness is checked, *Then*
    status = stale/not-ready.
- **Clear Outcome** — Swap is attempted only when a correctly-targeted, validated set
  is provably staged; readiness is observable before open.

### 16.5.2 Atomic activation

- **Responsibility** — Flip the active pointer from old to staged in one indivisible
  step.
- **Behavior / Actions**
  - Single pointer/reference swap; no window where the active set is partial,
    blended, or null.
  - If anything in the swap fails, leave the previous active set in place (no
    half-swap).
  - After swap, the new set becomes the freeze source for the new session (16.3).
- **Scenarios & Possibilities**
  - Crash midway through swap → previous active set remains; system is never left
    pointer-less.
  - Concurrent readers during the instant of swap → each sees either fully-old or
    fully-new, never a mix.
  - Swap succeeds but freeze then fails → defined ordering must ensure we don't open
    on an unfrozen set (freeze-then-publish ordering).
- **Functional Test Case(s)**
  - *Given* a staged set and an interrupted swap, *When* the system recovers, *Then*
    the active set is exactly the old one (atomicity preserved).
  - *Given* readers polling during swap, *When* swap completes, *Then* no reader
    ever observed a blended/null set.
- **Clear Outcome** — Activation is all-or-nothing; old set survives any failed swap;
  consumers never see an intermediate state.

### 16.5.3 Swap-window guard (no swap mid-session)

- **Responsibility** — Permit swaps only at legal session boundaries, never while a
  session is live.
- **Behavior / Actions**
  - Gate every swap behind a "session not active" check.
  - Queue any swap request that arrives mid-session for the next boundary rather
    than applying it.
  - Coordinate with immutability (16.3.2): mid-session the active set is frozen, so
    swap is structurally impossible, not merely discouraged.
- **Scenarios & Possibilities**
  - Approved set + swap request lands at 11:00 → deferred to next boundary, current
    session untouched.
  - Session-end boundary detection is wrong/ambiguous → default to "treat as active"
    (fail safe = don't swap) until boundary is certain.
  - Manual force-swap attempt mid-session → refused; logged as a policy violation.
- **Functional Test Case(s)**
  - *Given* an open session, *When* a swap is requested, *Then* it is deferred and
    the active set is unchanged for the rest of the session.
  - *Given* the session has ended, *When* the deferred swap runs, *Then* the new set
    activates atomically.
- **Clear Outcome** — Zero mid-session swaps; legitimate swaps happen exactly once,
  only at boundaries.

---

## 16.6 Rollback to Last-Known-Good

The recovery path when the intended set is absent, bad, or has caused trouble.

### 16.6.1 Rollback trigger & target selection

- **Responsibility** — Decide when to roll back and to which set.
- **Behavior / Actions**
  - Trigger on: no valid staged set by boundary, freeze-time validation failure,
    detected active-set corruption, or explicit operator/upstream command.
  - Select the last-known-good (16.4.3) as the target; never auto-select a set that
    never ran clean.
  - Emit a clear rollback decision record (why, from-version, to-version).
- **Scenarios & Possibilities**
  - Bad new set + healthy last-known-good → roll back to it (but note: it was tuned
    for a *different* day; rolling back stale params is itself a risk → bubble up
    whether to trade-on-old vs not-trade).
  - No last-known-good exists (first run) → rollback impossible → escalate to
    fail-safe no-set posture (16.9.2).
  - Repeated rollbacks across consecutive sessions → flag systemic producer/approval
    breakage upstream.
- **Functional Test Case(s)**
  - *Given* a freeze-time validation failure and a healthy last-known-good, *When*
    rollback triggers, *Then* the target selected is the last-known-good version with
    a recorded reason.
  - *Given* no last-known-good, *When* rollback triggers, *Then* it escalates to the
    no-set fail-safe instead of selecting an unproven set.
- **Clear Outcome** — Rollback chooses only a genuinely good predecessor or
  explicitly escalates; the decision is always recorded.

### 16.6.2 Rollback execution

- **Responsibility** — Make the last-known-good set the active/frozen set safely.
- **Behavior / Actions**
  - Reuse the same atomic activation + freeze path (16.5.2 / 16.3.1) — rollback is
    not a special unsafe shortcut.
  - Verify the last-known-good set still validates and its hash matches history
    before activating.
  - Mark the session as "running rolled-back params vN" for downstream awareness.
- **Scenarios & Possibilities**
  - Last-known-good itself now fails validation (schema evolved, or storage rot) →
    rollback fails → fail-safe no-set posture.
  - Hash of stored last-known-good ≠ recorded hash → corruption → do not activate;
    escalate.
  - Successful rollback → session runs, but flagged so risk/ops know params are not
    the freshly-approved ones.
- **Functional Test Case(s)**
  - *Given* a valid, hash-matching last-known-good, *When* rollback executes, *Then*
    it is frozen and served atomically and the session is tagged "rolled-back."
  - *Given* the last-known-good fails revalidation, *When* rollback executes, *Then*
    activation is refused and the no-set posture is entered.
- **Clear Outcome** — Rollback either cleanly activates a verified good set (clearly
  tagged) or refuses and fails closed — never activates something unverified.

---

## 16.7 Serving / Lookup to Consumers

The read side: how every other module gets its numbers.

### 16.7.1 Read API / parameter lookup

- **Responsibility** — Serve config and frozen parameters to consumers on request.
- **Behavior / Actions**
  - Provide key/path lookup and whole-bundle retrieval, returning read-only/copied
    values from the frozen snapshot only.
  - Reads are side-effect free and never trigger loads/swaps.
  - Every served response carries the active version/hash so consumers can assert
    agreement.
- **Scenarios & Possibilities**
  - Consumer asks for a known key → returns frozen value + version tag.
  - Consumer asks during the no-set posture → returns an explicit "no active set"
    error, not a default trade-enabling number (see 16.9.2).
  - High-frequency reads → must be cheap and lock-light (frozen immutable snapshot
    makes this safe).
- **Functional Test Case(s)**
  - *Given* a frozen session, *When* a consumer looks up a key twice at different
    times, *Then* it gets identical values and the same version tag.
  - *Given* a read request, *When* served, *Then* the active set is not mutated and
    no load/swap is triggered.
- **Clear Outcome** — Consumers get stable, version-tagged, read-only values; reads
  never alter state.

### 16.7.2 Consistency & snapshot serving

- **Responsibility** — Guarantee all consumers in a session see one coherent set.
- **Behavior / Actions**
  - Serve all reads from the single frozen snapshot for that session.
  - If a swap/rollback happens at a boundary, ensure no consumer straddles two
    versions within one session.
  - Optionally support "pin to version" so a long-running consumer can detect if its
    expected version changed underneath it.
- **Scenarios & Possibilities**
  - Two modules read at 09:20 and 14:30 → identical version/values (intra-session
    coherence).
  - A consumer caches values and a new session starts → version tag change lets the
    consumer know to refresh.
  - Consumer running against version vN while active is vN+1 (it missed the
    boundary) → version mismatch surfaced, not silently tolerated — bubble up.
- **Functional Test Case(s)**
  - *Given* multiple consumers in one session, *When* each reads, *Then* all observe
    the same version/hash.
  - *Given* a consumer pinned to vN, *When* active becomes vN+1, *Then* a version
    mismatch is reported to that consumer.
- **Clear Outcome** — One coherent set per session across all consumers; version
  drift is detectable, never silent.

### 16.7.3 Default / missing-key handling

- **Responsibility** — Define behavior when a consumer requests a key that isn't
  present.
- **Behavior / Actions**
  - Distinguish "key has a declared default" from "key is required and missing."
  - For required-but-absent risk keys, return an explicit error — never a fabricated
    or zero default that could silently enable/uncap trading.
  - For genuinely optional keys, return the declared default with a flag indicating
    it's a default, not a set value.
- **Scenarios & Possibilities**
  - Typo'd lookup key from a consumer → "unknown key" error (helps catch consumer
    bugs), not a silent default.
  - Optional cosmetic key absent → safe declared default returned and labeled.
  - A risk-relevant key absent at read time → hard error (this should have been
    caught at validation, so it's also a signal of corruption).
- **Functional Test Case(s)**
  - *Given* a required risk key is absent, *When* looked up, *Then* an explicit
    error is returned, not a default.
  - *Given* an optional key with a declared default is absent, *When* looked up,
    *Then* the default is returned flagged as default.
- **Clear Outcome** — Missing optional keys degrade gracefully and visibly; missing
  required keys fail loudly — no silent risk-enabling defaults.

---

## 16.8 Secrets vs Params Separation

Keeps credentials out of the parameter/version/history plane entirely.

### 16.8.1 Classification & partitioning

- **Responsibility** — Cleanly separate secrets (tokens, keys, credentials) from
  tunable parameters and static config.
- **Behavior / Actions**
  - Classify every stored item as secret vs param/config; store them in separate
    planes with separate handling.
  - Forbid secrets from entering the versioned param set, history, or freeze
    snapshot.
  - Reject any incoming parameter set that contains a field shaped like a secret
    (credential smuggling defense).
- **Scenarios & Possibilities**
  - Producer accidentally embeds an API token in the param bundle → rejected /
    quarantined; token never lands in history.
  - A param genuinely needs to *reference* a secret → store a reference/handle, not
    the secret value.
  - Audit export of param history → contains zero secrets by construction.
- **Functional Test Case(s)**
  - *Given* an incoming set containing a credential-shaped field, *When* validated,
    *Then* it is rejected and the secret value is not persisted to history.
  - *Given* a param history export, *When* inspected, *Then* it contains no secret
    material.
- **Clear Outcome** — Secrets and params are physically/logically partitioned;
  history and snapshots are secret-free by construction.

### 16.8.2 Secret access boundary

- **Responsibility** — Mediate access to secrets separately and minimally.
- **Behavior / Actions**
  - Serve secrets only to authorized consumers through a distinct, audited path —
    not via the general param lookup (16.7).
  - Never log, hash-into-version, or echo secret values; mask in any diagnostic
    output.
  - Allow secret rotation independent of the param-set lifecycle (a secret change is
    not a param version bump).
- **Scenarios & Possibilities**
  - General param lookup asked for a secret key → refused; secrets aren't on that
    channel.
  - Diagnostic dump runs → secrets appear masked/redacted.
  - Credential rotated mid-week → no new param version, no freeze impact, no history
    churn.
- **Functional Test Case(s)**
  - *Given* a request for a secret via the normal param lookup, *When* served,
    *Then* it is refused and routed/denied per the secret boundary.
  - *Given* a diagnostic output including a secret-bearing area, *When* produced,
    *Then* the secret is masked.
- **Clear Outcome** — Secrets flow only through their own audited, masked channel and
  rotate independently of param versioning.

---

## 16.9 Malformed / Out-of-Bounds Detection & Refusal

The "say no safely" subsystem — the union of all rejection paths and the fail-closed
posture when nothing usable exists.

### 16.9.1 Refusal & quarantine of bad sets

- **Responsibility** — Reject any set failing any gate and isolate it without
  contaminating the active path.
- **Behavior / Actions**
  - On any failure from 16.2 / 16.8, refuse acceptance; never partially apply a bad
    set.
  - Quarantine the rejected artifact with a full reason list (which fields, which
    rule).
  - Guarantee a rejected candidate cannot become staged, frozen, or last-known-good.
  - Surface a clear, actionable rejection signal upstream (to the producing/approval
    process).
- **Scenarios & Possibilities**
  - Set fails on 3 different rules → report all 3, not just the first (full
    diagnostics for the producer).
  - A bad set arrives repeatedly → each quarantined; repeated identical failures
    flagged as upstream breakage.
  - Rejection while a good set is already staged → staged good set untouched; only
    the bad candidate is isolated.
- **Functional Test Case(s)**
  - *Given* a set violating several rules, *When* validated, *Then* it is
    quarantined with all violations listed and is not eligible to stage/freeze.
  - *Given* a bad candidate is rejected, *When* the staged/active set is inspected,
    *Then* it is unchanged.
- **Clear Outcome** — Bad sets are fully isolated with complete reasons and can never
  reach any active or fallback role.

### 16.9.2 Fail-safe "no usable set" posture

- **Responsibility** — Define the safe state when neither a valid new set nor a
  usable last-known-good exists.
- **Behavior / Actions**
  - Enter an explicit "no active parameter set" state rather than serving defaults
    or partial data.
  - Make every consumer lookup return an unambiguous "no set" error so the trading
    system can refuse to open positions (no-trade is the safe default for a
    defined-risk theta strategy).
  - Loudly signal the condition upstream for human/operator attention.
- **Scenarios & Possibilities**
  - Session open with no approved set AND no last-known-good (e.g. first deployment,
    or both corrupt) → no-set posture → system should not trade.
  - Storage layer down so neither candidate nor history is reachable → no-set posture
    + critical alarm.
  - Partial availability (some keys readable, some not) → treat as no-set, not
    "serve what we have" — half a defined-risk config is more dangerous than none.
- **Functional Test Case(s)**
  - *Given* no valid set and no last-known-good, *When* a consumer looks up any
    parameter, *Then* it receives an explicit "no active set" error.
  - *Given* the no-set posture, *When* the system queries whether it may trade,
    *Then* the answer is no, with the condition alarmed upstream.
- **Clear Outcome** — Absence of a good set produces an explicit, trade-blocking,
  alarmed state — never a silent or best-effort serve.

---

## Suggestions (for bubble-up)

These are scenarios where Module 16 can *detect and refuse* locally, but the *right
system-wide response* spans other modules (execution, risk, ops, the offline
producer/approval pipeline). Flagged here for later cross-module grilling.

1. **No approved set by market open.** Module 16 will enter the no-set / rollback
   posture, but the *policy* — do we trade on stale rolled-back params, or stay flat
   for the day? — is a system decision. Rolling back to a set tuned for a different
   day is itself a risk; staying flat forgoes the day. Needs an explicit
   system-level rule and an operator alert SLA *before* 09:15.

2. **Mid-session config-change attempt.** Module 16 refuses/defers it, but the
   system should decide how such attempts are surfaced (alert? audit? who is even
   allowed to try?). Recurring attempts may indicate a broken operational habit or
   an emergency-override need that currently has no safe channel — possibly a
   sanctioned, narrowly-scoped "emergency halt" that is *not* a param mutation.

3. **Bad set served / out-of-bounds slips through.** Module 16's bounds are only as
   good as its declared schema. A value that is in-range but economically wrong for
   *today's* regime won't be caught here (no market context in this module). Needs a
   downstream sanity/guardrail layer (and possibly a pre-open dry-run) that
   cross-checks frozen params against live conditions and can veto the session.

4. **Version mismatch across consumers.** Module 16 tags every response with a
   version/hash, but enforcement that *all* live modules actually agree on the same
   version (and halt on divergence) is a system concern — the classic split-brain.
   Recommend a system-wide pre-open "everyone confirm active version vN" handshake
   and a hard halt on any mismatch.

5. **First-deployment / no last-known-good.** The very first session has no fallback;
   rollback is impossible. The system needs a defined bootstrap rule (e.g. require a
   verified seed set, or mandate flat-no-trade until one healthy session establishes
   a last-known-good).

6. **History/audit store unwritable.** Module 16 treats inability to audit as
   fail-closed, but whether "can't log → can't trade" is acceptable is a business
   risk decision that belongs to the Board/ops, not this module alone.

7. **Stale-but-valid rolled-back params.** A rolled-back set passes all of 16's
   gates yet was calibrated for a prior day. The "valid" stamp must not be read by
   downstream modules as "appropriate for today." Recommend the rolled-back tag
   (16.6.2) gate sizing/risk downstream (e.g. reduced size on stale params).
