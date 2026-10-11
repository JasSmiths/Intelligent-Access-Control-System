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
   before durable intake. AccessEventService stores immutable intake and one
   fenced coordinator consumes ready identities in durable receipt order. Window
   summaries, complete member associations and explainable suppression commit
   together; no RAM queue or debounce window establishes authority.
   [Pure candidate ranking](../backend/app/services/access/plate_matching.py)
   preserves exact/fuzzy priority. The worker queries normalized active exact
   candidates through a partial index before loading registrations for fuzzy
   fallback; recognition authority is still checked at dispatch.
2. [Evidence resolution](../backend/app/services/access/evidence.py) loads
   identity, schedules, movement history, and applicable camera/recovery evidence.
   The movement FSM resolves direction; the
   [pure access policy](../backend/app/services/access/decision.py) builds the plan.
3. [Execution](../backend/app/services/access/execution.py) persists the event,
   movement saga, session/pass state, anomalies, required delivery intents,
   optional completion reservations, publication and chart dirty work in the same
   transaction that terminalizes every window member. HA reservation is the last
   domain participant. The decision commits before any hardware dispatch.
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

[Historical candidates](../backend/app/services/historical_candidates.py) reserve
one canonical Protect-provider/event identity before domain mutation. The first
validated capture, discovery source/reason and deterministic event ID remain
immutable. The existing restart/reconciliation owner drains at most 32 due
candidates before checking discovery availability or its current time window.
One token-owned thumbnail preparation runs outside database sessions with a
20-second budget; settled media is reused across complete transaction retries.
Interrupted preparation becomes an explicit hold. A final 30-second transaction
commits candidate completion, event, movement, audit, initial publication and silent
chart dirty work together, with HA invalidation as its last domain participant.
Five failed historical-candidate attempts produce a durable hold; this budget is
separate from the core pipeline's attempt policy. Old differently keyed candidate
rows remain readable during drain. Before P6 activation, any pre-cutover rows for
the same Protect event must be reconciled to the canonical provider identity.

[Optional completion](../backend/app/services/access/completion_worker.py),
including visual evidence, snapshots and visitor linkage, runs under fixed owned
capacity after the core commit. Vehicle information uses its existing independent
job owner. These owners retain original receipt deadlines and apply only their
fields to freshly locked records. Required camera evidence remains part of core
resolution; its five-second decision deadline does not detach child cleanup.

The [publication store](../backend/app/services/access/publication.py) reserves
initial delivery in the same transaction as event creation. Its independent
[lane](../backend/app/services/access/publication_worker.py) reads the canonical
[event resource](../backend/app/services/access/event_reads.py) shared by HTTP,
sends one revision and retains a trailing revision after concurrent changes.
The Dashboard reconciles actual GET and realtime resources by revision. A bounded
ambiguous legacy Saga association presents an explicit held conflict and cannot
select arbitrary authority. Linked visitor transition signals use the same lane
and acknowledgment; transport acceptance before acknowledgment loss can repeat a
signal. This is at-least-once delivery, not an exactly-once browser guarantee.
The shared [transport receipt](../backend/app/services/realtime_contracts.py)
reports Redis stream acceptance or explicit in-process acceptance. Durable records
and mandatory audit remain evidence; committed-fact delivery has no optional
five-minute expiry.

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
result, exact reservation/current arrival, plate and field ownership.

Core preparation and resumed finalization share a total three-attempt ingest
budget, including the initial attempt. Exhaustion holds the whole frozen window;
stop retains open windows for restart rather than finalizing them early. The
health endpoint reports durable queue depth, pending windows and original receipt
age through indexed scalar reads, while readiness uses only local worker state.
Authenticated `/api/v1/events/{event_id}/candidates` pages at most 50 sanitized
members with a window-bound cursor. Event preview totals and references distinguish
that bounded preview from complete evidence. Runtime source is composed; final
P6 integrated validation and approval remain separate gates in the closure record.

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

### Calendar operation ownership

[Account/auth transactions](../backend/app/services/calendar_accounts.py),
[authentication orchestration](../backend/app/services/calendar_authentication.py),
[run storage](../backend/app/services/calendar_sync_store.py),
[account acceptance](../backend/app/services/calendar_sync_commit.py) and
[reconciliation](../backend/app/services/calendar_reconciliation.py) have distinct
transaction responsibilities. The [engine](../backend/app/services/calendar_sync_engine.py)
keeps one original attempt deadline and one renewal child across detached vendor
work. Account/domain/intents precede final current requester/workflow authority
and the run checkpoint lock. All writes flush before a final fresh clock and
exact lease/generation/authority-expiry check; no pending writes follow it.

