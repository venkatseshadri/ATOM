# Module 15 — Telemetry & Audit

## 15.0 Module Overview

**Role.** Module 15 is the system's memory and conscience. It collects trace events from every operation in the trading system into a structured, durable audit trail, and turns that trail into explanations: *why* a decision was made, *how* a trade lived and died, *what* the system believed at each instant. It does not make trading decisions; it observes, records, and reconstructs.

**Arena framing.** The host system trades Indian NIFTY/SENSEX weekly index options, intraday only (flat by EOD), harvesting theta via defined-risk spreads. That arena imposes specific telemetry demands:
- **Bounded session.** The trading day is a finite window (roughly pre-open to square-off). Telemetry has a natural "session" boundary and an EOD reconciliation point.
- **Regulatory weight.** Real orders hit a real exchange/broker. Disputed fills, mis-priced spreads, and risk-limit breaches must be reconstructable for the operator, and potentially for a broker/exchange/regulator inquiry. The audit trail is evidence.
- **Latency sensitivity.** The decision→order hot path is competing for theta in a moving market. Telemetry must NOT sit on that critical path; capture must be cheap and asynchronous.
- **Burst shape.** Event rate is spiky: quiet during a held position, then a burst at entry, at every adjustment, at SL/TP trigger, and at EOD square-off. Buffering must absorb bursts without dropping the very events that matter most (the order lifecycle).

**Inputs (plain data).** Trace events: `{source, type, payload, timestamp}` emitted by any operation — market capture, signal/decision logic, risk checks, order placement, fills, position management, EOD flatten, and the system's own health.

**Outputs (plain data).** A structured, queryable audit trail (durable store + structured log streams), plus explainability views: decision rationale ("why did we enter / skip / adjust"), full trade lifecycle reconstruction, and integrity attestations.

**Design tensions this module must resolve.**
1. *Completeness vs. hot-path cost* — record everything, slow nothing.
2. *Durability vs. throughput* — never lose an order event, but don't fsync per tick.
3. *Verbosity vs. signal* — console noise for humans vs. machine-parseable structured truth.
4. *Openness vs. integrity* — easy to query, hard to tamper.
5. *Retention vs. footprint* — keep enough history to defend a disputed trade; don't hoard forever.

**Non-goals.** Module 15 does not decide trades, does not enforce risk limits, does not place orders. It is read-mostly downstream of everything. It must remain correct even if every other module is wrong — its job is to faithfully record that wrongness.

**Sub-tree map.**
- 15.1 Event Schema & Taxonomy
- 15.2 Capture & Buffering (hot-path ingress)
- 15.3 Correlation & Trace Identity
- 15.4 Durable Structured Storage
- 15.5 Log Levels & Output Routing (console vs structured)
- 15.6 Decision / Audit Trail Assembly
- 15.7 Explainability & Reconstruction Queries
- 15.8 Integrity & Tamper-Evidence
- 15.9 Retention, Archival & Lifecycle
- 15.10 Self-Telemetry & Health (the watcher watches itself)

---

## 15.1 Event Schema & Taxonomy

### 15.1.1 Canonical Event Envelope

**Responsibility.** Define the single mandatory wrapper that every event of every type shares.

**Behavior / Actions.**
- Mandate a fixed envelope: `event_id` (unique), `timestamp` (monotonic + wall clock), `source` (module/operation id), `type` (taxonomy key), `schema_version`, `correlation_id`/`trace_id`, `severity`, and `payload` (type-specific body).
- Reject or quarantine events missing mandatory envelope fields (never silently drop; route to a dead-letter channel — see 15.2.4).
- Stamp ingest-time even when the producer supplied its own event-time, so clock skew is visible.

**Scenarios & Possibilities.**
- Producer omits `correlation_id` (e.g., a startup/health event with no trade context) → allowed, but flagged as "uncorrelated".
- Two events collide on `event_id` (bad UUID source) → dedupe/flag, keep first, quarantine second.
- Producer sends wall-clock timestamp from a skewed host → both clocks recorded so skew is later detectable.
- Edge: an event whose payload is itself a huge object (full option chain snapshot) → envelope still small; payload size policed by 15.1.4.

**Functional Test Case(s).**
- Given an event missing `type`; When ingested; Then it is routed to dead-letter with reason `MISSING_TYPE`, not stored as a normal event.
- Given an event with both producer event-time and a divergent ingest-time; When stored; Then both timestamps are persisted distinctly.

**Clear Outcome.** Every persisted event carries a complete, versioned envelope; malformed events are quarantined, never lost and never silently normalized.

### 15.1.2 Event Type Taxonomy

**Responsibility.** Enumerate and namespace the event types so consumers can filter by category without guessing.

**Behavior / Actions.**
- Define hierarchical type namespaces, e.g. `market.tick`, `market.bar`, `signal.evaluated`, `decision.entry`, `decision.skip`, `risk.check`, `risk.breach`, `order.submitted`, `order.ack`, `order.fill`, `order.reject`, `order.cancel`, `position.adjust`, `position.sl_trigger`, `position.tp_trigger`, `eod.flatten`, `system.health`, `system.error`.
- Each type maps to exactly one owning category and one payload schema (15.1.3).
- Provide a registry so an unknown type is detectable (not silently accepted as a typo).

**Scenarios & Possibilities.**
- New event type introduced by a producer not yet in the registry → accept-but-flag as `unregistered.<raw>`, surface in self-telemetry (15.10) so taxonomy drift is visible.
- Two producers use slightly different strings for the same concept (`order.filled` vs `order.fill`) → registry aliasing prevents fragmentation.
- Edge: an event legitimately spans categories (a risk breach that also causes an order cancel) → modeled as two correlated events, not one ambiguous type.

**Functional Test Case(s).**
- Given an event with `type=order.flll` (typo, unregistered); When ingested; Then it is stored under `unregistered.order.flll` and counted in the taxonomy-drift metric.
- Given a registered alias `order.filled`→`order.fill`; When ingested; Then it is stored canonically as `order.fill`.

