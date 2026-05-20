# Phase 4: Alfred V3 Agent Runtime

## Current State

- Alfred is v3-only in normal operation: an LLM-owned planner selects scoped tool
  calls from registry metadata, the executor runs only those tools, and answers
  are grounded in tool results.
- `alfred_agent_mode` is obsolete and is removed from dynamic settings during
  settings seeding. Do not reintroduce mode switches unless the old rollback
  runtime is deliberately restored and retested.
- `backend/app/services/chat.py` is the facade for HTTP/WebSocket chat,
  confirmations, attachments, direct result formatting, and compatibility
  behavior.
- `backend/app/services/alfred/*` owns planner, executor, streaming, permissions,
  memory, embeddings, and feedback workflows.
- `backend/app/services/chat_routing.py` now contains only small guided-flow and
  request-shape helpers that are still used by the facade. It is not a v2
  deterministic router.
- `backend/app/ai/tool_groups/*` owns domain tool catalogs and metadata.
  `backend/app/ai/tools.py` remains a live facade because several handlers still
  depend on it; migrate handlers incrementally instead of deleting it wholesale.

## Providers and Settings

LLM provider, model, timeout, learning, memory, embedding, and prompt-cache
settings are dynamic settings under Settings UI/API. Provider secrets are
encrypted in `system_settings`.

The local provider can still support development/testing paths, but Alfred live
ops must not rely on deterministic fallback answers. If the provider or planner
cannot produce a valid plan, Alfred fails closed with a configuration/retry
message instead of inventing operational facts.

## Tooling Rules

- Tool results are the source of truth for people, vehicles, schedules, access
  events, device states, DVLA data, telemetry, and alerts.
- Non-read-only tools must require confirmation and return
  `requires_confirmation` before mutation.
- Tool metadata must declare categories, safety level, permissions, default
  limits, and examples/return schemas where useful.
- Registry changes must update `backend/tests/test_chat_agent.py` for tool
  surface, permission, confirmation, and domain-card behavior.
- Outputs should stay compact JSON and must redact secrets, cookies, tokens, and
  media blobs.

## API Endpoints

- `GET /api/v1/ai/providers`
- `GET /api/v1/ai/tools`
- `GET /api/v1/ai/agent/status`
- `POST /api/v1/ai/chat`
- `POST /api/v1/ai/chat/confirm`
- `POST /api/v1/ai/chat/stream`
- `POST /api/v1/ai/chat/upload`
- `POST /api/v1/ai/feedback`
- `GET/POST /api/v1/ai/training/*`
- `WS /api/v1/ai/chat/ws`

All dashboard/chat routes require authentication after first-run setup. The
WhatsApp and Discord bridges create actor context before planning so tool
permissions still apply outside the web UI.
