# Backend agent guide

Read this for backend, API, migration, integration work. Paths below
are relative to `backend/app/` unless they start with `backend/` or `scripts/`.
Read [hardware safety](hardware-safety.md) before touching hardware or LPR effects.

## Find the owner

| Task | Start here |
| --- | --- |
| Startup, shutdown, API mounting | `main.py`, `api/router.py` |
| Models, sessions, migrations | `models/core.py`, `db/session.py`, `backend/alembic/versions/` |
| Product settings and authentication | `services/settings.py`, `services/auth.py`, `core/config.py` |
| LPR intake and access decisions | `services/access_events.py`, `services/access/` |
| Movement, admission, presence | `services/movement_fsm.py`, `services/movement_ledger.py`, `services/movement/` |
| Gate and garage commands | `services/gate_commands.py`, `services/access_devices.py` |
| Device configuration and frozen targets | `services/access_device_configuration.py` |
| Resident missed-exit recovery | `services/resident_recovery.py`, `services/resident_recovery_evidence.py` |
| Schedules and assignments | `services/schedule_operations.py`, `services/schedule_assignments.py` |
| Visitor passes, notification rules, automations | `services/visitor_passes.py`, `services/notification_rules.py`, `services/automations.py` |
| Durable notification delivery | `services/notification_runs.py`, `services/notification_dispatch.py`, `services/notifications.py` |
| Snapshots | `services/snapshots.py`, `services/snapshot_recovery.py` |

Services own business rules and transactions; modules own vendor protocol I/O.
API handlers adapt input, confirmation and presentation to the same
operations. Find callers with `rg -l`, then read the owner and relevant tests in
bounded ranges. Do not duplicate an operation in another adapter.

## Access and movement

The LPR adapter `modules/lpr/ubiquiti.py` produces `PlateRead`.
`services/lpr_webhook_security.py` validates webhook access before durable intake.
`services/access_events.py` owns ingest, worker scheduling, debounce and
suppression; finalization delegates to these stages:

| Stage | Owner |
| --- | --- |
| Read/window parsing | `services/access/reads.py` |
| Identity, schedule, history, conditional camera evidence | `services/access/evidence.py` |
| Pure access plan | `services/access/decision.py` |
| Current authority and recognition expiry | `services/access/authorization.py` |
| Event, saga, pass and session transactions | `services/access/execution.py` |
| Durable delivery handoffs | `services/access/delivery.py` |
| Hardware dispatch | `services/access/hardware.py` |
| Optional DVLA, attributes, snapshots and reporting | `services/access/enrichment.py` |
| Realtime/notification payloads | `services/access/payloads.py` |
| Historical execution | `services/access/historical.py` |

Direction resolution stays in `services/movement_fsm.py`. Durable idempotency
belongs to `services/movement_ledger.py`; sessions and suppressions to
`services/movement/sessions.py`; verified admission to
`services/movement/admission.py`; movement-derived presence to
`services/movement/presence.py`. Repair owners are
`services/movement_reconciliation.py` and `services/restart_backfill.py`.

Preserve these boundaries:

- Live decisions create or update durable saga/session state. Suppressed reads
  remain explainable `SUPPRESSED` movements.
- Commit core access and ingest outcome before hardware dispatch. Commit verified
  admission, presence and session changes through the admission owner. A provider
  accepting a request does not establish admission.
- Recheck current recognition authority at the command attempt. The ordinary
  deadline is 60 seconds from the earlier of capture and first durable receipt;
  retries or duplicate reads cannot renew it.
- Existing committed identities, restart/backfill and historical repair never
  replay hardware. Unknown plates never actuate hardware.
- Required notification/automation delivery intents join the originating
  transaction. Optional enrichment cannot prevent committed handoffs.
- Enrichment runs outside the core transaction. Copy only enrichment-owned
  columns/JSON keys into freshly loaded rows; never merge detached access or
  vehicle objects over current state. Cancellation propagates.

Full-flow simulation runs only through the isolated harness; its HTTP endpoint
returns 410. Other simulation injection endpoints enqueue reads and are not a
safe substitute for isolated tests. See `scripts/phase1/test_access_pipeline.py`.

## Gate and access devices

Gate opens use `GateCommandCoordinator` in `services/gate_commands.py`, through
`modules/gate/access_devices.py`. Garage/cover commands use `AccessDeviceService`
in `services/access_devices.py`. HA and ESPHome I/O stays inside provider modules.

Persist receipts and audit through those owners. Provider rejection is failure;
accepted-but-unverified outcomes remain reconcilable and must not be retried as
fresh commands. Automatic garage fanout requires verified admission and the
existing `fanout` policy, as well as applicable target/schedule authorization.
Freeze targets through `AccessDeviceConfiguration`; never rebuild an approved
plan from changed settings at dispatch time.

Admin receipt inspection uses `/api/v1/integrations/gate/commands` and
`/api/v1/integrations/cover/commands`, including their `/{command_id}` routes.
These expose durable delivery/verification state, not permission to resend.

## Resident missed-exit recovery

This feature defaults disabled. `services/recovery_tracker_discovery.py` supplies
read-only HA tracker inventory. `services/home_assistant.py` feeds authenticated
websocket changes into `services/resident_recovery.py`; the pure evidence policy
lives in `services/resident_recovery_evidence.py`.

