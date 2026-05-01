# Intelligent Access Control System: Agent Guide

This file is the first thing future AI agents should read before changing this
repository. It captures the architecture, current implementation state,
extension rules, API surface, and UI design constraints.

## Project Intent

The system is a modular Intelligent Access Control and Presence System for a
family/private site. It ingests License Plate Recognition events, resolves noisy
reads, tracks presence, detects anomalies, coordinates gate/audio/notification
integrations, and exposes a realtime dashboard plus Alfred, the AI operations
agent.

Primary goals:

- Run the full system with `docker compose`.
- Use host directory bind mounts only. Do not introduce Docker named volumes.
- Keep hardware and third-party I/O modular and swappable.
- Keep dashboards, logs, presence, and chat realtime through WebSockets.
- Make the frontend a usable operational console, not a marketing page.
- Keep real-world actions such as gate/door commands, notification sends, and
  schedule overrides explicit, confirmed, audited, and traceable.

## Current Stack

- Deployment: Docker Compose.
- Backend: Python 3.12, FastAPI, SQLAlchemy async, PostgreSQL, Redis.
- Frontend: React 19, TypeScript, Vite, Nginx.
- Icons: `lucide-react`.
- Rich notification-template editing: Tiptap.
- Frontend interaction helpers: `motion`, TanStack Virtual, Monaco, and
  jsondiffpatch.
- Notifications: Apprise, Home Assistant mobile app notify services, WhatsApp
  Cloud API, in-app realtime notifications, and Home Assistant TTS.
- Home Assistant: REST service calls plus WebSocket state listener.
- DVLA: Vehicle Enquiry Service API lookup integration.
- UniFi Protect: `uiprotect` camera/event/snapshot integration plus managed
  package update overlays.
- LLM providers: local fallback, OpenAI Responses API, Gemini, Claude, Ollama.
- Telemetry: database-backed traces, spans, audit logs, artifacts, and a
  dashboard Telemetry & Audit console.

## Ports and Access

Host defaults:

- Frontend app: `http://localhost:8089`
- Frontend LAN: `http://<host-ip>:8089`
- Backend API: `http://localhost:8088`
- PostgreSQL: `localhost:5432`
- Redis: `localhost:6379`

Do not bind the backend directly to host port `8000`; this site already uses
host `8088` for the backend and `8089` for the frontend to avoid local service
collisions.

Container defaults:

- `frontend` listens on container port `80`.
- `backend` listens on container port `8000`.
- `postgres` listens on `5432`.
- `redis` listens on `6379`.

For Nginx Proxy Manager, point the proxy host at:

```text
http://<docker-host-ip>:8089
```

Enable WebSocket support in NPM. The frontend Nginx container proxies `/api/*`,
`/health`, `/docs`, `/openapi.json`, and WebSocket upgrades to `backend:8000`.
Leave `IACS_ROOT_PATH` blank for normal subdomain-style NPM deployments. Set it
only when the backend is intentionally mounted under a URL subpath.

The SPA shell (`/` and `/index.html`) must be served with `Cache-Control:
no-store`, while hashed `/assets/*` files can be immutable. Keep this split so
frontend rebuilds do not leave browsers with a stale `index.html` pointing at
removed chunks, which presents as the whole page failing to load.

## Docker and Storage Rules

The project intentionally uses bind mounts:

- `./data/backend:/app/data`
- `./data/chat_attachments:/app/data/chat_attachments`
- `./logs/backend:/app/logs`
- `./backend/app:/app/app:ro`
- `./logs/frontend:/var/log/nginx`
- `./data/postgres:/var/lib/postgresql/data`
- `./data/redis:/data`

Do not add Docker named volumes. Keep new persistent runtime data under
`./data/...` or `./logs/...` and mount it explicitly as a bind mount.

## Repository Layout

```text
.
├── docker-compose.yml
├── .env.example
├── README.md
├── AGENTS.md
├── docs/
│   ├── phase-1.md
│   ├── phase-2.md
│   ├── phase-3.md
│   ├── phase-4.md
│   ├── phase-5.md
│   └── phase-6.md
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── app/
│       ├── main.py
│       ├── api/
│       ├── ai/
│       ├── core/
│       ├── db/
│       ├── models/
│       ├── modules/
│       ├── services/
│       ├── schemas/
│       ├── simulation/
│       └── workers/
└── frontend/
    ├── Dockerfile
    ├── nginx.conf
    ├── package.json
    ├── vite.config.ts
    └── src/
        ├── VariableRichTextEditor.tsx
        ├── main.tsx
        └── styles.css
```

Ignore generated runtime directories:

- `data/`
- `logs/`
- `frontend/node_modules/`
- `frontend/dist/`

## Testing

The backend Docker image installs the `backend/pyproject.toml` dev extras, so
`pytest` is available inside the normal Compose backend container. Run backend
tests from the repository bind mount, not `/app`, because `/app` contains the
installed service package while `/workspace/backend` contains the checked-out
tests:

```bash
docker compose exec -T backend sh -lc 'cd /workspace/backend && python -m pytest'
```

For a single file:

```bash
docker compose exec -T backend sh -lc 'cd /workspace/backend && python -m pytest tests/test_dependency_updates.py'
```

## Backend Architecture

Backend entry point:

- `backend/app/main.py`

Important packages:

- `app/core`: settings, logging, app lifecycle.
- `app/api`: HTTP and WebSocket routes.
- `app/db`: SQLAlchemy base/session/bootstrap.
- `app/models`: database models and enums.
- `app/modules`: hardware and third-party adapters.
- `app/services`: core orchestration and business logic.
- `app/ai`: provider wrappers and tool registry.
- `app/simulation`: hardware-free test endpoints.
- `app/workers`: future worker package placeholder; runtime background
  services are currently started from the FastAPI lifespan.

### Hard Modularity Rule

Core services must depend on normalized contracts, not vendor payloads.

Good:

- `AccessEventService` consumes `PlateRead`.
- Gate logic consumes `GateController`.
- Notifications consume `NotificationSender`.
- TTS uses `HomeAssistantTtsAnnouncer`.

Avoid:

- Importing Ubiquiti webhook schemas into event logic.
- Calling Home Assistant HTTP APIs directly from access decision code.
- Putting Apprise URL parsing inside anomaly detection.
- Adding hardware-specific conditionals in core services.

When adding new hardware, create a module under `backend/app/modules/...` and
register it in `backend/app/modules/registry.py` if selection by name is needed.

## Data Model

Current SQLAlchemy models are in `backend/app/models/core.py`.

Main tables:

- `groups`: Family, Friends, Visitors, Contractors, subtype support.
- `people`: driver/person profiles, assigned garage doors, and optional Home
  Assistant mobile app notify service.
- `vehicles`: one person to many vehicles. Stores canonical UI make/model/color
  plus DVLA compliance/cache fields `mot_status`, `tax_status`, `mot_expiry`,
  `tax_expiry`, and `last_dvla_lookup_date`.
- `schedules`: reusable weekly access windows assigned directly to people,
  vehicles, notification conditions, and configured door entities.
- `schedule_overrides`: one-off temporary access allowances, currently created
  by Alfred with confirmation.
- `visitor_passes`: anticipated access windows for unknown visitor vehicles.
  Stores `pass_type` (`one-time` or `duration`), visitor name, normalized
  `visitor_phone` for WhatsApp concierge duration passes, expected time, +/-
  window minutes, lifecycle status (`active`, `scheduled`, `used`, `expired`,
  `cancelled`), creation source (`ui`, `alfred`, future
  Discord/Slack/Calendar, etc.), creating user, arrival/departure event links,
  trace ID, confirmed plate, DVLA/visual vehicle details, calculated duration
  on site, optional explicit `valid_from`/`valid_until` windows for asymmetric
  sources and duration passes, source reference IDs, and source metadata.
- `icloud_calendar_accounts`: multiple connected Apple iCloud Calendar
  accounts. Stores Apple ID/display label, active/status state, encrypted
  trusted session/cookie bundle, auth/sync timestamps, last sync summary/error,
  and creating Admin user. Apple passwords are never stored long-term.
- `icloud_calendar_sync_runs`: durable manual/Alfred-triggered iCloud Calendar
  sync history with per-account results and pass create/update/cancel counts.
- `notification_rules`: DB-backed notification workflows with triggers,
  conditions, actions, active state, and templated content.
- `automation_rules`: DB-backed Trigger / If / Then automation rules. Stores
  metadata, active state, trigger JSON, indexed `trigger_keys`, condition JSON,
  action JSON, scheduler fields (`next_run_at`, `last_fired_at`, `run_count`),
  last status/error, creating user, and timestamps.
- `automation_runs`: durable execution history for every matched automation
  evaluation. Stores trigger key/payload, normalized runtime context, condition
  results, action results, trace ID, actor/source, status, error, and timing.
- `automation_webhook_senders`: source-IP tracking for public automation
  webhook keys, including first/last seen, event count, and payload shape. New
  key/IP pairs emit the `webhook.new_sender` trigger.
- `presence`: current person presence state.
- `access_events`: final entry/exit/denial records with timing classification
  and trace linkage in `raw_payload.telemetry.trace_id`.
- `anomalies`: unauthorized plates, duplicate states, outside schedule.
- `users`: local dashboard accounts, roles, status, hashed passwords, UI
  preferences, optional mobile phone number, and optional linked person.
- `system_settings`: database-backed dynamic settings. Secret values are
  encrypted at rest with a Fernet key derived from `IACS_AUTH_SECRET_KEY`.
- `maintenance_mode_state`: global automation kill-switch state, actor, reason,
  source, and Home Assistant sync identity.
- `audit_logs`: CRUD, integration, Alfred, maintenance, and alert action audit
  rows.
- `telemetry_traces` and `telemetry_spans`: sanitized operational traces and
  waterfall steps for LPR, APIs, integrations, Alfred, maintenance, and gate
  malfunction flows.
- `external_dependencies`: auto-enrolled system-wide external package records
  from Python, npm, Dockerfile, Compose, runtime, and lockfile sources.
- `dependency_update_analyses`: changelog/code-usage/LLM review records for
  dependency updates with `safe`, `warning`, or `breaking` verdicts.
- `dependency_update_backups`: offline rollback archives, storage root,
  checksum, manifest/config snapshots, and restore metadata.
- `dependency_update_jobs`: update/restore job state, actor, target version,
  streamed log path, trace ID, result, and error.
- `gate_state_observations`: persisted Home Assistant gate/door state changes.
- `gate_malfunction_states`, `gate_malfunction_timeline_events`, and
  `gate_malfunction_notification_outbox`: stuck-open gate detection, recovery,
  milestone notifications, and timeline state.
- `leaderboard_state`: persisted top-known-plate state used to detect Top
  Charts overtakes.
- `chat_sessions`: persistent AI chat sessions.
- `chat_messages`: persisted AI chat messages.

Filesystem runtime data:

- `data/chat_attachments/`: user uploads and Alfred-generated CSV/PDF/snapshot
  attachments, keyed by file ID and protected by owner-user checks.
- `data/backend/snapshots/access-events/`: compact access-event camera
  snapshots owned by `SnapshotManager`.
- `data/backend/notification-snapshots/`: short-lived notification camera
  attachments owned and TTL-cleaned by `SnapshotManager`.
- `data/backend/alert-snapshots/`: retained legacy unauthorized-plate snapshot
  files from older alert rows. New alert media should reference the linked
  access-event snapshot metadata instead of capturing new files here.
- `data/backend/telemetry-artifacts/`: bounded trace artifacts such as camera
  snapshots.
- `data/backend/unifi-protect-package/` and
  `data/backend/unifi-protect-backups/`: managed UniFi Protect package overlays
  and rollback backups.
- `data/backend/dependency-update-cache/`: update-engine temporary package
  downloads and cache files.
- `data/backend/dependency-update-backups/`: bind-mounted offline update
  rollback archives.
- `logs/backend/dependency-updates/`: bounded stdout/stderr logs for update and
  restore jobs.

Startup schema creation is currently handled by `app/db/bootstrap.py` through
`Base.metadata.create_all`, plus a small set of idempotent transitional column
additions for existing installs. This is acceptable for the current phased
scaffold. When the schema stabilizes, add Alembic migrations rather than
continuing to rely only on auto-create.

Retired legacy schedule tables:

- `time_slots` and `schedule_assignments` have been removed from models, API,
  scheduler logic, and the live database.
- Do not reintroduce `/api/v1/time-slots` or legacy fallback schedule logic.
- All access windows now use `schedules.time_blocks`; unassigned records use
  the `schedule_default_policy` dynamic setting.

Seed data:

- No demo people, vehicles, groups, schedules, or users are seeded at startup.
- `IACS_SEED_DEMO_DATA=true` is currently ignored with a warning.
- Create directory records and the first Admin through the UI/API.

## Configuration

Configuration is split into bootstrap and dynamic settings.

Bootstrap remains in `.env`/Compose:

