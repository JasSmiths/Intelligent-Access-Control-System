# Intelligent Access Control System: Agent Guide

This file is the first thing future AI agents should read before changing this
repository. It captures the architecture, current implementation state,
extension rules, API surface, and UI design constraints.

## Project Intent

The system is a modular Intelligent Access Control and Presence System for a
family/private site. It ingests License Plate Recognition events, resolves noisy
reads, tracks presence, detects anomalies, coordinates gate/audio/notification
integrations, and exposes a realtime dashboard plus AI chat agent.

Primary goals:

- Run the full system with `docker compose`.
- Use host directory bind mounts only. Do not introduce Docker named volumes.
- Keep hardware and third-party I/O modular and swappable.
- Keep dashboards, logs, presence, and chat realtime through WebSockets.
- Make the frontend a usable operational console, not a marketing page.

## Current Stack

- Deployment: Docker Compose.
- Backend: Python 3.12, FastAPI, SQLAlchemy async, PostgreSQL, Redis.
- Frontend: React 18, TypeScript, Vite, Nginx.
- Icons: `lucide-react`.
- Rich notification-template editing: Tiptap.
- Notifications: Apprise.
- Home Assistant: REST service calls plus WebSocket state listener.
- DVLA: Vehicle Enquiry Service API lookup integration.
- UniFi Protect: `uiprotect` camera/event/snapshot integration plus managed
  package update overlays.
- LLM providers: local fallback, OpenAI Responses API, Gemini, Claude, Ollama.

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

## Docker and Storage Rules

The project intentionally uses bind mounts:

- `./data/backend:/app/data`
- `./logs/backend:/app/logs`
- `./backend/app:/app/app:ro`
- `./logs/frontend:/var/log/nginx`
- `./data/postgres:/var/lib/postgresql/data`
- `./data/redis:/data`

Do not add Docker named volumes. Keep any new persistent runtime data under
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
│   └── phase-5.md
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
- `app/workers`: future worker package placeholder.

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
- `people`: driver/person profiles.
- `vehicles`: one person to many vehicles. Stores canonical UI make/model/color
  plus DVLA compliance/cache fields `mot_status`, `tax_status`, `mot_expiry`,
  `tax_expiry`, and `last_dvla_lookup_date`.
- `schedules`: reusable weekly access windows assigned directly to people,
  vehicles, notification conditions, and configured door entities.
- `notification_rules`: DB-backed notification workflows with triggers,
  conditions, actions, active state, and templated content.
- `presence`: current person presence state.
- `access_events`: final entry/exit/denial records.
- `anomalies`: unauthorized plates, duplicate states, outside schedule.
- `users`: local dashboard accounts, roles, status, hashed passwords, UI
  preferences.
- `system_settings`: database-backed dynamic settings. Secret values are
  encrypted at rest with a Fernet key derived from `IACS_AUTH_SECRET_KEY`.
- `chat_sessions`: persistent AI chat sessions.
- `chat_messages`: persisted AI chat messages.

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
- Integrations: Home Assistant, Apprise, DVLA Vehicle Enquiry Service, UniFi
  Protect.
- LLM providers: active provider, timeout, base URLs, models, API keys.

Backend service:

- `backend/app/services/settings.py`
- `backend/app/core/crypto.py`
- `backend/app/api/v1/settings.py`

Do not add new provider tokens or operational tuning values back to
`docker-compose.yml` or `.env.example`. Add them to the dynamic settings seed
and UI instead. Secret setting keys must be added to `SECRET_KEYS`; current
secret dynamic settings include Home Assistant token, Apprise URLs, DVLA API
key, UniFi Protect username/password/API key, and LLM provider API keys.

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
  Ubiquiti webhook ingestion paths remain public.
- Admin-only user CRUD prevents deleting, demoting, or deactivating the last
  active Admin.
- Sidebar collapse preference is persisted to `users.preferences`.

Route aliases exist under both `/api/v1/auth` and `/api/auth`, and both
`/api/v1/users` and `/api/users`, so older clients can use the shorter path.

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
  vehicles, and assigned garage-door entity IDs.
- Vehicles can store photo data, make, model, colour, description, active state,
  owner, and schedule.
- Vehicles can be deleted; people and groups currently have create/update flows
  but no delete/archive endpoint.
- Schedule deletion is blocked while the schedule is referenced by people,
  vehicles, or configured Home Assistant door entities.

Schedule rules:

