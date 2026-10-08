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

The pure plan carries decision and direction evidence. Execution computes whether
a command is required; persisted journals, movement sagas and receipts retain
command authority and outcome. Resident recovery retains its command requirement in the persisted movement
saga.

The ledger supplies durable idempotency; duplicate reads and retries cannot renew
recognition authority. Suppressed movements remain in history. Reconciliation
resolves pending receipts and admission, while historical/backfill processing
never replays hardware. Unknown plates never actuate devices.

[Optional enrichment](../backend/app/services/access/enrichment.py), including
vehicle data and snapshots, runs separately from the core decision. Copy only
enrichment-owned fields into freshly loaded records so an older object cannot
overwrite newer access or vehicle state. Realtime publication helps the console
refresh; durable records and audit remain the evidence of what happened.

The P6 publication owner is being introduced separately from the active access
runtime. Its [store](../backend/app/services/access/publication.py) reserves a
current compact projection in the caller's transaction; its independent
[lane](../backend/app/services/access/publication_worker.py) sends one loaded
revision and retains a trailing revision if the event changes during delivery.
The shared [transport receipt](../backend/app/services/realtime_contracts.py)
reports Redis stream acceptance or explicit in-process acceptance, never browser
receipt or local business completion. Committed-fact delivery has no optional
five-minute expiry. Runtime replacement and the matching frontend update behavior
remain part of the incomplete P6 stage.

The separate [completion repository](../backend/app/services/access/completion_store.py)
reserves four closed optional stages in the core event transaction. Its
[contracts](../backend/app/services/access/completion_contracts.py) retain the
exact chosen ingest/window identity and earliest immutable receipt, or the
explicit code-owned original external observation. Capture time and webhook trace
metadata do not establish the five-minute provider deadline. Duplicate reservation
cannot renew it. Claims scan at most 32 IDs and lease only the two currently free
media slots; no database connection survives the returned operation budget.

Stage applicators acquire event/domain rows before the completion checkpoint,
then flush all result, audit and receipt writes before their final fresh database
clock check. The origin revision is separate from the public event revision, so
independent media stages can merge without repeating provider I/O. A visitor-link
checkpoint may hydrate a result accepted within the original deadline after
canonical admission arrives later. This link is a bounded database projection,
not new provider work or admission authority. Its envelope alone does not prove
an accepted result or arrival: the applicator must lock and validate the retained
result, exact reservation/current arrival, plate and field ownership. These
applicators and runtime composition remain pending in P6.

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

Reports use compact, bounded [source reads](../backend/app/services/report_reads.py)
in one read-only repeatable snapshot, including runtime configuration. Subject
membership stays relational; previews do not load profile or vehicle photo blobs.
The shared 10,000 activity limit counts every output representation: selected
`events` plus `timeline.all` plus `timeline.selected`. A separate 10,000 subject
vehicle metadata ceiling rejects oversized subjects without truncation. The pure
[duration policy](../backend/app/services/report_durations.py) owns movement state
with an explicit site timezone and bounded strictly prior predecessor reads.
Same-time observations do not establish one another's duration; displayed rows
use ID to break timestamp ties. Departures retain cross-vehicle subject semantics.

