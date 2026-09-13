# Backend Agent Notes

Use this when touching backend services, APIs, tests, integrations, or Alfred.

## Architecture

Backend entrypoints:

- App entry: `backend/app/main.py`
- API router: `backend/app/api/router.py`
- Models: `backend/app/models/core.py`
- DB session: `backend/app/db/session.py`
- Settings: `backend/app/services/settings.py`
- Auth/session: `backend/app/services/auth.py`

Core services consume normalized contracts. Vendor I/O belongs under
`backend/app/modules/*` or a provider-specific module.

## Access And Movement

`backend/app/services/access_events.py` owns durable ingest, the worker, debounce
and suppression. Finalization delegates to the access stages below. Import helpers
from their owners; do not restore methods or aliases on the worker for old callers.

Current owners:

- LPR adapter: `backend/app/modules/lpr/ubiquiti.py` -> `PlateRead`
- LPR webhook security: `backend/app/services/lpr_webhook_security.py`
- Read/window data and metadata parsing: `access/reads.py`
- Identity, schedule, presence/history and conditional camera evidence: `access/evidence.py`
- Pure access plan: `access/decision.py`; existing MovementDirectionFSM still resolves direction
- Core event/saga/pass/presence/session transactions: `access/execution.py`
- Optional DVLA, visual attributes, snapshots and reporting: `access/enrichment.py`
- Access hardware side effects: `backend/app/services/access/hardware.py`
- Access realtime/notification payloads: `backend/app/services/access/payloads.py`
- Access snapshot delegation: `backend/app/services/access/snapshots.py`
- Movement FSM: `backend/app/services/movement_fsm.py`
- Movement ledger and idempotency: `backend/app/services/movement_ledger.py`
- Movement sessions/suppression: `backend/app/services/movement/sessions.py`
- Movement-derived presence: `backend/app/services/movement/presence.py`
- Reconciliation: `backend/app/services/movement_reconciliation.py`
- Restart backfill: `backend/app/services/restart_backfill.py`

Rules:

- Every live decision creates or updates durable movement saga/session state.
- Suppressed reads are durable `SUPPRESSED` movements, not silent drops.
- Historical/restart backfill suppresses hardware side effects.
- Presence commits only after the relevant access/movement decision is safe.
- Core access and ingest outcome commit before gate dispatch. Verified outcome and
  presence/session changes commit together afterward; accepted unknown outcomes
  remain reconcilable. Existing committed identities never replay hardware.
- Optional enrichment runs after core execution, outside its transaction. Copy
  only enrichment-owned columns/JSON keys into freshly loaded rows, never merge
  detached access or vehicle objects over current state.
- Required access/visitor/automation delivery intents join their originating
  transaction through `access/delivery.py` and the domain's explicit participants.
  Optional-stage failure cannot prevent those committed handoffs. Enrichment is
  not automatically replayed; cancellation propagates.
- The existing full-flow simulation patches process-global owners. Its production
  isolation contract remains unresolved; do not use it as a regression shortcut.
  The isolated pipeline suite retains its known failing simulation case until
  that contract is resolved. See `docs/architecture-review/IMPLEMENTATION_STATUS.md`.

See [milestone 5](../validation/milestone5-access.md) for timing changes, coverage,
remaining interruption windows and code/image rollback. Its implementation does
not authorize deployment or hardware tests.

## Gate And Access Devices

Current owners:

- Gate command coordinator: `backend/app/services/gate_commands.py`
- Gate controller adapter: `backend/app/modules/gate/access_devices.py`
- Access devices: `backend/app/services/access_devices.py`
- Providers: `backend/app/modules/access_devices/home_assistant.py`, `backend/app/modules/access_devices/esphome.py`

Rules:

- Gate opens use `GateCommandCoordinator`.
- Garage/cover/access-device commands use `AccessDeviceService`.
- Direct HA/ESPHome cover command calls are allowed only inside provider/modules.
- Provider rejection or failed verification is not success.
- Accepted-but-unverified gate commands must remain visible to reconciliation.

See `docs/agent/hardware-safety.md` before changing this area.

## Snapshots

Current owner: `backend/app/services/snapshots.py` (`SnapshotManager`).

Removed wrappers:

- `backend/app/services/alert_snapshots.py`
- `backend/app/services/notification_snapshots.py`

Rules:

