# Architecture and retirement guide

IACS has explicit owners for durable access decisions, hardware commands, provider
I/O, Alfred operations, and the realtime console. Use the focused agent guides for
implementation detail: [backend](agent/backend.md), [frontend](agent/frontend.md),
and [hardware safety](agent/hardware-safety.md).

## Extension rules

1. Identify the domain owner before editing. Keep vendor I/O in modules and
   business rules in services.
2. Keep API, UI, and Alfred presentation as adapters. They must call the same
   audited operations as other callers.
3. Preserve durable history, audit, reconciliation, and provider contracts.
   Realtime is enrichment, never the audit record.
4. For retirement, search callers, registries, callbacks, tests, configuration,
   Compose, UI, and documentation. Delete obsolete implementation and its public
   surface together; do not replace it with an alias or placeholder.
5. Use the isolated [validation harness](validation/phase1.md), including
   untracked files. Production rollout is a separate task.

## Current boundaries

- LPR ingest, debounce and durable suppression: `services/access_events.py`.
  Evidence, decision, execution and optional enrichment live under
  `services/access/`. Exact movement ownership is maintained in the backend
  guide.
- Gate commands: `services/gate_commands.py`; garage and access devices:
  `services/access_devices.py`. Hardware adapters stay in `modules/`.
- Alfred contracts/context/catalog assembly: `app/ai/`; orchestration:
  `services/chat.py` and `services/alfred/`.
- Notification durable claims: `services/notification_runs.py`; dispatch:
  `services/notification_dispatch.py`; rendering/providers:
  `services/notifications.py`.
- Frontend shell/refresh: `frontend/src/app/`; typed API clients:
  `frontend/src/api/`; feature and route owners remain under `features/` and
  `views/`.

## Retired updater systems

The application no longer performs dependency or UniFi Protect package updates;
normal UniFi cameras, events, snapshots, analysis, General, and Exposes remain.
See [the retirement release note](releases/remove-dependency-updaters.md) for
migration, retained-file, and rollback limits.

Historical milestone and architecture-review documents are retained evidence,
not active implementation instructions. Start from this guide and the focused
agent guides when they differ.
