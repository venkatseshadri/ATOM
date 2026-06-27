# Module 14 — Ledger & Persistence

## 14.0 Module Overview

**Role.** Module 14 is the single source of truth for *what we hold* and *what it is worth*. It consumes fills and live prices, maintains the current position (open legs with their entry prices and quantities), computes live (unrealized) and realized P&L, and persists all of this durably so the truth survives a process crash or host restart. It is a bookkeeper, not a trader: it makes no entry/exit decisions and sends no orders. It only records, values, persists, and recovers.

**Boundary (what is IN).**
- Fill application → position state (open legs, signed qty, average entry price).
- Average-price / per-leg cost-basis accounting.
- Mark-to-market valuation from live prices.
- Realized vs unrealized P&L separation.
- Durable, atomic, single-writer persistence.
- Crash/restart recovery from persisted state + replay.
- Reconciliation against broker-reported positions.
- End-of-day finalization (flat assertion, realized roll-up, archival).
- Historical record (immutable journal) for later analysis.

**Boundary (what is OUT — do not invent).** Order routing, order-state machine, strategy/signal logic, risk limits, SL/TP exit triggers, sizing, broker connectivity transport. Module 14 receives *fills as plain data* and *prices as plain data*; how they were produced is not its concern.

**Core invariants (the spine of every leaf).**
1. **Idempotency** — the same fill applied twice must not move the position twice (dedupe by order/fill id).
2. **Conservation** — realized + unrealized P&L is internally consistent with cost basis and marks; no value is created or destroyed by bookkeeping.
3. **Durability before acknowledgement** — state is not considered "applied" until it is committed to durable storage.
4. **Single writer** — exactly one process may mutate the ledger at a time.
5. **Append-only truth** — the fill journal is immutable; derived state (position, P&L) is reconstructible from it.
6. **Recoverability** — after any crash, the position can be rebuilt deterministically and reconciled against the broker.

**Sub-tree map.**
- 14.1 Position State Engine (apply fills → legs)
- 14.2 Cost-Basis & Average-Price Accounting
- 14.3 Mark-to-Market Valuation
- 14.4 P&L Computation (realized vs unrealized)
- 14.5 Durable Persistence & Atomic Writes
- 14.6 Crash/Restart Recovery & Replay
- 14.7 Single-Writer Integrity
- 14.8 Broker Reconciliation
- 14.9 End-of-Day Finalization
- 14.10 Historical Record & Query
- 14.11 Time, Identity & Clock Discipline (cross-cutting)

Two structural approaches are surfaced across the tree where they matter (and weighed, not chosen):
- **Approach A — Event-sourced:** the fill journal is the truth; position & P&L are projections recomputed by replay.
- **Approach B — Snapshot-of-record:** a mutable position record is the truth; the journal is an audit trail.
The tree is written so leaves work under either, and the trade-offs are stated where they bite (esp. 14.5, 14.6, 14.10).

---

## 14.1 Position State Engine

Owns the transformation `fill → updated open-leg set`. The heart of "what we hold."

### 14.1.1 Fill Ingestion & Validation

**Leaf — Responsibility.** Accept an inbound fill, validate its shape and admissibility before it is allowed to touch position state.

**Behavior / Actions.**
- Validate required fields present: order id, leg identifier (symbol/strike/right/expiry), fill price, qty, status, timestamp.
- Range-check: price ≥ 0 (allow 0 only for explicit expiry-worthless events if modeled), qty ≠ 0, qty an integer multiple of lot size.
- Normalize the leg key to a canonical instrument identity (so the same option always maps to the same leg).
- Classify status: filled / partially-filled / rejected / cancelled — only fills that represent executed quantity proceed.
- Stamp an ingestion-receipt time distinct from the broker fill time (see 14.11).

**Scenarios & Possibilities.**
- Malformed fill (missing leg, NaN price, qty 0) → reject to a quarantine sink, do not mutate position.
- Negative or absurd price (data glitch) → reject/flag, never silently apply.
- Fill for an instrument the ledger has never seen (valid new leg) → accept, create leg.
- Fill arriving for an already-closed/expired instrument → flag for reconciliation, do not blindly reopen.
- Status = rejected/cancelled but with a non-zero qty (broker inconsistency) → treat as suspect, quarantine.
- Unknown future status string → fail closed (quarantine), do not assume "filled."

**Functional Test Case(s).**
- Given a fill missing `leg`, When ingested, Then it is quarantined and position state is unchanged.
- Given a fill with qty = 0, When ingested, Then rejected as no-op.
- Given a fill with qty = 1.5× lot, When ingested, Then rejected with a lot-size violation.
- Given a well-formed filled fill for a new leg, When ingested, Then it passes to the applier with a canonical leg key.

**Clear Outcome.** Only structurally valid, executed-quantity fills reach the applier; everything else is quarantined with a reason and leaves position state untouched.

### 14.1.2 Idempotent Fill Application (dedupe)

**Leaf — Responsibility.** Guarantee each unique fill changes the position exactly once.

**Behavior / Actions.**
- Maintain a durable set of already-applied fill identities (fill id, or order id + sequence + qty hash if no fill id).
- On apply: check membership; if seen, no-op and log a duplicate-suppressed event; if new, apply and record identity *in the same atomic commit* as the position mutation (see 14.5).
- Handle partial fills: the same order id yields multiple fills; dedupe must key on the fill-level identity, not just order id.

