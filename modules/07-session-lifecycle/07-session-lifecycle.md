# Module 7 — Session Lifecycle

## 7.0 Module Overview

**Role.** Module 7 owns the trading-day clock. It is the single authority that decides
*when* the rest of the system is allowed to act in time. It does **not** decide *what*
to trade or *how much* — it only answers three temporal questions:

1. **Is the market open right now, and in what phase?** (pre-open / open / closed / halt)
2. **Is a new entry allowed at this instant?** (entry windows + last-entry cutoff)
3. **Is it time to force everything flat?** (mandatory end-of-day square-off)

…and it emits a steady **cadence tick** so downstream modules have a heartbeat to run
their cycles against.

**Why it must exist as its own module.** Time is a cross-cutting concern. If each
strategy module computed "is it too late to enter?" independently, they would drift,
disagree, and (worst case) one would carry a position past close because its own clock
was wrong. Centralising the clock guarantees a *single source of truth for time* — every
module sees the same phase, the same cutoff, the same square-off signal.

**Design stance — fail-safe, not fail-open.** This module is the last line of defence
against the cardinal sin of an intraday theta book: holding overnight. Therefore every
ambiguous condition resolves toward *safety*: if we cannot prove the market is open →
treat as closed (block entries). If we cannot prove we are before the cutoff → treat as
after (block entries). If we cannot prove positions are flat → keep firing square-off.
Entries are a privilege that must be positively proven; square-off is an obligation that
must be positively discharged.

**Inputs (plain data only).**
- Wall-clock time (with awareness it can drift, jump, or stall).
- Market calendar: trading days, holidays, expiry days, special/muhurat sessions, and
  the open/close/half-day timings per session.

**Outputs (plain data only — events, never orders).**
- `ENTRY_WINDOW_OPEN` / `ENTRY_WINDOW_CLOSE` — entries are/aren't permitted.
- `CADENCE_TICK(seq, ts)` — the regular heartbeat downstream cycles run on.
- `FORCE_SQUAREOFF` — flatten everything now (with escalation levels near close).
- `PHASE_CHANGE(phase)` — pre-open / open / close-imminent / closed / halt.
- (All consumed by other modules; Module 7 places no orders itself.)

**Boundaries — what this module is NOT.**
- Not a risk manager (no P&L, no stop logic) — it only times.
- Not an order router — `FORCE_SQUAREOFF` is a *signal*; someone else executes it.
- Not a calendar source — it *consumes* the calendar; it does not author holidays.
- Not a strategy — it does not know NIFTY from SENSEX beyond their session timings.

**Decomposition map.**
- **7.1 Calendar & Session-Definition** — what kind of day is today, and its timings.
- **7.2 Market-Phase State Machine** — turn wall-clock + calendar into the current phase.
- **7.3 Entry-Window Authority** — when may a new position be opened.
- **7.4 Cadence / Heartbeat Generation** — the regular tick downstream runs on.
- **7.5 End-of-Day Square-Off Authority** — force flat and confirm it happened.
- **7.6 Time-Integrity & Recovery** — clock drift, missed ticks, restart-mid-session.

---

## 7.1 Calendar & Session-Definition

Owns the question "what kind of day is today and what are its boundaries?" Everything
downstream of phase logic depends on this resolving *correctly and early*.

### 7.1.1 Day-Type Classification

#### 7.1.1.1 Trading-day vs non-trading-day resolver
- **Responsibility** — Decide whether the current calendar date is a tradable session at all.
- **Behavior / Actions**
  - On first call of the day (and on demand), look up today's date in the calendar.
  - Classify as one of: `FULL_TRADING`, `HALF_DAY`, `SPECIAL/MUHURAT`, `HOLIDAY`, `WEEKEND`.
  - Cache the verdict for the day; expose it as a queryable fact to other sub-nodes.
  - If the date is absent from the calendar (gap), resolve **fail-safe → treat as non-trading** and raise a calendar-gap alarm.
