# Module 11 — Connectivity & Auth

## 11.0 Module Overview

**Role.** Module 11 is the system's single point of contact with the broker/exchange gateway. Its job is to establish, prove, and continuously maintain a *healthy, authenticated session* so that every downstream module (market data, order placement, risk, position management) can assume "the pipe is up and I am who I say I am." It owns nothing about *what* flows through the pipe — only that the pipe exists, is authenticated, and its health is honestly reported.

**Inputs (plain data).**
- Credentials/config: user id, password/secret, API key/app-id, API secret, TOTP seed, gateway URLs (REST base + WebSocket base), timeouts, backoff/limit knobs.
- Connection events: socket open/close/error, ping/pong frames, HTTP status codes, gateway error payloads, clock/time source.

**Outputs (plain data).**
- An authenticated session artifact: token/session-id (+ derived signing material), validity window, scope.
- Connection-health status: a small, honest, machine-readable status object (state enum, last-good timestamp, reason code) that consumers poll or subscribe to.

**Boundary discipline (what this module must NOT do).**
- It does not decide trading actions, does not interpret market data, does not place orders. It only guarantees a session to do those things.
- It must **fail closed**: if it cannot prove the session is authenticated *and* live, it must report `DOWN`/`DEGRADED` rather than letting consumers believe a dead pipe is alive. A false "healthy" is the worst possible failure of this module — it invites blind order submission into a void.
- Indian-market specifics that shape the design: most retail broker gateways (Shoonya/Finvasia, Zerodha Kite, etc.) issue a **daily session token that expires/forcibly invalidates around end-of-day or next pre-open**, require **TOTP-based 2FA** at login, enforce **REST rate limits** and a **separate WebSocket session** for streaming. The module must treat "token good all day, dies overnight" as the normal lifecycle, and "forced single active session per credential" as a real constraint.

**Design tensions surfaced for grilling.**
- Proactive token refresh vs. reactive re-login on 401.
- One shared session vs. separate REST and WebSocket session objects with independent health.
- Aggressive reconnect (fast recovery) vs. backoff (avoid lockout / rate-limit ban).
- Caching a session across process restarts (fast cold-start) vs. always-fresh login (correctness/security).

**Decomposition map.**
- 11.1 Credential & Config Management
- 11.2 Login / Authentication Flow
- 11.3 Token / Session Lifecycle
- 11.4 Keep-Alive / Heartbeat
- 11.5 Reconnect & Backoff
- 11.6 Multi-Session / Multi-Channel Management
- 11.7 Rate-Limit Handling
- 11.8 Health Reporting

---

## 11.1 Credential & Config Management

Owns the safe acquisition, validation, and in-memory custody of everything needed to log in — without leaking it.

### 11.1.1 Secure credential ingestion & custody

- **Responsibility** — Load credentials/secrets from a trusted source and hold them so they never leak to logs, errors, or disk in clear form.
- **Behavior / Actions**
  - Read secrets from environment / secret store / encrypted file at startup; never hard-code.
  - Wrap secrets in a redacting type so `repr`/`str`/log/exception serialization prints `****`.
  - Zero/scrub from memory on shutdown where the runtime allows; never write secrets to trade logs or health output.
  - Hand the secret to the login routine only at the moment of use.
- **Scenarios & Possibilities**
  - Secret missing or empty string.
  - Secret present but contains trailing whitespace/newline from file read.
  - A stack trace during login risks dumping the password into a crash log.
  - Secret store unreachable (vault down) at cold start.
  - Operator accidentally commits secret to repo (out of scope to prevent, but module must not echo it back).
- **Functional Test Case(s)**
  - Given a credential object holding a password, When it is logged / str-formatted / raised in an exception, Then the output shows a redaction marker and never the plaintext.
  - Given a missing required secret, When the module initializes, Then it raises a clear typed "credential missing: <name>" error and reports health `DOWN(config)` — it does not attempt login with a null secret.
- **Clear Outcome** — Secrets are usable internally, invisible externally; absence is a clean, named failure, not a confusing login error.

### 11.1.2 Config validation & normalization

- **Responsibility** — Validate the connection config shape/values before any network call.
- **Behavior / Actions**
  - Check presence + type of every required field (URLs, ids, timeouts, limits).
  - Normalize: trim whitespace, enforce URL scheme (`https://`, `wss://`), coerce numeric knobs, apply documented defaults for optional knobs.
  - Reject contradictory/nonsensical values (negative timeout, backoff cap < base).
