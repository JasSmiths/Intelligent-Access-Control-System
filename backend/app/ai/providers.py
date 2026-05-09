import base64
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.logging import get_logger
from app.services.settings import get_runtime_config

logger = get_logger(__name__)


@dataclass(frozen=True)
class ChatMessageInput:
    role: str
    content: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LlmResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] | None = None


class LlmProvider(Protocol):
    name: str

    async def complete(
        self,
        messages: list[ChatMessageInput],
        tools: list[dict[str, Any]] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
        *,
        response_schema: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LlmResult:
        """Return model text and optional tool calls."""


class ProviderNotConfiguredError(RuntimeError):
    """Raised when a selected provider is missing required credentials."""


class ImageAnalysisUnsupportedError(RuntimeError):
    """Raised when the selected provider cannot analyze images."""


class BaseHttpProvider:
    name = "base"

    def __init__(self, timeout: float | None = None) -> None:
        self._timeout = timeout

    async def _post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any],
    ) -> dict[str, Any]:
        runtime = await get_runtime_config()
        async with httpx.AsyncClient(timeout=self._timeout or runtime.llm_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=json_body)

        if response.status_code >= 400:
            raise RuntimeError(f"{self.name} returned {response.status_code}: {response.text[:400]}")
        return response.json()


class OpenAIResponsesProvider(BaseHttpProvider):
    """OpenAI Responses API provider.

    OpenAI's Responses API supports application-defined function tools. The
    agent still has provider-neutral fallback tool planning, so this native path
    is an enhancement rather than a hard dependency.
    """

    name = "openai"

    async def complete(
        self,
        messages: list[ChatMessageInput],
        tools: list[dict[str, Any]] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
        *,
        response_schema: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LlmResult:
        config = await get_runtime_config()
        if not config.openai_api_key:
            raise ProviderNotConfiguredError("OpenAI API key is not configured.")

        system, input_items = self._split_messages(messages)
        if tool_results:
            input_items.extend(
                {
                    "type": "function_call_output",
                    "call_id": result["call_id"],
                    "output": json.dumps(result["output"]),
                }
                for result in tool_results
                if result.get("call_id")
            )

        model_name = model or config.openai_model
        body: dict[str, Any] = {
            "model": model_name,
            "instructions": system,
            "input": input_items,
        }
        if tools:
            body["tools"] = [self._to_openai_tool(tool) for tool in tools]
            body["parallel_tool_calls"] = True
        if response_schema:
            body["text"] = {"format": self._response_format(response_schema)}
        if reasoning_effort and _supports_reasoning_effort(model_name):
            body["reasoning"] = {"effort": reasoning_effort}

        data = await self._post(
            f"{config.openai_base_url.rstrip('/')}/responses",
            headers={
                "Authorization": f"Bearer {config.openai_api_key}",
                "Content-Type": "application/json",
            },
            json_body=body,
        )
        return LlmResult(
            text=self._extract_text(data),
            tool_calls=self._extract_tool_calls(data),
            raw=data,
        )

    async def analyze_image(self, prompt: str, image_bytes: bytes, mime_type: str) -> LlmResult:
        config = await get_runtime_config()
        if not config.openai_api_key:
            raise ProviderNotConfiguredError("OpenAI API key is not configured.")

        data_url = _image_data_url(image_bytes, mime_type)
        data = await self._post(
            f"{config.openai_base_url.rstrip('/')}/responses",
            headers={
                "Authorization": f"Bearer {config.openai_api_key}",
                "Content-Type": "application/json",
            },
            json_body={
                "model": config.openai_model,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {"type": "input_image", "image_url": data_url, "detail": "auto"},
                        ],
                    }
                ],
            },
        )
        return LlmResult(text=self._extract_text(data), raw=data)

    def _split_messages(self, messages: list[ChatMessageInput]) -> tuple[str, list[dict[str, str]]]:
        instructions = "\n".join(message.content for message in messages if message.role == "system")
        input_items = [
            {"role": message.role, "content": message.content}
            for message in messages
            if message.role in {"user", "assistant"}
        ]
        return instructions, input_items

    def _to_openai_tool(self, tool: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "function",
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["parameters"],
        }

    def _response_format(self, response_schema: dict[str, Any]) -> dict[str, Any]:
        schema = response_schema.get("schema") if isinstance(response_schema.get("schema"), dict) else response_schema
        return {
            "type": "json_schema",
            "name": str(response_schema.get("name") or "alfred_structured_response"),
            "schema": schema,
            "strict": True,
        }

    def _extract_text(self, data: dict[str, Any]) -> str:
        if data.get("output_text"):
            return data["output_text"]

        parts: list[str] = []
        for item in data.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"}:
                    parts.append(content.get("text", ""))
        return "\n".join(part for part in parts if part).strip()

    def _extract_tool_calls(self, data: dict[str, Any]) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for item in data.get("output", []):
            if item.get("type") != "function_call":
                continue
            try:
                arguments = json.loads(item.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            calls.append(
                ToolCall(
                    id=item.get("call_id") or item.get("id") or item.get("name", "tool_call"),
                    name=item.get("name", ""),
                    arguments=arguments,
                )
            )
        return calls


class GeminiProvider(BaseHttpProvider):
    name = "gemini"

    async def complete(
        self,
        messages: list[ChatMessageInput],
        tools: list[dict[str, Any]] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
        *,
        response_schema: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LlmResult:
        config = await get_runtime_config()
        if not config.gemini_api_key:
            raise ProviderNotConfiguredError("Gemini API key is not configured.")

        prompt = self._plain_prompt(messages, tool_results, response_schema=response_schema)
        model_name = model if model and model.startswith("gemini") else config.gemini_model
        data = await self._post(
            f"{config.gemini_base_url.rstrip('/')}/models/{model_name}:generateContent"
            f"?key={config.gemini_api_key}",
            json_body={"contents": [{"role": "user", "parts": [{"text": prompt}]}]},
        )
        return LlmResult(text=self._extract_text(data), raw=data)

    async def analyze_image(self, prompt: str, image_bytes: bytes, mime_type: str) -> LlmResult:
        config = await get_runtime_config()
        if not config.gemini_api_key:
            raise ProviderNotConfiguredError("Gemini API key is not configured.")

        data = await self._post(
            f"{config.gemini_base_url.rstrip('/')}/models/{config.gemini_model}:generateContent"
            f"?key={config.gemini_api_key}",
            json_body={
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": prompt},
                            {
                                "inline_data": {
                                    "mime_type": mime_type,
                                    "data": _image_base64(image_bytes),
                                }
                            },
                        ],
                    }
                ]
            },
        )
        return LlmResult(text=self._extract_text(data), raw=data)

    def _plain_prompt(
        self,
        messages: list[ChatMessageInput],
        tool_results: list[dict[str, Any]] | None,
        *,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        lines = [f"{message.role}: {message.content}" for message in messages]
        if tool_results:
            lines.append(f"tool_results: {json.dumps(tool_results)}")
        if response_schema:
            lines.append(
                "Return only compact JSON matching this schema: "
                f"{json.dumps(response_schema, separators=(',', ':'))}"
            )
        return "\n".join(lines)

    def _extract_text(self, data: dict[str, Any]) -> str:
        candidates = data.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "\n".join(part.get("text", "") for part in parts).strip()


class ClaudeProvider(BaseHttpProvider):
    name = "claude"

    async def complete(
        self,
        messages: list[ChatMessageInput],
        tools: list[dict[str, Any]] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
        *,
        response_schema: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LlmResult:
        config = await get_runtime_config()
        if not config.anthropic_api_key:
            raise ProviderNotConfiguredError("Anthropic API key is not configured.")

        system = "\n".join(message.content for message in messages if message.role == "system")
        body_messages = [
            {"role": message.role, "content": message.content}
            for message in messages
            if message.role in {"user", "assistant"}
        ]
        if tool_results:
            body_messages.append({"role": "user", "content": f"Tool results: {json.dumps(tool_results)}"})
        if response_schema:
            body_messages.append(
                {
                    "role": "user",
                    "content": (
                        "Return only compact JSON matching this schema: "
                        f"{json.dumps(response_schema, separators=(',', ':'))}"
                    ),
                }
            )
        model_name = model if model and model.startswith("claude") else config.anthropic_model

        data = await self._post(
            f"{config.anthropic_base_url.rstrip('/')}/messages",
            headers={
                "x-api-key": config.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json_body={
                "model": model_name,
                "max_tokens": 1200,
                "system": system,
                "messages": body_messages,
            },
        )
        return LlmResult(
            text="\n".join(
                item.get("text", "") for item in data.get("content", []) if item.get("type") == "text"
            ).strip(),
            raw=data,
        )

    async def analyze_image(self, prompt: str, image_bytes: bytes, mime_type: str) -> LlmResult:
        config = await get_runtime_config()
        if not config.anthropic_api_key:
            raise ProviderNotConfiguredError("Anthropic API key is not configured.")

        data = await self._post(
            f"{config.anthropic_base_url.rstrip('/')}/messages",
            headers={
                "x-api-key": config.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json_body={
                "model": config.anthropic_model,
                "max_tokens": 1200,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": mime_type,
                                    "data": _image_base64(image_bytes),
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            },
        )
        return LlmResult(
            text="\n".join(
                item.get("text", "") for item in data.get("content", []) if item.get("type") == "text"
            ).strip(),
            raw=data,
        )


class OllamaProvider(BaseHttpProvider):
    name = "ollama"

    async def complete(
        self,
        messages: list[ChatMessageInput],
        tools: list[dict[str, Any]] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
        *,
        response_schema: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LlmResult:
        config = await get_runtime_config()
        body: dict[str, Any] = {
            "model": model or config.ollama_model,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
            "stream": False,
        }
        if tool_results:
            body["messages"].append({"role": "user", "content": f"Tool results: {json.dumps(tool_results)}"})
        if response_schema:
            body["messages"].append(
                {
                    "role": "user",
                    "content": (
                        "Return only compact JSON matching this schema: "
                        f"{json.dumps(response_schema, separators=(',', ':'))}"
                    ),
                }
            )

        data = await self._post(
            f"{config.ollama_base_url.rstrip('/')}/api/chat",
            json_body=body,
        )
        return LlmResult(text=data.get("message", {}).get("content", ""), raw=data)

    async def analyze_image(self, prompt: str, image_bytes: bytes, mime_type: str) -> LlmResult:
        config = await get_runtime_config()
        if not _looks_like_ollama_vision_model(config.ollama_model):
            raise ImageAnalysisUnsupportedError(
                f"Ollama model '{config.ollama_model}' is not marked as vision-capable. "
                "Select a vision model such as llama3.2-vision, llava, bakllava, or qwen-vl."
            )

        data = await self._post(
            f"{config.ollama_base_url.rstrip('/')}/api/chat",
            json_body={
                "model": config.ollama_model,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [_image_base64(image_bytes)],
                    }
                ],
                "stream": False,
            },
        )
        return LlmResult(text=data.get("message", {}).get("content", ""), raw=data)


class LocalProvider:
    """Deterministic fallback so the assistant remains useful without API keys."""

    name = "local"

    async def complete(
        self,
        messages: list[ChatMessageInput],
        tools: list[dict[str, Any]] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
        *,
        response_schema: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LlmResult:
        if tool_results:
            return LlmResult(text=self._summarize_tools(tool_results))
        user_message = next((message.content for message in reversed(messages) if message.role == "user"), "")
        return LlmResult(
            text=(
                "I'm Alfred: warm gatehouse brain, sensible clipboard, occasional dry remark. "
                "I can help with presence, access events, anomalies, site rhythm summaries, "
                f"and alert notifications. You asked: {user_message}"
            )
        )

    async def analyze_image(self, prompt: str, image_bytes: bytes, mime_type: str) -> LlmResult:
        raise ImageAnalysisUnsupportedError("The local fallback provider cannot analyze camera images.")

    def _summarize_tools(self, tool_results: list[dict[str, Any]]) -> str:
        summaries: list[str] = []
        for result in tool_results:
            output = result.get("output", {})
            tool_name = result.get("name", "tool")
            if tool_name == "resolve_human_entity":
                summaries.append(self._summarize_entity_resolution(output))
            elif tool_name == "query_access_events":
                summaries.append(self._summarize_events(output))
            elif tool_name == "query_presence":
                summaries.append(self._summarize_presence(output))
            elif tool_name == "query_device_states":
                summaries.append(self._summarize_device_states(output))
            elif tool_name in {"open_device", "command_device", "open_gate"}:
                summaries.append(self._summarize_device_open(output))
            elif tool_name in {"get_maintenance_status", "enable_maintenance_mode", "disable_maintenance_mode", "toggle_maintenance_mode"}:
                summaries.append(self._summarize_maintenance_tool(tool_name, output))
            elif tool_name == "override_schedule":
                if output.get("requires_confirmation"):
                    summaries.append(str(output.get("detail") or "Please confirm the schedule override."))
                elif output.get("created"):
                    summaries.append(
                        f"Created a temporary access override for {output.get('person')} until {output.get('ends_at_display')}."
                    )
                else:
                    summaries.append(str(output.get("error") or output.get("detail") or "Schedule override was not created."))
            elif tool_name == "query_anomalies":
                summaries.append(self._summarize_anomalies(output))
            elif tool_name == "calculate_visit_duration":
                summaries.append(self._summarize_duration(output))
            elif tool_name == "query_leaderboard":
                summaries.append(self._summarize_leaderboard(output))
            elif tool_name == "summarize_access_rhythm":
                summaries.append(self._summarize_rhythm(output))
            elif tool_name == "trigger_anomaly_alert":
                summaries.append(f"Alert queued: {output.get('title')}")
            elif tool_name == "lookup_dvla_vehicle":
                summaries.append(self._summarize_dvla_vehicle(output))
            elif tool_name == "analyze_camera_snapshot":
                summaries.append(self._summarize_camera_analysis(output))
            elif tool_name == "read_chat_attachment":
                summaries.append(self._summarize_attachment_read(output))
            elif tool_name in {"export_presence_report_csv", "generate_contractor_invoice_pdf"}:
                summaries.append(self._summarize_generated_file(output))
            elif tool_name == "get_camera_snapshot":
                summaries.append(self._summarize_camera_attachment(output))
            elif tool_name == "diagnose_access_event":
                summaries.append(self._summarize_access_diagnostic(output))
            elif tool_name == "query_lpr_timing":
                summaries.append(self._summarize_lpr_timing(output))
            elif tool_name == "query_vehicle_detection_history":
                summaries.append(self._summarize_detection_history(output))
            elif tool_name == "get_telemetry_trace":
                summaries.append(self._summarize_telemetry_trace(output))
            elif tool_name in {
                "query_schedules",
                "get_schedule",
                "create_schedule",
                "update_schedule",
                "delete_schedule",
                "assign_schedule_to_entity",
                "verify_schedule_access",
            }:
                summaries.append(self._summarize_schedule_tool(tool_name, output))
            elif tool_name in {
                "query_notification_catalog",
                "query_notification_workflows",
                "get_notification_workflow",
                "create_notification_workflow",
                "update_notification_workflow",
                "delete_notification_workflow",
                "preview_notification_workflow",
                "test_notification_workflow",
            }:
                summaries.append(self._summarize_notification_tool(tool_name, output))
            elif tool_name in {
                "query_automation_catalog",
                "query_automations",
                "get_automation",
                "create_automation",
                "edit_automation",
                "delete_automation",
                "enable_automation",
                "disable_automation",
            }:
                summaries.append(self._summarize_automation_tool(tool_name, output))
            else:
                summaries.append(f"{tool_name}: {json.dumps(output, default=str)}")
        return "\n".join(summaries)

    def _summarize_entity_resolution(self, output: dict[str, Any]) -> str:
        status = output.get("status")
        if status == "unique" and isinstance(output.get("match"), dict):
            match = output["match"]
            label = match.get("display_name") or match.get("name") or match.get("registration_number")
            return f"Resolved {output.get('query')} to {match.get('type')} {label}. Neatly pinned to the board."
        if status == "ambiguous":
            matches = output.get("matches") if isinstance(output.get("matches"), list) else []
            labels = [
                str(match.get("display_name") or match.get("name") or match.get("registration_number") or match.get("entity_id"))
                for match in matches[:4]
                if isinstance(match, dict)
            ]
            return f"That reference is ambiguous: {', '.join(labels)}."
        return f"I could not resolve {output.get('query') or 'that reference'} to a known IACS entity. I will not guess; that way lies nonsense."

    def _summarize_telemetry_trace(self, output: dict[str, Any]) -> str:
        if not output.get("found"):
            return f"Telemetry trace: {output.get('error') or 'not found'}"
        trace = output.get("trace") if isinstance(output.get("trace"), dict) else {}
        return (
            f"Telemetry trace {trace.get('trace_id')} was {trace.get('status')} "
            f"and took {trace.get('duration_ms')}ms."
        )

    def _summarize_events(self, output: dict[str, Any]) -> str:
        events = output.get("events", [])
        if not events:
            return "I found no matching access events. The logbook is politely blank."
        latest = events[0]
        person = latest.get("person") or latest.get("registration_number")
        decision = latest.get("decision")
        direction = latest.get("direction")
        occurred_at = latest.get("occurred_at_display") or latest.get("occurred_at")
        if decision == "denied":
            return f"{person} had a denied access event at {occurred_at}."
        return f"{person} had a {direction} event at {occurred_at}."

    def _summarize_access_diagnostic(self, output: dict[str, Any]) -> str:
        if not output.get("found"):
            return f"Access diagnostic: {output.get('error') or 'no matching event found'}. I checked the usual cupboards."
        event = output.get("event") if isinstance(output.get("event"), dict) else {}
        recognition = output.get("recognition") if isinstance(output.get("recognition"), dict) else {}
        gate = output.get("gate") if isinstance(output.get("gate"), dict) else {}
        notifications = output.get("notifications") if isinstance(output.get("notifications"), dict) else {}
        subject = event.get("person") or event.get("registration_number") or "matched event"
        parts = [
            f"Access diagnostic for {subject} at {event.get('occurred_at_display') or event.get('occurred_at')}.",
        ]
        if recognition.get("total_pipeline_ms") is not None:
            parts.append(f"Total pipeline: {recognition.get('total_pipeline_ms')}ms.")
        if recognition.get("debounce_or_recognition_ms") is not None:
            parts.append(f"Debounce/recognition: {recognition.get('debounce_or_recognition_ms')}ms.")
        if recognition.get("likely_delay_reason"):
            parts.append(str(recognition["likely_delay_reason"]))
        if gate.get("outcome_reason"):
            parts.append(str(gate["outcome_reason"]))
        if notifications.get("summary"):
            parts.append(f"Notifications: {notifications['summary']}")
        return " ".join(parts)

    def _summarize_lpr_timing(self, output: dict[str, Any]) -> str:
        observations = output.get("observations") if isinstance(output.get("observations"), list) else []
        if not observations:
            return "No recent raw LPR timing observations matched. The stopwatch drawer is empty."
        latest = observations[0]
        delay = latest.get("captured_to_received_ms")
        if delay is not None:
            return (
                f"Latest raw LPR timing for {latest.get('registration_number')}: "
                f"{delay}ms captured-to-received from {latest.get('source_detail') or latest.get('source')}."
            )
        return f"I found {len(observations)} recent LPR timing observation(s), but no captured-to-received delay was available."

    def _summarize_detection_history(self, output: dict[str, Any]) -> str:
        if not output.get("found"):
            return f"Detection history: {output.get('error') or 'no matching events found'}"
        registration_number = output.get("registration_number") or "That vehicle"
        count = output.get("total_count")
        first_seen = output.get("first_seen_at_display") or output.get("first_seen_at")
        last_seen = output.get("last_seen_at_display") or output.get("last_seen_at")
        return (
            f"{registration_number} has been detected {count} time{'s' if count != 1 else ''}. "
            f"First seen: {first_seen}; last seen: {last_seen}."
        )

    def _summarize_presence(self, output: dict[str, Any]) -> str:
        records = output.get("presence", [])
        if not records:
            return "I found no matching presence records. Nobody is waving from the ledger."
        return "; ".join(f"{row['person']} is {row['state']}" for row in records)

    def _summarize_device_states(self, output: dict[str, Any]) -> str:
        if output.get("error"):
            return f"Device state check failed: {output.get('error')}"
        devices = output.get("devices", [])
        if not devices:
            target = output.get("target")
            if target:
                return f"I found no configured device matching {target}. No labelled lever for that one, alas."
            return "I found no configured gate, door, or garage-door states. The control panel is looking unusually minimalist."
        return "; ".join(
            f"{device.get('name') or device.get('entity_id')} is {device.get('state', 'unknown')}"
            for device in devices
        )

    def _summarize_device_open(self, output: dict[str, Any]) -> str:
        action = str(output.get("action") or "open")
        if output.get("requires_details"):
            return str(output.get("detail") or f"Which gate or garage door should I {action}?")
        if output.get("requires_confirmation"):
            device = output.get("device") if isinstance(output.get("device"), dict) else {}
            target = device.get("name") or output.get("target") or "that device"
            return f"Please use the confirmation button before I {action} {target}. Safety first; cape later."
        device = output.get("device") if isinstance(output.get("device"), dict) else {}
        name = device.get("name") or output.get("target") or "the device"
        success = bool(output.get("opened") if action == "open" else output.get("closed"))
        if success:
            return f"{'Opened' if action == 'open' else 'Closed'} {name}. Logged, tidy, and pleasingly uneventful."
        return f"I could not {action} {name}: {output.get('detail') or output.get('error') or 'command failed'}"

    def _summarize_maintenance_tool(self, tool_name: str, output: dict[str, Any]) -> str:
        if output.get("requires_confirmation"):
            return str(output.get("detail") or "Please use the confirmation button first.")
        status = output.get("maintenance_mode") if isinstance(output.get("maintenance_mode"), dict) else output
        active = bool(status.get("is_active"))
        if tool_name in {"enable_maintenance_mode", "toggle_maintenance_mode"} and output.get("state") == "enabled":
            return "Maintenance Mode is now enabled. Automated actions are disabled." if active else "Maintenance Mode was not enabled."
        if tool_name in {"disable_maintenance_mode", "toggle_maintenance_mode"} and output.get("state") == "disabled":
            duration = status.get("duration_label")
            return (
                f"Maintenance Mode is now disabled. It had been active for {duration}."
                if duration
                else "Maintenance Mode is now disabled. Automated actions have resumed."
            )
        if active:
            duration = status.get("duration_label") or "less than a minute"
            actor = status.get("enabled_by") or "System"
            return f"Maintenance Mode is enabled by {actor}; active for {duration}."
        return "Maintenance Mode is disabled. Automated actions are available; the machinery may proceed with dignity."

    def _summarize_anomalies(self, output: dict[str, Any]) -> str:
        anomalies = output.get("anomalies", [])
        if not anomalies:
            return "There are no matching anomalies. A rare case of nothing being exactly what we wanted."
        return "; ".join(
            f"{row['severity']} {row['type']}: {row['message']}" for row in anomalies[:5]
        )

    def _summarize_duration(self, output: dict[str, Any]) -> str:
        if not output.get("matched_events"):
            return "I found no matching visit events to calculate a duration. No timestamps, no stopwatch theatrics."
        return f"The matched visit duration is {output.get('duration_human')}."

    def _summarize_rhythm(self, output: dict[str, Any]) -> str:
        return (
            f"{output.get('period')} summary: {output.get('total_events')} events, "
            f"{output.get('entries')} entries, {output.get('exits')} exits, "
            f"{output.get('denials')} denials, {output.get('anomaly_events')} anomaly events."
        )

    def _summarize_leaderboard(self, output: dict[str, Any]) -> str:
        if output.get("error"):
            return f"Leaderboard query failed: {output.get('error')}"
        top = output.get("top_known") if isinstance(output.get("top_known"), dict) else None
        known = output.get("known") if isinstance(output.get("known"), list) else []
        unknown = output.get("unknown") if isinstance(output.get("unknown"), list) else []
        lines: list[str] = []
        if top:
            lines.append(
                f"Top Charts leader: {top.get('display_name') or top.get('registration_number')} "
                f"with {top.get('read_count')} detections."
            )
        if known:
            vip = "; ".join(
                f"#{row.get('rank')} {row.get('display_name') or row.get('registration_number')} ({row.get('read_count')})"
                for row in known[:5]
                if isinstance(row, dict)
            )
            if vip:
                lines.append(f"VIP Lounge: {vip}.")
        if unknown:
            mystery = "; ".join(
                f"#{row.get('rank')} {row.get('registration_number')} ({row.get('read_count')})"
                for row in unknown[:5]
                if isinstance(row, dict)
            )
            if mystery:
                lines.append(f"Mystery Guests: {mystery}.")
        return " ".join(lines) if lines else "I found no leaderboard entries yet. The podium is spotless."

    def _summarize_dvla_vehicle(self, output: dict[str, Any]) -> str:
        if output.get("error"):
            return f"DVLA lookup for {output.get('registration_number') or 'that vehicle'} failed: {output.get('error')}"

        vehicle = output.get("display_vehicle") or output.get("vehicle")
        if not isinstance(vehicle, dict):
            return "DVLA returned no vehicle details for that registration."
        normalized_vehicle = output.get("normalized_vehicle")
        if not isinstance(normalized_vehicle, dict):
            normalized_vehicle = {}

        registration_number = output.get("registration_number") or vehicle.get("registrationNumber")
        details = [
            ("Registration", registration_number),
            ("Make", vehicle.get("make")),
            ("Colour", vehicle.get("colour")),
            ("Tax status", normalized_vehicle.get("tax_status") or vehicle.get("taxStatus")),
            ("Tax due date", normalized_vehicle.get("tax_expiry") or vehicle.get("taxDueDate")),
            ("MOT status", normalized_vehicle.get("mot_status") or vehicle.get("motStatus")),
            ("MOT expiry", normalized_vehicle.get("mot_expiry") or vehicle.get("motExpiryDate")),
            ("Year of manufacture", vehicle.get("yearOfManufacture")),
            ("Fuel type", vehicle.get("fuelType")),
            ("Engine capacity", _format_engine_capacity(vehicle.get("engineCapacity"))),
            ("CO2 emissions", _format_co2(vehicle.get("co2Emissions"))),
            ("Euro status", vehicle.get("euroStatus")),
        ]
        lines = [f"{label}: {value}" for label, value in details if value not in {None, ""}]
        if not lines:
            return "DVLA returned a vehicle record, but it did not include displayable details."
        return "DVLA vehicle details, freshly polished:\n" + "\n".join(f"- {line}" for line in lines)

    def _summarize_camera_analysis(self, output: dict[str, Any]) -> str:
        if output.get("error"):
            return f"Camera analysis failed: {output.get('error')}"
        return str(output.get("analysis") or "Camera analysis returned no text.")

    def _summarize_attachment_read(self, output: dict[str, Any]) -> str:
        filename = output.get("filename") or "the attachment"
        if output.get("error"):
            return f"I could not read {filename}: {output.get('error')}"
        if output.get("analysis"):
            return f"Attachment analysis for {filename}: {output.get('analysis')}"
        text = str(output.get("text") or "").strip()
        if not text:
            return f"I read {filename}, but found no text to summarize. A very quiet document."
        preview = text[:800].rstrip()
        return f"I read {filename}. Content preview:\n{preview}"

    def _summarize_generated_file(self, output: dict[str, Any]) -> str:
        if output.get("error"):
            return f"File generation failed: {output.get('error')}"
        attachment = output.get("attachment") if isinstance(output.get("attachment"), dict) else {}
        filename = attachment.get("filename") or "the file"
        return f"I generated {filename} and attached it here. Paperwork, but make it useful."

    def _summarize_camera_attachment(self, output: dict[str, Any]) -> str:
        if output.get("error"):
            return f"Camera snapshot failed: {output.get('error')}"
        return "I fetched the latest camera snapshot and attached it here. Fresh from the lens."

    def _summarize_schedule_tool(self, tool_name: str, output: dict[str, Any]) -> str:
        if output.get("requires_details"):
            return str(output.get("detail") or "I need more schedule details before I can do that.")
        if output.get("requires_confirmation"):
            return str(output.get("detail") or "Please use the confirmation button before I change that schedule.")
        if output.get("error"):
            return f"Schedule action failed: {output.get('error')}"

        schedule = output.get("schedule") if isinstance(output.get("schedule"), dict) else {}
        name = schedule.get("name") or output.get("schedule_name") or "schedule"
        summary = schedule.get("summary")

        if tool_name == "query_schedules":
            count = output.get("count", 0)
            return f"I found {count} schedule{'s' if count != 1 else ''}. The calendar cabinet has spoken."
        if tool_name == "get_schedule":
            return f"{name}: {summary or 'schedule details returned'}."
        if tool_name == "create_schedule":
            if output.get("created"):
                return f"Created {name} with {summary or 'the requested allowed time'}. Calendar tucked in neatly."
            return str(output.get("detail") or "I did not create the schedule.")
        if tool_name == "update_schedule":
            if output.get("updated"):
                return f"Updated {name}. Calendar tidied."
            return "I did not update the schedule."
        if tool_name == "delete_schedule":
            if output.get("deleted"):
                return f"Deleted {name}. Calendar shelf cleared."
            return str(output.get("detail") or "I did not delete the schedule.")
        if tool_name == "assign_schedule_to_entity":
            if output.get("assigned"):
                return f"Assigned {name}. Schedule paperwork clipped into place."
            return "I did not assign the schedule."
        if tool_name == "verify_schedule_access":
            return str(output.get("reason") or "Schedule access verification completed.")
        return json.dumps(output, default=str)

    def _summarize_notification_tool(self, tool_name: str, output: dict[str, Any]) -> str:
        if output.get("requires_confirmation"):
            return str(output.get("detail") or "Please use the confirmation button before I make that notification change.")
        if output.get("error"):
            return f"Notification action failed: {output.get('error')}"

        if tool_name == "query_notification_catalog":
            triggers = [
                str(event.get("label") or event.get("value"))
                for group in output.get("triggers", [])
                for event in group.get("events", [])
                if isinstance(event, dict)
            ]
            integrations = [
                f"{item.get('name')} ({'configured' if item.get('configured') else 'not configured'})"
                for item in output.get("integrations", [])
                if isinstance(item, dict)
            ]
            trigger_text = ", ".join(triggers[:8]) if triggers else "the standard access-control events"
            integration_text = "; ".join(integrations) if integrations else "no delivery integrations reported"
            return (
                "Notification options available: "
                f"triggers include {trigger_text}. "
                f"Delivery options: {integration_text}."
            )

        if tool_name == "query_notification_workflows":
            workflows = output.get("workflows", [])
            if not workflows:
                return "I found no notification workflows. The notification desk is pristine."
            names = ", ".join(str(workflow.get("name")) for workflow in workflows[:5] if isinstance(workflow, dict))
            return f"I found {output.get('count', len(workflows))} notification workflow(s): {names}."

        workflow = output.get("workflow") if isinstance(output.get("workflow"), dict) else {}
        name = workflow.get("name") or output.get("workflow_name") or "notification workflow"
        if tool_name == "get_notification_workflow":
            if output.get("found"):
                return f"{name} is a notification workflow for {workflow.get('trigger_event') or 'the selected trigger'}."
            return "I could not find that notification workflow."
        if tool_name == "create_notification_workflow":
            return f"Created notification workflow {name}. Neatly filed." if output.get("created") else str(output.get("detail") or "I did not create the notification workflow.")
        if tool_name == "update_notification_workflow":
            return f"Updated notification workflow {name}." if output.get("updated") else str(output.get("detail") or "I did not update the notification workflow.")
        if tool_name == "delete_notification_workflow":
            return f"Deleted notification workflow {name}." if output.get("deleted") else str(output.get("detail") or "I did not delete the notification workflow.")
        if tool_name == "preview_notification_workflow":
            preview = output.get("preview") if isinstance(output.get("preview"), dict) else {}
            action_count = len(preview.get("actions", [])) if isinstance(preview.get("actions"), list) else 0
            return f"Previewed the notification workflow with {action_count} action(s)."
        if tool_name == "test_notification_workflow":
            return "Sent the notification workflow test. Tiny paper plane launched." if output.get("sent") else str(output.get("detail") or "I did not send the notification workflow test.")
        return "Notification workflow action completed."

    def _summarize_automation_tool(self, tool_name: str, output: dict[str, Any]) -> str:
        if output.get("requires_confirmation"):
            return str(output.get("detail") or "Please confirm before I change that automation.")
        if output.get("error"):
            return f"Automation action failed: {output.get('error')}"
        if tool_name == "query_automation_catalog":
            return "Automation options are available for time, vehicle, visitor pass, maintenance, Alfred, and webhook triggers."
        if tool_name == "query_automations":
            automations = output.get("automations", [])
            if not automations:
                return "I found no automation rules."
            names = ", ".join(str(item.get("name")) for item in automations[:5] if isinstance(item, dict))
            return f"I found {output.get('count', len(automations))} automation rule(s): {names}."
        automation = output.get("automation") if isinstance(output.get("automation"), dict) else {}
        name = automation.get("name") or output.get("automation_name") or "automation"
        if tool_name == "get_automation":
            return f"{name} is an automation rule." if output.get("found") else "I could not find that automation."
        if tool_name == "create_automation":
            return f"Created automation {name}." if output.get("created") else str(output.get("detail") or "I did not create the automation.")
        if tool_name == "edit_automation":
            return f"Updated automation {name}." if output.get("updated") else str(output.get("detail") or "I did not update the automation.")
        if tool_name == "delete_automation":
            return f"Deleted automation {name}." if output.get("deleted") else str(output.get("detail") or "I did not delete the automation.")
        if tool_name == "enable_automation":
            return f"Enabled automation {name}." if output.get("updated") else str(output.get("detail") or "I did not enable the automation.")
        if tool_name == "disable_automation":
            return f"Disabled automation {name}." if output.get("updated") else str(output.get("detail") or "I did not disable the automation.")
        return "Automation action completed."


def _format_engine_capacity(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    return f"{value} cc"


def _format_co2(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    return f"{value} g/km"


async def analyze_image_with_provider(
    provider_name: str,
    *,
    prompt: str,
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
) -> LlmResult:
    provider = get_llm_provider(provider_name)
    analyze = getattr(provider, "analyze_image", None)
    if not callable(analyze):
        raise ImageAnalysisUnsupportedError(f"{provider.name} does not support image analysis.")
    return await analyze(prompt, image_bytes, mime_type)


def _image_base64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("ascii")


def _image_data_url(image_bytes: bytes, mime_type: str) -> str:
    return f"data:{mime_type};base64,{_image_base64(image_bytes)}"


def _looks_like_ollama_vision_model(model: str) -> bool:
    normalized = model.lower()
    markers = (
        "vision",
        "llava",
        "bakllava",
        "moondream",
        "minicpm-v",
        "qwen-vl",
        "qwen2-vl",
        "qwen2.5-vl",
        "qwen3-vl",
        "gemma3",
        "granite3.2-vision",
    )
    return any(marker in normalized for marker in markers)


def _supports_reasoning_effort(model: str) -> bool:
    normalized = model.strip().lower()
    return normalized.startswith(("gpt-5", "o1", "o3", "o4"))


def get_llm_provider(provider_name: str) -> LlmProvider:
    provider = provider_name.lower()
    providers: dict[str, LlmProvider] = {
        "local": LocalProvider(),
        "openai": OpenAIResponsesProvider(),
        "gemini": GeminiProvider(),
        "claude": ClaudeProvider(),
        "anthropic": ClaudeProvider(),
        "ollama": OllamaProvider(),
    }
    try:
        return providers[provider]
    except KeyError as exc:
        raise ValueError(f"Unsupported LLM provider: {provider}") from exc
