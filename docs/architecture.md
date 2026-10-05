# Architecture and retirement guide

IACS has explicit owners for durable access decisions, hardware commands, provider
I/O, camera image analysis, and the realtime console. Use the focused agent guides for
implementation detail: [backend](agent/backend.md), [frontend](agent/frontend.md),
and [hardware safety](agent/hardware-safety.md).

## Extension rules

1. Identify the domain owner before editing. Keep vendor I/O in modules and
   business rules in services.
2. Keep API and UI presentation as adapters. They must call the same
   audited operations as other callers.
3. Preserve durable history, audit, reconciliation, and provider contracts.
   Realtime is enrichment, never the audit record.
4. For retirement, search callers, registries, callbacks, tests, configuration,
   Compose, UI, and documentation. Delete obsolete implementation and its public
   surface together; do not replace it with an alias or placeholder.
5. Use the isolated [validation harness](validation/phase1.md), including
   untracked files. Production rollout is a separate task.

## Current boundaries

Backend paths below are relative to `backend/app/`; frontend paths are relative
to the repository root.

- LPR ingest, debounce and durable suppression: `services/access_events.py`.
  Evidence, decision, execution and optional enrichment live under
  `services/access/`. Exact movement ownership is maintained in the backend
  guide.
- Gate commands: `services/gate_commands.py`; garage and access devices:
  `services/access_devices.py`. Hardware adapters stay in `modules/`.
- Camera image analysis: `ai/providers.py`, used by access evidence and UniFi snapshots.
- Notification durable claims: `services/notification_runs.py`; dispatch:
  `services/notification_dispatch.py`; providers/orchestration: `services/notifications.py`; pure rendering:
  `services/notification_rendering.py`.
- Recognition authority and expiry: `services/access/authorization.py`;
  verified admission: `services/movement/admission.py`.
- Missed-exit recovery: `services/resident_recovery.py`, with pure journey
  policy in `services/resident_recovery_evidence.py`. Its scoped resident
  capability uses the same command/admission owners and cannot invent an exit.
- Frontend shell/refresh: `frontend/src/app/`; typed API clients:
  `frontend/src/api/`; feature and route owners remain under
  `frontend/src/features/` and `frontend/src/views/`.

## Retired updater systems

The application no longer performs dependency or UniFi Protect package updates;
normal UniFi cameras, events, snapshots, analysis, General, and Exposes remain.
See [the retirement release note](releases/remove-dependency-updaters.md) for
migration, retained-file, and rollback limits.

Keep durable behavior in the focused guides and validation contracts. When retiring a feature, remove stale
plans and duplicate documentation along with its callers; Git history preserves
superseded implementation notes.