- **Scenarios & Possibilities**
  - REST base URL set but WebSocket URL missing.
  - Timeout set to 0 or absurdly high.
  - Wrong scheme (`http` for a gateway that mandates TLS).
  - Stale URL from a deprecated gateway endpoint.
- **Functional Test Case(s)**
  - Given a config with a negative `connect_timeout`, When validated, Then a `ConfigError` is raised naming the field; no socket is opened.
  - Given a valid config missing optional `backoff_jitter`, When validated, Then the documented default is applied and validation passes.
- **Clear Outcome** — Only structurally valid config ever reaches the network layer; bad config fails fast at boot with a precise message.

### 11.1.3 TOTP / 2FA secret handling & code generation

- **Responsibility** — Turn a stored TOTP seed into a valid time-based one-time code at login.
- **Behavior / Actions**
  - Derive the current TOTP from seed + current UTC time + standard step (e.g. 30s).
  - Guard against clock skew: optionally probe a trusted time source; refuse if local clock is grossly off.
  - Treat the seed like any other secret (11.1.1 custody rules).
- **Scenarios & Possibilities**
  - Local system clock drifted > one TOTP window → generated code is already invalid.
  - Code generated at the boundary of a 30s step and consumed in the next window → server rejects.
  - Seed stored base32 vs raw bytes mismatch → every code wrong.
  - Broker switches from TOTP to push/app-approval 2FA (design must allow swapping the 2FA provider).
- **Functional Test Case(s)**
  - Given a known seed and a fixed reference time, When a TOTP is generated, Then it equals the RFC-6238 expected value (golden vector).
  - Given local clock skewed by > one step beyond tolerance, When a code is requested, Then the module raises `ClockSkew` and reports health `DOWN(clock)` rather than emitting a doomed code.
- **Clear Outcome** — A correct, fresh 2FA code is produced whenever the clock is trustworthy; an untrustworthy clock is caught before it causes opaque login rejections.

---

## 11.2 Login / Authentication Flow

The orchestrated sequence that turns credentials into a first valid session, and that *distinguishes auth failure from network failure*.

### 11.2.1 Initial login orchestration (REST)

- **Responsibility** — Run the broker's login handshake end-to-end and obtain the first session token.
- **Behavior / Actions**
  - Assemble login request (hash/sign password per broker spec, attach app-id/api-key, attach TOTP from 11.1.3).
  - POST to login endpoint with a bounded timeout.
  - Parse success payload → hand token to 11.3.1; on non-success, classify (11.2.3).
  - Emit a single structured login-attempt record (no secrets).
- **Scenarios & Possibilities**
  - Happy path: 200 + token.
  - Password hashing scheme mismatch (e.g. wrong SHA variant) → consistent reject.
  - Gateway up but returns maintenance/holiday banner before market open.
  - Partial response / truncated JSON.
  - Login succeeds but returns a token already near expiry.
- **Functional Test Case(s)**
  - Given valid credentials and a mocked gateway returning a token, When login runs, Then a session is produced and health transitions `CONNECTING → UP`.
  - Given the gateway returns a malformed body, When login runs, Then it is treated as a retryable transport-class failure (not an auth rejection) and no token is fabricated.
- **Clear Outcome** — A single, well-ordered login produces a real token or a correctly-classified failure — never a guessed/placeholder token.

### 11.2.2 2FA / TOTP submission step

- **Responsibility** — Supply and, if needed, re-supply the second factor within the login flow.
- **Behavior / Actions**
  - Insert the TOTP at the correct handshake stage (single-step vs two-step login depending on broker).
  - On "invalid OTP" specifically, regenerate once for the *next* time window and retry a bounded number of times.
- **Scenarios & Possibilities**
  - OTP consumed too late (window rolled) → one fresh retry should succeed.
  - Broker enforces "OTP can only be tried N times" before lockout → must not burn all attempts on stale codes.
  - 2FA step returns a different error than the password step (must be distinguished, see 11.2.3).
