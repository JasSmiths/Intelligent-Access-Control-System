# Full Project Maintenance Audit

Date: 2026-05-31  
Scope: full repository review for architecture, maintainability, reliability, security, testing, and AI-agent maintainability.  
Mode: read-first audit against `AGENTS.md`; no application behavior changed.  
Generated/runtime exclusions: `data/`, `logs/`, `frontend/node_modules/`, `frontend/dist/`, `frontend/test-results/`, `backend/build/`, virtualenvs, cache folders, and `__pycache__/`.

## Executive Summary

This is a capable but high-risk operational system: it has strong domain modeling, a useful test suite, route splitting on the frontend, durable audit concepts, and careful integration boundaries. The main risk is that several safety invariants are implemented inconsistently across HTTP routes, background workers, and deployment defaults. The highest-impact work is not cosmetic refactoring; it is tightening access-control mutations, making accepted LPR reads durable before returning success, and making physical-gate reconciliation device-specific.

Scorecard:

| Area | Rating | Notes |
| --- | --- | --- |
| Architecture | B- | Clear FastAPI/React split and modular provider pattern, but several large facade/service files are now load-bearing. |
| Security | C | Good auth middleware and secret encryption, but unsafe mutation permissions, confirmation gaps, exposed infra ports, and query-token patterns remain. |
| Reliability | C+ | Durable movement ledger exists, but accepted webhook reads and suppressed reads still have non-durable failure paths. |
| Maintainability | C | 154k LOC with several 3k-8k line files, bootstrap SQL sprawl, and thin CI around the real risk surface. |
| Testing | B- locally, D+ in CI | 656/657 backend tests passed locally; CI only runs targeted Alfred checks. |
| Frontend UX/ops | B- | The console is functional and split into views, but critical controls lack consistent confirmation, error boundaries, and tests. |

Counts reviewed:

| Metric | Count |
| --- | ---: |
| In-scope files inventoried | 294 |
| Approximate source/docs/config LOC | 154,199 |
| Backend pytest items collected | 657 |
| Backend pytest result | 656 passed, 1 failed |
| Frontend build | Passed |
| TODO/FIXME/HACK/legacy/deprecated-style hits | 233 |
| Untracked generated `backend/build` files excluded | 73 |

## Repository Overview

The repository contains a Docker Compose deployed access-control and presence system:

- Backend: Python 3.12, FastAPI, async SQLAlchemy, Postgres/pgvector, Redis Streams, Home Assistant, ESPHome, UniFi Protect, WhatsApp, Discord, DVLA, iCloud Calendar, and Alfred AI tooling.
- Frontend: React 19, TypeScript, Vite, Nginx, lucide-react, Monaco, TanStack, and custom operational-console CSS.
- Data: bind-mounted Postgres, Redis, backend data, chat attachments, snapshots, dependency update backups/cache, and logs.
- Core flow: LPR webhook -> Ubiquiti adapter -> access-event queue/debounce -> movement FSM/ledger/saga -> gate command coordinator -> access-device provider -> reconciliation -> presence/alerts/notifications/realtime.

The repo follows the `AGENTS.md` constraint that all API routes live under `/api/v1`; no non-versioned API aliases were found beyond health/root service identification.

## Architecture Review

Strengths:

- `backend/app/main.py` owns lifecycle order and mounts a versioned API router once under `/api/v1`.
- `backend/app/api/router.py` keeps route registration centralized.
- `backend/app/modules/*` contains vendor I/O adapters, matching the requested provider boundary.
- Movement concepts are usefully separated into FSM, ledger, saga records, sessions, gate command records, and reconciliation.
- Frontend route bodies are lazily loaded from `frontend/src/views/*`; route bodies have not been collapsed back into `main.tsx`.
- Secret dynamic settings use encrypted DB payloads through the active auth root secret.

Architecture stress points:

- `backend/app/ai/tools.py` is still a 7,749-line legacy facade despite the newer `backend/app/ai/tool_groups/*` structure.
- `backend/app/services/access_events.py`, `backend/app/services/whatsapp_messaging.py`, `backend/app/services/chat.py`, and `backend/app/services/notifications.py` each carry multiple responsibilities.
- Startup schema creation in `backend/app/db/bootstrap.py` has become a migration subsystem while Alembic is only partially adopted.
- Frontend shell state, realtime handling, global route selection, search, auth, sidebar, and chat bootstrapping still live in a 2,588-line `frontend/src/main.tsx`.
- Confirmation safety is not expressed as a reusable route-level contract, so similar endpoints vary in enforcement.

## Reviewed File Inventory

Inventory method: file-system inventory with generated/runtime/dependency outputs excluded. `.env` was included only as presence/local-config evidence; raw secret contents were not read or copied into this report.