- Host ports.
- Postgres credentials and database URL construction.
- Redis URL.
- `IACS_ENVIRONMENT`, `IACS_AUTO_CREATE_SCHEMA`, `IACS_SEED_DEMO_DATA`.
- `IACS_AUTH_SECRET_KEY`.
- CORS, trusted hosts, public base URL, and root path.
- Module selectors: `IACS_LPR_ADAPTER`, `IACS_GATE_CONTROLLER`, and
  `IACS_NOTIFICATION_SENDER`.

Dynamic settings live in `system_settings` and are edited through the UI/API:

- General: app name, log level, timezone.
- Auth options: cookie name, token lifetimes, secure-cookie flag.
- LPR tuning: debounce quiet/max seconds and similarity threshold.
- Access: default policy when no schedule is assigned.
- Integrations: Home Assistant, Apprise, WhatsApp Cloud API, DVLA Vehicle
  Enquiry Service, UniFi Protect.
- LLM providers: active provider, timeout, base URLs, models, API keys.

Backend service:

- `backend/app/services/settings.py`
- `backend/app/core/crypto.py`
- `backend/app/api/v1/settings.py`

Do not add new provider tokens or operational tuning values back to
`docker-compose.yml` or `.env.example`. Add them to the dynamic settings seed
and UI instead. Secret setting keys must be added to `SECRET_KEYS`; current
secret dynamic settings include Home Assistant token, Apprise URLs, WhatsApp
access token/webhook verify token/app secret, DVLA API key, UniFi Protect
username/password/API key, and LLM provider API keys.

Startup prunes obsolete dynamic settings including `notification_rules` and
`home_assistant_presence_entities`; do not reintroduce either as
`system_settings` JSON.

## Authentication and Users

Auth service:

- `backend/app/services/auth.py`

Auth routes:

- `backend/app/api/v1/auth.py`
- `backend/app/api/v1/users.py`

Current behavior:

- First-run setup is required when the `users` table is empty.
- `GET /api/v1/auth/status` returns `setup_required: true` until the first
  Admin exists.
- `POST /api/v1/auth/setup` creates the initial master Admin, sets an
  HTTP-only session cookie, and permanently locks once any user exists.
- Passwords are hashed with Argon2 through `argon2-cffi`.
- Auth uses signed HTTP-only JWT cookies. Bearer tokens are also accepted for
  API clients.
- Dashboard APIs, docs, OpenAPI, realtime WebSocket, and AI chat WebSocket are
  protected once setup is complete. Health, auth setup/login/status/logout, and
  Ubiquiti plus WhatsApp webhook ingestion paths remain public.
- Admin-only user CRUD prevents deleting, demoting, or deactivating the last
  active Admin.
- Sidebar collapse preference is persisted to `users.preferences`.

All dashboard/API clients must use the versioned `/api/v1` prefix. Do not add
or rely on legacy `/api/auth`, `/api/users`, `/api/schedules`, `/api/webhooks`,
`/api/chat`, or other non-versioned API aliases.

Relevant bootstrap/dynamic keys:

- `IACS_AUTH_SECRET_KEY`
- `auth_cookie_name`
- `auth_access_token_minutes`
- `auth_remember_days`
- `auth_cookie_secure`

## Directory and Schedules

Directory routes:

- `backend/app/api/v1/directory.py`
- `backend/app/api/v1/schedules.py`

Current behavior:

- People, vehicles, groups, and weekly schedules are editable from the UI/API.
- Person and vehicle records can reference a reusable schedule through
  `schedule_id`; vehicle schedule takes precedence over owner schedule.
- People can store profile photos, notes, active state, group, schedule, linked
  vehicles, assigned garage-door entity IDs, and a linked Home Assistant mobile
  app notify service (`notify.mobile_app_*`).
- Vehicles can store photo data, make, model, colour, description, active state,
  owner, and schedule.
- Vehicles can be manually DVLA-refreshed through
  `POST /api/v1/vehicles/{vehicle_id}/dvla-refresh`; this updates official
  make/colour and compliance cache fields for that stored vehicle.
- Vehicles can be deleted; people and groups currently have create/update flows
  but no delete/archive endpoint.
- Schedule deletion is blocked while the schedule is referenced by people,
  vehicles, or configured Home Assistant door entities.

Schedule rules:

- `schedules.time_blocks` stores Monday-first weekly intervals normalized to
  30-minute boundaries.
- Active `schedule_overrides` take precedence for the matching person and,
  when supplied, vehicle.
- If no schedule applies, `schedule_default_policy` controls allow/deny.
- There is no legacy time-slot fallback.

## Visitor Passes

Visitor Pass service:

- `backend/app/services/visitor_passes.py`
- `backend/app/api/v1/visitor_passes.py`

Routes:

- `GET /api/v1/visitor-passes`: list passes, with repeated `status` query
  filters and optional `q` search.
- `GET /api/v1/visitor-passes/{pass_id}`: fetch one pass.
- `GET /api/v1/visitor-passes/{pass_id}/whatsapp-messages`: fetch the stored
  WhatsApp Visitor Concierge transcript for a duration pass.
- `GET /api/v1/visitor-passes/{pass_id}/logs`: fetch the pass-specific audit
  trail used by the Passes detail Log tab.
- `POST /api/v1/visitor-passes`: create a UI-sourced pass.
- `PATCH /api/v1/visitor-passes/{pass_id}`: edit scheduled/active pass details.
- `POST /api/v1/visitor-passes/{pass_id}/cancel`: cancel a scheduled/active
  pass.
- `DELETE /api/v1/visitor-passes/{pass_id}`: hard-delete a pass after an
  explicit operator action. Deletion must write an audit row before removing the
  database record.

Lifecycle rules:

- One-time pass windows are `expected_time +/- window_minutes`; default window
  is 30 minutes. Duration passes require `visitor_phone`, `valid_from`, and
  `valid_until`, and use that explicit window.
- Sources that need asymmetric validity can set `valid_from` and `valid_until`.
  Calendar-created passes use `expected_time` for the event start, `valid_from`
  for 30 minutes before the event, and `valid_until` for the event end.
- Matching, lifecycle refresh, API serialization, and filters use
  `valid_from`/`valid_until` when both fields are present; otherwise they fall
  back to `expected_time +/- window_minutes`.
- `scheduled` becomes `active` once the current time enters the window.
- `active` or still-`scheduled` passes become `expired` once the window has
  elapsed without a detection. Duration passes expire at `valid_until`.
- A matching unknown one-time arrival claims the best active pass and sets it to
  `used`. Duration arrivals require the confirmed stored `number_plate`, remain
  `active` for ongoing updates during their window, and record arrival
  telemetry without becoming `used`.
- `used` and `cancelled` are terminal for lifecycle refresh; used one-time
  passes and active duration passes can still receive departure telemetry.
- If multiple active passes overlap, choose the pass whose `expected_time` is
  closest to the detection time, then the oldest `created_at` as tie-breaker.
- A later same-plate exit updates `departure_time`, `departure_event_id`, and
  `duration_on_site_seconds`.
- The lifecycle worker runs from FastAPI lifespan every 30 seconds via
  `VisitorPassService.start()`.
- All creates, updates, cancels, deletes, lifecycle status changes, pass
  claims, and telemetry links write audit rows.
- Visitor Pass departure lookups must use the indexed same-plate, `used`,
  no-departure path ordered by latest arrival. Calendar reconciliation must use
  `creation_source="icloud_calendar"`, active/scheduled status, and
  `source_reference`; do not replace these hot paths with per-row scans.

Extensibility:

- Creation must go through `VisitorPassService.create_pass(..., source=...)`.
  Keep source values lower-case strings such as `ui`, `alfred`, `discord`,
  `slack`, or `icloud_calendar` so future modules can hook in without changing
  the core matching logic.
- Source integrations should set `source_reference` when they can provide a
  stable upstream event ID; iCloud Calendar uses
  `icloud:<account_id>:<calendar_id>:<event_id>` for idempotent sync.
- Do not model Visitor Pass vehicles as `vehicles` rows. Unknown-visitor DVLA
  and visual telemetry belongs on `visitor_passes`, not the directory.

## LPR and Access Pipeline

Webhook endpoint:

```text
POST /api/v1/webhooks/ubiquiti/lpr
```

Ubiquiti adapter:

- `backend/app/modules/lpr/ubiquiti.py`

Normalized LPR contract:

- `backend/app/modules/lpr/base.py`
- `PlateRead(registration_number, confidence, source, captured_at, raw_payload)`

Access service:

- `backend/app/services/access_events.py`

Current behavior:

- Maintenance Mode is a global kill-switch for automation. When it is active,
  Ubiquiti LPR webhooks return accepted/ignored, queued and pending plate reads
  are discarded, and no access event, presence update, gate command, or garage
  command is produced from LPR.
- Accept Ubiquiti Alarm Manager test webhook payloads and publish
  `webhook.test.received` without creating an access event.
- Ubiquiti LPR webhooks that name a UniFi smart zone are filtered before access
  processing. Only zones listed in `lpr_allowed_smart_zones` are allowed to
  enqueue a `PlateRead`; default is `default`. Payloads with no zone metadata
  remain accepted for compatibility. Ignored reads publish `plate_read.ignored`
  with `reason="outside_lpr_smart_zone"` and never create access events,
  alerts, notifications, visitor-pass matches, or gate commands.
- Queue every accepted plate read.
- Record raw LPR timing diagnostics from Ubiquiti webhooks and UniFi Protect
  websocket/track probes in an in-memory feed exposed by
  `/api/v1/diagnostics/lpr-timing`.
- Capture the current top-gate state as soon as each plate read is queued and
  store that observation in the read payload. This state is the source of truth
  for arrival/exit decisions when it is known.
- Compare every detected plate against active stored vehicle registrations
  before debounce grouping. If the detected plate is similar to a stored plate
  above `lpr_similarity_threshold`, canonicalize the read to the stored
  registration and preserve the original detected value in raw payload metadata.
- Exact active stored-plate detections win over confidence ordering. If the
  exact stored plate appears during a debounce burst, finalize that burst
  immediately as the exact stored registration instead of waiting for the quiet
  period.
- After an exact stored-plate detection finalizes a burst, suppress trailing
  same-source reads inside the original max debounce window so follow-up
  misreads do not create stray unauthorized events.
- Group similar canonicalized reads from the same source during a debounce
  window.
- Wait for a quiet period or max debounce period unless an exact active stored
  plate short-circuits the window.
- Select the best candidate, preferring exact active stored-plate matches, then
  highest confidence, then latest captured time.
- Determine authorization from vehicle/person/schedule.
- For unknown vehicles, run Visitor Pass matching before anomaly creation:
  arrival-like reads try to claim an active pass in the current window, and
  exit-like reads try to find a used same-plate pass with no departure. A
  Visitor Pass match grants the event, stores `raw_payload.visitor_pass`, links
  the pass to the access event/trace, and suppresses the unauthorized-plate
  anomaly.
- Classify presence timing as `earlier_than_usual`, `normal`,
  `later_than_usual`, or `unknown` from recent matching historical events.
- Infer direction primarily from the captured top-gate state:
  - `closed` means the vehicle/person is arriving and the event is an entry.
  - `open`, `opening`, or `closing` means the ground exit loop has already
    opened the gate and the vehicle/person is leaving; the event is an exit.
  - Unknown gate state falls back to explicit payload direction and then
    current presence state.
- If gate state says entry while the person is already marked present, fetch a
  live UniFi Protect snapshot from `camera.gate` and ask OpenAI image analysis
  whether the vehicle is facing towards the camera (arriving) or away from the
  camera (leaving). Use a clear `entry` or `exit` result from that camera
  tie-breaker as the source of truth; if the tie-breaker fails or returns
  unknown, keep the gate-state entry decision.
- Run DVLA enrichment after debounce/known-plate resolution and direction
  classification, but only for arrival-like events. Exit events must never call
  DVLA.
- For known arriving vehicles, use same-day cached `vehicles` compliance fields
  when `last_dvla_lookup_date` equals the current date in `site_timezone`;
  otherwise call the shared DVLA service, refresh official make/color and
  compliance fields, and set `last_dvla_lookup_date`.
- For unknown arriving vehicles, call the shared DVLA service and keep the
  normalized result only in the in-memory notification context. Do not persist
  unknown-vehicle DVLA data into `vehicles`, `access_events.raw_payload`, or
  telemetry span payloads. If a Visitor Pass is claimed, persist normalized
  vehicle make/colour and plate only on the linked `visitor_passes` row.
- DVLA enrichment failures are non-blocking: log/publish sanitized telemetry and
  continue access event persistence, presence updates, gate/garage commands, and
  existing notifications.
- Expired MOT/tax compliance is advisory only. It can emit notification
  workflow triggers but must not deny access or block gate/garage commands.
- Persist one final `access_event`.
- For Visitor Pass arrivals, mark the pass `used`, set `arrival_time`,
  `arrival_event_id`, `telemetry_trace_id`, `number_plate`, and vehicle details
  when available. For Visitor Pass exits, set departure/duration fields.
