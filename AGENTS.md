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
- Alfred tool facade/registry: `backend/app/ai/tools.py`, `backend/app/ai/tool_groups/*`, shared helpers in `backend/app/ai/tool_groups/_shared.py`.
- Access-event orchestration: `backend/app/services/access_events.py`.
- Access helpers: `backend/app/services/access/*`.
- Movement sessions/presence: `backend/app/services/movement/*`, with ledger/FSM/reconciliation in `movement_ledger.py`, `movement_fsm.py`, `movement_reconciliation.py`.
- Gate commands: `backend/app/services/gate_commands.py`.
- Access devices/providers: `backend/app/services/access_devices.py`, `backend/app/modules/access_devices/*`.
- Gate controller adapter: `backend/app/modules/gate/access_devices.py`.
- Notifications: `backend/app/services/notifications.py`.
- Automations: `backend/app/services/automations.py`.
- Shared workflow catalogs/context: `backend/app/services/workflows/*`.
- WhatsApp facade and implementation: `backend/app/services/whatsapp_messaging.py`, `backend/app/services/messaging/*`.
- Snapshots: `backend/app/services/snapshots.py` (`SnapshotManager`).

Frontend:

- Bootstrap only: `frontend/src/main.tsx`.
- App shell owners: `frontend/src/app/*`.
- Typed API owners: `frontend/src/api/*`.
- Domain-neutral primitives: `frontend/src/ui/*`.
- Shared helpers: `frontend/src/lib/*`.
- Feature owners: `frontend/src/features/integrations/*`, `frontend/src/features/workflows/*`.
- Route views: `frontend/src/views/*`.
- Styles: `frontend/src/styles.css` imports `frontend/src/styles/*`.
- `frontend/src/shared.tsx` was removed. Do not recreate a shared compatibility shim.

Removed legacy paths:

- `backend/app/ai/tool_groups/_facade_handlers.py`
- `backend/app/services/chat_routing.py`
- `backend/app/modules/gate/home_assistant.py`
- `backend/app/services/alert_snapshots.py`
- `backend/app/services/notification_snapshots.py`

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

Use repo-supported commands and the running Docker stack when available.

```bash
docker compose config
python3 -m compileall -q backend/app
python3 -m compileall -q backend/tests/contracts
./scripts/backend-pytest tests/contracts
./scripts/backend-pytest
cd frontend && npm run build
cd frontend && npm test
git diff --check
```

Targeted Alfred CI:

```bash
docker compose exec -T backend sh -lc 'cd /workspace/backend && /app/.venv/bin/python -m ruff check app/ai/tool_groups app/services/alfred app/services/chat.py app/services/domain_events.py && /app/.venv/bin/python -m mypy app/ai/tool_groups app/services/alfred/memory.py app/services/domain_events.py'
```

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