- **Scenarios & Possibilities**
  - Normal weekday present in calendar → `FULL_TRADING`.
  - Saturday/Sunday → `WEEKEND` (but note: exchanges occasionally run Saturday mock/special sessions — those must be explicit calendar entries, never inferred).
  - Exchange holiday → `HOLIDAY`.
  - Date missing entirely (stale calendar not updated for new year) → unknown → fail-safe non-trading + loud alarm.
  - Calendar says trading but it's a known national holiday (stale/wrong calendar) → we trust the calendar but the alarm on gaps is the only guard; cross-check optional.
  - Half-day flagged but timings missing → escalate to 7.1.2 as malformed.
- **Functional Test Case(s)**
  - Given a calendar with 2026-06-29 = FULL_TRADING, When resolver runs on that date, Then verdict = `FULL_TRADING`.
  - Given 2026-08-15 = HOLIDAY, When resolver runs, Then verdict = `HOLIDAY` and no entry windows are ever emitted.
  - Given a date not present in the calendar, When resolver runs, Then verdict = `NON_TRADING` (fail-safe) and a calendar-gap alarm is raised.
- **Clear Outcome** — Exactly one day-type per date, cached; unknown dates never produce a tradable day.

#### 7.1.1.2 Expiry-day tagger
- **Responsibility** — Tag whether today is a weekly/monthly expiry for each instrument the book trades.
- **Behavior / Actions**
  - For each instrument (NIFTY, SENSEX), mark `is_expiry_today` and `expiry_type` (weekly/monthly).
  - Expose expiry flags so the entry-window and square-off nodes can apply expiry-specific timing (e.g. tighter last-entry, earlier square-off escalation).
  - Handle expiry-day *shifts*: when the nominal expiry weekday is a holiday, expiry moves to the prior trading day — read this from the calendar, do **not** compute it.
- **Scenarios & Possibilities**
  - NIFTY weekly expiry on its usual weekday → tagged weekly expiry.
  - Expiry weekday is a holiday → calendar pre-shifts expiry to prior day; tagger reflects that.
  - Two instruments expire same day → both tagged independently.
  - Exchange changes expiry weekday convention (has happened historically) → driven purely by calendar; no hardcoded weekday.
  - Monthly expiry coinciding with weekly → tag both; downstream may treat as highest-sensitivity day.
- **Functional Test Case(s)**
  - Given calendar marks 2026-07-02 as NIFTY weekly expiry, When tagger runs, Then `NIFTY.is_expiry_today = true, type = weekly`.
  - Given the nominal expiry day is a holiday and calendar shifts it to the prior day, When tagger runs on the prior day, Then it is tagged expiry.
  - Given a non-expiry day, When tagger runs, Then `is_expiry_today = false` for all instruments.
- **Clear Outcome** — Correct per-instrument expiry tags sourced solely from the calendar; never inferred from weekday arithmetic.

### 7.1.2 Session-Timing Resolver

#### 7.1.2.1 Open/close time provider
- **Responsibility** — Provide today's authoritative open and close wall-clock times.
- **Behavior / Actions**
  - From the day-type + calendar, emit `session_open`, `session_close` (and pre-open start if modelled).
  - For half-days/special sessions, use the *session-specific* close, not the default.
  - Validate sanity: open < close, both within plausible bounds; reject malformed timing as fail-safe non-trading.
- **Scenarios & Possibilities**
  - Full day → standard open/close.
  - Half-day → early close; must override the default close everywhere downstream.
  - Muhurat session → a short evening window with its own open/close entirely outside normal hours.
  - Timezone/locale mismatch → all times anchored to exchange local time (IST); never use host-local TZ blindly.
  - Malformed timing (close before open, or null) → reject → non-trading + alarm.
- **Functional Test Case(s)**
  - Given a full day, When queried, Then returns the standard open and close.
  - Given a half-day with an early close, When queried, Then returns the early close (not the default).
  - Given malformed timings (close < open), When queried, Then resolves non-trading and raises an alarm.
- **Clear Outcome** — A single correct (open, close) pair per session, half-day/muhurat aware, IST-anchored.

