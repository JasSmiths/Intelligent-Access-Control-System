# IACS Agent Guide

golden_rules:
  ask: unclear intent/requirements/architecture => ask before writing.
  scope: touch only files/functions required by the task; no opportunistic cleanup.
  simple: prefer the smallest working change; add abstractions only for real complexity.
  honest: flag uncertainty early; do not fake library/system knowledge.

system:
  name: Intelligent Access Control + Presence System
  purpose: LPR ingest -> plate resolution -> presence/anomalies -> gate/audio/notification orchestration -> realtime ops console -> Alfred AI ops.
  deploy: docker compose
  ports:
    host: {frontend: 8089, backend: 8088, postgres: 5432, redis: 6379}
    container: {frontend: 80, backend: 8000, postgres: 5432, redis: 6379}
  proxy:
    frontend: http://localhost:8089
    backend: http://localhost:8088
    frontend_nginx: proxies /api/*, /health, /docs, /openapi.json, WS -> backend:8000
    websockets: required
    root_path: IACS_ROOT_PATH blank unless deployed under URL subpath
  stack:
    backend: Python 3.12, FastAPI, SQLAlchemy async, PostgreSQL, Redis
    frontend: React 19, TypeScript, Vite, Nginx
    ui_libs: lucide-react, Tiptap, motion, TanStack Virtual, Monaco, jsondiffpatch
    integrations: Home Assistant, Apprise, WhatsApp Cloud API, Discord, DVLA VES, UniFi Protect/uiprotect, iCloud Calendar
    ai: local, OpenAI, Gemini, Claude/Anthropic, Ollama

hard_rules:
  api: versioned /api/v1 only; do not add non-versioned API aliases.
  storage: bind mounts only; Docker named volumes forbidden.
  generated_ignore: data/, logs/, frontend/node_modules/, frontend/dist/
  cache: SPA shell no-store for / and /index.html; hashed /assets/* immutable.
  bootstrap_env_only: ports, DB/Redis URLs, auth secret file/override, CORS/trusted hosts/public URL/root path, module selectors.
  dynamic_config: system_settings via UI/API; encrypted secrets use Fernet derived from active auth root secret.
  auth_secret:
    default: data/backend/auth-secret.key
    advanced_override: IACS_AUTH_SECRET_KEY
    rotation: file mode only through UI/API
  secret_keys:
    - home_assistant_token
    - apprise_urls
    - discord_bot_token
    - whatsapp_access_token
    - whatsapp_webhook_verify_token
    - whatsapp_app_secret
    - dvla_api_key
    - unifi_protect_username
    - unifi_protect_password
    - unifi_protect_api_key
    - openai_api_key
    - gemini_api_key
    - anthropic_api_key
    - dependency_update_backup_mount_options
  safety:
    require_admin_confirmation_audit:
      - gate/door/cover commands and announcements
      - maintenance changes/overrides
      - schedule overrides
      - notification sends/tests and workflow/rule edits
      - integration connection tests
      - telemetry purge
      - UniFi Protect update apply, backup create/restore/delete
    audit: durable state changes; realtime logs are not audit history.
    sanitize: never log raw secrets, cookies, tokens, or media blobs.
    error_policy: never return success unless adapter/provider accepted the operation.
  modularity: core services consume normalized contracts; vendor I/O stays under backend/app/modules/*.

repo:
  backend:
    entry: backend/app/main.py
    api_router: backend/app/api/router.py
    db_models: backend/app/models/core.py
    settings: backend/app/services/settings.py
    alfred_facade: backend/app/services/chat.py
    alfred_v3: backend/app/services/alfred/*
    alfred_contracts: backend/app/services/chat_contracts.py
    alfred_tools_facade: backend/app/ai/tools.py
    alfred_tool_groups: backend/app/ai/tool_groups/*
  frontend:
    shell: frontend/src/main.tsx
    shared: frontend/src/shared.tsx
    views: frontend/src/views/*
    styles: frontend/src/styles.css imports frontend/src/styles/*

runtime:
  start_order: [database, event_bus, dependency_updates, notifications, automations, discord, visitor_passes, access_events, home_assistant, gate_malfunction, unifi_protect, restart_backfill, snapshot_recovery]
  stop_order: reverse; close UniFi Protect WS/aiohttp/session resources.
  persistence_mounts:
    - ./data/backend:/app/data
    - ./data/chat_attachments:/app/data/chat_attachments
    - ./logs/backend:/app/logs
    - ./backend/app:/app/app:ro
    - ./logs/frontend:/var/log/nginx
    - ./data/postgres:/var/lib/postgresql/data
    - ./data/redis:/data
    - ./data/backend/dependency-update-backups:/app/update-backups

data:
  tables:
    identity: users, messaging_identities
    directory: groups, people, vehicles, schedules, schedule_overrides, presence
    access: access_events, anomalies, visitor_passes
    chat: chat_sessions, chat_messages, alfred_memories, alfred_feedback, alfred_lessons, alfred_eval_examples
    notifications: notification_rules, notification_action_contexts
    automation: automation_rules, automation_runs, automation_webhook_senders
    integrations: system_settings, icloud_calendar_accounts, icloud_calendar_sync_runs, external_dependencies, dependency_update_analyses, dependency_update_backups, dependency_update_jobs
    safety: action_confirmations, maintenance_mode_state, gate_state_observations, gate_malfunction_states, gate_malfunction_timeline_events, gate_malfunction_notification_outbox
    telemetry: audit_logs, telemetry_traces, telemetry_spans
    leaderboard: leaderboard_state
  filesystem:
    chat_attachments: data/chat_attachments/
    access_snapshots: data/backend/snapshots/access-events/
    notification_snapshots: data/backend/notification-snapshots/
    telemetry_artifacts: data/backend/telemetry-artifacts/
    unifi_package: data/backend/unifi-protect-package/
    unifi_backups: data/backend/unifi-protect-backups/
    dependency_cache: data/backend/dependency-update-cache/
    dependency_backups: data/backend/dependency-update-backups/
    dependency_logs: logs/backend/dependency-updates/
  schema: Base.metadata.create_all + idempotent transitional columns/indexes; add Alembic when schema stabilizes.
  seed: no demo seed; first Admin via setup UI/API.

api:
  health: GET /, /health, /api/v1/health
  auth:
    routes: /api/v1/auth/*
    behavior: first-run setup if users empty; Argon2; signed HTTP-only JWT cookie; bearer accepted.
    protected: dashboard APIs, docs/openapi, realtime WS, AI chat WS after setup.
    public: health, setup/login/status/logout, Ubiquiti webhook, WhatsApp webhook.
    invariant: last active Admin cannot be deleted/demoted/deactivated.
  users: /api/v1/users/* Admin CRUD; sidebar preference in users.preferences.
  directory:
    routes: /api/v1/people, /vehicles, /groups, /schedules
    schedule_precedence: vehicle.schedule_id > person.schedule_id > schedule_default_policy
    schedule_blocks: Monday-first 30-minute normalized intervals in schedules.time_blocks
    deletes: vehicles yes; people/groups no hard-delete endpoint
  realtime:
    system: WS /api/v1/realtime/ws via event_bus; cookie or bearer/query token.
    alfred: /api/v1/ai/chat, /stream, /ws, /agent/status, /feedback, /training/*
    dependency_jobs: WS /api/v1/dependency-updates/jobs/{job_id}/ws
  confirmations: POST /api/v1/action-confirmations; Admin-only, short-lived, one-use tokens bound to action+payload.

lpr_pipeline:
  webhook: POST /api/v1/webhooks/ubiquiti/lpr
  adapter: backend/app/modules/lpr/ubiquiti.py -> PlateRead(registration_number, confidence, source, captured_at, raw_payload)
  service: backend/app/services/access_events.py
  maintenance_mode: accept/ignore webhook; clear queues; no access_event/presence/gate/garage.
  smart_zones: diagnostic only; missing/empty/nonmatching zones never drop valid plate reads.
  debounce: compare every read to active vehicles; exact active plate wins; suppress trailing same-source reads within max window; duplicate session suppression allows departure evidence.
  direction: captured gate state at queue time; closed=entry, open/opening/closing=exit, unknown => payload then presence; known-present entry tie-breaker may use UniFi snapshot + OpenAI vision.
  creation: authorization + visitor pass + DVLA context + one final access_events row; granted events update presence; anomalies include unauthorized_plate/outside_schedule/duplicate_entry/duplicate_exit.
  gate: open only granted entry with captured closed state; garage opens assigned doors only after accepted gate open and schedule allows.
  diagnostics: /api/v1/diagnostics/lpr-timing; restart backfill in backend/app/services/restart_backfill.py.

snapshots:
  owner: backend/app/services/snapshots.py SnapshotManager
  wrappers: alert_snapshots.py, notification_snapshots.py compatibility only
  access: SnapshotManager.capture_access_event_snapshot(); compact JPEG under /app/data/snapshots/access-events/
  db_fields: snapshot_path, content_type, bytes, width, height, captured_at, camera, created_at
  rule: no ad hoc Path.write_bytes/Pillow compression outside SnapshotManager.
  notification_media: short-lived notification-snapshots with SnapshotManager TTL cleanup.
  recovery: backend/app/services/snapshot_recovery.py startup repair only.

notifications:
  service: backend/app/services/notifications.py
  contract: NotificationContext(event_type, subject, severity, facts)
  storage: notification_rules DB, not system_settings.
  templates: new UI uses @Variable; renderer also accepts bracket tokens.
  channels: mobile(Apprise/HA), in_app, WhatsApp, Discord, voice/TTS
  ha_mobile: backend/app/modules/notifications/home_assistant_mobile.py; notify.mobile_app_* body {title,message,data}
  action_context:
    table: notification_action_contexts
    ttl: normal 10m; force 5m
    token: HMAC(auth_secret_key, token); one-time consume with outcome.
  bus: publish notification.failed/skipped/sent for UI/logs; Automation Engine must not cycle on notification.* status events.

events:
  bus: backend/app/services/event_bus.py owns realtime transport; preserve existing string event names and payload shapes.
  typed_publishers: backend/app/services/domain_events.py wraps event_bus; migrate one event at a time with compatibility tests.

automation:
  service: backend/app/services/automations.py
  routes: /api/v1/automations/*
  model: flat Trigger -> If -> Then
  storage: automation_rules, automation_runs, automation_webhook_senders
  scheduler: lifespan every 15s; row locks; recompute next_run_at; disable single-use/expired time-only rules.
  sources: scheduler, public webhook, event_bus, Alfred
  cycle_guard: ignore notification.trigger/sent/failed/skipped and automation.run.*
  dry_run: render context/conditions only; no sends, hardware, or sync timestamps.
  maintenance_mode: skips hardware, notification-toggle, WhatsApp sends; maintenance_mode.disable allowed.
  variables: trigger scopes; unknown/empty/unavailable @Variable => context_missing skip.

integrations:
  home_assistant:
    modules: modules/home_assistant/client.py, modules/gate/home_assistant.py, modules/announcements/home_assistant_tts.py, services/home_assistant.py
    settings: url, token, gate_entities, garage_door_entities, gate_open_service, tts_service, default_media_player
    rules: gate/garage via modules/services only; no HA person.* as IACS presence; maintenance syncs input_boolean.top_gate_maintenance_mode.
  whatsapp:
    service: backend/app/services/whatsapp_messaging.py
    webhooks: GET/POST /api/v1/webhooks/whatsapp
    verify: hub token; X-Hub-Signature-256 if app_secret set; metadata phone_number_id must match config.
    routing: Admin exact normalized users.mobile_phone_number + active Admin -> Alfred; Visitor active/scheduled pass phone -> Visitor Concierge; others denied/audited.
    visitor_sandbox: tools only get_pass_details(phone_number), update_visitor_plate(pass_id,new_plate).
  discord:
    service: backend/app/services/discord_messaging.py
    routes: /api/v1/integrations/discord/*
    config: bot_token secret; guild/channel/user/role/admin_role allowlists; default channel; DM allowed; mention required.
  unifi_protect:
    service: backend/app/services/unifi_protect.py
    module: backend/app/modules/unifi_protect/client.py
    capabilities: cameras, events, snapshots, thumbnails, videos, websocket updates, package overlays/backups.
    shutdown: close websocket + aiohttp/session cleanup.
  dvla:
    request: POST JSON VRN; never URL query.
    persist: known vehicle compliance fields only; unknown data stays notification context unless visitor pass claimed.
  icloud_calendar:
    sync: today + 14d; notes marker "Open Gate"; creates/updates/cancels visitor_passes with source_reference icloud:<account>:<calendar>:<event>.
  dependency_updates:
    scope: pyproject, installed dists, package.json/lock, Dockerfiles, Compose.
    jobs: apply/restore with offline backup; logs under logs/backend/dependency-updates; WS job stream.

alfred:
  runtime: services/alfred/* v3; chat.py facade; chat_routing.py v2 rollback-only.
  behavior:
    mode: alfred_agent_mode defaults v3; LLM-owned planner -> scoped agent loop.
    fail_closed: provider/planner failure => configuration/retry message; no free-form deterministic answer.
    source_of_truth: tool results; never invent people/vehicles/schedules/events/device states/DVLA/telemetry.
    permissions: actor context before planning; Admin mutation/read tools; standard read-only; visitors sandbox only.
    confirmations: state-changing tools return requires_confirmation; execute via /api/v1/ai/chat/confirm or WS tool_confirmation.
    audit: alfred.tool.<tool>, actor Alfred_AI, provider/model/session, sanitized args/outcomes.
  limits: {MAX_AGENT_TOOL_ITERATIONS: 5, MAX_RELEVANT_HISTORY_MESSAGES: 8, api_message_max_chars: 4000, attachment_max: 25MB}
  memory: alfred_memories Postgres JSON + optional pgvector; users own user memory; Admin site memory; visitors no durable memory; redact secrets/transient visitor data.
  semantic_cache: Redis TTL cache for semantic_search keyed by query, actor, and limit; cache failure must fall back silently.
  learning: feedback -> sanitized snapshots; review_then_learn requires Admin approval; repair is read-only and never executes mutations.
  tools:
    registry: build_agent_tools() public API; domain tools in backend/app/ai/tool_groups/*; registry rejects duplicates and unsafe metadata.
    metadata: tool group owns categories, safety_level, required_permissions, default_limit, examples/rate limits/return schema when needed.
    safety_levels: read_only, confirmation_required, admin_only; non-read-only tools must require confirmation.
    add_tool: add AgentTool + group metadata; confirmation tools must expose confirm/confirm_send/confirmed and return requires_confirmation before mutation.
    handlers: keep tool handlers beside their domain catalog when practical; preserve app.ai.tools facade shims for public imports.
    planner: domain cards are generated from registry metadata; do not add keyword prefilters or deterministic routing shortcuts.
    tests: update backend/tests/test_chat_agent.py registry surface/permissions/card tests and touched domain tests.
    output: compact JSON; redact secrets/media; state changes require confirmation metadata/tests.

frontend:
  shell: frontend/src/main.tsx owns auth, global refresh, realtime socket, toasts, theme, sidebar, route Suspense, chat launcher.
  shared: frontend/src/shared.tsx owns shared types, API client, route keys, realtime helpers, formatting, small primitives.
  views: route/domain modules in frontend/src/views/*; props explicit until server-state phase.
  styles: operational console, no landing/marketing hero; CSS under frontend/src/styles/*.
  routes: Dashboard, People, Groups, Schedules, Passes, Vehicles, Top Charts, Events, Alerts, Reports, API & Integrations, Logs/Telemetry/Audit, Settings, Alfred Training.
  splitting: non-shell routes are React.lazy chunks; do not move route bodies back into main.tsx or raise Vite chunk limits to hide growth.
  design: fixed desktop sidebar; bento cards; radius 8px; lucide icons; status badges; light/dark/system; no nested cards/text overflow.
  api: relative URLs only for LAN/NPM compatibility.
  css_hazards: never broad-style badge span; keep .badge inline-flex; scope integration header spans to title selectors.

extension_points:
  lpr_adapter: backend/app/modules/lpr/<vendor>.py -> PlateRead; registry if selectable; no vendor schema in AccessEventService.
  gate_controller: backend/app/modules/gate/<vendor>.py -> GateController/GateCommandResult; registry in modules/registry.py.
  notification_sender: backend/app/modules/notifications/<channel>.py -> NotificationSender.send(title, body, NotificationContext); no raw DB/log blobs.
  notification_trigger_variable: backend/app/services/notifications.py catalogs + rendering/delivery tests.
  domain_event: add typed publisher in backend/app/services/domain_events.py; keep existing event name/payload compatible and test it.
  automation_action: backend/app/services/automation_integration_actions.py or automations catalog; expose enabled/disabled_reason; dry-run has no side effects.
  ai_tool: add to backend/app/ai/tool_groups/<domain>.py; declare group metadata there; registry assembles; update permission/confirmation tests in backend/tests/test_chat_agent.py.
  ai_handler: expose handlers through backend/app/ai/tool_groups/<domain>_handlers.py; keep app.ai.tools shims stable while moving bodies.

commands:
  setup:
    - cp .env.example .env
    - mkdir -p data/backend data/chat_attachments data/postgres data/redis logs/backend logs/frontend
    - docker compose up --build
  backend:
    syntax: python3 -m compileall -q backend/app
    tests: docker compose exec -T backend sh -lc 'cd /workspace/backend && python -m pytest'
    one: docker compose exec -T backend sh -lc 'cd /workspace/backend && python -m pytest tests/test_dependency_updates.py'
    alfred_ci: docker compose exec -T backend sh -lc 'cd /workspace/backend && python -m ruff check app/ai/tool_groups app/services/alfred app/services/chat.py app/services/domain_events.py && python -m mypy app/ai/tool_groups app/services/alfred/memory.py app/services/domain_events.py'
    restart: docker compose restart backend
  frontend:
    build: cd frontend && npm run build
    install_build: cd frontend && npm ci && npm run build
    rebuild: docker compose up -d --build frontend
  compose: docker compose config && docker compose ps
  smoke_readonly:
    - curl -fsS http://localhost:8089/api/v1/health
    - curl -fsS http://localhost:8089/api/v1/auth/status
    - curl -fsS http://localhost:8089/api/v1/maintenance/status
    - curl -fsS http://localhost:8089/api/v1/leaderboard