The [SDK admission owner](../backend/app/services/icloud_calendar_sdk.py) owns
actual native work and pending clients. Bounded postaccept metadata/publication
runs inside that same admitted child, so process stop drains it before database
or provider disposal. [API adapters](../backend/app/api/v1/icloud_calendar.py)
authenticate in a short owned read and carry detached canonical authority; no
request-yield auth transaction spans SDK work. The existing checked workflow
execution reaches a narrow [current-authority participant](../backend/app/services/calendar_workflow_authority.py),
with final NOWAIT locks rather than a second permission policy.

[Auth-root coordination](../backend/app/services/auth_root.py) fences participating
settings seed/update, calendar ciphertext writers and secret rotation/migration.
Rotation retains its dedicated session-level lock across file/DB outcome and
cleanup; short writers reload under the same transaction-level fence before
actor/domain locks. Required audit UUIDs provide positive outcome evidence.
An unresolved outcome preserves recovery keys; it never blindly restores an old
key based on an absent receipt. This finite writer boundary does not globally
repair unrelated cached JWT/configuration readers.

The calendar editor owns exact-run read/acceptance generations and two UUID-only
recovery references. Passes owns at most the open detail/editor IDs; aggregate
calendar cancellation invalidates their metadata, while full row replacement
fences older selected GETs. Fresh status/errors can disable dependent actions
without resetting dirty fields. Calendar behavior extensions change the
reconciliation owner and its fixtures; protocol-envelope changes belong to the
vendor module, not the shared facade or editor frame.

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
truncation; no bounded-response claim is made for those APIs. Dashboard uses the
[compact presence owner](../backend/app/services/presence_reads.py) for global
state counts and at most six inside names from one statement snapshot, plus the
same forecast's six-name internal summary. Both responses include explicit hidden
counts. Visible plate/owner metadata, these selected person IDs and active vehicle
counts use bounded directory queries. Exited Today still derives from the recent
40 events. Public `/presence` remains population-sized and keeps its existing
updated-time order; it projects only its four public fields. Internal equal-change
name ties use deterministic PostgreSQL C collation, followed by updated-time and
person-ID ties, rather than browser locale ordering.

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
small leased database queue and post-lookup transactions. Its two process-local
slots remain occupied until actual provider/result cancellation cleanup settles.
A metadata-only page of 32 candidates leases only free slots; terminal prefixes
continue without treating the remaining backlog as empty. Claims, renewal and
failure writes use fresh database time after blocking row locks, flush before their
last lease check, and preserve earlier accepted results. The 90-second operation
budget is clipped to the original receipt deadline and remaining lease; renewal
cannot revive an expired claim. It runs outside the serial LPR worker and never
holds a database connection over provider I/O. Required access notifications,
admission, presence and hardware keep their owners. MOT/tax notice handoffs use
stable event identities and the originating job deadline.

The shared vehicle-information service owns each lookup's two parallel provider
children and drains both after an unexpected failure or cancellation. Connection
tests use the same single-cancel ownership boundary. Per-call completion receipts
track actual lookup cleanup without waiting for unrelated request/dispatcher parent
work. One retained cleanup task drains admitted calls and attempts both client
closes, even if one fails. The worker stops only its own work; main closes the
shared service after producer and notification consumers drain and before the
database. Closing rejects new calls and cannot construct a replacement cached
client. The ASGI server still owns general request draining; the shared service
independently retains every already-admitted lookup through client settlement.
Whole lookup/job children receive one cancellation. Settings cache misses and the
worker's claim/preparation/result/journal transactions cancel active SQL normally,
then explicitly drain their actual session close. A normal context-exit close is
also owned when cancellation first arrives there; a shielded but abandoned close
task cannot outlive the receipt, occupied slot or database resource. Settings cache
and conversion behavior remain with their existing owner.

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
information claim/failure/renewal/slot/shutdown shell is addressed by its separate
worker/shared-client checkpoint; the applicator approval alone does not cover it.
Old RAM optional access runtime replacement, atomic core reservation and the
remaining P6 owners still require integrated evidence and review.


HA target/effect URL and entity identity columns retain full TEXT values, matching
the existing settings and Person JSONB contract. Their bounded target keys and
fingerprints remain separate from the exact identity; nonindexed identity values
are never truncated to a URL/entity length assumption. UNKNOWN effects and physical
target barriers remain retained independently of originating Person/event lifetime.
This is schema compatibility for the pending HA owner, not runtime authority.