#### 7.1.2.2 Derived-deadline computer (cutoff & square-off offsets)
- **Responsibility** — Compute the day's key derived deadlines *relative to today's actual close*.
- **Behavior / Actions**
  - From `session_close`, derive: `last_entry_cutoff`, `squareoff_start`, `hard_flat_deadline` as offsets back from close.
  - On half-days/expiry, apply the appropriate (possibly tighter) offset profile.
  - Guarantee monotonic ordering: `last_entry_cutoff ≤ squareoff_start ≤ hard_flat_deadline ≤ session_close`.
  - Re-derive automatically if the close time changes mid-day (rare: exchange extends/shortens session).
- **Scenarios & Possibilities**
  - Normal day → cutoff and square-off computed off standard close.
  - Half-day → same offsets but anchored to early close → everything shifts earlier; never use yesterday's absolute times.
  - Expiry day → possibly larger square-off lead (illiquid/whippy near close) → tighter deadlines.
  - Exchange extends session intraday → close moves → deadlines must recompute, not stay stale.
  - Offsets so large they invert ordering on a very short session (muhurat) → clamp and alarm rather than emit inverted deadlines.
- **Functional Test Case(s)**
  - Given close = 15:30 and square-off offset = 12 min, When computed, Then `squareoff_start = 15:18`.
  - Given a half-day close = 13:00, When computed, Then deadlines anchor to 13:00 (e.g. square-off 12:48), not 15:18.
  - Given offsets that would invert on a 60-min muhurat session, When computed, Then deadlines are clamped to preserve ordering and an alarm fires.
- **Clear Outcome** — Correctly ordered, close-relative deadlines that always track the *actual* close for the day.

---

## 7.2 Market-Phase State Machine

Converts (wall-clock, calendar, deadlines) into a single current **phase**, and emits a
`PHASE_CHANGE` event on every transition. This is the spine everything else reads.

### 7.2.1 Phase Evaluator

#### 7.2.1.1 Phase computation & transition emitter
- **Responsibility** — Map the current instant to exactly one phase and announce transitions.
- **Behavior / Actions**
  - Phases: `PRE_OPEN` → `OPEN` → `CLOSE_IMMINENT` → `POST_CLOSE` → `CLOSED`, plus orthogonal `HALTED`.
  - On each evaluation, compute the phase from now vs (pre-open start, open, squareoff_start, close).
  - Emit `PHASE_CHANGE(new_phase)` only on edges (debounced — no repeat spam).
  - Phase transitions are **monotonic within a day** (never go OPEN→PRE_OPEN); a backward jump implies clock error → route to 7.6.
- **Scenarios & Possibilities**
  - Smooth day → clean ordered transitions.
  - System starts mid-day → first evaluation lands directly in OPEN/CLOSE_IMMINENT (no synthetic replay of earlier transitions, but downstream still gets current phase — see 7.6 recovery).
  - Clock jumps backward → would imply a backward transition → suppressed + flagged, hold previous phase.
  - Clock jumps forward across close → emit the skipped critical transitions (esp. into square-off) so square-off is not missed.
  - Halt occurs mid-OPEN → overlay `HALTED`; underlying time-phase still advances so close still arrives.
- **Functional Test Case(s)**
  - Given now is between open and squareoff_start, When evaluated, Then phase = `OPEN`.
  - Given the previous phase was OPEN and now < open (clock went backward), When evaluated, Then no backward transition is emitted and a clock-anomaly flag is raised.
  - Given a forward jump from OPEN to past close, When evaluated, Then `CLOSE_IMMINENT`/`POST_CLOSE` transitions are emitted so square-off triggers.
- **Clear Outcome** — Exactly one phase at all times; transitions emitted once, monotonic, with clock anomalies diverted to recovery.

#### 7.2.1.2 Halt / resume overlay
- **Responsibility** — Represent exchange-level trading halts independent of the time-of-day phase.
- **Behavior / Actions**
  - Accept a halt indication (from calendar-flagged halts or an external halt input if available) and set `HALTED` overlay.
  - While halted: block new entries (treat as not-allowed) regardless of entry window.
  - On resume: clear overlay, recompute whether entry window is (still) open given elapsed time.
  - Critically: a halt does **not** pause the march toward square-off — if a halt eats the whole back end of the day, square-off obligations still stand the moment trading resumes (or escalate if it never resumes intraday).