| Path | Type | Purpose and notable observation |
| --- | --- | --- |
| `.env` | Local config | Presence only; sensitive contents intentionally not reviewed. |
| `.env.example` | Config template | Bootstrap ports and dev credentials; Postgres default password is documented as dev. |
| `.github/workflows/backend-alfred.yml` | CI workflow | Targeted Alfred-only workflow; no full backend/frontend/compose CI. |
| `.gitignore` | Repo config | Generated paths mostly ignored; local `backend/build` exists but is untracked. |
| `AGENTS.md` | Agent guide | Source of audit constraints and safety invariants. |
| `ESPHOME.md` | Documentation | ESPHome integration documentation. |
| `README.md` | Documentation | Project setup and architecture docs; contains machine-local links. |
| `backend/.dockerignore` | Docker config | Backend context filtering. |
| `backend/Dockerfile` | Docker config | Runtime installs dev/test/browser tooling and runs as root. |
| `backend/README.md` | Documentation | Backend-specific notes. |
| `backend/alembic.ini` | Migration config | Alembic present but not full schema source of truth. |
| `backend/alembic/env.py` | Migration code | Alembic runtime environment. |
| `backend/alembic/script.py.mako` | Migration template | Alembic revision template. |
| `backend/alembic/versions/20260509_0001_add_alfred_semantic_embeddings.py` | Migration | Single revision assumes existing tables. |
| `backend/app/__init__.py` | Package | Backend package marker. |
| `backend/app/ai/__init__.py` | Package | AI package marker. |
| `backend/app/ai/providers.py` | Backend AI | LLM provider definitions. |
| `backend/app/ai/tool_groups/__init__.py` | Package | Tool-group package marker. |
| `backend/app/ai/tool_groups/_facade_handlers.py` | Backend AI | Facade bridge for legacy handlers. |
| `backend/app/ai/tool_groups/access_diagnostics.py` | Backend AI | Access diagnostics tool metadata/catalog. |
| `backend/app/ai/tool_groups/access_diagnostics_handlers.py` | Backend AI | Access diagnostics handlers. |
| `backend/app/ai/tool_groups/automations.py` | Backend AI | Automation tool metadata/catalog. |
| `backend/app/ai/tool_groups/automations_handlers.py` | Backend AI | Automation handlers. |
| `backend/app/ai/tool_groups/compliance_cameras_files.py` | Backend AI | Compliance/camera/file tool metadata/catalog. |
| `backend/app/ai/tool_groups/compliance_cameras_files_handlers.py` | Backend AI | Compliance/camera/file handlers. |
| `backend/app/ai/tool_groups/gate_maintenance.py` | Backend AI | Gate maintenance tool metadata/catalog. |
| `backend/app/ai/tool_groups/gate_maintenance_handlers.py` | Backend AI | Gate maintenance handlers. |
| `backend/app/ai/tool_groups/general.py` | Backend AI | General tools metadata/catalog. |
| `backend/app/ai/tool_groups/general_handlers.py` | Backend AI | General handlers. |
| `backend/app/ai/tool_groups/metadata.py` | Backend AI | Tool metadata contracts. |
| `backend/app/ai/tool_groups/notifications.py` | Backend AI | Notification tool metadata/catalog. |
| `backend/app/ai/tool_groups/notifications_handlers.py` | Backend AI | Notification handlers. |
| `backend/app/ai/tool_groups/registry.py` | Backend AI | Tool registry assembly and safety metadata. |
| `backend/app/ai/tool_groups/schedules.py` | Backend AI | Schedule tool metadata/catalog. |
| `backend/app/ai/tool_groups/schedules_handlers.py` | Backend AI | Schedule handlers. |
| `backend/app/ai/tool_groups/system_operations.py` | Backend AI | System operation tool metadata/catalog. |
| `backend/app/ai/tool_groups/system_operations_handlers.py` | Backend AI | System operation handlers. |
| `backend/app/ai/tool_groups/visitor_passes.py` | Backend AI | Visitor pass tool metadata/catalog. |
| `backend/app/ai/tool_groups/visitor_passes_handlers.py` | Backend AI | Visitor pass handlers. |
| `backend/app/ai/tools.py` | Backend AI | Legacy facade and handlers; very large, high maintainability risk. |
| `backend/app/api/__init__.py` | Package | API package marker. |
| `backend/app/api/dependencies.py` | API auth | `current_user` and `admin_user` dependencies. |
| `backend/app/api/router.py` | API routing | Central `/api/v1` router registration. |
| `backend/app/api/v1/__init__.py` | Package | API v1 package marker. |
| `backend/app/api/v1/access.py` | API route | Movement and gate command inspection/repair routes. |
| `backend/app/api/v1/access_devices.py` | API route | Access-device CRUD and bindings; missing required confirmation. |
| `backend/app/api/v1/action_confirmations.py` | API route | Server-side confirmation token creation. |
| `backend/app/api/v1/ai.py` | API route | Alfred chat, tools, feedback, uploads, and training. |
| `backend/app/api/v1/auth.py` | API route | Setup/login/logout/profile routes. |
| `backend/app/api/v1/automations.py` | API route | Automation rules and public webhooks; webhook body cap missing. |
| `backend/app/api/v1/dependency_updates.py` | API route | Dependency update enrollment, analysis, backup, apply/restore. |
| `backend/app/api/v1/diagnostics.py` | API route | LPR timing and zone-shadow diagnostics. |
| `backend/app/api/v1/directory.py` | API route | People/vehicles/groups; mutation routes allow standard users. |
| `backend/app/api/v1/discord.py` | API route | Discord status, identity, and test endpoints. |
| `backend/app/api/v1/events.py` | API route | Events, presence, alerts, and snapshots. |
| `backend/app/api/v1/gate_malfunctions.py` | API route | Gate malfunction history and overrides. |
| `backend/app/api/v1/health.py` | API route | Health checks. |
| `backend/app/api/v1/icloud_calendar.py` | API route | iCloud account auth and sync. |
| `backend/app/api/v1/integrations.py` | API route | Integration status/actions/config; several config paths lack confirmation. |
| `backend/app/api/v1/leaderboard.py` | API route | Leaderboard endpoint. |
| `backend/app/api/v1/maintenance.py` | API route | Maintenance mode; confirmation pattern is present. |
| `backend/app/api/v1/media.py` | API helper | Media response helpers. |
| `backend/app/api/v1/notification_snapshots.py` | API route | Public short-lived notification snapshot serving. |
| `backend/app/api/v1/notifications.py` | API route | Notification rule CRUD/test; confirmation pattern mostly present. |
| `backend/app/api/v1/realtime.py` | API route | Realtime WebSocket; current behavior fails one test. |
| `backend/app/api/v1/reports.py` | API route | Report export and PDF retrieval. |
| `backend/app/api/v1/schedules.py` | API route | Schedule CRUD; mutation routes allow standard users and no confirmation. |
| `backend/app/api/v1/search.py` | API route | Global search. |
| `backend/app/api/v1/settings.py` | API route | Dynamic settings and integration test routes. |
| `backend/app/api/v1/telemetry.py` | API route | Trace/audit/artifact/purge endpoints; purge token currently in query. |
| `backend/app/api/v1/unifi_protect.py` | API route | UniFi Protect media/update/backups; delete token currently in query. |
| `backend/app/api/v1/users.py` | API route | Admin user management. |
| `backend/app/api/v1/visitor_passes.py` | API route | Visitor pass CRUD/messaging; mutation routes allow standard users. |
| `backend/app/api/v1/webhooks.py` | API route | WhatsApp and Ubiquiti webhooks; body size and durability concerns. |
| `backend/app/api/v1/whatsapp.py` | API route | WhatsApp integration status/test. |
| `backend/app/core/__init__.py` | Package | Core package marker. |
| `backend/app/core/auth_secret.py` | Core security | Auth root secret loading. |
| `backend/app/core/config.py` | Core config | Bootstrap env settings; trusted hosts default broad. |
| `backend/app/core/crypto.py` | Core security | Fernet encryption helpers. |
| `backend/app/core/logging.py` | Core logging | Structured logger setup. |
| `backend/app/db/__init__.py` | Package | DB package marker. |
| `backend/app/db/base.py` | DB model base | SQLAlchemy base import surface. |
| `backend/app/db/bootstrap.py` | DB bootstrap | Large transitional schema/migration blob. |
| `backend/app/db/session.py` | DB session | Async engine/session setup. |
| `backend/app/main.py` | Backend entry | Lifecycle, middleware, auth guard, router mount. |
| `backend/app/models/__init__.py` | Package | Model exports. |
| `backend/app/models/core.py` | DB models | Main ORM model file; large but central. |
| `backend/app/models/enums.py` | DB enums | Domain enums. |
| `backend/app/modules/__init__.py` | Package | Provider package marker. |
| `backend/app/modules/access_devices/__init__.py` | Package | Access-device provider package marker. |
| `backend/app/modules/access_devices/base.py` | Provider contract | Access-device provider interfaces. |
| `backend/app/modules/access_devices/esphome.py` | Provider | ESPHome access-device implementation. |
| `backend/app/modules/access_devices/home_assistant.py` | Provider | Home Assistant access-device implementation. |
| `backend/app/modules/access_devices/registry.py` | Provider registry | Access-device provider selection. |
| `backend/app/modules/announcements/__init__.py` | Package | Announcement module marker. |
| `backend/app/modules/announcements/home_assistant_tts.py` | Provider | HA TTS announcements. |
| `backend/app/modules/dvla/__init__.py` | Package | DVLA module marker. |
| `backend/app/modules/dvla/vehicle_enquiry.py` | Provider | DVLA VES client/normalization. |
| `backend/app/modules/gate/__init__.py` | Package | Gate module marker. |
| `backend/app/modules/gate/access_devices.py` | Gate provider | Physical gate controller via access devices. |
| `backend/app/modules/gate/base.py` | Gate contract | Gate controller interfaces. |
| `backend/app/modules/gate/home_assistant.py` | Gate provider | Legacy HA gate controller. |
| `backend/app/modules/home_assistant/__init__.py` | Package | HA module marker. |
| `backend/app/modules/home_assistant/client.py` | Provider | HA HTTP/WebSocket client. |
| `backend/app/modules/home_assistant/covers.py` | Provider helper | HA cover entity helpers. |
| `backend/app/modules/home_assistant/input_booleans.py` | Provider helper | HA input boolean helpers. |
| `backend/app/modules/icloud_calendar/__init__.py` | Package | iCloud module marker. |
| `backend/app/modules/icloud_calendar/client.py` | Provider | iCloud client. |
| `backend/app/modules/lpr/__init__.py` | Package | LPR module marker. |
| `backend/app/modules/lpr/base.py` | Provider contract | LPR plate-read contract. |
| `backend/app/modules/lpr/ubiquiti.py` | Provider | Ubiquiti LPR adapter. |
| `backend/app/modules/messaging/__init__.py` | Package | Messaging module marker. |
| `backend/app/modules/messaging/base.py` | Provider contract | Messaging base interfaces. |
| `backend/app/modules/messaging/discord_bot.py` | Provider | Discord bot adapter. |
| `backend/app/modules/notifications/__init__.py` | Package | Notification provider marker. |
| `backend/app/modules/notifications/apprise_client.py` | Provider | Apprise notification sender. |
| `backend/app/modules/notifications/base.py` | Provider contract | Notification sender contract. |
| `backend/app/modules/notifications/discord_formatter.py` | Provider helper | Discord notification formatting. |
| `backend/app/modules/notifications/home_assistant_mobile.py` | Provider | HA mobile app notifier. |
| `backend/app/modules/registry.py` | Provider registry | Module registry helpers. |
| `backend/app/modules/unifi_protect/__init__.py` | Package | UniFi module marker. |
| `backend/app/modules/unifi_protect/client.py` | Provider | UniFi Protect API/client code. |
| `backend/app/modules/unifi_protect/package.py` | Provider helper | UniFi package overlay/backup helpers. |
| `backend/app/schemas/__init__.py` | Package | Schema package marker. |
| `backend/app/scripts/__init__.py` | Package | Script package marker. |
| `backend/app/services/__init__.py` | Package | Services package marker. |
| `backend/app/services/access_devices.py` | Service | Access-device service and seeding. |
| `backend/app/services/access_events.py` | Service | LPR ingest/debounce/decision orchestration; very large, critical. |
| `backend/app/services/action_confirmations.py` | Service | Confirmation token hashing/consume/audit; replay audit bug. |
| `backend/app/services/actionable_notifications.py` | Service | Notification action context execution. |
| `backend/app/services/alert_snapshots.py` | Service wrapper | Compatibility wrapper around SnapshotManager. |
| `backend/app/services/alfred/__init__.py` | Package | Alfred v3 package marker. |
| `backend/app/services/alfred/answer_contracts.py` | Alfred service | Critical answer contract checks. |
| `backend/app/services/alfred/embeddings.py` | Alfred service | Embedding helpers. |
| `backend/app/services/alfred/executor.py` | Alfred service | Tool execution. |
| `backend/app/services/alfred/feedback.py` | Alfred service | Feedback/learning. |
| `backend/app/services/alfred/memory.py` | Alfred service | Memory and semantic cache. |
| `backend/app/services/alfred/permissions.py` | Alfred service | Actor permission filtering. |
| `backend/app/services/alfred/planner.py` | Alfred service | LLM planner. |
| `backend/app/services/alfred/runtime.py` | Alfred service | Agent runtime. |
| `backend/app/services/alfred/streaming.py` | Alfred service | Streaming event helpers. |
| `backend/app/services/auth.py` | Service | Auth, tokens, password hashing. |
| `backend/app/services/auth_secret_management.py` | Service | Auth secret status/rotation. |
| `backend/app/services/automation_integration_actions.py` | Service | Automation integration action catalog. |
| `backend/app/services/automations.py` | Service | Automation rules, scheduler, event hooks. |
| `backend/app/services/chat.py` | Service | Alfred facade/chat orchestration; very large. |
| `backend/app/services/chat_attachments.py` | Service | Chat attachment storage and parsing; backend limit is 25 MB. |
| `backend/app/services/chat_contracts.py` | Service contract | Chat response contracts. |
| `backend/app/services/chat_routing.py` | Service helper | Guided helper heuristics. |
| `backend/app/services/dependency_updates.py` | Service | Dependency update jobs/backups; large. |
| `backend/app/services/discord_messaging.py` | Service | Discord message routing. |
| `backend/app/services/domain_events.py` | Service | Typed domain event publishers. |
| `backend/app/services/dvla.py` | Service | DVLA lookup normalization. |
| `backend/app/services/event_bus.py` | Service | Redis stream + WebSocket fanout. |
| `backend/app/services/expected_presence.py` | Service | Expected presence calculation. |
| `backend/app/services/gate_commands.py` | Service | Gate command coordinator/leases/idempotency. |
| `backend/app/services/gate_malfunctions.py` | Service | Gate malfunction detection/recovery. |
| `backend/app/services/home_assistant.py` | Service | HA runtime service/listener. |
| `backend/app/services/icloud_calendar.py` | Service | Calendar sync to visitor passes. |
| `backend/app/services/leaderboard.py` | Service | Top charts. |
| `backend/app/services/lpr_timing.py` | Service | LPR timing diagnostics. |
| `backend/app/services/lpr_webhook_security.py` | Service security | LPR token/IP allowlist. |
| `backend/app/services/lpr_zone_shadow.py` | Service | Smart-zone diagnostic shadowing. |
| `backend/app/services/maintenance.py` | Service | Maintenance mode state. |
| `backend/app/services/messaging_bridge.py` | Service | Messaging-to-Alfred bridge. |
| `backend/app/services/movement_fsm.py` | Service | Movement direction/suppression FSM. |
| `backend/app/services/movement_ledger.py` | Service | Durable saga/session/command repository. |
| `backend/app/services/movement_reconciliation.py` | Service | Gate command and movement reconciliation. |
| `backend/app/services/notification_snapshots.py` | Service wrapper | Compatibility wrapper around SnapshotManager. |
| `backend/app/services/notifications.py` | Service | Notification rules/render/delivery; large. |
| `backend/app/services/person_presence_input_booleans.py` | Service | HA presence boolean sync helpers. |
| `backend/app/services/profile_photos.py` | Service | Media normalization/compact image helpers. |
| `backend/app/services/report_templates/person_movements.html` | Template | Report HTML template. |
| `backend/app/services/reports.py` | Service | Report export/PDF generation. |
| `backend/app/services/restart_backfill.py` | Service | Startup backfill without hardware side effects. |
| `backend/app/services/schedules.py` | Service | Time-block normalization/dependency checks. |
| `backend/app/services/settings.py` | Service | Dynamic settings seed/update/runtime config. |
| `backend/app/services/snapshot_recovery.py` | Service | Startup snapshot repair. |
| `backend/app/services/snapshots.py` | Service | SnapshotManager for access/notification media. |
| `backend/app/services/telemetry.py` | Service | Traces, audit, artifacts; fire-and-forget audit path. |
| `backend/app/services/tts_phonetics.py` | Service helper | TTS pronunciation helpers. |
| `backend/app/services/type_helpers.py` | Service helper | Type coercion helpers. |
| `backend/app/services/unifi_protect.py` | Service | UniFi Protect runtime service. |
| `backend/app/services/unifi_protect_updates.py` | Service | UniFi update/backups. |
| `backend/app/services/vehicle_visual_detections.py` | Service | Vehicle visual detection helpers. |
| `backend/app/services/visitor_passes.py` | Service | Visitor pass lifecycle. |
| `backend/app/services/whatsapp_messaging.py` | Service | WhatsApp routing/webhook/test; very large. |
| `backend/app/simulation/__init__.py` | Package | Simulation package marker. |
| `backend/app/simulation/router.py` | API route | LPR simulation routes. |
| `backend/app/simulation/scenarios.py` | Simulation | E2E simulation scenarios. |
| `backend/app/workers/__init__.py` | Package | Worker package marker. |
| `backend/pyproject.toml` | Python config | Runtime deps mostly lower bounds; no Python lockfile. |
| `backend/tests/conftest.py` | Test config | Async engine disposal fixture. |
| `backend/tests/test_access_devices.py` | Test | Access-device service tests. |
| `backend/tests/test_access_events.py` | Test | LPR/access-event tests. |
| `backend/tests/test_action_confirmations.py` | Test | Confirmation safety tests. |
| `backend/tests/test_actionable_notifications.py` | Test | Actionable notification tests. |
| `backend/tests/test_alerts.py` | Test | Alert behavior tests. |
| `backend/tests/test_auth_secret.py` | Test | Auth secret tests. |
| `backend/tests/test_automations.py` | Test | Automation service/API/AI tests. |
| `backend/tests/test_chat_agent.py` | Test | Alfred agent tests; very large. |
| `backend/tests/test_dependency_updates.py` | Test | Dependency update tests. |
| `backend/tests/test_directory_people.py` | Test | Directory serialization tests. |
| `backend/tests/test_discord_messaging.py` | Test | Discord messaging tests. |
| `backend/tests/test_dvla_service.py` | Test | DVLA service tests. |
| `backend/tests/test_event_bus.py` | Test | Event bus tests. |
| `backend/tests/test_expected_presence.py` | Test | Expected presence tests. |
| `backend/tests/test_gate_commands.py` | Test | Gate command coordinator tests. |
| `backend/tests/test_gate_malfunctions.py` | Test | Gate malfunction tests. |
| `backend/tests/test_http_clients.py` | Test | HTTP client behavior tests. |
| `backend/tests/test_icloud_calendar.py` | Test | iCloud calendar tests. |
| `backend/tests/test_leaderboard.py` | Test | Leaderboard tests. |
| `backend/tests/test_lpr_timing.py` | Test | LPR timing tests. |
| `backend/tests/test_lpr_zone_shadow.py` | Test | Zone shadow tests. |
| `backend/tests/test_messaging_bridge.py` | Test | Messaging bridge tests. |
| `backend/tests/test_movement_fsm.py` | Test | Movement FSM tests. |
| `backend/tests/test_movement_ledger.py` | Test | Movement ledger tests. |
| `backend/tests/test_movement_reconciliation.py` | Test | Movement reconciliation tests; add multi-gate case. |
| `backend/tests/test_notification_workflows.py` | Test | Notification workflow tests. |
| `backend/tests/test_operational_status.py` | Test | Operational status tests. |
| `backend/tests/test_profile_photos.py` | Test | Profile photo tests. |
| `backend/tests/test_realtime.py` | Test | Realtime tests; currently failing one behavior assertion. |
| `backend/tests/test_reports.py` | Test | Report tests. |
| `backend/tests/test_restart_backfill.py` | Test | Restart backfill tests. |
| `backend/tests/test_search_api.py` | Test | Search API tests. |
| `backend/tests/test_simulation_e2e.py` | Test | Simulation E2E tests. |
| `backend/tests/test_snapshots.py` | Test | Snapshot tests. |
| `backend/tests/test_telemetry.py` | Test | Telemetry tests. |
| `backend/tests/test_ubiquiti_lpr.py` | Test | Ubiquiti LPR tests. |
| `backend/tests/test_unifi_protect.py` | Test | UniFi Protect service tests. |
| `backend/tests/test_unifi_protect_client.py` | Test | UniFi client tests. |
| `backend/tests/test_unifi_protect_updates.py` | Test | UniFi updates tests. |
| `backend/tests/test_users_api.py` | Test | Users API tests. |
| `backend/tests/test_vehicle_visual_detections.py` | Test | Vehicle visual detection tests. |
| `backend/tests/test_visitor_passes.py` | Test | Visitor pass tests. |
| `backend/tests/test_webhooks.py` | Test | Webhook tests. |
| `backend/tests/test_whatsapp_messaging.py` | Test | WhatsApp messaging tests; very large. |
| `docker-compose.yml` | Deploy config | Bind mounts only, but DB/Redis/backend are exposed on all interfaces. |
| `docs/phase-1.md` | Documentation | Phase notes. |
| `docs/phase-2.md` | Documentation | Phase notes; documents bootstrap schema approach. |
| `docs/phase-3.md` | Documentation | Phase notes. |
| `docs/phase-4.md` | Documentation | Phase notes. |
| `docs/phase-5.md` | Documentation | Phase notes; contains local Codex image path. |
| `docs/phase-6.md` | Documentation | Phase notes. |
| `frontend/Dockerfile` | Docker config | Frontend build/runtime image. |
| `frontend/index.html` | Frontend shell | Vite HTML entry. |
| `frontend/nginx.conf` | Frontend proxy | SPA/proxy config; upload cap conflicts with backend. |
| `frontend/package-lock.json` | Frontend lock | npm dependency lock present. |
| `frontend/package.json` | Frontend config | Build only; no lint/test/e2e scripts. |
| `frontend/public/favicon.svg` | Asset | Favicon. |
| `frontend/src/VariableRichTextEditor.tsx` | Frontend component | ContentEditable template editor; HTML escaping is present. |
| `frontend/src/main.tsx` | Frontend shell | Auth, routes, realtime, search, sidebar, chat bootstrapping; large. |
| `frontend/src/shared.tsx` | Frontend shared | API client/types/settings/UI helpers; numeric coercion issue. |
| `frontend/src/styles.css` | Frontend CSS | Main style imports. |
| `frontend/src/styles/auth-directory-modals.css` | Frontend CSS | Auth/directory modal styles. |
| `frontend/src/styles/base.css` | Frontend CSS | Base design tokens/layout. |
| `frontend/src/styles/chat-responsive.css` | Frontend CSS | Chat responsive styles; currently user-modified. |
| `frontend/src/styles/dashboard.css` | Frontend CSS | Dashboard styles. |
| `frontend/src/styles/data-views.css` | Frontend CSS | Data view styles; currently user-modified and very large. |
| `frontend/src/styles/integrations.css` | Frontend CSS | Integration page styles. |
| `frontend/src/styles/passes-schedules.css` | Frontend CSS | Pass/schedule styles; very large. |
| `frontend/src/styles/search-palette.css` | Frontend CSS | Search palette styles. |
| `frontend/src/styles/telemetry.css` | Frontend CSS | Logs/telemetry styles. |
| `frontend/src/styles/workflows.css` | Frontend CSS | Automation/notification workflow styles; very large. |
| `frontend/src/views/AlertsView.tsx` | Frontend view | Alerts view. |
| `frontend/src/views/AlfredTrainingView.tsx` | Frontend view | Alfred training view. |
| `frontend/src/views/ChatWidgetView.tsx` | Frontend view | Alfred chat widget; `/llm` UI lacks admin gate. |
| `frontend/src/views/DashboardView.tsx` | Frontend view | Dashboard; several dead "View all" controls. |
| `frontend/src/views/DirectoryViews.tsx` | Frontend view | People/vehicles/groups; currently has admin-style mutation UI. |
| `frontend/src/views/EventsView.tsx` | Frontend view | Events view; currently user-modified. |
| `frontend/src/views/IntegrationsView.tsx` | Frontend view | Integrations, dependency updates, UniFi; largest frontend view. |
| `frontend/src/views/LogsView.tsx` | Frontend view | Logs wrapper. |
| `frontend/src/views/MovementsView.tsx` | Frontend view | Movements view; currently user-modified. |
| `frontend/src/views/PassesView.tsx` | Frontend view | Visitor pass view. |
| `frontend/src/views/ReportsView.tsx` | Frontend view | Reports view. |
| `frontend/src/views/SchedulesView.tsx` | Frontend view | Schedule view. |
| `frontend/src/views/SettingsViews.tsx` | Frontend view | Settings/access devices/users; currently user-modified. |
| `frontend/src/views/TopChartsView.tsx` | Frontend view | Top charts; currently user-modified. |
| `frontend/src/views/WorkflowViews.tsx` | Frontend view | Automation/notification workflows; very large. |
| `frontend/src/views/logExplorer/LogsControls.tsx` | Frontend logs | Log controls. |
| `frontend/src/views/logExplorer/LogsWorkspace.tsx` | Frontend logs | Logs workspace; purge token sent in query. |
| `frontend/src/views/logExplorer/LprWaterfallPanel.tsx` | Frontend logs | LPR waterfall panel. |
| `frontend/src/views/logExplorer/NarrativeFeed.tsx` | Frontend logs | Narrative feed. |
| `frontend/src/views/logExplorer/components.tsx` | Frontend logs | Logs UI components and Monaco diff loader. |
| `frontend/src/views/logExplorer/constants.ts` | Frontend logs | Log constants. |
| `frontend/src/views/logExplorer/hooks.ts` | Frontend logs | Logs data-fetching hooks. |
| `frontend/src/views/logExplorer/lprWaterfall.ts` | Frontend logs | LPR waterfall transforms. |
| `frontend/src/views/logExplorer/narrative.ts` | Frontend logs | Narrative transforms. |
| `frontend/src/views/logExplorer/types.ts` | Frontend logs | Logs type contracts. |
| `frontend/src/views/logExplorer/utils.ts` | Frontend logs | Export utilities; CSV formula-injection issue. |
| `frontend/src/vite-env.d.ts` | Frontend type config | Vite env typings. |
| `frontend/tsconfig.json` | Frontend config | TypeScript config. |
| `frontend/vite.config.ts` | Frontend config | Vite config. |
| `scripts/load-test.mjs` | Script | Load test script; mints admin token and passes it through args/query string. |