- **Functional Test Case(s)**
  - Given the gateway rejects the first OTP as expired but accepts a freshly-generated one, When login runs, Then it retries once with a new code and succeeds within the attempt budget.
  - Given the gateway returns "OTP attempts exceeded", When encountered, Then the module stops, reports `DOWN(auth_locked)`, and does not keep retrying.
- **Clear Outcome** — The second factor is delivered correctly and recovered from a single window-roll, without blindly exhausting the broker's OTP attempt limit.

### 11.2.3 Auth-failure vs network-failure discrimination

- **Responsibility** — Classify every failed attempt into a small, actionable taxonomy that drives whether to retry, re-login, or stop.
- **Behavior / Actions**
  - Map outcomes to classes: `AUTH_REJECT` (bad creds/OTP/locked — do NOT blindly retry), `TRANSPORT` (timeout/DNS/TLS/5xx — retry with backoff), `RATE_LIMIT` (429/limit — honor retry-after), `GATEWAY_DOWN` (maintenance/holiday — slow retry), `UNKNOWN` (fail closed, surface loudly).
  - Use HTTP status + broker error code + exception type together; never infer auth failure from a bare timeout.
- **Scenarios & Possibilities**
  - 401 with broker code "session expired" vs "invalid password" — both auth-class but different remediation (re-login vs human).
  - TLS handshake error misread as auth failure → would wrongly stop retrying a recoverable network blip.
  - Bad-credential loop: retrying a wrong password could trigger account lockout — classification must prevent this.
- **Functional Test Case(s)**
  - Given a connection timeout, When classified, Then class is `TRANSPORT` and the reconnect/backoff path is taken (not a credential stop).
  - Given HTTP 401 "invalid password", When classified, Then class is `AUTH_REJECT`, retries stop, health is `DOWN(auth)`, and an operator-visible alert reason is set.
- **Clear Outcome** — The system never retries an unfixable credential error into a lockout, and never gives up on a recoverable network error.

### 11.2.4 Login rate-limit / lockout protection

- **Responsibility** — Bound login attempt frequency so the module cannot self-inflict an account lockout.
- **Behavior / Actions**
  - Cap consecutive login attempts; enforce a minimum gap between attempts; escalate the gap on repeated `AUTH_REJECT`.
  - On `AUTH_REJECT`, switch to "needs human" mode rather than auto-hammering.
- **Scenarios & Possibilities**
  - Process crash-loop restarting and re-logging in every few seconds.
  - Multiple module instances logging in with the same credential simultaneously.
  - Broker's "too many login attempts, locked for 30 min" response.
- **Functional Test Case(s)**
  - Given 3 consecutive `AUTH_REJECT`s, When a 4th login is requested, Then the module refuses to attempt and reports `DOWN(auth)` until reset by operator/config.
  - Given a restart loop, When login is attempted faster than the min-gap, Then attempts are spaced to the configured floor.
- **Clear Outcome** — A wrong/stale credential degrades to a halted, alerting state — it never escalates into a broker-side lockout.

---

## 11.3 Token / Session Lifecycle

Custody of the live token from acquisition to expiry to forced renewal.

### 11.3.1 Token acquisition & parsing

- **Responsibility** — Extract and structure the token + metadata from the login response.
- **Behavior / Actions**
  - Parse token string, scope, and any server-stated validity/expiry; wrap in an immutable session object with `issued_at`.
  - If the broker gives no explicit expiry, attach a conservative configured TTL (e.g. "valid until next pre-open").
- **Scenarios & Possibilities**
  - Token present but expiry field absent → must assume daily-expiry default, not "infinite".
  - Token field name/shape changes after a broker API update.
  - Two tokens returned (REST token + websocket access key) → both must be captured.
- **Functional Test Case(s)**
  - Given a login response with token but no expiry, When parsed, Then the session carries the configured default TTL and a flag "expiry_assumed".
  - Given a response missing the token field, When parsed, Then it is a `TRANSPORT/UNKNOWN` failure — no empty session is published.
- **Clear Outcome** — Every published session has a token and a *known or conservatively-assumed* expiry; never an unbounded-validity assumption.

### 11.3.2 Expiry tracking & proactive refresh

- **Responsibility** — Track time-to-expiry and renew before downstream calls start failing.
- **Behavior / Actions**
  - Maintain a countdown to expiry; trigger re-login at a configured safety margin before expiry.
  - Coordinate refresh so in-flight requests aren't orphaned (atomic swap of the session object).
  - Respect daily-cycle reality: schedule the natural re-login at/after the broker's daily reset.