**Scenarios & Possibilities.**
- Exact duplicate redelivery (at-least-once upstream) → suppressed, position unchanged.
- Re-application after a crash mid-commit → because identity + state commit atomically, either both happened or neither; replay is safe.
- Two distinct partials of one order sharing the same order id but different fill ids → both applied.
- Upstream reuses a fill id for a genuinely different fill (id collision/bug) → detect mismatch (same id, different price/qty) and quarantine rather than silently drop.
- Out-of-order arrival (fill B before fill A) → application is commutative for qty/cost accumulation, but ordering matters for realized-P&L lot matching (see 14.2); record broker timestamps to allow deterministic ordering on replay.

**Functional Test Case(s).**
- Given fill F applied once, When F is delivered again, Then position is identical and a duplicate-suppressed event is logged.
- Given a crash between mutating position and recording F's id, When recovery replays F, Then F is applied exactly once (atomic commit guarantees it).
- Given fill id X seen with (price 10, qty 50), When a second message claims id X with (price 12, qty 75), Then it is quarantined as an id-collision, not applied.

**Clear Outcome.** Position reflects each real fill once and only once, regardless of redelivery, retries, or crash-replay.

### 14.1.3 Leg State Update

**Leaf — Responsibility.** Apply an accepted fill to the affected leg's signed quantity and trigger cost-basis update.