- No ad hoc `Path.write_bytes`/Pillow compression for app snapshots outside `SnapshotManager`.
- Access snapshots go through the access snapshot helper, which delegates to `SnapshotManager`.
- Startup repair belongs in `backend/app/services/snapshot_recovery.py`.

## Schedule Operations

- CRUD validation, transactions and durable audit: `services/schedule_operations.py`.
- Assignment validation and audit within the caller's aggregate transaction:
  `services/schedule_assignments.py` (`set_schedule_assignment`). The caller must
  commit or roll back the entire aggregate. Do not split its audit into another transaction.
- Device assignment stays under `AccessDeviceService`; Alfred must not write
  Home Assistant settings to assign a schedule.
- Temporary override transaction and audit: `services/schedule_overrides.py`.
- Existing evaluation/FSM inputs and dependency queries: `services/schedules.py`.

API/Alfred adapters own confirmation, input interpretation and presentation.
The shared operations require an active Admin and record the real user/source.
Alfred's actor comes from request context, never tool arguments. CRUD and
standalone overrides commit mutation plus audit together. Realtime publication
is not the audit and cannot undo a committed override.

API schedule PATCH retains its replacement contract; Alfred updates are partial.
The device UI uses an empty string to explicitly clear a schedule because the
existing confirmation protocol omits nulls. Keep the confirmation and mutation
payloads consistent. Vehicle clearing preserves owner-schedule inheritance.

Tests: `backend/tests/test_schedule_operations.py` for canonical validation and
adapter boundaries; `scripts/phase1/test_schedule_operations.py` for real
PostgreSQL adapter parity, rollback, confirmation, assignments and overrides.
Run persistence tests only through the isolated harness.

## Feature mutation contracts

Visitor pass create/update/cancel rules and audit remain in `VisitorPassService`
(`services/visitor_passes.py`). Callers commit the pass and audit in one transaction.
Interactive Alfred calls resolve an actual active Admin, including phone clearing.
Calendar and visitor-sandbox actor scopes stay with their existing owners.
`publish_pass_change` runs after commit and does not invalidate a saved result.

Notification rule CRUD is owned by `services/notification_rules.py`; it validates
merged fields, locks update/delete, requires an active Admin, and commits row plus
audit together. API and Alfred retain confirmation and presentation. Delivery,
preview and rule tests remain with `NotificationService`.

Automation rule CRUD remains in `AutomationService`, as a transaction participant.
Pass raw fields to it; do not normalize or implement its policy in adapters.
It validates actual Admins and names, locks updates/deletes and persists webhook
hardening. Adapters commit/roll back the whole transaction. Dispatch is separate.

`services/mutation_context.py` defines the shared active-Admin invariant and
machine-readable `MutationError`. Do not use request-provided actor labels.

## Notifications, Automations, And Workflows

Current owners:

- Notifications: `backend/app/services/notifications.py`
- Automations: `backend/app/services/automations.py`
- Shared workflow catalogs/context: `backend/app/services/workflows/catalog.py`, `backend/app/services/workflows/context.py`
- Action contexts: `notification_action_contexts` table

Rules:

- Notification rules live in DB, not `system_settings`.
- Template tokens are `@Variable`; do not reintroduce bracket-token compatibility.
- Automations and notifications should share workflow catalogs/context helpers instead of duplicate trigger/action/variable definitions.
- Automation dry-run has no side effects.
- Automation gate actions must use `GateCommandCoordinator`.
- Notification/actionable contexts are TTL-bound and one-use.
- Notification delivery partial success is current safety behavior.

## Durable notification dispatch

Use `NotificationService.enqueue_notification` for background work and
`send_notification_now[_with_result]` for an immediate attempt. Both persist
through `NotificationRunStore` and `NotificationDispatcher`. Do not publish a
raw trigger as a substitute for creating a run, call delivery helpers from an
adapter, or add a second polling/claim implementation. The obsolete
`process_context*` and `execute_rule*` delivery loops were removed.

The store owns short transactions and database-time lease/token checks. The
service owns conditions, rendering, provider delivery and independent enrichment.
The dispatcher commits `attempting` before vendor I/O and outcome afterward.
Never retry an attempted action with an unknown result. A configured action can
fan out internally: interruption means the whole action requires review, without
claiming which endpoints accepted it. Known partial outcomes and uncertain
endpoints must remain independently visible; any uncertain send requires review,
without automatic resend. Confirmed plans freeze exact destinations and bind
provider configuration with a fingerprint; credentials remain in settings or
request memory. Ordinary saved-rule work also rechecks its current rule before
an unattempted effect.