- **Scenarios & Possibilities**
  - Broker forcibly invalidates the token early (e.g. another login elsewhere) → expiry timer is wrong, must also react to 401 (11.3.3).
  - Refresh fires during a burst of order activity → swap must not drop or mis-sign in-flight requests.
  - Clock drift makes the local countdown inaccurate.
  - Refresh at exactly market close vs holding flat — refresh that races EOD square-off (bubble-up).
- **Functional Test Case(s)**
  - Given a token with TTL T and safety margin M, When clock reaches T−M, Then a refresh login is initiated while the old session stays valid until the new one is confirmed.
  - Given refresh succeeds, When it completes, Then consumers observe an uninterrupted `UP` health and start using the new token atomically.
- **Clear Outcome** — Sessions are renewed ahead of expiry with no visible gap; expiry never silently surprises a downstream order.

### 11.3.3 Forced invalidation & reactive re-login

- **Responsibility** — Detect a server-side-killed session (401/session-expired mid-use) and recover.
- **Behavior / Actions**
  - On a `session expired/invalid` response to any operation, immediately mark health `DEGRADED`, trigger re-login, and signal consumers that the session is momentarily not authoritative.
  - De-duplicate: many concurrent 401s should trigger exactly one re-login, not a storm.
- **Scenarios & Possibilities**
  - Single-active-session brokers: a human logging into the mobile app kills the bot's token mid-day.
  - 401 storm: 10 in-flight calls all see expired → must collapse to one re-login.
  - Re-login itself fails (creds now rejected) → escalate to `DOWN(auth)`.
- **Functional Test Case(s)**
  - Given 5 concurrent calls each receiving "session expired", When handled, Then exactly one re-login is performed and the other callers wait for/retry on the new session.
  - Given the token is killed mid-session and re-login succeeds, When complete, Then health returns to `UP` and a `session_replaced` event is emitted.
- **Clear Outcome** — A server-killed session is recovered once, cleanly, with consumers explicitly warned during the gap.

### 11.3.4 Cross-restart session persistence (cache vs fresh)

- **Responsibility** — Decide whether to reuse a still-valid cached session on process restart or always re-login.
- **Behavior / Actions**
  - Optionally persist the (encrypted) session token + expiry to local store; on restart, validate it with a cheap authenticated probe before trusting it.
  - If probe fails or token expired, fall through to full login.
- **Scenarios & Possibilities**
  - Crash-restart mid-session: reusing the cached token avoids a redundant login (and avoids tripping single-session limits / rate limits).
  - Cached token written, but broker invalidated it overnight → restart next morning must not trust stale cache.
  - Cache file tampered/corrupt.
  - Security trade-off: persisting tokens widens the secret-at-rest surface.
- **Functional Test Case(s)**
  - Given a cached, unexpired token, When the process restarts and the validation probe returns OK, Then it reuses the session without a new login.
  - Given a cached token that the probe reports invalid, When restarting, Then it discards the cache and performs a fresh login.
- **Clear Outcome** — Fast warm restarts when safe; never trusts a cached token without a live validity check.

---

## 11.4 Keep-Alive / Heartbeat

Keeps an established session demonstrably *live*, and detects silent death.

### 11.4.1 REST session keep-alive

- **Responsibility** — Periodically prove the REST session is still authenticated.
- **Behavior / Actions**
  - Issue a lightweight authenticated "are-you-alive" call (e.g. profile/limits) on an interval well inside the token TTL.
  - On failure, route through classification (11.2.3) and possibly reactive re-login (11.3.3).
- **Scenarios & Possibilities**
  - Keep-alive call itself rate-limited → must count against the rate budget (11.7), not flood it.
  - Idle session silently expired between trading actions → keep-alive surfaces it before the next order does.
- **Functional Test Case(s)**
  - Given an idle but valid session, When the keep-alive interval elapses, Then a probe runs and health stays `UP`.
  - Given the probe returns "session expired", When observed, Then 11.3.3 reactive re-login is triggered before any order is attempted.
- **Clear Outcome** — REST session liveness is continuously, cheaply proven; silent expiry is caught proactively, not at order time.

