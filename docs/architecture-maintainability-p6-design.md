# P6 concrete design draft — read-only preparation

Prepared by Sol Ultra from approved P2/P4 candidate source. This is a proposal for
Astra's design review, not an implementation approval or a claim of exact legacy
recovery. P4 backend is approved/committed; P5 source is frozen for its full gate. P5 cleanup
and the approved P3 integration must land before P6 implementation.
Sol owns the one migration chain; report/calendar work must rebase after this
migration rather than invent a competing head.

## Current failure boundaries

`LprIngestRepository.persist_read` currently rewrites normalized_payload on every
duplicate, including succeeded rows. The original gate observation is added only
after persistence; queue recovery can take a fresh observation. Claim is an
unlocked read/write and has no token. RAM owns the unbounded queue, window/read
lists and pending-window order. `AccessExecution` carries the complete list into
payload serialization, ingest terminalization and admission/session construction.
Session OCR/protect-event arrays also grow on later suppressed reads.

`AccessEnrichment.run` blocks the serial LPR worker over visual/snapshot/visitor,
zone-shadow, HA presence, realtime and chart work. Vehicle information already has
its own worker, but enqueue establishes a fresh deadline and occurs after the
originating decision transaction. Reconciliation directly sends HA presence after
its admission transaction. All these producers must join the durable owners.

## Schema and ownership

1. Extend `lpr_ingest_events` with `observation_state`, immutable observation
   completion time, `ready_ordinal` (unique nullable bigint), lease token/until,
   terminal reason and a nullable durable window reference. Existing receipt,
   captured time, normalized payload and idempotency key remain canonical.
   The original raw normalized input is immutable after insert. Store its one
   original gate observation as a separate immutable JSON column, not by rewriting
   input. A missing observation is explicit, never silently refreshed on restart.
2. `lpr_debounce_windows`: UUID identity; unique open ordinal; source; lifecycle
   (`open`, `finalizing`, `completed`, `suppressed`, `held`, `merged`); first_seen,
   updated_at, min_received_at; count; first/best/anchor ingest IDs plus their
   member ordinals; compact typed ranking/context; lease token/until; merged_into;
   event/saga outcome links; finalization reason and bounded error. First_seen and
   updated_at retain existing meanings, not wall-clock processing time.
3. `lpr_window_members`: unique ingest ID, window ID, member ordinal and immutable
   prepared decision-context delta. Unique(window_id, member_ordinal), indexed
   (window_id, member_ordinal). Canonical read/observation remain in ingest;
   prepared flags/references preserve the queue-stage known/visitor/external match
   used by ranking while current authorization is revalidated by existing owners.
   A singleton `lpr_coordinator_state` row holds worker epoch/token/until and the
   last atomically handled ready ordinal. It preserves one logical serial LPR
   coordinator across processes without holding a DB connection across work.
4. `lpr_observation_tokens`: immutable ingest ID, token kind (`ocr`, `protect_event`),
   normalized value and per-read order; unique(ingest_id, kind, value), with lookup
   indexes(kind, value, ingest_id). Only existing normalization semantics apply.
5. `movement_session_observations`: unique ingest ID, movement session ID, role
   (`window`/`suppressed`), association ordinal and suppression reason/context.
   Indexed(session_id, ordinal) and reverse ingest lookup. Later suppressed reads
   attach here in the same transaction that updates suppression count/last_seen
   and terminalizes that ingest. They do not require a new debounce window.
6. `access_completion_work`: deterministic UUID(event, stage), unique(event, stage),
   immutable origin event/window/receipt/direction/binding facts, origin_at,
   deadline, readiness checkpoint, state (`waiting`, `queued`, `leased`,
   `succeeded`, `skipped`, `expired`, `held`), due_at, lease token/until, attempt
   count, bounded outcome and result references. Stages remain explicit and
   cohesive: visual, snapshot, visitor link/enrichment, zone-shadow, realtime
   update. This is not a generic runtime workflow/dispatcher framework.
7. VehicleInformationJob gains immutable origin_at and a readiness checkpoint;
   reservation moves into the decision transaction. Preserve its separate worker,
   two active jobs, candidate scan page32, lease120s and timeout90s clipped to deadline.
   Lease only available capacity (at most2); never lease32 to wait behind two workers.
   Its provider/result ownership stays in vehicle_information_store/jobs.
8. HA presence desired state and attempted effects get distinct durable records:
   per-person current desired generation/origin, per-physical-target latest desired
   generation, and immutable effect attempt identity/config/outcome. Exact target
   is canonical configured HA URL + input_boolean entity, not person alone.
   See ordering proposal below; uncertainty must remain held/reconcilable.
9. One Top Charts dirty-state row: requested generation, acknowledged generation,
   first_dirty_at, last_dirty_at, claim generation/token/until and live-origin
   eligibility/deadline. Generation history/effect intent remains durable where
   needed; only one evaluator plus one trailing unacknowledged generation is live.

10. `historical_access_candidates`, owned only by restart_backfill: unique(source,
    Protect event ID), immutable original validated candidate fact snapshot/time,
    deterministic event identity, state/due/token/lease/reason/attempt count and
    candidate-owned historical media preparation state/reference. It is always
    historical-only, never a live LPR/hardware/optional-eligibility record. Existing
    startup/reconciliation owner drains candidate page32 and leases only one slot;
    no new background task or generic queue framework.