**Behavior / Actions.**
- Determine direction: buy adds to signed qty, sell subtracts (short legs carry negative qty).
- Update the leg's net quantity; if a leg's net qty reaches 0, mark the leg closed (retain its record for the day's history, exclude from open structure).
- Detect sign flips (net long becomes net short within one fill) and split the fill into close-then-open portions for correct realized accounting (delegated to 14.2).
- Recompute the *structure* descriptor (e.g., which legs are currently open) for downstream consumers.

**Scenarios & Possibilities.**
- Open a new short leg → leg created with negative qty, entry price set.
- Add to an existing leg (scale-in) → qty grows, average price recomputed (14.2).
- Partial close → qty shrinks toward 0, realized P&L on the closed portion booked.
- Full close → leg net 0, marked closed.
- Over-close / sign flip (sell more than held) → split into close-remaining + open-opposite; flag because intraday defined-risk spreads usually should not flip — possible stray/duplicate fill.
- Close of a leg not currently open (phantom close) → flag for reconciliation; do not create a negative phantom.

**Functional Test Case(s).**
- Given flat, When a sell-50 fill on leg L arrives, Then L is open at qty −50.
- Given L at −50, When a buy-50 fill arrives, Then L is closed (qty 0) and realized P&L booked.
- Given L at −50, When a buy-75 fill arrives, Then L closes 50 and opens +25, with realized booked on 50 and a flag raised.
- Given no open L, When a buy-50 (close) arrives, Then phantom-close flag raised, no negative leg created.

**Clear Outcome.** Open-leg set and per-leg signed quantities always equal the net of validly applied fills; anomalies (flips, phantom closes) are flagged, not silently absorbed.

### 14.1.4 Structure Assembly

**Leaf — Responsibility.** Expose the current multi-leg structure as a coherent position object for consumers.

**Behavior / Actions.**
- Aggregate open legs into a structure view: per-leg (instrument, signed qty, avg entry), plus a structure label if derivable (e.g., "2-leg" / "4-leg defined-risk") purely descriptively — no strategy semantics invented.
- Provide a stable, serializable snapshot with a monotonically increasing version/sequence number.
- Mark structure "flat" when no legs are open.

**Scenarios & Possibilities.**
- Mid-construction state (one leg filled, others pending) → structure exposed as partial/incomplete; consumers must not assume balance.
- Asymmetric legs (one leg larger qty than another due to partial fills) → represented faithfully, flagged as imbalanced.
- Flat → structure is the empty set with a flat marker.

**Functional Test Case(s).**
- Given legs A(+50), B(−50) open, When structure requested, Then a 2-leg balanced view with a version number is returned.
- Given only A(+50) filled while B pending, When requested, Then structure is marked incomplete/imbalanced.
- Given all legs closed, When requested, Then a flat structure is returned.

**Clear Outcome.** Consumers always get a consistent, versioned snapshot of current holdings that faithfully represents partial/imbalanced/flat states.

---

## 14.2 Cost-Basis & Average-Price Accounting

Owns *how much we paid/received* per leg and the lot-matching that defines realized P&L on closes.

### 14.2.1 Average Entry Price Maintenance

**Leaf — Responsibility.** Maintain a correct cost basis per leg as fills accumulate.

**Behavior / Actions.**
- On an opening/scaling fill: new_avg = (old_qty·old_avg + fill_qty·fill_price) / (old_qty + fill_qty), using absolute quantities and the leg's sign convention.
- Keep avg price unchanged on a *reducing* fill (closes consume basis but do not move the remaining average).
- Track gross fill notional separately from net for audit.

**Scenarios & Possibilities.**
- Single fill open → avg = fill price.
- Multiple partials at different prices → weighted average.
- Scale-in after a partial close → average reflects only currently-held lots, not closed ones.
- Zero-qty divide guard → never compute avg when resulting qty is 0.
- Precision drift over many fills → use fixed-point/decimal, not binary float, for price·qty (avoid cent/paisa rounding creep).

**Functional Test Case(s).**
- Given buy 50 @10 then buy 50 @12, When averaged, Then avg = 11 on qty 100.
- Given the above then sell 50, When queried, Then avg stays 11 on qty 50.
- Given a sequence summing to qty 0, When avg requested, Then no divide-by-zero; basis is released.

**Clear Outcome.** Each open leg carries an exact weighted-average entry consistent with the lots currently held.

### 14.2.2 Lot Matching / Realized Disposal Method

**Leaf — Responsibility.** Decide which lots a closing fill disposes of, to compute realized P&L deterministically.

**Behavior / Actions.**
- Apply a single, declared, consistent disposal method. Breadth of options:
  - **Average-cost** (close against the running average) — simplest, order-insensitive; fits intraday theta harvesting well.
  - **FIFO** — closes oldest lots first; needed if per-lot holding analysis matters.
  - **LIFO** — rarely justified intraday; surfaced for completeness.
- Realized on close = (entry_basis_consumed − exit_proceeds) for shorts, or (exit_proceeds − entry_basis_consumed) for longs, sign-normalized.
- Method must be deterministic under replay → if FIFO/LIFO, lots must carry a stable order key (broker timestamp + sequence from 14.11).

**Scenarios & Possibilities.**
- Average-cost: a partial close realizes proportionally; remaining basis unchanged.
- FIFO: out-of-order fill arrival could change which lot is "first" unless ordering is by broker time, not arrival time.
- Tie-break on identical timestamps → secondary key (fill id) for determinism.
- Method must be fixed for the life of a position; switching mid-position corrupts realized totals.

**Functional Test Case(s).**
- Given (under average-cost) short 100 @ avg 11, When buy-to-close 50 @ 9, Then realized = (11−9)·50 = +100.
- Given FIFO with lots [t1:50@10, t2:50@12] and a close-50, When matched, Then the t1 lot is disposed regardless of arrival order.
- Given two lots with equal timestamps, When matched, Then tie broken by fill id deterministically (same result on replay).

**Clear Outcome.** Realized P&L from any close is reproducible bit-for-bit on replay under the declared disposal method.

### 14.2.3 Fees / Charges Attribution

**Leaf — Responsibility.** Attach known per-fill costs to the basis so P&L is net, not gross — *if and only if* fees are provided as input data.

**Behavior / Actions.**
- If a fill carries fee/charge fields (brokerage, taxes, exchange charges), accumulate them against the leg and into realized P&L on close.
- If fees are not provided to Module 14, expose P&L explicitly as *gross* and flag the absence so no one mistakes it for net.
- Never estimate/fabricate fees the module was not given (it is a bookkeeper, not a fee engine).

**Scenarios & Possibilities.**
- Fees present per fill → net P&L computed.
- Fees absent → gross P&L, clearly labeled gross.
- Fees arriving late (post-trade statement adjustment) → supported as an adjustment entry against realized (see 14.10 adjustments), never a silent rewrite.

**Functional Test Case(s).**
- Given fills with fee fields, When realized computed, Then realized is net of those fees.
- Given fills without fee fields, When P&L requested, Then output is labeled "gross — fees not supplied."
- Given a late fee adjustment, When applied, Then realized decreases by exactly that adjustment with an audit entry.

**Clear Outcome.** P&L is unambiguously gross or net per the data actually supplied; fees are never invented.

---

## 14.3 Mark-to-Market Valuation

Owns turning open positions + live prices into a current value.

### 14.3.1 Price Intake & Freshness

**Leaf — Responsibility.** Accept live prices for the instruments held and judge their usability.

**Behavior / Actions.**
- Map each incoming price to a held leg by canonical instrument key.
- Stamp each price with a received time; track per-leg last-good price and its age.
- Define a staleness threshold; beyond it, mark the leg's mark as stale.
- Prefer the price convention used for valuation consistently (e.g., last-trade vs mid) — declare it; do not mix conventions across legs.

**Scenarios & Possibilities.**
- No price yet for a freshly opened leg → mark unavailable, fall back to entry price with an explicit "valued-at-entry" flag.
- Stale price (feed gap) → mark stale; unrealized for that leg flagged low-confidence.
- Price for an instrument not held → ignore (or cache briefly) but never value a non-position.
- Zero/garbage price tick → reject for valuation, keep last good (mirrors the option-ltp clobber failure class).
- Wide bid/ask near expiry / illiquid strike → if only last-trade available, value may lag; flag.

**Functional Test Case(s).**
- Given a held leg with no price, When valued, Then mark = entry price with a "valued-at-entry" flag.
- Given a price older than the staleness threshold, When valued, Then the leg mark is flagged stale.
- Given a 0.0 tick after a good price, When ingested, Then last-good is retained and the tick rejected.

**Clear Outcome.** Every open leg has a mark with a known provenance and freshness state; bad/missing ticks never silently corrupt valuation.

### 14.3.2 Position Valuation

**Leaf — Responsibility.** Compute current market value of each leg and the aggregate.

**Behavior / Actions.**
- Per leg: market value = signed_qty · current_mark · contract_multiplier (lot size / point value).
- Aggregate across legs for total position value.
- Carry a confidence/quality flag derived from the worst leg freshness.
- Recompute on new price ticks and on demand; tag each valuation with the price-set version used.

**Scenarios & Possibilities.**
- All legs fresh → high-confidence valuation.
- One leg stale → aggregate flagged degraded.
- Mid-construction (imbalanced legs) → value is computed honestly on what's open, not on the intended structure.
- Multiplier/lot-size misconfig → guard with a sanity bound; a 100× off value should trip an assertion, not be published.

**Functional Test Case(s).**
- Given legs A(+50 @ mark 12) and B(−50 @ mark 8) with multiplier M, When valued, Then aggregate = (50·12 − 50·8)·M.
- Given B's price stale, When valued, Then aggregate is returned with a degraded-confidence flag.
- Given a multiplier 100× expected, When valued, Then a sanity assertion fires instead of publishing.

**Clear Outcome.** A current, multiplier-correct position value with an attached confidence flag, never silently wrong by a freshness or multiplier error.

---

## 14.4 P&L Computation (Realized vs Unrealized)

Owns the clean separation and reconciliation of the two P&L halves.

### 14.4.1 Unrealized P&L

**Leaf — Responsibility.** Value open legs against marks relative to cost basis.

**Behavior / Actions.**
- Per open leg: unrealized = (current_mark − avg_entry) · signed_qty · multiplier (sign handles long/short).
- Sum to position-level unrealized; carry the valuation confidence flag through.
- Distinguish "₹0 unrealized because flat" from "₹0 because no price / valued-at-entry" — never conflate.

**Scenarios & Possibilities.**
- Short option decayed (mark < entry) → positive unrealized (theta harvest working).
- Mark moved against short → negative unrealized.
- Stale/missing marks → unrealized flagged low-confidence, not reported as a hard number.
- Flat → unrealized is exactly 0 with a flat marker.

**Functional Test Case(s).**
- Given short 50 @ entry 11, mark 9, When computed, Then unrealized = (11−9)·50·M positive.
- Given the leg valued-at-entry (no price), When computed, Then unrealized = 0 flagged "valued-at-entry," distinct from flat-zero.
- Given flat, When computed, Then unrealized = 0 flagged flat.

**Clear Outcome.** Unrealized P&L is correct, sign-consistent, and never silently zero when the real cause is missing data.

### 14.4.2 Realized P&L

**Leaf — Responsibility.** Accumulate locked-in P&L from closed quantity over the day.

**Behavior / Actions.**
- On each close (from 14.2.2), book the realized delta into a running realized total.
- Keep realized as an append-only series of realization events (leg, qty closed, entry basis, exit, amount, time) — supports audit and 14.10.
- Net of fees if supplied (14.2.3).

**Scenarios & Possibilities.**
- Multiple partial closes → realized accumulates monotonically by event, not by recompute.
- Re-entry within the day (close then reopen same instrument) → realized from the first cycle preserved; new cycle's basis is independent.
- Adjustment/correction (late fee, busted trade) → posted as a new realization-adjustment event, never an in-place edit.

**Functional Test Case(s).**
- Given two partial closes booking +100 and −40, When totaled, Then realized = +60 via two events.
- Given close then reopen of the same leg, When realized requested, Then the first cycle's +X is retained independent of the new open.
- Given a busted-trade correction, When applied, Then a reversing event appears and realized reflects it, with the original event still visible.

**Clear Outcome.** Realized P&L is an auditable, append-only accumulation that only changes via explicit events.

### 14.4.3 Total P&L Reconciliation

**Leaf — Responsibility.** Combine realized + unrealized into a coherent total and assert internal consistency.

**Behavior / Actions.**
- total = realized + unrealized; expose all three.
- Cross-check: at full flat, total must equal realized and unrealized must be 0 — assert this at flat transitions.
- Expose day-start baseline so "today's P&L" is unambiguous (intraday-only → typically baseline 0 at session open).

**Scenarios & Possibilities.**
- Flat with unrealized ≠ 0 → invariant violation → raise, do not publish a contradictory total.
- Confidence: if unrealized is low-confidence (stale marks), total inherits the degraded flag.
- Mid-position total fluctuates with marks; realized stays monotone — keep them visibly separate so consumers aren't misled.

**Functional Test Case(s).**
- Given realized +60 and unrealized +20, When totaled, Then total = +80.
- Given flat but unrealized computed as +5, When reconciled, Then an invariant alarm fires (no silent publish).
- Given stale marks, When totaled, Then total carries the degraded-confidence flag.

**Clear Outcome.** Total P&L is always realized + unrealized, provably consistent at flat, and confidence-tagged.

---

## 14.5 Durable Persistence & Atomic Writes

Owns making state survive crashes without ever being half-written.

### 14.5.1 Write-Ahead Journal (fills + events)

**Leaf — Responsibility.** Append every state-changing event durably *before* it is reflected as applied.

**Behavior / Actions.**
- Append each accepted fill / realization / adjustment as an immutable record with a monotonic sequence number, then fsync before acknowledging.
- The journal is the replay source of truth (Approach A) or the audit trail (Approach B) — either way it is append-only and fsynced.
- Records carry enough to reconstruct: fill identity, leg, qty, price, broker time, ingest time, resulting sequence.

**Scenarios & Possibilities.**
- Crash after journal append but before snapshot update → recovery replays the tail; no loss.
- Crash mid-append (torn write) → on recovery, a partial trailing record is detected (checksum/length) and discarded; truth is the last intact record.
- Disk full → fail closed: refuse to acknowledge the fill as applied (better to reprocess than to lose durability) and alarm.
- fsync skipped for speed → forbidden; durability-before-ack is invariant #3.

**Functional Test Case(s).**
- Given a fill appended and fsynced, When the process is killed immediately after, Then recovery still sees the fill.
- Given a torn trailing record, When recovered, Then it is discarded and the prior intact state stands.
- Given disk full, When a fill is ingested, Then it is not acked-as-applied and an alarm fires.

**Clear Outcome.** No acknowledged fill is ever lost, and no partial record is ever mistaken for truth.

### 14.5.2 Atomic Snapshot of Derived State

**Leaf — Responsibility.** Persist position + P&L snapshots atomically so a reader never sees a half-written state.

**Behavior / Actions.**
- Write snapshot to a temp file, fsync, then atomic-rename over the canonical path (POSIX rename atomicity); or use a DB transaction that commits atomically.
- Snapshot records the journal sequence it reflects (the "applied-through" watermark) so recovery knows the replay start point.
- Position-mutation + dedupe-identity + watermark commit together (single transaction) to honor 14.1.2 atomicity.

**Scenarios & Possibilities.**
- Crash during snapshot write → temp file orphaned; canonical untouched; recovery uses canonical + journal tail.
- Snapshot watermark behind journal head → normal; replay the gap.
- Snapshot watermark *ahead* of journal head → impossible-state alarm (journal truncated/corrupt), fail closed.
- Concurrent reader during rename → sees either old or new whole snapshot, never a mix.

**Functional Test Case(s).**
- Given a snapshot at seq 100 and journal at 105, When recovered, Then seqs 101–105 are replayed onto the snapshot.
- Given a crash mid temp-write, When recovered, Then the previous canonical snapshot is intact and used.
- Given a snapshot watermark ahead of the journal, When recovered, Then an impossible-state alarm fires.

**Clear Outcome.** Readers and recovery always see a complete, self-describing snapshot plus a known replay watermark.

### 14.5.3 Storage Engine Choice & Schema (breadth)

**Leaf — Responsibility.** Choose a persistence substrate whose guarantees match single-writer durability, and lay out a schema.

**Behavior / Actions.** Surface options with trade-offs (do not silently pick):
- **Append-only log file + periodic snapshot** — simplest, fewest moving parts, easy fsync/rename reasoning; manual query support.
- **Embedded transactional DB (e.g., SQLite, single writer)** — ACID, easy queries, well-suited to one writer; must enforce one connection writes.
- **Columnar/analytical store (e.g., DuckDB)** — great for 14.10 analysis, *poor* for concurrent live writing (this codebase's repeated lock-contention failure class) → use read-only for history, never as the live single writer.
- Schema: `fills`/`events` (immutable), `positions` (current legs), `pnl` (realized series + latest unrealized), `recon` (broker compare results), `eod` (finalized days).

**Scenarios & Possibilities.**
- Two writers via an analytical store → lock contention / corruption → explicitly disallowed (route live writes to the single-writer log/embedded DB; analytics read a copy).
- Schema migration between versions → versioned schema header; recovery must reject unknown future versions (fail closed).
- Mixing live write DB and research DB → keep separate; analytics reads an export/replica, not the live file.

**Functional Test Case(s).**
- Given the live store, When a second writer attempts to open for write, Then it is refused (see 14.7), not allowed to contend.
- Given a snapshot with a newer schema version than the code, When loaded, Then load refuses and alarms rather than misparsing.
- Given history needs, When analytics runs, Then it reads a replica/export, not the live writer file.

**Clear Outcome.** Live truth lives in a single-writer durable store; analytical engines are read-only consumers, never the live writer.

---

## 14.6 Crash/Restart Recovery & Replay

Owns rebuilding correct state after any stop, expected or not.

### 14.6.1 Cold-Start State Reconstruction

**Leaf — Responsibility.** Rebuild current position + P&L deterministically on startup.

**Behavior / Actions.**
- Load latest atomic snapshot; read its applied-through watermark.
- Replay journal records after the watermark in deterministic (broker-time, fill-id) order, applying through the same idempotent path (14.1.2) so replay is naturally safe.
- Recompute unrealized from current marks once a fresh price set arrives (do not trust pre-crash marks).

**Scenarios & Possibilities.**
- Clean shutdown → snapshot == journal head → zero replay, fast start.
- Dirty crash → snapshot behind head → replay the tail.
- No snapshot at all (first run / lost snapshot) → full replay from journal start.
- Journal + snapshot both missing → cold start to flat, but must NOT assume flat if a broker position might exist → force reconciliation (14.8) before declaring flat.
- Replay nondeterminism risk (FIFO ordered by arrival) → ordering must be by broker time, not arrival (see 14.2.2).

**Functional Test Case(s).**
- Given a clean snapshot at head, When restarted, Then state matches pre-shutdown with no replay.
- Given a snapshot behind by 5 events, When restarted, Then replaying them reproduces the exact pre-crash position.
- Given no local state but a broker holding, When restarted, Then the module does not declare flat until reconciliation runs.

**Clear Outcome.** Startup always yields the exact, deterministic position implied by durable history — and never a false "flat."

### 14.6.2 Restart-With-Open-Position Handling

**Leaf — Responsibility.** Safely resume ownership of a live position that existed before the restart.

**Behavior / Actions.**
- After reconstruction, explicitly classify: flat vs holding-open-legs.
- If holding, surface the open structure prominently and require a reconciliation pass (14.8) before treating valuation/P&L as trustworthy for decisions.
- Re-establish price subscriptions for held legs (request inputs) before publishing confident unrealized.

**Scenarios & Possibilities.**
- Restart seconds before market close holding a position → must recover fast enough for EOD square-off accounting (14.9) to be correct.
- Restart during the day holding multi-leg structure → valuation degraded until fresh marks arrive; flagged.
- Restart after a fill occurred *during* downtime (broker filled while we were dead) → local journal lacks it → only reconciliation will find it (14.8) → this is the missed-fill class, bubbled up.

**Functional Test Case(s).**
- Given an open 4-leg structure persisted, When restarted, Then all 4 legs are restored and flagged "pending reconciliation."
- Given a restart with stale marks, When P&L requested, Then it is returned degraded until fresh prices arrive.
- Given a fill that occurred during downtime, When recovered locally, Then the gap is detectable only via reconciliation (flagged), not silently ignored.

**Clear Outcome.** A restart never loses an open position and never trusts pre-crash valuation; it resumes into a verify-then-trust posture.

### 14.6.3 Corruption Detection & Safe-Mode

**Leaf — Responsibility.** Detect unrecoverable/contradictory state and refuse to operate on a lie.

**Behavior / Actions.**
- Verify checksums on snapshot and journal; detect torn/partial records; detect watermark-ahead-of-journal (14.5.2).
- On corruption: enter read-only safe-mode, do not mutate, raise a loud alarm, and require operator/reconciliation intervention.
- Prefer halting to guessing — a bookkeeper that invents numbers is worse than one that stops.

**Scenarios & Possibilities.**
- Snapshot checksum fail but journal intact → rebuild from journal start, ignore snapshot.
- Both corrupt → safe-mode + reconciliation against broker as the only remaining truth.
- Silent bit-rot (no checksum) → why checksums are mandatory; without them corruption is undetectable.

**Functional Test Case(s).**
- Given a corrupt snapshot and intact journal, When started, Then state rebuilds from journal and a warning is logged.
- Given both corrupt, When started, Then the module enters safe-mode and demands reconciliation before any write.
- Given a checksum mismatch, When detected, Then no derived number is published as trustworthy.

**Clear Outcome.** Corruption is always detected and never silently propagated; the module fails loud and read-only.

---

## 14.7 Single-Writer Integrity

Owns the guarantee that exactly one process mutates the ledger.

### 14.7.1 Write Lock / Ownership

**Leaf — Responsibility.** Enforce mutual exclusion on ledger mutation.

**Behavior / Actions.**
- Acquire an exclusive lock (e.g., flock on the live file / OS advisory lock / single DB write connection) at startup before any write.
- Hold for the writer's lifetime; release on clean shutdown; rely on OS to release on crash.
- A process that cannot acquire the lock must not write — it either exits or runs read-only.

**Scenarios & Possibilities.**
- Second instance launched (cron overlap, manual double-start) → fails to acquire → exits/read-only, never contends (mirrors this codebase's duplicate-writer crash-loop history).
- Crash holding the lock → OS releases → next start acquires cleanly.
- Stale lock file with no live owner → liveness check (pid/heartbeat) distinguishes dead lock from live owner; reclaim only if provably dead.
- Lock on a network filesystem with weak semantics → flagged as unsafe; live store must be on a filesystem with real lock semantics.

**Functional Test Case(s).**
- Given writer A holds the lock, When writer B starts, Then B fails to write and does not corrupt state.
- Given A crashes, When A restarts, Then it reacquires the lock and proceeds.
- Given a stale lock with a dead owner, When a new writer checks liveness, Then it reclaims; with a live owner, it does not.

**Clear Outcome.** At most one mutator exists at any instant; extra processes degrade to read-only or exit, never contend.

### 14.7.2 Reader/Writer Separation

**Leaf — Responsibility.** Let many readers observe state without risking writer integrity.

**Behavior / Actions.**
- Readers consume the atomic snapshot / published state, never hold the write path.
- Provide a consistent read view (versioned snapshot) so a reader sees one coherent state, not a mutation in progress.
- Analytical/history consumers read replicas/exports (14.5.3), not the live writer file.

**Scenarios & Possibilities.**
- Reader during a write → sees pre- or post-write whole snapshot (atomic rename / MVCC), never a tear.
- Slow reader → gets a slightly stale but internally consistent snapshot, which is acceptable and labeled with its version.
- A reader that tries to write → blocked by 14.7.1.

**Functional Test Case(s).**
- Given a write in progress, When a reader reads, Then it gets a coherent prior-or-next snapshot with a version stamp.
- Given a slow reader, When it reads, Then the state is internally consistent even if not the very latest.
- Given a reader attempts a mutation, When it tries, Then it is denied by the writer lock.

**Clear Outcome.** Unlimited consistent reads coexist with exactly one writer; no reader can tear or corrupt state.

---

## 14.8 Broker Reconciliation

Owns confronting the ledger's belief against the broker's record of truth.

### 14.8.1 Position Reconciliation

**Leaf — Responsibility.** Compare ledger open legs/qty against broker-reported positions and classify divergence.

**Behavior / Actions.**
- Pull broker positions (as input data), normalize to canonical leg keys, and diff per leg: qty match / qty mismatch / leg-only-in-ledger / leg-only-at-broker.
- Classify cause hypotheses: missed fill (broker has more), double-applied/phantom fill (ledger has more), instrument-mapping mismatch.
- Produce a reconciliation report; do NOT auto-mutate the ledger on divergence by default — flag for resolution (auto-heal only under an explicit, narrow, logged policy).

**Scenarios & Possibilities.**
- Perfect match → confidence stamp "reconciled@T."
- Broker qty > ledger → suspected missed fill (fill happened during downtime / lost message) → bubble up.
- Ledger qty > broker → suspected double-applied or phantom fill → bubble up.
- Leg only at broker (unknown instrument) → mapping bug or out-of-scope manual trade.
- Reconciliation while orders are in-flight (transient) → tolerate within a settling window before alarming.

**Functional Test Case(s).**
- Given ledger short 50 and broker short 50 on L, When reconciled, Then match with a reconciled timestamp.
- Given ledger short 50 and broker short 100, When reconciled, Then a missed-fill divergence is flagged, ledger untouched.
- Given ledger short 50 and broker flat, When reconciled, Then a phantom/double-applied divergence is flagged.

**Clear Outcome.** Every divergence between ledger and broker is detected and classified; the ledger is never silently overwritten.

### 14.8.2 P&L / Cash Reconciliation

**Leaf — Responsibility.** Sanity-check computed realized/fees against broker-reported P&L or cash deltas when available.

**Behavior / Actions.**
- If broker supplies realized P&L / charges, compare to internal realized within a tolerance.
- Differences attributed to fees-not-supplied (14.2.3) or rounding are explained; unexplained gaps are flagged.

**Scenarios & Possibilities.**
- Match within tolerance → confidence on P&L.
- Internal gross vs broker net → expected fee gap, explained not alarmed.
- Unexplained large gap → flag; possible disposal-method or sign bug.

**Functional Test Case(s).**
- Given broker net realized and internal gross realized, When compared, Then the difference equals supplied/known charges (explained).
- Given an unexplained ₹ gap beyond tolerance, When compared, Then it is flagged for investigation.

**Clear Outcome.** Internal P&L is corroborated against the broker, with every gap either explained or flagged.

### 14.8.3 Reconciliation Cadence & Triggers

**Leaf — Responsibility.** Decide when reconciliation runs.

**Behavior / Actions.**
- Run on: startup-with-open-position (mandatory, 14.6.2), periodically intraday, after any divergence-prone event, and at EOD (14.9).
- Respect a settling window so in-flight orders don't cause false alarms.

**Scenarios & Possibilities.**
- Startup recon → catches downtime fills.
- Periodic recon → catches slow drift early.
- EOD recon → final truth before archival.
- Too-frequent recon during heavy fills → noise; use the settling window.

**Functional Test Case(s).**
- Given a restart with an open position, When the module comes up, Then a mandatory reconciliation runs before P&L is trusted.
- Given an in-flight order within the settling window, When recon runs, Then transient mismatch is tolerated, not alarmed.
- Given EOD, When finalization begins, Then a final reconciliation precedes archival.

**Clear Outcome.** Reconciliation happens at exactly the moments divergence is most likely, without alarm noise from transients.

---

## 14.9 End-of-Day Finalization

Owns closing the books for the session (intraday-only → must end flat).

### 14.9.1 Flat Assertion & Open-Position Alarm

**Leaf — Responsibility.** Verify the position is flat at session end and alarm if not.

**Behavior / Actions.**
- At/after square-off time, assert open-leg set is empty and broker confirms flat (via 14.8).
- If any leg remains open at EOD, raise a high-severity alarm (intraday mandate violated) and record the residual — do not silently carry it.

**Scenarios & Possibilities.**
- Clean flat → finalize.
- Residual open leg (failed square-off elsewhere) → alarm; record as carried risk; this is a serious cross-module event → bubble up.
- Expiry-day legs expiring worthless vs assigned → record the realization correctly (worthless = full premium realized for shorts) only on explicit settlement data, not assumption.

**Functional Test Case(s).**
- Given all legs closed by square-off, When EOD runs, Then finalization proceeds.
- Given one leg still open at EOD, When asserted, Then a high-severity not-flat alarm fires and the residual is recorded.
- Given a short option expiring worthless with settlement data, When finalized, Then full premium is realized.

**Clear Outcome.** The day cannot be closed clean while any risk remains open; residuals are always surfaced, never hidden.

### 14.9.2 Daily Realized Roll-Up & Baseline Reset

**Leaf — Responsibility.** Freeze the day's realized total and set the next session's baseline.

**Behavior / Actions.**
- Sum the day's realization events into a final daily realized figure (net if fees supplied).
- Snapshot the finalized day; reset the intraday unrealized to 0 (flat) and establish the next-day baseline.
- Mark the day record immutable.

**Scenarios & Possibilities.**
- Late adjustment after roll-up (next-day fee correction) → posted against the historical day as an explicit adjustment, not a rewrite (14.10).
- Crash during finalization → finalization is itself an atomic, replayable step (14.5) so it completes or is redone idempotently.

**Functional Test Case(s).**
- Given the day's realization events, When rolled up, Then the daily realized equals their sum and is frozen immutable.
- Given a crash mid-finalization, When restarted, Then finalization completes idempotently with the same result.
- Given a next-day fee correction, When posted, Then it appears as an adjustment, leaving the frozen figure auditable.

**Clear Outcome.** Each day yields one immutable, auditable realized figure and a clean baseline for the next session.

### 14.9.3 Archival & Retention

**Leaf — Responsibility.** Move finalized day state into the historical record and manage live-store size.

**Behavior / Actions.**
- Export the finalized day (fills journal, positions timeline, P&L series, recon results) to the history store/replica.
- Optionally compact/rotate the live journal after successful archival so it doesn't grow unbounded — only after archival is confirmed durable.
- Verify the archived copy before any live-store pruning.

**Scenarios & Possibilities.**
- Archival fails (history store down) → do NOT prune live journal; retry; live remains the fallback truth.
- Pruning before archival confirmation → forbidden (could lose history).
- Long-running process across many days → journal rotation needed to bound recovery-replay time.

**Functional Test Case(s).**
- Given a finalized day, When archived, Then the history store contains a verified complete copy.
- Given archival failure, When pruning is attempted, Then pruning is blocked and the live journal is retained.
- Given many archived days, When the journal rotates, Then recovery replay time stays bounded.

**Clear Outcome.** History is durably captured before the live store is ever trimmed; nothing is lost to rotation.

---

## 14.10 Historical Record & Query

Owns the immutable analytical trail for later study.

### 14.10.1 Immutable Event Journal (audit trail)

**Leaf — Responsibility.** Preserve a complete, append-only, replayable record of everything that happened.

**Behavior / Actions.**
- Persist every fill, realization, mark-snapshot (sampled), reconciliation result, and adjustment with timestamps and sequence.
- Guarantee append-only semantics: corrections are new reversing/adjusting entries, never edits or deletes.
- Make the journal sufficient to reconstruct any past position/P&L state (event-sourcing replay).

**Scenarios & Possibilities.**
- Need to answer "what did we hold at 11:42?" → replay journal to that timestamp.
- Correcting a past error → append a reversal + correct entry; original remains visible for audit.
- Tampering attempt / accidental edit → detectable via sequence + checksums.

**Functional Test Case(s).**
- Given a journal through the day, When asked for the position at 11:42, Then replay reconstructs it exactly.
- Given a correction, When applied, Then a reversing entry exists and the original is still present.
- Given an out-of-band edit to a past record, When verified, Then a checksum/sequence break is detected.

**Clear Outcome.** Any historical state is reconstructible and the record is provably append-only.

### 14.10.2 Analytical Query Surface

**Leaf — Responsibility.** Serve history to downstream analysis without touching the live writer.

**Behavior / Actions.**
- Expose queries over the replica/export: per-day P&L, per-leg realized, hold-time distributions, divergence history.
- Read-only against history; never query the live single-writer store directly (14.5.3 / 14.7.2).
- Stable, versioned schema so old analyses remain reproducible.

**Scenarios & Possibilities.**
- Heavy analytical scan → runs on replica, zero impact on live writer.
- Schema evolution → versioned so historical queries don't break.
- Analyst accidentally points at the live file → blocked/discouraged by access separation.

**Functional Test Case(s).**
- Given a month of history, When per-day P&L is queried, Then results come from the replica with no live-writer contention.
- Given a schema change, When an old query runs, Then versioning keeps it reproducible.
- Given a query aimed at the live store, When attempted, Then it is routed/blocked to the replica.

**Clear Outcome.** Analytics get rich, reproducible history while the live ledger stays uncontended and safe.

---

## 14.11 Time, Identity & Clock Discipline (cross-cutting)

Owns the timestamps and identifiers every other leaf depends on.

### 14.11.1 Timestamp Provenance

**Leaf — Responsibility.** Distinguish and preserve the several relevant times for each event.

**Behavior / Actions.**
- Record broker/exchange fill time (for ordering & realized matching), local ingest time (for latency/audit), and persistence-commit time — never collapse them into one.
- Use a monotonic sequence as the tie-breaker independent of wall-clock.
- Normalize to a declared timezone (IST/exchange) for session boundaries (EOD).

**Scenarios & Possibilities.**
- Clock skew between host and broker → ordering uses broker time + sequence, not host time.
- Daylight/timezone confusion at session boundary → declared exchange TZ avoids EOD off-by-one.
- Two fills with identical broker timestamps → sequence/fill-id tie-break ensures deterministic replay.

**Functional Test Case(s).**
- Given host clock skewed, When fills are ordered, Then broker time governs and replay is deterministic.
- Given two equal-timestamp fills, When ordered, Then the sequence tie-break yields a stable order.
- Given a session-boundary event, When classified, Then exchange-TZ assignment puts it in the correct trading day.

**Clear Outcome.** Ordering, realized matching, and EOD boundaries are deterministic and skew-proof.

### 14.11.2 Identity & Canonical Keys

**Leaf — Responsibility.** Provide stable identifiers for fills and instruments so dedupe and reconciliation are reliable.

**Behavior / Actions.**
- Canonicalize instrument identity (symbol/expiry/strike/right) to one key used everywhere (matches the SENSEX/NIFTY symbol-format pitfalls — wrong key = silent mis-mapping).
- Establish a stable fill identity (broker fill id, or a deterministic composite when absent) used by dedupe (14.1.2) and recon (14.8).

**Scenarios & Possibilities.**
- Same instrument expressed two ways by feed vs broker → canonicalization collapses them; failure = phantom duplicate legs.
- No broker fill id → composite key must be collision-resistant yet deterministic for replay.
- Symbol-format change (broker convention update) → mapping layer flagged as a single point to maintain.

**Functional Test Case(s).**
- Given the same option from feed and broker in two formats, When keyed, Then both map to one canonical leg.
- Given fills without broker ids, When deduped, Then the deterministic composite key prevents double-apply on replay.
- Given a changed symbol convention, When mapping fails, Then it is flagged, not silently mis-mapped.

**Clear Outcome.** One instrument is one leg and one fill is one identity everywhere, making dedupe and reconciliation trustworthy.

---

## Suggestions (for bubble-up)

These scenarios exceed Module 14's authority and need system-wide treatment. Module 14 can *detect, persist, and flag* them, but resolution involves other modules / operator policy.

1. **Restart with an open position.** Module 14 reconstructs and flags "holding, pending reconciliation," but *who re-arms exit/SL monitoring* and whether resumed valuation may drive decisions is a system-level concern. Recommend a system contract: no exit/risk decisions act on Module-14 state until a post-restart reconciliation stamps it trustworthy. (Detected here at 14.6.2 / 14.8.3.)

2. **Ledger-vs-broker divergence.** Module 14 classifies missed-fill / phantom / mapping divergences but must NOT silently self-heal. Needs a system policy: when to auto-correct vs require operator confirmation, and which side (ledger or broker) is authoritative per cause. (14.8.1 / 14.8.2.)

3. **Double-applied fill.** Idempotency (14.1.2) defends within Module 14, but a double-apply that originates upstream (two distinct messages for one execution) or an id-collision can still slip through. Needs a system-wide fill-identity contract and an end-to-end dedupe guarantee, plus the recon backstop. (14.1.2 / 14.8.1 / 14.11.2.)

4. **Missed fill (broker filled while we were down / message lost).** Module 14 cannot manufacture a fill it never received; only reconciliation reveals it. Needs a system path to backfill the missing fill into the journal under audit, and to decide risk posture for a position the system didn't know it held. (14.6.2 / 14.8.1.)

5. **EOD residual open position.** A not-flat-at-close alarm (14.9.1) is a cross-cutting risk event (carried overnight gamma/assignment risk against an intraday mandate). Needs a system runbook: who force-squares, and how the carried position is accounted next session.

6. **Gross-vs-net P&L authority.** If fees are not supplied to Module 14, all P&L is gross (14.2.3). The system must decide where net-of-charges P&L is computed and reconciled so no consumer mistakes gross for net.

7. **Live-store engine for writes.** The codebase's recurring lock-contention failure class implies a system-level rule: analytical engines (DuckDB-style) are read-only history consumers; the live single writer uses a log/embedded-transactional store. Worth ratifying system-wide. (14.5.3.)