- Update presence if access is granted.
- Create anomalies/alerts for unauthorized plates, outside schedule, duplicate
  entry, and duplicate exit. Unauthorized-plate alerts attach compact
  `camera.gate` snapshot metadata from the finalized access event when
  available. Do not create unauthorized-plate anomalies for Visitor Pass
  matched unknown plates.
- Record sanitized telemetry traces/spans for webhook/API ingress, debounce,
  vehicle verification, schedule evaluation, direction resolution, DVLA,
  persistence, gate/garage commands, notifications, and vision tie-breakers.
- Broadcast realtime events.
- On granted entry, request gate open only when the captured top-gate state at
  plate-read time was `closed`.
- Open assigned garage doors only after the automatic gate-open command is
  accepted, only for entries whose captured top-gate state was `closed`, and
  only after checking each garage door's optional schedule.
- If the captured top-gate state was already `open`, `opening`, or `closing`,
  treat the event as leaving and do not open any assigned garage doors.
- Send contextual anomaly notifications.

Relevant dynamic settings:

- `lpr_debounce_quiet_seconds`
- `lpr_debounce_max_seconds`
- `lpr_similarity_threshold`
- `lpr_allowed_smart_zones`
- `site_timezone`
- `schedule_default_policy`

## Maintenance Mode and Gate Malfunctions

Maintenance service:

- `backend/app/services/maintenance.py`
- `backend/app/api/v1/maintenance.py`

Gate malfunction service:

- `backend/app/services/gate_malfunctions.py`
- `backend/app/api/v1/gate_malfunctions.py`

Runtime behavior:

- Maintenance Mode is persisted in `maintenance_mode_state`, published as
  `maintenance_mode.changed`, audited, notifies through the workflow engine, and
  syncs to `input_boolean.top_gate_maintenance_mode`.
- While Maintenance Mode is active, LPR automation is ignored/cleared and gate
  malfunction recovery attempts are paused. Manual UI/API hardware commands
  remain explicit user actions with confirmation where the frontend requires it.
- Maintenance Mode is also the global kill-switch for Automation Engine
  runtime actions. Hardware actions and notification workflow toggles are
  skipped while active; `maintenance_mode.disable` is the only runtime exception
  so scheduled rules can turn Maintenance Mode off.
- The gate malfunction scheduler starts from FastAPI lifespan, listens to Home
  Assistant gate state changes, declares a malfunction after the primary gate
  has remained in an unsafe open/opening/closing state for five minutes, and
  resolves it when the gate closes.
- Recovery attempts are scheduled at increasing offsets, claim-protected to
  avoid duplicate workers, and bypass normal gate schedules because they are
  safety recovery commands.
- Milestone notifications are queued for initial, 30-minute, 60-minute,
  2-hour, and FUBAR states. Notification dispatch is tracked in a persistent
  outbox.
- Gate malfunction timelines include declaration, preceding access event,
  recovery attempts, notifications, manual overrides, FUBAR, and resolution.
- Admin-only manual overrides can recheck live state, run an attempt now, mark
  resolved, or mark FUBAR.
- Alfred can inspect maintenance/malfunction state and prepare confirmed
  maintenance toggles or admin-only malfunction overrides.

Rules:

- Do not create a second kill-switch mechanism. Use `maintenance.py`.
- Do not send recovery commands while Maintenance Mode is active.
- Keep gate malfunction state persistent and auditable; do not replace it with
  a purely in-memory timer.
- Do not mark a gate malfunction resolved unless a live/observed closed state
  or explicit admin override supports that outcome.

## Home Assistant Integration

Shared client:

- `backend/app/modules/home_assistant/client.py`

Gate controller:

- `backend/app/modules/gate/home_assistant.py`

TTS announcer:

- `backend/app/modules/announcements/home_assistant_tts.py`

State sync service:

- `backend/app/services/home_assistant.py`

Cover helpers:

- `backend/app/modules/home_assistant/covers.py`

Mobile notification sender:

- `backend/app/modules/notifications/home_assistant_mobile.py`

Configuration lives in dynamic settings; token values are encrypted:

- `home_assistant_url`
- `home_assistant_token`
- `home_assistant_gate_entities`: list of configured gate cover entities with
  `entity_id`, display `name`, `enabled`, and optional `schedule_id`.
- `home_assistant_garage_door_entities`: list of configured garage door cover
  entities with the same shape.
- `home_assistant_gate_open_service`
- `home_assistant_tts_service`
- `home_assistant_default_media_player`
- `home_assistant_gate_entity_id`: legacy single-gate seed/fallback only; do
  not build new UI or logic against it.

Rules:

- Route gate commands through the gate module.
- Route garage door commands through `POST /api/v1/integrations/cover/command`
  or the configured per-person automatic garage door flow.
- Route audio announcements through the HA TTS module.
- Do not use Home Assistant `person.*` geofence/entity states as IACS
  presence. Presence is derived from LPR access events because HA person state
  is already `home` by the time a vehicle reaches the gate.
- Home Assistant discovery can suggest `notify.mobile_app_*` services and
  person-to-mobile mappings, but those are notification endpoints only, not
  presence sources.
- Do not call HA directly from API route handlers except through services or
  integration modules.
- Gate and garage door opens can be schedule-gated through `schedule_id`; closed
  commands are allowed for configured garage doors.
- Maintenance Mode syncs to
  `input_boolean.top_gate_maintenance_mode` when toggled through the UI/API or
  Alfred.
- Home Assistant entity discovery and auto-detect endpoints are Admin-only.

## Notifications

Notification service:

- `backend/app/services/notifications.py`

Apprise sender:

- `backend/app/modules/notifications/apprise_client.py`

Structured notification contract:

- `NotificationContext(event_type, subject, severity, facts)`

Notification workflows:

- Stored in `notification_rules`, not `system_settings`.
- Configured through `/api/v1/notifications/*` and the Settings /
  Notifications UI.
- Trigger catalog currently covers authorised entry, unauthorised plate,
  outside schedule, duplicate entry/exit, expired MOT/tax detected, gate open
  failure, garage door open failure, gate malfunction initial/30m/60m/2hrs/FUBAR,
  leaderboard overtake, Maintenance Mode enabled/disabled, and AI anomaly alert.
- Supported action channels are mobile (Apprise and Home Assistant mobile app
  notify services), WhatsApp Admin messages, in-app realtime dashboard
  notifications, Discord, and Home Assistant voice/TTS.
- Supported conditions currently include schedule windows and presence state.
- Templates use `@Variable` tokens in the Tiptap editor. Legacy `[Variable]`
  tokens are accepted by the renderer but should not be used for new UI.
- Vehicle notification variables include `@VehicleMake`, `@VehicleColor`,
  `@VehicleColour`, `@MotStatus`, `@MotExpiry`, `@TaxStatus`, and
  `@TaxExpiry`. `@VehicleColor` and `@VehicleColour` resolve from the same
  unified colour fact.
- Maintenance, malfunction, and leaderboard variables include
  `@MaintenanceModeReason`, `@MaintenanceModeDuration`,
  `@MalfunctionDuration`, `@MalfunctionOpenedTime`,
  `@MalfunctionFixAttemptTime`, `@MalfunctionFixAttempts`,
  `@MalfunctionResolutionTime`, `@LastKnownVehicle`, `@NewWinnerName`,
  `@OvertakenName`, and `@ReadCount`.
- Visitor Pass variables include `@VisitorName`, `@VisitorPassName`,
  `@VisitorPassVehicleRegistration`, `@VisitorPassVehicleMake`,
  `@VisitorPassVehicleColour`, `@VisitorPassDurationOnSite`,
  `@VisitorPassCurrentWindow`, `@VisitorPassRequestedWindow`,
  `@VisitorPassOriginalTime`, `@VisitorPassRequestedTime`, and
  `@VisitorPassVisitorMessage`. Timeframe-change approval notifications must
  populate the original and requested time variables.
- Mobile and in-app notification actions can attach a UniFi Protect camera
  snapshot when the selected action media has `attach_camera_snapshot`.
- WhatsApp notification actions use `type="whatsapp"` and target IDs shaped as
  `whatsapp:*`, `whatsapp:admin:<user_id>`, or
  `whatsapp:number:<@Variable-or-phone>`. Dynamic phone targets are rendered
  from notification variables, normalized to digits, deduplicated, and sent via
  the WhatsApp service.
- Voice/TTS messages run through `apply_vehicle_tts_phonetics` before delivery.

Rules:

- Always compose notifications from structured facts.
- Alfred AI naturalization should operate on `NotificationContext`, not raw
  logs or invented context.
- `apprise_urls` lives in encrypted dynamic settings and may be comma-separated
  or newline-separated.
- The obsolete `notification_rules` dynamic setting is pruned at startup; do
  not store notification workflow JSON in `system_settings`.

## WhatsApp Integration

Service and routes:

- `backend/app/services/whatsapp_messaging.py`
- `backend/app/api/v1/whatsapp.py`
- `GET/POST /api/v1/webhooks/whatsapp`

Configuration lives in dynamic settings. Secret values are encrypted:

- `whatsapp_enabled`
- `whatsapp_access_token` secret
- `whatsapp_phone_number_id`
- `whatsapp_business_account_id`
- `whatsapp_webhook_verify_token` secret
- `whatsapp_app_secret` optional secret
- `whatsapp_graph_api_version`, default `v25.0`
- `whatsapp_visitor_pass_template_name`, default
  `visitor_pass_registration_request`
- `whatsapp_visitor_pass_template_language`, default `en_GB`

Runtime behavior:

- The frontend exposes a WhatsApp tile in API & Integrations with enable toggle,
  Meta credentials, webhook verify token, optional app secret, Graph API
  version, and a read-only webhook URL derived from the current public origin.
  If production uses a dedicated webhook hostname, operators must paste that
  dedicated URL into Meta even if the modal was opened from the UI hostname.
- Outbound text and confirmation messages use Meta Cloud API
  `POST https://graph.facebook.com/{version}/{phone_number_id}/messages` with
  `messaging_product="whatsapp"`. Text sends use `type="text"`; Alfred
  confirmations use interactive reply buttons.
- `whatsapp_phone_number_id` is always the configured WhatsApp business sender
  ID for outbound sends. Never use an Admin user's `mobile_phone_number` as the
  sender. Admin mobile numbers are personal recipient and inbound identity
  values only.
- Webhook verification accepts Meta's `hub.challenge` only when
  `hub.verify_token` matches the encrypted `whatsapp_webhook_verify_token`.
- Incoming POST webhooks validate `X-Hub-Signature-256` when
  `whatsapp_app_secret` is configured. If the app secret is blank, POSTs are
  accepted as unsigned and logged as such.
- Incoming webhooks must match the configured `whatsapp_phone_number_id` from
  webhook metadata before message/status processing. If `whatsapp_app_secret`
  is configured, signature validation happens before routing.
- Incoming sender numbers are normalized by stripping non-digits. Routing is
  Admin-first: exact match against an active Admin
  `users.mobile_phone_number`, including country code digits. Matched Admin
  senders are linked to a `messaging_identities` row with
  `provider="whatsapp"` and routed through `MessagingBridgeService` into the
  fully privileged Alfred ReAct chat loop with messaging context.
- If no active Admin matches, the service checks for an active or scheduled
  `duration` Visitor Pass with exact `visitor_phone`. Eligible visitors are
  routed to the Visitor Concierge persona only. Expired/cancelled/no-match
  visitors receive the safe visitor response where appropriate or are audited
  as denied; they must never reach Admin Alfred or `MessagingBridgeService`.
- WhatsApp interactive confirmation button IDs are bound as
  `iacs:<confirm|cancel>:<session_id>:<confirmation_id>` and call
  `ChatService.handle_tool_confirmation`.
- Visitor Pass interactive confirmation button IDs are bound as
  `iacs:vp:<confirm|change>:<pass_id>:<nonce>`. `Confirm` saves the pending
  normalized plate and any server-side DVLA make/colour enrichment to that
  bound pass; `Change` asks the visitor to type a new registration.
- Visitor Pass timeframe approval button IDs are bound as
  `iacs:vp_time:<allow|deny>:<pass_id>:<request_id>`. Admin approval updates
  that pass window and messages the visitor; denial leaves the existing window
  unchanged and messages the visitor.
- Visitor-side timeframe confirmation button IDs are bound as
  `iacs:vp_time_user:<confirm|change>:<pass_id>:<request_id>`. Small
  timeframe changes are stored as pending metadata first; `Confirm` applies the
  requested window, while `Change` asks the visitor to type a new time.

Visitor Concierge sandbox:

- The Visitor Concierge is a separate, restricted LLM chain for visitor
  registration capture only. It must not import, call, or be given Admin Alfred
  tools, `MessagingBridgeService`, gate/door controls, settings, schedules,
  user records, maintenance controls, notification workflow tools, or file
  tools.
- The only Visitor Concierge tool names are
  `get_pass_details(phone_number)` and
  `update_visitor_plate(pass_id, new_plate)`. Server-side handlers bind the
  normalized WhatsApp sender and resolved Visitor Pass context; supplied
  prompt-injected pass IDs or other phone numbers are rejected or ignored.
