# Phase 4: Multi-Provider LLM Agent

## Delivered

- Multi-provider LLM backend:
  - `local` deterministic fallback.
  - OpenAI Responses API adapter.
  - Gemini `generateContent` adapter.
  - Anthropic Claude Messages adapter.
  - Ollama local chat adapter.
- Provider selection by environment variable or per-request override.
- Persistent chat memory:
  - `chat_sessions`
  - `chat_messages`
  - Session context for pronoun/follow-up resolution.
- Agent tools:
  - `query_presence`
  - `query_access_events`
  - `query_anomalies`
  - `summarize_access_rhythm`
  - `calculate_visit_duration`
  - `trigger_anomaly_alert`
- Retrieval-augmented answers through live system tools:
  - The agent retrieves current database state before answering operational
    questions.
  - Tool results are inserted into the model context as source-of-truth data.
- Native tool-call support for OpenAI where returned by the model, plus
  provider-neutral deterministic planning so Gemini, Claude, Ollama, and the
  local fallback can all use the same system capabilities.
- HTTP and WebSocket chat APIs for the frontend global chat UI.

## Configuration

```env
IACS_LLM_PROVIDER=local

IACS_OPENAI_API_KEY=
IACS_OPENAI_MODEL=gpt-5
IACS_OPENAI_BASE_URL=https://api.openai.com/v1

IACS_GEMINI_API_KEY=
IACS_GEMINI_MODEL=gemini-2.5-flash
IACS_GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta

IACS_ANTHROPIC_API_KEY=
IACS_ANTHROPIC_MODEL=claude-sonnet-4-5
IACS_ANTHROPIC_BASE_URL=https://api.anthropic.com/v1

IACS_OLLAMA_BASE_URL=http://host.docker.internal:11434
IACS_OLLAMA_MODEL=llama3.1
IACS_LLM_TIMEOUT_SECONDS=45
```

The default `local` provider requires no API key and is intentionally useful for
testing the tool pipeline. It does not attempt creative language generation; it
summarizes tool outputs deterministically.

## API Endpoints

- `GET /api/v1/ai/providers`
- `GET /api/v1/ai/tools`
- `POST /api/v1/ai/chat`
- `WS /api/v1/ai/chat/ws`

Example HTTP request:

```bash
curl -X POST http://localhost:8088/api/v1/ai/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Did the gardener arrive today?"}'
```

Example WebSocket message:

```json
{"message":"Summarize today","session_id":"optional-session-id"}
```

## Memory Behavior

The first request:

```text
Did the gardener arrive today?
```

stores `gardener` as the session subject. A follow-up such as:

```text
How long did they stay?
```

resolves `they` to the gardener context and calls `calculate_visit_duration`.

## OpenAI Notes

The OpenAI adapter uses the Responses API and supports function tools. The
agent also has app-side tool planning so system tools continue to work when a
provider does not support native tool calling.
