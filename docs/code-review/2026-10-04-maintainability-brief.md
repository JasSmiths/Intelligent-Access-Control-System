> Superseded by the approved [feature reset](../releases/core-reset.md): Alfred, Discord and WhatsApp are retired rather than retained. This brief is preserved as prior review context.

# IACS maintainability brief

Snapshot date: 2026-10-04. Counts are first-party source in this working tree
(`*.py`, `*.ts`, `*.tsx`, `*.css`, `*.md`), excluding `node_modules`, `dist`,
`data/`, `prototypes/`, and `__pycache__`.

This file is an implementation brief. It is not a second architecture guide.
When a phase lands, update the owning guide in `docs/agent/` and delete the
finished section here. When every phase is done or rejected, delete this file.

The accepted shape of the system stays the modular monolith in
[architecture.md](../architecture.md): FastAPI, SQLAlchemy, PostgreSQL, Redis,
React, Vite, Docker Compose. One gate actuator. One presence model. Vendor I/O
stays in `backend/app/modules/`. Business rules stay in `backend/app/services/`.
API routes and Alfred tools are adapters over those services.

## Plain-English summary

The access-control core is in good shape and is not why the tree feels huge.
Opening a gate is a few hundred lines. Deciding a plate read is a small package
with a clear stage order. The bulk of the code is everything that grew around
that loop.

About 199,000 lines of first-party source are in the repo. Roughly 86,000 of
those are the backend application, 38,000 are backend tests, 51,000 are the
React app (17,000 of that is CSS), and 21,000 are the validation harness.
Alfred alone is about 24,000 lines of runtime and UI, plus about 5,000 lines of
tests, and it exposes 75 tools. That is larger than the plate-read, movement,
and gate/garage code put together. A household ops assistant became a second
admin console, with its own SQL, its own planner, a second language model pass
that checks the first one's answer, a lesson/training system, and vector
memory.

Two other piles are the same size as the core. Notifications and automations
are two workflow engines that share a variable catalog and a circular import
with the gate path. WhatsApp and Discord are two inbound chat stacks beside
the console chat. Investigations is a second way to read the audit log, with
its own outcome vocabulary and a natural-language interpreter.

The way back is deletion and thinning, in small pull requests, leaving the gate
path alone until a later structural pass. Remove products that do not change
who gets in. Shrink Alfred to one tool-calling loop and about fifteen tools
whose handlers call existing services. After the dead surface is gone, split
the remaining giant files and break the notification/gate import cycle. Do not
rewrite the access pipeline, and do not introduce a plugin framework, a new
event bus, or microservices.

A few deletions need a yes or no first, because they may hold real household
data: iCloud calendar passes, the WhatsApp visitor inbox, Discord, the live LPR
zone filter, and whether automations are used separately from notification
rules. Investigations was just rebuilt; keep the activity page and remove the
question interpreter unless you want that page gone too.

## How to use this brief

Work in the phase order below. One phase is one pull request. A later phase
starts only after the earlier phase's checks pass.

For every deletion:

1. Search callers, registries, nav keys, realtime event names, settings keys,
   Compose, tests, and docs. Delete the implementation and its public surface
   together. Leave no alias, placeholder route, or setting shim.
2. Add an Alembic migration that drops tables and deletes obsolete
   `system_settings` rows. Do not apply it to the running site in the same
   task. Production migration and rollout stay a separate, explicit request.
3. Leave these paths behaviorally unchanged unless the phase names them:
   `services/gate_commands.py`, `services/access_devices.py`,
   `services/movement/admission.py`, `services/access/decision.py`,
   `services/access/authorization.py`, `services/notification_runs.py`,
   `services/notification_dispatch.py`.
4. Validate with `python3 scripts/phase1/validate.py --reuse-dependencies`
   (or `--allow-downloads` when the lockfile requires it). Do not use
   `scripts/backend-pytest` for this; it can select the running Compose
   backend. Frontend-only edits also run `npm run build` and `npm test` in
   `frontend/`.
5. If a table you are about to drop has rows, stop and ask. Empty tables can
   go. Occupied tables mean someone may still depend on that feature.

## Where the lines are

| Area | Lines | Read this as |
| --- | ---: | --- |
| `backend/app` | 86,068 | Application |
| `backend/tests` | 37,869 | Tests that lock the current shape |
| `backend/alembic` | 1,522 | Schema history |
| `frontend/src` | 50,789 | Console, including CSS |
| `frontend/src/styles` | 17,194 | Global CSS, a third of the frontend |
| `scripts` | 21,512 | Harness, including ~4,500 lines of schema fixtures |
| `docs` | 1,246 | Guides |
| **First-party total** | **~199,000** | The "over 200k" tree, without data or prototypes |

`backend/app` by package:

