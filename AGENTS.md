# IACS Agent Guide

meta:
  product: Intelligent Access Control + Presence System
  purpose: LPR ingest -> noisy-read resolution -> presence -> anomalies -> gate/audio/notification orchestration -> realtime ops console -> Alfred AI ops
  deploy: docker compose
  host_ports:
    frontend: http://localhost:8089
    backend: http://localhost:8088
    postgres: localhost:5432
    redis: localhost:6379
  container_ports:
    frontend: 80
    backend: 8000
    postgres: 5432
    redis: 6379
  proxy:
    target: http://<docker-host-ip>:8089
    websockets: required
    root_path: IACS_ROOT_PATH blank unless URL subpath deployment
    frontend_nginx: proxies /api/* /health /docs /openapi.json WS -> backend:8000
  cache:
    spa_shell: no-store for / and /index.html
    assets: immutable for hashed /assets/*

stack:
  backend: Python 3.12 + FastAPI + SQLAlchemy async
  database: PostgreSQL
  cache_bus: Redis
  frontend: React 19 + TypeScript + Vite + Nginx
  frontend_libs: lucide-react, Tiptap, motion, TanStack Virtual, Monaco, jsondiffpatch
  integrations: Home Assistant, Apprise, WhatsApp Cloud API, Discord, DVLA VES, UniFi Protect/uiprotect, iCloud Calendar
  ai_providers: local, openai, gemini, claude/anthropic, ollama
  telemetry: db traces/spans/audit/artifacts

hard_rules:
  api_prefix: /api/v1 only; non-versioned API aliases forbidden
  storage: bind mounts only; Docker named volumes forbidden
  persistence_roots:
    - ./data/backend:/app/data
    - ./data/chat_attachments:/app/data/chat_attachments
    - ./logs/backend:/app/logs
    - ./backend/app:/app/app:ro
    - ./logs/frontend:/var/log/nginx
    - ./data/postgres:/var/lib/postgresql/data
    - ./data/redis:/data
    - ./data/backend/dependency-update-backups:/app/update-backups
  bootstrap_config: .env/Compose only for ports, DB/Redis URLs, auth secret, CORS/trusted hosts/public URL/root path, module selectors
  dynamic_config: system_settings via UI/API; encrypted secrets via Fernet derived from IACS_AUTH_SECRET_KEY
  secret_keys: home_assistant_token, apprise_urls, discord_bot_token, whatsapp_access_token, whatsapp_webhook_verify_token, whatsapp_app_secret, dvla_api_key, unifi_protect_username, unifi_protect_password, unifi_protect_api_key, openai_api_key, gemini_api_key, anthropic_api_key
  real_world_actions: gate/door commands, maintenance, schedule overrides, notification sends/tests, workflow/rule edits require explicit confirmation + audit
  modularity: core services consume normalized contracts; vendor I/O stays under backend/app/modules/*
  telemetry: sanitize secrets/media/cookies/tokens; audit durable state changes; realtime logs are not audit history
  error_policy: never return success unless adapter/provider accepted operation

repo:
  backend_entry: backend/app/main.py
  api_router: backend/app/api/router.py
  db_models: backend/app/models/core.py
  settings: backend/app/services/settings.py
  frontend_shell: frontend/src/main.tsx
  frontend_shared: frontend/src/shared.tsx
  frontend_views: frontend/src/views/*
  frontend_styles: frontend/src/styles.css imports frontend/src/styles/*
  alfred_service: backend/app/services/chat.py
  alfred_contracts: backend/app/services/chat_contracts.py
  alfred_routing_policy: backend/app/services/chat_routing.py
  alfred_tool_facade: backend/app/ai/tools.py
  alfred_tool_groups: backend/app/ai/tool_groups/*
  generated_ignore: data/, logs/, frontend/node_modules/, frontend/dist/

runtime_lifespan:
  start_order:
    - init_database
    - event_bus
    - dependency_update_service
    - notification_service
    - automation_service
    - discord_messaging_service
    - visitor_pass_service
    - access_event_service
    - home_assistant_service
    - gate_malfunction_service
    - unifi_protect_service
    - restart_backfill heartbeat/check
    - snapshot_recovery
  stop_order: reverse; close UniFi Protect WS/aiohttp/session resources

data:
  tables:
    identity: users, messaging_identities
    directory: groups, people, vehicles, schedules, schedule_overrides, presence
    access: access_events, anomalies, visitor_passes
    chat: chat_sessions, chat_messages
    notifications: notification_rules, notification_action_contexts
    automation: automation_rules, automation_runs, automation_webhook_senders
    integrations: system_settings, icloud_calendar_accounts, icloud_calendar_sync_runs, external_dependencies, dependency_update_analyses, dependency_update_backups, dependency_update_jobs
    safety: maintenance_mode_state, gate_state_observations, gate_malfunction_states, gate_malfunction_timeline_events, gate_malfunction_notification_outbox
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
  schema_bootstrap: Base.metadata.create_all + idempotent transitional columns/indexes; add Alembic when schema stabilizes
  seed: no demo seed; first Admin via UI/API setup

core_api:
  health: GET /, /health, /api/v1/health
  auth:
    service: backend/app/services/auth.py
    routes: /api/v1/auth/*
    behavior: first-run setup if users empty; Argon2; signed HTTP-only JWT cookie; bearer accepted
    protected: dashboard APIs, docs/openapi, realtime WS, AI chat WS after setup
    public: health, auth setup/login/status/logout, Ubiquiti webhook, WhatsApp webhook
    invariant: last active Admin cannot be deleted/demoted/deactivated
  users: /api/v1/users/* Admin CRUD; sidebar preference in users.preferences
  directory:
    routes: /api/v1/people, /vehicles, /groups, /schedules
    schedule_precedence: vehicle.schedule_id > person.schedule_id > schedule_default_policy
    schedule_blocks: Monday-first 30-minute normalized intervals in schedules.time_blocks
    deletions: vehicles yes; people/groups no hard-delete endpoint
  realtime:
    system: WS /api/v1/realtime/ws via event_bus; cookie or bearer/query token
    alfred: WS /api/v1/ai/chat/ws
    dependency_jobs: WS /api/v1/dependency-updates/jobs/{job_id}/ws

lpr_pipeline:
  webhook: POST /api/v1/webhooks/ubiquiti/lpr
  adapter: backend/app/modules/lpr/ubiquiti.py
  contract: PlateRead(registration_number, confidence, source, captured_at, raw_payload)
  service: backend/app/services/access_events.py
  maintenance_mode: active => accept/ignore webhook, clear queues, no access_event/presence/gate/garage
  smart-zone:
    current_state: diagnostic evidence only
    numeric_zone_ids: may resolve through UniFi Protect camera smart_detect_zones
    invariant: missing/empty/nonmatching smart zones never block or drop valid plate reads
    setting: lpr_allowed_smart_zones diagnostic only
  debounce:
    settings: lpr_debounce_quiet_seconds, lpr_debounce_max_seconds, lpr_similarity_threshold, lpr_vehicle_session_idle_seconds
    canonicalize: compare every read to active stored vehicles before grouping; preserve detected plate in raw payload
    exact_match: active stored exact plate wins; finalize burst immediately; suppress trailing same-source reads in original max window
    session_suppression: suppress duplicate reads in same physical visit; allow departure evidence through
  direction:
    primary: captured top-gate state at queue time
    closed: entry
    open/opening/closing: exit
    unknown: payload direction then presence state
    tie_breaker: known already-present entry => live UniFi Protect snapshot from camera.gate + OpenAI image analysis
  event_creation:
    authorization: vehicle/person/schedule/override
    visitor_pass: unknown arrival claims active pass before anomaly; unknown exit links used/duration pass departure
    dvla: arrival-like only; known vehicle same-day cache by site_timezone; unknown DVLA stays notification context only unless visitor_pass claimed
    persist: one final access_events row with telemetry trace id in raw_payload.telemetry.trace_id
    presence: update on granted events
    anomalies: unauthorized_plate, outside_schedule, duplicate_entry, duplicate_exit; suppress unauthorized anomaly for matched visitor pass
    gate: open only granted entry with captured gate state closed
    garage: open assigned doors only after accepted gate open, entry, captured closed, per-door schedule allowed
    realtime: publish finalized access/presence/anomaly/notification events
  diagnostics: /api/v1/diagnostics/lpr-timing in-memory feed from webhooks + UniFi Protect probes
  restart_backfill: backend/app/services/restart_backfill.py; missed UniFi Protect event repair; auditable; marked restart_backfill source/metadata

snapshots:
  owner: SnapshotManager in backend/app/services/snapshots.py
  wrappers: alert_snapshots.py, notification_snapshots.py compatibility only
  access_event_capture: SnapshotManager.capture_access_event_snapshot()
  disk: compact JPEG under /app/data/snapshots/access-events/
  db_fields: snapshot_path, snapshot_content_type, snapshot_bytes, snapshot_width, snapshot_height, snapshot_captured_at, snapshot_camera, created_at
  stream_processing: live media via UniFi Protect service; no ad hoc Path.write_bytes/Pillow compression outside SnapshotManager
  alert_media: unauthorized alert uses linked access_event snapshot metadata
  notification_media: short-lived files under notification-snapshots; TTL cleanup via SnapshotManager
  recovery: backend/app/services/snapshot_recovery.py startup pass; repair only
  ttl_future: use access_events.created_at + snapshot_path index; do not invent file mtime purge

notifications_mobile_push:
  service: backend/app/services/notifications.py
  contract: NotificationContext(event_type, subject, severity, facts)
  storage: notification_rules DB; not system_settings
  registry:
    triggers_conditions_actions_variables: backend/app/services/notifications.py catalogs
    add_trigger: define metadata + structured facts + sample context + tests
    templates: @Variable tokens; renderer accepts bracket tokens but new UI uses @Variable
  channels: mobile(Apprise/HA), in_app, WhatsApp, Discord, voice/TTS
  ha_mobile_sender: backend/app/modules/notifications/home_assistant_mobile.py
  ha_mobile_payload:
    service: notify.mobile_app_*
    body: {title, message, data}
    data: {tag: iacs-{event_type}, group: iacs, image?, attachment?: {url, content-type}, actions?}
  return_trip_webhook:
    source: Home Assistant WS event mobile_app_notification_action
    visitor_timeframe_actions: iacs:vp_time:<allow|deny>:<pass_id>:<request_id>
    gate_actions: iacs:gate_open:<token>, iacs:gate_force_open:<token>
  NotificationActionContext:
    table: notification_action_contexts
    token: HMAC(auth_secret_key, token); token_urlsafe(24)
    ttl: normal 10m; force 5m
    binding: notify_service -> exactly one active Person; optional linked active User
    consume: one-time; record consumed_at/outcome/outcome_detail
  audit_required:
    action_names: gate.open.actionable_notification, gate.open.actionable_notification.force
    category: integrations
    target_entity: Gate
    metadata_keys: action, context_id, parent_context_id, registration_number, access_event_id, person_id, notify_service, force, state, detail, malfunction_id, malfunction_duration_seconds, home_assistant_event_device_id
  event_bus_failures: notification.failed/skipped/sent published for UI/logs; Automation Engine must not cycle on notification.* status events

automation:
  service: backend/app/services/automations.py
  routes: /api/v1/automations/*
  model: flat Trigger -> If -> Then
  storage:
    rules: automation_rules {triggers[], trigger_keys[], conditions[], actions[], next_run_at,last_fired_at,run_count,last_run_status,last_error}
    runs: automation_runs {trigger_key,payload,context,condition_results,action_results,trace_id,status,error,actor,source}
    webhook_senders: automation_webhook_senders tracks key+source_ip; emits webhook.new_sender
  scheduler: lifespan; 15s; row locks; due active rules; recompute next_run_at; disable single-use/expired time-only rules
  event_sources: scheduler, public webhook, event_bus, Alfred
  event_bus_routes: access_event.finalized, visitor_pass.*, maintenance_mode.changed, ai.issue_detected, integration health/failure events to Notification System
  cycle_guard: ignore notification.trigger/sent/failed/skipped and automation.run.*
  dry_run: context/conditions/render only; no sends, no hardware, no sync timestamps
  integration_actions: backend/app/services/automation_integration_actions.py; catalog exposes enabled + disabled_reason
  maintenance_mode: skips hardware + notification-toggle + WhatsApp sends; maintenance_mode.disable allowed
  variable_policy: trigger scopes; unknown/empty/unavailable @Variable => context_missing skip

integrations:
  home_assistant:
    modules: modules/home_assistant/client.py, modules/gate/home_assistant.py, modules/announcements/home_assistant_tts.py, services/home_assistant.py
    dynamic_settings: url, token, gate_entities, garage_door_entities, gate_open_service, tts_service, default_media_player
    rules: gate/garage through modules/services only; no HA person.* as IACS presence; maintenance syncs input_boolean.top_gate_maintenance_mode
  whatsapp:
    service: backend/app/services/whatsapp_messaging.py
    webhooks: GET/POST /api/v1/webhooks/whatsapp
    outbound: Graph /{version}/{phone_number_id}/messages; phone_number_id is sender ID
    verification: hub token; X-Hub-Signature-256 if app_secret set; metadata phone_number_id must match config
    routing:
      Admin: exact normalized users.mobile_phone_number + active Admin -> upsert messaging_identities(provider=whatsapp) -> IncomingChatMessage -> MessagingBridgeService -> full Alfred ReAct
      Visitor: active/scheduled duration visitor_pass exact visitor_phone -> Visitor Concierge sandbox
      Other: denied/safe response/audit; never Admin Alfred
    Visitor Concierge: restricted LLM; tools only get_pass_details(phone_number), update_visitor_plate(pass_id,new_plate); no admin/gate/settings/schedules/files/tools
    visitor_time_changes: <=1h cumulative auto path with Confirm/Change; larger => visitor_pass_timeframe_change_requested notification; one open request max
  discord:
    service: backend/app/services/discord_messaging.py
    module: backend/app/modules/messaging/discord_bot.py
    routes: /api/v1/integrations/discord/*
    config: bot_token secret; guild/channel/user/role/admin_role allowlists; default channel; DM allowed; mention required
    routing: normalized IncomingChatMessage -> MessagingBridgeService -> Alfred ReAct after allowlist/admin checks
    identities: messaging_identities(provider=discord)
    confirmations: iacs:<confirm|cancel>:<session_id>:<confirmation_id>
  unifi_protect:
    service: backend/app/services/unifi_protect.py
    module: backend/app/modules/unifi_protect/client.py
    capabilities: cameras, events, snapshots, thumbnails, videos, websocket updates, package overlays/backups
    secrets: username/password/api_key encrypted
    shutdown: close websocket + all aiohttp/session cleanup methods
  dvla:
    service: backend/app/services/dvla.py
    module: backend/app/modules/dvla/vehicle_enquiry.py
    request: POST JSON body VRN; never URL query; API key secret
    persistence: known vehicle compliance fields only; unknown vehicle data not persisted except claimed visitor_pass details
  icloud_calendar:
    service: backend/app/services/icloud_calendar.py
    module: backend/app/modules/icloud_calendar/client.py
    auth: pyicloud trusted session bundle encrypted; Apple password never persisted
    sync: active accounts; today + 14d; notes/description marker "Open Gate"; creates/updates/cancels visitor_passes with source_reference icloud:<account>:<calendar>:<event>
  dependency_updates:
    service: backend/app/services/dependency_updates.py
    scope: system-wide dependencies from pyproject, installed dists, package.json/lock, Dockerfiles, Compose
    jobs: apply/restore with offline backup; logs under logs/backend/dependency-updates; WS job stream

alfred:
  services: chat.py orchestration; chat_contracts.py prompts/contracts/constants; chat_routing.py deterministic routing/planning policy; providers.py LLM adapters; tools.py compatibility facade; tool_groups/* domain catalogs; chat_attachments.py file store
  behavior:
    name: Alfred
    mode: LLM intent routing + ReAct loop; existing deterministic routing/planning lives only in chat_routing.py and must stay documented/tested
    entrypoints: dashboard HTTP/WS, Discord, WhatsApp Admin, future providers all through LLM intent router
    fail_closed: no provider/router failure => clear configuration/retry message
    source_of_truth: tool results; never invent people/vehicles/schedules/events/device states/DVLA/telemetry
    entity_resolution: fuzzy references -> resolve_human_entity before exact IDs
    confirmations: state-changing tools return requires_confirmation; /api/v1/ai/chat/confirm or WS tool_confirmation executes
    confirmation_binding: session_id + confirmation_id shared across dashboard/Discord/WhatsApp
    audit: alfred.tool.<tool>, actor Alfred_AI, provider/model/session, sanitized args/outcomes
  context_limits:
    MAX_AGENT_TOOL_ITERATIONS: 5
    MAX_RELEVANT_HISTORY_MESSAGES: 8
    api_message_max_chars: 4000
    attachment_max: 25 MB
    prompt_results: _tool_results_for_prompt compacts outputs; strings >1000 chars truncated; secret-like keys redacted
    tool_outputs: _compact_value trims long strings to 800 chars
  tools:
    registry: build_agent_tools() remains the stable public API; domain definitions live in backend/app/ai/tool_groups/* and are assembled by tool_groups/registry.py
    general: resolve_human_entity, get_system_users
    access_diag: query_presence, query_access_events, diagnose_access_event, investigate_access_incident, query_unifi_protect_events, backfill_access_event_from_protect, test_unifi_alarm_webhook, query_lpr_timing, query_vehicle_detection_history, get_telemetry_trace, query_leaderboard, query_anomalies, summarize_access_rhythm, calculate_visit_duration
    gate_maintenance: query_device_states, get_maintenance_status, get_active_malfunctions, get_malfunction_history, trigger_manual_malfunction_override, enable_maintenance_mode, disable_maintenance_mode, open_device, command_device, open_gate, toggle_maintenance_mode
    schedules: query_schedules, get_schedule, create_schedule, update_schedule, delete_schedule, query_schedule_targets, assign_schedule_to_entity, verify_schedule_access, override_schedule
    visitor_passes: query_visitor_passes, get_visitor_pass, create_visitor_pass, update_visitor_pass, cancel_visitor_pass
    calendar: trigger_icloud_sync
    compliance_cameras: lookup_dvla_vehicle, analyze_camera_snapshot, get_camera_snapshot
    files_reports: read_chat_attachment, export_presence_report_csv, generate_contractor_invoice_pdf
    notifications: query_notification_catalog, query_notification_workflows, get_notification_workflow, create_notification_workflow, update_notification_workflow, delete_notification_workflow, preview_notification_workflow, test_notification_workflow, trigger_anomaly_alert
    automations: query_automation_catalog, query_automations, get_automation, create_automation, edit_automation, delete_automation, enable_automation, disable_automation
  state_changing: assign_schedule_to_entity, create_notification_workflow, create_automation, create_schedule, create_visitor_pass, cancel_visitor_pass, delete_automation, delete_notification_workflow, delete_schedule, disable_automation, disable_maintenance_mode, edit_automation, enable_automation, enable_maintenance_mode, command_device, open_gate, open_device, override_schedule, trigger_anomaly_alert, trigger_manual_malfunction_override, test_notification_workflow, toggle_maintenance_mode, update_notification_workflow, update_schedule, update_visitor_pass, trigger_icloud_sync, backfill_access_event_from_protect, test_unifi_alarm_webhook

frontend:
  app_shell: frontend/src/main.tsx owns auth, global refresh, realtime socket, toasts, theme, sidebar, route Suspense, chat launcher
  shared: frontend/src/shared.tsx owns shared types, API client, route keys, realtime helpers, formatting, small common primitives
  views: frontend/src/views/* route/domain modules; keep props explicit from shell until a dedicated server-state phase
  styles: frontend/src/styles.css import manifest; domain CSS under frontend/src/styles/* in cascade order
  style: operational console; no landing/marketing hero
  routes_surfaces: Dashboard, People, Groups, Schedules, Passes, Vehicles, Top Charts, Events, Alerts, Reports, API & Integrations, Logs/Telemetry/Audit, Settings
  code_splitting: non-shell routes are React.lazy chunks; do not re-centralize route bodies into main.tsx or raise Vite chunk limits to hide bundle growth
  design: fixed desktop sidebar; bento cards; radius 8px; lucide icons; status badges; light/dark/system; no nested cards; no text overflow
  api: relative URLs only; LAN/NPM compatible
  notifications_ui: workflow builder; Tiptap @Variable; endpoint pickers; media toggles; preview/test
  automations_ui: When/If/Then builder; scoped @Variable picker; dry-run; integration action picker; safe rich-text fallback editors
  passes_ui: default Active+Scheduled; one-time/duration; WhatsApp transcript tab; Log tab; edit/cancel/delete in detail modal
  integrations_ui: Home Assistant, iCloud Calendar, Apprise, Discord, WhatsApp, DVLA, UniFi Protect, LLM providers, dependency update hub
  css_hazards: never broad-style badge span; keep .badge inline-flex; scope integration header spans to title selectors

extension_points:
  new_lpr_adapter:
    path: backend/app/modules/lpr/<vendor>.py
    contract: normalize vendor payload -> PlateRead
    registry: backend/app/modules/registry.py if selectable
    rule: do not import vendor schema into AccessEventService
  new_gate_controller:
    path: backend/app/modules/gate/<vendor>.py
    protocol: GateController -> GateCommandResult
    registry: modules/registry.py
  new_notification_sender:
    path: backend/app/modules/notifications/<channel>.py
    contract: NotificationSender.send(title, body, NotificationContext)
    rule: no raw DB models/log blobs
  new_notification_trigger_variable:
    registry: backend/app/services/notifications.py
    tests: rendering/delivery when variables/actions change
  new_automation_action:
    registry: backend/app/services/automation_integration_actions.py or automations catalog
    shape: {id,type,config}; expose enabled/disabled_reason
    rule: dry-run no side effects; runtime records automation_runs action_results
  new_ai_tool:
    path: add AgentTool in the relevant backend/app/ai/tool_groups/<domain>.py; keep backend/app/ai/tools.py as compatibility facade
    registry: backend/app/ai/tool_groups/registry.py assembles groups and rejects duplicate names
    definition: AgentTool name/description/JSON schema/handler; metadata applied by _with_tool_metadata in tools.py
    output: compact JSON; redact secrets/media
    state_change: add to state-changing metadata/tests; requires_confirmation before mutation/send/hardware
    tests: update backend/tests/test_chat_agent.py public tool surface + confirmation metadata guard

commands:
  run:
    - cp .env.example .env
    - mkdir -p data/backend data/chat_attachments data/postgres data/redis logs/backend logs/frontend
    - docker compose up --build
  backend_tests:
    all: docker compose exec -T backend sh -lc 'cd /workspace/backend && python -m pytest'
    one: docker compose exec -T backend sh -lc 'cd /workspace/backend && python -m pytest tests/test_dependency_updates.py'
    syntax: python3 -m compileall -q backend/app
  frontend:
    install_build: cd frontend && npm ci && npm run build
    rebuild: docker compose up -d --build frontend
  backend_restart: docker compose restart backend
  compose: docker compose config && docker compose ps
  smoke:
    - curl -fsS http://localhost:8089/api/v1/health
    - curl -fsS http://localhost:8089/api/v1/auth/status
    - curl -fsS http://localhost:8089/api/v1/maintenance/status
    - curl -fsS http://localhost:8089/api/v1/leaderboard
    - curl -fsS -X POST http://localhost:8089/api/v1/simulation/misread-sequence/TEST123
