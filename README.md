# Intelligent Access Control System

AI-ready access control and presence system with modular LPR/gate integrations,
Home Assistant support, Apprise notifications, multi-provider LLM tooling, and
a realtime React dashboard.

Future AI agents should read [AGENTS.md](AGENTS.md) before making changes.

## Run

```bash
cp .env.example .env
mkdir -p data/backend data/chat_attachments data/postgres data/redis logs/backend logs/frontend
docker compose up --build
```

On first development start, the backend creates `data/backend/auth-secret.key`
with a random root secret. This key signs sessions and encrypts dynamic
secrets. Keep it backed up, do not commit it, and use Settings -> Auth to rotate
it. Production/non-development environments must provide either that file or a
non-default `IACS_AUTH_SECRET_KEY` before startup.

Backend health endpoints:

- `GET http://localhost:8088/`
- `GET http://localhost:8088/health`
- `GET http://localhost:8088/api/v1/health`

Frontend app:

- `http://localhost:8089`
- LAN: `http://<host-ip>:8089`
- NPM target: `http://<docker-host-ip>:8089`

The backend container listens on port `8000` internally, while the host-facing
port defaults to `8088` to avoid common conflicts. Change `BACKEND_PORT` in
`.env` if needed. The service binds to `0.0.0.0`, so it is reachable on the LAN
at `http://<host-ip>:8088`.

For Nginx Proxy Manager, proxy to `http://<docker-host-ip>:8089`, enable
WebSocket support, and keep the standard forwarded headers enabled. The
frontend Nginx service serves the React app and proxies `/api/*` plus WebSocket
upgrades to the backend container. Use backend port `8088` only for API-only
debugging.

## Tests

The backend image installs the project dev extras, including `pytest` and
`pytest-asyncio`, so tests can be run from the live Compose container:

```bash
docker compose exec -T backend sh -c 'cd /workspace/backend && python -m pytest'
```

For a focused test file:

```bash
docker compose exec -T backend sh -c 'cd /workspace/backend && python -m pytest tests/test_dependency_updates.py'
```

Frontend guardrail tests run with Vitest:

```bash
cd frontend && npm run test
```

Schema changes are managed through Alembic. Normal Compose startup runs
`alembic upgrade head` when `IACS_AUTO_CREATE_SCHEMA=true`; the previous
transitional bootstrap DDL is available only for older local databases by
setting `IACS_LEGACY_SCHEMA_BOOTSTRAP=true`.

## Architecture Shape

- `backend/app/core`: configuration, logging, lifecycle wiring.
- `backend/app/api`: HTTP and WebSocket API routers.
- `backend/app/modules`: swappable hardware and service integrations.
- `backend/app/services`: core orchestration services that depend on module interfaces.
- `backend/app/db`: SQLAlchemy session and migration-ready database wiring.
- `backend/app/workers`: reserved package; current background services start from the FastAPI lifespan.
- `backend/app/simulation`: mock event endpoints for hardware-free testing.
- `backend/app/ai`: Alfred tool registry, domain tool groups, and provider boundaries.

Docker storage uses host bind mounts only. No Docker named volumes are declared.
The backend and updater containers mount the repository at `/workspace`; the updater
service also mounts Docker's socket for dependency update jobs.

## Access API

- `POST /api/v1/webhooks/ubiquiti/lpr`
- `POST /api/v1/simulation/arrival/{registration_number}`
- `POST /api/v1/simulation/misread-sequence/{registration_number}`
- `POST /api/v1/simulation/e2e/full-access-flow` (Admin)
- `GET /api/v1/events`
- `GET /api/v1/alerts`
- `PATCH /api/v1/alerts/action`
- `GET /api/v1/alerts/{alert_id}/snapshot`
- `GET /api/v1/presence`
- `GET /api/v1/access/movements`
- `GET /api/v1/access/gate-commands`
- `WS /api/v1/realtime/ws`

See [docs/phase-2.md](docs/phase-2.md)
for the current data model and movement-session behavior.

## Phase 3 Integrations

- `GET /api/v1/integrations/home-assistant/status`
- `POST /api/v1/integrations/gate/open`
- `POST /api/v1/integrations/announcements/say`
- `POST /api/v1/integrations/notifications/test`

See [docs/phase-3.md](docs/phase-3.md)
for Home Assistant, TTS, presence sync, and Apprise configuration.

## Phase 4 AI Agent

- `GET /api/v1/ai/providers`
- `GET /api/v1/ai/tools`
- `POST /api/v1/ai/chat`
- `WS /api/v1/ai/chat/ws`

See [docs/phase-4.md](docs/phase-4.md)
for provider configuration, agent tools, and conversational memory behavior.

## Smoke Checks

Anonymous:

```bash
curl -fsS http://localhost:8089/api/v1/health
curl -fsS http://localhost:8089/api/v1/auth/status
```

Dashboard routes such as `/api/v1/maintenance/status`, `/api/v1/leaderboard`,
`/api/v1/presence`, and `/api/v1/events` require an authenticated Admin session
after first-run setup.

## Phase 5 Frontend

The frontend is served by the `frontend` Docker service on port `8089` and
proxies API/WebSocket traffic to the backend.

See [docs/phase-5.md](docs/phase-5.md)
for UI routes, NPM setup, and verification notes.

## Phase 6 Agent Guide

Future implementation work should start with
[AGENTS.md](AGENTS.md), which
documents the architecture, modular I/O rules, API surface, UI design language,
reverse-proxy expectations, and safe extension points for future AI agents.
