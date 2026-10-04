# Backend agent guide

Read this for backend, API, migration, integration, or Alfred work. Paths below
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
| Schedules and assignments | `services/schedule_operations.py`, `services/schedule_assignments.py`, `services/schedule_overrides.py` |
| Visitor passes, notification rules, automations | `services/visitor_passes.py`, `services/notification_rules.py`, `services/automations.py` |
| Durable notification delivery | `services/notification_runs.py`, `services/notification_dispatch.py`, `services/notifications.py` |
| Incoming messages and WhatsApp | `services/messaging/` |
| Alfred tools and orchestration | `ai/tool_groups/`, `services/alfred/`, `services/chat.py` |
| Snapshots | `services/snapshots.py`, `services/snapshot_recovery.py` |

Services own business rules and transactions; modules own vendor protocol I/O.
API and Alfred handlers adapt input, confirmation and presentation to the same
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
never caller-supplied labels or tool arguments. API/Alfred adapters own the
confirmation interaction; domain owners retain validation and durable audit.

- Schedule CRUD: `services/schedule_operations.py`; standalone temporary overrides:
  `services/schedule_overrides.py`. Mutation and audit commit together.
- Schedule assignments: `services/schedule_assignments.py` participates in the caller's
  aggregate transaction. Device assignment stays in `AccessDeviceService`.
  Evaluation/dependency queries remain in `services/schedules.py`.
- Schedule PATCH is replacement-oriented; Alfred updates are partial. The device
  UI uses an empty string to clear a schedule because confirmation omits nulls.
  Vehicle clearing preserves owner-schedule inheritance.
- `VisitorPassService` and `AutomationService` participate in caller-owned
  transactions; commit mutation and audit together. Pass raw automation fields
  to the service rather than normalizing policy in an adapter.
- `services/notification_rules.py` validates merged fields, locks update/delete and owns
  row-plus-audit transactions. Delivery, preview and tests remain in
  `NotificationService`.
- Post-commit publication cannot undo a saved mutation. Preserve calendar/visitor
  actor scopes and active-Admin checks for interactive Alfred operations.

## Notifications and workflows

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

## Incoming messaging

Concrete WhatsApp owners live in `services/messaging/`: `whatsapp_delivery.py`
for output, `whatsapp_configuration.py` for typed settings,
`whatsapp_webhook.py` for validation, `whatsapp_incoming.py` for durable intake,
and `whatsapp_router.py` for Admin/visitor routing. `incoming_messages.py` owns
provider-neutral claims/reply checkpoints. Visitor interpretation is in
`services/messaging/visitor_conversation.py`; shared scoped policy is in
`services/visitor_conversations.py`.

An exact normalized active Admin phone routes to Alfred; an active/scheduled
visitor-pass phone routes to the visitor sandbox; unknown senders are denied and
audited. Acknowledge accepted work only after durable intake. Preserve operation
identity through handling, approval and reply. Interrupted handlers and uncertain
replies require review; never reset attempted work to pending. Revalidate the
linked actor and auth-session version before buffered Admin work: shared history
cannot transfer authority. Visitor tools stay scoped to pass details and that
pass's plate, including privileged-plate refusal, cooldown and privacy rules.

Operational Discord output uses `modules/messaging/discord_transport.py`.
SDK convenience sends can internally retry a possibly accepted request; preserve
the transport's per-destination outcomes and view registration contract.

## Alfred V3

Alfred V3 is the supported runtime. `services/chat.py` orchestrates sessions;
`services/alfred/` owns planning, execution, approvals and streaming.
The stdlib-only contracts are `ai/tools.py` (`ToolOutcome`/`ToolError`),
`ai/tool_inputs.py` (validation), and `ai/context.py` (actor context).
`ai/tool_groups/registry.py` assembles feature catalogs and handlers.

- Keep handler dependencies explicit. `ai/tool_groups/_shared.py` exports its own helpers, not
  other services/models. Inject fakes or patch the dependency use site before
  building a registry that captures them.
- The planner is LLM-owned and scoped. Do not add keyword prefilters,
  deterministic answer shortcuts or provider-signature compatibility retries.
- Tool results carry domain `output` plus `outcome.status` and nullable
  `outcome.error` (`code`, `message`). Statuses are `succeeded`, `failed`,
  `requires_confirmation`, `requires_details`.
- Catalogs own status labels, success flags, confirmation summaries/buttons and
  turn-completion metadata. Presentation callbacks have no side effects.
- Validate inputs before handlers. Extend the validator and tests before adding
  unsupported schema constraints. `return_schema` describes answer metadata;
  it does not validate domain output.
- State-changing tools return `requires_confirmation` before mutation and use
  the shared audited owner. Declare permissions, safety and confirmation metadata.

Use `backend/tests/contracts/test_alfred_catalog_contract.py`,
`backend/tests/test_alfred_architecture.py`, `backend/tests/test_chat_tool_context.py`,
`backend/tests/test_alfred_provider_contract.py` and touched feature tests to
check these boundaries.

## Settings, snapshots and lifecycle

Bootstrap configuration is defined by `core/config.py`; product settings belong
in `system_settings` through `services/settings.py`. Consult its `SECRET_KEYS`
instead of copying a secret-key catalog. Fernet encryption derives from the
active auth root secret. Compose persists it at `data/backend/auth-secret.key`;
`IACS_AUTH_SECRET_KEY` is the advanced override. Preserve the key with the data.
Use Alembic migrations; never add runtime schema bootstrap or setting aliases.

`SnapshotManager` in `services/snapshots.py` owns app snapshot writes/compression;
the access helper delegates to it. Startup repair uses `services/snapshot_recovery.py`.

Shutdown closes producer intake, drains in-flight work and shielded Alfred
approvals, then closes hardware, delivery sinks and the database. Fence lazy
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