| Package | Lines | What it actually is |
| --- | ---: | --- |
| `services/` | 53,865 | Almost all business logic, much of it optional |
| `ai/` | 11,275 | Alfred tool catalog and handlers |
| `api/` | 11,184 | HTTP adapters, several of which grew their own logic |
| `modules/` | 5,242 | Vendor I/O. This boundary is healthy |
| `models/` | 1,828 | 54 model classes, almost all in `models/core.py` |
| `simulation/` | 1,637 | Harness scenarios. The HTTP full-flow endpoint already returns 410 |
| `core/`, `db/` | ~500 | Small, leave them |

The protected loop is the small part:

| Owner | Lines |
| --- | ---: |
| `services/access/` + `services/access_events.py` | 5,020 |
| Movement (`movement/`, FSM, ledger, reconciliation) | 2,565 |
| `services/gate_commands.py` | 318 |
| `services/movement_fsm.py` | 304 |
| Gate and garage services plus their modules | ~4,100 including device providers |

`gate_commands.py` and `movement_fsm.py` are the right size. The maintainability
problem is the code that every new idea attached to this loop.

Largest single files, and why they are large:

| File | Lines | Problem |
| --- | ---: | --- |
| `backend/tests/test_chat_agent.py` | 3,806 | Larger than the module it locks |
| `frontend/src/styles/data-views.css` | 3,626 | Shared visual layer for every table |
| `backend/tests/test_whatsapp_messaging.py` | 2,906 | Test mass for one optional transport |
| `backend/app/services/chat.py` | 2,877 | One class, 94 methods, the whole Alfred turn |
| `frontend/src/styles/passes-schedules.css` | 2,808 | Page CSS detached from components |
| `backend/tests/test_access_events.py` | 2,597 | Core tests; keep, do not "clean up" in a drive-by |
| `frontend/src/views/DirectoryViews.tsx` | 2,350 | People, groups, and vehicles in one module |
| `backend/app/services/notifications.py` | 2,207 | 98 functions, templates plus delivery plus HA copy |
| `frontend/src/styles/workflows.css` | 2,179 | Two editors' styles in one sheet |
| `backend/app/ai/tool_groups/access_diagnostics_handlers.py` | 2,163 | Alfred re-queries access data with its own SQL |
| `frontend/src/views/PassesView.tsx` | 2,048 | Passes plus calendar-sync presentation |
| `frontend/src/views/ChatWidgetView.tsx` | 2,006 | Transcript, confirmation, feedback, attachments |
| `frontend/src/views/SettingsViews.tsx` | 1,978 | Zones, devices, generic settings, and users |
| `backend/tests/test_notification_workflows.py` | 1,928 | Workflow engine tests |
| `backend/app/services/actionable_notifications.py` | 1,890 | Buttons that reach back into gate commands |
| `backend/app/ai/tool_groups/access_incident_handlers.py` | 1,797 | A second incident investigator inside Alfred |
| `backend/app/services/alfred/feedback.py` | 1,708 | Lessons, reflections, eval examples |
| `backend/app/services/gate_malfunctions.py` | 1,671 | Real ops feature; keep, stop growing Alfred around it |
| `backend/app/models/core.py` | 1,624 | Every table in one module |
| `backend/app/services/automations.py` | 1,608 | Second workflow engine |

Empty packages that advertise a structure the app does not use:
`backend/app/schemas/`, `backend/app/scripts/`, `backend/app/workers/`. Live
workers start from the FastAPI lifespan. Delete the empty packages in any
phase that is already touching packaging. The plugin registry in
`modules/registry.py` returns one gate controller under two names
(`configured` and `access_device`). Leave it as that map. A feature flag or a
new registry will make removal harder.

## What is already modular

Keep these patterns. New work should look like them.

- Access stages are real owners: reads, evidence, decision, authorization,
  execution, delivery, hardware, enrichment, payloads, historical. The backend
  guide names them. Enrichment is the stage that has been misused as a hook
  point; the stage split itself is sound.
- Hardware has one command owner per device family. Alfred is required to use
  those owners. That rule is correct and is already tested.
- `services/notification_rules.py` (166 lines) and
  `services/notification_runs.py` / `notification_dispatch.py` are the right
  shape: rules, claim, dispatch. `notifications.py` grew the rendering and
  provider copy that should have stayed beside them, not inside a new engine.
- `modules/` is vendor protocol code. ESPHome, UniFi, DVLA, and HA clients
  belong there.
- Alfred's stdlib contracts (`ai/tools.py`, `ai/tool_inputs.py`,
  `ai/context.py`) and the single catalog builder
  `ai/tool_groups/registry.py` are a good seam. The handlers behind the seam
  are the problem.
- Frontend routes already lazy-load views from `app/routes.tsx`, and nav is
  data in `app/navigation.tsx`. Adding or removing a screen is a localized
  edit once the view file is a single feature.

## Findings, in the order they cost the most

### 1. Alfred is a second product, and the runtime never left `chat.py`