The inert [completion lifetime](../backend/app/services/access/completion_worker.py)
supplies two actual optional-work slots through explicit visual, snapshot,
visitor-link and zone-shadow ports, separate from committed-facts publication.
Its [stage owner](../backend/app/services/access/completion_media.py) prepares
scalar current identity and saved compact input without hydrating arbitrary raw
evidence before provider I/O. Input is captured once at reservation; later malformed
input holds rather than rebuilding it from changed evidence. Relevant zone arrays
remain complete and are not assumed to have a tiny fixed size.

One operation deadline is captured before preparation. The fresh DB-derived
remaining lease/origin budget is anchored to loop time before the query and may
only shorten that deadline. Explicit stage arguments preserve it across session
close and delayed child execution. Actual provider/visual children and all three
snapshot threads remain owned through cleanup; expiry permits no late work or
result authority. Information lookup uses the same conservative start boundary;
publication is bounded by its current lease and does not acquire optional-work expiry.

Snapshot files use immutable event/claim primary, archive and manifest keys, while
the stable event URL resolves the committed `snapshot_path`. Legacy event UUID keys
still restore. Event/anomaly metadata, the stage receipt and public revision share
the acceptance transaction. A settled precommit failure may delete only its exact
unreferenced claim files after serialized Event→work checks. Commit uncertainty or
unresolved writer settlement retains them; an unlocked absent pointer proves nothing.
Hydrated alerts use the event's exact stored path; legacy eventless alert files keep
their existing fallback. Startup legacy recovery excludes completion-owned events
before loading the full event or fetching provider media.

Zone mapping has one pure mapper and an in-session participant. The completion
receipt and rows share commit; existing `lpr_zone_shadow.observed` payloads publish
afterward. Delivery failure never reopens succeeded work or inserts rows again.
The standalone suppression owner drains its own session close. These new completion
and publication lifetimes stay inactive until the atomic core/frontend-consumer
cutover; importing them grants no alternate admission or hardware authority.

Publication also owns one actual transport child. Its send timer exits before
single cancellation and drain, so renewal loss followed by the send deadline
cannot interrupt an already-running cleanup. The lane remains occupied until
that child settles; no ACK or retry is created after ownership loss. The actual
transport invocation checks the conservative original lease/start deadline too.


Required confirmation rejection audit stays with the
[confirmation owner](../backend/app/services/action_confirmations.py). A participant
(`commit=False`) carries frozen scalar evidence on its typed rejection and leaves
caller state untouched. The notification-send and grouped-alert transaction owners
roll back before awaiting a fresh audit transaction, so the audit actor foreign-key
check cannot wait on their own retained Admin lock. Missing-token 428 carries no audit.
Standalone consumption (`commit=True`) owns its same-session rejection audit and commit.
Audit failure remains visible; no detached task substitutes for required evidence.
The fresh audit's active SQL is cancellable while its actual session close is owned
through completion.


The dormant [HA presence repository](../backend/app/services/ha_presence_attempts.py)
separates latest desired state from immutable physical attempts and retained UNKNOWN
barriers. Its last-flush participant uses the auth owner's nonblocking root fence,
locked scalar Person bindings and only the two relevant HA settings. Profile photos
and unrelated settings are excluded. The setting fence uses the existing settings
writer's `iacs:setting:{key}` advisory namespace, including when the setting row has
not yet been created. Pure binding policy preserves ordered entity
IDs and the existing turn_off defaults for both entry and exit; it uses final Presence,
not a stale incoming direction. The vendor contract owns the single connection
fingerprint and action vocabulary.

New attempts require current authority, maintenance clearance, original receipt
budget and exact full physical identity beside its bounded hash. A later desired
state cannot discard an earlier owned delivery receipt or clear a replacement barrier.
A terminal receipt retains the global capacity token through actual operation,
session and dedicated-client cleanup. Renewal then preserves capacity only; an
explicit exact-token release follows settlement. The final release transaction owns
its actual close, although its commit necessarily exposes capacity before that final
connection cleanup returns. UNKNOWN barriers survive idle capacity release.

These repository ports are not started or called by the production access owners.
The existing person-presence sender remains the active authority until the reviewed
cutover. The dormant [single-slot worker](../backend/app/services/ha_presence_worker.py)
starts renewal only after confirmed attempt commit, before actual claim-session close.
Its fixed send deadline includes that close. Delivery settles after the I/O timer
exits; renewal loss cancels the actual receipt once and cannot terminate the service.
The vendor call uses a dedicated existing HA client with the exact frozen connection
for both POST and optional observation. Accepted POST truth survives observation failure.