All new contract/repository/worker owners enter inherited checked/strict coverage.
No runtime schema bootstrap, live migration or second migration writer.

## Intake and canonical observation transition

- Reserve the idempotency key, original validated payload and received_at first.
  New state is `observing`, not runnable. Reserve a unique capture token and fixed
  capture lease20s using fresh DB clock at insertion, with no renewal/reclaim.
  Only the insert winner owns the original observation capture; a duplicate reloads that row and does not recapture.
- Capture gate observation outside an open DB transaction through the current
  gate owner. Compare-and-set the insert winner's identity/token to persist it
  once while that capture token/observing state and unexpired DB lease still match.
  Bound the existing configured gate owner's current_state read to10s, outside any
  DB session. Existing read failures/timeouts produce original UNKNOWN evidence
  with the capture's original observed_at if persisted within its lease. Recovery
  scans only expired observing leases, page50, and CAS-holds abandoned rows; it
  never holds a currently live capture or invokes capture again. A late/cancelled
  first capture cannot persist observation/readiness after expiry or recovery hold.
  Capture tasks/clients remain owned and cancelled/awaited; late results have no
  callback with write authority. An UNKNOWN observation remains original evidence.
  A crash after reserve
  but before capture/persistence creates a durable missing-original-observation
  hold; retry does not grant a new observation or recognition lifetime.
- Assign `ready_ordinal` only when the original observation is durably complete.
  Use a short global advisory transaction lock around sequence allocation and
  readiness commit. Thus ordinal follows durable observation-completion/queue
  insertion order; received_at independently remains immutable for deadlines.
  Sequence allocation alone would not guarantee commit-visible order.
- Maintenance intake also persists. Reserve an explicit skipped/suppressed
  outcome/reason atomically instead of returning before persistence. Do not take
  a live observation solely for suppressed maintenance intake.
- Replace the Queue[PlateRead] with an asyncio wake Event; the DB is the queue.
  Poll at most50 ready IDs ordered by ready_ordinal. Recovery uses that order,
  never received_at/UUID substitutes. Token claims use row lock/CAS, database
  time and expired-lease checks. No missing ingest row is treated as permission.
- Restart of failed/leased processing reloads canonical input+observation. Retry
  counts and backoff can change; original timestamps and observation cannot.
- Claiming a page does not permit SKIP LOCKED past the lowest unhandled ready
  ordinal. One fenced logical coordinator handles one read then flush, as today;
  another worker must wait for its lease expiry and recover that same ordered
  boundary. Advancing the handled ordinal, membership/suppression and ingest
  state commit together. This prevents parallel classification completion from
  silently changing grouping/ranking order.

## Durable grouping, merges and claims

- One short debounce coordinator advisory lock serializes membership/order
  mutation across workers. It never covers gate/provider/camera I/O. A read's
  classification probe happens outside that lock; membership commit verifies its
  current lease and handles the unique-ingest conflict idempotently.
- Search open compact window summaries in pages50 ordered by open_ordinal.
  Apply the exact existing fuzzy predicate against best_read. Select the FIRST
  matching window, including windows beyond the first page. Filter by source only;
  the existing grouping predicate has NO captured-time cutoff because flush occurs
  afterward. A time filter could skip the historical first match. Append one member
  under lock and update compact counts/rank references. New windows append after
  existing open windows. Normal append sets updated_at to that read's captured_at
  even if it is earlier; it does not use max or refresh first_seen.
- Ranking is exactly (known-exact, visitor-match, external-match, confidence,
  captured_at), with the first member in list order winning equal ranks. The
  first_read is minimum captured_at with first-member tie. The first_seen of an
  ordinary window remains its original first append, even if a later read is
  chronologically earlier.
- Exact/visitor/external finalization merges the selected window first, then
  related windows in their original pending/open order. Bulk move members using
  prefix offsets from compact counts, preserving each segment's member order.
  This is bounded Python memory and SQL updates, not an unbounded Python list.
  After merge only, first_seen=min(all captured_at), updated_at=max(all captured_at).
  Related records become merged and refer to the selected identity.
- Keep quiet/max-age/exact-match/visitor/external/stop triggers unchanged. A
  bounded page is not an early-finalization trigger. The worker processes the
  same one-read→flush boundary; loading50 is not permission to append50 first.
  No pending read/window is erased on maintenance or stop: durable suppression or
  hold must explain every acknowledged member.
- Finalization moves open→finalizing under the same coordinator lock and assigns
  a fenced token. No append can enter the frozen finalizing window. Each write
  verifies token/lifecycle; expired workers cannot commit outcomes. Event/saga,
  required notification/workflow intents, completion reservations, all member
  terminal statuses and outcome links commit in the SAME caller transaction.
  Terminalization is UPDATE by window reference, not Python ID collection.
- Crash before transaction commit leaves no event/receipt/member partial state;
  crash after commit reuses deterministic event/outcome and never replays hardware.
  Existing GateCommandCoordinator/AccessDeviceService and recognition authorization
  remain the only physical-command owners and revalidate before any attempt.