Alfred V3 is an LLM planner over 75 tools, then a ReAct loop, then a second
structured completion that drafts an answer from "artifacts", then a verifier,
then a background reflection that writes lessons, then a vector recall of
those lessons on the next turn. `services/alfred/executor.py` is 31 lines.
`services/alfred/runtime.py` is 64 lines of provider-capability flags.
`services/chat.py` is the runtime: session IO, planning, plan repair, tool
execution, confirmation, answer drafting, audit, memory, and provider options.

The planner prompt in `services/alfred/planner.py` is a growing rulebook
("oil delivery", "Dove Fuels", absence versus visit duration, device state
versus malfunction). Each new household question has been encoded as prompt
text, an answer-type enum, and a repair function in `chat.py`
(`_repair_missing_planned_answer_type`, `_duration_repair_arguments`). That is
why the file grows every time Alfred gets a new trick.

Tool handlers are not adapters. They open `AsyncSessionLocal` and run their
own queries:

| Handler module | Lines | `AsyncSessionLocal` uses | `select(` uses |
| --- | ---: | ---: | ---: |
| `access_diagnostics_handlers.py` | 2,164 | 8 | 17 |
| `access_incident_handlers.py` | 1,798 | 4 | 15 |
| `schedules_handlers.py` | 883 | 12 | 10 |
| `visitor_passes_handlers.py` | 612 | 6 | 0 |
| `gate_maintenance_handlers.py` | 518 | 0 | 0 |
| `notifications_handlers.py` | 353 | 8 | 2 |
| `general_handlers.py` | 285 | 4 | 5 |

`gate_maintenance_handlers.py` is the model to copy: it calls the device
service. The diagnostics and incident handlers are a private query API that
will drift from the console every time a column changes.

`feedback.py` (1,708 lines, 66 functions) stores lessons, feedback, and eval
examples, calls a model to reflect on turns, and seeds standing instructions
that the planner is told to "apply by semantic analogy". The Settings page
`AlfredTrainingView.tsx` (491 lines) is the admin UI for that loop. Session
history already records what was said. The training loop is a second memory
with its own failure modes (timeouts, stale analysis, reflection tasks).

The 75 tools include full CRUD for schedules, notification workflows, and
automations, plus `update_system_settings`, `rotate_auth_secret`,
`test_integration_connection`, `test_unifi_alarm_webhook`,
`backfill_access_event_from_protect`, `generate_contractor_invoice_pdf`,
`export_presence_report_csv`, `query_leaderboard`, and `trigger_icloud_sync`.
Those are console jobs. Every one of them is another schema, another
confirmation path, and another block in `test_chat_agent.py`.

Duplicate tools that already overlap:

- `open_gate`, `open_device`, and `command_device`
- `toggle_maintenance_mode`, `enable_maintenance_mode`, and
  `disable_maintenance_mode`
- `query_anomalies` and `query_alert_activity`
- `calculate_visit_duration` and `calculate_absence_duration`
- `diagnose_access_event` and `investigate_access_incident`

Desired Alfred, after the shrink:

- One completion per step, with the provider's native tool calls, at most
  four steps.
- Mutations return `requires_confirmation` and stop the loop. The existing
  approval owner (`services/alfred/approvals.py`, 258 lines) stays.
- Handlers are thin: validate actor and arguments, call one service method,
  return `output` plus an outcome of `succeeded`, `failed`,
  `requires_confirmation`, or `requires_details`.
- Session transcript in `ChatMessage` is the only memory.
- The local provider still cannot run a turn. That check in `runtime.py` stays.
- Hardware tools still call `GateCommandCoordinator` and `AccessDeviceService`.

The replacement turn lives in `services/alfred/turn.py`. `ChatService` keeps
session create, message append, confirmation resume, and the public methods
`handle_message` and `handle_tool_confirmation`. It calls `run_turn`. Delete
the planner prompt, the answer-type enum, artifact drafting, the verifier,
lesson recall, semantic search, and reflection scheduling in the same change
that deletes their tests. Porting `test_chat_agent.py` forward would freeze
the complexity. Replace it with a short contract file that covers:

- a read-only question executes one tool and returns the tool's text
- an open request stores an approval and does not call the gate owner
- confirming that approval calls the gate owner once
- the local provider returns the existing not-agent-capable error
- an unknown plate is never an actuation argument the tool will accept
- a non-admin actor does not receive admin tools

Keep `tests/test_alfred_architecture.py`. It already guards the stdlib
contracts and the single registry. Extend it with one assertion: handler
modules may import services and `app.ai`, and may not import
`sqlalchemy.select` or `AsyncSessionLocal`.

Tool catalog to keep (names can stay so the approval table still matches):