### 11.4.2 WebSocket heartbeat / ping-pong

- **Responsibility** — Maintain and verify the streaming socket's liveness via the protocol's ping/pong.
- **Behavior / Actions**
  - Send pings (or honor server pings) on the broker's required cadence; track last-pong time.
  - Declare the socket stale if no pong/data within a tolerance and hand off to reconnect (11.5).
- **Scenarios & Possibilities**
  - "Zombie socket": TCP connection open, but no data flowing (common on flaky mobile/Wi-Fi or after a network NAT timeout) — only a heartbeat gap reveals it.
  - Server expects client pings at a fixed interval or it disconnects.
  - Market quiet period (low tick volume) misread as a dead socket — heartbeat (not data) must be the liveness signal.
- **Functional Test Case(s)**
  - Given no pong received within the configured tolerance, When the heartbeat check runs, Then the socket is marked stale and reconnect is triggered.
  - Given a quiet market with regular pongs but few ticks, When checked, Then the socket is considered healthy (data scarcity ≠ death).
- **Clear Outcome** — Streaming liveness is judged by heartbeat, not data volume; zombie sockets are detected and torn down promptly.

### 11.4.3 Liveness / staleness arbiter

- **Responsibility** — Combine REST and WebSocket signals into a single "is the connection actually usable now" judgment with a last-good timestamp.
- **Behavior / Actions**
  - Track `last_successful_auth_op` and `last_market_data_heartbeat`; define freshness thresholds.
  - Expose the worst-of view so a healthy REST + dead WS still reports `DEGRADED`.
- **Scenarios & Possibilities**
  - REST fine but WS dead → market data stale while orders still work (dangerous asymmetry — bubble-up).
  - Both stale → `DOWN`.
  - Threshold too tight → flapping; too loose → slow detection.
- **Functional Test Case(s)**
  - Given REST `UP` and WS heartbeat stale beyond threshold, When the arbiter evaluates, Then overall health is `DEGRADED(market_data)` with reason populated.
  - Given both channels fresh, When evaluated, Then overall health is `UP` with a recent last-good timestamp.
- **Clear Outcome** — A single honest, timestamped liveness verdict that never hides a dead channel behind a healthy one.

---

## 11.5 Reconnect & Backoff

Recovers a dropped connection without hammering the gateway into a ban.

### 11.5.1 Drop detection & teardown

- **Responsibility** — Notice a connection has dropped and cleanly tear down the dead resources.
- **Behavior / Actions**
  - Listen for socket close/error events and heartbeat-stale signals; cancel timers, close the socket, mark health.
  - Capture the drop reason for backoff and reporting.
- **Scenarios & Possibilities**
  - Clean server-initiated close (EOD) vs abrupt TCP reset vs TLS error.
  - Half-open connection where close event never fires (covered by 11.4 heartbeat).
  - Repeated rapid drops (flapping link).
- **Functional Test Case(s)**
  - Given a socket emits a close event, When detected, Then all associated timers are cancelled and health goes `RECONNECTING`.
  - Given a heartbeat-stale signal with no close event, When detected, Then the socket is force-closed and treated as a drop.
- **Clear Outcome** — Every drop (explicit or silent) leads to a clean teardown and a single, accurate state transition.

### 11.5.2 Backoff & jitter policy

- **Responsibility** — Space reconnect attempts to recover fast but avoid rate-limit/ban.
- **Behavior / Actions**
  - Exponential backoff from a base to a cap, with randomized jitter; reset to base after a sustained-stable connection.
  - Distinguish reconnect (transport) backoff from login (auth) backoff — different budgets.
- **Scenarios & Possibilities**
  - Gateway outage at the open: every attempt fails — must not melt the rate budget, must keep trying within sane bounds.
  - Thundering herd if multiple instances reconnect in lockstep (jitter mitigates).
  - Backoff never resets because connection is briefly up then drops → "stable for N seconds" gate before reset.
- **Functional Test Case(s)**
  - Given 4 consecutive failed reconnects, When computing the next delay, Then delays increase exponentially toward the cap and include jitter (not identical).
  - Given a connection that stays up beyond the stability window, When it later drops, Then backoff starts again from base.
- **Clear Outcome** — Reconnection is persistent yet polite — fast initial recovery, bounded worst-case rate, no synchronized storms.