The inert [export owner](../backend/app/services/report_exports.py) admits one
export per process without a queue and owns a 120-second deadline through source,
image, template, browser and final persistence work. Source/auth sessions close
before rendering. Request disconnect, cancellation and shutdown stop subsequent
stages; capacity and database disposal wait for actual owned resource cleanup.
[Image preparation](../backend/app/services/report_images.py) caps complete data
URLs at 25 MiB and actual source canvases at 64 Mi-pixels across all embedded
occurrences; PDF inputs must be static raster data. Public preview photo URLs are
unchanged. The [renderer](../backend/app/services/report_renderer.py) denies remote
requests and owns synchronous work in tracked threads. These input caps are not
an RSS guarantee; the measured runtime envelope is in [validation](validation.md#final-runtime-pdf-capability).

Final persistence freshly verifies the original token's expiry/revocation and
current active user/session version, then commits export and required audit
atomically. The public receipt is prepared before commit. Private UUID artifacts
isolate public-number collisions; a known accepted commit remains success even
if later cleanup fails. An unresolved commit retains its artifact for reconciliation
and returns an explicit unknown outcome instead of inviting a blind retry.

Camera AI analyzes images through [provider implementations](../backend/app/ai/providers.py).
Access evidence and UniFi snapshot analysis consume this interface. Reports and
Top Charts query recorded activity; Investigations uses structured filters and
retained evidence. Missed Exit Recovery is an optional bounded admission-recovery
flow; its policy and safety limits are in [integrations](integrations.md).

## Frontend ownership

[App](../frontend/src/app/App.tsx) composes authentication, navigation, account-scoped
read signals, shared operational indicators, toast delivery and realtime transport.
[Navigation](../frontend/src/app/navigation.tsx) owns URLs, labels and role gates;
[routes](../frontend/src/app/routes.tsx) lazily compose feature views. Collections,
queries, selected identities, cursors, errors and event predicates belong to their
features rather than shell state or navigation requirements. The dashboard
prototype is a separate fixture app.

[API types and domain clients](../frontend/src/api/) define frontend contracts.
Use [client.ts](../frontend/src/api/client.ts) for HTTP and the existing
confirmation helpers for protected mutations. Backend permission and confirmation
checks remain authoritative even when the UI hides an action.

The [read signal port](../frontend/src/lib/readSignals.ts) transports realtime
messages and manual/connection refresh intent without selecting feature policy.
Each owner selects events locally and awaits actual HTTP through
[useCoordinatedRead](../frontend/src/lib/useCoordinatedRead.ts) and its independent
[refresh coordinator](../frontend/src/lib/refreshCoordinator.ts). Relevant bursts
retain the current read plus at most one trailing read per owner, with five-second
coalescing. Manual local refresh cancels stale responses and awaits only the
feature's required reads; the toolbar explicitly refreshes all mounted owners.
Account replacement disposes subscriptions, timers and pending reads, and prevents
old responses from publishing into the new session. Shared alerts, maintenance
and gate controls have independent [operational owners](../frontend/src/app/operationalReads.ts).

Directory pagination, server search and selected-ID hydration belong to
[feature reads](../frontend/src/features/directory/reads.ts) and the
[typed directory client](../frontend/src/api/directory.ts). Fresh selected metadata
wins over old page data; deleted selections never fall back to a seed. Refreshes
preserve local drafts and selected chips outside matching search rows. Groups and
schedules retain their existing unpaged catalog API contracts without client
truncation; no bounded-response claim is made for those APIs. Dashboard full
presence remains temporary until the P7 bounded summary migration; visible
plate/owner metadata and active counts already use bounded directory queries.

[History reads](../frontend/src/views/useHistoryPage.ts) preserve filters and the
page-two as-of snapshot on realtime/connection signals, exposing new activity
without jumping pages. Explicit latest refresh resets history. Reports search
visitor subjects on the server with `q` and `limit=8`, then hydrate the selected
ID. Preview identity uses subject IDs, range/options/folds and an explicit revision,
independent of hydrated object-array identity. Report export and dashboard command
receipts retain dedicated feature request lifetimes. Initial read failures appear
separately from empty results; background failures retain usable data with an
explicit stale/error state. Accepted mutations remain accepted when a later read
fails.

[Integration composition](../frontend/src/features/integrations/IntegrationModal.tsx)
resolves complete provider editors by the existing provider key. The
[catalog](../frontend/src/features/integrations/catalog.tsx) explicitly distinguishes
managed editors from scalar settings; exact managed registration coverage rejects
missing or extra bindings. Each mounted editor owns its reads, draft, pending
operations and feedback. Account/session, role and provider identity replace the
editor; same-provider tabs and refreshed server settings preserve the draft.
The shared [dialog frame](../frontend/src/ui/EditorDialog.tsx) guards Escape,
backdrop, Close, Done and provider switching before a keyed editor is replaced.
Pending iCloud handshakes remain meaningful drafts after passwords are cleared.

The narrow [settings protocol owner](../frontend/src/features/integrations/useIntegrationSettingsEditor.ts)
shares confirmed settings save/test mechanics; it does not select providers or
render a generic form engine. Confirmation helpers check the captured editor
lifetime before confirmation and immediately before the write. A stopped-before-write
result is distinct from provider rejection or acceptance. Started writes are
always observed and never aborted as reads; disposal only fences stale UI work
and subsequent operations. Accepted receipts survive failed metadata refreshes,
which remain read-owner errors. Initial metadata failure blocks dependent actions.

The canonical ordered [frontend binding descriptors](../frontend/src/features/settings/accessDeviceProviders.ts)
own discovery, selection/config conversion, labels and confirmed binding paths.
HA then ESPHome enumeration/save order is independent of configured primary/failover
priority. Missing saved bindings remain selectable, and a blank binding retains
its deletion contract. The [save owner](../frontend/src/features/settings/accessDeviceSave.ts)
records every accepted device/binding phase before later confirmation, write or
read failure; retries obtain fresh confirmations. The generic editor and discovery
owner also accept the inert third descriptor exercised by the extension fixture.
Access-device create/save/delete operations fence duplicate submissions, draft
changes and stale pre-mutation reads through their own lifetime and urgent refresh.

Shared editor lifecycle helpers in [ui/](../frontend/src/ui/) protect dirty drafts,
prevent duplicate submission or dismissal while busy, cancel owned reads and restore
focus. OAuth placeholders and artificial integration-test progress waits have been
retired; feedback follows actual pending work. The existing ESPHome 750 ms stream
refresh retains its timing and now fences the in-flight read result on disposal.
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
| Frontend read or invalidation | Put the typed query, result/error state and event predicate under its feature. Use the shared account signal/coordinator lifetime; test actual HTTP, cancellation, pagination, selected-ID metadata and dirty drafts. Keep pending navigation separate from displayed cursors. Do not add shell collections, route/event tables, numeric refresh tokens or seed props. |
| Directory field or operation | Extend the [directory services](../backend/app/services/directory/), [schemas](../backend/app/schemas/directory.py) and [typed client](../frontend/src/api/directory.ts) as needed. Keep validation, assignment changes, audit and commit with the service owner; routes adapt HTTP and confirmation. Migrate every consumer of a changed contract together. |
| Notification behavior | Use the [planner](../backend/app/services/notification_planning.py), [recipients](../backend/app/services/notification_recipients.py) or [authorization](../backend/app/services/notification_authorization.py) owner. Keep transport orchestration and durable delivery separate from policy. |
| Automation action or trigger | Extend the [action definitions](../backend/app/services/workflows/action_definitions.py), generated contracts and explicit typed editor for actions, and the supported trigger catalog and [action handler](../backend/app/services/automation_actions.py), [webhook intake](../backend/app/services/automation_webhooks.py) or [time intake](../backend/app/services/automation_time_intake.py) owner. Preserve checked execution identities, deadlines, idempotency and uncertain outcomes; reuse command and authorization owners. |
| Integration observation or effect | Keep vendor I/O in modules; bind narrow [effect ports](../backend/app/services/integration_effects.py) in [composition](../backend/app/composition.py). Binding must not start services or send requests. Business consumers do not become imports of vendor adapters. |
| Report or dashboard behavior | Keep request, export and command lifetimes in [report](../frontend/src/features/reports/) or [dashboard](../frontend/src/features/dashboard/) hooks. Put rendering in focused components. Keep [duration policy](../backend/app/services/report_durations.py) pure with an explicit site timezone; the compact report read owner and admitted export owner own source and operation lifetimes. |

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

### Access-device binding policies

[Binding policies](../backend/app/modules/access_devices/binding_policies.py)
own provider-specific configuration validation, commandability, physical identity
inputs, frozen runtime inputs and affected setting keys. They receive the caller's
complete typed settings snapshot and perform no I/O. HA and ESPHome preserve their
existing configuration coercion and vendor metadata. Configuration settings that
affect commandability are distinct from the broader keys that lock frozen targets.

The [runtime registry](../backend/app/modules/access_devices/registry.py) requires
exact policy/adapter coverage. A new provider adds a policy and inert adapter;
new typed settings remain in the [settings owner](../backend/app/services/settings.py).
The [configuration owner](../backend/app/services/access_device_configuration.py)
retains provider order, duplicate-target decisions and outer fingerprints. The
device journal and settings services retain target locks, setting locks,
authorization and transaction boundaries; provider policy cannot authorize I/O.
Blank existing bindings are removed before nonblank configuration validation.

The inert [extension fixture](../scripts/validation/test_provider_binding_persistence.py)
exercises discovery, provider-owned validation, persistence/rollback, canonical
typed admission settings and unchanged frozen-plan composition. It invokes no
hardware transport. Golden identity/hash fixtures preserve exact HA URL/token
and whole ordered ESPHome-list inputs, including unrelated device/secret changes.

### Workflow contracts

[Action definitions](../backend/app/services/workflows/action_definitions.py) own
closed configuration models, defaults, execution mode, effect and maintenance
classification, uncertainty continuation and editor identity.
[Channel definitions](../backend/app/services/workflows/channel_definitions.py)
own notification config, templates and channel capabilities. These pure owners
import no ORM, context, provider, runtime configuration or authorization policy.

The executor's action-type binding map and notification delivery map must cover
their definitions exactly. Transactional handlers share the caller's session:
mutation or domain reservation, action receipt and required audit commit together.
Their checked operation identity comes from the currently owned pending action;
it does not prove an external attempt. Durable effects retain the attempted
checkpoint before independent work and their existing domain authorization.

New writes and executable notification tests validate raw actions before display
normalization. Preview remains a tolerant display operation. Historical serializers
retain unknown records;
unsupported or malformed pending work becomes an explicit review hold before
attempt. Attempted payloads and frozen destinations remain unchanged on recovery.
Keep automation and notification stores separate. Automation intake uses durable
trigger producers and its scheduler; it has no realtime subscription. The
notification listener treats `notification.trigger` only as a wake hint. Visitor
realtime messages cannot reserve work; pure workflow mappers remain available to
the durable producers.

[The exporter](../backend/app/services/workflows/export_contracts.py) derives
[static frontend types and defaults](../frontend/src/api/generated/workflows.ts).
Its supported schema subset is deliberate; unsupported constructs fail generation.
Editor controls remain explicit and typed. Add a definition, its action-type
handler binding, config/editor coverage and a domain reservation fixture; no
action-specific branch belongs in a dispatcher, run store or facade.

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
Its in-session invalidation operation clears the snapshot and owned summary
fields together when a registration changes. Directory mutations retain the
vehicle lock, actor checks, cached replacement lookup, required audit and commit;
the store never commits independently. User-entered make/model remain outside
this reset. Summary reads do not join or load history. Vehicle registration and request-time
checks prevent late refreshes overwriting a changed plate or newer data. Existing
models are preserved; DVSA fills blanks. Editor request cancellation and individual
field edits remain owned by the directory feature. Source and freshness travel
with summaries; raw provider responses never become API or audit data.


### Accepted optional results and visitor arrival hydration

The [accepted-result participant](../backend/app/services/access/completion_results.py)
keeps a visual receipt on its existing completion row and the latest make/colour
receipt on the existing per-event information job. Information acceptance advances
a result revision independently of the public event revision. Accepted projection,
event enrichment, link request and publication dirtiness share the caller commit;
original token, receipt and deadline are checked again after all writes flush.
Preparers lock only current identity columns before closing the transaction for
I/O. A result first accepted after the original receipt plus five minutes is refused.
Later retry, failure or expiry preserves an earlier accepted information projection.

The [provider-free linker](../backend/app/services/access/visitor_link.py) locks the
exact canonical Saga before Event, then Visitor, consumed reservation, visual
receipt, information job and link checkpoint. It verifies retained result identities,
verified consumed intent, current arrival and exact plate; it does not require an
ACTIVE pass or a still-open admission window. A result accepted on time may hydrate
a USED arrival verified later. Missing or ambiguous canonical saga, changed target
or missing frozen field baseline holds the link rather than guessing authority.
The caller supplies that typed baseline under its existing Visitor lock.

Per-field state is never-owned, owned with its last written value, or protected
following an observed distinct replacement/clear. Baseline equality is considered
only before the first owner write. An empty baseline filled Blue, explicitly cleared,
then offered Green stays empty. A protected field cannot reacquire ownership merely
because it later equals the initial baseline. Information colour precedes visual
fallback; absent source values never clear a field. The current Visitor API has no
direct make/colour setter; the supported plate-update path is also fenced by exact
plate identity. Unobservable same-value write intent is not claimed as detectable.
Mutation, required arrival-enriched audit and per-kind applied receipt are atomic.

The origin revision fences the immutable completion contract. A leased linker reads
the latest coherent requested result set under Event; an acceptance waiting behind
that lock creates at most one trailing request after commit. It never changes the
origin revision to represent a later result version.

This partial P6 checkpoint retires the information result path's inverse Job-to-Event
lock order and inline visitor mutation. Current enqueue still commits afterward:
it calls the one participant reservation using the minimum genuinely linked ingest
receipt with consistent event/saga/plate provenance. Missing provenance is held;
replay cannot renew a deadline, and already expired original work stays expired.
Final core wiring must reserve the job in the decision transaction instead. The old
information claim/failure/renewal/slot/shutdown shell and old RAM optional access
runtime are not approved by this applicator checkpoint; their bounded lifetime,
fresh database-time fences and composition remain required P6 work.