- **Scenarios & Possibilities**
  - Market-wide circuit-breaker halt then resume within the day → entries blocked during halt, may re-open after.
  - Halt that persists to close → no resume → positions must be flattened at/around reopen or carried as an exception → escalate loudly (cannot square off a halted market).
  - Halt right at square-off window → square-off can't execute → escalation to operator; this is a flagged unsafe state, not silently swallowed.
  - Spurious halt signal (false positive) → entries needlessly blocked; safe direction, but log for review.
  - Resume with no remaining entry window → stay blocked for entries, proceed to square-off timing.
- **Functional Test Case(s)**
  - Given phase = OPEN and a halt is signalled, When evaluated, Then entries are blocked and overlay = `HALTED`.
  - Given a halt that persists past `squareoff_start`, When the window arrives, Then a "cannot-square-off-market-halted" escalation is raised (not a normal square-off).
  - Given resume after a halt with entry window already closed, When evaluated, Then entries remain blocked and square-off timing is unaffected.
- **Clear Outcome** — Halts suspend entries but never the square-off obligation; unresolvable halt states escalate rather than fail silently.

---

## 7.3 Entry-Window Authority

The "may we open a new position now?" gatekeeper. Pure boolean authority + the events
that mark window edges. Fail-safe default: **no entry** unless positively allowed.

### 7.3.1 Window Definition

#### 7.3.1.1 Entry-window boundary builder
- **Responsibility** — Define the start and end instants during which entries are permitted today.
- **Behavior / Actions**
  - Build `[entry_window_start, last_entry_cutoff]` from session open + a post-open settle delay and the derived cutoff (7.1.2.2).
  - Support possibly *multiple* windows (e.g. a morning window only, or morning + a brief second window) if configured — represented as a list of intervals.
  - Never allow window end to exceed `last_entry_cutoff`; clamp.
  - No window at all on non-trading days, and a degenerate/empty window on ultra-short sessions if cutoff ≤ start (block all entries, alarm).
- **Scenarios & Possibilities**
  - Single morning window → standard.
  - Post-open settle delay skips the volatile first minutes → start later than open.
  - Half-day → window compressed; may collapse to empty → no entries that day (acceptable, alarmed).
  - Expiry day → cutoff much earlier → window shorter.
  - Multiple windows configured → emit open/close per interval.
- **Functional Test Case(s)**
  - Given open=09:15, settle=15m, cutoff=14:30, When built, Then window = [09:30, 14:30].
  - Given a half-day where cutoff ≤ start, When built, Then window is empty, no entries permitted, alarm raised.
  - Given two configured intervals, When built, Then two open/close edge pairs are produced.
- **Clear Outcome** — A concrete, clamped set of entry intervals for the day; empty when the session can't safely support entries.

#### 7.3.1.2 Last-entry cutoff guard ("no-entry-after")
- **Responsibility** — Enforce the hard latest instant a new position may be opened.
- **Behavior / Actions**
  - Expose `is_entry_allowed(now)` returning false once `now ≥ last_entry_cutoff`, irrevocably for the day.
  - The cutoff is a one-way latch: once passed, no condition (re-eval, clock wobble) re-opens entries that day.
  - Emit `ENTRY_WINDOW_CLOSE` exactly once at the cutoff.
  - Reason: a position opened too close to square-off cannot mature its theta and risks being force-closed at a loss in thin liquidity.
- **Scenarios & Possibilities**
  - Entry request at cutoff − 1s → allowed.
  - Entry request at cutoff exactly → blocked (boundary inclusive on the block side; define `≥` blocks).
  - Clock briefly rewinds just after cutoff → latch keeps entries closed (does not re-open).
  - On expiry/half-day the cutoff is earlier → guard uses today's derived cutoff, not a fixed clock time.
  - Downstream asks after square-off has begun → always false.
- **Functional Test Case(s)**
  - Given cutoff=14:30 and now=14:29:59, When `is_entry_allowed`, Then true.
  - Given now=14:30:00, When `is_entry_allowed`, Then false, and `ENTRY_WINDOW_CLOSE` emitted once.
  - Given the clock rewinds to 14:29 after the latch tripped, When `is_entry_allowed`, Then still false (one-way latch).
