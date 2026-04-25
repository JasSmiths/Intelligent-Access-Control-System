# Phase 1: Deployment and Backend Scaffold

This phase establishes the runtime contract and backend boundaries for the
Intelligent Access Control System.

## Delivered

- Docker Compose deployment with `backend`, `postgres`, and `redis`.
- Host bind mounts only:
  - `./data/backend:/app/data`
  - `./logs/backend:/app/logs`
  - `./data/postgres:/var/lib/postgresql/data`
  - `./data/redis:/data`
- FastAPI backend with health, webhook, realtime WebSocket, and simulator routes.
- Modular I/O packages:
  - `app/modules/lpr`
  - `app/modules/gate`
  - `app/modules/notifications`
  - `app/modules/announcements`
- Integration registry for selecting swappable modules by configuration.
- Event bus abstraction for realtime dashboard updates.
- Database and worker packages ready for Phase 2 models and queues.

## Modularity Rule

Core services consume normalized contracts such as `PlateRead`, `GateController`,
and `NotificationSender`. Vendor-specific details remain inside module adapters.
For example, Ubiquiti webhook payload handling is isolated in
`app/modules/lpr/ubiquiti.py`, while Home Assistant gate control is isolated in
`app/modules/gate/home_assistant.py`.

Future hardware changes should add or replace modules and registry entries,
not rewrite access event services.

## Next Phase

Phase 2 will add:

- SQLAlchemy models for people, groups, vehicles, schedules, events, presence,
  anomalies, and audit logs.
- Ubiquiti debounce and confidence-window resolution.
- Event classification against historical rhythms.
- Unauthorized and impossible-state anomaly detection.