## Top 10 Highest Impact Findings

| Rank | Severity | Finding | Primary paths | Recommended first fix |
| ---: | --- | --- | --- | --- |
| 1 | Critical | Standard authenticated users can mutate schedules, directory records, and visitor passes that affect physical access. | `backend/app/api/v1/schedules.py`, `directory.py`, `visitor_passes.py` | Change state-changing routes to `admin_user`, add confirmation where policy/hardware effects exist, and add standard-user denial tests. |
| 2 | Critical | Accepted LPR webhooks can be lost before durable processing. | `backend/app/api/v1/webhooks.py:332`, `backend/app/services/access_events.py:278` | Persist an ingest row before returning `202`, then process from durable storage or Redis stream with ack/retry. |
| 3 | High | Suppressed LPR reads may publish without durable `SUPPRESSED` movement history. | `backend/app/services/access_events.py:1206`, `:1246` | Treat suppression persistence failure as a failed terminal event, retry, or return degradation status before publishing. |
| 4 | High | Gate reconciliation can accept an unrelated gate observation. | `backend/app/services/movement_reconciliation.py:265`, `backend/app/models/core.py:625`, `:1015` | Filter observations by command `gate_key` or explicit provider external ID mapping. |
| 5 | High | Access-device, ESPHome, and several integration config mutations bypass required server-side confirmation. | `backend/app/api/v1/access_devices.py:56`, `:118`, `backend/app/api/v1/integrations.py:220` | Add confirmation tokens to request models and consume them before writes/tests. |
| 6 | High | Confirmation/admin tokens are put in query strings or process args. | `frontend/src/views/logExplorer/LogsWorkspace.tsx:102`, `frontend/src/views/IntegrationsView.tsx:2736`, `scripts/load-test.mjs:140`, `:168` | Move DELETE confirmations to JSON bodies and keep load-test tokens out of args/URLs. |
| 7 | High | Postgres and Redis are exposed on host interfaces with dev defaults/no auth. | `docker-compose.yml:87`, `:103`, `.env.example:12` | Bind to loopback by default or document LAN-hardening envs; require non-default DB password for non-dev. |
| 8 | High | Simulation LPR endpoints are authenticated-only and feed the live access pipeline. | `backend/app/simulation/router.py:21`, `backend/app/services/access_events.py:2991` | Require Admin and confirmation or disable outside test/dev mode. |
| 9 | Medium | Public automation webhooks rely only on URL keys but can trigger high-impact actions. | `backend/app/main.py:187`, `backend/app/api/v1/automations.py:214`, `backend/app/services/automations.py:1201` | Enforce server-generated high-entropy keys, optional HMAC/source policies, and rate/replay controls. |
| 10 | Medium | CI is too narrow and current full backend test run fails one realtime test. | `.github/workflows/backend-alfred.yml:5`, `backend/tests/test_realtime.py:40` | Add full backend, frontend build, compose config, and smoke CI jobs; fix or update realtime test behavior. |