Only new recovery_version=1 runs are automatic. Historical queued/processing
runs and historical unfinished gate notification outbox entries are review-only.
The gate outbox uses one stable run ID across dispatch attempts; realtime enriches
its timeline and never sets delivery status. Gate actuation remains unchanged.

Admin inspection: GET `/api/v1/notifications/runs?review_only=true`,
`/runs/{run_id}`, and `/recovery/gate-outbox` under the same notifications prefix.
Lists accept limit 1–100 and offset 0–10000. No retry/resend action is provided.
Inspection excludes raw context, rendered content and provider diagnostics.

Tests: `scripts/phase1/test_notification_recovery.py` (real isolated PostgreSQL),
`backend/tests/test_notification_recovery_boundaries.py`, and existing notification
provider/confirmation tests. Migration/release/rollback design and limits:
[Milestone 4](../validation/milestone4-recovery.md). A rollback must retain the
additive revision and pause the old gate-notification dispatcher, not blindly
restart the old image against recovery records.

## WhatsApp And Messaging

Current shape:

- Concrete owners: `backend/app/services/messaging/*`.
- The `services/whatsapp_messaging.py` facade is removed. Do not recreate it.

Owners:

- Delivery/status/API calls: `messaging/whatsapp_delivery.py`
- Typed provider configuration: `messaging/whatsapp_configuration.py`
- Webhook validation/shape: `messaging/whatsapp_webhook.py`
- Durable acceptance and buffered processing: `messaging/whatsapp_incoming.py`
- Provider-neutral incoming claims/reply checkpoints: `messaging/incoming_messages.py`
- Admin/visitor routing: `messaging/whatsapp_router.py`
- Channel-specific visitor interpretation: `messaging/visitor_conversation.py`
- Channel-neutral visitor policy and scoped mutations: `services/visitor_conversations.py`
- Shared helpers/parsers/sanitizers: `messaging/whatsapp_helpers.py`

Rules:

- Admin exact normalized `users.mobile_phone_number` + active Admin routes to Alfred.
- Active/scheduled visitor pass phone routes to the visitor sandbox.
- Unknown senders are denied/audited.
- Acknowledge accepted incoming work only after durable intake. Preserve its
  operation identity across handler, approval and reply. An interrupted handler
  or uncertain reply requires review; never reset attempted work to pending.
- Shared conversation history cannot transfer authority. Revalidate the linked
  IACS actor and auth-session version before buffered Admin/Alfred work.
- Visitor tools only get pass details and update visitor plate for that visitor pass.
- Preserve privileged-plate refusal, abuse cooldown, visitor privacy, timeframe confirmation, delivery reconciliation, and Admin-to-Alfred feedback/confirmation flows.

## Alfred V3

Current shape:

- Runtime: `backend/app/services/alfred/*`
- Chat facade/session orchestration: `backend/app/services/chat.py`
- Tool contracts, `ToolOutcome`/`ToolError`, safety and catalog presentation: `backend/app/ai/tools.py` (stdlib-only)
- Input/schema validation: `backend/app/ai/tool_inputs.py` (stdlib-only)
- Actor context and token restoration: `backend/app/ai/context.py` (stdlib-only)
- Catalog assembly: `backend/app/ai/tool_groups/registry.py` (`build_agent_tools`)
- Tool catalogs/handlers: `backend/app/ai/tool_groups/*`
- Shared handler utilities: `backend/app/ai/tool_groups/_shared.py`

Removed legacy:

- `backend/app/services/chat_routing.py`
- `backend/app/ai/tool_groups/_facade_handlers.py`
- Pre-V3 guided/local/deterministic routing paths

Rules:

- Alfred V3 is the sole supported Alfred version. Do not preserve old import or test compatibility.
- Use explicit handler dependencies; `_shared.py` exports its own helpers, not other services/models.
- Domain tool schemas belong in their catalog module. Registry assembly belongs only in `registry.py`.
- Tests patch the dependency use site or inject fakes; install fakes before constructing a registry that captures them.
- Provider calls use the current `LlmProvider` signature once; test doubles must support that interface.
- Planner is LLM-owned and scoped; do not add keyword prefilters or deterministic answer shortcuts.
- Tool results are the source of truth. Each execution includes `output` (domain data)
  and `outcome` (`status`, nullable `error` with `code`/`message`). States are
  `succeeded`, `failed`, `requires_confirmation`, `requires_details`.