## Full evidence and bounded presentation

- Replace DebounceWindow.reads handoffs with a checked WindowEvidence summary:
  window ID, evidence version, counts/timestamps, first/best/anchor immutable read
  references and bounded preview. Repository iterators read complete evidence in
  keyset pages50. Authority consumers accept a durable EvidenceReference and
  session, never a preview array or synchronous Sequence masquerading as complete.
- Movement session OCR/protect matching queries indexed observation tokens joined
  through session associations. Where fuzzy comparisons inherently need all
  variants, stream the complete projected token set with exactly existing order
  and early-success semantics. Do not materialize sorted distinct population
  arrays. Preserve normalized ordering where output depends on it via DB ordering.
- Later suppressed observations use the same association/token lookup, including
  non-window observations. Counts and compact last/best evidence replace growing
  payload arrays. An original observation remains inspectable by ID.
- New event raw evidence format version2 contains first50 sanitized candidates,
  exact total, durable window ID, has_more/next cursor, first/best/anchor IDs. It is
  explicitly a preview, never the input to authorization. Existing payloads retain
  their version1 decoder; no historical data is rewritten to imply completeness.
- Authenticated cursor endpoint `/api/v1/events/{event_id}/candidates` extends the
  existing events API's authorization and sanitization owner. It binds signed
  or strictly validated keyset cursor to event/window and immutable final evidence;
  page limit<=50. Return existing public sanitized fields only, no raw provider
  payload, credentials or arbitrary private metadata. Verify access on every page.

## Optional completion readiness/deadlines

- Reserve stage identities in the core event transaction. Readiness moves only
  after committed execution/admission checkpoint through the existing finalizer;
  reconciliation and external admission take the same path. No enqueue gap.
- Optional origin is the earliest immutable original ingest RECEIPT, independently
  of captured/event occurrence time. Deadline=receipt+5min. This preserves the
  programme's agreed behavior for late-arriving observations. External origins
  with no ingest use the original persisted gate-observation timestamp. New
  external admission reserves it once in the same transaction from the canonical
  synthetic read: gate state_changed_at, else RealtimeEvent.created_at, else the
  original first-call observation time. The helper stores that value as captured_at,
  received_at and webhook_received_at, and event.occurred_at. Recovery decodes that
  persisted external trace/origin; event.occurred_at is permitted only for this
  explicitly external source, never as a generic legacy fallback. Prior denied
  session ingests referenced by the external event are not members of its new
  gate-observation origin. Missing/invalid legacy origin holds/skips optional work;
  enqueue/reconciliation/recovery time and row update times are never substitutes.
  Astra must approve this exact source-specific decoding.
  Hardware remains separately min(event occurrence, earliest linked receipt)+60s;
  optional work cannot refresh or extend recognition/hardware authority.
- Claims are bounded/fenced with database-time expiry; provider/media I/O occurs
  without an open session. Timeout is clipped to remaining immutable deadline.
  Stage results commit only if token, expected origin/entity revision and deadline
  still match. Terminal stage outcomes survive repeated wake/restart.
- Snapshot live camera capture must START AND COMPLETE within received_at+5s.
  Pass remaining budget into capture. After that only exact event-bound thumbnail
  is eligible, within the five-minute completion deadline. Visual matching retains
  existing45s tolerance, separately constrained by overall completion deadline.
- Visitor late linkage never reruns provider work: persist completed visual/info
  result references and an idempotent link checkpoint; admission linkage hydrates
  already available results in its transaction or schedules link-only work.
- Publish initial committed access/movement/presence facts independently of optional
  completion. Subsequent typed update events carry IDs/revisions and changed-field
  semantics; avoid full stale-object publications overwriting fresh frontend state.
  P3 feature owners need contract handoff and composed update/invalidation tests.

## HA latest-desired ordering proposal (Astra approval pending)

Presence finalization returns a typed `HaPresenceReservation` proposal only after
its existing admission/session mutations, without acquiring the HA coordination
lock. The proposal identifies event/person, applied presence state/order, immutable
original optional origin and live-eligibility; it is not a provider command or an
attempt checkpoint. Historical state changes return invalidation-only proposals:
no new eligible effect/deadline, and no clearing an existing attempted/UNKNOWN barrier.

Every outer transaction collects the proposals in its existing processing order.
After ALL origin/saga/event/visitor/session locks, required mutations and intents,
it calls `reserve_all_in_session` once immediately before caller commit. That owner
NOWAIT-locks current Person rows sorted by ID and the canonical HA URL/token setting
rows in sorted key order, re-reads current persisted bindings/actions, then acquires
ONE short HA coordination row/advisory lock. Only HA rows and required HA audit can
be touched after that flush; no subsequent domain lock acquisition is allowed.
The existing Presence locks are still held by the same caller until commit.
Under HA coordination allocate monotonic generations and update per-person AND
per-physical-target desired rows in that same transaction/processing order. Two
people sharing one canonical URL+input_boolean target therefore commit one ordered
latest generation, not a later independently projected job. Assigning a sequence
without the lock through commit would not establish commit-visible order.

