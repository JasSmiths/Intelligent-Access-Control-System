# Phase 5: Frontend UI

## Delivered

- Dockerized React + TypeScript frontend.
- Nginx static runtime with reverse proxy for:
  - `/api/*`
  - `/health`
  - `/docs`
  - WebSocket upgrades for realtime and AI chat routes.
- Host port defaults:
  - Backend API: `8088`
  - Frontend app: `8089`
- Fixed left-hand sidebar navigation.
- Required views:
  - Dashboard
  - People
  - Groups
  - Schedules
  - Passes
  - Vehicles
  - Movements
  - Top Charts
  - Events
  - Alerts
  - Reports
  - API & Integrations
  - Logs/Telemetry/Audit
  - Settings
  - Alfred Training
- Bento dashboard widgets:
  - Site presence
  - Gate state
  - Alerts
  - Recent events
  - Presence
  - Access rhythm
  - Simulator controls
- Realtime UI:
  - `/api/v1/realtime/ws` refreshes live dashboard state.
  - Logs view displays realtime event bus messages.
- Global chat:
  - Persistent `Chat with me` pill.
  - WebSocket chat through `/api/v1/ai/chat/ws`.
  - Authenticated Alfred v3 responses through the global chat launcher.
- Theme control:
  - System, light, and dark modes.
  - System is the default.

## Access

```text
http://localhost:8089
http://<LAN-host-ip>:8089
```

For Nginx Proxy Manager, point the proxy host to:

```text
http://<docker-host-ip>:8089
```

Enable WebSocket support in NPM. The frontend Nginx service proxies API and
WebSocket traffic to the backend container over the Docker network.

## Concept Reference

Accepted layout concept:

```text
/Users/jas/.codex/generated_images/019dbc4e-2adb-7a20-8ccf-4f6a46682281/ig_05dd6360a0b3f49b0169eaa033eb908191b3423ff819aa3979.png
```

Implemented design inventory:

- Fixed left sidebar with operational navigation.
- Top command bar with search, refresh, and appearance control.
- Three status cards across the top.
- Main bento area for recent events, presence, alerts, rhythm, and simulator.
- Functional color only: blue actions, green present/granted, gray exited, amber
  warning, red critical/denied.
- 8px card radius and thin borders.
- No marketing hero, no decorative gradients, no ornamental background assets.

## Verification

- `npm run build`
- `python3 -m compileall -q backend/app`
- `docker compose config --quiet`
- `GET http://localhost:8089/api/v1/health`
- `GET http://localhost:8089/api/v1/auth/status`
- Authenticated Admin session for dashboard data routes such as
  `/api/v1/presence`, `/api/v1/events`, `/api/v1/maintenance/status`, and
  `/api/v1/leaderboard`.
- WebKit-rendered desktop and mobile snapshots.
- WebKit-rendered navigation and chat-open states.
- WebSocket chat response verified through the rendered chat panel.