**Clear Outcome.** Every event type is namespaced and registered; unknown types are accepted but flagged, so taxonomy gaps are observable rather than corrupting queries.

### 15.1.3 Per-Type Payload Schemas

**Responsibility.** Define and validate the body shape for each event type.

**Behavior / Actions.**
- Maintain a schema per type (field names, types, units — e.g., prices in paise vs rupees, quantities in lots vs contracts, timestamps in IST/UTC).
- Validate payloads at ingest at a configurable strictness: strict (reject), lenient (store + flag), or off (store raw) — defaulting to lenient so a schema bug never blackholes audit data.
- Record validation outcome alongside the event.

**Scenarios & Possibilities.**
- A producer sends price as a string `"123.45"` instead of integer paise → lenient mode coerces + flags; strict mode quarantines.
- Units mismatch (lots vs contracts) is the classic options bug → schema declares units explicitly so reconstruction (15.7) is unambiguous.
- Edge: payload has extra fields beyond schema → kept (forward-compat), flagged as `extra_fields` so additive producer changes aren't lost.
- Failure: schema registry itself unavailable → fall back to store-raw, never block ingest.

**Functional Test Case(s).**
- Given `order.fill` payload missing `fill_price`; When validated in lenient mode; Then event is stored with `validation=FAILED:missing fill_price` and still queryable.
- Given the schema registry is unreachable; When events arrive; Then they are stored raw with `validation=SKIPPED`, and a `system.health` warning is emitted.

**Clear Outcome.** Payloads are validated with explicit units and graceful degradation; validation status travels with the event so downstream trust is calibrated, and audit capture never blocks on schema problems.

### 15.1.4 Schema Versioning & Evolution

**Responsibility.** Let event shapes evolve over months/years without breaking old-record reads.

**Behavior / Actions.**
- Stamp `schema_version` per event; keep a migration/interpretation map so a query engine can read v1 and v3 records in one result set.
- Enforce additive-only evolution by policy (new optional fields OK; renames/removals require a version bump + interpreter).
- Police payload max-size; oversized payloads (full chain snapshots) are either externalized (store a reference/hash, blob to side store) or truncated-with-marker.

**Scenarios & Possibilities.**
- Reconstruction query spans a schema-change boundary (a trade opened pre-deploy, closed post-deploy) → interpreter normalizes both into one lifecycle view.
- A field's units change between versions (paise→rupees) → interpreter applies the conversion; raw stays untouched.
- Edge: a record with a `schema_version` newer than the reader (downgrade scenario) → reader reports "future schema, partial read" rather than crashing.

**Functional Test Case(s).**
- Given stored events at v1 and v3 for one trade; When a lifecycle reconstruction runs; Then all events appear with fields normalized to the current interpretation, with `schema_version` preserved.
- Given a 5 MB option-chain payload; When ingested; Then the body is externalized to the blob store and the event retains a content hash + reference.

**Clear Outcome.** Schemas evolve safely; historical records remain readable and correctly interpreted across versions, and oversized payloads never bloat the primary trail.

---

## 15.2 Capture & Buffering (Hot-Path Ingress)

### 15.2.1 Non-Blocking Emit API

**Responsibility.** Give producers a fire-and-forget call that never stalls the trading hot path.

**Behavior / Actions.**
- Expose an emit that enqueues to an in-memory ring/queue and returns immediately; serialization + persistence happen off the caller's thread.
- Bound the call's worst-case cost (no synchronous I/O, no lock the writer holds during fsync).
- Provide a synchronous-flush variant ONLY for critical checkpoints (order submit/fill, EOD) where the producer explicitly accepts the cost.

**Scenarios & Possibilities.**
- Hot path emits 500 `market.tick` events in a burst → all enqueued in microseconds; backpressure handled by 15.2.3, not by blocking the caller.
- A producer mistakenly calls the synchronous-flush variant on every tick → guard/metric flags the misuse (it would wreck latency).
- Edge: emit called before the telemetry subsystem is fully initialized (early boot) → events buffered to a pre-init queue and drained on startup, not dropped.
- Failure: the queue is full → policy decides (15.2.3); the emit call still returns fast.

**Functional Test Case(s).**
- Given the writer thread is blocked on disk; When a producer emits; Then the emit call returns within its bounded budget and the event sits in the buffer.
- Given emit is called during pre-init; When the subsystem starts; Then pre-init events are drained into the pipeline in order.

**Clear Outcome.** Producers never block on telemetry; the trading decision/order path latency is independent of storage latency.

### 15.2.2 In-Memory Buffering & Batching

**Responsibility.** Absorb bursts and amortize I/O by batching events before durable write.

**Behavior / Actions.**
- Buffer events in memory; flush on size threshold, time threshold, or a critical-event trigger (whichever first).
- Preserve ordering within a source; batch-write to the durable store (15.4).
- Tune batch/flush so steady-state tick volume doesn't fsync-per-event but order events still hit disk promptly.

**Scenarios & Possibilities.**
- Burst at entry: signal + risk + order-submit + ack arrive in milliseconds → batched together, flushed fast because the batch hits the critical-event trigger.
- Quiet period holding a position → time-threshold flush keeps the buffer from holding the last few events indefinitely.
- Edge: a single critical `order.fill` arrives alone → critical-event trigger forces an immediate flush rather than waiting for the batch to fill.
- Failure: process crash with un-flushed buffer → those events are lost unless WAL (15.2.5) is enabled; quantify the at-risk window.

**Functional Test Case(s).**
- Given 1,000 `market.tick` events in 200 ms; When buffering with size-threshold 100; Then exactly 10 batch writes occur and ordering within source is preserved.
- Given a lone `order.fill` event; When the critical-event trigger fires; Then it is flushed within the critical latency budget regardless of batch fill level.

**Clear Outcome.** Bursts are absorbed and I/O amortized, while critical order events are flushed promptly; steady-state I/O is bounded.

### 15.2.3 Backpressure & Overflow Policy

**Responsibility.** Decide what happens when ingest outpaces drain — and prioritize the events that matter.