## Critical Issues

### 1. Standard users can mutate access policy and visitor access

- Severity: Critical
- File path: `backend/app/api/v1/schedules.py:74`, `backend/app/api/v1/directory.py:726`, `backend/app/api/v1/visitor_passes.py:188`
- Description: Several state-changing routes depend on `current_user` rather than `admin_user`. This includes schedule create/update/delete, people/vehicle/group create/update/delete paths, and visitor pass create/update/cancel/delete/message paths.
- Why it matters: `AGENTS.md` defines standard users as read-only in Alfred and requires admin confirmation/audit for schedule overrides, access-device changes, notification sends/tests, and physical-access impacting actions. Schedules, assigned vehicles, people, garage-door bindings, and visitor passes directly affect access decisions.
- Recommended fix: Require `admin_user` on all HTTP mutations that change access policy, identity-to-vehicle mapping, visitor access, or garage/notification bindings. Add server-side confirmation tokens where the action grants or expands physical access, sends messages, or changes hardware-related bindings.
- Effort: Medium
- Risk: Medium. Existing UI may assume standard users can write; migration should be explicit and tested.

### 2. Accepted LPR webhook reads are not durable before `202`

- Severity: Critical
- File path: `backend/app/api/v1/webhooks.py:332`, `backend/app/services/access_events.py:278`, `backend/app/services/access_events.py:503`
- Description: The LPR webhook returns accepted after `enqueue_plate_read()`, but that queue is an in-memory `asyncio.Queue`. Shutdown stops the worker and flushes pending debounce windows, but queued reads are not durably drained.
- Why it matters: A crash or restart immediately after `202` can drop a physical access event entirely. This violates the system's durable movement-saga posture and makes forensic reconstruction incomplete.
- Recommended fix: Create an `access_ingest_events` or movement-saga placeholder row before returning `202`, or move ingest to Redis Streams/Postgres with explicit ack/retry. Surface queue depth and oldest queued age in health.
- Effort: Large
- Risk: High. This touches the LPR critical path and needs compatibility tests for duplicate/idempotent webhook delivery.

