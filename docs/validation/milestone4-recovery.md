# Milestone 4 — recoverable notification work

## Confirmed starting boundary

Main source matches the released milestone 3 manifest (original project revision
69f9d8c plus milestones 1–3; isolated checkpoint 12871c3). Baseline
`iacs-phase1-hra59un4` passes all checks. Implementation lives in an isolated
checkout because main backend/app is a live production bind mount. No deployment,
production data changes, live notification sends or hardware tests in this task.

## Design before persistence changes

1. Keep NotificationService responsible for rule evaluation, rendering and vendor
   delivery through existing providers. A notification run store owns short SQL
   transactions, exclusive claims, immutable rendered plans and action checkpoints.
   A dispatcher owns recovery and lifecycle; realtime events only wake it. Polling
   the database guarantees new queued work survives lost callbacks/Redis/restarts.
2. Add nullable recovery_version, claim_token, lease_expires_at, delivery_plan,
   rules_override, claim_count and review_reason columns to notification_runs. Null version
   means historical: preserve existing records and exclude them from automatic
   dispatch. Only new version 1 rows are claimable. Plans contain existing rendered
   notification data and selected target identifiers; never runtime credentials.
3. Claim one run at a time with SELECT FOR UPDATE SKIP LOCKED. Use database time
   and a random token. Every checkpoint checks token and an unexpired lease under
   a row lock. Mark an action attempting and commit BEFORE calling its provider.
   Save accepted/skipped/unknown outcome before independent realtime enrichment.
   No database transaction is held across vendor I/O.
4. Recovery resumes pending actions only. Completed actions are never sent again.
   Expired claims with an attempting action become review_required; the provider
   may have accepted it. No automatic retry of provider exceptions or ambiguous
   partial delivery. A whole configured action is the checkpoint unit, including
   any provider-internal fanout. Interrupted fanout is explicitly unknown; this
   does not claim endpoint-level exactly-once delivery.
5. Initial bounds: 5-minute claim lease, 2-minute action timeout, 15-minute maximum
   automatic dispatch age, 3 preparation attempts, one run per worker at a time,
   a bounded database poll. Stale events/preparation failures require review.
   These are safety behaviour changes: old alerts are not blindly emitted later.
6. Freeze conditions/rendering/action order once before the first send. Persist
   accepted counts and partial-failure metadata. Preserve existing successful
   partial-delivery semantics. Unknown results are visible even when earlier
   actions were accepted. Provider acceptance never asserts end-user delivery.
7. Synchronous API/Alfred sends and visitor event adapters use the durable owner.
   The existing gate-malfunction notification outbox must use a stable run ID so
   its outer retries cannot create another send. Only its notification adapter
   changes; gate diagnosis/actuation/reconciliation remain untouched. Historical
   unfinished outbox work is review-only as well.
8. Add Admin-only paginated run/recovery inspection, exposing states/action IDs
   and safe reasons, without raw contexts, rendered messages or provider secrets.
   Review is read-only in this milestone: no resend button or implicit retry.

## Migration, release and rollback

Use an additive migration after 20260713_0002. The existing initial migration
imports current metadata; additions must tolerate columns already present on
fresh installation. Test an actual old-schema fixture upgrade separately from
fresh-head creation, preserving historical rows and their status/content.

Deploy only after separate authorization: stop the old backend before upgrading,
apply migration, then start the new image/source. Do not run old/new dispatchers
against the same database during rollout. Preserve old image AND baked source.
Rollback restores milestone 3 with the additive migration retained and a targeted
gate-notification delivery hold; see the safe rollback correction below. Leave
additive columns and all records in place. Stop workers first. A destructive downgrade is refused while
version 1 records exist; archive/reconcile evidence before a separately designed
schema removal. No automatic downgrade or provider replay.

## Acceptance

Real PostgreSQL: competing workers, cancelled/crashed preparation and delivery,
lease expiration/fencing, lost wakeup, frozen plans, partial delivery, commit
failure after send, historical review-only, old-schema upgrade preservation and
downgrade guard. Providers are fakes and tests use isolated loopback-only harness.
Cover service start/stop, API Admin restrictions, duplicate event/outbox dispatch,
synchronous errors, zero calls on unconfirmed tests, and event publication failure.
Run full baseline/final validation and retain manifests/patches/cleanup plus
before/after production metadata. Do not weaken existing assertions.