The proposal list is bounded by the current caller batch (at most25 sagas plus25
standalone commands), not an unbounded retained queue. Multiple applied changes
for a person in the same transaction preserve list order; the last visible proposal
wins. Unchanged/stale admissions do not renew origin/deadline. The currently stored
Presence state chooses the person's current entry/exit action policy through the
existing normalizer: DEFAULT_INPUT_BOOLEAN_ACTION is turn_off for BOTH unspecified
entry and exit; never invent PRESENT→turn_on. Stale, legacy_equal_time and
already_applied PresenceTransition results allocate no new live generation or
origin/deadline. Applied proposals use final locked Presence/event order, never a
stale incoming direction. Old event direction alone is never replay authority. Retired/inactive/missing bindings produce
an explicit invalidation/skip; optional config changes do not authorize an unsolicited
new presence effect or revive historical origins. A binding/settings mutation takes
its existing owner locks then HA coordination to invalidate unattempted desired
work affected by its revision. It preserves any immutable attempted target barrier.

The HA worker NOWAIT-locks relevant current Person and HA setting rows BEFORE HA
coordination, then target desired/barrier and attempt rows. It never takes saga,
event, Presence or unrelated domain locks after HA coordination. It checks current
active person/binding/config, maintenance and immutable deadline plus latest person
AND physical-target generation. Persist frozen attempting identity/config/action
and commit before I/O, then release every DB session. A singleton owner lease/token
makes one current worker-authorized HA effect global across processes. Candidate
page32 leases only the one free processing slot. Renewal/receipt writes are fenced
with fresh `clock_timestamp()` after locks; loss/timeout/crash preserves UNKNOWN.
A newer desired generation can commit while old I/O runs but cannot send on that
physical target until its old attempt is resolved. Other physical targets progress.
Read-only provider observation cannot prove that an arbitrarily late old request
cannot still arrive; the narrow manual settlement operation below releases only
the uncertainty barrier, never replays the old action.

### Outer caller/rollback inventory

| Actual caller | Last flush and retry owner |
| --- | --- |
| AccessExecution.run initial decision/admission transaction | after outputs/anomalies/recovery mutations and all session work, before core commit; NOWAIT abort rolls back full transaction and durable window phase retries original IDs/input/observation/deadline |
| AccessExecution.run post-hardware admission transaction | after canonical visitor reload/session work, before commit; failed admission transaction leaves existing separately committed hardware journal untouched; retry only admission/reconciliation phase, never dispatch again |
| movement_reconciliation.reconcile_once | collect proposals across the existing25-saga and standalone loops, flush once after every origin lock; whole batch rollback leaves both cursors unadvanced and publications unsent; next owned loop retries same durable outcomes |
| recover_unattempted_reservation_in_session at admission.py542 and its outer per-reservation transaction | carry returned ineligible/proposal into the outer collector, flush last before commit; rollback keeps reservation/cursor identity, retry next recovery scan; never dispatch hardware |
| resident_recovery.handle_resident_action initial transaction | collect inside nested domain work but flush after all origin mutations/audit before outer commit; existing NOWAIT deferred-tap semantics rolls back complete owned work without spending bearer; same original capability/expiry can retry |
| resident_recovery.handle_resident_action post-hardware finalizer | admission-only retry/reconciliation from committed saga/journal; no repeated gate send or refreshed capability |
| access/historical.persist_historical_event_in_session→restart_backfill candidate transaction | propagate invalidation proposal; caller flushes after anomaly/audit/session work before commit; full candidate transaction rollback and bounded lock-conflict retry preserves Protect identity/time; bounded retry/exhaustion uses the durable original-candidate hold below, independent of scan overlap; no discovery/provider/hardware replay |
| no-ingest external gate-open persistence | source explicitly creates person=None, so no HA proposal/Person lock; completion and publication reserve in its event transaction with the original synthetic gate-observation origin; known-person external mode uses the durable AccessExecution path above |

NOWAIT errors become the checked `HaCoordinationBusy` retry outcome. The resident
initial-action owner MUST explicitly exempt it alongside raw DBAPIError/55P03 from
its broad revoke/consume exception branch (currently resident_recovery.py553–567).
If the final flush occurs outside its nested block, roll back the WHOLE outer
transaction before returning deferred: no context/attempt spend or audit survives.
The same scoped capability/expiry remains available; post-hardware contention
recovers admission only from its committed journal and never re-executes a gate.
Test both positions/cancellation and assert complete rollback and no revocation.
Other owners catch this checked coordination outcome only at their transaction
boundary; malformed persisted config is a named hold/invalidation, not indefinite
coordination retry. NOWAIT is never a caught HA-only
savepoint after which presence is committed. Each owner must roll back its entire
transaction and retry/recover the original durable phase. Add real PG lock-cycle,
batch rollback/cursor, blocked-final-check expiry, Person/settings edit race and
post-hardware no-replay fixtures before accepting this design implementation.

## Coalesced charts

- Every relevant committed live access marks one durable requested generation in
  its originating transaction; historical refresh marks dirty silently. First
  dirty time stays fixed, last_dirty refreshes; readiness is min(last+2s, first+10s).
- Claim exactly current generation/token; release claim/source locks before
  aggregation. If requests arrive during computation, retain one trailing dirty
  generation. Commit changed leader state and deterministic notification intent
  atomically, acknowledge only claimed generation and verify token/source generation.
