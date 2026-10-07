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
modules own vendor protocol I/O. [Application composition](../backend/app/composition.py)
wires Home Assistant effects, maintenance notification intake, and device status
ports without starting I/O. Integration services do not import the business
owners that consume their observations. The service-backed
[gate controller](../backend/app/services/gate_controller.py) receives the device
owner explicitly; vendor modules never construct business services.

| Concern | Start here |
| --- | --- |
| LPR intake and processing | [AccessEventService](../backend/app/services/access_events.py), [durable intake](../backend/app/services/lpr_ingest.py), [access stages](../backend/app/services/access/) |
| Movement and admission | [Direction FSM](../backend/app/services/movement_fsm.py), [movement ledger](../backend/app/services/movement_ledger.py), [admission](../backend/app/services/movement/admission.py) |
| Gate and garage commands | [GateCommandCoordinator](../backend/app/services/gate_commands.py), [AccessDeviceService](../backend/app/services/access_devices.py) |
| Directory, schedules, and passes | [Directory services](../backend/app/services/directory/), [schedule operations](../backend/app/services/schedule_operations.py), [visitor passes](../backend/app/services/visitor_passes.py) |
| Notifications and automations | [NotificationService](../backend/app/services/notifications.py), [AutomationService](../backend/app/services/automations.py), [workflow contracts](../backend/app/services/workflows/) |
| Configuration and authentication | [Runtime settings](../backend/app/services/settings.py), [bootstrap configuration](../backend/app/core/config.py), [authentication](../backend/app/services/auth.py) |
| Schema and audit | [Models](../backend/app/models/), [Alembic migrations](../backend/alembic/versions/), [telemetry and audit](../backend/app/services/telemetry.py) |

## From a plate read to presence

1. The LPR adapter produces a plate read. Webhook security validates the request
   before durable intake. AccessEventService stores intake, manages workers and
   debounce windows, and records explainable suppression.
   [Pure candidate ranking](../backend/app/services/access/plate_matching.py)
   preserves exact/fuzzy priority. The worker queries normalized active exact
   candidates through a partial index before loading registrations for fuzzy
   fallback; recognition authority is still checked at dispatch.
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
Notification planning, recipient selection and authorization have separate owners;
the notification facade retains transport orchestration. Automation rule operations,
webhook intake, time intake and action dispatch also have separate owners. The
[dispatch ports](../backend/app/services/workflow_dispatch_ports.py) and
[execution contracts](../backend/app/services/workflows/execution_contracts.py)
check persisted action identity, status and deadlines at the boundary. New actions
join the supported handler table and reuse existing authorization/command owners.
Rules are database records. Required delivery handoffs join the originating
transaction; the [notification run store](../backend/app/services/notification_runs.py)
and [dispatcher](../backend/app/services/notification_dispatch.py) persist attempts
and outcomes around provider I/O. Automations have their own durable runs and
route hardware actions through the same command owners. An uncertain attempted
effect needs review rather than an automatic resend.

Directory routes delegate mutations, assignments and audit to the directory
services. Read operations return bounded pages; editors hydrate explicitly selected
identities through detail/lookup reads. Snapshot fallback uses per-registration
latest-row lookups rather than ranking complete movement histories.

Reports query the selected period and necessary predecessor state. The pure
[duration policy](../backend/app/services/report_durations.py) owns the movement
state and chronological calculation, with an explicit site timezone. Observations
at the same timestamp do not establish one another's duration. Departures retain
the existing cross-vehicle subject
semantics. Timeline inputs stream in batches; report output still grows with the
selected period and PDF generation remains a separate cost.

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

Directory pagination, search, selected-ID hydration and cancellation belong to
[feature reads](../frontend/src/features/directory/reads.ts) and the
[typed directory client](../frontend/src/api/directory.ts). The route boundary
provides directory refresh context so nested selectors reload selected records
outside the current page when shell data changes. Metadata refresh preserves
local editor drafts, and selected chips remain separate from matching search rows.
Dashboard resolves the
identities needed for its visible data; Reports searches its subjects on the server.
Report selection/preview/export and dashboard command receipts have dedicated
feature hooks and components. Preserve those request and draft lifetimes when
extending a view.

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

The architecture guard rejects runtime cycles, vendor imports of business owners,
service imports of API adapters, direct directory-route transactions and shared UI
imports of features. Runtime configuration through `services.settings` is the
existing shared adapter configuration boundary. Explicit exception handling at
vendor, cleanup and optional-publication boundaries preserves arbitrary failure
truth; any lint exception there must have a local justification.