| Tool | Calls |
| --- | --- |
| `query_presence` | movement presence read |
| `query_access_events` | existing access-event query used by the events API, limited filters: person or plate, direction, day |
| `explain_access_decision` | new thin wrapper, one function, over the stored event, suppression reason, and gate command receipt. Replaces `diagnose_access_event` and `investigate_access_incident` |
| `query_device_states` | access-device read |
| `get_active_malfunctions` | `gate_malfunctions` read |
| `open_device` | existing confirmed command path, `kind` of `gate` or `garage_door` |
| `enable_maintenance_mode` / `disable_maintenance_mode` | existing maintenance service, confirmed |
| `query_visitor_passes` / `get_visitor_pass` | `VisitorPassService` |
| `create_visitor_pass` / `cancel_visitor_pass` | `VisitorPassService`, confirmed |
| `verify_schedule_access` | schedule evaluation read |
| `query_alert_activity` | anomaly/alert read used by the alerts API |
| `query_presence_duration` | one movement-session query for "how long on site" and "how long away". Replaces the two duration tools and the planner repair |

That is fifteen tools. Everything else in `build_agent_tools()` goes, including
the entire `automations`, `notifications`, `system_operations`, and
`compliance_cameras_files` groups, schedule CRUD, UniFi backfill, leaderboard,
iCloud, telemetry traces, and manual malfunction override.

Delete with the catalog:

- `services/alfred/planner.py` answer-type machinery. If a one-call planner is
  still useful to avoid sending fifteen tool schemas, reduce it to "pick a
  subset of tool names". Delete `requested_answer_type`, planned-call repair,
  and domain essays in the prompt.
- `services/alfred/answer_contracts.py` and every `_compose_verified_*` /
  `_draft_artifact_*` method on `ChatService`.
- `services/alfred/feedback.py`, models `AlfredLesson`, `AlfredFeedback`,
  `AlfredEvalExample`, routes and UI for training, `ChatFeedbackPanel` in
  `ChatWidgetView.tsx`.
- `services/alfred/memory.py` vector recall and `services/alfred/embeddings.py`.
  Drop `AlfredMemory` if no UI lists memories the household uses. Session
  messages stay.
- Frontend phases that only exist to narrate planner stages. Keep the
  confirmation card and the transcript.

`ChatWidgetView.tsx` should end as transcript, composer, confirmation card,
and attachment display. Feedback, training entry points, and provider-debug
copy move out with the features they belong to.

### 2. Optional work runs inside the plate-read path

`AccessEnrichment.run` is supposed to be optional work after a committed
decision. It currently always calls:

- DVLA lookup and compliance notifications
- vehicle visual detection, then writes `raw_payload`
- camera snapshot capture
- LPR zone-shadow recording
- Home Assistant input-boolean presence sync
- leaderboard overtake evaluation, which can send a notification
- realtime publication

`access_events.py` also calls the zone-shadow service when the live filter
suppresses a read (`_suppress_by_live_lpr_zone_filter`, around the
`lpr_zone_filter_mode` setting). So zone logic is both a live suppress decision
and a second observation log written from enrichment.

This is the modularity failure that matters. A leaderboard or a shadow log
should be deletable by removing one module. Today each is an import in the
ingest path, so every access change risks those side effects, and every side
effect risks the worker.

Fix, as its own small change before the bigger deletions:

- Keep realtime publication and snapshot capture in enrichment. They feed the
  console.
- Move the live zone suppress decision fully into `access_events.py` (it is
  already there) and stop writing a shadow row from enrichment.
- Remove the leaderboard call from enrichment in the same change that deletes
  Top Charts.
- Keep DVLA and visual detection behind the existing "fail independently"
  boundary until a later phase decides whether those products stay. They must
  not gain new writes on the access, movement, or gate tables beyond the
  enrichment columns they already own.

`vehicle_visual_detections.py` (1,114 lines) is also referenced from
`access/reads.py`, `access/execution.py`, and `access/payloads.py`. That is
too deep for an optional camera attribute. After the product decision below,
either delete it or confine it to enrichment plus a single payload key. It
must not sit in execution.

### 3. Two workflow engines, one import cycle with the gate

Notification rules and automation rules are both stored workflows:

- shared tokens in `services/workflows/catalog.py` (person, vehicle, event,
  visitor, integration, maintenance)
- notification rules: `notification_rules.py`, `notifications.py` (2,207),
  dispatch and runs
- automations: `automations.py` (1,608) with its own cron parser, webhook
  HMAC, nonce store, rate limit, and `automation_execution.py` (434)
- actionable notifications (1,890) that import gate commands so a phone button
  can open a gate
- eight Alfred tools for each engine

The architecture baseline at `scripts/architecture/baseline.json` already
allows this cycle:

`access_devices` → `home_assistant` → `actionable_notifications` →
`gate_commands` → `modules.registry` → `modules.gate.access_devices` →
`access_devices`, plus `notifications` ↔ `actionable_notifications`,
`home_assistant` → `notifications`, and `maintenance` → `notifications`.

A change to notification copy can import-cycle into the gate owner. The
baseline says cycle edges may only decrease. That is the right ratchet. Use
it. Do not merge the two engines in one pull request.