## High Priority Issues

### 3. Suppressed reads can publish without durable movement history

- Severity: High
- File path: `backend/app/services/access_events.py:1206`, `backend/app/services/access_events.py:1246`
- Description: `_publish_suppressed_read()` calls `_record_suppressed_movement_read()` and then publishes `plate_read.suppressed`. `_record_suppressed_movement_read()` catches all exceptions and only logs.
- Why it matters: `AGENTS.md` says suppressed reads must be durable `SUPPRESSED` movements, not ephemeral drops. A DB failure still emits realtime suppression, creating a false sense of durable history.
- Recommended fix: Return a success/failure value from the persistence method and do not publish a terminal suppressed event until durability succeeds. Add retry/dead-letter handling and tests for DB failure.
- Effort: Medium
- Risk: Medium. Needs careful behavior for transient DB errors during webhook storms.

### 4. Gate reconciliation is not device-specific

- Severity: High
- File path: `backend/app/services/movement_reconciliation.py:265`, `backend/app/models/core.py:625`, `backend/app/models/core.py:1015`
- Description: `_gate_open_observation_after_command()` filters by open state and time window, but not by `GateStateObservation.gate_entity_id` against `GateCommandRecord.gate_key` or binding external ID.
- Why it matters: With multiple gates/garage doors, an unrelated open observation can reconcile a command and commit presence.
- Recommended fix: Store the physical provider/external ID used by each gate command or map `gate_key` to allowed observation IDs, then filter reconciliation by that identity. Add multi-gate tests.
- Effort: Medium
- Risk: High. Incorrect fixes can block legitimate reconciliation.

### 5. Access-device and ESPHome configuration paths lack required confirmations

- Severity: High
- File path: `backend/app/api/v1/access_devices.py:56`, `backend/app/api/v1/access_devices.py:118`, `backend/app/api/v1/integrations.py:220`, `frontend/src/views/SettingsViews.tsx:681`
- Description: Access-device create/update/delete/binding and ESPHome device add/update/delete/test are admin-only and audited, but do not consume server-side action confirmations. The frontend also calls these routes directly without the established confirmation helper.
- Why it matters: These routes bind software access policy to physical hardware. `AGENTS.md` explicitly requires admin confirmation/audit for access-device config and provider bindings.
- Recommended fix: Add confirmation fields to request bodies, consume them before mutations/tests, and update `SettingsViews.tsx` to use `createActionConfirmation()`.
- Effort: Medium
- Risk: Medium. Must avoid breaking existing forms.

### 6. Confirmation/admin tokens leak through URLs or process arguments

