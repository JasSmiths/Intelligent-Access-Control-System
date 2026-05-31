# Codebase Optimisation Audit

Date: 2026-05-31
Scope: tracked files plus untracked, not-ignored files in the repository. Generated dependency/cache/runtime folders were excluded: `node_modules`, `.venv`, `venv`, `__pycache__`, `dist`, `build`, `coverage`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `data`, and `logs`.
Note: this report was generated after one tiny cleanup and is not counted in the before/after source totals.

# Executive Summary

IACS is functional and well-tested, but it is getting heavy. The backend safety model, durable movement records, integrations, and Alfred tooling are valuable; the maintainability risk is that too much behavior now lives in a small number of giant files. Five files exceed 4,000 lines, 22 exceed 2,000 lines, and 79 exceed 500 lines. That is too much cognitive surface for a physical-access system where changes must preserve auth, confirmations, LPR decisions, gate behavior, and webhook contracts.

I applied only one safe code simplification: removed accidental LPR ingest status re-exports from `backend/app/services/access_events.py` and made `backend/tests/test_access_events.py` import those constants from `backend/app/services/lpr_ingest.py`, their owning module. Net source reduction: 1 line. Broader reductions are recommended below, but most touch safety-critical or large dirty files and should be done as focused follow-up tasks.

# Repository Line Count Summary

| Metric | Count |
| --- | ---: |
| Total reviewable files | 311 |
| Total lines before simplification | 162,763 |
| Total non-empty lines before simplification | 145,628 |
| Total lines after simplification | 162,762 |
| Total non-empty lines after simplification | 145,627 |
| Net line reduction, excluding this report | 1 |
| Files over 500 lines | 79 |
| Files over 1,000 lines | 43 |
| Files over 2,000 lines | 22 |
| Files over 4,000 lines | 5 |

Largest files:

| Path | Lines | Non-empty | Complexity |
| --- | ---: | ---: | --- |
| `backend/app/ai/tools.py` | 7,757 | 7,071 | Very High |
| `frontend/src/views/IntegrationsView.tsx` | 5,008 | 4,775 | Very High |
| `frontend/src/views/WorkflowViews.tsx` | 4,277 | 4,051 | Very High |
| `backend/app/services/access_events.py` | 4,265 | 3,990 | Very High |
| `backend/app/services/whatsapp_messaging.py` | 4,127 | 3,800 | Very High |
| `backend/tests/test_chat_agent.py` | 3,807 | 3,224 | High |
| `frontend/src/styles/data-views.css` | 3,425 | 2,939 | High |
| `frontend/src/styles/passes-schedules.css` | 3,312 | 2,832 | High |
| `backend/app/services/chat.py` | 3,309 | 3,089 | High |
| `frontend/src/styles/chat-responsive.css` | 3,218 | 2,728 | High |
| `backend/tests/test_access_events.py` | 3,081 | 2,564 | High |
| `backend/uv.lock` | 3,013 | 2,891 | High |
| `backend/tests/test_whatsapp_messaging.py` | 3,004 | 2,458 | High |
| `frontend/src/styles/workflows.css` | 2,968 | 2,553 | High |
| `backend/app/services/notifications.py` | 2,854 | 2,647 | High |
| `backend/app/services/dependency_updates.py` | 2,612 | 2,418 | High |
| `frontend/src/main.tsx` | 2,593 | 2,423 | High |
| `backend/app/services/automations.py` | 2,465 | 2,288 | High |
| `frontend/package-lock.json` | 2,380 | 2,380 | High |
| `frontend/src/views/DirectoryViews.tsx` | 2,322 | 2,190 | High |
| `frontend/src/views/ChatWidgetView.tsx` | 2,066 | 1,982 | High |
| `frontend/src/views/PassesView.tsx` | 2,035 | 1,914 | High |
| `backend/tests/test_notification_workflows.py` | 1,942 | 1,618 | High |
| `frontend/src/styles/integrations.css` | 1,905 | 1,645 | High |
| `frontend/src/views/SettingsViews.tsx` | 1,806 | 1,711 | High |

# Reviewed File Inventory

