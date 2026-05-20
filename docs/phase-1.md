# Phase 1: Deployment and Backend Scaffold

This phase establishes the runtime contract and backend boundaries for the
Intelligent Access Control System.

## Delivered

- Docker Compose deployment with `frontend`, `backend`, `updater`, `postgres`, and `redis`.
- Host bind mounts only:
  - `./data/backend:/app/data`
  - `./data/chat_attachments:/app/data/chat_attachments`
  - `./:/workspace`
  - `./logs/backend:/app/logs`
  - `./logs/frontend:/var/log/nginx`
  - `./data/postgres:/var/lib/postgresql/data`
  - `./data/redis:/data`
  - `./data/backend/dependency-update-cache:/app/data/dependency-update-cache`
  - `./data/backend/dependency-update-backups:/app/update-backups`
  - `/var/run/docker.sock:/var/run/docker.sock` on the updater service only.
- FastAPI backend with health, webhook, realtime WebSocket, and simulator routes.
- Modular I/O packages:
  - `app/modules/lpr`
  - `app/modules/gate`
  - `app/modules/notifications`
  - `app/modules/announcements`
- Integration registry for selecting swappable modules by configuration.
- Event bus abstraction for realtime dashboard updates.
- Database bootstrap and lifespan-managed background services for runtime work.

## Modularity Rule

Core services consume normalized contracts such as `PlateRead`, `GateController`,
and `NotificationSender`. Vendor-specific details remain inside module adapters.
For example, Ubiquiti webhook payload handling is isolated in
`app/modules/lpr/ubiquiti.py`, while Home Assistant gate control is isolated in
`app/modules/gate/home_assistant.py`.

Future hardware changes should add or replace modules and registry entries,
not rewrite access event services.

## Next Phase

The current system now includes:

- SQLAlchemy models for people, groups, vehicles, many-to-many vehicle/person
  assignments, schedules, events, presence, alerts/anomalies, audit logs, report
  exports, dependency update jobs, and Alfred memory/training data.
- Durable movement sessions/sagas for LPR suppression, idempotent gate commands,
  reconciliation, and restart backfill.
- Unauthorized, outside-schedule, duplicate-entry, and duplicate-exit anomaly
  detection surfaced through `/api/v1/alerts`.