The dormant [settlement owner](../backend/app/services/ha_presence_resolutions.py)
returns sanitized exact-attempt previews and performs confirmed, audited no-send
acknowledgement. It preserves the original UNKNOWN attempt and only releases its
matching barrier. Matching committed retries return the immutable receipt before
new-resolution eligibility checks, so a later generation or active attempt cannot
invalidate that receipt. New acknowledgement rejects live durable capacity or actual
local cleanup. Retired origin Persons do not prevent settlement. Root-first current
Admin authority and typed rollback-before-rejection-audit are explicit owner ports.
Coherent auth-root ciphertext writers, every proposal/invalidation caller and
integrated validation are required before activation; importing the inert root/auth
ports alone proves no rotation freshness for the running application.


### UniFi stream runtime

The [UniFi runtime](../backend/app/services/unifi_protect_runtime.py) owns bounded
optional callback/probe processing and runtime counters. Existing timing and
visual/presence extractors still own observation interpretation; concrete scalar
[contracts](../backend/app/services/unifi_protect_contracts.py) preserve each
extractor's camera/event precedence without retaining SDK objects. Cached stream
observations reference only a tiny generation cell, so invalidated evidence cannot
retain old clients or runtimes. Contracts import no integration, recorder or bus.

[Operation scopes](../backend/app/services/unifi_protect_operations.py) own actual
operation admission/completion, independently of the calling request's remaining
lifetime. The service detaches under its lock and drains outside it, allowing
reentrant status/read observers. Application shutdown must stop UniFi before
stopping event delivery/diagnostics or disposing the database.


## Compact chart and forecast reads

[Chart reads](../backend/app/services/leaderboard_reads.py) share one typed known
leader aggregate with the durable evaluator; winner policy, claims and notification
intents belong to [LeaderboardService](../backend/app/services/leaderboard.py) and
its [dirty store](../backend/app/services/leaderboard_dirty.py). Participant writes
join the access/directory mutation; the inertly constructed worker starts through
application lifecycle composition. It waits for two quiet seconds, capped at ten
seconds from the first mark, and runs one calculation plus one trailing generation.
A valid claimed result commits leader progress and its deterministic notification
intent atomically, acknowledges only that claim, and preserves later dirty work.
Continuous arrivals cannot discard every result. Silent historical/directory marks
create no notification authority; a live claim keeps its original five-minute
origin/deadline even when later live or silent marks arrive. Dispatch checks that
deadline again after blocking authorization/configuration work. Legacy unattempted
chart notices without origin are retired; confirmed manual sends and attempted
receipt recovery retain their existing authority. Ranking still aggregates full history. Only
selected scalar person/vehicle fields and small media references leave SQL; data
URL bodies are not transferred. Unknown plates rank first, then a lateral latest
snapshot lookup examines only those selected plates. A path with unknown byte size
remains eligible; an unavailable latest file does not fall back to older media.
Exact snapshot timestamp ties now use event ID descending as the final tie.

[Expected presence](../backend/app/services/expected_presence.py) keeps its existing
12-week local-midnight horizon and predictor. Its source query projects only person
ID/name, event time and direction. Recorded person identity wins over current
vehicle ownership; inactive recorded identities do not fall back. Only an absent
recorded identity permits exactly one distinct active owner/assignee, deduplicating
the same person represented both ways. The full public forecast remains available;
Dashboard uses the bounded six-name presentation with the same global count and
learning metadata. Prediction memory still grows with required population/event
density. These changes add no prediction cache, count store or persisted ranks.


### Access and workflow extension exercises

The known-vehicle refresh exercise in the existing
[transactional action suite](../scripts/validation/test_notification_activation.py)
adds only a test definition and executor binding. The real dispatcher passes its
checked pending-operation identity to a transaction participant that locks current
Vehicle/registration and calls the existing vehicle-information aggregate owner.
A small disposable fixture table represents a hypothetical automation-origin
reservation; completion receipt, domain work and mandatory audit commit or roll
back together. It is not a runtime queue. A real manual-origin feature would need
an explicit domain reservation/schema/API with its own original authority;
current arrival jobs require an actual AccessEvent and cannot be repurposed by
inventing one. Provider lookup, worker deadlines and registration checks remain
with their existing feature owners. No dispatcher action-string branch is added.

The [dated access-schedule exercise](../scripts/validation/test_schedule_operations.py)
injects a test-only JSON date-key normalizer at schedule_operations and evaluator
at schedules. It covers legacy/default round-trip, actual persistence/audit,
vehicle then unambiguous owner then default selection, London's repeated DST hour
and a separately committed edit denying actual final recognition authority despite
stale preloaded state. A real dated rule would extend these owners and corresponding
request/read contracts; this fixture ships no new rule format or calendar behavior.