| Path | Type | Lines | Non-empty | Complexity | Purpose | Simplification recommendation |
| --- | --- | ---: | ---: | --- | --- | --- |
| `.env.example` | Env template | 41 | 33 | Low | Example bootstrap environment file. | No immediate simplification needed. |
| `.github/workflows/backend-alfred.yml` | YAML | 121 | 117 | Low | GitHub Actions workflow. | No immediate simplification needed. |
| `.gitignore` | Text | 19 | 18 | Low | Ignore/build-context configuration. | No immediate simplification needed. |
| `AGENTS.md` | Markdown | 329 | 312 | Low | Repository documentation/instructions. | No immediate simplification needed. |
| `ESPHOME.md` | Markdown | 100 | 71 | Low | Repository documentation/instructions. | No immediate simplification needed. |
| `README.md` | Markdown | 151 | 111 | Low | Repository documentation/instructions. | No immediate simplification needed. |
| `backend/.dockerignore` | Text | 7 | 7 | Low | Ignore/build-context configuration. | No immediate simplification needed. |
| `backend/Dockerfile` | Dockerfile | 57 | 42 | Low | Container build definition. | No immediate simplification needed. |
| `backend/README.md` | Markdown | 13 | 9 | Low | Repository documentation/instructions. | No immediate simplification needed. |
| `backend/alembic.ini` | INI | 38 | 30 | Low | INI support file. | No immediate simplification needed. |
| `backend/alembic/env.py` | Python | 55 | 40 | Low | Python support file. | No immediate simplification needed. |
| `backend/alembic/script.py.mako` | Mako | 26 | 17 | Low | Mako support file. | No immediate simplification needed. |
| `backend/alembic/versions/20260509_0001_add_alfred_semantic_embeddings.py` | Python | 68 | 56 | Low | Alembic schema migration revision. | Leave alone after merge; migration history should stay append-only. |
| `backend/alembic/versions/20260531_0000_current_schema_baseline.py` | Python | 30 | 20 | Low | Alembic schema migration revision. | Leave alone after merge; migration history should stay append-only. |
| `backend/alembic/versions/20260531_0002_automation_webhook_hardening.py` | Python | 154 | 143 | Low | Alembic schema migration revision. | Leave alone after merge; migration history should stay append-only. |
| `backend/alembic/versions/20260531_0003_notification_runs.py` | Python | 79 | 70 | Low | Alembic schema migration revision. | Leave alone after merge; migration history should stay append-only. |
| `backend/app/__init__.py` | Python | 1 | 1 | Low | Python support file. | Leave alone; package marker with negligible cost. |
| `backend/app/ai/__init__.py` | Python | 1 | 1 | Low | Python support file. | Leave alone; package marker with negligible cost. |
| `backend/app/ai/providers.py` | Python | 1,219 | 1,094 | High | Python support file. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/ai/tool_groups/__init__.py` | Python | 1 | 1 | Low | Alfred   init   tool metadata/catalog. | Leave alone; package marker with negligible cost. |
| `backend/app/ai/tool_groups/_facade_handlers.py` | Python | 25 | 17 | Low | Alfred  facade tool handlers. | No immediate simplification needed. |
| `backend/app/ai/tool_groups/access_diagnostics.py` | Python | 528 | 520 | Medium | Alfred access diagnostics tool metadata/catalog. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/ai/tool_groups/access_diagnostics_handlers.py` | Python | 23 | 20 | Low | Alfred access diagnostics tool handlers. | No immediate simplification needed. |
| `backend/app/ai/tool_groups/automations.py` | Python | 195 | 187 | Low | Alfred automations tool metadata/catalog. | No immediate simplification needed. |
| `backend/app/ai/tool_groups/automations_handlers.py` | Python | 14 | 11 | Low | Alfred automations tool handlers. | No immediate simplification needed. |
| `backend/app/ai/tool_groups/compliance_cameras_files.py` | Python | 141 | 135 | Low | Alfred compliance cameras files tool metadata/catalog. | No immediate simplification needed. |
| `backend/app/ai/tool_groups/compliance_cameras_files_handlers.py` | Python | 12 | 9 | Low | Alfred compliance cameras files tool handlers. | No immediate simplification needed. |
| `backend/app/ai/tool_groups/gate_maintenance.py` | Python | 266 | 259 | Low | Alfred gate maintenance tool metadata/catalog. | No immediate simplification needed. |
| `backend/app/ai/tool_groups/gate_maintenance_handlers.py` | Python | 16 | 13 | Low | Alfred gate maintenance tool handlers. | No immediate simplification needed. |
| `backend/app/ai/tool_groups/general.py` | Python | 89 | 82 | Low | Alfred general tool metadata/catalog. | No immediate simplification needed. |
| `backend/app/ai/tool_groups/general_handlers.py` | Python | 272 | 251 | Low | Alfred general tool handlers. | No immediate simplification needed. |
| `backend/app/ai/tool_groups/metadata.py` | Python | 80 | 67 | Low | Alfred metadata tool metadata/catalog. | No immediate simplification needed. |
| `backend/app/ai/tool_groups/notifications.py` | Python | 202 | 194 | Low | Alfred notifications tool metadata/catalog. | No immediate simplification needed. |
| `backend/app/ai/tool_groups/notifications_handlers.py` | Python | 14 | 11 | Low | Alfred notifications tool handlers. | No immediate simplification needed. |
| `backend/app/ai/tool_groups/registry.py` | Python | 119 | 102 | Low | Alfred registry tool metadata/catalog. | No immediate simplification needed. |
| `backend/app/ai/tool_groups/schedules.py` | Python | 246 | 238 | Low | Alfred schedules tool metadata/catalog. | No immediate simplification needed. |
| `backend/app/ai/tool_groups/schedules_handlers.py` | Python | 15 | 12 | Low | Alfred schedules tool handlers. | No immediate simplification needed. |
| `backend/app/ai/tool_groups/system_operations.py` | Python | 314 | 305 | Low | Alfred system operations tool metadata/catalog. | No immediate simplification needed. |
| `backend/app/ai/tool_groups/system_operations_handlers.py` | Python | 22 | 19 | Low | Alfred system operations tool handlers. | No immediate simplification needed. |
| `backend/app/ai/tool_groups/visitor_passes.py` | Python | 193 | 185 | Low | Alfred visitor passes tool metadata/catalog. | No immediate simplification needed. |
| `backend/app/ai/tool_groups/visitor_passes_handlers.py` | Python | 12 | 9 | Low | Alfred visitor passes tool handlers. | No immediate simplification needed. |
| `backend/app/ai/tools.py` | Python | 7,757 | 7,071 | Very High | Legacy Alfred tool facade, handlers, schemas, diagnostics, and utility code. | Yes: dedicated reduction plan; do not rewrite in one pass. |
| `backend/app/api/__init__.py` | Python | 1 | 1 | Low | Python support file. | Leave alone; package marker with negligible cost. |
| `backend/app/api/confirmations.py` | Python | 27 | 23 | Low | Shared route helper for server-side action confirmations. | No immediate simplification needed. |
| `backend/app/api/dependencies.py` | Python | 29 | 23 | Low | Python support file. | No immediate simplification needed. |
| `backend/app/api/router.py` | Python | 68 | 66 | Low | Central API v1 router assembly. | No immediate simplification needed. |
| `backend/app/api/v1/__init__.py` | Python | 1 | 1 | Low | Versioned FastAPI routes for   init  . | Leave alone; package marker with negligible cost. |
| `backend/app/api/v1/access.py` | Python | 274 | 253 | Low | Versioned FastAPI routes for access. | No immediate simplification needed. |
| `backend/app/api/v1/access_devices.py` | Python | 234 | 208 | Low | Versioned FastAPI routes for access devices. | No immediate simplification needed. |
| `backend/app/api/v1/action_confirmations.py` | Python | 44 | 37 | Low | Versioned FastAPI routes for action confirmations. | No immediate simplification needed. |
| `backend/app/api/v1/ai.py` | Python | 543 | 471 | Medium | Versioned FastAPI routes for ai. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/api/v1/auth.py` | Python | 198 | 167 | Low | Versioned FastAPI routes for auth. | No immediate simplification needed. |
| `backend/app/api/v1/automations.py` | Python | 272 | 235 | Low | Versioned FastAPI routes for automations. | No immediate simplification needed. |
| `backend/app/api/v1/dependency_updates.py` | Python | 198 | 156 | Low | Versioned FastAPI routes for dependency updates. | No immediate simplification needed. |
| `backend/app/api/v1/diagnostics.py` | Python | 43 | 34 | Low | Versioned FastAPI routes for diagnostics. | No immediate simplification needed. |
| `backend/app/api/v1/directory.py` | Python | 1,344 | 1,187 | High | Versioned FastAPI routes for directory. | Yes: move serializers/helpers out of route module; keep routes thin. |
| `backend/app/api/v1/discord.py` | Python | 165 | 144 | Low | Versioned FastAPI routes for discord. | No immediate simplification needed. |
| `backend/app/api/v1/events.py` | Python | 451 | 392 | Low | Versioned FastAPI routes for events. | No immediate simplification needed. |
| `backend/app/api/v1/gate_malfunctions.py` | Python | 75 | 62 | Low | Versioned FastAPI routes for gate malfunctions. | No immediate simplification needed. |
| `backend/app/api/v1/health.py` | Python | 176 | 151 | Low | Versioned FastAPI routes for health. | No immediate simplification needed. |
| `backend/app/api/v1/icloud_calendar.py` | Python | 184 | 159 | Low | Versioned FastAPI routes for icloud calendar. | No immediate simplification needed. |
| `backend/app/api/v1/integrations.py` | Python | 1,103 | 986 | High | Versioned FastAPI routes for integrations. | Yes: move serializers/helpers out of route module; keep routes thin. |
| `backend/app/api/v1/leaderboard.py` | Python | 16 | 12 | Low | Versioned FastAPI routes for leaderboard. | No immediate simplification needed. |
| `backend/app/api/v1/maintenance.py` | Python | 77 | 64 | Low | Versioned FastAPI routes for maintenance. | No immediate simplification needed. |
| `backend/app/api/v1/media.py` | Python | 35 | 30 | Low | Versioned FastAPI routes for media. | No immediate simplification needed. |
| `backend/app/api/v1/notification_snapshots.py` | Python | 25 | 21 | Low | Versioned FastAPI routes for notification snapshots. | No immediate simplification needed. |
| `backend/app/api/v1/notifications.py` | Python | 459 | 413 | Low | Versioned FastAPI routes for notifications. | No immediate simplification needed. |
| `backend/app/api/v1/realtime.py` | Python | 80 | 68 | Low | Versioned FastAPI routes for realtime. | No immediate simplification needed. |
| `backend/app/api/v1/reports.py` | Python | 87 | 76 | Low | Versioned FastAPI routes for reports. | No immediate simplification needed. |
| `backend/app/api/v1/schedules.py` | Python | 247 | 213 | Low | Versioned FastAPI routes for schedules. | No immediate simplification needed. |
| `backend/app/api/v1/search.py` | Python | 984 | 885 | Medium | Versioned FastAPI routes for search. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/api/v1/settings.py` | Python | 389 | 343 | Low | Versioned FastAPI routes for settings. | No immediate simplification needed. |
| `backend/app/api/v1/telemetry.py` | Python | 1,104 | 962 | High | Versioned FastAPI routes for telemetry. | Yes: move serializers/helpers out of route module; keep routes thin. |
| `backend/app/api/v1/unifi_protect.py` | Python | 421 | 371 | Low | Versioned FastAPI routes for unifi protect. | No immediate simplification needed. |
| `backend/app/api/v1/users.py` | Python | 336 | 295 | Low | Versioned FastAPI routes for users. | No immediate simplification needed. |
| `backend/app/api/v1/visitor_passes.py` | Python | 569 | 520 | Medium | Versioned FastAPI routes for visitor passes. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/api/v1/webhooks.py` | Python | 651 | 590 | Medium | Versioned FastAPI routes for webhooks. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/api/v1/whatsapp.py` | Python | 77 | 65 | Low | Versioned FastAPI routes for whatsapp. | No immediate simplification needed. |
| `backend/app/core/__init__.py` | Python | 1 | 1 | Low | Python support file. | Leave alone; package marker with negligible cost. |
| `backend/app/core/auth_secret.py` | Python | 177 | 142 | Low | Python support file. | No immediate simplification needed. |
| `backend/app/core/config.py` | Python | 144 | 117 | Low | Python support file. | No immediate simplification needed. |
| `backend/app/core/crypto.py` | Python | 22 | 14 | Low | Python support file. | No immediate simplification needed. |
| `backend/app/core/logging.py` | Python | 28 | 18 | Low | Python support file. | No immediate simplification needed. |
| `backend/app/db/__init__.py` | Python | 1 | 1 | Low | Database session/bootstrap support. | Leave alone; package marker with negligible cost. |
| `backend/app/db/base.py` | Python | 5 | 3 | Low | Database session/bootstrap support. | No immediate simplification needed. |
| `backend/app/db/bootstrap.py` | Python | 738 | 720 | Medium | Database session/bootstrap support. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/db/session.py` | Python | 13 | 8 | Low | Database session/bootstrap support. | No immediate simplification needed. |
| `backend/app/main.py` | Python | 448 | 397 | Low | Python support file. | No immediate simplification needed. |
| `backend/app/models/__init__.py` | Python | 105 | 103 | Low | SQLAlchemy model/enumeration definitions. | No immediate simplification needed. |
| `backend/app/models/core.py` | Python | 1,479 | 1,256 | High | SQLAlchemy model/enumeration definitions. | Yes eventually: split model declarations by domain once migrations settle. |
| `backend/app/models/enums.py` | Python | 91 | 65 | Low | SQLAlchemy model/enumeration definitions. | No immediate simplification needed. |
| `backend/app/modules/__init__.py` | Python | 5 | 4 | Low | Vendor/provider module for   init   integration behavior. | Leave alone; package marker with negligible cost. |
| `backend/app/modules/access_devices/__init__.py` | Python | 1 | 1 | Low | Vendor/provider module for   init   integration behavior. | Leave alone; package marker with negligible cost. |
| `backend/app/modules/access_devices/base.py` | Python | 97 | 71 | Low | Vendor/provider module for base integration behavior. | No immediate simplification needed. |
| `backend/app/modules/access_devices/esphome.py` | Python | 983 | 909 | Medium | Vendor/provider module for esphome integration behavior. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/modules/access_devices/home_assistant.py` | Python | 146 | 133 | Low | Vendor/provider module for home assistant integration behavior. | No immediate simplification needed. |
| `backend/app/modules/access_devices/registry.py` | Python | 26 | 17 | Low | Vendor/provider module for registry integration behavior. | No immediate simplification needed. |
| `backend/app/modules/announcements/__init__.py` | Python | 1 | 1 | Low | Vendor/provider module for   init   integration behavior. | Leave alone; package marker with negligible cost. |
| `backend/app/modules/announcements/home_assistant_tts.py` | Python | 32 | 24 | Low | Vendor/provider module for home assistant tts integration behavior. | No immediate simplification needed. |
| `backend/app/modules/dvla/__init__.py` | Python | 2 | 1 | Low | Vendor/provider module for   init   integration behavior. | Leave alone; package marker with negligible cost. |
| `backend/app/modules/dvla/vehicle_enquiry.py` | Python | 163 | 137 | Low | Vendor/provider module for vehicle enquiry integration behavior. | No immediate simplification needed. |
| `backend/app/modules/gate/__init__.py` | Python | 1 | 1 | Low | Vendor/provider module for   init   integration behavior. | Leave alone; package marker with negligible cost. |
| `backend/app/modules/gate/access_devices.py` | Python | 43 | 38 | Low | Vendor/provider module for access devices integration behavior. | No immediate simplification needed. |
| `backend/app/modules/gate/base.py` | Python | 30 | 22 | Low | Vendor/provider module for base integration behavior. | No immediate simplification needed. |
| `backend/app/modules/gate/home_assistant.py` | Python | 96 | 85 | Low | Vendor/provider module for home assistant integration behavior. | No immediate simplification needed. |
| `backend/app/modules/home_assistant/__init__.py` | Python | 1 | 1 | Low | Vendor/provider module for   init   integration behavior. | Leave alone; package marker with negligible cost. |
| `backend/app/modules/home_assistant/client.py` | Python | 226 | 191 | Low | Vendor/provider module for client integration behavior. | No immediate simplification needed. |
| `backend/app/modules/home_assistant/covers.py` | Python | 203 | 171 | Low | Vendor/provider module for covers integration behavior. | No immediate simplification needed. |
| `backend/app/modules/home_assistant/input_booleans.py` | Python | 75 | 61 | Low | Vendor/provider module for input booleans integration behavior. | No immediate simplification needed. |
| `backend/app/modules/icloud_calendar/__init__.py` | Python | 1 | 1 | Low | Vendor/provider module for   init   integration behavior. | Leave alone; package marker with negligible cost. |
| `backend/app/modules/icloud_calendar/client.py` | Python | 315 | 262 | Low | Vendor/provider module for client integration behavior. | No immediate simplification needed. |
| `backend/app/modules/lpr/__init__.py` | Python | 1 | 1 | Low | Vendor/provider module for   init   integration behavior. | Leave alone; package marker with negligible cost. |
| `backend/app/modules/lpr/base.py` | Python | 28 | 19 | Low | Vendor/provider module for base integration behavior. | No immediate simplification needed. |
| `backend/app/modules/lpr/ubiquiti.py` | Python | 599 | 498 | Medium | Vendor/provider module for ubiquiti integration behavior. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/modules/messaging/__init__.py` | Python | 15 | 13 | Low | Vendor/provider module for   init   integration behavior. | No immediate simplification needed. |
| `backend/app/modules/messaging/base.py` | Python | 65 | 52 | Low | Vendor/provider module for base integration behavior. | No immediate simplification needed. |
| `backend/app/modules/messaging/discord_bot.py` | Python | 203 | 171 | Low | Vendor/provider module for discord bot integration behavior. | No immediate simplification needed. |
| `backend/app/modules/notifications/__init__.py` | Python | 1 | 1 | Low | Vendor/provider module for   init   integration behavior. | Leave alone; package marker with negligible cost. |
| `backend/app/modules/notifications/apprise_client.py` | Python | 153 | 124 | Low | Vendor/provider module for apprise client integration behavior. | No immediate simplification needed. |
| `backend/app/modules/notifications/base.py` | Python | 34 | 25 | Low | Vendor/provider module for base integration behavior. | No immediate simplification needed. |
| `backend/app/modules/notifications/discord_formatter.py` | Python | 120 | 100 | Low | Vendor/provider module for discord formatter integration behavior. | No immediate simplification needed. |
| `backend/app/modules/notifications/home_assistant_mobile.py` | Python | 65 | 54 | Low | Vendor/provider module for home assistant mobile integration behavior. | No immediate simplification needed. |
| `backend/app/modules/registry.py` | Python | 47 | 36 | Low | Vendor/provider module for registry integration behavior. | No immediate simplification needed. |
| `backend/app/modules/unifi_protect/__init__.py` | Python | 2 | 1 | Low | Vendor/provider module for   init   integration behavior. | Leave alone; package marker with negligible cost. |
| `backend/app/modules/unifi_protect/client.py` | Python | 545 | 460 | Medium | Vendor/provider module for client integration behavior. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/modules/unifi_protect/package.py` | Python | 144 | 114 | Low | Vendor/provider module for package integration behavior. | No immediate simplification needed. |
| `backend/app/schemas/__init__.py` | Python | 1 | 1 | Low | Python support file. | Leave alone; package marker with negligible cost. |
| `backend/app/scripts/__init__.py` | Python | 1 | 1 | Low | Python support file. | Leave alone; package marker with negligible cost. |
| `backend/app/services/__init__.py` | Python | 1 | 1 | Low | Backend   init   service logic. | Leave alone; package marker with negligible cost. |
| `backend/app/services/access_devices.py` | Python | 1,102 | 1,031 | High | Backend access devices service logic. | Yes: split along existing domain boundaries behind current tests. |
| `backend/app/services/access_events.py` | Python | 4,265 | 3,990 | Very High | Backend access events service logic. | Yes: dedicated reduction plan; do not rewrite in one pass. |
| `backend/app/services/action_confirmations.py` | Python | 251 | 226 | Low | Backend action confirmations service logic. | No immediate simplification needed. |
| `backend/app/services/actionable_notifications.py` | Python | 865 | 782 | Medium | Backend actionable notifications service logic. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/services/alert_snapshots.py` | Python | 78 | 60 | Low | Backend alert snapshots service logic. | No immediate simplification needed. |
| `backend/app/services/alfred/__init__.py` | Python | 2 | 1 | Low | Alfred runtime   init   service. | Leave alone; package marker with negligible cost. |
| `backend/app/services/alfred/answer_contracts.py` | Python | 385 | 320 | Low | Alfred runtime answer contracts service. | No immediate simplification needed. |
| `backend/app/services/alfred/embeddings.py` | Python | 138 | 108 | Low | Alfred runtime embeddings service. | No immediate simplification needed. |
| `backend/app/services/alfred/executor.py` | Python | 32 | 25 | Low | Alfred runtime executor service. | No immediate simplification needed. |
| `backend/app/services/alfred/feedback.py` | Python | 1,592 | 1,479 | High | Alfred runtime feedback service. | Yes: split along existing domain boundaries behind current tests. |
| `backend/app/services/alfred/memory.py` | Python | 636 | 580 | Medium | Alfred runtime memory service. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/services/alfred/permissions.py` | Python | 55 | 43 | Low | Alfred runtime permissions service. | No immediate simplification needed. |
| `backend/app/services/alfred/planner.py` | Python | 437 | 397 | Low | Alfred runtime planner service. | No immediate simplification needed. |
| `backend/app/services/alfred/runtime.py` | Python | 69 | 55 | Low | Alfred runtime runtime service. | No immediate simplification needed. |
| `backend/app/services/alfred/streaming.py` | Python | 39 | 34 | Low | Alfred runtime streaming service. | No immediate simplification needed. |
| `backend/app/services/auth.py` | Python | 284 | 223 | Low | Backend auth service logic. | No immediate simplification needed. |
| `backend/app/services/auth_secret_management.py` | Python | 216 | 186 | Low | Backend auth secret management service logic. | No immediate simplification needed. |
| `backend/app/services/automation_integration_actions.py` | Python | 286 | 247 | Low | Backend automation integration actions service logic. | No immediate simplification needed. |
| `backend/app/services/automations.py` | Python | 2,465 | 2,288 | High | Backend automations service logic. | Yes: split along existing domain boundaries behind current tests. |
| `backend/app/services/chat.py` | Python | 3,309 | 3,089 | High | Backend chat service logic. | Yes: split along existing domain boundaries behind current tests. |
| `backend/app/services/chat_attachments.py` | Python | 310 | 263 | Low | Backend chat attachments service logic. | No immediate simplification needed. |
| `backend/app/services/chat_contracts.py` | Python | 297 | 272 | Low | Backend chat contracts service logic. | No immediate simplification needed. |
| `backend/app/services/chat_routing.py` | Python | 377 | 336 | Low | Backend chat routing service logic. | No immediate simplification needed. |
| `backend/app/services/dependency_updates.py` | Python | 2,612 | 2,418 | High | Backend dependency updates service logic. | Yes: split along existing domain boundaries behind current tests. |
| `backend/app/services/discord_messaging.py` | Python | 766 | 696 | Medium | Backend discord messaging service logic. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/services/domain_events.py` | Python | 37 | 23 | Low | Backend domain events service logic. | No immediate simplification needed. |
| `backend/app/services/dvla.py` | Python | 166 | 142 | Low | Backend dvla service logic. | No immediate simplification needed. |
| `backend/app/services/event_bus.py` | Python | 451 | 395 | Low | Backend event bus service logic. | No immediate simplification needed. |
| `backend/app/services/expected_presence.py` | Python | 458 | 410 | Low | Backend expected presence service logic. | No immediate simplification needed. |
| `backend/app/services/gate_commands.py` | Python | 193 | 173 | Low | Backend gate commands service logic. | No immediate simplification needed. |
| `backend/app/services/gate_malfunctions.py` | Python | 1,665 | 1,563 | High | Backend gate malfunctions service logic. | Yes: split along existing domain boundaries behind current tests. |
| `backend/app/services/home_assistant.py` | Python | 645 | 602 | Medium | Backend home assistant service logic. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/services/icloud_calendar.py` | Python | 953 | 859 | Medium | Backend icloud calendar service logic. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/services/leaderboard.py` | Python | 531 | 482 | Medium | Backend leaderboard service logic. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/services/lpr_ingest.py` | Python | 188 | 175 | Low | Backend lpr ingest service logic. | No immediate simplification needed. |
| `backend/app/services/lpr_timing.py` | Python | 729 | 648 | Medium | Backend lpr timing service logic. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/services/lpr_webhook_security.py` | Python | 190 | 159 | Low | Backend lpr webhook security service logic. | No immediate simplification needed. |
| `backend/app/services/lpr_zone_shadow.py` | Python | 364 | 304 | Low | Backend lpr zone shadow service logic. | No immediate simplification needed. |
| `backend/app/services/maintenance.py` | Python | 269 | 238 | Low | Backend maintenance service logic. | No immediate simplification needed. |
| `backend/app/services/messaging_bridge.py` | Python | 344 | 304 | Low | Backend messaging bridge service logic. | No immediate simplification needed. |
| `backend/app/services/movement_fsm.py` | Python | 304 | 267 | Low | Backend movement fsm service logic. | No immediate simplification needed. |
| `backend/app/services/movement_ledger.py` | Python | 518 | 482 | Medium | Backend movement ledger service logic. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/services/movement_reconciliation.py` | Python | 495 | 461 | Low | Backend movement reconciliation service logic. | No immediate simplification needed. |
| `backend/app/services/notification_snapshots.py` | Python | 46 | 30 | Low | Backend notification snapshots service logic. | No immediate simplification needed. |
| `backend/app/services/notifications.py` | Python | 2,854 | 2,647 | High | Backend notifications service logic. | Yes: split along existing domain boundaries behind current tests. |
| `backend/app/services/person_presence_input_booleans.py` | Python | 209 | 190 | Low | Backend person presence input booleans service logic. | No immediate simplification needed. |
| `backend/app/services/profile_photos.py` | Python | 100 | 79 | Low | Backend profile photos service logic. | No immediate simplification needed. |
| `backend/app/services/report_templates/person_movements.html` | HTML | 844 | 755 | Medium | Backend person movements service logic. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/services/reports.py` | Python | 958 | 842 | Medium | Backend reports service logic. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/services/restart_backfill.py` | Python | 1,230 | 1,125 | High | Backend restart backfill service logic. | Yes: split along existing domain boundaries behind current tests. |
| `backend/app/services/schedules.py` | Python | 368 | 325 | Low | Backend schedules service logic. | No immediate simplification needed. |
| `backend/app/services/settings.py` | Python | 831 | 777 | Medium | Backend settings service logic. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/services/snapshot_recovery.py` | Python | 461 | 403 | Low | Backend snapshot recovery service logic. | No immediate simplification needed. |
| `backend/app/services/snapshots.py` | Python | 460 | 404 | Low | Backend snapshots service logic. | No immediate simplification needed. |
| `backend/app/services/telemetry.py` | Python | 723 | 643 | Medium | Backend telemetry service logic. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/services/tts_phonetics.py` | Python | 26 | 19 | Low | Backend tts phonetics service logic. | No immediate simplification needed. |
| `backend/app/services/type_helpers.py` | Python | 17 | 9 | Low | Backend type helpers service logic. | No immediate simplification needed. |
| `backend/app/services/unifi_protect.py` | Python | 629 | 550 | Medium | Backend unifi protect service logic. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/services/unifi_protect_updates.py` | Python | 556 | 504 | Medium | Backend unifi protect updates service logic. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/services/vehicle_visual_detections.py` | Python | 1,114 | 987 | High | Backend vehicle visual detections service logic. | Yes: split along existing domain boundaries behind current tests. |
| `backend/app/services/visitor_passes.py` | Python | 1,081 | 991 | High | Backend visitor passes service logic. | Yes: split along existing domain boundaries behind current tests. |
| `backend/app/services/whatsapp_messaging.py` | Python | 4,127 | 3,800 | Very High | Backend whatsapp messaging service logic. | Yes: dedicated reduction plan; do not rewrite in one pass. |
| `backend/app/simulation/__init__.py` | Python | 1 | 1 | Low | Python support file. | Leave alone; package marker with negligible cost. |
| `backend/app/simulation/router.py` | Python | 117 | 100 | Low | Python support file. | No immediate simplification needed. |
| `backend/app/simulation/scenarios.py` | Python | 1,119 | 1,003 | High | Python support file. | Maybe: review when touched; file is above comfort threshold. |
| `backend/app/workers/__init__.py` | Python | 1 | 1 | Low | Python support file. | Leave alone; package marker with negligible cost. |
| `backend/pyproject.toml` | TOML | 64 | 58 | Low | Backend Python package/tool configuration. | No immediate simplification needed. |
| `backend/tests/conftest.py` | Python | 9 | 6 | Low | Backend pytest coverage for conftest. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_access_devices.py` | Python | 791 | 626 | Medium | Backend pytest coverage for access devices. | Maybe: keep coverage, factor shared fixtures if duplication grows. |
| `backend/tests/test_access_events.py` | Python | 3,081 | 2,564 | High | Backend pytest coverage for access events. | Maybe: split by behavior/fixture only when tests are next touched. |
| `backend/tests/test_action_confirmations.py` | Python | 277 | 223 | Low | Backend pytest coverage for action confirmations. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_actionable_notifications.py` | Python | 426 | 349 | Low | Backend pytest coverage for actionable notifications. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_alerts.py` | Python | 345 | 291 | Low | Backend pytest coverage for alerts. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_auth_secret.py` | Python | 259 | 206 | Low | Backend pytest coverage for auth secret. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_automations.py` | Python | 857 | 712 | Medium | Backend pytest coverage for automations. | Maybe: keep coverage, factor shared fixtures if duplication grows. |
| `backend/tests/test_chat_agent.py` | Python | 3,807 | 3,224 | High | Backend pytest coverage for chat agent. | Maybe: split by behavior/fixture only when tests are next touched. |
| `backend/tests/test_dependency_updates.py` | Python | 672 | 566 | Medium | Backend pytest coverage for dependency updates. | Maybe: keep coverage, factor shared fixtures if duplication grows. |
| `backend/tests/test_directory_people.py` | Python | 220 | 188 | Low | Backend pytest coverage for directory people. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_discord_messaging.py` | Python | 419 | 332 | Low | Backend pytest coverage for discord messaging. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_dvla_service.py` | Python | 107 | 91 | Low | Backend pytest coverage for dvla service. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_event_bus.py` | Python | 269 | 218 | Low | Backend pytest coverage for event bus. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_expected_presence.py` | Python | 238 | 194 | Low | Backend pytest coverage for expected presence. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_gate_commands.py` | Python | 129 | 105 | Low | Backend pytest coverage for gate commands. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_gate_malfunctions.py` | Python | 378 | 309 | Low | Backend pytest coverage for gate malfunctions. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_http_clients.py` | Python | 50 | 42 | Low | Backend pytest coverage for http clients. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_icloud_calendar.py` | Python | 344 | 270 | Low | Backend pytest coverage for icloud calendar. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_leaderboard.py` | Python | 308 | 249 | Low | Backend pytest coverage for leaderboard. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_lpr_timing.py` | Python | 203 | 178 | Low | Backend pytest coverage for lpr timing. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_lpr_zone_shadow.py` | Python | 174 | 135 | Low | Backend pytest coverage for lpr zone shadow. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_messaging_bridge.py` | Python | 65 | 54 | Low | Backend pytest coverage for messaging bridge. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_movement_fsm.py` | Python | 123 | 104 | Low | Backend pytest coverage for movement fsm. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_movement_ledger.py` | Python | 168 | 141 | Low | Backend pytest coverage for movement ledger. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_movement_reconciliation.py` | Python | 320 | 275 | Low | Backend pytest coverage for movement reconciliation. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_notification_workflows.py` | Python | 1,942 | 1,618 | High | Backend pytest coverage for notification workflows. | Maybe: keep coverage, factor shared fixtures if duplication grows. |
| `backend/tests/test_operational_status.py` | Python | 296 | 235 | Low | Backend pytest coverage for operational status. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_profile_photos.py` | Python | 59 | 41 | Low | Backend pytest coverage for profile photos. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_realtime.py` | Python | 44 | 33 | Low | Backend pytest coverage for realtime. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_reports.py` | Python | 404 | 338 | Low | Backend pytest coverage for reports. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_restart_backfill.py` | Python | 171 | 136 | Low | Backend pytest coverage for restart backfill. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_safety_hardening.py` | Python | 90 | 74 | Low | Backend pytest coverage for safety hardening. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_search_api.py` | Python | 253 | 214 | Low | Backend pytest coverage for search api. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_settings_api.py` | Python | 64 | 48 | Low | Backend pytest coverage for settings api. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_simulation_e2e.py` | Python | 179 | 147 | Low | Backend pytest coverage for simulation e2e. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_snapshots.py` | Python | 356 | 298 | Low | Backend pytest coverage for snapshots. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_telemetry.py` | Python | 483 | 432 | Low | Backend pytest coverage for telemetry. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_ubiquiti_lpr.py` | Python | 187 | 164 | Low | Backend pytest coverage for ubiquiti lpr. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_unifi_protect.py` | Python | 112 | 93 | Low | Backend pytest coverage for unifi protect. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_unifi_protect_client.py` | Python | 57 | 42 | Low | Backend pytest coverage for unifi protect client. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_unifi_protect_updates.py` | Python | 109 | 85 | Low | Backend pytest coverage for unifi protect updates. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_users_api.py` | Python | 100 | 74 | Low | Backend pytest coverage for users api. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_vehicle_visual_detections.py` | Python | 221 | 192 | Low | Backend pytest coverage for vehicle visual detections. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_visitor_passes.py` | Python | 706 | 564 | Medium | Backend pytest coverage for visitor passes. | Maybe: keep coverage, factor shared fixtures if duplication grows. |
| `backend/tests/test_webhooks.py` | Python | 422 | 335 | Low | Backend pytest coverage for webhooks. | No immediate change; coverage value outweighs size. |
| `backend/tests/test_whatsapp_messaging.py` | Python | 3,004 | 2,458 | High | Backend pytest coverage for whatsapp messaging. | Maybe: split by behavior/fixture only when tests are next touched. |
| `backend/uv.lock` | Lockfile | 3,013 | 2,891 | High | Dependency lockfile; generated but reviewable for size/change impact. | No source simplification; keep, but monitor dependency-driven growth. |
| `docker-compose.yml` | YAML | 120 | 115 | Low | Docker Compose runtime topology. | No immediate simplification needed. |
| `docs/code-review/change-bloat-audit.md` | Markdown | 118 | 87 | Low | Existing code-review/audit documentation. | No immediate simplification needed. |
| `docs/code-review/full-project-maintenance-audit.md` | Markdown | 827 | 706 | Medium | Existing code-review/audit documentation. | Maybe: review when touched; file is above comfort threshold. |
| `docs/code-review/full-project-maintenance-pr-split.md` | Markdown | 36 | 27 | Low | Existing code-review/audit documentation. | No immediate simplification needed. |
| `docs/code-review/full-project-maintenance-remediation-ledger.md` | Markdown | 62 | 53 | Low | Existing code-review/audit documentation. | No immediate simplification needed. |
| `docs/phase-1.md` | Markdown | 51 | 42 | Low | Project phase documentation. | No immediate simplification needed. |
| `docs/phase-2.md` | Markdown | 70 | 57 | Low | Project phase documentation. | No immediate simplification needed. |
| `docs/phase-3.md` | Markdown | 58 | 45 | Low | Project phase documentation. | No immediate simplification needed. |
| `docs/phase-4.md` | Markdown | 62 | 52 | Low | Project phase documentation. | No immediate simplification needed. |
| `docs/phase-5.md` | Markdown | 96 | 83 | Low | Project phase documentation. | No immediate simplification needed. |
| `docs/phase-6.md` | Markdown | 48 | 39 | Low | Project phase documentation. | No immediate simplification needed. |
| `frontend/.dockerignore` | Text | 13 | 13 | Low | Ignore/build-context configuration. | No immediate simplification needed. |
| `frontend/Dockerfile` | Dockerfile | 19 | 12 | Low | Container build definition. | No immediate simplification needed. |
| `frontend/index.html` | HTML | 13 | 13 | Low | HTML support file. | No immediate simplification needed. |
| `frontend/nginx.conf` | Nginx | 51 | 43 | Low | Frontend Nginx proxy/cache configuration. | No immediate simplification needed. |
| `frontend/package-lock.json` | Lockfile | 2,380 | 2,380 | High | Dependency lockfile; generated but reviewable for size/change impact. | No source simplification; keep, but monitor dependency-driven growth. |
| `frontend/package.json` | JSON | 35 | 35 | Low | Frontend package manifest and scripts. | No immediate simplification needed. |
| `frontend/public/favicon.svg` | SVG | 4 | 4 | Low | Frontend favicon asset. | No immediate simplification needed. |
| `frontend/src/RouteErrorBoundary.test.tsx` | TSX | 36 | 29 | Low | Shared frontend RouteErrorBoundary.test module. | No immediate simplification needed. |
| `frontend/src/RouteErrorBoundary.tsx` | TSX | 32 | 26 | Low | Shared frontend RouteErrorBoundary module. | No immediate simplification needed. |
| `frontend/src/VariableRichTextEditor.tsx` | TSX | 526 | 464 | Medium | Shared frontend VariableRichTextEditor module. | Maybe: review when touched; file is above comfort threshold. |
| `frontend/src/main.tsx` | TSX | 2,593 | 2,423 | High | Shared frontend main module. | Yes: extract shell hooks for auth/realtime/navigation/search. |
| `frontend/src/shared.test.ts` | TypeScript | 26 | 22 | Low | Shared frontend shared.test module. | No immediate simplification needed. |
| `frontend/src/shared.tsx` | TSX | 1,164 | 1,054 | High | Shared frontend shared module. | Maybe: review when touched; file is above comfort threshold. |
| `frontend/src/styles.css` | CSS | 10 | 10 | Low | Frontend CSS for styles surfaces. | No immediate simplification needed. |
| `frontend/src/styles/auth-directory-modals.css` | CSS | 1,405 | 1,200 | High | Frontend CSS for auth-directory-modals surfaces. | Yes: split by route/component and delete stale selectors after visual QA. |
| `frontend/src/styles/base.css` | CSS | 1,037 | 903 | High | Frontend CSS for base surfaces. | Yes: split by route/component and delete stale selectors after visual QA. |
| `frontend/src/styles/chat-responsive.css` | CSS | 3,218 | 2,728 | High | Frontend CSS for chat-responsive surfaces. | Yes: split by route/component and delete stale selectors after visual QA. |
| `frontend/src/styles/dashboard.css` | CSS | 1,182 | 1,011 | High | Frontend CSS for dashboard surfaces. | Yes: split by route/component and delete stale selectors after visual QA. |
| `frontend/src/styles/data-views.css` | CSS | 3,425 | 2,939 | High | Frontend CSS for data-views surfaces. | Yes: split by route/component and delete stale selectors after visual QA. |
| `frontend/src/styles/integrations.css` | CSS | 1,905 | 1,645 | High | Frontend CSS for integrations surfaces. | Yes: split by route/component and delete stale selectors after visual QA. |
| `frontend/src/styles/passes-schedules.css` | CSS | 3,312 | 2,832 | High | Frontend CSS for passes-schedules surfaces. | Yes: split by route/component and delete stale selectors after visual QA. |
| `frontend/src/styles/search-palette.css` | CSS | 327 | 280 | Low | Frontend CSS for search-palette surfaces. | No immediate simplification needed. |
| `frontend/src/styles/telemetry.css` | CSS | 1,667 | 1,426 | High | Frontend CSS for telemetry surfaces. | Yes: split by route/component and delete stale selectors after visual QA. |
| `frontend/src/styles/workflows.css` | CSS | 2,968 | 2,553 | High | Frontend CSS for workflows surfaces. | Yes: split by route/component and delete stale selectors after visual QA. |
| `frontend/src/test/setup.ts` | TypeScript | 1 | 1 | Low | Frontend test harness setup. | No immediate simplification needed. |
| `frontend/src/views/AlertsView.tsx` | TSX | 312 | 290 | Low | React route/view for Alerts. | No immediate simplification needed. |
| `frontend/src/views/AlfredTrainingView.tsx` | TSX | 458 | 426 | Low | React route/view for AlfredTraining. | No immediate simplification needed. |
| `frontend/src/views/ChatWidgetView.tsx` | TSX | 2,066 | 1,982 | High | React route/view for ChatWidget. | Yes: extract route-local components/hooks while preserving lazy route chunks. |
| `frontend/src/views/DashboardView.tsx` | TSX | 963 | 905 | Medium | React route/view for Dashboard. | Maybe: review when touched; file is above comfort threshold. |
| `frontend/src/views/DirectoryViews.tsx` | TSX | 2,322 | 2,190 | High | React route/view for Directorys. | Yes: extract route-local components/hooks while preserving lazy route chunks. |
| `frontend/src/views/EventsView.tsx` | TSX | 188 | 176 | Low | React route/view for Events. | No immediate simplification needed. |
| `frontend/src/views/IntegrationsView.tsx` | TSX | 5,008 | 4,775 | Very High | React route/view for Integrations. | Yes: dedicated reduction plan; do not rewrite in one pass. |
| `frontend/src/views/LogsView.tsx` | TSX | 9 | 8 | Low | React route/view for Logs. | No immediate simplification needed. |
| `frontend/src/views/MovementsView.tsx` | TSX | 616 | 565 | Medium | React route/view for Movements. | Maybe: review when touched; file is above comfort threshold. |
| `frontend/src/views/PassesView.tsx` | TSX | 2,035 | 1,914 | High | React route/view for Passes. | Yes: extract route-local components/hooks while preserving lazy route chunks. |
| `frontend/src/views/ReportsView.tsx` | TSX | 1,556 | 1,442 | High | React route/view for Reports. | Yes: extract route-local components/hooks while preserving lazy route chunks. |
| `frontend/src/views/SchedulesView.tsx` | TSX | 991 | 906 | Medium | React route/view for Schedules. | Maybe: review when touched; file is above comfort threshold. |
| `frontend/src/views/SettingsViews.tsx` | TSX | 1,806 | 1,711 | High | React route/view for Settingss. | Yes: extract route-local components/hooks while preserving lazy route chunks. |
| `frontend/src/views/TopChartsView.tsx` | TSX | 484 | 443 | Low | React route/view for TopCharts. | No immediate simplification needed. |
| `frontend/src/views/WorkflowViews.tsx` | TSX | 4,277 | 4,051 | Very High | React route/view for Workflows. | Yes: dedicated reduction plan; do not rewrite in one pass. |
| `frontend/src/views/logExplorer/LogsControls.tsx` | TSX | 224 | 218 | Low | Log explorer LogsControls UI module. | No immediate simplification needed. |
| `frontend/src/views/logExplorer/LogsWorkspace.tsx` | TSX | 206 | 191 | Low | Log explorer LogsWorkspace UI module. | No immediate simplification needed. |
| `frontend/src/views/logExplorer/LprWaterfallPanel.tsx` | TSX | 249 | 234 | Low | Log explorer LprWaterfallPanel UI module. | No immediate simplification needed. |
| `frontend/src/views/logExplorer/NarrativeFeed.tsx` | TSX | 407 | 385 | Low | Log explorer NarrativeFeed UI module. | No immediate simplification needed. |
| `frontend/src/views/logExplorer/components.tsx` | TSX | 951 | 909 | Medium | Log explorer components UI module. | Maybe: review when touched; file is above comfort threshold. |
| `frontend/src/views/logExplorer/constants.ts` | TypeScript | 136 | 123 | Low | Log explorer constants UI module. | No immediate simplification needed. |
| `frontend/src/views/logExplorer/hooks.ts` | TypeScript | 472 | 412 | Low | Log explorer hooks UI module. | No immediate simplification needed. |
| `frontend/src/views/logExplorer/lprWaterfall.ts` | TypeScript | 528 | 487 | Medium | Log explorer lprWaterfall UI module. | Maybe: review when touched; file is above comfort threshold. |
| `frontend/src/views/logExplorer/narrative.ts` | TypeScript | 311 | 293 | Low | Log explorer narrative UI module. | No immediate simplification needed. |
| `frontend/src/views/logExplorer/types.ts` | TypeScript | 321 | 298 | Low | Log explorer types UI module. | No immediate simplification needed. |
| `frontend/src/views/logExplorer/utils.test.ts` | TypeScript | 17 | 14 | Low | Log explorer utils.test UI module. | No immediate simplification needed. |
| `frontend/src/views/logExplorer/utils.ts` | TypeScript | 691 | 636 | Medium | Log explorer utils UI module. | Maybe: review when touched; file is above comfort threshold. |
| `frontend/src/vite-env.d.ts` | TypeScript | 2 | 1 | Low | Shared frontend vite-env.d module. | No immediate simplification needed. |
| `frontend/tsconfig.json` | JSON | 21 | 21 | Low | JSON support file. | No immediate simplification needed. |
| `frontend/vite.config.ts` | TypeScript | 22 | 21 | Low | TypeScript support file. | No immediate simplification needed. |
| `scripts/load-test.mjs` | JavaScript | 825 | 763 | Medium | JavaScript support file. | Maybe: review when touched; file is above comfort threshold. |

