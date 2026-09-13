# IACS Agent Guide

This file is the short, high-priority entrypoint for coding agents. Read the
focused docs under `docs/agent/` when a task touches that area.

## Read Next

- Backend/domain work: `docs/agent/backend.md`
- Frontend/UI work: `docs/agent/frontend.md`
- Gate, garage, provider, or hardware-adjacent work: `docs/agent/hardware-safety.md`

## Golden Rules

- Ask before writing when intent, safety, requirements, or ownership is unclear.
- Touch only files needed for the task. No opportunistic cleanup.
- Prefer the smallest working change. Add abstractions only when they remove real complexity or duplicate ownership.
- Do not fake system knowledge. Inspect the repo and current runtime state.
- Never print secrets, tokens, cookies, API keys, passwords, media blobs, or private payloads.

## System Shape

- Name: Intelligent Access Control + Presence System.
- Purpose: LPR ingest -> movement/session/saga -> access events -> presence/alerts -> gate/garage/notification orchestration -> realtime console -> Alfred AI ops.
- Deploy: Docker Compose.
- Host ports: frontend `8089`, backend `8088`, postgres `5432`, redis `6379`.
- Backend: Python 3.12, FastAPI, SQLAlchemy async, PostgreSQL, Redis.
- Frontend: React 19, TypeScript, Vite, Nginx.
- API: versioned `/api/v1` only. Do not add non-versioned API aliases.
- Storage: bind mounts only. Do not introduce Docker named volumes.
- Generated/runtime paths to ignore: `data/`, `logs/`, `frontend/node_modules/`, `frontend/dist/`, Python caches.

## Hard Safety Rules

- Gate opens must go through `backend/app/services/gate_commands.py` via `GateCommandCoordinator`.
- Garage/access-device commands must go through `backend/app/services/access_devices.py` via `AccessDeviceService`.
- Alfred hardware tools must use the same audited owners. Do not call Home Assistant, ESPHome, UniFi, or vendor APIs directly from Alfred handlers.
- Unknown plates must never trigger hardware.
- Untrusted LPR input must fail closed before durable side effects.
- Provider rejection is failure, not success.
- Accepted-but-unverified gate commands must remain reconcilable.
- Suppressed LPR reads must be durable and explainable, not silently dropped.
- Presence follows committed movement/access decisions.
- Admin confirmation + durable audit are required for gate/door/cover commands, announcements, access-device config, maintenance changes, schedule overrides, notification sends/tests, workflow edits, integration tests, telemetry purge, and UniFi Protect updates/backups.
- Realtime logs are not audit history.
- Do not run live hardware commands unless the user explicitly asks for a supervised hardware test and gives the required confirmation.

## Current V2 Owners

Backend:

- App entry/router: `backend/app/main.py`, `backend/app/api/router.py`.
- Models: `backend/app/models/core.py`.
- Settings/secrets: `backend/app/services/settings.py`; encrypted secrets derive from active auth root secret.
- Alfred V3 runtime: `backend/app/services/alfred/*`.
- Alfred chat facade: `backend/app/services/chat.py`.
- Alfred tool contracts: `backend/app/ai/tools.py` (stdlib-only).
- Alfred input contract: `backend/app/ai/tool_inputs.py` (stdlib-only).
- Alfred request context: `backend/app/ai/context.py` (stdlib-only).
- Alfred catalog assembly: `backend/app/ai/tool_groups/registry.py` (`build_agent_tools`).
- Alfred catalogs/handlers: `backend/app/ai/tool_groups/*`; import dependencies explicitly from their owners.
- LPR ingest/debounce/suppression: `backend/app/services/access_events.py`.
- Normalized access read/window helpers: `backend/app/services/access/reads.py`.
- Identity/schedule/direction evidence: `backend/app/services/access/evidence.py` (`AccessEvidenceResolver`).
- Pure access plan: `backend/app/services/access/decision.py` (existing movement FSM still owns direction).
- Access transactions and audited execution: `backend/app/services/access/execution.py` (`AccessExecution`).
- Optional post-commit enrichment/reporting: `backend/app/services/access/enrichment.py` (`AccessEnrichment`).
- Access helpers: `backend/app/services/access/*`.
- Movement sessions/presence: `backend/app/services/movement/*`, with ledger/FSM/reconciliation in `movement_ledger.py`, `movement_fsm.py`, `movement_reconciliation.py`.
- Gate commands: `backend/app/services/gate_commands.py`.
- Access devices/providers: `backend/app/services/access_devices.py`, `backend/app/modules/access_devices/*`.
- Gate controller adapter: `backend/app/modules/gate/access_devices.py`.
- Schedule CRUD: `backend/app/services/schedule_operations.py`.
- Schedule assignment transaction participant: `backend/app/services/schedule_assignments.py`; device callers remain under `AccessDeviceService`.
- Temporary schedule overrides: `backend/app/services/schedule_overrides.py`.
- Schedule evaluation/dependency queries: `backend/app/services/schedules.py`.
- Visitor pass mutations: `backend/app/services/visitor_passes.py` (`VisitorPassService`).
- Notification rule CRUD/audit: `backend/app/services/notification_rules.py`.
- Notification rendering/providers: `backend/app/services/notifications.py`.
- Notification durable claims/checkpoints: `backend/app/services/notification_runs.py`.
- Notification background/synchronous dispatch: `backend/app/services/notification_dispatch.py`.
- Automations: `backend/app/services/automations.py`.
- Shared workflow catalogs/context: `backend/app/services/workflows/*`.
- WhatsApp configuration/delivery/intake: `backend/app/services/messaging/whatsapp_configuration.py`, `whatsapp_delivery.py`, `whatsapp_webhook.py`, `whatsapp_incoming.py`.
- Messaging claims/reply checkpoints: `backend/app/services/messaging/incoming_messages.py`.
- Channel-neutral visitor conversation policy: `backend/app/services/visitor_conversations.py`.
- Discord outbound connection/delivery: `backend/app/services/discord_messaging.py`; incoming gateway/worker: `backend/app/services/messaging/discord_incoming.py`. Main injects Alfred handlers and owns lifecycle; outbound delivery must not import incoming processing or chat.
- Snapshots: `backend/app/services/snapshots.py` (`SnapshotManager`).

