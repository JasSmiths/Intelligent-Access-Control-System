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
    ) -> LlmResult:
        """Return model text and optional tool calls."""


class ProviderNotConfiguredError(RuntimeError):
    """Raised when a selected provider is missing required credentials."""


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

        body: dict[str, Any] = {
            "model": config.openai_model,
            "instructions": system,
            "input": input_items,
        }
        if tools:
            body["tools"] = [self._to_openai_tool(tool) for tool in tools]
            body["parallel_tool_calls"] = True

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
    ) -> LlmResult:
        config = await get_runtime_config()
        if not config.gemini_api_key:
            raise ProviderNotConfiguredError("Gemini API key is not configured.")

        prompt = self._plain_prompt(messages, tool_results)
        data = await self._post(
            f"{config.gemini_base_url.rstrip('/')}/models/{config.gemini_model}:generateContent"
            f"?key={config.gemini_api_key}",
            json_body={"contents": [{"role": "user", "parts": [{"text": prompt}]}]},
        )
        return LlmResult(text=self._extract_text(data), raw=data)

    def _plain_prompt(
        self, messages: list[ChatMessageInput], tool_results: list[dict[str, Any]] | None
    ) -> str:
        lines = [f"{message.role}: {message.content}" for message in messages]
        if tool_results:
            lines.append(f"tool_results: {json.dumps(tool_results)}")
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


class OllamaProvider(BaseHttpProvider):
    name = "ollama"

    async def complete(
        self,
        messages: list[ChatMessageInput],
        tools: list[dict[str, Any]] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
    ) -> LlmResult:
        config = await get_runtime_config()
        body: dict[str, Any] = {
            "model": config.ollama_model,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
            "stream": False,
        }
        if tool_results:
            body["messages"].append({"role": "user", "content": f"Tool results: {json.dumps(tool_results)}"})

        data = await self._post(
            f"{config.ollama_base_url.rstrip('/')}/api/chat",
            json_body=body,
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
    ) -> LlmResult:
        if tool_results:
            return LlmResult(text=self._summarize_tools(tool_results))
        user_message = next((message.content for message in reversed(messages) if message.role == "user"), "")
        return LlmResult(
            text=(
                "I can help with presence, access events, anomalies, site rhythm summaries, "
                f"and alert notifications. You asked: {user_message}"
            )
        )

    def _summarize_tools(self, tool_results: list[dict[str, Any]]) -> str:
        summaries: list[str] = []
        for result in tool_results:
            output = result.get("output", {})
            tool_name = result.get("name", "tool")
            if tool_name == "query_access_events":
                summaries.append(self._summarize_events(output))
            elif tool_name == "query_presence":
                summaries.append(self._summarize_presence(output))
            elif tool_name == "query_anomalies":
                summaries.append(self._summarize_anomalies(output))
            elif tool_name == "calculate_visit_duration":
                summaries.append(self._summarize_duration(output))
            elif tool_name == "summarize_access_rhythm":
                summaries.append(self._summarize_rhythm(output))
            elif tool_name == "trigger_anomaly_alert":
                summaries.append(f"Alert queued: {output.get('title')}")
            else:
                summaries.append(f"{tool_name}: {json.dumps(output, default=str)}")
        return "\n".join(summaries)

    def _summarize_events(self, output: dict[str, Any]) -> str:
        events = output.get("events", [])
        if not events:
            return "I found no matching access events."
        latest = events[0]
        person = latest.get("person") or latest.get("registration_number")
        decision = latest.get("decision")
        direction = latest.get("direction")
        if decision == "denied":
            return f"{person} had a denied access event at {latest.get('occurred_at')}."
        return f"{person} had a {direction} event at {latest.get('occurred_at')}."

    def _summarize_presence(self, output: dict[str, Any]) -> str:
        records = output.get("presence", [])
        if not records:
            return "I found no matching presence records."
        return "; ".join(f"{row['person']} is {row['state']}" for row in records)

    def _summarize_anomalies(self, output: dict[str, Any]) -> str:
        anomalies = output.get("anomalies", [])
        if not anomalies:
            return "There are no matching anomalies."
        return "; ".join(
            f"{row['severity']} {row['type']}: {row['message']}" for row in anomalies[:5]
        )

    def _summarize_duration(self, output: dict[str, Any]) -> str:
        if not output.get("matched_events"):
            return "I found no matching visit events to calculate a duration."
        return f"The matched visit duration is {output.get('duration_human')}."

    def _summarize_rhythm(self, output: dict[str, Any]) -> str:
        return (
            f"{output.get('period')} summary: {output.get('total_events')} events, "
            f"{output.get('entries')} entries, {output.get('exits')} exits, "
            f"{output.get('denials')} denials, {output.get('anomaly_events')} anomaly events."
        )


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