# Files Over 4,000 Lines

## `backend/app/ai/tools.py` (7,757 lines)

- Why it is large: It still carries the historical Alfred tool facade plus many concrete handlers, schemas, serializers, diagnostics, and shared utility helpers, even though `backend/app/ai/tool_groups/*` now exists.
- Is the size justified: No. The facade import surface is justified, but the implementation bulk is not.
- Responsibilities mixed together: Tool contracts, permission constants, registry-facing metadata, schedule parsing, visitor-pass mutations, access diagnostics, notification/automation CRUD, report/PDF helpers, camera/snapshot tools, and generic payload compaction.
- Proposed target structure: Keep `app.ai.tools` as a small compatibility facade; move remaining bodies into existing `tool_groups/*_handlers.py`, and move shared contracts to a narrow contracts module if needed.
- Target line count: Facade under 800 lines; domain handler files generally under 700 lines each.
- Should it be split, rewritten, or left alone: Split gradually; never delete wholesale.
- Safest reduction plan:
  1. Freeze public imports with tests in `backend/tests/test_chat_agent.py`.
  2. Move one domain at a time, starting with schedule and notification helpers because tool-group files already exist.
  3. After each move, leave only import shims in `tools.py` and rerun registry/card/permission tests.
  4. Delete duplicate helper copies once call sites use the owning domain module.
  5. Only then consider extracting `AgentTool`/context constants to a tiny contract module.

