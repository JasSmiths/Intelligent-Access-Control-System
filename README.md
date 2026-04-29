# Intelligent Access Control System

AI-ready access control and presence system with modular LPR/gate integrations,
Home Assistant support, Apprise notifications, multi-provider LLM tooling, and
a realtime React dashboard.

Future AI agents should read [AGENTS.md](/Users/jas/Documents/Intelligent%20Access%20System/AGENTS.md)
before making changes.

## Run

```bash
cp .env.example .env
mkdir -p data/backend data/postgres data/redis logs/backend logs/frontend
docker compose up --build
```

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
docker compose exec -T backend sh -lc 'cd /workspace/backend && python -m pytest'
```

For a focused test file:

```bash
docker compose exec -T backend sh -lc 'cd /workspace/backend && python -m pytest tests/test_dependency_updates.py'
```

## Architecture Shape

- `backend/app/core`: configuration, logging, lifecycle wiring.
- `backend/app/api`: HTTP and WebSocket API routers.
- `backend/app/modules`: swappable hardware and service integrations.
- `backend/app/services`: core orchestration services that depend on module interfaces.
- `backend/app/db`: SQLAlchemy session and migration-ready database wiring.
- `backend/app/workers`: queue and background processing entry points.
- `backend/app/simulation`: mock event endpoints for hardware-free testing.
- `backend/app/ai`: Phase 4 LLM provider and tool-call boundaries.

Docker storage uses host bind mounts only. No Docker named volumes are declared.

## Phase 2 API

- `POST /api/v1/webhooks/ubiquiti/lpr`
- `POST /api/v1/simulation/arrival/{registration_number}`
- `POST /api/v1/simulation/misread-sequence/{registration_number}`
- `GET /api/v1/events`
- `GET /api/v1/presence`
- `GET /api/v1/anomalies`
- `WS /api/v1/realtime/ws`

See [docs/phase-2.md](/Users/jas/Documents/Intelligent%20Access%20System/docs/phase-2.md)
for the current data model and debounce behavior.

## Phase 3 Integrations

- `GET /api/v1/integrations/home-assistant/status`
- `POST /api/v1/integrations/gate/open`
- `POST /api/v1/integrations/announcements/say`
- `POST /api/v1/integrations/notifications/test`

See [docs/phase-3.md](/Users/jas/Documents/Intelligent%20Access%20System/docs/phase-3.md)
for Home Assistant, TTS, presence sync, and Apprise configuration.

## Phase 4 AI Agent

- `GET /api/v1/ai/providers`
- `GET /api/v1/ai/tools`
- `POST /api/v1/ai/chat`
- `WS /api/v1/ai/chat/ws`

See [docs/phase-4.md](/Users/jas/Documents/Intelligent%20Access%20System/docs/phase-4.md)
for provider configuration, agent tools, and conversational memory behavior.

## Phase 5 Frontend

The frontend is served by the `frontend` Docker service on port `8089` and
proxies API/WebSocket traffic to the backend.

See [docs/phase-5.md](/Users/jas/Documents/Intelligent%20Access%20System/docs/phase-5.md)
for UI routes, NPM setup, and verification notes.

## Phase 6 Agent Guide

Future implementation work should start with
[AGENTS.md](/Users/jas/Documents/Intelligent%20Access%20System/AGENTS.md), which
documents the architecture, modular I/O rules, API surface, UI design language,
reverse-proxy expectations, and safe extension points for future AI agents.