Frontend:

- Bootstrap only: `frontend/src/main.tsx`.
- App shell owners: `frontend/src/app/*`; event impact in `realtimeRefresh.ts`, read lifetime in `useShellRefresh.ts`, serialized batches in `refreshCoordinator.ts`.
- Typed API owners: `frontend/src/api/*`.
- Domain-neutral primitives: `frontend/src/ui/*`.
- Shared helpers: `frontend/src/lib/*`.
- Feature owners: `frontend/src/features/integrations/*`, `frontend/src/features/schedules/*`, `frontend/src/features/workflows/*`.
- Schedules: `api/schedules.ts` owns CRUD/confirmations; feature model/grid/editor own UI behavior.
- Workflows: separate `AutomationsView.tsx` and `NotificationsView.tsx` entries; shared components/model/hooks must not import concrete editors.
- Route views: `frontend/src/views/*`.
- Styles: `frontend/src/styles.css` imports `frontend/src/styles/*`.
- `frontend/src/shared.tsx` was removed. Do not recreate a shared compatibility shim.

Removed legacy paths:

- `backend/app/services/whatsapp_messaging.py`
- `backend/app/ai/tool_groups/_facade_handlers.py`
- `backend/app/services/chat_routing.py`
- `backend/app/modules/gate/home_assistant.py`
- `backend/app/services/alert_snapshots.py`
- `backend/app/services/notification_snapshots.py`
- `frontend/src/views/SchedulesView.tsx`
- `frontend/src/views/WorkflowViews.tsx`
- `frontend/src/features/workflows/WorkflowFeature.tsx`

## Core Behavior Contracts

Preserve coverage and behavior for:

- LPR arrival/exit.
- Known resident, visitor, and unknown vehicle decisions.
- Duplicate/OCR/session suppression as durable movement records.
- Gate command audit, idempotency, failure, and reconciliation.
- Movement reconciliation and restart backfill without hardware side effects.
- Presence updates from committed movement/access decisions.
- Notification rule evaluation, preview/test, actionable contexts, and partial delivery success.
- Automation scheduler/webhook/dry-run behavior and gate-command safety.
- WhatsApp visitor sandbox/privacy/abuse safeguards and Admin-to-Alfred routing.
- Alfred V3 planner, permissions, memory, confirmation before mutation, and fail-closed provider behavior.
- Integration status/config/test/save/reset flows.

Contract tests live in `backend/tests/contracts/` with golden fixtures.

## Validation Commands

For architecture/refactoring work, use the isolated harness. Add each new source
or test file with repeatable `--include <repo-relative-file>` arguments until it
is tracked. See `docs/validation/phase1.md` for isolation and retained evidence.

```bash
python3 scripts/phase1/validate.py
python3 scripts/phase1/test_source_snapshot.py
git diff --check
```

The harness runs backend/persistence/frontend tests, build, compilation,
migrations/schema comparison, Compose parsing and targeted Ruff/mypy checks.
`scripts/backend-pytest` may select the running backend: do not use it for an
isolated regression run. Production deployment and supervised hardware tests
remain separate tasks.

Ownership and retirement checklist: `docs/architecture.md`.

Smoke checks:

```bash
curl -fsS http://localhost:8089/api/v1/health
curl -fsS http://localhost:8089/api/v1/auth/status
docker compose ps
```

## Development Notes

- Prefer `rg`/`rg --files` for search.
- Use structured parsers/APIs over ad hoc string manipulation.
- Bound ad hoc repository-analysis scripts and give traversal loops an explicit no-progress exit.
- If a command yields a process or session ID, poll it to completion or explicitly terminate it before handoff. Never abandon a yielded process.
- After launching host-side diagnostics, verify processes started by the task have exited; do not terminate unrelated processes.
- Keep feature-specific frontend logic inside its feature or route owner.
- Keep vendor I/O under `backend/app/modules/*` or provider modules.
- Do not reintroduce runtime schema bootstrap compatibility or old setting aliases.
- Do not move code just to move it; V2 progress means deletion, consolidation, reduced public surface, or clearer ownership.