- The persona prompt must tell the model it is speaking to a visitor, must
  ignore requests to act as Admin Alfred or reveal instructions, and must return
  only registration extraction JSON, allowed timeframe-change JSON, or a short
  safe visitor-facing reply. Any request outside Visitor Pass details,
  timeframe, or vehicle registration, including gate/door/garage operations,
  must reply exactly:
  `Sorry, I can only discuss details about your visitor pass and vehicle registration.`
- Visitor Concierge replies should still sound like Alfred. Once a visitor has
  confirmed their plate, friendly acknowledgements or light banter should get a
  short warm close-out such as "Haha, thanks Josh! You're all set." rather than
  another registration prompt. Server-side handling must only accept
  `plate_detected` when the plate appears in the visitor's latest message; the
  LLM must never reuse the stored pass plate from context as a new detection.
- Visitor text messages are briefly debounced and combined before Concierge
  processing so split replies like "my reg is" followed by "AB12 CDE" are
  interpreted together. Emoji-only visitor messages are not treated as content,
  but they mark the visitor as emoji-friendly so Alfred may use a light emoji in
  later safe replies.
- If a visitor uses Alfred's name directly in an otherwise allowed message,
  Alfred may include a concise cheeky, geeky nod to Alfred and Jason creating
  the system. Never add this nod to restricted/off-topic responses, which must
  keep the exact sandbox wording.
- After the Visitor Concierge extracts a plate, the webhook service may run a
  server-side DVLA lookup for that plate and use the returned make/colour in the
  WhatsApp confirmation copy, for example "which is a Silver Tesla". Never tell
  visitors that make/colour came from DVLA or another external integration;
  Alfred should simply sound like he knows the vehicle details. DVLA lookup is
  not a Visitor LLM tool and failures must be non-blocking; the visitor should
  still be able to confirm or change the parsed registration.
- Visitor-requested timeframe changes up to one hour on either boundary may be
  accepted by the restricted Visitor Concierge, but timeframe interpretation
  must come from the Visitor Concierge LLM using the supplied site timezone and
  current local pass window. Do not add local keyword/regex timeframe parsers
  ahead of the LLM; if the LLM is unavailable or returns an invalid window, the
  visitor must be asked to contact their host or provide clearer times rather
  than guessing. Small changes must be confirmed back to the visitor with
  WhatsApp `Confirm` / `Change` buttons before the database window is updated.
  The one-hour auto-change limit is cumulative against the original Visitor
  Pass window, not the latest visitor-updated window, so visitors cannot walk a
  pass outside the allowed range through repeated small changes. Larger changes
  must create the
  `visitor_pass_timeframe_change_requested` notification trigger with the
  pending request stored in `visitor_passes.source_metadata`; the visitor
  receives "I've sent a request for approval to change your allowed timeframe,
  I'll get back to you shortly." while awaiting Admin action.
- The `visitor_pass_timeframe_change_requested` notification supports Admin
  action buttons in WhatsApp, in-app notifications, and Home Assistant mobile
  app notifications. Home Assistant mobile buttons use
  `mobile_app_notification_action` events carrying the same
  `iacs:vp_time:<allow|deny>:<pass_id>:<request_id>` action IDs; the IACS Home
  Assistant WebSocket listener processes those events and calls the restricted
  approval handler.
- Duration Visitor Passes should have `visitor_phone`, `valid_from`, and
  `valid_until`. The first outbound contact uses the approved utility template
  named by `whatsapp_visitor_pass_template_name` and language
  `whatsapp_visitor_pass_template_language`, with body parameters
  `[visitor_name, window_label]`. Free-form text and interactive buttons are
  sent only after Meta's customer-service window permits them.
- Before production templates are approved, testing can be initiated by the
  visitor sending `Begin` or `Start` from the matching `visitor_phone` to the
  configured WhatsApp business number. That inbound message opens Meta's
  customer-service window and the Visitor Concierge replies with the
  registration prompt for the active or scheduled duration pass.
- Duration Visitor Pass card/status metadata uses
  `source_metadata.whatsapp_concierge_status` and related detail/error fields
  to show states such as Welcome Message Sent, Awaiting Visitor Reply, Visitor
  Replied, Requested Time Change, Awaiting Time Change Approval,
  Complete - Vehicle Registration, Complete - Vehicle Registration: [plate]
  Time Updated, Message Sending Failed, User Not On WhatsApp, and Failed.
- Duration Visitor Pass WhatsApp transcripts are stored on
  `source_metadata.whatsapp_chat_history` as bounded inbound/outbound message
  entries and exposed through
  `GET /api/v1/visitor-passes/{pass_id}/whatsapp-messages`.
  Transcript writes must publish a Visitor Pass realtime update so open detail
  modals refresh without being closed and reopened.
  Sending failure states must expose an IACS tooltip with a plain-language
  explanation and next step; do not rely on raw Meta/API error strings as the
  only operator-facing help.

Meta production setup requirements:

- `whatsapp_phone_number_id` is Meta's numeric sender ID, not the visible
  telephone number. Keep it aligned with the production number that users are
  actually messaging; test-number IDs and production-number IDs are different.
- Use a permanent Meta System User token, not a temporary API Setup token. The
  token must cover the selected app/WABA and include WhatsApp messaging
  permissions such as `whatsapp_business_messaging` and
  `whatsapp_business_management`.
- A production Cloud API sender must be registered before it can send. After
  Meta phone-code verification, registration is performed with
  `POST /{phone_number_id}/register` and body
  `{messaging_product:"whatsapp", pin:"<six-digit-pin>"}`. Store the
  two-step verification PIN outside the repository. If Meta returns
  `#133005 Two step verification PIN Mismatch`, use the existing PIN or reset
  it in WhatsApp Manager before retrying.
- The WABA must be subscribed to the app for production webhooks. Verify with
  `GET /{waba_id}/subscribed_apps`; an empty list means message/status
  webhooks will not flow for the production account. Subscribe with
  `POST /{waba_id}/subscribed_apps` and `subscribed_fields=["messages"]`.
- The production phone number should show `platform_type="CLOUD_API"`,
  `status="CONNECTED"`, and `account_mode="LIVE"` from Graph API before IACS
  is considered production-ready.

Delivery model:

- Meta can return HTTP 200 for `/{phone_number_id}/messages` and later reject
  the message asynchronously through a status webhook. Always inspect
  `whatsapp_message_status` logs when a message was accepted but not received.
- Free-form `type="text"` messages are only valid inside WhatsApp's customer
  service window after the recipient has replied to the exact production
  business number. Status error `131047` / `Re-engagement message` means the
  send was blocked because the recipient has not replied within the active
  window.
- To initiate contact outside that window, IACS needs approved production
  template-message support. Template sends must use `type="template"` with an
  approved WABA template; Meta's `hello_world` sample template is limited to
  public test numbers and cannot be used from production numbers.
- Admin opt-in templates should be Utility/account language, not marketing
  language. Prefer concise copy such as "Confirm this WhatsApp number for your
  Crest House Access Control admin account." with a Quick Reply button such as
  `Confirm`. Avoid words like "subscribe", "marketing", "offers", and broad
  "notifications" copy that Meta may classify as marketing.
- Template quick replies arrive through the same WhatsApp webhook as
  interactive messages. Future template opt-in handling should bind the button
  payload to an Admin user/phone and then mark the channel ready for free-form
  replies during the customer-service window.

Recommended Cloudflare Tunnel deployment:

- Prefer a separate webhook hostname on standard HTTPS 443 instead of a custom
  public port. Example: `iacs.example.com` for the UI and
  `iacs-whatsapp.example.com` for WhatsApp webhooks.
- The UI hostname may sit behind Cloudflare Access. The WhatsApp webhook
  hostname must not require Cloudflare Access, browser challenges, CAPTCHA,
  mTLS, or Basic Auth because Meta cannot complete those flows.
- Route the webhook hostname through the tunnel to `backend:8000` or to the
  frontend proxy only if `/api/*` is forwarded correctly. In Cloudflare WAF,
  allow only `GET` and `POST` for `/api/v1/webhooks/whatsapp`; block every
  other path on the webhook hostname.
- Add a moderate rate limit on the webhook hostname/path, but use block-style
  enforcement rather than browser challenges. Keep `whatsapp_app_secret`
  configured so IACS validates Meta's `X-Hub-Signature-256` at the app layer.
- Do not depend on Meta source IP allowlisting unless Meta publishes stable
  ranges for the integration in use. Signature validation and path/method
  restrictions are the primary controls.

Rules:

- Keep WhatsApp Admin chat and Visitor Concierge routing separate. Do not route
  unknown phone numbers, expired visitors, visitor numbers, or partial phone
  matches into Admin Alfred.
- Do not store WhatsApp credentials in `.env` or Compose. Add future WhatsApp
  credentials/settings to dynamic settings and `SECRET_KEYS` when secret.
- Keep the service boundary provider-neutral for Admin Alfred by converting
  Admin inbound payloads into `IncomingChatMessage` before calling
  `MessagingBridgeService`. Visitor Concierge traffic must stay on the
  restricted visitor chain.
- Rotate any Meta access token that was pasted into chat, logs, issue trackers,
  or other shared surfaces, then update the encrypted dynamic setting.

## Unified Snapshot Service

Snapshot service:

- `backend/app/services/snapshots.py`

Compatibility wrappers:

- `backend/app/services/alert_snapshots.py`
- `backend/app/services/notification_snapshots.py`

Runtime behavior:

- `SnapshotManager` is the only owner for new filesystem image snapshot writes,
  path resolution, deletion helpers, and notification snapshot TTL cleanup.
- Access-event snapshots are captured through
  `SnapshotManager.capture_access_event_snapshot()`, compacted to small JPEGs,
  stored under `snapshots/access-events/`, and linked from `access_events`
  through `snapshot_path`, dimensions, byte count, content type, camera, and
  capture timestamp fields.
- Unauthorized-plate alert rows store snapshot metadata copied from the linked
  access event. `alert_snapshots.py` remains only for legacy retained
  `alert-snapshots` file serving/deletion and metadata compatibility.
- Notification camera attachments use `notification_snapshots.py` as a thin
  compatibility API, but storage, filename validation, TTL cleanup, and delete
  operations delegate to `SnapshotManager`.
- Do not add direct Pillow compression, `Path.write_bytes`, or ad hoc
  filesystem cleanup for access-event, alert, or notification camera snapshots
  outside `SnapshotManager`.
- Future age-based snapshot purge automation should use the indexed
  `created_at`/`snapshot_path` database fields and the manager's path resolver.

## Automation Engine

Automation service:

- `backend/app/services/automations.py`
- `backend/app/api/v1/automations.py`

Runtime behavior:

- Automations are stored in `automation_rules` as `triggers`, `conditions`, and
  `actions` JSON arrays shaped like `{id,type,config}`. Actions may also carry
  `reason_template`.
- Matching executions create `automation_runs` with sanitized trigger payload,
  normalized context, condition results, action results, telemetry trace ID,
  actor/source, status, and error details.
- Runtime rules are enabled only after explicit Admin UI/API actions or
  Alfred-confirmed create/edit/enable tools. State-changing Alfred automation
  tools must require confirmation.
- The scheduler starts from FastAPI lifespan, wakes every 15 seconds, selects
  due active rules with row locks, fires once per overdue rule, recomputes
  `next_run_at`, and disables single-use or expired time-only rules.
- AI schedule parsing uses the active LLM provider with a JSON-only prompt to
  produce `cron_expression`, optional `run_at`/`start_at`/`end_at`, timezone,
  summary, confidence, and ambiguity notes. Output is validated with `croniter`,
  timezone parsing, future next-run checks, end-date checks, and a confidence
  threshold; ambiguous output returns `requires_review`.
- Public webhook ingestion is
  `POST /api/v1/automations/webhooks/{webhook_key}`. Keys must be unguessable;
  sender IPs are tracked in `automation_webhook_senders`.
- Event-bus automations are bridged from direct domain events, not notification
  workflow status events. Vehicle triggers come from `access_event.finalized`;
  Visitor Pass triggers come from `visitor_pass.*`; Maintenance Mode triggers
  come from `maintenance_mode.changed`; AI issue triggers come from
  `ai.issue_detected`.
- Cycle guard rule: the Automation Engine must ignore `notification.trigger`,
  `notification.sent`, `notification.failed`, `notification.skipped`, and every
  `automation.run.*` event. Notification failures can publish health/status
  events, but they must never re-enter automation as a vehicle or notification
  trigger.
- Automation dry-runs are previews only: they build context, evaluate
  conditions, and render action reasons, but they must not execute actions,
  update iCloud Calendar sync timestamps, send notifications, or command
  hardware.

Trigger registry:

- Time & Date: `time.specific_datetime`, `time.every_x`, `time.cron`,
  `time.ai_text`.
- Vehicle Detections: `vehicle.known_plate`, `vehicle.unknown_plate`,
  `vehicle.outside_schedule`.