**Behavior / Actions.**
- Define per-priority queues: order/risk/decision events are HIGH; market ticks/bars are LOW.
- On overflow, shed LOW-priority events first; NEVER silently drop HIGH-priority (order/decision/risk) — these go to an overflow spill file or block the low producers, never the trade path.
- Emit a `system.health` overflow event recording how many of which class were shed (lossy by design must be *visible*).

**Scenarios & Possibilities.**
- Tick flood saturates the queue → LOW ticks shed first, counted; HIGH order events still flow.
- Sustained overflow (downstream store wedged) → HIGH events spill to an emergency local file so the order lifecycle is never gapped.
- Edge: overflow occurs exactly during an order fill → fill is HIGH, must survive; test this explicitly.
- Failure: spill file also full/unwritable → escalate to console-stderr + health alarm; treat as a Module 15 outage, not a silent loss.

**Functional Test Case(s).**
- Given the queue is full of LOW ticks; When an `order.fill` (HIGH) arrives; Then a LOW event is shed and the fill is retained, with a `telemetry.overflow` event recording the drop count by class.
- Given downstream store is unavailable and spill file is writable; When HIGH events arrive; Then they are written to spill and replayed on recovery.

**Clear Outcome.** Under load the system degrades gracefully and *visibly*: market noise may be shed, but the order/decision/risk audit chain is never silently broken.

### 15.2.4 Dead-Letter / Quarantine Channel

**Responsibility.** Capture events that can't be processed normally instead of discarding them.

**Behavior / Actions.**
- Route malformed-envelope (15.1.1), unregistered-type (15.1.2), and hard schema-failures into a dead-letter store with the original raw bytes + reason.
- Make dead-letter independently queryable and counted in self-telemetry.
- Support reprocessing dead-letter once the schema/registry is fixed.

**Scenarios & Possibilities.**
- A producer ships a broken event format after a bad deploy → all its events land in dead-letter, visible immediately, recoverable after rollback.
- Edge: dead-letter volume spikes (whole producer mis-formatted) → alarm; this is a producer outage signal.
- Failure: dead-letter store unavailable → fall back to raw spill file; still never drop.

**Functional Test Case(s).**
- Given a stream of malformed events; When ingested; Then each appears in dead-letter with raw bytes + reason and the dead-letter counter increments.
- Given the schema is fixed and dead-letter is reprocessed; When replay runs; Then previously-failed events are validated and promoted into the main trail.

**Clear Outcome.** No event is ever truly lost to a format error; bad data is parked, visible, and recoverable.

### 15.2.5 Write-Ahead / Crash Durability of the Buffer

**Responsibility.** Bound how much in-flight telemetry a process crash can lose.

**Behavior / Actions.**
- Optionally append HIGH-priority events to a lightweight WAL before they sit only in volatile memory.
- On restart, replay the WAL into the pipeline before accepting new events; dedupe by `event_id` against the durable store.
- Make the at-risk window (WAL-off) explicit and configurable per priority class.

**Scenarios & Possibilities.**
- Process killed (OOM, deploy) mid-session with order events buffered → WAL replay recovers them; without WAL, the lost window is documented.
- Edge: WAL replay re-injects an event already persisted before crash → dedupe by `event_id` prevents double-count (critical for order events).
- Failure: corrupt WAL tail (partial write) → truncate at last valid record, log the truncation, continue.

**Functional Test Case(s).**
- Given HIGH events in WAL and a simulated crash; When the process restarts; Then those events appear in the durable trail exactly once.
- Given a WAL with a torn final record; When replayed; Then valid records are recovered and the torn tail is logged, not fatal.

**Clear Outcome.** Crash-induced telemetry loss is bounded to a known, configurable window — zero for HIGH-priority order events when WAL is enabled — with no double-counting on replay.

---

## 15.3 Correlation & Trace Identity

### 15.3.1 Trade Lifecycle Correlation ID

**Responsibility.** Bind every event of one trade — from signal to square-off — under a single stable id.

**Behavior / Actions.**
- Mint a `trade_id` at decision/intent time and propagate it through risk check, order submit, ack, fills, every adjustment, SL/TP trigger, and EOD flatten.
- Allow many events and many child orders (legs of a spread) to share one `trade_id`.
- Persist the id on every event so a single query returns the whole life of the trade.

**Scenarios & Possibilities.**
- A defined-risk spread = multiple option legs → all legs' orders carry the same `trade_id` plus their own `order_id`.
- Re-entry after SL (a fresh trade) → new `trade_id`, but linked to prior via a `parent_trade_id` so the re-entry chain is reconstructable.
- Edge: an event is emitted before the `trade_id` exists (e.g., a market signal that *might* become a trade) → carries a provisional `signal_id` that is later linked to the `trade_id` if it converts.
- Failure: producer forgets to propagate `trade_id` → event is uncorrelated; flagged so gaps are detectable (see 15.6.3 / 15.10).

**Functional Test Case(s).**
- Given a spread entry with 2 legs, 1 adjustment, and an EOD flatten; When queried by `trade_id`; Then all leg orders, fills, the adjustment, and the flatten appear under one id.
- Given an SL re-entry; When tracing; Then the new trade links to the prior via `parent_trade_id`.

**Clear Outcome.** Any trade's complete lifecycle is retrievable with one id, including multi-leg spreads and re-entry chains.

### 15.3.2 Span / Causal Trace IDs

**Responsibility.** Capture causality within a single decision cycle — which input caused which action.

**Behavior / Actions.**
- Assign a `trace_id` per decision cycle and `span`/`parent_span` ids so the chain market-snapshot→signal→risk-check→order is causally ordered, not just timestamp-ordered.
- Record parent/child relationships so reconstruction shows *cause*, not merely *sequence* (timestamps alone fail under clock skew/concurrency).

**Scenarios & Possibilities.**
- Two near-simultaneous signals in one cycle → distinct spans under the same `trace_id`, disambiguated by parent links.
- Edge: a retried order (reject→resubmit) → child span links to the original attempt span, so the retry is visibly causal not duplicate.
- Failure: a span emitted with a `parent_span` that never arrived (lost parent) → dangling-span flag, surfaced in integrity checks.