Until you confirm which rows exist, treat both engines as live. The shrink
that is safe now is deleting their Alfred CRUD (phase 3). The engine decision
is phase 6.

The structural fix, when you get to it: move the shared dataclasses those
modules import from each other (`NotificationContext`, command request values,
maintenance predicates) into a leaf module that imports neither services nor
HA. Services import the leaf. Behavior of command order, confirmation, and
recovery stays. Run `python3 scripts/architecture/check_boundaries.py` and
shrink `python_cycle_edges`. Frontend cycles are already empty; keep them
empty.

### 4. A second inbox, and a third

Console chat is Alfred. `services/messaging/` (about 3,970 lines) is another
inbound router: WhatsApp webhook, identity, visitor sandbox, admin routing
back into Alfred, delivery and replies. Discord adds
`discord_incoming.py` (716), `discord_messaging.py` (840), and transport
modules. Tests for the two transports are about 4,700 lines
(`test_whatsapp_messaging.py` is 2,906 by itself).

Visitor texting a pass phrase is a real access feature if you use it. Discord
as a second admin inbox is the same Alfred conversation with another
transport, which means every Alfred change has two more delivery paths.

Recommendation, pending the decision list: keep one outbound notification
transport that the household actually receives (Home Assistant mobile is
already a notification module). Keep WhatsApp only if visitors use it. Remove
Discord inbound and the Alfred-over-WhatsApp admin route if the console is how
you talk to Alfred. Visitor WhatsApp can stay without being a second admin
client: the visitor sandbox already has a narrower tool set. Do not let that
sandbox import the full 75-tool catalog.

### 5. Investigations is a second read model

`services/investigations/` is 2,935 lines: contracts, a 382-line natural
language interpreter, a repository that scans up to 5,000 rows, an outcome
taxonomy (`outcomes.py`, 494), and a presenter (752). The frontend feature is
about 1,900 lines plus `investigations.css` (1,205). `LogsView.tsx` is a
31-line gate in front of `InvestigationsWorkspace`.

Events, movements, alerts, and audit logs already exist. Investigations
re-derives episodes and outcome words on top of them. The page was recently
rebuilt, so this brief does not delete it in the first waves. The YAGNI cut
inside it is the question interpreter and `QuestionComposer.tsx`: filters
already express time range and outcome. A later pass can make the timeline
read stored access decisions and command receipts directly, and delete the
parallel outcome vocabulary once those screens agree.

### 6. The console is a few giant view files and a global CSS layer

Routing is fine. The files behind the routes are not.

- `DirectoryViews.tsx` (2,350) exports people, groups, vehicles, HA person
  matching, DVLA badges, and directory accordions.
- `SettingsViews.tsx` (1,978) exports zones, dynamic settings, access devices,
  and users.
- `PassesView.tsx` (2,048) knows about iCloud as a creation source.
- `providerPanels.tsx` (1,743) is every integration form.
- `data-views.css` (3,626) and `passes-schedules.css` (2,808) are where layout
  changes become global.

`App.tsx` (610) holds shell data for presence, people, vehicles, schedules,
and anomalies, and `navigation.tsx` subscribes many routes to that data. That
shell is acceptable while the views stay small. It becomes a problem because
each view file is several pages.

Split a view only when a phase already deletes part of it, or when the split
is a pure move of an existing exported function into its own file with no
behavior change. Do not restyle, and do not rewrite `data-views.css` as a
project of its own. Delete CSS when its feature goes (`chat` training pieces,
Top Charts, iCloud blocks inside passes, a workflow editor if phase 6 removes
one).

`api/v1/directory.py` (1,378), `api/v1/telemetry.py` (1,276),
`api/v1/integrations.py` (1,019), and `api/v1/search.py` (984) are the HTTP
twins of the fat views. Telemetry's API is larger than `services/telemetry.py`
(726). Search is the command palette backend and should shrink after the
deleted screens drop out of the index. Directory should stay the people and
vehicle API; move any HA-suggestion and DVLA presentation helpers that are
pure functions next to the view that uses them, so the route module returns
data.

### 7. Tests and fixtures copy the surface area

Large tests are a symptom, not a separate failure. `test_chat_agent.py` and
`test_whatsapp_messaging.py` will disappear with the features. Do not spend a
phase "reducing tests" in place.

`scripts/phase1/fixtures/schema/**/models/core.py` (about 3,000 lines across
snapshots) is intentional harness state for migration checks. Leave those
copies. They are not a second runtime.

`simulation/scenarios.py` (1,531) stays with the harness.

### 8. `models/core.py` is wide because the product list is wide

54 model classes in one module is uncomfortable and is still cheaper than a
premature split. After phases 1–3, delete the dead classes in the migration
that drops their tables (`LeaderboardState`, iCloud account and sync run,
`LprZoneShadowObservation`, Alfred lesson/feedback/eval, and messaging tables
if those products go). Split the file by domain only when you are already
editing it for that deletion: access and movement, notifications and
automations, chat and approvals, integrations. A standalone "split the models"
pull request will churn every import for no behavior change.