## `frontend/src/views/IntegrationsView.tsx` (5,008 lines)

- Why it is large: It combines the integrations landing surface, modal framework, every provider settings form, UniFi Protect cameras/exposes/updates, dependency update hub, analysis rendering, job websocket state, and many formatting helpers.
- Is the size justified: No. Route-level lazy loading is useful, but the route body has become several products in one file.
- Responsibilities mixed together: Provider definitions, LLM settings, Apprise/ESPHome/Discord/WhatsApp/Home Assistant fields, iCloud modal, UniFi Protect update UI, dependency update UI, websocket logs, diff parsing, backup storage, and confirmation modals.
- Proposed target structure: Create `views/integrations/` with `types.ts`, `definitions.ts`, `IntegrationModal.tsx`, `settingsFields/*`, `DependencyUpdatesHub.tsx`, `UnifiProtectPanels.tsx`, and `ICloudCalendarModal.tsx`; keep a small exported route wrapper.
- Target line count: Route wrapper under 900 lines; largest child under 900 lines.
- Should it be split, rewritten, or left alone: Split; do not rewrite styling or behavior at the same time.
- Safest reduction plan:
  1. Extract pure types/constants first with no JSX behavior change.
  2. Move dependency update hub and tests together because it is self-contained.
  3. Move UniFi Protect panels next, preserving confirmation props.
  4. Move provider settings fields one provider at a time.
  5. Run frontend tests/build and browser smoke after each move.