Retiring a feature includes its callers, registrations, settings, tests, UI, and
documentation. Do not leave aliases, fallback catalogs, provider bypasses, or
runtime schema repair. Apply schema changes through Alembic. Update the relevant
guide in the same change and run the checks in [validation](validation.md).

## Extension rules

Choose the owner before implementation. A feature should have one place for its
policy and transaction, with small adapters for HTTP, UI and vendor protocols.
Change the owners and contracts the feature actually needs; broad edits across
unrelated services or views are a signal to review the boundary. File count alone
is not a design goal: avoid both large mixed-purpose files and a scattering of
one-function wrappers. Extract cohesive responsibilities with an explicit interface;
introduce shared abstractions only for demonstrated shared behavior.

| Change | Extension point and invariants |
| --- | --- |
| Directory field or operation | Extend the [directory services](../backend/app/services/directory/), [schemas](../backend/app/schemas/directory.py) and [typed client](../frontend/src/api/directory.ts) as needed. Keep validation, assignment changes, audit and commit with the service owner; routes adapt HTTP and confirmation. Migrate every consumer of a changed contract together. |
| Notification behavior | Use the [planner](../backend/app/services/notification_planning.py), [recipients](../backend/app/services/notification_recipients.py) or [authorization](../backend/app/services/notification_authorization.py) owner. Keep transport orchestration and durable delivery separate from policy. |
| Automation action or trigger | Extend the supported catalog and the [action handler](../backend/app/services/automation_actions.py), [webhook intake](../backend/app/services/automation_webhooks.py) or [time intake](../backend/app/services/automation_time_intake.py) owner. Preserve checked execution identities, deadlines, idempotency and uncertain outcomes; reuse command and authorization owners. |
| Integration observation or effect | Keep vendor I/O in modules; bind narrow [effect ports](../backend/app/services/integration_effects.py) in [composition](../backend/app/composition.py). Binding must not start services or send requests. Business consumers do not become imports of vendor adapters. |
| Report or dashboard behavior | Keep request, export and command lifetimes in [report](../frontend/src/features/reports/) or [dashboard](../frontend/src/features/dashboard/) hooks. Put rendering in focused components. Keep [duration policy](../backend/app/services/report_durations.py) pure with an explicit site timezone; the report service owns reads and export orchestration. |

Type inputs, outputs and dependency ports. Use concrete schemas, dataclasses,
protocols or TypedDicts for domain values rather than passing unstructured
`dict[str, Any]` through policy. Variable JSON/provider metadata may remain dynamic
at its boundary; validate it before it becomes an execution identity, action,
deadline, state or outcome. Persisted/wire format changes need explicit migration
and consumer changes, not silent casts or compatibility fallbacks.

Collection APIs must define server-side filters, stable ordering and a bounded
page/lookup size. Reset cursors when filters change, and hydrate selected identities
outside the current page. Use the route directory refresh context so nested
selectors receive invalidation without forwarding the same prop through every
editor. Cancel superseded requests and protect local drafts when metadata changes.
Do not rebuild a complete directory in the shell, concatenate every page, or
apply a narrower local filter that hides valid server search matches.

Select only needed database columns, including related rows. Compact media reads
must not materialize photo blobs or trigger asynchronous lazy loads. Avoid query
counts that grow once per returned row; batch selected-ID lookups within API bounds.
Use index-backed exact plate and latest-snapshot reads. Report history must stay
bounded to necessary predecessor state, with strictly prior timestamp semantics;
stream projected timeline inputs and account separately for output/PDF cost.
Performance changes must preserve authorization freshness, transaction ordering
and recovery behavior. Do not trade those guarantees for a cache or a shorter path.

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

## Vehicle information enrichment

[Vehicle information](../backend/app/services/vehicle_information.py) coordinates
DVLA/DVSA cache and requests; [pure contracts](../backend/app/services/vehicle_information_contracts.py)
own normalization and MOT precedence. The DVSA module owns HTTP/token I/O only.
The [arrival worker](../backend/app/services/vehicle_information_jobs.py) owns a
small leased database queue and post-lookup transactions. It runs outside the
serial LPR worker and never holds a database connection over provider I/O.
Required access notifications, admission, presence and hardware keep their owners.
MOT/tax notice handoffs use stable event identities and the originating job deadline.

The [snapshot owner](../backend/app/services/vehicle_information_store.py) stores
allowlisted results separately from vehicle rows and pages history on the server.
Summary reads do not join or load history. Vehicle registration and request-time
checks prevent late refreshes overwriting a changed plate or newer data. Existing
models are preserved; DVSA fills blanks. Editor request cancellation and individual
field edits remain owned by the directory feature. Source and freshness travel
with summaries; raw provider responses never become API or audit data.
