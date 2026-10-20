# Background work and idle behaviour

Durable workers sleep until committed work arrives, a real deadline expires, or
an infrequent repair check becomes due. PostgreSQL rows remain the source of
truth; wake notifications never authorize an access decision or physical effect.

## Committed wake hints

The [wake transport](../backend/app/services/durable_work.py) carries only a closed
work-topic name through PostgreSQL LISTEN/NOTIFY. Existing service owners publish
inside the same transaction that reserves work or changes its eligibility. A
rollback publishes no hint. Lease renewals do not create unnecessary fanout.
The realtime event bus keeps its existing observer and workflow semantics.

Each backend process owns one dedicated listener connection. Subscription completes
before catch-up reads; reconnect repeats subscription and catch-up. Notification
callbacks update bounded generations and wake events, without doing database work
or creating one task per notification. Workers capture a generation before reading
readiness so arrivals during a query or wait setup cannot disappear.

Healthy workers use a **60-second safety interval**. A silently missed hint can
therefore delay otherwise ready work for up to that interval. A detected listener
outage restores each lane's former polling cadence. The listener checks liveness
at 30-second intervals with a five-second timeout and bounded reconnect backoff.
Listener failure is reported as degraded operation; the durable fallback continues.

Each owner reads readiness and its next eligibility deadline using compact indexed
SQL and database time. These reads return scalar scheduling information, take no
row locks and grant no mutation authority. Readiness queries have a five-second
timeout and drain their actual session cleanup before returning. Existing fenced claim and execution
transactions recheck authorization, lease identity and original deadlines.

| Lane | Signals and timed eligibility |
|---|---|
| LPR | Ready ordinal publication, unfinished boundaries, debounce and takeover |
| Event completion | Reserved stages, retries, visitor-link release and claim/eligibility expiry |
| Event publication | Changed event generations, retries and claim expiry |
| Home Assistant presence | Desired generations, barrier release, invalidation and claim expiry |
| Charts | Dirty generations, quiet/max debounce and claim expiry |
| Vehicle information | Reservations, retry not-before, original deadline and capacity release |
| Notifications | Reserved runs, resumable interruption and claim expiry |
| Automations | Reserved/deferred runs, claim expiry and pending completion audits |
| Automation clock | Rule edits and the earliest active next occurrence |
| Visitor lifecycle | Pass edits, scheduled start and expiry |
| Movement recovery | Command/output receipts, reservation/lease/grace expiry and recovery work |

Full batches continue after yielding. Occupied lanes wait for owned operations to
settle, and partially occupied lanes also accept new wakes. Unresolved recovery
records retain their truth without repeatedly producing immediate no-progress
passes. Cancellation drains owned work; a notification never makes an uncertain
physical effect safe to replay. Recovery hold leaves normal executors stopped.
LPR retains idle coordinator ownership and its ten-second lease renewal.

Visitor processing uses up to three indexed claims for scheduled starts, active
expiries and malformed explicit windows, sharing a total 25-row lock budget.
Each query skips locked rows before limiting; the first lane rotates after commit.
The separate readiness check remains one compact statement.
Shared UTC expression indexes preserve the existing window policy,
including the inclusive implicit endpoint and exclusive explicit endpoint. The
owner rechecks policy after locking; no derived deadline column needs maintenance.

## Browser refresh

Zone-shadow diagnostics consume the existing committed `lpr_zone_shadow.observed`
hint through their coordinated read owner. Initial, reconnect, manual and
visibility-resume refreshes remain. A visible page repairs missed hints every
60 seconds; hidden/unmounted pages have no periodic repair timer. Observation
refresh does not invalidate settings drafts or extend account/request lifetimes.

## Intentional periodic work

These timers have separate correctness purposes and remain part of idle costs:

- Device state refresh every ten seconds and gate-malfunction safety reads: current
  provider flags do not prove acknowledged, complete, current per-device streams.
- WebSocket authority revalidation every two seconds, token expiry and transport
  pings: quiet connections still need revocation and liveness enforcement.
- Camera history reconciliation every 120 seconds: missing webhooks cannot emit
  their own repair signal.
- Runtime heartbeat, lease renewal and container health checks: recovery and
  ownership require continuing evidence while a process is alive.
- Bounded confirmation and retry waits while a command or lookup is outstanding.
  They are active-operation work, not empty-queue discovery.
- Blocking Redis/provider readers and the dashboard's local display clock.

Provider polling suppression requires a separate verified observation contract:
per-binding/configuration generations, subscription acknowledgement, initial state,
explicit gap/overflow handling, distinct observation/transition timestamps and
bounded failure detection. A connected flag or old unchanged state alone is
insufficient. Existing physical-state safety checks must not be removed to meet
an idle CPU target.

See [validation](validation.md) for executable work budgets and retained measurements,
and [recovery guidance](integrations.md) for deployment and matching checkpoints.