## `frontend/src/views/WorkflowViews.tsx` (4,277 lines)

- Why it is large: It contains notification workflow UI and automation workflow UI, plus modals, catalogs, normalization, template rendering, and helper utilities.
- Is the size justified: Only partly. Keeping workflows in one lazy chunk is understandable, but humans and agents must reason through two complex domains at once.
- Responsibilities mixed together: Automation rule editing, notification rule editing, trigger/action/condition modals, preview rendering, variable templates, gate malfunction compatibility normalization, and shared status/filter UI.
- Proposed target structure: Create `views/workflows/notifications/*`, `views/workflows/automations/*`, and `views/workflows/shared/*`; keep `WorkflowViews.tsx` as a route aggregator.
- Target line count: Aggregator under 600 lines; notification and automation editors under 1,000 lines each.
- Should it be split, rewritten, or left alone: Split by domain; avoid visual redesign in same task.
- Safest reduction plan:
  1. Move pure types/default catalogs first.
  2. Extract shared list/status/filter/modal primitives.
  3. Move automation editor/list as one slice with existing helpers.
  4. Move notification editor/action/condition modals as one slice.
  5. Add focused component tests for normalization before deleting compatibility helpers.

## `backend/app/services/access_events.py` (4,265 lines)

- Why it is large: It is the central LPR access pipeline: ingest, durable queueing, debounce, movement saga/session handling, gate observation, direction decisions, event commit, garage side effects, snapshots, DVLA checks, notifications, leaderboard, telemetry, and recovery hooks.
- Is the size justified: No, but it is safety-critical and should be reduced carefully.
- Responsibilities mixed together: Transport ingest, durable LPR repository coordination, debounce windows, vehicle session suppression, movement decisions, persistence, hardware side effects, notification contexts, snapshot work, telemetry/audit, and retry/failure handling.
- Proposed target structure: Keep `AccessEventService` as an orchestrator; extract pure notification/serialization helpers, debounce/session handling, gate side effects, and event commit collaborators around existing FSM/ledger modules.
- Target line count: Orchestrator 1,200-1,500 lines; extracted modules 300-900 lines each.
- Should it be split, rewritten, or left alone: Split surgically; no clean rewrite.
- Safest reduction plan:
  1. Start with pure helpers that do not touch DB/hardware, such as notification payload/plate formatting.
  2. Extract debounce/session helpers with tests using existing `test_access_events.py`.
  3. Extract event commit and snapshot persistence only after durable ingest tests are stable.
  4. Extract gate/garage side effects last because they affect physical hardware semantics.
  5. Run full backend tests after every slice.