- Live notification eligibility/deadline derives from immutable origin; stale
  generations refresh state silently and cannot obtain a new live send lifetime.
  Already attempted effects follow existing durable store recovery semantics.

## Legacy migration and deployment limits

- Schema migration is additive and does not perform provider calls or replay work.
  Completed/failed/skipped old rows retain data/outcome. Version1 historical evidence
  remains readable through its decoder; observation arrays are not fabricated.
- An old pending/processing ingest without a persisted original gate observation
  becomes an explicit migration/recovery hold and cannot actuate hardware. Old RAM
  window order/membership is not reconstructable. Existing valid original evidence
  can be retained, but absent ready/member ordinals require a conservative hold or
  explicitly documented deterministic historical-only grouping, never a claim of
  exact replay. Preferred safe default: hold pre-P6 unfinished rows for review.
- Mixed old/new workers cannot safely share these intake/window semantics. Rollout
  requires a separately authorized maintenance/drain/version cutover; local plan
  work does not deploy or migrate production. Old code cannot write new lifecycle
  rows during the version transition. Downgrade with live new data is unsafe and
  must not silently erase observations/jobs; document explicit no-loss constraints.

## Required acceptance matrix before implementation approval

- Immutable duplicate (including succeeded/failed) and first-observation CAS;
  live capture versus recovery, expired capture token and late completion;
  crash before/after capture; maintenance acknowledged intake; no refreshed receipt.
- Two concurrent claims, stale token, restart, missing-row fail closed and unique
  membership; interleaved receipt vs observation completion proves ready order.
- >50 reads/windows, fuzzy first-match beyond page1, equal rank/min timestamp ties,
  out-of-order captured_at, merge selected+related order, late suppressed reads.
- Complete OCR/protect matching outside preview50; candidate cursor sanitization,
  account/authorization/cursor boundary; old version1 decoding remains intact.
- Event/saga/intents/completion/member rollback together; post-commit recovery no
  duplicate hardware; separate60s deadline and stale observation authorization.
- Block visual/snapshot/HA work for A while B commits core decision; lease/timeout,
  deadline expiry,5s capture complete boundary,45s match, late visitor linkage once.
- HA conflicting people sharing target, binding/settings change, delayed admission,
  concurrent generations, old in-flight completion, uncertain no-replay/maintenance.
- Chart2s/10s windows, one evaluator+trailing generation, historical silence,
  source-lock release, changed-state+intent rollback, stale claim ack rejection.
- Equivalent before/after small+target synthetic burst outputs AND many distinct
  simultaneous open windows; preserve first-match beyond page1 without a time
  cutoff. Measure added DB round trips and scanned summaries. Bounded Python
  memory does not improve the worst-case fuzzy scan complexity. Query counts/full
  EXPLAIN roots, Python memory and serialized payload; disclose SQL sort/output
  growth separately. Exact source/image/lock identities and full isolated gate,
  host harness checks, affected frontend/browser interaction, Astra exact sign-off.

## Concrete transition/fence supplement (Astra approval pending)

The coordinator and window leases are120s, renewed every10s by an owned renewal
task only while its epoch/token remains current. One logical coordinator handles
one-read→flush across all processes. There is one active core decision, including
current command-owner work; page50 limits reads, not parallel core decisions.
One boundary has a90s watchdog clipped to the lease budget; existing hardware
authority's60s expiry is never extended. Recovery may finish durable no-hardware
transitions after that authority expires. A stalled former owner cannot renew or
write after takeover. Every membership/suppression/window/outcome update checks
coordinator epoch, ingest/window token, lifecycle and DB-time lease expiry under
row lock, and rechecks before caller commit after other blocking locks. Sample
`clock_timestamp()` AFTER each blocking lock acquisition and for the last check;
PostgreSQL now()/CURRENT_TIMESTAMP are transaction-start values and cannot fence
a lease that expired while waiting. A blocked-lock-past-expiry fixture must reject
membership, terminalization and attempting despite an initially valid lease.

| Entity/current state | Transition and transaction checkpoint | Recovery/fence |
| --- | --- | --- |
| Ingest absent | insert observing + original input/receipt + insert-owner token | unique idempotency key reloads winner; never capture for a duplicate |
| Ingest observing | same token persists one original observation, assigns ready ordinal and changes to ready | crash/missing original observation→held; no refresh |
| Maintenance intake | insert durable skipped reason with receipt; observation explicitly unavailable | no runnable/hardware permission |
| Coordinator idle | acquire new epoch/token; load lowest unhandled ready ordinal; persist boundary read ID/phase | expired owner cannot advance; do not skip a live unfinished boundary |
| Ingest ready | fenced lease→processing; classification + suppression/membership commit with boundary append phase | retry reloads original+prepared context; unique membership makes append idempotent |
| Window open | append read or selected+related merge under coordinator lock | all affected summaries/member offsets and phase checkpoint commit together |
| Boundary appended | persist flush clock, scan upper open ordinal, last completed flush ordinal and phase | handled ordinal has NOT advanced; takeover resumes this exact flush before next read |
| Window open + due/exact trigger | fenced finalizing claim and deterministic outcome identity | no append while finalizing; same identity/token renewed or safely taken over |
| Window finalizing | event+saga+required intents+completion reservations+member terminalization+outcome links in caller transaction | token/epoch checked before write and commit; rollback preserves finalizing recovery identity |
| Window committed | existing command owner dispatches only current authorized unattempted intent, then admission finalizer checkpoint | attempted/uncertain journal cannot be replayed by window recovery |
| Boundary flush complete | advance handled ordinal and clear boundary atomically | only now can next-ready read start; durable cursor not RAM membership drives recovery |
| Maintenance/stop during boundary | finish a safe already committed checkpoint or atomically suppress/hold remaining memberships | no clear/drop; original data retained and exact boundary resumed/held |