- **Clear Outcome** — A monotonic, one-way "no-entry-after" latch tied to today's actual cutoff; emits its close event exactly once.

### 7.3.2 Window Eventing

#### 7.3.2.1 Window open/close event emitter
- **Responsibility** — Announce entry-window edges so downstream knows when it may start/stop attempting entries.
- **Behavior / Actions**
  - Emit `ENTRY_WINDOW_OPEN` at each window start, `ENTRY_WINDOW_CLOSE` at each window end.
  - Idempotent: emit each edge once; on restart mid-window, emit a synthetic "window currently open, X minutes remain" status rather than a fresh OPEN (see 7.6).
  - Include remaining-time context so downstream can decide if there's enough runway to enter.
- **Scenarios & Possibilities**
  - Clean day → one OPEN, one CLOSE.
  - Restart inside the window → status "open, remaining=…" not a duplicate OPEN.
  - Restart after the window closed → emit/serve `ENTRY_WINDOW_CLOSE`-equivalent state so nobody enters.
  - Halt during open window → window may still be "open" by clock but entries blocked by halt overlay (7.2.1.2); downstream must AND both.
  - Missed tick across the open edge → emit OPEN late on next tick with correct remaining-time (better late than never; still before cutoff).
- **Functional Test Case(s)**
  - Given window start arrives, When tick fires, Then `ENTRY_WINDOW_OPEN` emitted once with remaining-time.
  - Given restart 20m into a 60m window, When state served, Then "open, remaining≈40m", no duplicate OPEN.
  - Given a tick was missed across the start edge, When the next tick fires, Then OPEN is emitted with corrected remaining-time.
- **Clear Outcome** — Exactly-once, restart-safe window edge events with remaining-time context; never a duplicate OPEN that double-triggers entries.

---

## 7.4 Cadence / Heartbeat Generation

The regular tick that downstream cycles run on. Must be steady, observable, and robust
to drift — a missed or doubled heartbeat must not corrupt downstream cycle accounting.

### 7.4.1 Tick Generation

#### 7.4.1.1 Cadence tick scheduler
- **Responsibility** — Emit `CADENCE_TICK(seq, ts)` at the configured interval during active phases.
- **Behavior / Actions**
  - Fire at a fixed cadence (e.g. every N seconds) while phase ∈ {OPEN, CLOSE_IMMINENT}; optionally a slower cadence in PRE_OPEN/POST_CLOSE for housekeeping.
  - Each tick carries a monotonic `seq` and the wall-clock `ts` it represents.
  - Align ticks to wall-clock boundaries (e.g. :00/:30) rather than to process start, so cadence is reproducible across restarts.
  - Self-correct: schedule the *next* tick off the intended grid, not off "now + interval", to prevent slow drift accumulation.
- **Scenarios & Possibilities**
  - Steady state → evenly spaced ticks on the grid.
  - Process pause/GC stall → a tick is late; scheduler skips to the correct grid slot rather than firing a burst to "catch up" (see 7.4.2.1).
  - Clock jump forward → many grid slots skipped → emit at most one catch-up tick + a "ticks were missed" note, not a flood.
  - Cadence faster than downstream can process → backpressure risk → downstream must be idempotent per seq; module still emits on grid.
  - Phase change mid-interval (e.g. into CLOSE_IMMINENT) → cadence may switch rate cleanly at the next grid slot.
- **Functional Test Case(s)**
  - Given interval=30s and phase=OPEN, When 2 minutes pass, Then 4 ticks emitted with increasing seq aligned to grid.
  - Given the process stalls for 70s at interval=30s, When it resumes, Then it emits the current grid slot's tick (not 2 backlogged ticks) and flags the gap.
  - Given phase changes from OPEN to CLOSED, When evaluated, Then trading-cadence ticks stop.
- **Clear Outcome** — Grid-aligned, monotonically sequenced heartbeat; drift self-corrected; no catch-up bursts.

### 7.4.2 Tick Integrity