## `backend/app/services/whatsapp_messaging.py` (4,127 lines)

- Why it is large: It owns WhatsApp Cloud API config, outbound sends, webhook parsing, Admin Alfred routing, visitor concierge LLM prompts/tools, visitor pass updates/timeframe approvals, abuse controls, templates, and many small formatting/parsing helpers.
- Is the size justified: No. It is an integration boundary plus a visitor-agent product plus an admin bridge.
- Responsibilities mixed together: Provider transport, webhook security/parsing, admin identity routing, visitor concierge policy, LLM JSON normalization, timeframe math, button IDs, templates, abuse/mute state, and notification delivery.
- Proposed target structure: Create `whatsapp_config.py`, `whatsapp_webhook.py`, `whatsapp_templates.py`, `visitor_concierge.py`, `visitor_timeframes.py`, and keep `WhatsAppMessagingService` as coordinator.
- Target line count: Coordinator under 900 lines; visitor concierge/timeframes under 900 lines each.
- Should it be split, rewritten, or left alone: Split; preserve webhook and visitor security behavior exactly.
- Safest reduction plan:
  1. Extract stateless formatting/button/template helpers first and update tests to import owning modules.
  2. Move visitor timeframe parsing/math with existing `test_whatsapp_messaging.py` coverage.
  3. Move visitor concierge prompt/tool loop after preserving snapshot tests for allowed/denied behavior.
  4. Move webhook parsing/security separately from outbound send logic.
  5. Keep public service methods stable until API/webhook tests pass.

# Highest Priority Simplifications