### 11.5.3 WebSocket reconnect & resubscribe

- **Responsibility** — Rebuild the streaming socket *and* restore its subscription state after reconnect.
- **Behavior / Actions**
  - Re-handshake the WS (re-auth with current token), then replay the prior subscription set so data resumes.
  - Signal consumers that a data gap occurred over the reconnect interval.
- **Scenarios & Possibilities**
  - Token expired during the outage → WS re-auth must fetch a fresh token first.
  - Reconnect succeeds but resubscribe is forgotten → silent "connected but no data" (a classic, dangerous bug).
  - Subscription list changed while disconnected.
- **Functional Test Case(s)**
  - Given a WS reconnect after a drop, When the socket reopens, Then the full prior subscription set is re-sent and ticks resume.
  - Given the token expired during the outage, When reconnecting, Then a fresh token is obtained before the WS auth frame is sent.
- **Clear Outcome** — A reconnected stream is not just "open" but *resubscribed and flowing*, with an explicit data-gap notice to consumers.

### 11.5.4 Reconnect circuit breaker

- **Responsibility** — Stop infinite reconnect loops and escalate when recovery is hopeless.
- **Behavior / Actions**
  - After a configured max attempts / max duration, open a circuit: stop auto-reconnect, hold `DOWN`, raise a loud operator alert; allow a manual/periodic half-open probe.
- **Scenarios & Possibilities**
  - Broker fully down for an extended window → endless retries waste resources and spam logs.
  - Credentials revoked → reconnect can never succeed; breaker prevents masking it as "still trying".
  - Half-open probe accidentally re-triggers a storm (must be single, spaced).
- **Functional Test Case(s)**
  - Given reconnect fails for the configured max duration, When the threshold is crossed, Then auto-reconnect halts, health is `DOWN`, and an alert reason is set.
  - Given an open circuit, When the half-open probe interval elapses, Then exactly one probe attempt is made; on success the circuit closes and normal operation resumes.
- **Clear Outcome** — Hopeless reconnection converts to a clear, alerting halt with controlled recovery probes — not an invisible infinite loop.

---

## 11.6 Multi-Session / Multi-Channel Management

Owns the distinction between session *types* and *accounts*, and the single source of truth.

### 11.6.1 REST vs WebSocket session distinction

- **Responsibility** — Model REST (request/response) and WebSocket (streaming) as related-but-independent sessions with independent health.
- **Behavior / Actions**
  - Derive both from the same login but track them separately; a failure on one must not silently mark the other unhealthy or vice versa.
  - Share the underlying token where the broker uses one; track per-channel liveness.
- **Scenarios & Possibilities**
  - WS dies, REST fine (data stale, orders OK) — must be representable.
  - REST 401 (token dead) implies WS will also fail → token refresh must propagate to both.
  - Broker uses distinct tokens/keys per channel.
- **Functional Test Case(s)**
  - Given the WS channel drops while REST keeps succeeding, When health is queried, Then REST=`UP`, WS=`DOWN`, overall=`DEGRADED`.
  - Given a token refresh, When it completes, Then both REST and WS channels adopt the new token.
- **Clear Outcome** — Channel health is reported independently yet token lifecycle is shared coherently across both.

### 11.6.2 Multi-credential / multi-account handling

- **Responsibility** — Support more than one credential set (e.g. separate accounts/sub-accounts) without cross-contamination.
- **Behavior / Actions**
  - Key every session, token, rate budget, and health record by account id; isolate failures per account.
  - Sequence logins to respect global gateway limits.
- **Scenarios & Possibilities**
  - One account locked while others must keep trading.
  - Shared rate limit across accounts at the IP/app level.
  - Accidental token reuse across accounts (must be impossible by construction).
- **Functional Test Case(s)**
  - Given account A is `DOWN(auth)`, When account B operates, Then B's session and health are unaffected.
  - Given two accounts logging in, When sequenced, Then the global login rate floor is respected across both.
- **Clear Outcome** — Accounts are fully isolated in token/health, while shared gateway limits are still honored globally.

### 11.6.3 Session registry (single source of truth)

- **Responsibility** — Be the one authoritative place that holds the current session(s) and hands them to consumers.
- **Behavior / Actions**
  - Provide a thread-safe "get current valid session" accessor that always returns the freshest token or a clear "no valid session" signal.
  - Atomically swap sessions on refresh so consumers never read a half-updated token.