## YAGNI: what to remove, and the simpler replacement

"Simpler" here means fewer concepts, not a new framework.

| Product | Production lines, approximate | Simpler replacement | When |
| --- | ---: | --- | --- |
| Alfred planner, answer contracts, lessons, training UI, vector memory | ~8,000 across `chat.py`, `alfred/`, training view, chat tests | One tool loop and fifteen tools | Phase 3 |
| Alfred admin CRUD (schedules, workflows, settings, secret rotation, invoices, CSV, UniFi test, backfill) | most of `ai/tool_groups` outside the keep list | The existing Settings and directory screens | Phase 3 |
| Top Charts / leaderboard, including the overtake notification fired from enrichment | ~1,000 plus tests | Nothing. Presence and events already show who came through | Phase 2 |
| Contractor invoice PDF and presence CSV export tools | inside `compliance_cameras_files*` and `reports.py` pieces | Reports screen if you still want a PDF of access; otherwise delete the invoice path entirely | Phase 2 for the tool; reports decision in phase 6 |
| iCloud calendar → visitor passes | ~1,450 plus pass UI branches | Create the pass in the Passes screen | Phase 2, after the decision check |
| LPR zone shadow log | 364 plus a model, settings mode, two write sites | Keep the live suppress check in `access_events.py` if `lpr_zone_filter_mode` is `live`. Delete shadow rows, the shadow service, and the mode toggle | Phase 2, after the decision check |
| Discord inbound and outbound | ~1,500 service/modules plus ~1,600 tests | The notification transport you already use | Phase 5, after the decision check |
| WhatsApp admin route into full Alfred | part of `whatsapp_router.py` and visitor/admin split | Console Alfred. Keep the visitor sandbox only if visitors text in | Phase 5 |
| Expected-presence summary | 458, plus a shell data key on the dashboard | Presence rows the dashboard already loads | Phase 4 if the dashboard widget is unused |
| Vehicle visual detection | 1,114, hooked into reads, execution, payloads, enrichment | Plate identity. If you still want a snapshot label, store it only from enrichment | Phase 4 |
| Investigations question interpreter | 382 plus `QuestionComposer.tsx` | The filter bar on the same page | Phase 4 |
| Gate malfunction Alfred diagnostics beyond `get_active_malfunctions` | part of the two large handler files | `gate_malfunctions.py` and its settings/API, which stay | Phase 3 |
| Telemetry trace browser as an Alfred/admin product | service 726 + API 1,276 | Audit log and application logs. Keep in-process trace spans only where recovery code reads them; delete the HTTP browser and the Alfred tool if nothing in the console calls them | Phase 4, and only after a caller search shows the UI is the only consumer |
| Empty `schemas`, `scripts`, `workers` packages | trivial | Delete the directories | Any early phase |
| Dependency updater | already removed from runtime | Leave the retirement note and the historical Alembic revision. Do not delete `data/backend/dependency-update-backups/` as part of a code change | Done |

Keep, and do not fold into a cleanup:

- Plate ingest, debounce, suppression, identity, schedules, visitor passes
- Movement ledger, admission, presence
- Gate and garage command owners, command history, maintenance mode
- Notification runs and dispatch, including recovery rules in the backend guide
- Actionable notification buttons that already confirm a gate or recovery
  action, until phase 6. They are part of the safety path
- Missed-exit recovery
- Auth, users, directory, events, movements, alerts, dashboard
- UniFi as the LPR camera integration, and ESPHome or the configured gate
  provider as the actuator
- The isolated harness

`gate_malfunctions.py` (1,671) stays. It is operational state for a stuck gate,
and `access_events.py` already cooperates with it. The simplification is to
stop cloning that logic into Alfred handlers.

Reports (`reports.py` 1,048 and `ReportsView.tsx` 1,230) stay until phase 6
unless you already know you never open them. They are a read-only export, not
on the ingest path. The invoice tool is the piece that goes immediately.

## Decisions to make before an agent deletes data

Default recommendation if you want the agent to proceed without another
design discussion. The agent still stops when the matching table has rows.

| Question | Default if you say "proceed" | Stop if |
| --- | --- | --- |
| Do calendar events still create passes? | Delete iCloud sync, accounts, and the pass `creation_source` branch | `icloud_calendar_accounts` or passes with source `icloud_calendar` have rows you still want |
| Do visitors text the gate on WhatsApp? | Keep the visitor sandbox, delete the admin-to-Alfred WhatsApp route | You still operate Alfred from WhatsApp |
| Does anything alert or chat on Discord? | Delete Discord inbound and outbound | A notification rule or automation still targets Discord and you want it |
| Is LPR zone filter mode `live` in settings? | Keep the live suppress in `access_events.py`; delete shadow recording and the shadow/live switch | You still compare shadow decisions against live decisions |
| Are automation rules used for something notification rules cannot do? | Leave both engines installed; delete only their Alfred CRUD | You want one engine removed. That is phase 6, and it needs a row-by-row look |
| Do you still want the Investigations page? | Keep the page; delete the question interpreter | You want the whole activity product removed |
| Do you use Top Charts or the "new number one plate" notification? | Delete both | You want that notification. Then keep the service and delete only the page, which saves little |