- Maintenance Mode: `maintenance_mode.enabled`, `maintenance_mode.disabled`.
- Visitor Pass: `visitor_pass.created`, `visitor_pass.detected`,
  `visitor_pass.used`, `visitor_pass.expired`.
- AI Agent: `ai.phrase_received`, `ai.issue_detected`.
- Webhook: `webhook.received`, `webhook.unrecognized`, `webhook.new_sender`.

Condition registry:

- Person: `person.on_site`, `person.off_site`.
- Vehicles: `vehicle.on_site`, `vehicle.off_site`.
- Maintenance Mode: `maintenance_mode.enabled`, `maintenance_mode.disabled`.

Action registry:

- Notifications: `notification.enable`, `notification.disable`.
- Gate Actions: `gate.open`.
- Garage Door Actions: `garage_door.open`, `garage_door.close`.
- Maintenance Mode: `maintenance_mode.enable`, `maintenance_mode.disable`.
- Integrations: dynamically supplied by
  `backend/app/services/automation_integration_actions.py`. Integration action
  payloads use provider/action config, for example
  `integration.icloud_calendar.sync` with
  `{provider:"icloud_calendar", action:"sync_calendars"}`.
- WhatsApp automation sends use `integration.whatsapp.send_message` with
  config `{provider:"whatsapp", action:"send_message", target_mode,
  target_user_ids, phone_number_template, message_template}`. `target_mode`
  may be `all`, `selected`, or `dynamic`; selected mode requires active Admin
  user IDs with mobile numbers, while dynamic mode renders
  `phone_number_template` from automation variables.
- Integration catalog entries include `enabled` and `disabled_reason`. Disabled
  actions remain visible to explain unavailable integrations, but runtime
  execution must return `skipped` with `reason="integration_disabled"` instead
  of crashing the Automation Engine.
- Maintenance Mode pauses WhatsApp automation sends along with notification and
  hardware actions.

Context variable pipeline:

- Every trigger is normalized into an `AutomationContext` with `trigger`,
  `entities`, `facts`, `variables`, `missing_required_variables`, and warnings.
- Trigger definitions declare available scopes: `time`, `person`, `vehicle`,
  `visitor_pass`, `maintenance`, `ai`, `webhook`, and `event`.
- Visitor Pass triggers serialize the pass payload and refresh from the database
  when a pass ID is available. For `visitor_pass.used`,
  `@VisitorPassVehicleRegistration` maps from `visitor_pass.number_plate`.
- Variables render as strings only. Unknown, unavailable, or empty tokens render
  as an empty string, produce a validation warning, and are treated as missing
  context.
- Before evaluating a condition or action, scan config/templates for `@Variable`
  references. If a referenced variable is unavailable for the trigger scope or
  resolves empty, skip that condition/action with `context_missing` rather than
  executing with null context.
- The automation catalog returns variable groups with scopes and trigger type
  filters. The frontend `@` menu must filter variables from the selected
  trigger so irrelevant tokens are hidden.

Execution loop:

- A normalized trigger is received from the scheduler, event bus, Alfred, or a
  webhook.
- Active matching `automation_rules` are selected by `trigger_keys`, then
  trigger-specific config filters are applied.
- A telemetry trace and `automation_runs` row are created.
- Conditions are evaluated as AND. The rule skips on the first false or missing
  condition.
- Actions execute sequentially and record individual results. Maintenance Mode
  skips hardware and notification-toggle actions, except
  `maintenance_mode.disable`.
- Integration actions are routed through the integration action registry rather
  than hardcoded in the core execution loop. The first registered integration
  action is iCloud Calendar `Sync Calendars Now`, which calls
  `ICloudCalendarService.sync_all(trigger_source="automation")`.
- iCloud Calendar sync is enabled only when at least one active connected
  account has a stored trusted session bundle. If all accounts are disabled,
  disconnected, or require re-authentication, the action is cataloged as
  disabled and stale automation runs skip with `integration_disabled`.
- The run row, rule `last_fired_at`, `next_run_at`, status/error, audit log,
  and realtime `automation.run.*` event are updated.

## DVLA Vehicle Enquiry Integration

DVLA client:

- `backend/app/modules/dvla/vehicle_enquiry.py`

Service wrapper:

- `backend/app/services/dvla.py`

Configuration lives in dynamic settings; the API key is encrypted:

- `dvla_api_key`
- `dvla_vehicle_enquiry_url`
- `dvla_test_registration_number`
- `dvla_timeout_seconds`

Rules:

- Send Vehicle Registration Numbers in the POST JSON body, never as URL query
  parameters.
- Normalize VRNs by removing spaces and non-alphanumeric characters before
  calling DVLA.
- Keep `backend/app/services/dvla.py` as the shared integration boundary for
  raw lookups and normalized enrichment. Manual lookup endpoints, AI tools,
  leaderboard enrichment, and LPR enrichment should reuse this service rather
  than calling the DVLA module directly.
- Manual vehicle lookup through `POST /api/v1/integrations/dvla/lookup` still
  returns `vehicle` and `display_vehicle`, and now also returns a normalized
  compliance view. Vehicle create/update endpoints continue to accept
  client-provided make/model/color and must not force an extra server-side DVLA
  lookup on save.
- LPR known-vehicle DVLA refreshes may overwrite `Vehicle.make` and
  `Vehicle.color` with official DVLA make/colour. Compliance fields are stored
  only for known vehicles.
- Do not log or expose the `x-api-key` value.
- Surface DVLA HTTP/API failures back to the caller; do not report a successful
  lookup unless DVLA returned a successful response.

## UniFi Protect Integration

Client/module:

- `backend/app/modules/unifi_protect/client.py`
- `backend/app/services/unifi_protect.py`

Managed `uiprotect` package overlays:

- `backend/app/modules/unifi_protect/package.py`
- `backend/app/services/unifi_protect_updates.py`

Configuration lives in dynamic settings; credentials and API keys are encrypted:

- `unifi_protect_host`
- `unifi_protect_port`
- `unifi_protect_username`
- `unifi_protect_password`
- `unifi_protect_api_key`
- `unifi_protect_verify_ssl`
- `unifi_protect_snapshot_width`
- `unifi_protect_snapshot_height`

Runtime behavior:

- The service starts only when UniFi Protect is configured.
- Camera bootstrap, event reads, snapshots, thumbnails, videos, and websocket
  update events are exposed through `/api/v1/integrations/unifi-protect/*`.
- Camera snapshot analysis uses the selected AI provider's image-analysis path;
  the local fallback provider cannot inspect images.
- Managed `uiprotect` updates install package overlays under
  `/app/data/unifi-protect-package` and write backups under
  `/app/data/unifi-protect-backups`.

Rules:

- Do not log UniFi Protect credentials or API keys.
- Treat package updates as state-changing Admin-only operations.
- Always create/keep a backup before applying a managed package update.
- If a package update verification fails, restore the previous package state and
  restart the UniFi Protect service.
- On service shutdown or restart, `close_unifi_protect_client` must close the
  Protect WebSocket plus every available async or sync aiohttp/session cleanup
  method. Do not leave `aiohttp` client sessions or connectors open.

## Dependency Update & Rollback Engine

Service:

- `backend/app/services/dependency_updates.py`
- `backend/app/api/v1/dependency_updates.py`

Runtime behavior:

- `packaging` is a direct backend dependency because the update service parses
  Python requirements and versions at runtime.
- The update manager is system-wide, not integration-scoped. It auto-enrolls
  external dependencies from `backend/pyproject.toml`, installed backend Python
  distributions, `frontend/package.json`, `frontend/package-lock.json`,
  Dockerfiles, and Compose.
- Enrollment runs on backend boot, after integration settings changes, and
  before manual update workflows where needed.
- Update analysis fetches registry metadata/release notes, scans local code
  usage with `rg` and parser-aware helpers, and asks the configured LLM for a
  `safe`, `warning`, or `breaking` verdict. The local provider falls back to
  conservative heuristics.
- Before any apply or restore job, the engine writes a local offline backup
  archive with manifest snapshots, encrypted dynamic setting rows, package
  artifact cache attempts, checksums, and restore metadata.
- Restore jobs first checksum and extract the archive into a temporary
  directory, then validate `backup.json`, manifest snapshots, settings
  snapshots, and offline package artifacts without network access. Live
  manifest restore only runs after that validation passes.
- Backup storage is bind-mounted in containers at `/app/update-backups` from
  `./data/backend/dependency-update-backups`.
- Remote backup storage should be mounted on the host under that bind-mounted
  path; do not introduce Docker named volumes for NFS/Samba storage.
- Update and restore jobs stream stdout/stderr over
  `WS /api/v1/dependency-updates/jobs/{job_id}/ws`, write durable logs under
  `logs/backend/dependency-updates/`, and emit telemetry/audit records in the
  `dependency_updates` category.

Rules:

- Keep update application Admin-confirmed and never silent.
- Do not expose package manager output that contains secrets; sanitize telemetry
  and audit metadata before storage.
- Verify frontend dependency updates from a clean Linux install or Docker build
  (`npm ci`/`docker compose build frontend`). Do not rely on host-mounted
  `frontend/node_modules` from inside the backend container; optional native npm
  bindings can be platform-specific.
- Do not add Docker named volumes.

## iCloud Calendar Integration

Client/module:

- `backend/app/modules/icloud_calendar/client.py`
- `backend/app/services/icloud_calendar.py`
- `backend/app/api/v1/icloud_calendar.py`

Runtime behavior:

- Multiple active iCloud Calendar accounts can be connected by Admin users.
- Setup uses `pyicloud` with a pause-and-resume six-digit verification flow.
  The backend returns `requires_2fa` plus a short-lived `handshake_id` when a
  code is needed; verify stores the encrypted trusted session bundle and clears
  the pending handshake.
- Submitted Apple passwords are used only to establish the session and must not
  be persisted.
- Trusted session/cookie bundles are encrypted with the same dynamic-secret
  crypto boundary used for other integration secrets.
- Manual sync scans all active connected accounts from today through the next
  14 days.
- Only event notes/descriptions containing the exact phrase `Open Gate`,
  case-insensitive, are processed. Event titles alone must not trigger passes.
- Matched events create/update Visitor Passes with
  `creation_source="icloud_calendar"`, visitor name extracted from the event
  title by the configured LLM with deterministic fallback parsing,
  `expected_time` from event start, `valid_from` 30 minutes before event start,
  and `valid_until` at event end.
- The original calendar title must remain in `source_metadata.event_title`; the
  extracted pass name and extraction source are stored as
  `source_metadata.visitor_name` and `source_metadata.visitor_name_source`.
- Sync is idempotent through
  `source_reference="icloud:<account_id>:<calendar_id>:<event_id>"`.
- Re-sync updates future scheduled/active calendar passes, creates new ones,
  and cancels future scheduled/active calendar passes when the source event
  disappears or loses the marker. Used and manually cancelled passes are not
  overwritten.
- Account add/remove and sync actions write audit rows and publish realtime
  `icloud_calendar.*` events. Visitor Pass changes caused by sync also publish
  the usual `visitor_pass.*` realtime events.

Routes:

- `GET /api/v1/integrations/icloud-calendar/accounts`
- `POST /api/v1/integrations/icloud-calendar/accounts/auth/start`
- `POST /api/v1/integrations/icloud-calendar/accounts/auth/verify`
- `DELETE /api/v1/integrations/icloud-calendar/accounts/{account_id}`
- `POST /api/v1/integrations/icloud-calendar/sync`

Rules:

- All iCloud Calendar routes are Admin-only.
- Return descriptive `detail` errors for authentication, unsupported challenge,
  reconnect-required, sync, and provider failures.
- Security-key-only and older two-step challenges should fail clearly rather
  than pretending setup succeeded.
- Keep all iCloud setup, verification, account management, status, and manual
  sync UI inside the API & Integrations iCloud Calendar tile modal. Do not add
  a separate Settings page or detached setup flow for this integration.

## Telemetry, Audit, Alerts, and Top Charts

Telemetry service:

- `backend/app/services/telemetry.py`
- `backend/app/api/v1/telemetry.py`

Alert snapshot service:

- `backend/app/services/alert_snapshots.py`
- Legacy compatibility over `SnapshotManager`-owned access-event snapshots and
  retained pre-refactor alert snapshot files.

Leaderboard service:

- `backend/app/services/leaderboard.py`

Runtime behavior:

- Telemetry traces/spans are stored in `telemetry_traces` and
  `telemetry_spans`; audit rows are stored in `audit_logs`.
- Categories currently include LPR Telemetry, Alfred AI Audit, System CRUD,
  Webhooks & API, Integrations, Gate Events, Maintenance Mode, Automations, and
  Access & Presence.
- HTTP middleware traces non-read-only `/api/v1/*` requests except telemetry
  endpoints, plus all webhook ingress, and emits `X-IACS-Request-ID`.
- Telemetry payloads are sanitized for secrets and large media. Raw API keys,
  auth tokens, cookies, data URLs, thumbnails, videos, and large snapshot data
  must not be stored in trace payloads.