| File path | Current issue | Why it matters | Recommended change | Expected line reduction | Risk level | Applied |
| --- | --- | --- | --- | ---: | --- | --- |
| backend/app/ai/tools.py | Legacy facade contains domain implementations. | This is the largest file and duplicates the newer tool-group architecture. | Move handlers into existing `tool_groups/*_handlers.py` one domain at a time; keep facade shims. | 3,000-5,500 lines from facade | Medium | No |
| frontend/src/views/IntegrationsView.tsx | One route owns all provider settings plus dependency and UniFi update workflows. | Provider changes require understanding unrelated update UI and websocket state. | Split into `views/integrations/*` domain files while preserving lazy route loading. | 2,500-3,800 lines from route file | Medium | No |
| frontend/src/views/WorkflowViews.tsx | Automation and notification builders share one giant file. | Two domains amplify review blast radius and duplicate list/modal patterns. | Split notifications, automations, and shared workflow primitives. | 2,000-3,200 lines from route file | Medium | No |
| backend/app/services/access_events.py | LPR pipeline orchestrator owns too many side effects. | Any edit risks access decisions, gate commands, presence, notifications, and durability. | Extract pure helpers first, then debounce/session and event commit slices. | 2,500-3,000 lines from orchestrator | High | No |
| backend/app/services/whatsapp_messaging.py | Provider transport, Admin Alfred, visitor concierge, templates, and abuse controls are intertwined. | Visitor security behavior is hard to audit in one file. | Extract stateless helpers, visitor timeframe/conversation modules, then webhook parsing. | 2,000-3,000 lines from service | High | No |
| frontend/src/styles/*.css | Several CSS files exceed 1,000-3,000 lines. | Stale selectors and broad rules are hard to reason about visually. | Split by route/component and prune only with browser screenshots. | 500-1,500 lines over time | Medium | No |
| frontend/src/main.tsx | Shell owns auth, realtime, routing, search, sidebar, toast, and chat launcher. | Global app behavior is concentrated in one 2,593-line file. | Extract hooks/modules for auth, realtime, navigation/search, and sidebar state. | 1,000-1,500 lines from shell | Medium | No |
| backend/app/services/notifications.py | Catalog, normalization, rendering, dispatch, action context, and run state share one service. | Workflow semantics are duplicated with API/frontend normalization. | Separate catalog/normalization/rendering/delivery run state after tests. | 1,000-1,800 lines | High | No |
| backend/app/db/bootstrap.py | Transitional DDL still sits in runtime bootstrap. | Schema ownership is split between bootstrap and Alembic. | Keep only runtime seed/repair; move DDL to Alembic after baseline confidence. | 400-650 lines | Medium | No |
| backend/app/services/access_events.py + backend/tests/test_access_events.py | Tests imported LPR status constants through `access_events.py`. | That made an accidental re-export look like public service API. | Import LPR ingest statuses from `app.services.lpr_ingest`. | 1 line | Low | Yes |

# Duplicated Logic Findings

| File path | Current issue | Why it matters | Recommended change | Expected line reduction | Risk level | Applied |
| --- | --- | --- | --- | ---: | --- | --- |
| backend/app/api/v1/* | Confirmation/error translation appears both through `app.api.confirmations.require_confirmed_action` and route-local `consume_action_confirmation` blocks. | Inconsistent confirmation semantics are dangerous for admin-only hardware/settings actions. | Continue converging route mutations on the shared helper where payload/body contracts allow it. | 50-150 lines | Medium | No |
| `backend/app/ai/tools.py` and `backend/app/ai/tool_groups/*` | Tool metadata/handlers exist in both the legacy facade and newer groups. | Duplicated contracts increase permission/card drift. | Move one domain at a time to tool groups; facade should only re-export. | 3,000+ lines from facade | Medium | No |
| `backend/app/modules/home_assistant/covers.py` callers | Legacy gate entity normalization is repeated in access devices, settings, movement reconciliation, and gate modules. | Provider compatibility decisions become scattered. | Centralize legacy entity resolution behind the access-device provider boundary. | 100-250 lines | Medium | No |
| frontend/src/views/* | Tooltip/popover close-on-scroll/resize/key handlers recur across reports, schedules, passes, workflows, and charts. | Small repeated UI state makes regressions likely and bloats views. | Extract a tiny local hook after visual tests exist. | 150-300 lines | Low | No |
| Workflow frontend and backend notification services | Notification action/condition normalization exists in UI and backend. | Mismatch can produce saved rules the UI renders differently. | Document backend as source of truth and keep frontend normalization display-only. | 100-250 lines | Medium | No |
| WhatsApp visitor helpers | Button ID generation/parsing, timeframe parsing, and visitor reply sanitation live as many adjacent helpers. | The logic is testable but hard to navigate. | Move helper clusters into `visitor_timeframes.py` and `whatsapp_templates.py`. | 400-900 lines from service | Medium | No |

# Over-Engineering Findings

| File path | Current issue | Why it matters | Recommended change | Risk level | Applied |
| --- | --- | --- | --- | --- | --- |
| backend/app/ai/tools.py | A facade has become a second implementation layer. | The old abstraction no longer earns its file size. | Keep public facade only; domain modules own bodies. | High | No |
| backend/app/services/dependency_updates.py | A large in-app dependency update system owns detection, analysis, backups, apply/restore, logs, and UI-facing job state. | It is useful, but it is much larger than most domain services and should not grow further. | Split job repository/logging/apply-backup helpers if changed again. | Medium | No |
| frontend/src/views/IntegrationsView.tsx | Provider settings and dependency-management product are coupled because they share an integrations page. | Unrelated integration edits can break update workflows. | Separate dependency update hub into its own route-local module. | Medium | No |
| backend/app/db/bootstrap.py | Runtime bootstrap still contains a migration-like DDL layer. | Alembic now exists, so two schema paths create drift risk. | Reduce bootstrap to runtime idempotent repairs/seeds after migrations are trusted. | Medium | No |

# Dead Code Findings

| File path | Current issue | Why it matters | Recommended change | Expected line reduction | Risk level | Applied |
| --- | --- | --- | --- | ---: | --- | --- |
| backend/app/services/access_events.py | Imported `LPR_INGEST_STATUS_PENDING` and `LPR_INGEST_STATUS_SUCCEEDED` only so tests could reach them accidentally. | Accidental re-exports make module contracts larger and made `ruff` fail. | Tests now import from `app.services.lpr_ingest`; unused imports removed. | 1 line | Low | Yes |
| docs/code-review/* | Several existing audit documents overlap with this new report. | Documentation sprawl can obscure the current source of truth. | Keep them for traceability now; consolidate/archive only after merge decisions. | 0 now | Low | No |
| backend/app/db/bootstrap.py | Contains transitional schema/compatibility DDL that should not remain permanent application logic. | Runtime boot should not be a parallel migration framework long term. | Delete DDL only after Alembic baseline/fresh install tests are accepted. | 400-650 lines | Medium | No |
| backend/app/ai/tools.py | Legacy facade code is redundant in shape but not safe to delete yet. | Public imports and registry tests still depend on it. | Delete only migrated handler bodies after domain moves prove compatibility. | 3,000+ lines eventually | Medium | No |

# Inefficient Code Findings

- `backend/app/services/access_events.py`: heavy logic remains on the LPR request/worker path. Durable ingest is valuable, but more of the work should be staged behind explicit repository/service boundaries before adding features.
- `frontend/src/views/IntegrationsView.tsx`: dependency job websocket state and large update panels render in the same file as integration settings; split modules would improve memoization and reviewability.
- `frontend/src/styles/*.css`: large global CSS makes selector invalidation and browser style work harder to inspect; pruning should be visual-test led.
- `backend/app/services/whatsapp_messaging.py`: visitor LLM parsing, outbound templates, and webhook processing are interleaved; extracting stateless helpers will make unit tests cheaper and clearer.

# Files Recommended For Splitting
- `backend/app/ai/tools.py` (7,757 lines): Yes: dedicated reduction plan; do not rewrite in one pass.
- `frontend/src/views/IntegrationsView.tsx` (5,008 lines): Yes: dedicated reduction plan; do not rewrite in one pass.
- `frontend/src/views/WorkflowViews.tsx` (4,277 lines): Yes: dedicated reduction plan; do not rewrite in one pass.
- `backend/app/services/access_events.py` (4,265 lines): Yes: dedicated reduction plan; do not rewrite in one pass.
- `backend/app/services/whatsapp_messaging.py` (4,127 lines): Yes: dedicated reduction plan; do not rewrite in one pass.
- `backend/app/services/notifications.py` (2,854 lines): Yes: split along existing domain boundaries behind current tests.
- `backend/app/services/automations.py` (2,465 lines): Yes: split along existing domain boundaries behind current tests.
- `backend/app/services/dependency_updates.py` (2,612 lines): Yes: split along existing domain boundaries behind current tests.
- `frontend/src/main.tsx` (2,593 lines): Yes: extract shell hooks for auth/realtime/navigation/search.
- `frontend/src/shared.tsx` (1,164 lines): Maybe: review when touched; file is above comfort threshold.
- `backend/app/models/core.py` (1,479 lines): Yes eventually: split model declarations by domain once migrations settle.

# Files Recommended For Rewriting

No full rewrites are recommended now. The largest files should be split and simplified in behavior-preserving slices. A rewrite of LPR, WhatsApp, or Alfred tooling would be riskier than the bloat it removes unless accompanied by a larger test harness and staged compatibility plan.

# Files Recommended For Deletion

No source file was safe to delete outright in this pass. The only source simplification applied was removing two unused imported names and updating tests to import from the owning module. Potential future deletion should target migrated handler bodies in `backend/app/ai/tools.py` and transitional DDL in `backend/app/db/bootstrap.py` after tests prove the replacement path.

# Files That Should Be Left Alone

- `frontend/package-lock.json` (2,380 lines): No source simplification; keep, but monitor dependency-driven growth.
- `backend/uv.lock` (3,013 lines): No source simplification; keep, but monitor dependency-driven growth.
- `backend/alembic/versions/20260531_0000_current_schema_baseline.py` (30 lines): Leave alone after merge; migration history should stay append-only.
- `backend/alembic/versions/20260531_0002_automation_webhook_hardening.py` (154 lines): Leave alone after merge; migration history should stay append-only.
- `backend/alembic/versions/20260531_0003_notification_runs.py` (79 lines): Leave alone after merge; migration history should stay append-only.
- `frontend/public/favicon.svg` (4 lines): No immediate simplification needed.
- `backend/app/__init__.py` (1 lines): Leave alone; package marker with negligible cost.
- `backend/app/api/v1/__init__.py` (1 lines): Leave alone; package marker with negligible cost.
- `backend/app/services/report_templates/person_movements.html` (844 lines): Leave alone unless redesigning report export; a single rendered template is less risky than a split template without visual regression checks.

# Maintainability Risk Areas

- Safety-critical physical access behavior is concentrated in `access_events.py`, `gate_commands.py`, access-device providers, and movement reconciliation. Changes here must be tiny and test-first.
- Alfred tooling is mid-migration: the tool-group architecture exists, but the facade remains larger than the new structure.
- Frontend operational routes are split by route, but several route files now contain many sub-products. Route-level lazy loading should remain; internal files should shrink.
- Runtime schema ownership is transitional: Alembic exists, while bootstrap still contains compatibility DDL. That is acceptable short term but should not keep growing.
- CSS size is high and broad selectors can cause invisible regressions. Prune only with browser screenshots or component-level checks.

# AI-Agent Maintainability Notes

Future agents should avoid broad edits across the five 4,000+ line files. The safest pattern is: prove the current behavior with a focused test, move one pure helper or one domain slice, keep public imports stable, and run the relevant backend/frontend validation immediately. The repo already has many uncommitted changes, so agents must not revert unrelated edits and should name exactly which lines they changed.

# Simplifications Applied

| File path | Current issue | Why it matters | Recommended change | Expected line reduction | Risk level | Applied |
| --- | --- | --- | --- | ---: | --- | --- |
| `backend/app/services/access_events.py`; `backend/tests/test_access_events.py` | LPR ingest status constants were imported through `access_events.py` only for tests. | It enlarged the implicit public surface and failed `ruff` unused-import checks. | Tests now import `LPR_INGEST_STATUS_PENDING` and `LPR_INGEST_STATUS_SUCCEEDED` from `app.services.lpr_ingest`; unused service imports removed. | 1 | Low | Yes |

# Simplifications Not Applied Because Too Risky

- Split `AccessEventService` now: Touches LPR decisions, durable ingest, gate side effects, presence commits, snapshots, and notifications. Recommended next step: Do as separate PR slices with full backend tests after each slice.
- Delete `backend/app/ai/tools.py` bodies now: Tool groups are not yet the sole import surface; tests still cover facade behavior. Recommended next step: Migrate one tool domain at a time.
- Split large frontend routes now: Many view/style files already have uncommitted changes; moving JSX risks conflicts and visual regressions. Recommended next step: Extract pure types/constants first, then components with frontend build and browser QA.
- Remove bootstrap DDL now: Schema transition is active and fresh-install expectations must remain stable. Recommended next step: Wait until Alembic baseline is accepted and tested from empty database.
- Delete overlapping code-review docs: They are useful audit history while remediation branches are unresolved. Recommended next step: Consolidate docs after merge strategy is decided.

# Validation Results

| Command | Result | Relevant output |
| --- | --- | --- |
| `cd backend && python3 -m ruff check app tests` | Fail | Local system Python has no `ruff` module. |
| `cd backend && ./.venv/bin/python -m ruff check app tests` | Pass | All checks passed after the unused-import cleanup. |
| `backend/.venv/bin/python -m compileall -q backend/app` | Pass | No syntax errors reported. |
| `cd backend && ./.venv/bin/python -m mypy app/ai/tool_groups app/services/alfred/memory.py app/services/domain_events.py` | Fail | Local backend virtualenv has no `mypy` module. |
| `docker compose exec -T backend sh -lc "cd /workspace/backend && python -m ruff check app/ai/tool_groups app/services/alfred app/services/chat.py app/services/domain_events.py && python -m mypy app/ai/tool_groups app/services/alfred/memory.py app/services/domain_events.py"` | Pass | Ruff passed; mypy reported no issues in 24 source files. |
| `cd frontend && npm run test -- --runInBand` | Fail | Vitest does not support Jest's `--runInBand` option. |
| `cd frontend && npm run test` | Pass | 3 test files / 5 tests passed. |
| `cd frontend && npm run build` | Pass | TypeScript and Vite build passed; largest chunks remain `IntegrationsView`, `LogsView`, and `WorkflowViews`. |
| `docker compose config --quiet` | Pass | Compose configuration parsed successfully. |
| `docker compose ps` | Pass | backend, frontend, postgres, redis, and updater were running; backend healthy. |
| `docker compose exec -T backend sh -lc "cd /workspace/backend && python -m pytest tests/test_access_events.py::test_lpr_ingest_read_persists_pending_row_before_processing tests/test_access_events.py::test_lpr_ingest_duplicate_does_not_wake_worker"` | Pass | 2 targeted tests passed. |
| `docker compose exec -T backend sh -lc "cd /workspace/backend && python -m pytest"` | Pass | 683 tests passed in 5.67s. |
| `curl -fsS http://localhost:8089/api/v1/health` | Pass | Returned `status: ok` with healthy database/realtime/access-events checks. |
| `curl -fsS http://localhost:8089/api/v1/auth/status` | Pass | Returned setup not required and unauthenticated user state. |
| `uv lock --project backend --check` | Pass | Resolved 118 packages; no additional tracked source changes were needed. |
| `git diff --check` | Pass | No whitespace errors. |

# Recommended Follow-Up Tasks

1. Run a dedicated `backend/app/ai/tools.py` shrink pass: migrate one tool domain into `tool_groups/*_handlers.py`, update registry/permission tests, and delete the moved facade bodies.
2. Split `frontend/src/views/IntegrationsView.tsx` by extracting pure types/constants and the dependency update hub first; keep the route lazy chunk intact.
3. Split `frontend/src/views/WorkflowViews.tsx` into notification, automation, and shared workflow modules with frontend tests around normalization helpers.
4. Start an `AccessEventService` reduction with pure helper extraction only; do not touch gate side effects in the first slice.
5. Extract WhatsApp visitor timeframe/template helpers into separate modules with existing `test_whatsapp_messaging.py` coverage.
6. Plan a CSS pruning pass with browser screenshots for the large route CSS files.
7. After remediation branches settle, consolidate old code-review docs so this report is not competing with stale audits.

# Appendix: Oversized Buckets

## Files over 4,000 lines (5)

- `backend/app/ai/tools.py`: 7,757 lines, Very High.
- `frontend/src/views/IntegrationsView.tsx`: 5,008 lines, Very High.
- `frontend/src/views/WorkflowViews.tsx`: 4,277 lines, Very High.
- `backend/app/services/access_events.py`: 4,265 lines, Very High.
- `backend/app/services/whatsapp_messaging.py`: 4,127 lines, Very High.

## Files over 2,000 lines (22)

- `backend/app/ai/tools.py`: 7,757 lines, Very High.
- `frontend/src/views/IntegrationsView.tsx`: 5,008 lines, Very High.
- `frontend/src/views/WorkflowViews.tsx`: 4,277 lines, Very High.
- `backend/app/services/access_events.py`: 4,265 lines, Very High.
- `backend/app/services/whatsapp_messaging.py`: 4,127 lines, Very High.
- `backend/tests/test_chat_agent.py`: 3,807 lines, High.
- `frontend/src/styles/data-views.css`: 3,425 lines, High.
- `frontend/src/styles/passes-schedules.css`: 3,312 lines, High.
- `backend/app/services/chat.py`: 3,309 lines, High.
- `frontend/src/styles/chat-responsive.css`: 3,218 lines, High.
- `backend/tests/test_access_events.py`: 3,081 lines, High.
- `backend/uv.lock`: 3,013 lines, High.
- `backend/tests/test_whatsapp_messaging.py`: 3,004 lines, High.
- `frontend/src/styles/workflows.css`: 2,968 lines, High.
- `backend/app/services/notifications.py`: 2,854 lines, High.
- `backend/app/services/dependency_updates.py`: 2,612 lines, High.
- `frontend/src/main.tsx`: 2,593 lines, High.
- `backend/app/services/automations.py`: 2,465 lines, High.
- `frontend/package-lock.json`: 2,380 lines, High.
- `frontend/src/views/DirectoryViews.tsx`: 2,322 lines, High.
- `frontend/src/views/ChatWidgetView.tsx`: 2,066 lines, High.
- `frontend/src/views/PassesView.tsx`: 2,035 lines, High.

## Files over 1,000 lines (43)

- `backend/app/ai/tools.py`: 7,757 lines, Very High.
- `frontend/src/views/IntegrationsView.tsx`: 5,008 lines, Very High.
- `frontend/src/views/WorkflowViews.tsx`: 4,277 lines, Very High.
- `backend/app/services/access_events.py`: 4,265 lines, Very High.
- `backend/app/services/whatsapp_messaging.py`: 4,127 lines, Very High.
- `backend/tests/test_chat_agent.py`: 3,807 lines, High.
- `frontend/src/styles/data-views.css`: 3,425 lines, High.
- `frontend/src/styles/passes-schedules.css`: 3,312 lines, High.
- `backend/app/services/chat.py`: 3,309 lines, High.
- `frontend/src/styles/chat-responsive.css`: 3,218 lines, High.
- `backend/tests/test_access_events.py`: 3,081 lines, High.
- `backend/uv.lock`: 3,013 lines, High.
- `backend/tests/test_whatsapp_messaging.py`: 3,004 lines, High.
- `frontend/src/styles/workflows.css`: 2,968 lines, High.
- `backend/app/services/notifications.py`: 2,854 lines, High.
- `backend/app/services/dependency_updates.py`: 2,612 lines, High.
- `frontend/src/main.tsx`: 2,593 lines, High.
- `backend/app/services/automations.py`: 2,465 lines, High.
- `frontend/package-lock.json`: 2,380 lines, High.
- `frontend/src/views/DirectoryViews.tsx`: 2,322 lines, High.
- `frontend/src/views/ChatWidgetView.tsx`: 2,066 lines, High.
- `frontend/src/views/PassesView.tsx`: 2,035 lines, High.
- `backend/tests/test_notification_workflows.py`: 1,942 lines, High.
- `frontend/src/styles/integrations.css`: 1,905 lines, High.
- `frontend/src/views/SettingsViews.tsx`: 1,806 lines, High.
- `frontend/src/styles/telemetry.css`: 1,667 lines, High.
- `backend/app/services/gate_malfunctions.py`: 1,665 lines, High.
- `backend/app/services/alfred/feedback.py`: 1,592 lines, High.
- `frontend/src/views/ReportsView.tsx`: 1,556 lines, High.
- `backend/app/models/core.py`: 1,479 lines, High.
- `frontend/src/styles/auth-directory-modals.css`: 1,405 lines, High.
- `backend/app/api/v1/directory.py`: 1,344 lines, High.
- `backend/app/services/restart_backfill.py`: 1,230 lines, High.
- `backend/app/ai/providers.py`: 1,219 lines, High.
- `frontend/src/styles/dashboard.css`: 1,182 lines, High.
- `frontend/src/shared.tsx`: 1,164 lines, High.
- `backend/app/simulation/scenarios.py`: 1,119 lines, High.
- `backend/app/services/vehicle_visual_detections.py`: 1,114 lines, High.
- `backend/app/api/v1/telemetry.py`: 1,104 lines, High.
- `backend/app/api/v1/integrations.py`: 1,103 lines, High.
- `backend/app/services/access_devices.py`: 1,102 lines, High.
- `backend/app/services/visitor_passes.py`: 1,081 lines, High.
- `frontend/src/styles/base.css`: 1,037 lines, High.

## Files over 500 lines (79)

- `backend/app/ai/tools.py`: 7,757 lines, Very High.
- `frontend/src/views/IntegrationsView.tsx`: 5,008 lines, Very High.
- `frontend/src/views/WorkflowViews.tsx`: 4,277 lines, Very High.
- `backend/app/services/access_events.py`: 4,265 lines, Very High.
- `backend/app/services/whatsapp_messaging.py`: 4,127 lines, Very High.
- `backend/tests/test_chat_agent.py`: 3,807 lines, High.
- `frontend/src/styles/data-views.css`: 3,425 lines, High.
- `frontend/src/styles/passes-schedules.css`: 3,312 lines, High.
- `backend/app/services/chat.py`: 3,309 lines, High.
- `frontend/src/styles/chat-responsive.css`: 3,218 lines, High.
- `backend/tests/test_access_events.py`: 3,081 lines, High.
- `backend/uv.lock`: 3,013 lines, High.
- `backend/tests/test_whatsapp_messaging.py`: 3,004 lines, High.
- `frontend/src/styles/workflows.css`: 2,968 lines, High.
- `backend/app/services/notifications.py`: 2,854 lines, High.
- `backend/app/services/dependency_updates.py`: 2,612 lines, High.
- `frontend/src/main.tsx`: 2,593 lines, High.
- `backend/app/services/automations.py`: 2,465 lines, High.
- `frontend/package-lock.json`: 2,380 lines, High.
- `frontend/src/views/DirectoryViews.tsx`: 2,322 lines, High.
- `frontend/src/views/ChatWidgetView.tsx`: 2,066 lines, High.
- `frontend/src/views/PassesView.tsx`: 2,035 lines, High.
- `backend/tests/test_notification_workflows.py`: 1,942 lines, High.
- `frontend/src/styles/integrations.css`: 1,905 lines, High.
- `frontend/src/views/SettingsViews.tsx`: 1,806 lines, High.
- `frontend/src/styles/telemetry.css`: 1,667 lines, High.
- `backend/app/services/gate_malfunctions.py`: 1,665 lines, High.
- `backend/app/services/alfred/feedback.py`: 1,592 lines, High.
- `frontend/src/views/ReportsView.tsx`: 1,556 lines, High.
- `backend/app/models/core.py`: 1,479 lines, High.
- `frontend/src/styles/auth-directory-modals.css`: 1,405 lines, High.
- `backend/app/api/v1/directory.py`: 1,344 lines, High.
- `backend/app/services/restart_backfill.py`: 1,230 lines, High.
- `backend/app/ai/providers.py`: 1,219 lines, High.
- `frontend/src/styles/dashboard.css`: 1,182 lines, High.
- `frontend/src/shared.tsx`: 1,164 lines, High.
- `backend/app/simulation/scenarios.py`: 1,119 lines, High.
- `backend/app/services/vehicle_visual_detections.py`: 1,114 lines, High.
- `backend/app/api/v1/telemetry.py`: 1,104 lines, High.
- `backend/app/api/v1/integrations.py`: 1,103 lines, High.
- `backend/app/services/access_devices.py`: 1,102 lines, High.
- `backend/app/services/visitor_passes.py`: 1,081 lines, High.
- `frontend/src/styles/base.css`: 1,037 lines, High.
- `frontend/src/views/SchedulesView.tsx`: 991 lines, Medium.
- `backend/app/api/v1/search.py`: 984 lines, Medium.
- `backend/app/modules/access_devices/esphome.py`: 983 lines, Medium.
- `frontend/src/views/DashboardView.tsx`: 963 lines, Medium.
- `backend/app/services/reports.py`: 958 lines, Medium.
- `backend/app/services/icloud_calendar.py`: 953 lines, Medium.
- `frontend/src/views/logExplorer/components.tsx`: 951 lines, Medium.
- `backend/app/services/actionable_notifications.py`: 865 lines, Medium.
- `backend/tests/test_automations.py`: 857 lines, Medium.
- `backend/app/services/report_templates/person_movements.html`: 844 lines, Medium.
- `backend/app/services/settings.py`: 831 lines, Medium.
- `docs/code-review/full-project-maintenance-audit.md`: 827 lines, Medium.
- `scripts/load-test.mjs`: 825 lines, Medium.
- `backend/tests/test_access_devices.py`: 791 lines, Medium.
- `backend/app/services/discord_messaging.py`: 766 lines, Medium.
- `backend/app/db/bootstrap.py`: 738 lines, Medium.
- `backend/app/services/lpr_timing.py`: 729 lines, Medium.
- `backend/app/services/telemetry.py`: 723 lines, Medium.
- `backend/tests/test_visitor_passes.py`: 706 lines, Medium.
- `frontend/src/views/logExplorer/utils.ts`: 691 lines, Medium.
- `backend/tests/test_dependency_updates.py`: 672 lines, Medium.
- `backend/app/api/v1/webhooks.py`: 651 lines, Medium.
- `backend/app/services/home_assistant.py`: 645 lines, Medium.
- `backend/app/services/alfred/memory.py`: 636 lines, Medium.
- `backend/app/services/unifi_protect.py`: 629 lines, Medium.
- `frontend/src/views/MovementsView.tsx`: 616 lines, Medium.
- `backend/app/modules/lpr/ubiquiti.py`: 599 lines, Medium.
- `backend/app/api/v1/visitor_passes.py`: 569 lines, Medium.
- `backend/app/services/unifi_protect_updates.py`: 556 lines, Medium.
- `backend/app/modules/unifi_protect/client.py`: 545 lines, Medium.
- `backend/app/api/v1/ai.py`: 543 lines, Medium.
- `backend/app/services/leaderboard.py`: 531 lines, Medium.
- `backend/app/ai/tool_groups/access_diagnostics.py`: 528 lines, Medium.
- `frontend/src/views/logExplorer/lprWaterfall.ts`: 528 lines, Medium.
- `frontend/src/VariableRichTextEditor.tsx`: 526 lines, Medium.
- `backend/app/services/movement_ledger.py`: 518 lines, Medium.