- Severity: High
- File path: `frontend/src/views/logExplorer/LogsWorkspace.tsx:102`, `frontend/src/views/IntegrationsView.tsx:2736`, `backend/app/api/v1/telemetry.py:255`, `backend/app/api/v1/unifi_protect.py:277`, `scripts/load-test.mjs:140`, `scripts/load-test.mjs:168`
- Description: Telemetry purge and UniFi backup delete send `confirmation_token` in query strings. The load-test script mints an Admin token and passes it through `autocannon` command-line headers and WebSocket query params.
- Why it matters: Query strings and process args are routinely captured in browser history, shell history, process inspection, Nginx/proxy logs, and telemetry.
- Recommended fix: Move confirmation tokens to JSON request bodies for DELETEs. Prefer short-lived test users or manually supplied tokens for load tests; pass bearer tokens via environment or stdin, and avoid WebSocket query auth where possible.
- Effort: Small to Medium
- Risk: Low to Medium. Requires API and frontend shape changes for two DELETE routes.

### 7. Infrastructure services are exposed broadly with dev defaults

- Severity: High
- File path: `docker-compose.yml:87`, `docker-compose.yml:103`, `.env.example:12`
- Description: Postgres and Redis are published on host interfaces. The example Postgres password is `iacs_dev_password`; Redis has no auth.
- Why it matters: On a LAN host, database and Redis exposure can turn a dashboard compromise or local-network scan into direct data/control access.
- Recommended fix: Bind DB/Redis to `127.0.0.1` by default, document when to expose them, and require operators to set a non-default database password before production/LAN deployment.
- Effort: Small
- Risk: Medium. Existing LAN maintenance workflows may rely on direct ports.

### 8. Simulation LPR endpoints can drive live access decisions

- Severity: High
- File path: `backend/app/simulation/router.py:21`, `backend/app/simulation/router.py:42`, `backend/app/simulation/router.py:71`, `backend/app/services/access_events.py:2991`
- Description: `/api/v1/simulation/arrival/{registration_number}` and `/api/v1/simulation/misread-sequence/{registration_number}` lack `admin_user`; only `/api/v1/simulation/e2e/full-access-flow` is Admin-only. Because the router is mounted under the globally authenticated API, any authenticated user can inject synthetic plate reads.
- Why it matters: For a known and allowed plate, the synthetic read enters the same access-event service that can reach automatic LPR grant and gate command coordination.
- Recommended fix: Require `admin_user` and an action confirmation for live-pipeline simulation endpoints, or gate them behind an explicit dev/test bootstrap setting unavailable in production.
- Effort: Small
- Risk: Medium. Existing demo/test workflows may rely on non-admin simulation access.

## Medium Priority Issues

### 9. Public automation webhooks rely only on URL keys

- Severity: Medium
- File path: `backend/app/main.py:187`, `backend/app/api/v1/automations.py:214`, `backend/app/services/automations.py:939`, `backend/app/services/automations.py:1201`, `frontend/src/views/WorkflowViews.tsx:1751`
- Description: `/api/v1/automations/webhooks/{webhook_key}` is intentionally public and selects rules by URL key. The default frontend key is generated with `Math.random()`, and there is no HMAC/header secret, mandatory source policy, replay protection, or rate control.
- Why it matters: If a key is guessed, weak, or leaked, an unauthenticated caller can trigger any Admin-created rule bound to that webhook, including gate, garage, notification, maintenance, or integration actions.
- Recommended fix: Generate webhook keys server-side with high entropy, display once, optionally require HMAC signatures for high-impact actions, enforce source allowlists where configured, and add replay/rate controls.
- Effort: Medium
- Risk: Medium. Needs migration for existing webhook senders.

### 10. Public webhook body handling has DoS exposure

- Severity: Medium
- File path: `backend/app/api/v1/webhooks.py:113`, `backend/app/api/v1/webhooks.py:214`, `backend/app/api/v1/automations.py:220`, `docker-compose.yml:47`
- Description: WhatsApp reads the full body before signature validation; LPR and automation webhooks parse full JSON without explicit backend caps. The backend is published directly on port 8088, bypassing the frontend Nginx body limit.
- Why it matters: Public webhook routes are unauthenticated by design. Oversized payloads can consume memory/CPU before rejection.
- Recommended fix: Add body-size middleware or endpoint-level `Content-Length` checks and streaming limits. Keep limits aligned with webhook provider specs and chat upload limits.
- Effort: Small to Medium
- Risk: Low

### 11. LPR source allowlist and proxy topology can conflict

- Severity: Medium
- File path: `backend/app/services/lpr_webhook_security.py:87`, `frontend/nginx.conf:18`, `docker-compose.yml:47`
- Description: LPR source verification uses direct ASGI client IP and ignores forwarded headers, while frontend Nginx proxies `/api/*` to the backend.
- Why it matters: This is secure against spoofed `X-Forwarded-For`, but if operators route UNVR through Nginx, the observed source becomes the proxy/container. Allowlisting the proxy restores function but weakens the intended static UNVR allowlist.
- Recommended fix: Document direct-backend LPR webhook routing, or implement trusted-proxy-aware source verification that only trusts forwarded headers from configured proxies.
- Effort: Medium
- Risk: Medium

### 12. Current full backend test run fails one realtime behavior test

- Severity: Medium
- File path: `backend/app/api/v1/realtime.py:42`, `backend/tests/test_realtime.py:40`
- Description: `_handle_client_realtime_message()` now emits `connection.error` for malformed JSON, while the test still expects malformed/non-ping control messages to be ignored.
- Why it matters: The mismatch means either behavior changed without updating tests or the route regressed. Realtime reliability is important for ops consoles.
- Recommended fix: Decide the intended WebSocket contract. If error messages are desired, update tests and frontend handling; otherwise remove the malformed JSON send path.
- Effort: Small
- Risk: Low

### 13. CI coverage is too narrow

- Severity: Medium
- File path: `.github/workflows/backend-alfred.yml:5`, `.github/workflows/backend-alfred.yml:55`, `frontend/package.json:6`
- Description: The only workflow is path-filtered to Alfred-related files and runs `pytest -m alfred_critical`; only five test references use that marker. There is no full backend, frontend build, Docker/compose config, migration smoke, or dependency audit CI.
- Why it matters: Safety-critical regressions in LPR, access devices, schedules, visitor passes, webhooks, and frontend confirmations can merge without CI.
- Recommended fix: Add at least four jobs: backend full tests, frontend `npm ci && npm run build`, `docker compose config --quiet`, and targeted security/dependency audit.
- Effort: Medium
- Risk: Low

### 14. Runtime backend image carries dev/test/browser tooling

- Severity: Medium
- File path: `backend/Dockerfile:13`, `backend/Dockerfile:20`
- Description: The backend runtime installs Node/npm/ripgrep, dev extras, and Playwright Chromium, and does not set a non-root runtime user.
- Why it matters: Larger image and broader runtime toolchain increase attack surface and rebuild time.
- Recommended fix: Split test/browser tooling into a dev/test image or optional stage. Run production backend as a non-root user.
- Effort: Medium
- Risk: Medium

### 15. Dependency reproducibility is weak on backend

- Severity: Medium
- File path: `backend/pyproject.toml:6`, `backend/Dockerfile:1`, `frontend/Dockerfile:1`, `docker-compose.yml:80`
- Description: Backend dependencies are mostly lower bounds and there is no committed Python lockfile. Docker image tags are mutable rather than digest-pinned.
- Why it matters: Builds can drift without source changes, making incident response and rollback harder.
- Recommended fix: Introduce a Python lock workflow such as `uv lock` or `pip-tools`, commit the lock, and pin base images by digest for release builds.
- Effort: Medium
- Risk: Medium

### 16. Alembic is present but not a complete schema path

- Severity: Medium
- File path: `backend/app/db/bootstrap.py:15`, `backend/alembic/versions/20260509_0001_add_alfred_semantic_embeddings.py:14`
- Description: Startup still uses `Base.metadata.create_all` plus hundreds of lines of transitional DDL. The only Alembic revision assumes existing Alfred tables.
- Why it matters: Fresh `alembic upgrade head` is not equivalent to bootstrapping the application schema, so schema drift remains hard to review and reproduce.
- Recommended fix: Cut a baseline Alembic revision from current metadata, move transitional DDL into migrations, and add migration smoke tests.
- Effort: Large
- Risk: High

### 17. Upload size contract is inconsistent