**Functional Test Case(s).**
- Given a decision cycle with snapshot→signal→risk→order; When reconstructed; Then the causal chain is rebuilt correctly even if two events share an identical millisecond timestamp.
- Given an order reject then resubmit; When traced; Then the resubmit span references the reject span as parent.

**Clear Outcome.** Causality within a decision cycle is explicit and reconstructable independent of timestamp resolution or skew.

### 15.3.3 ID Generation, Uniqueness & Propagation Contract

**Responsibility.** Guarantee ids are globally unique, collision-safe, and consistently propagated.

**Behavior / Actions.**
- Use collision-resistant id generation (UUID/ULID with embedded time for sortability).
- Define the propagation contract producers must honor (which id to pass where) and detect violations rather than assume compliance.
- Detect duplicate ids and orphaned ids and report them.

**Scenarios & Possibilities.**
- Restart/clock reset → id scheme must not recycle ids (avoid time-only schemes that collide on clock rollback).
- Edge: a broker-assigned `order_id` is reused across days → namespace broker ids by trading session/date to keep them unique in the trail.
- Failure: two trades minted with the same `trade_id` (bug) → uniqueness check raises an integrity alarm.

**Functional Test Case(s).**
- Given 1M generated ids across simulated restarts and a clock rollback; When checked; Then zero collisions occur.
- Given a duplicate `trade_id`; When ingested; Then an integrity alarm fires and both occurrences are flagged.

**Clear Outcome.** Ids are unique, sortable, and propagation violations are detected — the correlation graph is trustworthy.

---

## 15.4 Durable Structured Storage

### 15.4.1 Append-Only Event Log (Primary Trail)

**Responsibility.** Persist the canonical, immutable, time-ordered stream of all events.

**Behavior / Actions.**
- Write events append-only; no in-place update or delete of historical records (corrections are new compensating events, never edits).
- Partition by trading session/date for bounded files and easy archival.
- Guarantee durability ordering: an event acknowledged as durable is recoverable after crash.

**Scenarios & Possibilities.**
- Steady stream + bursty entries → append-only handles both; partition rolls at session boundary.
- A correction is needed (a value was wrong) → emit a `correction` event referencing the original; the original stays.
- Edge: disk full mid-session → fail to a secondary path / spill and alarm; do not overwrite old partitions.
- Failure: partial write at crash → recovery truncates to last complete record (paired with 15.8 hashing to detect).

**Functional Test Case(s).**
- Given an attempt to modify a historical record; When issued; Then it is rejected and only a new `correction` event is accepted.
- Given a crash mid-append; When recovered; Then the log truncates to the last complete record and a recovery marker is written.

**Clear Outcome.** The primary trail is immutable, append-only, and crash-recoverable; history is never rewritten, only appended to.

### 15.4.2 Query-Optimized Store / Indexes

**Responsibility.** Make the append-only data fast to query by trade, type, time, and source.