## Automation dispatch assessment (after notification implementation)

Scheduler already locks due rules and stores claimed runs, then advances next_run_at.
Execution currently changes the claim without a claim-token/state guard and commits
its result/audit only after actions. Event/webhook paths also depend on process-local
execution. Assess stale claims, duplicate calls, action idempotency and unknown
outcomes against notification evidence before selecting a bounded follow-up. Do
not blindly retry automation actions: some operate hardware through audited owners.

## Automation dispatch assessment

Assessed unchanged milestone 3 `services/automations.py` and its existing tests.

| Boundary | Existing guarantee | Remaining gap and bounded follow-up |
| --- | --- | --- |
| Scheduler selection (`_claim_due_rules`) | Locks due rules with SKIP LOCKED, inserts claimed runs and advances next_run_at in one commit. | `_process_due_rules` executes only its in-memory claim list. Crash after claim loses that scheduled occurrence. Add durable claimed-run consumption and new-work eligibility, preserving historical claims for review. |
| Claimed execution (`execute_rule`) | Checks rule identity and active state. | Loads a claimed run without exclusive state/token transition; an existing finished run ID can be reused. Add a fenced claimed-to-running transition and immutable execution snapshot before action I/O. |
| Action results (`_execute_rule_actions`) | Existing ordered condition/action policy, trace results, final durable audit. | Results/audit commit after actions; a crash can lose proof of earlier actions. Persist per-action attempt/result separately from final aggregate completion. |
| Gate and garage actions (`_execute_action`) | Uses GateCommandCoordinator and AccessDeviceService; gate outcomes expose command ID/reconciliation state. | Gate intent is newly constructed at each execution and does not use a stable automation run/action identity. Reconcile/reuse the existing durable command before considering another attempt. Garage actions need an explicit duplicate/unknown-outcome contract. Never infer that a generic notification retry rule authorizes actuation. |
| Event/webhook entry (`fire_trigger`) | Trigger normalization, authorization/HMAC policy and matching remain intact. | No durable inbox before process-local execution. Select durable event identity and transactional acceptance at each producer boundary; webhook nonce consumption alone is not execution recovery. |

Automation code is unchanged in this milestone. It is **not yet covered by the new
notification recovery guarantees**. The next bounded design is scheduler
claimed-run handoff and per-action identity/reconciliation, with synthetic providers,
no hardware tests and a separate persistence/rollback design. Do not turn on
automatic replay of existing claimed/running automations. This assessment fulfils
the roadmap's assessment step, not a claim that automation recovery is implemented.

## Resolved implementation choices and limits

- One configured notification action is the journal unit. Provider-internal
  recipient fanout stays intact. Partial success exposed by the existing mobile
  provider remains accepted and records a partial-failure count. Discord/WhatsApp/
  voice exceptions after internal fanout are conservative unknown outcomes; no
  endpoint-level resend is available.
- Rendered content/action order/condition decisions are frozen. Target selection
  criteria are frozen, while current provider configuration and credentials are
  resolved just before delivery. Configured `all` targets can change between
  attempts of different pending actions. No previously attempted action is retried.
- New runs older than 15 minutes require review before another action. Provider
  errors/timeouts stop remaining actions and require review; accepted earlier
  actions stay recorded. This deliberately replaces automatic ambiguous retries.
- `notification.failed` is emitted only after the unknown outcome is committed.
  Realtime/telemetry/last-fired updates are best effort and cannot reverse delivery
  evidence. There is no guarantee that a crash also publishes the corresponding
  transient event; Admin run inspection is the durable source.
- The durable acceptance boundary starts when a run is inserted. Visitor/event
  producers which lose their originating process before the adapter inserts a run
  still require a producer outbox/inbox; broader producer persistence belongs to
  the access/automation designs. No end-to-end exactly-once promise is made.
- Admin recovery inspection is API-only here; UI work remains a later milestone.
  No acknowledge/retry/resend mutation is introduced. Historical records keep their
  original stored status/content and are projected as requiring review.