- `schedules.time_blocks` stores Monday-first weekly intervals normalized to
  30-minute boundaries.
- If no schedule applies, `schedule_default_policy` controls allow/deny.
- There is no legacy time-slot fallback.

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

- Queue every plate read.
- Accept Ubiquiti Alarm Manager test webhook payloads and publish
  `webhook.test.received` without creating an access event.
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
  telemetry span payloads.
- DVLA enrichment failures are non-blocking: log/publish sanitized telemetry and
  continue access event persistence, presence updates, gate/garage commands, and
  existing notifications.
- Expired MOT/tax compliance is advisory only. It can emit notification
  workflow triggers but must not deny access or block gate/garage commands.
- Persist one final `access_event`.
- Update presence if access is granted.
- Create anomalies for unauthorized plates, outside schedule, duplicate entry,
  and duplicate exit.
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
- `site_timezone`
- `schedule_default_policy`

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

Configuration lives in dynamic encrypted settings:

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
- Do not call HA directly from API route handlers except through services or
  integration modules.
- Gate and garage door opens can be schedule-gated through `schedule_id`; closed
  commands are allowed for configured garage doors.
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
  failure, garage door open failure, leaderboard overtake, AI anomaly alert,
  and integration tests.
- Supported action channels are mobile/Apprise, in-app realtime dashboard
  notifications, and Home Assistant voice/TTS.
- Supported conditions currently include schedule windows and presence state.
- Templates use `@Variable` tokens in the Tiptap editor. Legacy `[Variable]`
  tokens are accepted by the renderer but should not be used for new UI.
- Vehicle notification variables include `@VehicleMake`, `@VehicleColor`,
  `@VehicleColour`, `@MotStatus`, `@MotExpiry`, `@TaxStatus`, and
  `@TaxExpiry`. `@VehicleColor` and `@VehicleColour` resolve from the same
  unified colour fact.

Rules:

- Always compose notifications from structured facts.
- Phase 4+ AI naturalization should operate on `NotificationContext`, not raw
  logs or invented context.
- `apprise_urls` lives in encrypted dynamic settings and may be comma-separated
  or newline-separated.
- The obsolete `notification_rules` dynamic setting is pruned at startup; do
  not store notification workflow JSON in `system_settings`.

## DVLA Vehicle Enquiry Integration

DVLA client:

- `backend/app/modules/dvla/vehicle_enquiry.py`

Service wrapper:

- `backend/app/services/dvla.py`

Configuration lives in dynamic encrypted settings:

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

Configuration lives in dynamic encrypted settings:

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

## AI Agent

Provider wrappers:

- `backend/app/ai/providers.py`

Agent tools:

- `backend/app/ai/tools.py`

Chat orchestration:

- `backend/app/services/chat.py`

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

The local provider is deterministic and requires no API keys. It summarizes
tool outputs and is meant to keep the UI and agent tool pipeline usable during
development.

AI tools:

- `query_presence`
- `query_access_events`
- `query_anomalies`
- `summarize_access_rhythm`
- `calculate_visit_duration`
- `trigger_anomaly_alert`
- `get_system_users`
- `lookup_dvla_vehicle`
- `analyze_camera_snapshot`

Memory:

- Chat sessions persist in `chat_sessions`.
- Chat messages persist in `chat_messages`.
- Session context tracks recent subject/person/group for follow-up resolution.
- Example: after "Did the gardener arrive today?", "How long did they stay?"
  resolves to the gardener context.

Rules:

- Tool results are the source of truth for live access state.
- Do not let LLM providers invent access records.
- Add new tools in `app/ai/tools.py` with a clear JSON schema and handler.
- Keep high-risk actions such as gate control as explicit tools/endpoints with
  clear audit context before exposing them to AI automation.
- Camera image analysis must fetch live media through the UniFi Protect service;
  do not persist snapshots unless a future feature explicitly adds retention.

## API Surface

Base API prefix:

```text
/api/v1
```

Health:

- `GET /`
- `GET /health`
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

Events and presence:

- `GET /api/v1/events`
- `GET /api/v1/presence`
- `GET /api/v1/anomalies`

Webhooks:

- `POST /api/v1/webhooks/ubiquiti/lpr`

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
- `POST /api/v1/integrations/gate/open`
- `POST /api/v1/integrations/cover/command`
- `POST /api/v1/integrations/announcements/say`
- `POST /api/v1/integrations/notifications/test`
- `POST /api/v1/integrations/dvla/lookup`

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
- `WS /api/v1/ai/chat/ws`

