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
- Notifications: Apprise.
- Home Assistant: REST service calls plus WebSocket state listener.
- DVLA: Vehicle Enquiry Service API lookup integration.
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
│       ├── schemas/
│       ├── services/
│       ├── simulation/
│       └── workers/
└── frontend/
    ├── Dockerfile
    ├── nginx.conf
    ├── package.json
    ├── vite.config.ts
    └── src/
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
- `app/workers`: queue constants and future worker entry points.

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
- `vehicles`: one person to many vehicles.
- `time_slots`: always, weekly, and one-time schedules.
- `schedule_assignments`: attach schedules to people or groups.
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
`Base.metadata.create_all`. This is acceptable for the current phased scaffold.
When the schema stabilizes, add Alembic migrations rather than continuing to
rely only on auto-create.

Seed data:

- Steph, group `Family`, vehicle `STEPH26`, all-day access.
- Bob, group `Contractors - Gardener`, vehicle `BOB123`, Wednesday 08:00-12:00.

Optional test account seed helper:

```bash
docker compose exec backend python -m app.scripts.seed_family_users
```

This adds standard family accounts only after a master Admin exists.

## Configuration

Configuration is split into bootstrap and dynamic settings.

Bootstrap remains in `.env`/Compose:

- Host ports.
- Postgres credentials and database URL construction.
- Redis URL.
- `IACS_ENVIRONMENT`, `IACS_AUTO_CREATE_SCHEMA`, `IACS_SEED_DEMO_DATA`.
- `IACS_AUTH_SECRET_KEY`.
- CORS, trusted hosts, public base URL, and root path.

Dynamic settings live in `system_settings` and are edited through the UI/API:

- General: app name, log level, timezone.
- Auth options: cookie name, token lifetimes, secure-cookie flag.
- LPR tuning: debounce quiet/max seconds and similarity threshold.
- Integrations: Home Assistant, Apprise, DVLA Vehicle Enquiry Service.
- LLM providers: active provider, timeout, base URLs, models, API keys.

Backend service:

- `backend/app/services/settings.py`
- `backend/app/core/crypto.py`
- `backend/app/api/v1/settings.py`

Do not add new provider tokens or operational tuning values back to
`docker-compose.yml` or `.env.example`. Add them to the dynamic settings seed
and UI instead. Secret setting keys must be added to `SECRET_KEYS`.

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
  protected once setup is complete.
- Admin-only user CRUD prevents deleting, demoting, or deactivating the last
  active Admin.
- Sidebar collapse preference is persisted to `users.preferences`.

Route aliases exist under both `/api/v1/auth` and `/api/auth`, and both
`/api/v1/users` and `/api/users`, so older clients can use the shorter path.

Relevant environment variables:

- `IACS_AUTH_SECRET_KEY`
- `IACS_AUTH_COOKIE_NAME`
- `IACS_AUTH_ACCESS_TOKEN_MINUTES`
- `IACS_AUTH_REMEMBER_DAYS`
- `IACS_AUTH_COOKIE_SECURE`

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
- Group similar reads from the same source during a debounce window.
- Wait for a quiet period or max debounce period.
- Select the highest-confidence candidate.
- Determine authorization from vehicle/person/schedule.
- Infer direction from explicit payload direction or current presence state.
- Persist one final `access_event`.
- Update presence if access is granted.
- Create anomalies for unauthorized plates, outside schedule, duplicate entry,
  and duplicate exit.
- Broadcast realtime events.
- Send contextual anomaly notifications.

Relevant environment variables:

- `IACS_LPR_DEBOUNCE_QUIET_SECONDS`
- `IACS_LPR_DEBOUNCE_MAX_SECONDS`
- `IACS_LPR_SIMILARITY_THRESHOLD`
- `IACS_SITE_TIMEZONE`

## Home Assistant Integration

Shared client:

- `backend/app/modules/home_assistant/client.py`

Gate controller:

- `backend/app/modules/gate/home_assistant.py`

TTS announcer:

- `backend/app/modules/announcements/home_assistant_tts.py`

State sync service:

- `backend/app/services/home_assistant.py`

Configuration:

```env
IACS_HOME_ASSISTANT_URL=http://homeassistant.local:8123
IACS_HOME_ASSISTANT_TOKEN=<long-lived-access-token>
IACS_HOME_ASSISTANT_GATE_ENTITY_ID=cover.driveway_gate
IACS_HOME_ASSISTANT_GATE_OPEN_SERVICE=cover.open_cover
IACS_HOME_ASSISTANT_TTS_SERVICE=tts.cloud_say
IACS_HOME_ASSISTANT_DEFAULT_MEDIA_PLAYER=media_player.all_google_home_speakers
IACS_HOME_ASSISTANT_PRESENCE_ENTITIES=Steph=person.steph,Bob=person.bob
```

Rules:

- Route gate commands through the gate module.
- Route audio announcements through the HA TTS module.
- Do not call HA directly from API route handlers except through services or
  integration modules.
- Presence entity mapping is optional and configured as comma-separated
  `Person Name=entity_id` pairs.

## Notifications

Notification service:

- `backend/app/services/notifications.py`

Apprise sender:

- `backend/app/modules/notifications/apprise_client.py`

Structured notification contract:

- `NotificationContext(event_type, subject, severity, facts)`

Rules:

- Always compose notifications from structured facts.
- Phase 4+ AI naturalization should operate on `NotificationContext`, not raw
  logs or invented context.
- `IACS_APPRISE_URLS` may be comma-separated or newline-separated.

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
- Do not log or expose the `x-api-key` value.
- Surface DVLA HTTP/API failures back to the caller; do not report a successful
  lookup unless DVLA returned a successful response.

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

- `IACS_LLM_PROVIDER=local`

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
- `GET /api/v1/vehicles`
- `GET /api/v1/groups`
- `GET /api/v1/time-slots`

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
- `POST /api/v1/integrations/gate/open`
- `POST /api/v1/integrations/announcements/say`
- `POST /api/v1/integrations/notifications/test`
- `POST /api/v1/integrations/dvla/lookup`

AI:

- `GET /api/v1/ai/providers`
- `GET /api/v1/ai/tools`
- `POST /api/v1/ai/chat`
- `WS /api/v1/ai/chat/ws`

Frontend Nginx proxies these under the same host at `:8089`.

## Frontend Architecture

Frontend app:

- `frontend/src/main.tsx`
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
- Vehicles
- Events
- Reports
- API & Integrations
- Logs
- Settings
- Settings / Users

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
npm install
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
curl -fsS -X POST http://localhost:8089/api/v1/simulation/misread-sequence/STEPH26
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

Still pending or intentionally incomplete:

- CRUD editing for people, vehicles, and schedules.
- Alembic migrations.
- Production worker process separation for queues.
- Real log rotation controls in UI.
- More robust access direction inference if separate entry/exit cameras exist.
- Fine-grained AI action authorization for state-changing tools.

## Documentation Map

- `README.md`: quick run and phase endpoint summary.
- `docs/phase-1.md`: initial Compose/backend scaffold.
- `docs/phase-2.md`: models and LPR debounce.
- `docs/phase-3.md`: Home Assistant and notifications.
- `docs/phase-4.md`: AI providers, tools, memory.
- `docs/phase-5.md`: frontend, NPM target, UI verification.
- `AGENTS.md`: authoritative orientation for future agents.