## Safe rollback correction

A bare milestone 3 image is **not** a valid rollback: its Alembic graph cannot
resolve the new recorded revision, and its old gate notification outbox can retry
ambiguous work. Prepare a rollback build from the preserved milestone 3 source,
apply the retained `rollback-safe.patch` (keeps this additive migration and pauses
only `GateMalfunctionService._process_notification_id`), and build/recreate backend
with the matching source mount. Stop milestone 4 workers first. Keep all database,
audit, configuration and notification records. No status rewrite or downgrade is
needed. Ordinary milestone 3 notification handling returns; gate-malfunction
notification delivery is deliberately paused until the forward fix. Gate hardware
control/reconciliation code is untouched by the hold. Verify health/read-only UI
and the paused adapter, then resolve the incident before removing the hold.

Source reversal alone is a development operation, not a safe live rollback.
Always preserve unrelated work and verify reverse-patch applicability first.

## Completed validation and handoff

Final isolated run: `iacs-phase1-acolznpc` under
`/Users/jas/Documents/IACS Regression Baselines`.

- 816 backend tests, 147 real PostgreSQL persistence tests, 21 frontend tests and
  5 snapshot checks pass, with no failed, errored or skipped tests.
- Compilation, frontend build, fresh migrations/head/schema comparison, isolated
  old-schema upgrade and downgrade guard, Compose validation and targeted Ruff/mypy
  pass. New modules are included in the harness and CI targets.
- All 445 main-source hashes still match the deployed milestone 3 manifest.
  Production IDs/images/mounts/start times/restart counts remained unchanged
  throughout the milestone. All harness-owned containers were cleaned up.
- Final code/tests match the final harness manifest. Handoff documentation was
  completed afterward. No main edits, production migration, deployment, provider
  sends, hardware test or performance claim.
- Baseline `hra59un4` passed. Intermediate `ilaezt04` caught obsolete dependency
  doubles, a JSON SQL fixture bind mistake and lint. `5y314z8_` caught a reused
  synthetic gate ID violating the existing unresolved-gate uniqueness guarantee.
  Those fixtures/doubles were corrected without weakening assertions. `8ln9bzoc`
  passed after consolidation; `acolznpc` adds failure-publication and stale-worker
  coverage. Every run retained its results and passed cleanup/isolation checks.

The delivery path is now NotificationService -> NotificationDispatcher ->
NotificationRunStore checkpoints -> existing NotificationService provider adapters.
Removed methods: process_context, process_context_with_result, execute_rule,
execute_rule_with_result, old run create/start/finish helpers and their unused
context-ID parsing helpers. Producers/test doubles call the new owner directly.
Realtime gate notification callbacks now only enrich history, never set dispatch
state. The existing `_mark_rule_fired` helper remains the one last-fired updater.

The handoff package contains an immutable combined patch against milestone 3,
source/patch hash manifests, a durable checkout and Git bundle, the safe rollback
patch, and forward/reverse application evidence. Patch application to the current
main checkout is only a preflight check; actual application is a future release
step because main source is mounted into production.

## Complete changed-file inventory

- `.github/workflows/backend-alfred.yml`
- `AGENTS.md`
- `README.md`
- `backend/README.md`
- `backend/alembic/versions/20260912_0001_notification_recovery.py`
- `backend/app/ai/tool_groups/notifications_handlers.py`
- `backend/app/api/v1/notifications.py`
- `backend/app/models/core.py`
- `backend/app/services/gate_malfunctions.py`
- `backend/app/services/home_assistant.py`
- `backend/app/services/notification_dispatch.py`
- `backend/app/services/notification_runs.py`
- `backend/app/services/notifications.py`
- `backend/tests/test_notification_recovery_boundaries.py`
- `backend/tests/test_notification_workflows.py`
- `backend/tests/test_operational_status.py`
- `docs/agent/backend.md`
- `docs/architecture.md`
- `docs/validation/milestone4-recovery.md`
- `docs/validation/phase1.md`
- `scripts/phase1/test_notification_recovery.py`
- `scripts/phase1/validate.py`
