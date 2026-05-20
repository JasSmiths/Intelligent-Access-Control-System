# Phase 2: Data Models and LPR Movement Pipeline

## Current State

- Core SQLAlchemy models include groups, people, vehicles, vehicle/person
  assignments, schedules, schedule overrides, presence, access events, anomalies,
  visitor passes, movement sagas, movement sessions, gate command records, report
  exports, audit logs, telemetry, and Alfred memory/training tables.
- Startup uses `Base.metadata.create_all` plus idempotent transitional
  columns/indexes in `backend/app/db/bootstrap.py`. There is no demo seed data;
  the first Admin is created through setup UI/API.
- Ubiquiti webhook normalization remains isolated in
  `backend/app/modules/lpr/ubiquiti.py`.
- `AccessEventService` owns ingest orchestration with `movement_fsm.py`,
  `movement_ledger.py`, and `gate_commands.py`.
- Durable `movement_sessions` and `movement_sagas` drive exact echo suppression,
  gate-cycle/session handling, convoy handling, visitor departures, OCR variant
  handling, arrival OCR noise suppression, restart backfill, and reconciliation.
- Every live LPR decision creates or updates movement saga/session state. Final
  granted/denied reads create one `access_events` row; suppressed reads are
  durable suppressed movements, not alerts.
- Anomalies are persisted in the `anomalies` table and exposed operationally as
  alerts through `/api/v1/alerts`.

## Useful Endpoints

- `POST /api/v1/webhooks/ubiquiti/lpr`
- `POST /api/v1/simulation/arrival/{registration_number}`
- `POST /api/v1/simulation/misread-sequence/{registration_number}`
- `POST /api/v1/simulation/e2e/full-access-flow` (Admin)
- `GET /api/v1/events`
- `GET /api/v1/events/{event_id}/snapshot`
- `GET /api/v1/alerts`
- `PATCH /api/v1/alerts/action`
- `GET /api/v1/alerts/{alert_id}/snapshot`
- `GET /api/v1/presence`
- `GET /api/v1/access/movements`
- `GET /api/v1/access/gate-commands`
- `POST /api/v1/access/events/{event_id}/movement-reconciliation`
- `GET /api/v1/diagnostics/lpr-timing`
- `WS /api/v1/realtime/ws`

There is no `/api/v1/anomalies` route; use `/api/v1/alerts`.

## Runtime Settings

Bootstrap environment variables are limited to compose/runtime selectors such as
ports, DB/Redis URLs, auth secret file or override, CORS/trusted hosts, public
URL/root path, and module selectors. Operational settings live in
`system_settings` and are managed through Settings UI/API, with encrypted secrets
derived from the active auth root secret.

LPR timing controls such as quiet/max debounce windows, vehicle-session idle
seconds, similarity threshold, and smart-zone diagnostics are dynamic settings.
Smart zones are diagnostic only and must not reject otherwise valid plate reads.

## Reverse Proxy Notes

The frontend service is the normal ingress on host port `8089`; it serves the SPA
and proxies `/api/*`, `/health`, `/docs`, `/openapi.json`, and WebSocket upgrades
to the backend container on port `8000`.

For Nginx Proxy Manager, proxy to:

```text
http://<docker-host-ip>:8089
```

Enable WebSocket support. Use backend host port `8088` only for direct API
debugging.