- Declare status labels, success flags, confirmation summary/button callbacks and
  whether confirmation finishes the turn in the feature catalog. Do not add chat
  branches for these. Summary callbacks are presentation only, never side effects.
- All invocations validate input against the catalog before calling a handler.
  The supported schema subset is explicit and catalog assembly rejects unsupported
  constraints. Extend validator + tests before adding a new schema keyword.
- `return_schema` remains descriptive answer metadata. It is not JSON Schema
  validation of domain output. Execution metadata has its own preservation fixture.
- State-changing tools must return `requires_confirmation` before mutation.
- Hardware tools must use audited IACS owners.
- Tool metadata must declare categories, safety level, permissions, confirmation, examples/rate limits/return schema when needed.
- Update `backend/tests/test_chat_agent.py` and touched domain tests for tool changes.

## Settings And Config

- Bootstrap env should stay limited to ports, DB/Redis URLs, auth secret file/override, CORS/trusted hosts/public URL/root path, and module selectors.
- Dynamic product config belongs in `system_settings` via UI/API.
- Encrypted secrets use Fernet derived from the active auth root secret.
- Do not reintroduce runtime schema bootstrap compatibility or old HA/ESPHome setting aliases.
- Auth root secret defaults to `data/backend/auth-secret.key`; advanced override is `IACS_AUTH_SECRET_KEY`.

Secret setting keys include:

- `home_assistant_token`
- `apprise_urls`
- `discord_bot_token`
- `whatsapp_access_token`
- `whatsapp_webhook_verify_token`
- `whatsapp_app_secret`
- `dvla_api_key`
- `unifi_protect_username`
- `unifi_protect_password`
- `unifi_protect_api_key`
- `esphome_api_encryption_key`
- `esphome_legacy_password`
- `lpr_webhook_token`
- `openai_api_key`
- `gemini_api_key`
- `anthropic_api_key`
- `dependency_update_backup_mount_options`

## API Notes

- Health: `GET /`, `/health`, `/api/v1/health`
- Auth: `/api/v1/auth/*`
- Realtime WS: `/api/v1/realtime/ws`
- Alfred: `/api/v1/ai/chat`, `/api/v1/ai/chat/stream`, `/api/v1/ai/chat/ws`, `/api/v1/ai/agent/status`, `/api/v1/ai/feedback`, `/api/v1/ai/training/*`
- Confirmations: `POST /api/v1/action-confirmations`
- Access movements: `GET /api/v1/access/movements`
- Gate commands: `GET /api/v1/access/gate-commands`
- Access devices: `/api/v1/access-devices`
- LPR webhook: `POST /api/v1/webhooks/ubiquiti/lpr`
- WhatsApp webhook: `GET/POST /api/v1/webhooks/whatsapp`

## Backend Validation

Use the isolated harness for refactoring. Explicitly include each new source,
fixture, or test file until tracked:

```bash
python3 scripts/phase1/validate.py --include backend/app/ai/context.py
```

See [validation instructions](../validation/phase1.md). The production-selecting
test wrapper is not an isolated runner. Catalog preservation is covered by
`backend/tests/contracts/test_alfred_catalog_contract.py`; dependency rules by
`backend/tests/test_alfred_architecture.py`; actor isolation by
`backend/tests/test_chat_tool_context.py`; single-call provider and planner
contracts by `backend/tests/test_alfred_provider_contract.py`.

## Delivery and lifecycle boundaries

- Operational Discord sends use `modules/messaging/discord_transport.py`. Do not
  call SDK channel/webhook send conveniences from durable output paths: their
  internal retries can repeat a possibly accepted request. Keep payload/view
  registration contracts and truthful per-destination outcomes together.
- Shutdown closes producer intake, drains its in-flight work, then drains shielded
  Alfred approvals before closing hardware, delivery sinks and the database.
  A provider that supports lazy connection must fence reopening after shutdown.
- Use `core/task_lifecycle.py` for the existing cancellation-resistant cleanup and
  checkpoint paths. An outer timeout requests cancellation; it must not detach a
  child that can still write or send. Cleanup can therefore exceed that timeout
  while its owned checkpoint finishes. Test the blocked-child interleaving.
- The locked harness checks undefined names across all backend application code,
  alongside the existing scoped style/type checks and dependency/writer ratchet.