#### 7.4.2.1 Missed-tick detector & gap reporter
- **Responsibility** — Detect when a heartbeat was skipped or delayed beyond tolerance and report the gap.
- **Behavior / Actions**
  - Track expected vs actual tick times; if a slot is missed beyond tolerance, record a gap and emit a `CADENCE_GAP(seq_from, seq_to, duration)` note.
  - Never silently swallow gaps near critical deadlines (cutoff, square-off) — escalate, because a missed tick there could mean a missed square-off trigger.
  - Provide downstream a way to know "ticks N..M never arrived" so cycle-counting logic can compensate.
- **Scenarios & Possibilities**
  - Single late tick within tolerance → no gap reported.
  - Multiple consecutive missed ticks → gap reported with span.
  - Gap straddling `squareoff_start` → high-severity escalation; square-off node (7.5) must be re-checked immediately on resume.
  - Gap straddling `last_entry_cutoff` → entry latch must be re-evaluated on resume (likely now closed).
  - Persistent gaps (scheduler dead) → watchdog-level alarm; module may be unhealthy.
- **Functional Test Case(s)**
  - Given expected ticks at :00,:30 and :30 never fires, When :00 of next minute arrives, Then a gap covering the missing slot is reported.
  - Given a gap straddles squareoff_start, When detected, Then a high-severity escalation fires and square-off state is re-evaluated.
  - Given all ticks arrive within tolerance, When evaluated, Then no gap is reported.
- **Clear Outcome** — Every meaningful heartbeat gap is surfaced; gaps over critical deadlines force re-evaluation rather than passing unnoticed.

---

## 7.5 End-of-Day Square-Off Authority

The module's safety-critical core: force everything flat before close and *confirm* it
happened. This is an obligation, escalated until discharged.

### 7.5.1 Square-Off Trigger

#### 7.5.1.1 Square-off signal emitter (with escalation ladder)
- **Responsibility** — Emit `FORCE_SQUAREOFF` at the scheduled time and keep escalating until flat.
- **Behavior / Actions**
  - At `squareoff_start`, emit `FORCE_SQUAREOFF(level=1, soft)`.
  - If still not flat by later thresholds, escalate: `level=2 (aggressive)` … `level=3 (hard/market)` approaching `hard_flat_deadline`.
  - Re-emit on each cadence tick while positions remain open and now ≥ squareoff_start (idempotent, level may rise).
  - The signal is advisory-to-executor but *relentless*: it does not stop until 7.5.2 confirms flat or the day ends with an unresolved-position escalation.
- **Scenarios & Possibilities**
  - Positions flatten on level 1 → escalation never advances; clean.
  - Partial fills / slippage → repeated emits at rising urgency.
  - Illiquid expiry-day close → executor struggles → escalates to hard/market level earlier on expiry (uses tighter expiry deadlines from 7.1.2.2).
  - Market halted at square-off (7.2.1.2) → cannot execute → emit unresolved-position escalation instead of futile repeated signals.
  - Clock near close uncertain/drifting → fail-safe: if unsure whether past squareoff_start, assume yes and emit.
  - Square-off starts but a *new entry* somehow requested → entry latch (7.3.1.2) already blocks; defence in depth.
- **Functional Test Case(s)**
  - Given now=squareoff_start and positions open, When tick fires, Then `FORCE_SQUAREOFF(level=1)` emitted.
  - Given positions still open near hard_flat_deadline, When ticks continue, Then level escalates toward hard/market.
  - Given the market is halted at squareoff_start, When the window arrives, Then an unresolved-position escalation is raised rather than a normal square-off.
- **Clear Outcome** — A relentless, escalating flatten signal that starts on time, intensifies toward close, and never stops while positions remain (or escalates if it cannot act).

### 7.5.2 Square-Off Confirmation

#### 7.5.2.1 Flat-confirmation & exception escalator
- **Responsibility** — Confirm the book is actually flat by close, and escalate hard if not.
- **Behavior / Actions**
  - Consume a position-state input (open positions count / flat flag) to decide whether square-off succeeded.
  - Stop re-emitting `FORCE_SQUAREOFF` only once flat is confirmed.
  - If `hard_flat_deadline`/close passes with positions still open → emit `SQUAREOFF_FAILED` (overnight-risk) escalation — the single worst outcome this module exists to prevent.
  - Record/emit a daily "flat by close: yes/no + time-to-flat" outcome for audit.