The flush checkpoint is bounded scalar state: read ID, phase, immutable flush
clock/upper open ordinal, current finalizing window and last completed ordinal.
If a flush spans multiple pages/windows, persist progress after each committed
window; takeover never recomputes a fresh-clock flush or crosses unfinished work.

Cancellation propagates into the finalization transaction and rolls it back;
normal event/outcome commits are not shielded from cancellation. Core HTTP/gate
attempt durability remains its existing owner. An already durable attempted
checkpoint followed by cancellation remains uncertain/reconcilable, never reset
to pending. Finalization checks current authority separately from compact summary
ranking—summary/preview cannot authorize a physical effect.

## Exact stage readiness and dependency supplement

Stages reserve identities at core decision commit. All conditions re-read
canonical stored event/saga/admission state; event type alone is not readiness.

| Committed origin/checkpoint | Initial realtime | Media/zone work | Vehicle information | Visitor result link | HA desired presence | Charts |
| --- | --- | --- | --- | --- | --- | --- |
| Denied/non-gate decision | ready immediately with committed state | ready, independent stage outcomes | existing eligible_arrival predicate only | only existing canonical arrival/departure association; otherwise waiting/skip | only if admission finalizer actually commits eligible presence | existing granted-known-entry predicate false→skip |
| Granted gate pending | ready immediately, explicitly pending | ready after core decision commit; no authorization use | reserve waiting until execution checkpoint; preserve eligible_arrival predicate | waiting for canonical verified arrival association | waiting; no presence from transport acceptance | waiting for execution checkpoint |
| Gate accepted/verified | typed committed update ready | existing identities continue | release eligible reserved work | link once canonical arrival recorded; use retained results | reserve latest desired in presence commit | mark dirty if existing granted-known-entry predicate |
| Gate rejected | committed rejection update ready | existing identities continue | same existing arrival predicate at execution checkpoint; no new access authority | no fabricated arrival; waiting expires/skip | none unless canonical eligible presence changed independently | existing granted-known-entry predicate retained, not a new verified-only rule |
| Gate uncertain/accepted-unverified | explicit unresolved update ready | existing identities continue | same existing predicate at execution checkpoint | wait for later verified canonical linkage; no provider rerun | wait until eligible presence commit | existing event predicate retained; uncertainty does not invent admission |
| External admission committed | initial ready with committed external evidence | reserve/release same media/zone owner using original origin | existing predicate after external checkpoint | canonical association link once | same finalizer desired-generation reservation | existing event predicate |
| Later admission/reconciliation | typed changed-state update ready | never re-reserve completed provider/media work | retain result reference; link-only job may wake | idempotent link-only work consumes persisted visual/info result references | reserve current latest desired in SAME presence transaction | mark current dirty generation if predicate; original eligibility deadline |

Visual→visitor visual-link is a dependency; snapshot and zone-shadow can proceed
independently. Vehicle information remains separate and can complete before or
after visitor linkage. A link job has unique(event, visitor, result kind/revision)
and never calls a provider. Initial committed realtime never waits for media/HA
or optional completion. Realtime updates carry event/resource revision and fields,
not stale whole ORM objects. Required notifications remain transaction-owned
intents; this optional worker does not replace durable notification stores.

## Fixed worker topology and shutdown

- Existing vehicle worker: separate two active jobs; claim32; lease120s; processing
  timeout90s clipped to original optional deadline. Preserve its current service
  session/cache ownership and close it once on shutdown.
- New access media/link/zone completion owner: two active jobs; claim at most32
  candidate identifiers, but lease only the currently free two slots; lease120s;
  operation timeout90s clipped to stage/origin deadline.
  Live snapshot additionally requires completion within original receipt+5s;
  visual45s matching tolerance stays separate. Link-only operations use5s
  timeouts; no expiry refresh on wake. Queue only identifiers. Initial facts have
  their independent publication lane below; blocked media cannot consume its slot.
- HA desired-sync owner: one active effect globally for this initial implementation;
  candidate scan page32, lease only one available job; lease120s; provider
  timeout10s clipped to optional eligibility. A singleton DB owner lease/token
  is required for the stated cross-process active-attempt bound; local task count
  alone proves only a per-process bound. After expiry any prior remote request
  remains UNKNOWN and its target barrier stays closed; the bound is on current
  worker-authorized attempts, not an invented bound on uncertain remote requests.
  Physical target barriers/leases remain enforced across processes even if topology
  later increases. Frozen attempting record before I/O; no open DB connection.