- **Scenarios & Possibilities**
  - Two consumers grab the token at the instant of a refresh swap → must both get a consistent (old-then-new) value, never a torn one.
  - A stale reference cached by a consumer outlives a refresh (registry must be the only valid source, consumers should not hoard).
- **Functional Test Case(s)**
  - Given a refresh swaps the session, When concurrent consumers read it, Then each gets either the complete old or complete new session, never a mixed/empty one.
  - Given no valid session exists, When a consumer requests one, Then it receives an explicit `NoValidSession` signal, not a stale/null token.
- **Clear Outcome** — Exactly one authoritative, atomically-updated session source; consumers can never act on a torn or stale token.

---

## 11.7 Rate-Limit Handling

Keeps the module within the gateway's request budget — proactively and reactively.

### 11.7.1 Proactive throttle (token bucket)

- **Responsibility** — Pace outbound auth/keep-alive calls under the broker's published limits before being throttled.
- **Behavior / Actions**
  - Maintain a token-bucket/leaky-bucket per limit scope (per-second, per-minute, login-specific); block or queue when empty.
  - Account for *all* module traffic (login, keep-alive, validation probes) against the budget.
- **Scenarios & Possibilities**
  - Keep-alive + reconnect + validation probes coincide and exceed the per-second cap.
  - Limit shared with downstream order traffic (module must reserve headroom — bubble-up).
  - Burst at the open when everything reconnects at once.
- **Functional Test Case(s)**
  - Given the per-second budget is exhausted, When another auth call is requested, Then it is delayed until a token is available rather than sent immediately.
  - Given calls arrive under the cap, When dispatched, Then none are delayed.
- **Clear Outcome** — The module stays inside the rate budget by design, rarely needing the reactive path.

### 11.7.2 Reactive 429 / Retry-After handling

- **Responsibility** — Correctly back off when the gateway *does* return a rate-limit response.
- **Behavior / Actions**
  - On 429/limit error, parse `Retry-After`/broker hint and pause that scope for the stated duration; if absent, apply a conservative default backoff.
  - Never count a 429 as an auth failure (classification 11.2.3).
- **Scenarios & Possibilities**
  - 429 with no Retry-After header.
  - Repeated 429s indicating a misconfigured throttle (should widen the proactive bucket / alert).
  - 429 on the login endpoint specifically (risk of escalating to lockout).
- **Functional Test Case(s)**
  - Given a 429 with `Retry-After: 30`, When received, Then the affected scope pauses ~30s before the next attempt.
  - Given a 429 with no header, When received, Then a conservative default cooldown is applied and an alert metric is incremented.
- **Clear Outcome** — Rate-limit responses are honored exactly, never misclassified as auth errors, and never escalated into a ban.

---

## 11.8 Health Reporting

Publishes the honest, machine-readable truth about connectivity for the rest of the system.

### 11.8.1 Connection-health status emission

- **Responsibility** — Expose a single status object: state enum + reason code + last-good timestamp(s).
- **Behavior / Actions**
  - Maintain a state machine: `INIT → CONNECTING → UP → DEGRADED → RECONNECTING → DOWN` with reason codes (`auth`, `network`, `rate_limit`, `clock`, `market_data`, `config`).
  - Update on every meaningful transition; make it cheaply pollable and/or subscribable; debounce flapping.
- **Scenarios & Possibilities**
  - Rapid up/down flapping → consumers need a debounced, stable signal.
  - Stale status (status thread itself wedged) → must include its own freshness timestamp so a frozen reporter is detectable.
  - Reason code must be specific enough to drive different downstream reactions (halt new entries vs square off).
- **Functional Test Case(s)**
  - Given the WS goes stale, When status is emitted, Then state=`DEGRADED`, reason=`market_data`, with an updated timestamp.
  - Given the reporter has not updated within its own freshness window, When polled, Then the staleness is itself detectable by the consumer.
- **Clear Outcome** — Consumers always have a current, specific, trustworthy health signal — and can tell if that signal itself has gone stale.

### 11.8.2 Authoritative auth-state exposure