- **Scenarios & Possibilities**
  - Confirmed flat before deadline → success, signals cease, clean EOD.
  - Position-state feed stale/unavailable → cannot confirm flat → fail-safe: keep emitting square-off + raise "cannot-confirm-flat" alarm (treat as not-flat).
  - Flat then a phantom/late fill reopens a position → re-detect open → resume square-off.
  - Positions open at close due to halt or liquidity → `SQUAREOFF_FAILED` with reason code → operator escalation, overnight-risk flagged.
  - Multiple instruments: one flat, one not → not-flat overall until both confirmed.
- **Functional Test Case(s)**
  - Given position-state shows 0 open before deadline, When evaluated, Then flat confirmed and square-off emission stops.
  - Given position-state is unavailable, When evaluated near close, Then square-off keeps emitting and a "cannot-confirm-flat" alarm fires (assume not flat).
  - Given close passes with positions still open, When evaluated, Then `SQUAREOFF_FAILED` overnight-risk escalation is emitted and the day is recorded as not-flat.
- **Clear Outcome** — Square-off is only considered done on positive flat confirmation; any unconfirmed/failed flatten by close produces a loud, audited overnight-risk escalation.

---

## 7.6 Time-Integrity & Recovery

Cross-cutting robustness: the clock can lie, ticks can vanish, and the process can die
and restart mid-session. This sub-tree makes the module trustworthy under those faults.

### 7.6.1 Clock-Drift & Anomaly Handling

#### 7.6.1.1 Clock sanity & drift detector
- **Responsibility** — Detect when wall-clock time is untrustworthy (drift, jump, stall) and resolve fail-safe.
- **Behavior / Actions**
  - Compare successive observed timestamps against the monotonic tick grid; flag backward jumps, large forward jumps, and stalls.
  - On detected anomaly: prefer the *safer* interpretation near deadlines (closer to close/cutoff), and raise a clock-anomaly alarm.
  - Backward jump → never undo a one-way latch (entry cutoff, square-off start, flat confirmation).
  - Large forward jump → re-evaluate phase, cutoff latch, and square-off immediately (may have skipped critical edges).
- **Scenarios & Possibilities**
  - NTP step correction nudges clock backward a few seconds → flagged, latches preserved.
  - Host clock badly wrong at boot → first reading implausible vs calendar session → fail-safe non-trading until clock sane.
  - Clock frozen (VM paused) → ticks stall → gap detector (7.4.2.1) + this node agree the clock stalled → on resume, jump to correct phase.
  - Forward jump from mid-session to past close → immediately drive square-off (do not "miss" the close).
  - DST/timezone misconfig → all logic anchored to exchange IST; host TZ changes must not shift sessions.
- **Functional Test Case(s)**
  - Given the clock steps backward 5s after the entry latch tripped, When evaluated, Then the latch stays closed and an anomaly is flagged.
  - Given a forward jump from 11:00 to 15:45 (past close), When evaluated, Then phase recomputes to POST_CLOSE/CLOSED and square-off/flat-check fire.
  - Given an implausible boot clock (year 1970), When evaluated, Then the module resolves non-trading and alarms until the clock is sane.
- **Clear Outcome** — Clock faults are detected, never silently trusted near deadlines, and always resolved toward safety (no missed square-off, no reopened latch).

### 7.6.2 Restart-Mid-Session Recovery

#### 7.6.2.1 State reconstruction on cold start
- **Responsibility** — On process (re)start, reconstruct correct session state from wall-clock + calendar alone, without replaying the past.
- **Behavior / Actions**
  - On boot: resolve day-type, timings, deadlines (7.1), then compute *current* phase directly from now.
  - Re-derive latch states from time, not from lost in-memory history: if now ≥ cutoff → entry latch closed; if now ≥ squareoff_start → square-off active.
  - Do **not** replay missed historical edges (no fake morning OPEN at 2pm); emit current-state snapshots instead (e.g. "entries closed", "square-off in progress").
  - Immediately consult position-state: if positions are open and now ≥ squareoff_start (or past close), drive square-off at once.