Frontend Nginx proxies these under the same host at `:8089`.

Compatibility aliases:

- `/api/auth/*` mirrors `/api/v1/auth/*`.
- `/api/users/*` mirrors `/api/v1/users/*`.
- `/api/schedules/*` mirrors `/api/v1/schedules/*`.
- `/api/webhooks/*` mirrors `/api/v1/webhooks/*`.

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
- Vehicles
- Events
- Reports
- API & Integrations
- Logs
- Settings
- Settings / General
- Settings / Auth & Security
- Settings / Notifications
- Settings / LPR Tuning
- Settings / Users

Current frontend integration/editor surfaces:

- API & Integrations manages Home Assistant, Apprise, DVLA, UniFi Protect, and
  LLM providers.
- Home Assistant settings include discovery, gates, garage doors, and presence
  mapping tabs.
- UniFi Protect settings include general config, exposed camera/entity state,
  camera media/snapshot analysis, and managed update/backup controls.
- Settings / Notifications is the notification workflow builder with
  trigger/condition/action editing and Tiptap `@Variable` insertion.
- Vehicles edit modal includes a display-only DVLA Compliance card for MOT
  status/expiry, tax status/expiry, and last DVLA sync date. Make and colour
  remain on the main vehicle details fields, and registration edits still use
  the manual DVLA auto-fill flow.

Current realtime behavior:

- `WebSocket /api/v1/realtime/ws` receives system events and refreshes data.
- Logs view displays event bus messages.
- Global chat uses `WebSocket /api/v1/ai/chat/ws`.
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
3. Keep side effects explicit and auditable.
4. Return compact JSON-serializable output.
5. If the tool sends alerts, opens the gate, or changes state, make the user
   flow explicit in the UI before exposing it as a casual chat action.

## Development Commands

Run the full system:

```bash
cp .env.example .env
mkdir -p data/backend data/postgres data/redis logs/backend logs/frontend
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

## Current Phase State

Completed:

- Phase 1: Compose and backend scaffold.
- Phase 2: data models and Ubiquiti debounce.
- Phase 3: Home Assistant, TTS, Apprise.
- Phase 4: LLM providers, tools, memory, chat.
- Phase 5: frontend UI, realtime dashboard, chat panel.
- Phase 6: this `AGENTS.md`.
- User Management & Auth: first-run Admin setup, login/logout/me,
  admin-protected user CRUD, protected APIs/WebSockets, and `get_system_users`
  AI tool.
- Dynamic Configuration: database-backed settings, encrypted secrets,
  settings pages, integration tiles, and connection-test API.
- DVLA Lookup: encrypted API-key setting, connection test, lookup endpoint, and
  AI tool access to the Vehicle Enquiry Service API.
- Directory Management: CRUD for people, vehicles, groups, reusable schedules,
  profile/vehicle photos, notes/descriptions, and garage-door assignment.
- Notification Workflows: DB-backed rules, trigger catalog, conditions,
  Apprise/in-app/voice actions, rich template variables, preview, and test send.
- UniFi Protect: dynamic settings, camera/event discovery, media endpoints,
  AI snapshot analysis, realtime update events, and managed `uiprotect` package
  update/backup workflow.
- Access Pipeline Direction: top-gate-state arrival/exit inference, known-plate
  misread canonicalization, exact stored-plate debounce short-circuiting,
  garage-door opening only after an accepted gate-open command, and
  `camera.gate` OpenAI vision tie-breaking for double-arrival conflicts.
- Legacy Cleanup: old `time_slots` / `schedule_assignments` tables, endpoint,
  enum, and scheduler fallback have been removed.

Still pending or intentionally incomplete:

- Person and group deletion/archive workflows.
- Alembic migrations.
- Production worker process separation for queues.
- Real log rotation controls in UI.
- Optional dedicated entry/exit camera support beyond the current top-gate-state
  inference and `camera.gate` tie-breaker.
- Fine-grained AI action authorization for state-changing tools.

## Documentation Map

- `README.md`: quick run and phase endpoint summary.
- `docs/phase-1.md`: initial Compose/backend scaffold.
- `docs/phase-2.md`: models and LPR debounce.
- `docs/phase-3.md`: Home Assistant and notifications.
- `docs/phase-4.md`: AI providers, tools, memory.
- `docs/phase-5.md`: frontend, NPM target, UI verification.
- `AGENTS.md`: authoritative orientation for future agents.