## Implementation phases

Each phase lists the work, the files that own it, and the finish line. Line
numbers will drift; search by symbol.

### Phase 1. Unhook toys from ingest

Goal: a plate read's enrichment no longer imports the leaderboard, and no
longer writes a zone-shadow row. Live suppression behavior stays.

- In `services/access/enrichment.py`, remove the `leaderboard` stage and the
  `zone_shadow` stage. Remove those imports.
- Leave `_suppress_by_live_lpr_zone_filter` in `access_events.py` in place,
  including its `record_decision` call, until phase 2 replaces that call with
  a direct suppress.
- Do not change decision, authorization, hardware, or admission.

Done when: a granted entry test still commits the event, presence, and gate
command behavior it already asserted, and no test or production import path
from `services/access/` reaches `services.leaderboard`. Harness passes.

### Phase 2. Delete self-contained products

One product per commit, in this order, so a failure is obvious.

**Top Charts.** Delete `services/leaderboard.py`, `api/v1/leaderboard.py`, the
router include, `LeaderboardState`, `TopChartsView.tsx`, nav key `top_charts`,
realtime type `leaderboard_overtake`, tests `test_leaderboard.py`, and the
e2e stub. Migration drops `leaderboard_state`.

**Zone shadow log.** The live predicate
`evaluate_lpr_zone_filter_for_read` currently lives in
`services/lpr_zone_shadow.py`, and the settings default is `shadow` (no
suppress). After confirming the decision above, move that predicate next to
the caller in `access_events.py`, keep the suppress behavior when the setting
is `live`, then delete the shadow service, model `LprZoneShadowObservation`,
the shadow write, the mode toggle, and `test_lpr_zone_shadow.py`. Zones
settings that configure real cameras stay.

**iCloud.** Delete `services/icloud_calendar.py`, `modules/icloud_calendar/`,
`api/v1/icloud_calendar.py`, models `ICloudCalendarAccount` and
`ICloudCalendarSyncRun`, Alfred tool `trigger_icloud_sync`, integration tile
and pass-view branches that special-case `icloud_calendar`, realtime types
`icloud_calendar.accounts_changed` and `icloud_calendar.sync_completed`.
Passes created by hand stay.

**Invoice tool.** Delete `generate_contractor_invoice_pdf` and
`export_presence_report_csv` from the compliance tool group and their handler
functions. Leave `services/reports.py` and `ReportsView.tsx` alone in this
phase.

**Empty packages.** Delete `backend/app/schemas/`, `backend/app/scripts/`,
`backend/app/workers/` if they still contain only an `__init__.py`.

Done when: nav, router, and `rg` show no remaining imports; migrations are
present and unapplied to production; harness passes.

### Phase 3. Replace Alfred with the fifteen-tool loop

This is the large phase. Do it after phase 2 so the catalog shrink is not
also a product deletion.

1. Add `services/alfred/turn.py` with the loop described in finding 1.
   `ChatService.handle_message` becomes: persist the user message, call
   `run_turn`, persist the assistant message, return the existing
   `ChatTurnResult` shape the API and the chat widget already parse.
2. Rebuild `build_agent_tools()` to the keep list. Implement
   `explain_access_decision` and `query_presence_duration` as short functions
   in a new `ai/tool_groups/ops_handlers.py` that call services. No
   `select()` in that file.
3. Delete the handler modules and group files that have no remaining tools.
   Delete planner repair, answer contracts, feedback, embeddings, semantic
   memory, training API routes, `AlfredTrainingView`, and `ChatFeedbackPanel`.
4. Replace `test_chat_agent.py` with the short contract list. Keep approval
   persistence tests that already exist under `scripts/phase1/` and
   `test_alfred_approvals.py`. Update `docs/agent/backend.md` Alfred section
   and `docs/agent/frontend.md` so they describe one loop and no training page.
5. Add the handler import assertion to `test_alfred_architecture.py`.

Done when: the catalog length test, if you add one, expects the keep list; a
confirmation still cannot actuate before approval; harness and frontend tests
pass; `chat.py` is session and confirmation code only. A reasonable finished
size is under 800 lines for `chat.py` and under 400 lines for `turn.py`. If
either file is heading back toward 1,500, the extra behavior does not belong
in the turn.

Leave `gate_maintenance` confirmation semantics exactly as the current
`open_device` path implements them. Read that handler and call it, or call the
same service functions it calls. Do not re-specify gate safety inside the new
loop.

