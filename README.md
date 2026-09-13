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

Use the isolated regression harness for development and architectural changes.
It snapshots the current source and runs against disposable resources without
production credentials, data or provider access:

```bash
python3 scripts/phase1/validate.py
```

See [isolated validation](docs/validation/phase1.md) for source inclusion,
focused checks, dependency reuse and retained evidence. The full harness includes
backend/persistence tests and the locked frontend tests/build. The older
`scripts/backend-pytest` wrapper may select the running Compose backend and must
not be used for an isolated regression run.

Schema changes are managed through Alembic. Normal Compose startup runs
`alembic upgrade head` when `IACS_AUTO_CREATE_SCHEMA=true`. The old runtime
bootstrap compatibility path has been removed. Test migrations only in disposable
databases; production migration and deployment require a separate instruction.

## Architecture Shape

- `backend/app/core`: configuration, logging, lifecycle wiring.
- `backend/app/api`: HTTP and WebSocket API routers.
- `backend/app/modules`: swappable hardware and service integrations.
- `backend/app/services`: core orchestration services that depend on module interfaces.
- `backend/app/db`: SQLAlchemy session and migration-ready database wiring.
- `backend/app/workers`: reserved package; current background services start from the FastAPI lifespan.
- `backend/app/simulation`: scenario tests and synthetic event injection. Arrival
  and misread endpoints enter the access pipeline and can actuate hardware;
  synthetic input alone does not make a call hardware-free. Use isolated fixtures.
- `backend/app/ai`: Alfred tool registry, domain tool groups, and provider boundaries.

Docker storage uses host bind mounts only. No Docker named volumes are declared.
The backend and updater containers mount the repository at `/workspace`; the updater
service also mounts Docker's socket for dependency update jobs.

## Access API

- `POST /api/v1/webhooks/ubiquiti/lpr`
- `POST /api/v1/simulation/arrival/{registration_number}`
- `POST /api/v1/simulation/misread-sequence/{registration_number}`
- `POST /api/v1/simulation/e2e/full-access-flow` (retired; returns HTTP 410, full-flow scenarios run only through isolated tests)
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

See the [backend integration owners](docs/agent/backend.md)
for Home Assistant, TTS, presence sync, and notification implementation guidance.

## Alfred V3

- `GET /api/v1/ai/providers`
- `GET /api/v1/ai/tools`
- `POST /api/v1/ai/chat`
- `WS /api/v1/ai/chat/ws`

See [Alfred V3 ownership](docs/agent/backend.md#alfred-v3)
for provider contracts, agent tools, and conversational memory guidance.
The [architecture recovery guide](docs/architecture.md) defines extension and
retirement rules; [isolated validation](docs/validation/phase1.md) verifies changes
without using the production stack.

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

Architecture recovery milestone 3 ownership and validation: [feature operations and Alfred contracts](docs/validation/milestone3-operations.md).

Architecture recovery milestone 4: [durable notification dispatch and recovery](docs/validation/milestone4-recovery.md).

Architecture recovery milestone 5: [access evidence, decisions, execution and enrichment](docs/validation/milestone5-access.md).

Architecture recovery milestone 6: [frontend owners, refresh coordination and retirement](docs/validation/milestone6-frontend.md).