- Severity: Medium
- File path: `frontend/nginx.conf:4`, `backend/app/services/chat_attachments.py:21`, `frontend/src/views/ChatWidgetView.tsx:238`
- Description: Nginx caps request bodies at 12 MB while backend chat attachments allow 25 MB. The frontend upload path lacks a client-side size guard.
- Why it matters: Users see proxy-level failures for files the app advertises as acceptable.
- Recommended fix: Align Nginx, backend, and frontend limits; show a client-side error before upload.
- Effort: Small
- Risk: Low

### 18. Settings coercion can silently send invalid numeric values

- Severity: Medium
- File path: `frontend/src/shared.tsx:1068`, `frontend/src/shared.tsx:1083`, `frontend/src/views/SettingsViews.tsx:1752`
- Description: Numeric settings use `Number(value)`. Empty strings become `0`; invalid strings become `NaN`, which JSON serializes as `null`.
- Why it matters: Auth token durations and LPR tuning can be accidentally changed to invalid values.
- Recommended fix: Validate finite numbers in the frontend before save, preserve blank as "do not update" where appropriate, and add backend bounds validation for dynamic settings.
- Effort: Small
- Risk: Low

### 19. Log CSV export is formula-injection prone

- Severity: Medium
- File path: `frontend/src/views/logExplorer/utils.ts:541`, `frontend/src/views/logExplorer/utils.ts:584`
- Description: CSV cells are quoted but not neutralized when they begin with `=`, `+`, `-`, or `@`.
- Why it matters: Attacker-controlled log or telemetry fields can execute formulas when opened in Excel/Sheets.
- Recommended fix: Prefix formula-leading cells with a single quote or tab and add tests around CSV export.
- Effort: Small
- Risk: Low

### 20. Fire-and-forget audit writes weaken durable audit guarantees

- Severity: Medium
- File path: `backend/app/services/telemetry.py:319`, `backend/app/services/telemetry.py:607`, `backend/app/services/telemetry.py:654`, `backend/app/api/v1/access_devices.py:62`
- Description: `emit_audit_log()` enqueues asynchronous persistence. Several mutation routes return success before audit persistence has committed.
- Why it matters: `AGENTS.md` says durable state changes need audit history. If the background task fails or shutdown races it, a mutation can exist without audit.
- Recommended fix: Use awaited `write_audit_log()` inside the same DB transaction for safety-critical state changes; reserve fire-and-forget for non-critical telemetry.
- Effort: Medium
- Risk: Medium

### 21. Notification API can look synchronous when delivery is asynchronous

- Severity: Medium
- File path: `backend/app/services/notifications.py:676`
- Description: `NotificationService.notify()` returns a composed notification after publishing `notification.trigger` unless `raise_on_failure=True`; actual delivery occurs later by listener.
- Why it matters: Callers can accidentally treat enqueue success as delivery success.
- Recommended fix: Rename/split APIs into `enqueue_notification` and `send_notification_now`, or return a durable run/outbox ID that callers must observe.
- Effort: Medium
- Risk: Medium

### 22. Action confirmation replay can clobber prior success outcome

- Severity: Medium
- File path: `backend/app/services/action_confirmations.py:130`, `backend/app/services/action_confirmations.py:191`
- Description: A validation failure always sets `row.outcome = "rejected"`. Reusing an already consumed token raises "already used", then overwrites a previously consumed row outcome with rejected.
- Why it matters: Audit history for the original confirmed action is weakened.
- Recommended fix: Do not mutate already-consumed rows on replay; emit a separate rejection audit event instead.
- Effort: Small
- Risk: Low

### 23. Standard users can list and download UniFi Protect backups

- Severity: Medium-Low
- File path: `backend/app/api/v1/unifi_protect.py:216`, `backend/app/api/v1/unifi_protect.py:221`
- Description: UniFi Protect backup list/download routes use `current_user`, while backup create/restore/delete use Admin confirmation.
- Why it matters: Backup archives can expose integration setting keys, metadata, package state, and encrypted secret blobs. The secrets are not plaintext, but backups are still sensitive operational material.
- Recommended fix: Make backup list/download Admin-only, or split safe summaries from archive downloads and require Admin for file retrieval.
- Effort: Small
- Risk: Low

## Low Priority Issues

### 24. Frontend Docker context lacks `.dockerignore`

- Severity: Low
- File path: `frontend/` and `docker-compose.yml:3`
- Description: No `frontend/.dockerignore` exists, so local `node_modules`, `dist`, and test outputs can be sent in the build context if present.
- Why it matters: Slower builds and more accidental context leakage.
- Recommended fix: Add `frontend/.dockerignore` for `node_modules`, `dist`, `test-results`, logs, caches, and local env files.
- Effort: Small
- Risk: Low

### 25. Some docs contain machine-local paths

- Severity: Low
- File path: `README.md:7`, `README.md:92`, `docs/phase-5.md:70`
- Description: Docs reference `/Users/jas/...` and Codex-generated local image paths.
- Why it matters: Other checkouts cannot use those links, and local environment shape leaks into docs.
- Recommended fix: Replace with repo-relative paths or checked-in assets.
- Effort: Small
- Risk: Low

### 26. Dead dashboard controls create false affordances

- Severity: Low
- File path: `frontend/src/shared.tsx:652`, `frontend/src/views/DashboardView.tsx:291`
- Description: `PanelHeader` renders action buttons even when no handler exists; several Dashboard "View all" controls pass no handler.
- Why it matters: Operators can click controls that do nothing.
- Recommended fix: Hide action buttons when `onAction` is absent, or wire them to relevant routes.
- Effort: Small
- Risk: Low

### 27. Unknown dynamic setting keys are silently ignored

- Severity: Low
- File path: `backend/app/services/settings.py:784`
- Description: `update_settings()` continues on keys not in `DEFAULT_DYNAMIC_SETTINGS`.
- Why it matters: Typos or stale frontend settings can appear to save successfully while no state changes.
- Recommended fix: Reject unknown keys with a 400 response and include the unknown key list.
- Effort: Small
- Risk: Medium if any clients rely on partial updates with stale keys.

## Bugs and Correctness Issues

- Gate reconciliation can match the wrong gate observation. See Finding 4.
- Realtime test failure shows contract drift for malformed WebSocket messages. See Finding 10.
- Numeric settings can serialize invalid input as `null` or `0`. See Finding 16.
- Confirmation replay can rewrite consumed outcome. See Finding 20.
- Uploads between 12 MB and 25 MB fail at the proxy instead of app validation. See Finding 15.

## Dead Code Findings

- `backend/app/ai/tools.py` remains a large legacy facade even though tool groups exist. It should be reduced gradually, not deleted wholesale.
- `backend/app/db/bootstrap.py` contains transitional migration logic that should be considered migration debt rather than permanent application code.
- `backend/build/` contains 73 untracked build-artifact files excluded from this audit. It is not tracked, but it should be removed locally or ignored if it recurs.
- 233 source/docs hits contain terms such as TODO/FIXME/HACK/legacy/obsolete/deprecated/cleanup. Many are legitimate compatibility notes, but the count is high enough to warrant a debt triage pass.

## Performance Findings

- Frontend production build passes, but CSS is 315 KB uncompressed and major view chunks are large: `IntegrationsView` about 109 KB gzip source chunk output, `LogsView` about 95 KB, `WorkflowViews` about 94 KB.
- Backend runtime image installs Playwright Chromium and dev dependencies, increasing image size and cold-build time.
- `AccessEventService` uses an unbounded in-memory queue; under webhook spikes this is both a memory and durability risk.
- Large route/service files slow review and increase the odds that AI agents make local changes without understanding cross-file contracts.

## Security Findings

- Standard-user mutation access on schedules, directory records, and visitor passes is the most important authorization issue.
- Simulation LPR endpoints should be Admin-only or disabled outside development because they feed the live movement pipeline.
- Public automation webhooks should not rely only on URL keys when they can trigger hardware or notification actions.
- Confirmation token placement in query strings and load-test token handling should be fixed.
- Postgres and Redis host exposure should be hardened for LAN deployments.
- Webhook body limits should be enforced in the backend, not only Nginx.
- CSV export should neutralize spreadsheet formulas.
- UniFi Protect backup downloads should be treated as Admin-only operational material.
- Integration test error logging should continue to be reviewed for secret redaction, especially providers that put keys in query strings.
- `IACS_TRUSTED_HOSTS` defaults to `*`, and `auth_cookie_secure` defaults to false. These are acceptable for local bootstrap but should be called out in production/LAN hardening docs.