**Behavior / Actions.**
- Maintain indexes / a queryable projection (e.g., a columnar/analytical store) keyed by `trade_id`, `type`, `timestamp`, `source`, `correlation_id`.
- Build the query store as a *derived* read model from the immutable log (rebuildable from source of truth).
- Keep the analytical store separate from the hot ingest path to avoid lock contention (a known DuckDB cross-process pitfall in this system's lineage).

**Scenarios & Possibilities.**
- Reconstruction query "all events for trade X" → index on `trade_id` makes it O(matches) not a full scan.
- Index corruption/drift → rebuild from the append-only log; the log is authoritative.
- Edge: a single writer/reader contention scenario → enforce single-writer to the analytical store; readers use snapshots/copies.
- Failure: query store unavailable → queries can still run (slower) directly over the primary log.

**Functional Test Case(s).**
- Given 1M events and a query by `trade_id`; When executed; Then results return via index without full scan.
- Given the derived query store is deleted; When rebuild runs from the primary log; Then the query store is reconstructed identically.

**Clear Outcome.** Queries are fast and the query store is fully rebuildable from the immutable log; ingest and query don't contend.

### 15.4.3 External Blob / Large-Payload Side Store

**Responsibility.** Hold oversized payloads (option-chain snapshots, raw broker responses) out of the primary trail.

**Behavior / Actions.**
- Store large bodies in a side store keyed by content hash; the primary event holds the reference + hash.
- Deduplicate identical large payloads by hash.
- Apply the same retention/integrity policy to blobs as to events they belong to.

**Scenarios & Possibilities.**
- The same option chain referenced by many events in a cycle → stored once, referenced N times.
- Edge: blob missing at reconstruction time (pruned early) → reconstruction shows "payload archived/unavailable" with the hash, not a crash.
- Failure: hash mismatch on read → integrity alarm (15.8), blob treated as corrupt.

**Functional Test Case(s).**
- Given two events with identical large payloads; When stored; Then one blob exists and both events reference the same hash.
- Given a referenced blob is missing; When reconstructing; Then the view degrades gracefully with the hash recorded.

**Clear Outcome.** Large payloads don't bloat the trail, are deduplicated by hash, and their absence/corruption is detectable.

### 15.4.4 Durability & Storage Health Policy

**Responsibility.** Govern fsync, replication, and disk-capacity behavior so the trail survives failures.

**Behavior / Actions.**
- Define fsync/durability tiers per priority (HIGH order events durable promptly; LOW ticks batched).
- Monitor disk capacity; alarm before full; rotate/archive (15.9) proactively.
- Optionally mirror the primary trail to a second location (cloud/secondary disk) for disaster recovery.

**Scenarios & Possibilities.**
- Disk approaching full mid-session → proactive archival + alarm before append failure.
- Edge: mirror lags behind primary → lag metric exposed; primary remains source of truth.
- Failure: primary disk dies mid-session → fail over to secondary path, alarm loudly; reconstruct from mirror.

**Functional Test Case(s).**
- Given disk at 90% capacity; When threshold crossed; Then an archival job triggers and a health alarm fires before any append fails.
- Given the primary path becomes unwritable; When an append occurs; Then it routes to the secondary path and emits a `system.health` failover event.

**Clear Outcome.** Durability is tiered by priority and the trail survives disk-full / disk-loss with proactive alarms and failover.

---

## 15.5 Log Levels & Output Routing (Console vs Structured)

### 15.5.1 Severity / Log-Level Model

**Responsibility.** Classify events by severity so humans and machines can filter consistently.

**Behavior / Actions.**
- Define levels (TRACE/DEBUG/INFO/WARN/ERROR/CRITICAL) orthogonal to event type — a `decision.entry` can be INFO, a `risk.breach` CRITICAL.
- Make levels filterable at output without affecting what is *stored* (store everything; filter only the views).
- Map CRITICAL/ERROR to alerting hooks (operator/Telegram surface) — observability, not just logging.

**Scenarios & Possibilities.**
- Operator sets console to WARN+ during live trading to cut noise → DEBUG ticks still fully stored, just not shown.
- Edge: a flood of ERROR events (cascading failure) → rate-limit the *alert* channel, never the *storage*.
- Failure: a producer mislabels a breach as INFO → taxonomy/severity policy can override severity for known critical types.

**Functional Test Case(s).**
- Given console level WARN; When a DEBUG and a CRITICAL event arrive; Then only CRITICAL prints but both are persisted in full.
- Given 1,000 ERROR events in a second; When alerting; Then alerts are rate-limited while storage records all 1,000.

**Clear Outcome.** Severity controls *views and alerts*, never *retention*; everything is stored, levels only filter what is surfaced.

### 15.5.2 Output Sink Routing (Console / Structured / Alert)

**Responsibility.** Fan one event out to the right destinations simultaneously.

**Behavior / Actions.**
- Route each event to: human console (pretty, level-filtered), structured durable store (full), and conditional alert sink (CRITICAL/ERROR).
- Decouple sinks so a slow/broken sink (e.g., alert webhook down) never blocks the others or the hot path.
- Console output is for humans and is lossy-by-design; structured store is authoritative and complete.

**Scenarios & Possibilities.**
- Telegram/alert webhook is down → alert sink buffers/drops with a metric; console + structured store unaffected.
- Edge: console attached to a slow terminal/pipe → console writes are bounded/dropped, never back-pressuring storage.
- Failure: structured sink down → this is the real outage (see 15.2.3 spill); console keeps a degraded human trail.

**Functional Test Case(s).**
- Given the alert webhook is unreachable; When a CRITICAL event occurs; Then console + structured store still receive it and the alert is queued/dropped with a metric.
- Given a slow console pipe; When a burst arrives; Then structured storage throughput is unaffected.

**Clear Outcome.** Sinks are independent; the authoritative structured store is never compromised by a slow/broken human or alert sink.

### 15.5.3 Sensitive-Data Redaction in Outputs

**Responsibility.** Keep secrets/PII out of human-facing and exportable logs without losing audit fidelity.

**Behavior / Actions.**
- Redact credentials, API keys, account numbers, tokens at the output boundary (console/exports), while the secure structured store may keep a hashed/tokenized form for audit.
- Define a redaction policy per field; never log raw broker session tokens.

**Scenarios & Possibilities.**
- A broker error payload echoes an auth token → redacted in console + exports, hashed in secure store.
- Edge: a new field carrying secrets appears (producer change) → default-deny unknown fields in exports until classified.
- Failure: redaction rule missing → fail safe (redact unknown sensitive-looking patterns) rather than leak.

**Functional Test Case(s).**
- Given an event payload containing an API key; When written to console/export; Then the key is masked; When read from the secure store; Then only a hash is present.
- Given an unclassified field matching a secret pattern; When exported; Then it is redacted by default.

**Clear Outcome.** Secrets/PII never appear in human or exportable outputs; audit value is preserved via hashing/tokenization.

---

## 15.6 Decision / Audit Trail Assembly

### 15.6.1 Decision Record Composition

**Responsibility.** Assemble, per decision, the full set of inputs, rules fired, and the resulting action into one coherent record.

**Behavior / Actions.**
- Gather: market snapshot referenced, indicators/signals evaluated, thresholds compared, risk checks applied, the chosen action (enter/skip/adjust), and the rationale fields the decision logic emitted.
- Link these via `trace_id` (15.3.2) into a single decision record.
- Capture *both* taken and not-taken branches when the decision logic exposes them (why we did NOT enter is as auditable as why we did).

**Scenarios & Possibilities.**
- A skip decision ("conditions not met") → record the specific failing condition + values, so a skipped opportunity is explainable later.
- An entry decision → record every gate that passed and the parameter values at decision time (frozen, not re-derived later).
- Edge: a decision references a market snapshot that arrived late/stale → staleness recorded so the decision is judged against what it *actually* saw.
- Failure: rationale fields absent (decision logic emitted only the action) → record an incomplete-rationale flag rather than fabricate a reason.

**Functional Test Case(s).**
- Given a skip decision; When the record is assembled; Then it names the exact failing condition and the values compared.
- Given an entry decision; When assembled; Then all input values are frozen as-seen, and any staleness is recorded.

**Clear Outcome.** Every decision (taken and not-taken) has a self-contained record of inputs, gates, and rationale, frozen as the system saw them — never reconstructed by re-running logic later.

### 15.6.2 Trade Lifecycle Stitching

**Responsibility.** Roll all of a trade's events into one ordered lifecycle view.

**Behavior / Actions.**
- By `trade_id` (15.3.1), order: intent → risk → submit → ack → fills → adjustments → SL/TP triggers → exit/flatten → reconciliation.
- Compute lifecycle-derived facts: realized P&L, time-in-trade, slippage (intended vs filled), number of adjustments.
- Mark lifecycle state transitions explicitly (PENDING→OPEN→ADJUSTED→CLOSED) so partial/incomplete lifecycles are visible.

**Scenarios & Possibilities.**
- A trade closed at EOD flatten vs hit TP vs hit SL → exit reason captured distinctly.
- Edge: a trade that never fully closed in the trail (missing exit event) → lifecycle shows `CLOSED?` / incomplete, flagged as an audit gap (do not assume closed).
- Edge: partial fills across legs → lifecycle reflects per-leg fill state, not an averaged fiction.
- Failure: out-of-order arrival (fill before ack due to async) → stitching reorders by causal links + timestamps and notes the anomaly.

**Functional Test Case(s).**
- Given a complete trade with 2 legs and an SL exit; When stitched; Then the lifecycle shows correct ordering, exit reason SL, and computed realized P&L + slippage.
- Given a trade missing its exit event; When stitched; Then the lifecycle is marked incomplete and raised as an audit gap.

**Clear Outcome.** Each trade has a single ordered lifecycle with derived facts and explicit state; incomplete lifecycles are flagged, never silently assumed complete.

### 15.6.3 Gap & Anomaly Detection in the Trail

**Responsibility.** Detect missing/expected-but-absent events that would make a reconstruction misleading.

**Behavior / Actions.**
- Encode expected-sequence rules (a submit should be followed by ack/reject; an OPEN trade should reach a terminal state by EOD).
- Flag violations: orphan fills (fill with no submit), dangling spans, trades open past square-off, uncorrelated order events.
- Surface gaps as first-class findings for the operator and for post-incident review.

**Scenarios & Possibilities.**
- An order submit with no ack/reject in the trail → flagged as a potential lost event or a real stuck order (both matter).
- Edge: EOD reached with a trade still OPEN in the trail → critical flag: either a real un-flattened position or a missing flatten event — both demand attention.
- Failure: detection itself misses a gap class → rules are explicit and testable so coverage is auditable.

**Functional Test Case(s).**
- Given a fill event with no preceding submit for its `order_id`; When gap detection runs; Then an `orphan_fill` anomaly is raised.
- Given a trade in OPEN state after the session EOD marker; When detection runs; Then an `open_past_eod` critical anomaly is raised.

**Clear Outcome.** Trail gaps and impossible sequences are detected and surfaced, so reconstructions are never silently incomplete.

---

## 15.7 Explainability & Reconstruction Queries

### 15.7.1 "Why Did We Do X?" Decision Explanation

**Responsibility.** Answer, for any decision, the human question "why" from recorded facts only.

**Behavior / Actions.**
- Given a `trade_id` or decision id, return the decision record (15.6.1) rendered as a human-readable rationale: inputs seen, gates evaluated, the deciding condition.
- Strictly source from stored events; never re-run decision logic to *infer* a reason after the fact (that would fabricate, not reconstruct).
- Distinguish "the system's recorded rationale" from "an analyst's interpretation."

**Scenarios & Possibilities.**
- "Why did we skip entry at 10:15?" → returns the exact failing gate and values at that time.
- Edge: rationale was incomplete (15.6.1 flag) → answer states "recorded rationale incomplete," not a guess.
- Failure: someone asks why for a trade whose decision record is missing → return "no decision record found," an honest gap, not a synthesized story.

**Functional Test Case(s).**
- Given a skip decision record; When asked "why"; Then the response cites only stored gates/values and labels them as recorded.
- Given a missing decision record; When asked "why"; Then the response is an explicit "not recorded," never a reconstruction-by-rerun.

**Clear Outcome.** Decision explanations are faithful to recorded facts; absence of record is reported honestly, never papered over with inferred rationale.

### 15.7.2 Full Trade Reconstruction View

**Responsibility.** Render a complete, chronological, human-readable life of a trade for review or dispute.

**Behavior / Actions.**
- Produce a timeline: every event with timestamp, the snapshot it saw, orders/fills/prices, adjustments, exit reason, P&L, slippage, and any anomalies flagged (15.6.3).
- Make it exportable (for the operator, a broker dispute, or an audit) with redaction (15.5.3) applied.
- Include integrity attestation (15.8) so the export is defensibly authentic.

**Scenarios & Possibilities.**
- Disputed fill price with broker → export the full timeline showing intended price, broker ack, fill, and timestamps.
- Edge: reconstruction across a schema-version boundary → 15.1.4 interpreter normalizes; view stays coherent.
- Failure: blob payloads pruned → view shows references/hashes with "archived," remaining defensible.

**Functional Test Case(s).**
- Given a completed trade; When reconstruction is exported; Then the timeline includes all events, derived P&L/slippage, anomalies, and an integrity attestation, with secrets redacted.
- Given a trade spanning a schema upgrade; When reconstructed; Then all events render coherently under current interpretation.

**Clear Outcome.** Any trade can be reconstructed into a complete, integrity-attested, redacted timeline suitable for review or external dispute.

### 15.7.3 Ad-Hoc Audit Query Interface

**Responsibility.** Let an operator/analyst slice the trail by arbitrary dimensions.

**Behavior / Actions.**
- Support queries by time range, type, source, severity, `trade_id`, anomaly class, exit reason, etc.
- Return results from the query store (15.4.2), falling back to the primary log if needed.
- Bound query cost / paginate so a huge range doesn't exhaust resources.

**Scenarios & Possibilities.**
- "All SL exits today" / "all risk breaches this week" / "all uncorrelated order events" → direct filtered queries.
- Edge: a query spans archived partitions (15.9) → transparently includes archived data or clearly states the boundary.
- Failure: query store stale/rebuilding → results served from primary log with a "degraded/slower" notice.

**Functional Test Case(s).**
- Given a query for all `risk.breach` events in a date range; When executed; Then matching events return paginated with consistent ordering.
- Given a range crossing into archived data; When queried; Then archived partitions are included or the boundary is explicitly reported.

**Clear Outcome.** The trail is freely sliceable by any recorded dimension, with bounded cost and transparent handling of archived/degraded sources.

### 15.7.4 Session / EOD Reconciliation Report

**Responsibility.** Produce the end-of-day audit summary that proves the session is consistent and flat.

**Behavior / Actions.**
- At EOD, assemble: all trades, their terminal states, total realized P&L, count of anomalies/gaps, any positions not confirmed flat, telemetry loss/overflow stats.
- Cross-check that every OPEN trade reached a terminal state (ties to 15.6.3 `open_past_eod`).
- Emit the report as a durable, attested artifact for the day.

**Scenarios & Possibilities.**
- Clean day → report shows all trades terminal, flat confirmed, zero HIGH-priority loss.
- Edge: a position the trail believes is still open at EOD → report headlines it as the day's top exception.
- Failure: telemetry itself shed HIGH events that day → report surfaces the loss window so the day's audit completeness is qualified, not assumed.

**Functional Test Case(s).**
- Given a session with all trades closed and flat; When the EOD report runs; Then it confirms flat, totals P&L, and reports zero HIGH-priority telemetry loss.
- Given a trade still OPEN at EOD; When the report runs; Then it is flagged as a top-priority exception with its `trade_id`.

**Clear Outcome.** Each session ends with a durable, attested reconciliation that proves (or explicitly disproves) flatness, completeness, and audit integrity for the day.

---

## 15.8 Integrity & Tamper-Evidence

### 15.8.1 Per-Record & Chained Hashing

**Responsibility.** Make any post-hoc alteration of the trail detectable.

**Behavior / Actions.**
- Hash each record; chain each record's hash to the previous (hash-chain / Merkle-style) so editing or removing any record breaks the chain downstream.
- Persist chain heads periodically so verification has anchor points.
- Verification walks the chain and reports the first break.

**Scenarios & Possibilities.**
- Someone edits a historical P&L value to hide a loss → chain verification flags the exact broken record.
- Edge: legitimate truncation after crash (15.4.1) → recovery writes a signed recovery marker so the "break" is explained, not falsely alarming.
- Failure: hash of a record can't be computed (corrupt) → flagged as corrupt, chain reports the boundary.

**Functional Test Case(s).**
- Given a tampered historical record; When chain verification runs; Then it reports the first broken link at that record.
- Given a legitimate crash-truncation with recovery marker; When verified; Then the break is reconciled against the marker and not reported as tampering.

**Clear Outcome.** Any silent alteration/removal of trail records is cryptographically detectable; legitimate recoveries are distinguishable from tampering.

### 15.8.2 Append-Only Enforcement & Access Control

**Responsibility.** Prevent edits/deletes of the trail at the access layer, and record who can write.

**Behavior / Actions.**
- Enforce append-only at the storage/permission layer (no UPDATE/DELETE on historical partitions); corrections only via compensating events.
- Restrict write access to the telemetry writer identity; readers are separate/read-only.
- Log all administrative actions on the store itself as meta-events.

**Scenarios & Possibilities.**
- A process (or operator) attempts to delete yesterday's partition → denied + meta-event recorded.
- Edge: archival/retention deletion (15.9) is the *one* sanctioned removal → performed by a privileged, logged, integrity-aware path only.
- Failure: write identity compromised → access logs + hash chain together localize the damage window.

**Functional Test Case(s).**
- Given a delete attempt on a historical partition by a normal process; When issued; Then it is denied and a meta-event records the attempt.
- Given a sanctioned retention deletion; When performed; Then it is logged with authorization and updates integrity anchors.

**Clear Outcome.** The trail is structurally append-only; the only removals are sanctioned, privileged, logged retention actions — everything else is denied and recorded.

### 15.8.3 Clock & Timestamp Trust

**Responsibility.** Ensure timestamps are trustworthy enough to anchor disputes.

**Behavior / Actions.**
- Record both monotonic and wall-clock time; periodically sample a trusted time source and log offset.
- Detect/flag clock jumps (NTP step, host reboot) in the stream so reconstructions account for them.
- For dispute-grade events (orders/fills), prefer the broker/exchange-provided timestamp when available, recorded alongside local time.

**Scenarios & Possibilities.**
- NTP steps the clock backward mid-session → jump flagged; monotonic order preserved via 15.3.2 causal links.
- Edge: broker timestamp and local timestamp disagree on a disputed fill → both recorded; reconstruction shows the discrepancy explicitly.
- Failure: no trusted time source reachable → mark timestamps "unsynced" for that window rather than implying precision.

**Functional Test Case(s).**
- Given a backward clock step; When events span it; Then the jump is flagged and causal ordering remains correct.
- Given a fill with both broker and local timestamps; When stored; Then both are retained and any divergence is queryable.

**Clear Outcome.** Timestamps are dispute-grade: clock anomalies are visible, broker time is preserved for order events, and unsynced windows are honestly marked.

---

## 15.9 Retention, Archival & Lifecycle

### 15.9.1 Tiered Retention Policy

**Responsibility.** Keep data as long as needed for audit/regulation, then release footprint deliberately.

**Behavior / Actions.**
- Define retention tiers by class: order/decision/risk + reconciliation reports kept long (regulatory/dispute horizon); raw market ticks kept short (high volume, low long-term audit value) or downsampled.
- Make retention explicit and policy-driven, never ad-hoc deletion.
- Retention deletes go through the sanctioned integrity-aware path (15.8.2).

**Scenarios & Possibilities.**
- Ticks expire after N days; order lifecycle kept for years → footprint controlled while audit defensibility preserved.
- Edge: a disputed trade falls under legal hold → its records are exempt from retention deletion until released.
- Failure: retention job over-aggressively targets HIGH-value events → class-aware policy + dry-run guard prevents it.

**Functional Test Case(s).**
- Given tick events older than the tick-retention window; When the retention job runs; Then ticks are pruned while order/decision events of the same age are retained.
- Given a trade under legal hold; When retention runs; Then its records are skipped and the hold is logged.

**Clear Outcome.** Footprint is controlled by explicit, class-aware retention; high-value and legally-held records survive; deletions are sanctioned and logged.

### 15.9.2 Archival / Cold Storage & Restore

**Responsibility.** Move aged-but-retained data to cheap storage and restore it on demand.

**Behavior / Actions.**
- Roll closed session partitions to compressed cold storage (local/cloud) with integrity anchors intact.
- Support transparent or explicit restore so reconstruction queries (15.7) can reach archived data.
- Verify archive integrity on write and on restore.

**Scenarios & Possibilities.**
- A dispute about a trade from months ago → restore that session's archive, verify hash chain, reconstruct.
- Edge: archive medium (cloud) slow/unavailable → query reports archived-data latency/unavailability rather than returning a false empty.
- Failure: archive corrupted in transit → integrity check on restore catches it; fall back to mirror if present.

**Functional Test Case(s).**
- Given an archived session; When restored and verified; Then the hash chain validates and reconstruction succeeds.
- Given a corrupted archive; When restored; Then integrity verification fails loudly and a mirror is sought.

**Clear Outcome.** Aged data is cheaply retained yet restorable with integrity intact; archive corruption/unavailability is detected, never silently returned as "no data."

---

## 15.10 Self-Telemetry & Health (the Watcher Watches Itself)

### 15.10.1 Pipeline Health Metrics

**Responsibility.** Continuously expose Module 15's own health so its failures aren't invisible.

**Behavior / Actions.**
- Emit metrics: ingest rate, buffer depth, drop/overflow counts by class, dead-letter rate, write latency, query store lag, disk usage, WAL replay events.
- Alarm on thresholds (HIGH-priority drops > 0, buffer near full, write latency spiking, taxonomy-drift rising).
- These self-metrics are themselves events in the trail (recursively auditable) but must not infinite-loop (rate-limited, separate budget).

**Scenarios & Possibilities.**
- HIGH-priority drop count goes above zero → immediate alarm; this means the audit chain may be gapped right now.
- Edge: self-telemetry generating its own load → bounded, separate budget so monitoring can't starve trading telemetry.
- Failure: the metric emitter itself dies → an external/dead-man's-switch heartbeat (15.10.2) catches the silence.

**Functional Test Case(s).**
- Given a single HIGH-priority drop; When it occurs; Then a CRITICAL self-health alarm fires with the class and count.
- Given self-telemetry under load; When measured; Then its resource budget stays bounded and does not displace trade-event capture.

**Clear Outcome.** Module 15's own degradation is observable and alarmed in real time; a gap in the audit chain announces itself.

### 15.10.2 Liveness / Dead-Man's-Switch & Completeness Audit

**Responsibility.** Detect *silence* — the most dangerous failure, where telemetry stops and no error is raised.

**Behavior / Actions.**
- Maintain a heartbeat; if telemetry events cease during trading hours (when activity is expected), raise an external alarm (silence is a failure, not calm).
- Run periodic completeness audits: expected events per active trade present? sequence rules (15.6.3) satisfied across the live session?
- Cross-check telemetry's view of activity against independent signals (e.g., known market-hours window) so a wedged producer is caught.

**Scenarios & Possibilities.**
- The whole feed/telemetry path silently wedges mid-session (a real failure class in this system's history) → heartbeat absence triggers an out-of-band alarm.
- Edge: legitimately quiet period (holding one position, no market events configured to log) → distinguish "expected quiet" from "wedged" using activity expectations, not raw event count alone.
- Failure: alarm channel down too → secondary alarm path / operator-visible degraded state.

**Functional Test Case(s).**
- Given no telemetry events for longer than the liveness threshold during market hours; When the watchdog checks; Then an out-of-band silence alarm fires.
- Given an active trade with no expected lifecycle events recorded; When the completeness audit runs; Then a completeness-gap alarm is raised.

**Clear Outcome.** Telemetry silence and incompleteness during expected-active periods are detected out-of-band — the audit system cannot fail quietly.

---

## Suggestions (for bubble-up)

These scenarios exceed Module 15's boundary and deserve system-wide treatment. Listed separately for later review.

1. **Lost events under sustained load (audit chain gap).** Module 15 can shed LOW and spill HIGH locally, but if ingest *chronically* outpaces the whole system, the right fix is upstream: producer-side rate governance, sampling policy for `market.tick`, or an architectural decision that some classes are sampled-by-design. The *acceptable loss budget per event class* is a system policy, not a telemetry-local one. Needs Board/architecture ruling on "what may we ever drop, and does dropping ticks while keeping orders satisfy the audit obligation?"

2. **Post-incident reconstruction depends on every producer cooperating.** Faithful lifecycle reconstruction requires every module to mint/propagate `trade_id`/`trace_id` and emit decision rationale. Module 15 can *detect* non-cooperation (uncorrelated events, missing rationale) but cannot *fix* it. A cross-module contract — "every operation MUST emit X with correlation Y" — should be enforced system-wide (e.g., a conformance/integration test gate), otherwise audit gaps are structural. This connects to the existing agent-conformance-harness theme.

3. **Audit gaps around a disputed trade (evidentiary sufficiency).** When a trade is disputed (broker fill, slippage, a risk-limit breach claim), is the recorded trail *sufficient as evidence*? That depends on what other modules chose to emit (did the order module record the broker's raw ack + exchange timestamp? did risk record the limit it checked against?). Module 15 guarantees *faithful storage of what it's given*; it cannot guarantee *what it's given is enough*. Recommend a system-level "evidentiary completeness" review per critical path: define, for each disputable event, the minimum payload that must be captured to defend it externally.

4. **Time authority for disputes.** Whether local clock, NTP, or broker/exchange timestamp is *authoritative* in a dispute is a system/legal decision. Module 15 records all of them and flags divergence, but which one "wins" for P&L reconciliation and external defense must be ruled at system level.

5. **Telemetry is itself a single point of failure for accountability.** If Module 15 goes down, the system might keep trading blind to its own auditability. Bubble up: should a HIGH-priority telemetry outage (e.g., structured store unwritable, 15.2.3 spill exhausted) *halt new entries* as a safety interlock? That couples telemetry health to trading authorization — a deliberate system-level policy choice, not a Module 15 decision.

6. **Retention horizon vs. regulatory reality.** The actual legal/regulatory retention horizon for Indian exchange order records should set 15.9.1 tiers. This is an external compliance input, not an engineering guess.
