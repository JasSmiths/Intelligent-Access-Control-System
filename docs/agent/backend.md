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

Access decision orchestration lives in `backend/app/services/access_events.py`.
It may coordinate current flows, but it should not deeply own movement,
hardware, snapshot, notification, or realtime implementation details.

Current owners:

- LPR adapter: `backend/app/modules/lpr/ubiquiti.py` -> `PlateRead`
- LPR webhook security: `backend/app/services/lpr_webhook_security.py`
- Access helpers: `backend/app/services/access/*`
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

## WhatsApp And Messaging

Current shape:

- Public service facade: `backend/app/services/whatsapp_messaging.py`
- Implementation modules: `backend/app/services/messaging/*`

Owners:

- Delivery/status/API calls: `messaging/whatsapp_delivery.py`
- Webhook validation/shape: `messaging/whatsapp_webhook.py`
- Admin/visitor routing: `messaging/whatsapp_router.py`
- Visitor concierge flow: `messaging/visitor_conversation.py`
- Shared helpers/parsers/sanitizers: `messaging/whatsapp_helpers.py`

Rules:

- Admin exact normalized `users.mobile_phone_number` + active Admin routes to Alfred.
- Active/scheduled visitor pass phone routes to the visitor sandbox.
- Unknown senders are denied/audited.
- Visitor tools only get pass details and update visitor plate for that visitor pass.
- Preserve privileged-plate refusal, abuse cooldown, visitor privacy, timeframe confirmation, delivery reconciliation, and Admin-to-Alfred feedback/confirmation flows.

## Alfred V3

Current shape:

- Runtime: `backend/app/services/alfred/*`
- Chat facade/session orchestration: `backend/app/services/chat.py`
- Tool facade/public imports: `backend/app/ai/tools.py`
- Tool catalogs/handlers: `backend/app/ai/tool_groups/*`
- Shared handler utilities: `backend/app/ai/tool_groups/_shared.py`

Removed legacy:

- `backend/app/services/chat_routing.py`
- `backend/app/ai/tool_groups/_facade_handlers.py`
- Pre-V3 guided/local/deterministic routing paths

Rules:

- Alfred V3 is current functionality.
- Planner is LLM-owned and scoped; do not add keyword prefilters or deterministic answer shortcuts.
- Tool results are the source of truth.
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

```bash
python3 -m compileall -q backend/app
python3 -m compileall -q backend/tests/contracts
./scripts/backend-pytest tests/contracts
./scripts/backend-pytest
```

Targeted Alfred CI:

```bash
docker compose exec -T backend sh -lc 'cd /workspace/backend && /app/.venv/bin/python -m ruff check app/ai/tool_groups app/services/alfred app/services/chat.py app/services/domain_events.py && /app/.venv/bin/python -m mypy app/ai/tool_groups app/services/alfred/memory.py app/services/domain_events.py'
```