### Phase 4. Thin the next layer, still without merging engines

Only items whose caller search shows they are off the command path.

- Expected presence: remove the service, shell key, and dashboard widget if
  the widget is only a projection of presence you already render.
- Vehicle visual detection: delete it, or move the remaining write into
  `enrichment.py` and remove the imports from `reads.py`, `execution.py`, and
  the payload builder's special case. Pick one of those two; do not leave the
  hooks "for later".
- Investigations: delete `interpreter.py` and `QuestionComposer.tsx`. Keep
  filters, timeline, and audit detail.
- Telemetry HTTP: delete `api/v1/telemetry.py` routes that no remaining view
  calls. Keep service functions that access recovery, gate malfunction, or
  ingest tracing still call. Delete Alfred's `get_telemetry_trace` in phase 3
  even if this phase slips.
- Search palette: remove entries for deleted views.

Split these files by moving existing exports, with no JSX or query changes:

- `DirectoryViews.tsx` into one file per exported view (`PeopleView`,
  `VehiclesView`, `GroupsView`) plus a small `directoryGrouping.ts` for the
  pure helpers already in that file.
- `SettingsViews.tsx` into one file per exported view.

Done when: those view files are each under about 800 lines, imports resolve,
and the screens render the same fields. Browser-check the directory, settings,
dashboard, and investigations routes on a desktop width and a phone width.

### Phase 5. One inbox

After you confirm the messaging decision:

- Delete Discord modules, `discord_messaging.py`, `api/v1/discord.py`,
  Discord tests, and the integration tile.
- If WhatsApp admin routing goes: delete the admin branch in
  `whatsapp_router.py` and any code that feeds a WhatsApp sender into
  `ChatService` with the full tool catalog. Keep visitor intake, the visitor
  sandbox, and outbound replies to that visitor.
- If the whole WhatsApp stack goes: delete `services/messaging/whatsapp_*`,
  `modules/messaging/whatsapp.py`, the API, and tests together. Also delete
  `incoming_messages.py` only when no provider remains.

Done when: an unknown WhatsApp sender is still denied if the visitor stack
remains; a visitor still cannot actuate a gate; harness messaging tests that
remain pass; deleted tests are gone, not skipped.

### Phase 6. One workflow engine, only with a row count in hand

Do this last. It is the easy place to break notification recovery.

Read `notification_rules` and `automation_rules`. For each automation action
type, decide it is one of: already expressible as a notification rule, a
hardware command that must stay, or unused.

- Unused action types: delete the executor branch, the editor controls, the
  catalog entries, and the tests for that type.
- If every remaining automation is unused: delete `automations.py`,
  `automation_execution.py`, the automation API, the settings view, models
  `AutomationRule`, `AutomationRun`, `AutomationWebhookSender`,
  `AutomationWebhookNonce`, and workflow CSS that only the automation editor
  uses. Point hardware side effects at the audited command owners from
  notification actions that already exist, or at missed-exit and maintenance
  flows. Do not invent a third engine.
- If both stay: stop at the Alfred CRUD removal from phase 3, and do the
  import-cycle leaf extraction from finding 3 as the only structural edit.

The cycle extraction is allowed in this phase even if both engines stay.
Finish line: `python_cycle_edges` in `scripts/architecture/baseline.json` is
shorter, and a gate command test plus a notification dispatch test still
describe the same order of commit, provider call, and recovery.

### Phase 7. Models file, only as part of the deletions above

No separate phase of import rewrites. When phase 2 or 3 drops tables, move
the surviving model classes for that domain into `models/access.py`,
`models/workflows.py`, or `models/chat.py`, and re-export them from
`models/__init__.py` so call sites keep working. Delete a re-export in a
follow-up only when `rg` shows it has one import site.

## What an implementer should refuse to add

These are the patterns that created the current size. A review of a future
change should reject them.

- A new Alfred tool that opens its own SQL session. Call a service.
- A new answer type, lesson, reflection prompt, or planner paragraph for one
  household phrasing. If the fifteen tools cannot answer it, add one service
  method and one tool.
- A second transport that routes into `ChatService` with the admin catalog.
- A new package registry, dependency-injection container, or generic event bus.
  `services/event_bus.py` already publishes the realtime events the console
  uses. New domain facts belong on the owning service's return value.
- A shadow mode, compatibility alias, or dual-run flag for a feature you are
  replacing. The zone-shadow service is the example to avoid repeating.
- A new top-level view file that exports more than one page.
- An enrichment stage that imports a feature the access decision does not use.

## Suggested first pull request

Phase 1, then Top Charts from phase 2. Both are small, both are on the
"proceed" side of the decision list if leaderboard rows are disposable, and
both prove the deletion mechanics (router, nav, enrichment import, migration,
tests, harness) before anyone touches Alfred.

Alfred phase 3 is the change that makes the codebase feel smaller. It should
not be the first commit.