## Reliability Findings

- Accepted LPR reads need durable ingest before `202`.
- Suppressed reads need durability before terminal publish.
- Reconciliation needs device-specific gate observation matching.
- Fire-and-forget audits should not be used for safety-critical durable mutations.
- Direct backend exposure bypasses frontend Nginx request-size limits.
- Notification enqueue success and delivery success should be represented separately.

## Observability Findings

- The health endpoint is useful and currently reports healthy DB, Redis realtime, access events, maintenance, Home Assistant, Discord, and WhatsApp through the frontend proxy.
- Audit and telemetry models are strong, but some mutation routes use asynchronous audit persistence.
- Query-string redaction exists in telemetry tests, but the system should avoid putting confirmation tokens into URLs at all.
- Queue metrics show depth, but not oldest queued read age or durable ingest lag because ingest is not durable yet.

## Testing Gaps

- Full backend test run inside the container currently fails `tests/test_realtime.py::test_realtime_websocket_ignores_non_ping_control_messages`.
- Add tests that standard users cannot mutate schedules, directory records, visitor passes, access devices, or integration provider bindings.
- Add tests that standard users cannot call live-pipeline simulation endpoints or download UniFi Protect backup archives.
- Add tests for public automation webhook entropy/HMAC/source policy once hardened.
- Add tests that confirmation is required for access-device and ESPHome config/test routes.
- Add a multi-gate reconciliation test proving an observation for gate B cannot reconcile a command for gate A.
- Add DB-failure tests for suppressed-read durability.
- Add LPR accepted-read durability/restart tests once durable ingest exists.
- Add frontend tests for confirmation flows, CSV export, numeric settings validation, and chat upload size limits.
- Expand CI beyond targeted Alfred checks.

## AI-Agent Maintainability Assessment

The codebase is workable for AI-assisted maintenance, but only with careful scoping. The main issue is not style; it is cognitive blast radius. Files such as `backend/app/ai/tools.py`, `backend/app/services/access_events.py`, `backend/app/services/whatsapp_messaging.py`, `frontend/src/views/IntegrationsView.tsx`, and `frontend/src/views/WorkflowViews.tsx` require long-range context to change safely.

AI-agent risk score: High for broad refactors, Medium for scoped route/service/test fixes.

Recommended guardrails:

- Require a failing test or explicit invariant before editing LPR, gate commands, reconciliation, confirmations, or auth.
- Prefer narrow fixes plus tests over opportunistic cleanup.
- Continue migrating AI tool bodies from `backend/app/ai/tools.py` into `tool_groups/*` one group at a time.
- Add route-level helpers for admin+confirmation requirements so future agents cannot miss them endpoint by endpoint.

## Technical Debt Assessment

Highest debt:

- Monolithic service/view files.
- Transitional schema code in bootstrap.
- Confirmation safety expressed inconsistently.
- CI not representative of production risk.
- Runtime image mixing production, dev, test, and browser tooling.

Debt that is intentional/acceptable for now:

- `Base.metadata.create_all` bootstrap is acknowledged in `AGENTS.md` as transitional until schema stabilizes.
- Compatibility wrappers for snapshots are acceptable if new code continues to use `SnapshotManager`.
- `chat.py` facade and `app.ai.tools` shims are acceptable while tool groups are migrated carefully.

## Quick Wins

1. Fix or update `test_realtime_websocket_ignores_non_ping_control_messages`.
2. Move telemetry purge and UniFi backup delete confirmation tokens from query string to body.
3. Add `frontend/.dockerignore`.
4. Align Nginx and backend chat attachment size limits.
5. Add frontend finite-number validation for dynamic settings.
6. Add standard-user denial tests around schedules, directory, visitor passes, and access devices.
7. Bind Postgres and Redis to loopback by default in `.env.example`/compose docs.

## Recommended Refactors

1. Introduce route helpers or dependencies such as `confirmed_admin_action(action, payload)` for all safety-critical mutations.
2. Add a durable LPR ingest table/stream and process from that source rather than in-memory queue acceptance.
3. Make gate observations and gate commands share a stable physical device identity.
4. Extract `AccessEventService` into ingest, debounce/session, decision, event commit, garage side effects, and telemetry collaborators behind existing tests.
5. Split `IntegrationsView.tsx` by integration domain and preserve route-level lazy loading.
6. Move bootstrap DDL into Alembic baseline and follow-on revisions.
7. Split backend Dockerfile into prod and dev/test targets.

## Areas To Avoid Touching Yet

- Do not rewrite the movement FSM and ledger together; fix durable ingest and reconciliation identity first.
- Do not remove `backend/app/ai/tools.py` facade shims until all public imports and tests prove the new tool groups cover them.
- Do not change provider contracts while access-device confirmation gaps are still open.
- Do not broaden frontend UI refactors while there are existing uncommitted user edits in `frontend/src/styles/*` and several view files.
- Do not replace bootstrap with Alembic in one untested jump; create a baseline and smoke it.

## Long-Term Architecture Recommendations

- Treat physical-access decisions like financial transactions: accepted external input should have a durable ingest row, idempotency key, processing state, and retry/dead-letter handling.
- Make safety policies declarative in route metadata: required role, confirmation action, audited entity, and side-effect type.
- Keep vendor I/O under `modules/*`, but also keep physical device identity normalized in the core DB so reconciliation is not provider-specific.
- Promote an outbox pattern for notifications, audit events, and possibly realtime fanout.
- Establish "thin route, explicit service, durable repository" as the default shape for new backend code.
- Add frontend route-level error boundaries and a small test harness for destructive/admin actions.

## Suggested Future Codex Tasks

1. Fix authorization and confirmation coverage for schedules, directory mutations, visitor passes, access devices, and ESPHome config.
2. Lock live simulation endpoints and UniFi backup archive downloads to Admin-only access.
3. Harden public automation webhooks with server-generated keys, HMAC/source policy, replay/rate controls, and migration for existing senders.
4. Implement durable LPR ingest with tests for restart/crash after webhook acceptance.
5. Fix gate reconciliation to match command device identity and add multi-gate tests.
6. Move DELETE confirmation tokens to request bodies and update clients/tests.
7. Add CI for full backend pytest, frontend build, compose config, and dependency audit.
8. Split the backend Dockerfile into production and dev/test stages.
9. Start Alembic baseline migration and add migration smoke tests.
10. Break `frontend/src/views/IntegrationsView.tsx` into domain components after current user edits settle.

## Validation Results

Commands run on 2026-05-31:

| Command | Result | Notes |
| --- | --- | --- |
| `python3 -m compileall -q backend/app` | Passed | No syntax errors in backend app. |
| `docker compose config --quiet` | Passed | Compose config is valid. |
| `docker compose ps` | Passed | Frontend, backend, Postgres, Redis, updater were running; DB and backend were healthy. |
| `curl -fsS http://localhost:8089/api/v1/health` | Passed | Health returned `status: ok` with DB/realtime/access-events integrations healthy. |
| `curl -fsS http://localhost:8089/api/v1/auth/status` | Passed | Returned setup complete and unauthenticated status. |
| `cd frontend && npm run build` | Passed | Production Vite build completed. |
| `docker compose exec -T backend sh -lc 'cd /workspace/backend && python -m pytest'` | Failed | 656 passed, 1 failed: `tests/test_realtime.py::test_realtime_websocket_ignores_non_ping_control_messages`. |

Pytest failure summary:

```text
Expected websocket.sent == [] for malformed/non-ping messages.
Actual included one connection.error payload for malformed JSON.
```

## Files Modified

- Added `docs/code-review/full-project-maintenance-audit.md`.

No application code was changed. No safe low-risk code fix was applied because the obvious fixes either require a product/security decision, affect safety-critical behavior, or overlap existing user-modified frontend files.