- Charts owner: one active evaluator, one trailing generation; claim singleton;
  lease120s; aggregate timeout90s;2s quiet/10s batching maximum. Locks released
  before aggregation. No new generic scheduler or dispatcher registry.
- Application lifespan explicitly starts/stops these owners. Stop accepts no new
  in-memory tasks, cancels and awaits all owned processing+renewal tasks, closes
  HTTP/media clients, and then permits DB/event-bus disposal. Cancelled optional
  leases remain durably recoverable; cancelled HA attempts stay UNKNOWN. There
  are no detached result-write tasks or shielded post-shutdown commits. Session
  transactions roll back on cancellation. New readiness reservations are DB-first
  and do not require a wake to survive shutdown.

## Durable health reads

The access pipeline owns a typed async health read composed from ingest/window/
completion repository projections: counts by active/held lifecycle, oldest eligible
original receipt and age, oldest open/finalizing window, lease/coordinator progress
and optional stage age/deadline outcomes. Counts/minimum timestamps are SQL
aggregates over projected/indexed active-state columns; never load all rows or
report RAM wake-event/queue length as backlog. The actual external consumer is the
async health API; make status async end-to-end and return explicit unavailable if
the durable read fails. Worker logs are already async and use this projection or
compact process-owned counters. No periodic metric cache/task is needed. Query
plans disclose aggregate DB work: bounded Python memory is not O(1) SQL count cost.

## Independent initial-publication lane and revision order

The same access completion owner owns one separate publication task per process,
independent of its two media/link/zone processing tasks. It scans at most32 ready
publication identifiers and leases only its one free slot (120s lease, owned10s
renewal,5s publish timeout). A unique event publication checkpoint prevents
concurrent claims of the same resource. Core event commit reserves this checkpoint;
a post-commit wake attempts initial publication promptly, and poll/restart recovers
it if the process dies or Redis is unavailable. No optional-result completion or
provider request is a dependency for publishing committed facts.

Add a monotonic AccessEvent resource_revision (legacy default0, new event1).
The existing access mutation owner increments it in the same transaction whenever
hardware/admission/suppression/enrichment changes publicly projected fields and
marks that event's publication checkpoint dirty. Initial publication and later
updates load the CURRENT compact committed projection after claim, never replay a
detached original ORM object. Payload and HTTP event projection both carry revision.
A publish claim acknowledges only its loaded revision; a concurrently increased
revision leaves one trailing dirty checkpoint. Revision allocation is under the
existing event row lock; no timestamp/UUID substitutes for resource ordering.

Only actual event-payload replacement consumers (currently Dashboard and any
composed history consumer proven to replace rows) compare incoming revision with
the revision already on the owned event row and ignore older payloads, including
delayed initial facts after a newer update. Invalidate/refetch-only features keep
their existing owned request generation/cancellation discipline; do NOT add an
unbounded or redundant per-account shadow revision cache. Revision metadata travels
with existing bounded page/activity rows and their normal account lifetime.
Feature invalidation is a hint to refetch canonical server state, not payload authority. Test Redis/backpressure delay of
revision1, publish revision2 first, then release1, including page refresh/account
change/cancellation. Legacy revision0 remains readable but cannot overwrite a
known newer revision. Explicit source/contract handoff to the frontend owner is
required before edits. Shutdown cancels/awaits this lane and renewal before DB or
event-bus disposal. Its lease is durable; it never blocks B behind A's media I/O.

## Concrete HA uncertainty resolution API

Existing integrations owner adds a narrow Admin-confirmed action, proposed route
`POST /api/v1/integrations/home-assistant/presence-effects/{attempt_id}/acknowledge`.
It performs NO provider send. Request pins attempt ID, physical-target fingerprint,
current target desired generation/barrier version, current optional person/binding/
config projection (including absence), expected UNKNOWN
state, explicit operator assertion that the former provider request is settled,
bounded reason and confirmation token. A separate immutable resolution receipt
records this manual evidence decision; original attempt outcome remains UNKNOWN.
Provider-state observation alone is explicitly insufficient proof of settlement.

Resolution locks the current Admin row first, then existing applicable Person and
HA settings rows NOWAIT (absence/retirement is allowed), then HA coordination,
physical target/barrier, immutable attempt and confirmation row. It takes no origin
saga/event/Presence locks. Confirmation pins immutable attempt/physical target,
current target desired generation/barrier version and the current binding/config
projection including explicit absence; changes after preview require a new
confirmation. The originating Person/binding need NOT remain active or exist to
acknowledge an old UNKNOWN attempt. A later fresh send separately requires current
active authority. Under locks reject any live singleton/attempt lease or owned
local worker, mismatched/replaced barrier, non-UNKNOWN attempt, stale target version
or confirmation. Sample fresh DB clock after blocking locks. An expired lost owner
has no write rights, but its remote request remains uncertain; explicit operator
assertion of settlement is the manual evidence decision, not a transport guarantee.

Consume confirmation with the existing commit=False participant, insert
unique(attempt_id) resolution receipt, release only
that target's uncertainty barrier and write audit atomically. Same idempotent
confirmation/attempt retry returns the stored resolution without a second audit or
mutation; mismatched payload cannot reuse it. Audit failure rolls all back. No old
action is requeued. A later ordinary worker may claim only the LATEST desired state
through current maintenance/binding/eligibility checks and its own durable attempt.
Resolution itself never implies access admission or permission to replay history.