- **Scenarios & Possibilities**
  - Restart pre-open → normal forward schedule, no special handling.
  - Restart inside entry window → serve "window open, remaining=…", allow entries with correct runway.
  - Restart after cutoff but before square-off → entries closed, idle-but-watching for square-off.
  - Restart inside square-off window with open positions → immediately re-emit `FORCE_SQUAREOFF` at the appropriate (possibly escalated) level.
  - Restart after close with positions still open (crashed through EOD) → immediate `SQUAREOFF_FAILED`/overnight-risk escalation + drive flatten if market still reachable.
  - Restart on a holiday → non-trading, emit nothing tradable.
  - Repeated crash-restart loop near square-off → idempotent emits prevent duplicated side effects; each boot re-derives the same safe state.
- **Functional Test Case(s)**
  - Given a cold start at 14:00 with cutoff=14:30, When state reconstructs, Then entries are allowed with ~30m remaining and no synthetic morning OPEN is emitted.
  - Given a cold start at 15:20 (past squareoff_start) with open positions, When reconstructed, Then `FORCE_SQUAREOFF` is emitted immediately at the correct escalation level.
  - Given a cold start at 16:00 (post-close) with positions still open, When reconstructed, Then an overnight-risk escalation fires immediately.
- **Clear Outcome** — After any restart, the module's state matches what the clock+calendar imply *right now* — latches, phase, and square-off all consistent — with no replayed past events and no missed obligations.

---

## Suggestions (for bubble-up)

These are market-condition scenarios that surfaced while decomposing Module 7 but whose
correct handling spans **multiple modules**. They belong to system-wide review, not to
Module 7 alone. Module 7 can *signal* them; it cannot *resolve* them by itself.

1. **Expiry-day cutoff & square-off profile.** Expiry afternoons are uniquely whippy and
   pin-prone, and weekly options near expiry can gap or go illiquid fast. Module 7 can
   apply tighter cutoffs/earlier square-off on expiry (7.1.1.2 / 7.1.2.2), but *how
   aggressive* is a risk+strategy decision. Recommend a system-wide expiry policy that
   Module 7 consumes, rather than Module 7 inventing the magnitude.

2. **Half-day / special-session compression.** On half-days the entire entry window can
   collapse to near-empty, meaning "trade nothing today" may be the correct outcome. This
   needs an explicit cross-module stance: does the system *want* to trade compressed
   sessions and muhurat at all? Module 7 will faithfully time whatever is decided, but the
   go/no-go on tiny sessions is a portfolio-level call.

3. **Mid-session exchange halt.** A halt freezes entries (Module 7 handles) but also
   freezes the *executor's* ability to square off. The dangerous case — halt persisting to
   close — produces a position that physically cannot be flattened. This is a joint
   risk + execution + session problem: needs an agreed protocol (carry with hedge? widen
   square-off window? operator override?) that no single module owns.

4. **Late-day illiquidity for square-off.** Even without a halt, the last minutes can be
   too thin to exit a defined-risk spread without severe slippage. Module 7's escalation
   ladder (7.5.1.1) raises urgency, but the trade-off between "exit at any price now" vs
   "slightly later, better fill, more time-risk" is a risk-management policy. Recommend a
   system-wide square-off-aggression curve that Module 7 times and the executor obeys.

5. **Calendar freshness as a system dependency.** Module 7 fails safe on calendar gaps,
   but a stale calendar (missing a newly-announced holiday, or a changed expiry weekday)
   silently degrades every module that trusts the clock. Recommend a system-level calendar
   integrity check / refresh SLA, owned outside Module 7, that this module can assert
   against on each boot.

6. **Cannot-confirm-flat / overnight-risk escalation routing.** When `SQUAREOFF_FAILED`
   fires, *who* gets paged and *what* automated fallback runs (hedge, alert, broker
   square-off API) is an operations + risk concern. Module 7 should only raise the
   high-severity event; the response runbook is system-wide.