For an opted-in resident with an exact known plate and a conflicting prior entry,
`services/access/evidence.py` first checks a bounded phone journey, then a camera result
within a five-second budget. Unresolved eligible denials can reserve one scoped
resident **Allow entry** notification. A capability expires 120 seconds after the
original observation; it does not widen ordinary recognition authorization.
Current owner, vehicle, schedule, configuration, provenance and gate plan remain
binding. A confirmed recovery creates a linked event/saga and preserves the
original denial; it never fabricates an exit or an absence duration.

Admission and later reconciliation finalize recovery through
`services/movement/admission.py`. The apology is reserved only after verified admission.
Diagnostics are Admin-only `/api/v1/missed-exit-recovery/attempts` and
`/attempts/{attempt_id}`; omit coordinates, capabilities and raw provider payloads.
Detailed policy and tests: [missed-exit recovery](../validation/missed-exit-recovery.md).

## Shared mutations

`services/mutation_context.py` defines the active-Admin invariant and
machine-readable `MutationError`. Resolve actors from trusted request context,
never caller-supplied labels or tool arguments. API adapters own the
confirmation interaction; domain owners retain validation and durable audit.

- Schedule CRUD: `services/schedule_operations.py`. Mutation and audit commit together.
  Existing temporary overrides remain part of access evaluation/history.
- Schedule assignments: `services/schedule_assignments.py` participates in the caller's
  aggregate transaction. Device assignment stays in `AccessDeviceService`.
  Evaluation/dependency queries remain in `services/schedules.py`.
- Schedule PATCH is replacement-oriented. The device
  UI uses an empty string to clear a schedule because confirmation omits nulls.
  Vehicle clearing preserves owner-schedule inheritance.
- `VisitorPassService` and `AutomationService` participate in caller-owned
  transactions; commit mutation and audit together. Pass raw automation fields
  to the service rather than normalizing policy in an adapter.
- `services/notification_rules.py` validates merged fields, locks update/delete and owns
  row-plus-audit transactions. Delivery, preview and tests remain in
  `NotificationService`.
- Post-commit publication cannot undo a saved mutation. Preserve calendar/visitor
  actor scopes and active-Admin checks for interactive operations.

## Notifications and workflows

`services/notifications.py` owns orchestration and delivery;
`services/notification_rendering.py` owns pure message/context/preview rendering.

Rules live in database tables, not `system_settings`. Shared trigger/action/token
contracts belong in `services/workflows/catalog.py` and `services/workflows/context.py`.
Templates use `@Variable`; `services/workflows/template_recipients.py` owns per-occurrence recipient
visibility, and `services/workflows/vehicle_away.py` derives absence only from recorded movements.
Dry-run has no effects. Automation hardware actions use audited command owners.
Actionable contexts remain TTL-bound and one-use.

Use `NotificationService.enqueue_notification` for background work and
`send_notification_now[_with_result]` for an immediate attempt. Both persist
through `NotificationRunStore` and `NotificationDispatcher`. Do not substitute a
raw realtime trigger, call delivery helpers from adapters or add another poller.

The store owns short transactions and database-time lease/token checks. Dispatch
commits `attempting` before vendor I/O and records outcomes afterward. Never retry
an attempted action with an unknown result. Fanout outcomes must show known
partial success and uncertain destinations independently; uncertainty requires
review, without automatic resend. Confirmed plans freeze exact destinations and
bind provider settings by fingerprint; credentials stay in settings or request
memory. Unattempted saved-rule work rechecks the current rule.

Only `recovery_version=1` runs are automatic. Historical unfinished runs/outbox
entries are review-only. Gate outbox dispatch reuses one run ID; realtime never
sets delivery status. Admin inspection under `/api/v1/notifications` uses
`/runs?review_only=true`, `/runs/{run_id}` and `/recovery/gate-outbox`; there is no
retry/resend endpoint. See [recovery boundaries](../validation/recovery-boundaries.md).

## Camera AI and visitor passes

Camera image analysis uses `ai/providers.py` through access evidence and UniFi
snapshot routes. Providers accept images only. Calendar visitor names use the
existing deterministic title fallback; schedules use structured recurrence or
cron; investigations use bounded structured activity queries. There is no chat,
training, conversational command, incoming messaging or Discord/WhatsApp delivery.

Manual duration passes require a registration when created or converted. Existing
passes remain editable and keep their history; calendar provenance, contact data,
validity windows and linked movements remain in the pass owner.

## Settings, snapshots and lifecycle

Bootstrap configuration is defined by `core/config.py`; product settings belong
in `system_settings` through `services/settings.py`. Consult its `SECRET_KEYS`
instead of copying a secret-key catalog. Fernet encryption derives from the
active auth root secret. Compose persists it at `data/backend/auth-secret.key`;
`IACS_AUTH_SECRET_KEY` is the advanced override. Preserve the key with the data.
Use Alembic migrations; never add runtime schema bootstrap or setting aliases.

`SnapshotManager` in `services/snapshots.py` owns app snapshot writes/compression;
the access helper delegates to it. Startup repair uses `services/snapshot_recovery.py`.

Shutdown closes producer intake, drains in-flight work, then closes hardware, delivery sinks and the database. Fence lazy
provider reconnection after close. `core/task_lifecycle.py` owns existing
cancellation-resistant cleanup/checkpoints: an outer timeout must not detach a
child that can still write or send; cleanup may exceed that timeout.

## Validation

Use the [isolated validation harness](../validation/phase1.md), including new
untracked sources and tests as documented there. Never use the production-selecting
`scripts/backend-pytest` wrapper as an isolated regression runner. The harness
checks undefined names, scoped style/types and dependency/writer boundaries.
Deployment, production migrations, live notifications and hardware tests require
separate authorization.