- **Responsibility** — Tell consumers, unambiguously, whether the session is *authenticated and usable right now*.
- **Behavior / Actions**
  - Expose a boolean/enum `is_authenticated` that is true only when a non-expired token exists AND recent liveness was proven.
  - Flip to false the instant a forced invalidation (11.3.3) or expiry is detected — fail closed.
- **Scenarios & Possibilities**
  - Token technically unexpired but server killed it → `is_authenticated` must already be false via reactive detection, not wait for timer.
  - During a refresh swap, brief window where authority is uncertain → represent as `DEGRADED`, not a false `true`.
- **Functional Test Case(s)**
  - Given a forced session invalidation, When auth-state is queried, Then `is_authenticated=false` immediately and consumers are told not to submit orders.
  - Given a valid, liveness-proven token, When queried, Then `is_authenticated=true`.
- **Clear Outcome** — Downstream order/risk modules can gate every action on a single honest, fail-closed auth flag.

### 11.8.3 Metrics, events & alerting hooks

- **Responsibility** — Emit structured events/metrics for observability and operator alerting (no secrets).
- **Behavior / Actions**
  - Emit counters/events: login attempts/success/fail by class, reconnect counts, 429s, time-in-DEGRADED, token-refresh events.
  - Raise an operator alert on `DOWN(auth)`, circuit-open, and persistent `DEGRADED`.
- **Scenarios & Possibilities**
  - Alert fatigue from transient flaps → alert only on sustained/critical states.
  - A secret accidentally included in an event payload (must be impossible via redaction).
  - Metrics pipeline itself down (must not block the connection path).
- **Functional Test Case(s)**
  - Given login fails with `AUTH_REJECT`, When the event is emitted, Then it carries the failure class and account id but no credential material, and triggers an alert.
  - Given the metrics sink is unavailable, When events are emitted, Then connection handling is unaffected (best-effort, non-blocking).
- **Clear Outcome** — Operators get timely, secret-free, de-noised signal; observability never blocks or leaks.

---

## Suggestions (for bubble-up)

These cross-cut Module 11 and other modules; flagged for system-wide treatment, not resolved here.

1. **Auth outage at market open.** If login/connect cannot succeed before the open, the *whole* trading day is at risk. System policy needed: how long does the system wait, does it alert a human, does it forbid all entries, and is there a fallback credential/gateway? Module 11 can only report `DOWN(auth/network)` — the *go/no-go for the day* is a system decision.

2. **Mid-session disconnect while a position is open.** A drop (11.5) or degraded market-data channel (11.4.3/11.6.1) while holding an open option spread is a risk event, not just a connectivity event. The system must decide: block new entries, attempt to flatten via REST even if WS data is stale, or hold. Module 11 must give a *fast, specific* `DEGRADED(market_data)` vs `DOWN(orders)` distinction so risk/position modules can react correctly.

3. **Token expiry mid-order.** A forced invalidation or expiry between order build and order ack (11.3.2/11.3.3) can leave order state ambiguous (did it reach the broker?). Needs a system-level idempotency/order-reconciliation policy after any session swap — Module 11 should emit a `session_replaced`/`auth_gap` event with a precise window so the order module can reconcile rather than blindly resubmit.

4. **Asymmetric channel health (orders up, data stale).** The dangerous case where REST works but WS is dead: the system might place spreads on stale prices. Decide system-wide whether order submission is allowed when market-data freshness is below threshold.

5. **Shared rate budget with order traffic.** Module 11's keep-alive/reconnect traffic competes with order/data traffic for the same gateway limit. A global rate-budget owner (or reserved headroom contract) should be defined so connectivity probes can never starve order placement, and vice versa.

6. **Single-active-session brokers vs human intervention.** On brokers that allow one live session per credential, a human logging into the mobile app silently kills the bot. The system needs an operational rule (dedicated bot credential, or alert-on-eviction) — Module 11 can detect and report it but cannot prevent it.

7. **EOD square-off vs scheduled re-login/refresh race.** Proactive refresh (11.3.2) timed near end-of-day could collide with the system's flat-by-EOD routine. Sequencing should be owned at system level so connectivity maintenance never disturbs the close-out.

8. **Cached-session reuse policy & secret-at-rest.** Persisting tokens across restarts (11.3.4) trades a wider secret surface for faster recovery. The security/ops posture (encrypt, TTL, where stored) is a system-level decision.