Required inert API/PG fixtures: Admin/auth and exact confirmation binding, stale
revision/generation/target, active claim/race, expired-token late result, double
resolution, audit rollback, token-consumption rollback, operator assertion absent,
and forbidden provider/hardware factories. Cross-person shared physical target
and concurrent resolver/worker prove barrier ordering. No expanded UI is required.

## Enforceable migration/downgrade rules

Migration fixture seeds old pending/processing rows with and without original
observation/receipt, completed/failed/skipped historical rows and old JSON evidence.
Upgrade marks ALL unfinished legacy rows held with explicit legacy_unordered or
missing_original_observation reason, preserves original columns/payload byte-for-
byte, and creates no runnable ready/member ordinal. Historical terminals/outcome
links are unchanged. There is no claim that lost RAM membership/order is recovered.

Downgrade refuses (before any DDL) if ANY new observation/window/member/token/
session-association/completion/HA-attempt-resolution/chart-generation row exists,
any ready ordinal or new observation state exists, or legacy held disposition has
not been explicitly exported/restored to a verified pre-cutover backup. It may
only drop an empty additive schema with original legacy row values preserved.
No automatic lossy status restoration or delete-cascade makes downgrade appear
safe. Validate refusal against populated tables and no-op success only on an empty
fresh upgraded DB; retention/backup rehearsal remains separate. Production
maintenance/drain/version cutover and migration still require user authorization.

Stable concrete proposal for exact Astra review (P6 implementation remains gated): the complete proposal/flush/retry inventory,
source-specific external origin, capture ownership and detailed manual settlement
lock/confirmation contract above. All transition,
readiness/topology and downgrade details require explicit approval before P6 code.


## Lossless historical contention retry and media ownership

Before attempting the existing historical candidate mutation, reserve its unique
source/Protect identity and original validated fact snapshot in the narrow
historical_access_candidates owner. Only the insert winner establishes immutable
candidate/time and deterministic event UUID. A duplicate reloads it; it cannot
replace facts, fetch discovery/track again or invent a live optional deadline.
The existing startup/reconciliation owner drains already-reserved candidates in
bounded pages independently of whether a fresh Protect listing succeeds or is
configured. Historical state changes still use historical admission and may only
invalidate older queued HA desired work; no hardware/visitor/provider send is
replayed and no live five-minute eligibility is introduced.

A claim leases one available processing slot, with token/expiry checked before
all outcome writes. The historical event/session/audit, HA invalidation last flush
and candidate terminal outcome commit together. HaCoordinationBusy rolls back the
whole transaction; original candidate remains pending, with bounded backoff and
at most5 automatic coordination retries before explicit durable review hold.
Cursor/scan overlap may advance only when the discovered identity is terminal,
explicitly skipped or durably reserved. Failure to reserve cannot count as
successful accounting or advance that scan checkpoint. The held original identity
remains visible even when its capture predates startup/scan overlap; it does not
rely on movement reconciliation discovering a rolled-back, nonexistent saga.
No unbounded Python retry list or indefinite provider discovery refetch is added.

The current _create_backfill_event calls _attach_protect_thumbnail INSIDE the DB
transaction. Move preparation/reuse to the candidate's historical media boundary
BEFORE the retried mutation, without an open DB session. The candidate reserves
one media preparation token/state and owns one deterministic artifact identity,
bounded existing thumbnail operation and immutable ready/unavailable result.
Coordination retries reuse that result/reference; they never invoke event_thumbnail
again or write a new random-event file per transaction attempt. If media is not
available, retain the existing historical no-media outcome; do not borrow live
snapshot deadlines. An interrupted preparing state becomes explicit held/unavailable
under its expired token rather than silently reissuing provider work.

The candidate owns any prepared file until the deterministic event links it in the
same successful mutation transaction, or explicit safe cleanup. Rollback retains
that candidate-owned reference (so it is not an untracked orphan). Cleanup verifies
candidate token/state and that the deterministic event has not successfully linked
it; a concurrent successful claimant's artifact cannot be deleted. An uncertain
preparation/file result remains a named candidate hold with deterministic location,
not repeated random files or an unsafe blind delete. Lifecycle/retention cleanup
must account for these records explicitly when an artifact is discarded.

Required PG/filesystem/inert fixtures: old startup candidate beyond scan overlap;
crash before/after reserve; duplicate original facts; retry/exhaustion hold; drain
with discovery unavailable; full candidate+event+HA invalidation rollback; repeated
coordination contention with EXACTLY one provider-thumbnail/file preparation;
rollback preserves owned artifact, successful concurrent link prevents cleanup,
and historical mode never obtains live eligibility or calls hardware ports.


P6 frontend validation also composes P3 read coalescing with the actual independent
initial/enrichment event types: unrelated events cause no feature fanout; relevant
revisions while a read is blocked produce current plus at most one trailing read.
Coordinate concrete payload/consumer ownership before frontend edits. P3 production
request comparisons remain P3 baseline-stage evidence; P9 reruns the equivalent
integrated fixture if P6/P7 changes those request paths, rather than presenting the
old stage comparison as a final-candidate guarantee.
