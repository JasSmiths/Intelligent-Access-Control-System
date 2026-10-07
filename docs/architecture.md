# Architecture

IACS has a FastAPI backend, a React console, PostgreSQL for durable state, and
Redis for transient coordination. The backend owns access policy, movement,
presence, workflows, and provider operations. The console presents those results
and requests changes through typed `/api/v1` clients.

This guide explains how to find and extend the current owners. Use
[development](development.md) to run the system,
[integrations](integrations.md) for external effects, and
[validation](validation.md) to check changes.

## Backend ownership

[main.py](../backend/app/main.py) constructs the application and owns startup and
shutdown. [API registration](../backend/app/api/router.py) defines the supported
route groups. Request handlers validate input, authenticate callers, and adapt
confirmation and responses; services own business rules and transactions;
modules own vendor protocol I/O.

| Concern | Start here |
| --- | --- |
| LPR intake and processing | [AccessEventService](../backend/app/services/access_events.py), [durable intake](../backend/app/services/lpr_ingest.py), [access stages](../backend/app/services/access/) |
| Movement and admission | [Direction FSM](../backend/app/services/movement_fsm.py), [movement ledger](../backend/app/services/movement_ledger.py), [admission](../backend/app/services/movement/admission.py) |
| Gate and garage commands | [GateCommandCoordinator](../backend/app/services/gate_commands.py), [AccessDeviceService](../backend/app/services/access_devices.py) |
| Directory, schedules, and passes | [Directory API](../backend/app/api/v1/directory.py), [schedule operations](../backend/app/services/schedule_operations.py), [visitor passes](../backend/app/services/visitor_passes.py) |
| Notifications and automations | [NotificationService](../backend/app/services/notifications.py), [AutomationService](../backend/app/services/automations.py), [workflow contracts](../backend/app/services/workflows/) |
| Configuration and authentication | [Runtime settings](../backend/app/services/settings.py), [bootstrap configuration](../backend/app/core/config.py), [authentication](../backend/app/services/auth.py) |
| Schema and audit | [Models](../backend/app/models/), [Alembic migrations](../backend/alembic/versions/), [telemetry and audit](../backend/app/services/telemetry.py) |

## From a plate read to presence

1. The LPR adapter produces a plate read. Webhook security validates the request
   before durable intake. AccessEventService stores intake, manages workers and
   debounce windows, and records explainable suppression.
2. [Evidence resolution](../backend/app/services/access/evidence.py) loads
   identity, schedules, movement history, and applicable camera/recovery evidence.
   The movement FSM resolves direction; the
   [pure access policy](../backend/app/services/access/decision.py) builds the plan.
3. [Execution](../backend/app/services/access/execution.py) persists the event,
   movement saga, session/pass state, anomalies, and required delivery intents.
   It commits the decision before any hardware dispatch.
4. If a gate command is required, the hardware stage uses the command coordinator
   and rechecks [current recognition authority](../backend/app/services/access/authorization.py).
   Command receipts distinguish delivery, acceptance, and verification.
5. The admission owner finalizes eligible session and
   [presence transitions](../backend/app/services/movement/presence.py) in one
   transaction. A grant awaiting gate verification does not establish admission.
   Applicable garage fanout follows verified admission through the device owner.

The ledger supplies durable idempotency; duplicate reads and retries cannot renew
recognition authority. Suppressed movements remain in history. Reconciliation
resolves pending receipts and admission, while historical/backfill processing
never replays hardware. Unknown plates never actuate devices.

[Optional enrichment](../backend/app/services/access/enrichment.py), including
vehicle data and snapshots, runs separately from the core decision. Copy only
enrichment-owned fields into freshly loaded records so an older object cannot
overwrite newer access or vehicle state. Realtime publication helps the console
refresh; durable records and audit remain the evidence of what happened.

## Other application flows

People, groups, vehicles, schedules, and visitor passes provide identity and access
configuration. Schedule evaluation and assignments have separate owners under
`services/`; API adapters must reuse those operations. Manual duration passes
require a plate when created. Calendar synchronization feeds the same pass owner.

Notifications and automations use shared trigger, action, and template contracts.
Rules are database records. Required delivery handoffs join the originating
transaction; the [notification run store](../backend/app/services/notification_runs.py)
and [dispatcher](../backend/app/services/notification_dispatch.py) persist attempts
and outcomes around provider I/O. Automations have their own durable runs and
route hardware actions through the same command owners. An uncertain attempted
effect needs review rather than an automatic resend.

Camera AI analyzes images through [provider implementations](../backend/app/ai/providers.py).
Access evidence and UniFi snapshot analysis consume this interface. Reports and
Top Charts query recorded activity; Investigations uses structured filters and
retained evidence. Missed Exit Recovery is an optional bounded admission-recovery
flow; its policy and safety limits are in [integrations](integrations.md).

## Frontend ownership

[App](../frontend/src/app/App.tsx) composes the shell.
[Navigation](../frontend/src/app/navigation.tsx) owns URLs, labels, role gates,
and required shell data; [routes](../frontend/src/app/routes.tsx) lazily compose
views. Route views live in `frontend/src/views/`; editors and feature logic live
in `frontend/src/features/`. The dashboard prototype is a separate fixture app.

[API types and domain clients](../frontend/src/api/) define frontend contracts.
Use [client.ts](../frontend/src/api/client.ts) for HTTP and the existing
confirmation helpers for protected mutations. Backend permission and confirmation
checks remain authoritative even when the UI hides an action.

The shell's realtime modules select affected data and route refreshes; the
[refresh coordinator](../frontend/src/app/refreshCoordinator.ts) serializes reads.
Preserve account/request lifetime checks so stale responses cannot overwrite
completed mutations. Show initial load failures separately from empty results,
and retain usable data with an explicit stale state after refresh failure.

Shared editor lifecycle helpers in [ui/](../frontend/src/ui/) protect dirty drafts,
prevent duplicate submission or dismissal while saving, and restore focus.
Global styles are composed by [styles.css](../frontend/src/styles.css); feature
styles may load with lazy routes. The navigation drawer changes at 980px and
compact editor layouts at 720px; validate both when layout changes.

## Making a change

Find callers with `rg -l`, then read the owner and relevant tests. Keep feature
logic out of the shell, provider I/O out of policy, and transaction ownership in
services. Interactive mutations use trusted actor context and retain validation
and durable audit in the domain owner.

Retiring a feature includes its callers, registrations, settings, tests, UI, and
documentation. Do not leave aliases, fallback catalogs, provider bypasses, or
runtime schema repair. Apply schema changes through Alembic. Update the relevant
guide in the same change and run the checks in [validation](validation.md).

## Retirement boundaries

The current application has no Alfred assistant, dedicated WhatsApp or Discord
providers, conversational visitor workflows, or AI-authored automation actions.
Camera image analysis, Home Assistant and general Apprise notifications remain.
The dashboard prototype follows the same feature boundary.

Historical migrations and checksum-pinned schema fixtures retain the old names
to prove upgrades from earlier releases. Retained audit categories and redaction
rules preserve readable, safe historical records. These are historical data
handling, not available operations. Keep them with their migration and audit
tests; remove unused runtime adapters, mock conversations and prototype controls.

[Runtime retirement tests](../backend/tests/test_retired_feature_contracts.py)
check routes, settings, models and workflow catalogs alongside retained provider
contracts. [Populated retirement checks](../scripts/validation/test_feature_retirement.py)
verify that queued captured work from retired features is held for review while
completed delivery history is preserved.