- Telemetry artifacts can store bounded files such as camera snapshots under
  `/app/data/telemetry-artifacts` in the backend container
  (`data/backend/telemetry-artifacts/` on the host) and are served by
  admin-only artifact endpoints.
- Alerts are anomaly records with resolve/reopen actions. Unauthorized-plate
  alerts are grouped by plate/day in the API/UI and can carry retained compact
  `camera.gate` snapshot metadata from their access event pending future TTL
  purge automation.
- Top Charts aggregates known granted entries and unknown denied plates. Known
  top-spot changes publish `leaderboard_overtake` and can trigger notification
  workflows. Unknown leaderboard rows may be DVLA-enriched on read without
  persisting unknown-vehicle details.

Rules:

- Add meaningful spans around new cross-system work, especially hardware,
  provider, notification, and state-changing paths.
- Always sanitize telemetry/audit metadata before storing it.
- Use audit logs for user/Admin/Alfred-visible state changes; realtime logs are
  not durable audit history.
- Keep alert snapshot display backed by `SnapshotManager` metadata or retained
  legacy files. Do not create new alert-specific capture/compression paths.

## Alfred 2.0 AI Agent

Provider wrappers:

- `backend/app/ai/providers.py`

Agent tools:

- `backend/app/ai/tools.py`

Chat orchestration:

- `backend/app/services/chat.py`

Chat attachments:

- `backend/app/services/chat_attachments.py`

Supported provider names:

- `local`
- `openai`
- `gemini`
- `claude`
- `anthropic`
- `ollama`

Default provider:

- `llm_provider=local` by default. `IACS_LLM_PROVIDER` only seeds the initial
  dynamic setting on a fresh database.

The public providers endpoint lists `local`, `openai`, `gemini`, `claude`, and
`ollama`; `anthropic` is accepted internally as an alias for Claude. The local
provider is deterministic and requires no API keys, but it must not route
free-form Alfred chat because all chat intent/tool selection must be LLM-first.
Use it only for direct development of tool-output summarization or other
explicitly non-chat fallback tests.

Alfred 2.0 behavior:

- Alfred is the named AI operations agent in the chat UI and system prompt.
- Alfred chat is ReAct-only. Do not add legacy naive `/api/chat` handlers,
  compatibility monkeypatches, or direct prompt-to-answer shortcuts around
  `ChatService`.
- All free-form chat, regardless of entrypoint (dashboard, WebSocket, Discord,
  Slack, WhatsApp, or future providers), must run through the LLM intent router
  first so the model decides the user's intent and the tool catalog to load.
  Do not add keyword-first guided flows, slash-command shortcuts, provider
  conditionals, or deterministic phrase routers that bypass this LLM-first
  intent/tool selection path. If no LLM provider is available or the router
  fails, chat must fail closed with a clear configuration/retry message rather
  than selecting tools by phrase matching. Deterministic logic is only
  acceptable as a narrow safety guard after the LLM-selected path has produced
  a state-changing action requiring confirmation.
- WhatsApp has split-brain routing. Active Admin senders are a
  `MessagingBridgeService` entrypoint, not a separate Alfred brain: resolve the
  Admin by full normalized phone number, upsert the matching
  `messaging_identities` row, and pass
  `IncomingChatMessage(provider="whatsapp", is_direct_message=True)` into the
  shared bridge. Visitor senders must route to the restricted Visitor Concierge
  chain only.
- Tool results are the source of truth for live access state, device state,
  schedules, DVLA records, telemetry, notifications, reports, and files.
- Alfred must not invent people, vehicles, schedules, events, device states,
  IDs, telemetry, or DVLA records.
- Fuzzy references should go through `resolve_human_entity` before using exact
  person, vehicle, group, or device identifiers.
- Access causality questions should prefer `diagnose_access_event` over shallow
  event lists.
- Visitor Pass intent (`Visitor_Passes`) handles expected unknown visitors and
  visitor follow-ups. If the user says something like "I have a visitor
  coming", Alfred must gather the visitor name and expected time for one-time
  passes, or visitor name, phone number, `valid_from`, and `valid_until` for
  duration passes before preparing `create_visitor_pass`; ask concise
  follow-ups instead of guessing.
- Visitor Pass names are free-text expected visitors. Alfred must not call
  `resolve_human_entity` or check whether the visitor exists as a `Person`
  before creating a pass.
- Alfred must always interpret Visitor Pass times in local site time silently;
  never ask the user to confirm local-time details unless the date or clock time
  is missing, and never mention local-time names, labels, or UTC offsets in pass
  confirmation text.
- If the user does not specify a Visitor Pass window, Alfred must default to
  `window_minutes=30` (`+/- 30m`). Do not ask for plate, make, or colour while
  creating a pass; arrival telemetry fills those fields later.
- For questions like "What car did Sarah arrive in?" or "How long was Sarah
  here?", Alfred should use `query_visitor_passes` or `get_visitor_pass` and
  answer from the linked pass telemetry.
- For requests like "check my calendar for gate passes" or "sync iCloud
  Calendar", Alfred should use `trigger_icloud_sync`. The tool is
  state-changing because it can create, update, or cancel Visitor Passes, so it
  must return a confirmation card before running.
- Automation intent (`Automations`) handles Trigger / If / Then rules. Alfred
  should use `query_automation_catalog` before creating or editing rules, then
  resolve referenced people, vehicles, devices, and notification workflows
  before writing automation JSON.
- `query_automation_catalog` returns dynamic integration actions with
  `enabled` and `disabled_reason`. Alfred may explain disabled actions to the
  user, but should not create or enable an automation that depends on a disabled
  integration until the integration has been connected again.
- `query_automations` and `query_notification_workflows` push trigger, active,
  and search filters into SQL. Use those filters instead of fetching all rows
  and doing broad client-side scans.
- For a request like "create a rule to open the gate if Steph arrives outside
  her schedule", Alfred should resolve Steph, create a
  `vehicle.outside_schedule` trigger filtered by `person_id` or `vehicle_id`,
  and add a `gate.open` action with an operator-readable `reason_template`.
- State-changing tools return `requires_confirmation` first. The chat UI stores
  a pending action in chat session context, renders confirm/cancel controls,
  and calls `/api/v1/ai/chat/confirm` or sends `tool_confirmation` on the chat
  WebSocket before the tool runs with `confirm=true` or `confirm_send=true`.
- Discord buttons and WhatsApp interactive reply buttons must both resolve the
  existing pending `session_id` and `confirmation_id`; do not create a separate
  confirmation store per messaging provider.
- Pending action confirmations expire after 10 minutes and are bound to the
  session/user that created them.
- Alfred tool calls are audited as `alfred.tool.<tool>` with actor
  `Alfred_AI`, provider/model/session context, sanitized arguments, and
  outcomes including `pending_confirmation`.
- Parallel tool batches must await every tool with a per-tool timeout and
  return failed tool results for slow integrations rather than hanging the whole
  response.
- Alfred can upload/read attachments. Uploads are limited to 25 MB and currently
  accept images, text/CSV/Markdown/XML/HTML/JSON, PDFs, and DOCX files.
- Alfred-generated CSV/PDF/snapshot outputs are returned as secure chat
  attachments and stored under `data/chat_attachments/`.
- Camera image analysis and camera snapshots must fetch live media through the
  UniFi Protect service. Snapshot attachments may be stored as chat or telemetry
  artifacts; do not add broad snapshot retention without an explicit feature.

Current Alfred tools:

- General/entity resolution: `resolve_human_entity`, `get_system_users`.
- Access and diagnostics: `query_presence`, `query_access_events`,
  `diagnose_access_event`, `query_lpr_timing`,
  `query_vehicle_detection_history`, `get_telemetry_trace`,
  `query_anomalies`, `summarize_access_rhythm`,
  `calculate_visit_duration`, `query_leaderboard`.
- Gate/hardware and maintenance: `query_device_states`,
  `get_maintenance_status`, `get_active_malfunctions`,
  `get_malfunction_history`, `trigger_manual_malfunction_override`,
  `open_device`, `command_device`, `open_gate`, `enable_maintenance_mode`,
  `disable_maintenance_mode`, `toggle_maintenance_mode`.
- Schedules: `query_schedules`, `get_schedule`, `create_schedule`,
  `update_schedule`, `delete_schedule`, `query_schedule_targets`,
  `assign_schedule_to_entity`, `verify_schedule_access`,
  `override_schedule`.
- Visitor Passes: `query_visitor_passes`, `get_visitor_pass`,
  `create_visitor_pass`, `update_visitor_pass`, `cancel_visitor_pass`.
- Calendar integrations: `trigger_icloud_sync`.
- Notifications: `query_notification_catalog`,
  `query_notification_workflows`, `get_notification_workflow`,
  `create_notification_workflow`, `update_notification_workflow`,
  `delete_notification_workflow`, `preview_notification_workflow`,
  `test_notification_workflow`, `trigger_anomaly_alert`.
- Automations: `query_automation_catalog`, `query_automations`,
  `get_automation`, `create_automation`, `edit_automation`,
  `delete_automation`, `enable_automation`, `disable_automation`.
- Compliance and cameras: `lookup_dvla_vehicle`, `analyze_camera_snapshot`,
  `get_camera_snapshot`.
- Files and reports: `read_chat_attachment`, `export_presence_report_csv`,
  `generate_contractor_invoice_pdf`.

Current tool behavior:

- `trigger_icloud_sync` is state-changing because it can create, update, expire,
  or cancel Visitor Pass rows from calendar events. It must require
  confirmation before `ICloudCalendarService.sync_all()` runs.
- Notification workflow create/update/delete/test tools must preserve DB-backed
  `notification_rules` semantics. Test sends require confirmation because they
  can deliver real mobile, in-app, WhatsApp, Discord, or voice messages.
- Automation create/edit/enable/disable/delete tools must require confirmation.
  Dry-runs and catalog queries are read-only and must not execute actions or
  mutate integration sync state.
- Hardware tools (`open_gate`, `open_device`, `command_device`) and
  Maintenance Mode toggles must include explicit target/context and
  confirmation before real-world commands are sent.

State-changing Alfred tools:

- `assign_schedule_to_entity`, `create_notification_workflow`,
  `create_automation`, `create_schedule`, `create_visitor_pass`,
  `cancel_visitor_pass`, `delete_automation`, `delete_notification_workflow`,
  `delete_schedule`, `disable_automation`, `disable_maintenance_mode`,
  `edit_automation`, `enable_automation`, `enable_maintenance_mode`,
  `command_device`, `open_gate`, `open_device`, `override_schedule`,
  `trigger_anomaly_alert`,
  `trigger_manual_malfunction_override`, `test_notification_workflow`,
  `toggle_maintenance_mode`, `update_notification_workflow`, `update_schedule`,
  and `update_visitor_pass`.

Memory:

- Chat sessions persist in `chat_sessions`; this is active Alfred/ReAct memory,
  not legacy dead code.
- Chat messages persist in `chat_messages`; this is active Alfred/ReAct memory,
  not legacy dead code.
- Session context tracks recent subject/person/group/visitor, guided schedule
  and Visitor Pass setup state, and pending confirmation actions.
- Example: after "Did the gardener arrive today?", "How long did they stay?"
  resolves to the gardener context.

Rules:

- Add new tools in `app/ai/tools.py` with a strict JSON schema, compact
  JSON-serializable output, categories, and accurate read-only/confirmation
  metadata.
- Keep high-risk actions such as gate/door control, maintenance changes,
  workflow edits, notification sends, and schedule overrides explicit,
  confirmed, and audited before exposing them as casual chat actions.
- Keep providers from seeing secrets or large raw payloads. Use telemetry
  sanitization and compact tool outputs.

## API Surface

Base API prefix:

```text
/api/v1
```

Health:

- `GET /` (service identification only; excluded from OpenAPI)
- `GET /health` (container/LAN health only; excluded from OpenAPI)
- `GET /api/v1/health`

Auth:

- `GET /api/v1/auth/status`
- `POST /api/v1/auth/setup`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`
- `PATCH /api/v1/auth/me/preferences`

Users:

- `GET /api/v1/users`
- `POST /api/v1/users`
- `PATCH /api/v1/users/{user_id}`
- `POST /api/v1/users/{user_id}/reset-password`
- `DELETE /api/v1/users/{user_id}`

Directory:

- `GET /api/v1/people`
- `POST /api/v1/people`
- `PATCH /api/v1/people/{person_id}`
- `GET /api/v1/vehicles`
- `POST /api/v1/vehicles`
- `PATCH /api/v1/vehicles/{vehicle_id}`
- `POST /api/v1/vehicles/{vehicle_id}/dvla-refresh`
- `DELETE /api/v1/vehicles/{vehicle_id}`
- `GET /api/v1/groups`
- `POST /api/v1/groups`
- `PATCH /api/v1/groups/{group_id}`

Schedules:

- `GET /api/v1/schedules`
- `POST /api/v1/schedules`
- `GET /api/v1/schedules/{schedule_id}`
- `PATCH /api/v1/schedules/{schedule_id}`
- `GET /api/v1/schedules/{schedule_id}/dependencies`
- `DELETE /api/v1/schedules/{schedule_id}`

Visitor Passes:

- `GET /api/v1/visitor-passes`
- `POST /api/v1/visitor-passes`
- `GET /api/v1/visitor-passes/{pass_id}`
- `GET /api/v1/visitor-passes/{pass_id}/whatsapp-messages`
- `GET /api/v1/visitor-passes/{pass_id}/logs`
- `PATCH /api/v1/visitor-passes/{pass_id}`
- `POST /api/v1/visitor-passes/{pass_id}/cancel`
- `DELETE /api/v1/visitor-passes/{pass_id}`

Settings:

- `GET /api/v1/settings`
- `GET /api/v1/settings/runtime`
- `PATCH /api/v1/settings`
- `POST /api/v1/settings/test`

Notifications:

- `GET /api/v1/notifications/catalog`
- `GET /api/v1/notifications/rules`
- `POST /api/v1/notifications/rules`
- `GET /api/v1/notifications/rules/{rule_id}`
- `PATCH /api/v1/notifications/rules/{rule_id}`
- `DELETE /api/v1/notifications/rules/{rule_id}`
- `POST /api/v1/notifications/rules/preview`
- `POST /api/v1/notifications/rules/test`
- `POST /api/v1/notifications/rules/{rule_id}/test`

Automations:

- `GET /api/v1/automations/catalog`
- `GET /api/v1/automations/rules`
- `POST /api/v1/automations/rules`
- `GET /api/v1/automations/rules/{rule_id}`
- `PATCH /api/v1/automations/rules/{rule_id}`
- `DELETE /api/v1/automations/rules/{rule_id}`
- `POST /api/v1/automations/rules/{rule_id}/dry-run`
- `POST /api/v1/automations/dry-run`
- `POST /api/v1/automations/parse-schedule`
- `POST /api/v1/automations/webhooks/{webhook_key}` public webhook ingress

Events and presence:

- `GET /api/v1/events`
- `GET /api/v1/presence`
- `GET /api/v1/alerts`
- `PATCH /api/v1/alerts/action`
- `GET /api/v1/alerts/{alert_id}/snapshot`
- `GET /api/v1/anomalies`

Top Charts:

- `GET /api/v1/leaderboard`

Diagnostics:

- `GET /api/v1/diagnostics/lpr-timing`
- `DELETE /api/v1/diagnostics/lpr-timing`

Maintenance:

- `GET /api/v1/maintenance/status`
- `POST /api/v1/maintenance/enable`
- `POST /api/v1/maintenance/disable`

Gate malfunctions:

- `GET /api/v1/gate-malfunctions/active`
- `GET /api/v1/gate-malfunctions/history`
- `GET /api/v1/gate-malfunctions/{malfunction_id}/trace`
- `POST /api/v1/gate-malfunctions/{malfunction_id}/override`

Telemetry and audit:

- `GET /api/v1/telemetry/categories`
- `GET /api/v1/telemetry/traces`
- `GET /api/v1/telemetry/traces/{trace_id}`
- `GET /api/v1/telemetry/audit`
- `DELETE /api/v1/telemetry/purge`
- `GET /api/v1/telemetry/artifacts/{artifact_id}`

Dependency updates:

- `GET /api/v1/dependency-updates/packages`
- `POST /api/v1/dependency-updates/sync`
- `POST /api/v1/dependency-updates/packages/{dependency_id}/check`
- `POST /api/v1/dependency-updates/packages/{dependency_id}/analyze`
- `POST /api/v1/dependency-updates/packages/{dependency_id}/apply`
- `GET /api/v1/dependency-updates/packages/{dependency_id}/backups`
- `GET /api/v1/dependency-updates/backups`
- `POST /api/v1/dependency-updates/backups/{backup_id}/restore`
- `GET /api/v1/dependency-updates/jobs/{job_id}`
- `GET /api/v1/dependency-updates/storage/status`
- `POST /api/v1/dependency-updates/storage/validate`
- `POST /api/v1/dependency-updates/storage/config`
- `WS /api/v1/dependency-updates/jobs/{job_id}/ws`

Webhooks:

- `POST /api/v1/webhooks/ubiquiti/lpr`
- `GET /api/v1/webhooks/whatsapp` public Meta verification challenge
- `POST /api/v1/webhooks/whatsapp` public WhatsApp message/status ingress

Simulation:

- `POST /api/v1/simulation/arrival/{registration_number}`
- `POST /api/v1/simulation/misread-sequence/{registration_number}`

Realtime:

- `WS /api/v1/realtime/ws`

Integrations:

- `GET /api/v1/integrations/home-assistant/status`
- `GET /api/v1/integrations/home-assistant/entities`
- `POST /api/v1/integrations/home-assistant/gates/auto-detect`
- `POST /api/v1/integrations/home-assistant/garage-doors/auto-detect`
- `GET /api/v1/integrations/apprise/urls`
- `POST /api/v1/integrations/apprise/urls`
- `DELETE /api/v1/integrations/apprise/urls/{index}`
- `POST /api/v1/integrations/home-assistant/mobile-notifications/test`
- `POST /api/v1/integrations/gate/open`
- `POST /api/v1/integrations/cover/command`
- `POST /api/v1/integrations/announcements/say`
- `POST /api/v1/integrations/notifications/test`
- `POST /api/v1/integrations/dvla/lookup`
- `GET /api/v1/integrations/icloud-calendar/accounts`
- `POST /api/v1/integrations/icloud-calendar/accounts/auth/start`
- `POST /api/v1/integrations/icloud-calendar/accounts/auth/verify`
- `DELETE /api/v1/integrations/icloud-calendar/accounts/{account_id}`
- `POST /api/v1/integrations/icloud-calendar/sync`
- `GET /api/v1/integrations/whatsapp/status`
- `GET /api/v1/integrations/whatsapp/admin-targets`
- `POST /api/v1/integrations/whatsapp/test`

UniFi Protect:

- `GET /api/v1/integrations/unifi-protect/status`
- `GET /api/v1/integrations/unifi-protect/cameras`
- `GET /api/v1/integrations/unifi-protect/events`
- `GET /api/v1/integrations/unifi-protect/cameras/{camera_id}/snapshot`
- `POST /api/v1/integrations/unifi-protect/cameras/{camera_id}/analyze`
- `GET /api/v1/integrations/unifi-protect/events/{event_id}/thumbnail`
- `GET /api/v1/integrations/unifi-protect/events/{event_id}/video`
- `GET /api/v1/integrations/unifi-protect/update/status`
- `POST /api/v1/integrations/unifi-protect/update/analyze`
- `POST /api/v1/integrations/unifi-protect/update/apply`
- `GET /api/v1/integrations/unifi-protect/backups`
- `POST /api/v1/integrations/unifi-protect/backups`
- `GET /api/v1/integrations/unifi-protect/backups/{backup_id}/download`
- `POST /api/v1/integrations/unifi-protect/backups/{backup_id}/restore`
- `DELETE /api/v1/integrations/unifi-protect/backups/{backup_id}`

AI:

- `GET /api/v1/ai/providers`
- `GET /api/v1/ai/tools`
- `POST /api/v1/ai/chat`
- `POST /api/v1/ai/chat/confirm`
- `POST /api/v1/ai/chat/upload`
- `GET /api/v1/ai/chat/files/{file_id}`
- `WS /api/v1/ai/chat/ws`

Frontend Nginx proxies these under the same host at `:8089`.

Legacy non-versioned API aliases have been removed; the latest route audit found
no active `/api/...` aliases outside versioned `/api/v1/...` paths. New routes
and clients must use `/api/v1/...` so OpenAPI and runtime routing have a single
source of truth.

## Frontend Architecture

Frontend app:

- `frontend/src/main.tsx`
- `frontend/src/VariableRichTextEditor.tsx`
- `frontend/src/styles.css`

Runtime:

- Build with Vite.
- Serve static assets with Nginx.
- Proxy `/api/*` to backend.
- Use relative URLs from the browser so LAN and NPM deployments work without
  rebuild-time API URLs.

Current frontend views:

- Dashboard
- People
- Groups
- Schedules
- Passes
- Vehicles
- Top Charts
- Events
- Alerts
- Reports
- API & Integrations
- Logs / Telemetry & Audit
- Settings
- Settings / General
- Settings / Auth & Security
- Settings / Notifications
- Settings / Automations
- Settings / LPR Tuning
- Settings / Users

Current frontend integration/editor surfaces:

- API & Integrations manages Home Assistant, iCloud Calendar, Apprise, DVLA,
  UniFi Protect, LLM providers, and the system-wide dependency update hub.
- API & Integrations has top-level `Integrations` and `Updates` tabs. The
  `Updates` tab lists enrolled dependencies, LLM analysis, backup history,
  storage status/configuration, and live update/restore job output.
- The iCloud Calendar tile opens `ICloudCalendarModal` behavior inside the
  existing integration modal shell. It contains the overview/status header,
  connected account list, inline Add Account stepper, six-digit code step,
  remove action, manual Sync Calendars Now action, and recent sync summary.
  Keep future iCloud Calendar setup/account management inside this tile modal.
- Home Assistant settings include setup/discovery, gates, garage doors, media
  players, and mobile-app notification service discovery. Do not add a Home
  Assistant presence-mapping UI; IACS presence is LPR-derived.
- UniFi Protect settings include general config, exposed camera/entity state,
  camera media/snapshot analysis, and managed update/backup controls.
- The WhatsApp integration tile belongs under notification providers. Its modal
  must include enable/disable, Meta Cloud API credentials, webhook verify token,
  optional app secret, Graph API version, Visitor Pass outreach template
  name/language, and the read-only IACS webhook URL.
- Settings / Notifications is the notification workflow builder with
  trigger/condition/action editing, endpoint pickers, snapshot-media toggles,
  live preview, and Tiptap `@Variable` insertion.
- Notification WhatsApp actions let operators select Admin targets and add
  dynamic `whatsapp:number:@Variable` destinations.
- Settings / Automations is the Automation Engine builder. Keep it visually
  aligned with Settings / Notifications: vertical When / If / Then flow,
  categorized trigger/condition/action modals, dry-run preview, and trigger
  scoped `@Variable` insertion.
- Automation builder template/reason fields must use
  `PlainTemplateEditor`, `SafeVariableRichTextEditor`, or an equivalent error
  boundary with the plain text fallback. Do not mount the lazy
  `VariableRichTextEditor` directly inside automation cards or modals; editor
  render failures previously blanked the app when selecting actions such as
  iCloud Calendar Sync Calendars Now.
- The Automations action picker includes an Integrations category. It drills
  down from Integrations to providers such as iCloud Calendar, then to
  registered provider actions such as Sync Calendars Now. Disabled provider
  actions remain visible with their disabled reason and cannot be selected.
- The WhatsApp automation action editor must expose target mode
  (all/selected/dynamic), Admin-user chips for selected mode, a dynamic phone
  template for dynamic mode, and a message template rendered from automation
  variables.
- Vehicles edit modal includes a display-only DVLA Compliance card for MOT
  status/expiry, tax status/expiry, and last DVLA sync date. Make and colour
  remain on the main vehicle details fields. Registration edits still use the
  manual DVLA auto-fill flow, and saved vehicles have a manual DVLA refresh
  action.
- People edit modal supports assigned garage doors and Home Assistant
  `notify.mobile_app_*` linking with a test-send action.
- Dashboard includes Maintenance Mode state/control, configured gate and garage
  door controls with confirmation modals, alert summary, and live site status.
- Alerts view groups same-day unresolved unauthorized-plate alerts by
  registration, supports resolve/reopen actions, and displays captured
  snapshots when available.
- Top Charts shows known granted-entry leaders and denied unknown Mystery Guest
  leaders, with optional DVLA enrichment for unknown plates.
- Passes view (`/passes`) is the Visitor Pass management hub. It defaults to
  Active + Scheduled filters, supports multi-select All/Active/Scheduled/Used/
  Expired/Cancelled status filters, and provides `+ Visitor Pass` creation with
  visitor name, custom calendar/time picker, and preset +/- 30/60/90/120/180
  minute windows. Pass cards open a detail modal; edit, cancel, and hard-delete
  actions live in that modal. Used pass cards must show captured vehicle
  summary such as `Silver Ford - PE70DHX` plus duration/departure data when
  available. Duration pass detail modals include a WhatsApp tab showing the
  stored two-sided Visitor Concierge transcript in a scrollable message-bubble
  layout that opens at the latest message and smoothly scrolls new messages
  into view. Detail modals also include a Log tab backed by pass-specific audit
  rows; it must show date/time changes, source/actor context such as Jason in
  UI, Jason via Alfred, or Visitor via WhatsApp, and approval decisions for
  Visitor Concierge timeframe requests.
- Logs includes realtime event logs plus Telemetry & Audit tabs for LPR, gate
  events, maintenance, AI audit, CRUD, API, integrations, Updates & Rollbacks,
  and access traces.

Current realtime behavior:

- `WebSocket /api/v1/realtime/ws` receives system events and refreshes data.
- Logs view displays event bus messages.
- Global Alfred chat uses `WebSocket /api/v1/ai/chat/ws`, streams thinking,
  tool batch/status, confirmation-required, response-delta, response, and error
  events, and supports attachment upload/download through HTTP endpoints.
- Dependency update job panels keep one managed
  `WS /api/v1/dependency-updates/jobs/{job_id}/ws` connection per active job,
  close it on job completion, job change, or component unmount, and retain only
  bounded/truncated job events in React state.
- In-app notification rules publish realtime toast payloads; the header alert
  tray tracks unresolved alert-style anomalies.
- WebSockets authenticate with the same HTTP-only session cookie or an explicit
  Bearer/query token.

## UI Design Rules

Follow the established Modern SaaS Clean operational style:

- Fixed left sidebar on desktop.
- Main content uses bento-box cards.
- Keep card radius at `8px`.
- Use thin borders and restrained shadows.
- Use high-legibility sans-serif typography through the system stack.
- Use lucide-style icons.
- Use pill status badges.
- Badge/pill text must remain vertically centered and content-sized. When
  styling tables, rows, cards, or generic `span` elements, explicitly avoid
  overriding `.badge` display behavior; scoped row rules like `row span {
  display: block; }` have caused regressions before.
- In schedule cards specifically, never style `.schedule-card-main span`
  broadly. The hours pill is a `.badge` rendered as a `span`; use
  `.schedule-card-main div > span` for description text and keep
  `.schedule-card-main .badge` as `display: inline-flex` with centered content.
- In integration category headers, never style `.integration-category-header
  span` broadly. Scope title copy to `.integration-category-title span` so
  header controls, select labels, and count badges remain aligned and
  content-sized.
- Use neutral foundation colors.
- Reserve color for function:
  - Blue: primary actions and live markers.
  - Green: active, present, granted.
  - Gray: exited, inactive, unknown.
  - Amber: warning.
  - Red: critical, denied.
- Support light, dark, and system themes.
- Default theme is system.
- Keep screens useful and data-oriented.
- Do not add landing pages, marketing heroes, decorative gradient blobs, or
  large illustrative empty surfaces.
- Do not nest cards inside cards.
- Keep text inside buttons/cards from overflowing on mobile.
- Keep fixed-format controls stable so hover/loading states do not shift layout.

Accepted Phase 5 concept reference:

```text
/Users/jas/.codex/generated_images/019dbc4e-2adb-7a20-8ccf-4f6a46682281/ig_05dd6360a0b3f49b0169eaa033eb908191b3423ff819aa3979.png
```

This concept is a reference for layout density, palette, hierarchy, and card
topology. Do not replace it with a marketing dashboard style.

## Adding New Modules

### New LPR Adapter

1. Add a file under `backend/app/modules/lpr/`, for example:
   `backend/app/modules/lpr/axis.py`.
2. Implement an adapter that normalizes vendor payloads to `PlateRead`.
3. Keep vendor-specific validation and aliases in that module.
4. Register the adapter in `backend/app/modules/registry.py` if runtime
   selection is required.
5. Add a route only if the vendor needs a unique webhook payload endpoint.
6. Do not alter `AccessEventService` unless the normalized `PlateRead` contract
   truly needs to change.

### New Gate Controller

1. Add a file under `backend/app/modules/gate/`.
2. Implement the `GateController` protocol from `modules/gate/base.py`.
3. Return `GateCommandResult`.
4. Register it in `modules/registry.py` if configurable.
5. Keep physical I/O, credentials, and vendor mapping inside the module.

### New Notification Sender

1. Add a file under `backend/app/modules/notifications/`.
2. Implement `NotificationSender`.
3. Keep `NotificationContext` as the input contract.
4. Do not pass raw database models or free-form logs into sender modules.

### New Notification Trigger or Variable

1. Add trigger metadata or variable metadata in `backend/app/services/notifications.py`.
2. Populate trigger facts from structured sources such as `AccessEvent`,
   `Person`, `Vehicle`, Home Assistant covers, or UniFi Protect payloads.
3. Keep templates based on `@Variable` tokens.
4. Add or adjust notification workflow tests when rendering or delivery changes.

### New UniFi Protect Capability

1. Keep console I/O in `backend/app/modules/unifi_protect/client.py` or a
   sibling module.
2. Route orchestration through `backend/app/services/unifi_protect.py` or
   `backend/app/services/unifi_protect_updates.py`.
3. Keep credential handling in dynamic settings and never expose secrets in
   status responses.
4. Use `/app/data/...` for package overlays, backups, or retained artifacts.

### New AI Tool

1. Add an `AgentTool` in `backend/app/ai/tools.py`.
2. Provide a strict JSON schema in `parameters`.
3. Add category, `read_only`, confirmation, and default-limit metadata through
   `_with_tool_metadata`.
4. Keep side effects explicit and auditable. If the tool changes state, sends
   alerts/tests, opens/closes hardware, or edits workflows/schedules, return
   `requires_confirmation` before acting and support a clear confirmation flag.
5. Return compact JSON-serializable output; summarize, redact, or omit large
   raw payloads and media.
6. Update the local-provider summarizer and chat confirmation display helpers
   when the tool has a user-facing result or confirmation card.
7. Add/adjust tests in `backend/tests/test_chat_agent.py` and telemetry/audit
   tests when routing, confirmation, or auditing changes.

## Development Commands

Run the full system:

```bash
cp .env.example .env
mkdir -p data/backend data/chat_attachments data/postgres data/redis logs/backend logs/frontend
docker compose up --build
```

Backend syntax check:

```bash
python3 -m compileall backend/app
```

Frontend install/build:

```bash
cd frontend
npm ci
npm run build
```

After changing frontend code or other container-baked assets, rebuild and
restart the affected container before handing back for inspection. For dashboard
changes, run:

```bash
docker compose up -d --build frontend
```

After backend code changes, restart the backend container so the bind-mounted
app reloads cleanly:

```bash
docker compose restart backend
```

Compose validation:

```bash
docker compose config
docker compose ps
```

Useful smoke checks:

```bash
curl -fsS http://localhost:8089/api/v1/health
curl -fsS http://localhost:8089/api/v1/auth/status
curl -fsS http://localhost:8089/setup
curl -fsS http://localhost:8089/api/v1/presence
curl -fsS http://localhost:8089/api/v1/events?limit=5
curl -fsS http://localhost:8089/api/v1/schedules
curl -fsS http://localhost:8089/api/v1/maintenance/status
curl -fsS http://localhost:8089/api/v1/leaderboard
curl -fsS -X POST http://localhost:8089/api/v1/simulation/misread-sequence/TEST123
curl -fsS -X POST http://localhost:8089/api/v1/ai/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Summarize today"}'
```

When no Admin exists yet, protected API checks should return HTTP 428 with
`setup_required: true`. After setup, run protected smoke checks from a browser
session or with the `iacs_session` cookie.

## Reverse Proxy Notes

Preferred NPM target is frontend:

```text
http://<docker-host-ip>:8089
```

Why:

- The frontend serves the SPA.
- The frontend proxies backend API and WebSockets internally.
- Users need only one public host.

Enable:

- WebSocket support.
- Standard `X-Forwarded-*` headers.
- TLS termination at NPM if desired.

Only point NPM directly at backend `8088` for API-only debugging.

## Security and Safety Notes

Local dashboard authentication is implemented with first-run Admin setup,
Argon2 password hashes, HTTP-only signed cookies, protected HTTP APIs, and
protected WebSockets. Do not expose the service publicly without TLS and a
strong `IACS_AUTH_SECRET_KEY`; NPM TLS termination is the expected deployment
path.

Sensitive bootstrap configuration belongs in `.env`, not source files:

- `IACS_AUTH_SECRET_KEY`.
- Database credentials.
- Proxy and trusted host settings.

Sensitive integration configuration belongs in the encrypted `system_settings`
table and is managed through the Settings/API UI, not `.env`:

- Home Assistant tokens.
- Apprise URLs.
- DVLA API keys.
- UniFi Protect credentials and API keys.
- OpenAI/Gemini/Anthropic API keys.

Gate-opening and notification actions are real-world effects. Keep endpoint
names explicit, include a reason/context, and do not hide these behind ambiguous
UI or chat flows.

## Error Propagation Rule

Never report success for an operation unless the underlying adapter or provider
actually accepted and completed it. This is especially important for
integrations, notifications, gate commands, AI provider tests, and any
state-changing action.

Backend rules:

- Do not silently swallow integration errors and then return `200 OK`.
- If an adapter returns a false/failed result, convert it to a clear exception
  or non-2xx API response with a useful `detail` message.
- Connection-test endpoints must validate the real provider behavior whenever
  feasible, not only check that fields are non-empty.
- Optional background notifications may log and publish `notification.failed`,
  but user-triggered tests must raise failures back to the UI.

Frontend rules:

- Display success, progress, info, and error states with distinct visual
  treatments.
- Keep user-triggered test results visible long enough for the user to read
  them; do not clear feedback just because realtime refresh data arrived.
- Treat `{ ok: false }` responses and non-2xx responses as failures and show the
  provider message.

## Implementation State

Completed:

- Compose deployment, backend scaffold, bind-mounted runtime storage, and
  versioned `/api/v1` API surface.
- SQLAlchemy models, auto-create bootstrap, transitional column/index additions,
  and legacy schedule-table cleanup.
- Ubiquiti LPR ingestion, debounce/canonicalization, top-gate-state direction
  inference, timing diagnostics, telemetry, DVLA enrichment, presence, alerts,
  and notification dispatch.
- Home Assistant gate/garage/door state sync, TTS, mobile app notifications,
  Maintenance Mode sync, and schedule-gated cover commands.
- Apprise, in-app, Home Assistant mobile, and Home Assistant voice notification
  workflows with DB-backed rules, Tiptap variables, conditions, media snapshots,
  preview, and test sends.
- WhatsApp Cloud API integration with encrypted dynamic settings, setup modal,
  webhook verification/signature validation, Admin-only Alfred routing,
  interactive confirmations, notification channel, and Automation Engine send
  action.
- DVLA encrypted settings, connection tests, manual lookup, saved-vehicle
  refresh, Alfred tool access, LPR compliance refresh, and Top Charts
  enrichment.
- UniFi Protect dynamic settings, camera/event discovery, media endpoints,
  Alfred and access-pipeline snapshot analysis, realtime update events, and
  managed `uiprotect` package update/backup workflow.
- iCloud Calendar integration with multi-account encrypted sessions, six-digit
  verification handshake, modal-contained setup, manual/Alfred sync, and
  automated asymmetric-window Visitor Pass creation.
- Alfred 2.0 providers, intent routing, tools, memory, file attachments,
  generated reports, confirmation cards, audit logging, and WebSocket streaming.
- ReAct-only chat cleanup with no legacy naive chat route aliases; the
  `chat_sessions` and `chat_messages` tables remain active memory stores.
- Telemetry & Audit: traces, spans, audit logs, sanitized payloads, artifacts,
  API/webhook middleware, gate malfunction traces, and dashboard review UI.
- Maintenance Mode and gate malfunction detection/recovery with persistent
  timelines, milestone notifications, retry outbox, and admin overrides.
- Top Charts, grouped Alerts review, resolve/reopen flows, and retained compact
  snapshots pending future TTL purge.
- User Management & Auth: first-run Admin setup, login/logout/me,
  admin-protected user CRUD, protected APIs/WebSockets, and `get_system_users`
  AI tool.
- Dynamic Configuration: database-backed settings, encrypted secrets,
  settings pages, integration tiles, and connection-test API.
- Directory Management: CRUD for people, vehicles, groups, reusable schedules,
  profile/vehicle photos, notes/descriptions, garage-door assignment, mobile
  app notify mapping, schedule overrides, and saved-vehicle DVLA refresh.
- Dependency Update stock check: direct `packaging` dependency, clean Linux
  frontend verification requirement, backup validation before restore, and
  bounded job WebSocket handling.
- Visitor Pass index coverage for open departure lookups and iCloud Calendar
  active/scheduled reconciliation.
- UniFi Protect shutdown cleanup for WebSocket/session/aiohttp resources.

Still pending or intentionally incomplete:

- Person and group deletion/archive workflows.
- Alembic migrations.
- Production worker process separation for queues.
- Real log rotation controls in UI.
- Optional dedicated entry/exit camera support beyond the current top-gate-state
  inference and `camera.gate` tie-breaker.
- Fine-grained per-tool policy controls beyond the current authenticated-user,
  Admin-only endpoint, confirmation-card, and audit-log protections.

## Documentation Map

- `README.md`: quick run and phase endpoint summary.
- `docs/phase-1.md`: initial Compose/backend scaffold.
- `docs/phase-2.md`: models and LPR debounce.
- `docs/phase-3.md`: Home Assistant and notifications.
- `docs/phase-4.md`: AI providers, tools, memory.
- `docs/phase-5.md`: frontend, NPM target, UI verification.
- `docs/phase-6.md`: original agent-guide phase note.
- `AGENTS.md`: authoritative orientation for future agents.
