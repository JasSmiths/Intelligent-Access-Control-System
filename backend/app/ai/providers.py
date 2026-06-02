import base64
import json
from time import monotonic
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.logging import get_logger
from app.services.settings import get_runtime_config
from app.services.type_helpers import as_dict

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
    usage_summary: dict[str, Any] | None = None


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
        max_output_tokens: int | None = None,
        prompt_cache_key: str | None = None,
        prompt_cache_retention: str | None = None,
        metadata: dict[str, Any] | None = None,
        request_purpose: str | None = None,
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
        async with httpx.AsyncClient(
            timeout=self._timeout or runtime.llm_timeout_seconds,
            trust_env=False,
        ) as client:
            response = await client.post(url, headers=headers, json=json_body)

        if response.status_code >= 400:
            raise RuntimeError(f"{self.name} returned {response.status_code}: {response.text[:400]}")
        return response.json()

    async def _post_with_response_metadata(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, str], float]:
        runtime = await get_runtime_config()
        started = monotonic()
        async with httpx.AsyncClient(
            timeout=self._timeout or runtime.llm_timeout_seconds,
            trust_env=False,
        ) as client:
            response = await client.post(url, headers=headers, json=json_body)
        elapsed_ms = (monotonic() - started) * 1000.0

        if response.status_code >= 400:
            raise RuntimeError(f"{self.name} returned {response.status_code}: {response.text[:400]}")
        return response.json(), dict(response.headers), elapsed_ms


class OpenAIResponsesProvider(BaseHttpProvider):
    """OpenAI Responses API provider.

    OpenAI's Responses API supports application-defined function tools. The
    agent still has provider-neutral tool planning, so this native path
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
        max_output_tokens: int | None = None,
        prompt_cache_key: str | None = None,
        prompt_cache_retention: str | None = None,
        metadata: dict[str, Any] | None = None,
        request_purpose: str | None = None,
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
        if max_output_tokens is not None:
            body["max_output_tokens"] = max(1, int(max_output_tokens))
        if prompt_cache_key:
            body["prompt_cache_key"] = _compact_cache_key(prompt_cache_key)
        if prompt_cache_retention:
            body["prompt_cache_retention"] = str(prompt_cache_retention)
        metadata_payload = _compact_metadata(metadata or {})
        if request_purpose:
            metadata_payload.setdefault("purpose", str(request_purpose)[:80])
        if metadata_payload:
            body["metadata"] = metadata_payload

        data, response_headers, elapsed_ms = await self._post_with_response_metadata(
            f"{config.openai_base_url.rstrip('/')}/responses",
            headers={
                "Authorization": f"Bearer {config.openai_api_key}",
                "Content-Type": "application/json",
            },
            json_body=body,
        )
        usage_summary = _openai_usage_summary(
            as_dict(data.get("usage")),
            model=model_name,
            request_id=response_headers.get("x-request-id") or response_headers.get("openai-request-id"),
            elapsed_ms=elapsed_ms,
            request_purpose=request_purpose,
            prompt_cache_key=body.get("prompt_cache_key"),
            prompt_cache_retention=body.get("prompt_cache_retention"),
        )
        _emit_provider_usage_audit(usage_summary)
        return LlmResult(
            text=self._extract_text(data),
            tool_calls=self._extract_tool_calls(data),
            raw=data,
            usage_summary=usage_summary,
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
        max_output_tokens: int | None = None,
        prompt_cache_key: str | None = None,
        prompt_cache_retention: str | None = None,
        metadata: dict[str, Any] | None = None,
        request_purpose: str | None = None,
    ) -> LlmResult:
        config = await get_runtime_config()
        if not config.gemini_api_key:
            raise ProviderNotConfiguredError("Gemini API key is not configured.")

        prompt = self._plain_prompt(messages, tool_results, response_schema=response_schema)
        model_name = model if model and model.startswith("gemini") else config.gemini_model
        data = await self._post(
            f"{config.gemini_base_url.rstrip('/')}/models/{model_name}:generateContent"
            f"?key={config.gemini_api_key}",
            json_body={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                **({"generationConfig": {"maxOutputTokens": max(1, int(max_output_tokens))}} if max_output_tokens else {}),
            },
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
        max_output_tokens: int | None = None,
        prompt_cache_key: str | None = None,
        prompt_cache_retention: str | None = None,
        metadata: dict[str, Any] | None = None,
        request_purpose: str | None = None,
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
                "max_tokens": max(1, int(max_output_tokens)) if max_output_tokens else 1200,
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
        max_output_tokens: int | None = None,
        prompt_cache_key: str | None = None,
        prompt_cache_retention: str | None = None,
        metadata: dict[str, Any] | None = None,
        request_purpose: str | None = None,
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
        if max_output_tokens:
            body["options"] = {"num_predict": max(1, int(max_output_tokens))}

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


class LocalDiagnosticProvider:
    """Diagnostic-only provider placeholder; Alfred V3 chat must use a hosted LLM."""

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
        max_output_tokens: int | None = None,
        prompt_cache_key: str | None = None,
        prompt_cache_retention: str | None = None,
        metadata: dict[str, Any] | None = None,
        request_purpose: str | None = None,
    ) -> LlmResult:
        raise ProviderNotConfiguredError(
            "The local diagnostics provider cannot generate Alfred answers. "
            "Configure OpenAI, Gemini, Claude, or Ollama for Alfred V3 chat."
        )

    async def analyze_image(self, prompt: str, image_bytes: bytes, mime_type: str) -> LlmResult:
        raise ImageAnalysisUnsupportedError("The local diagnostics provider cannot analyze camera images.")


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


async def complete_with_provider_options(
    provider: Any,
    messages: list[ChatMessageInput],
    **options: Any,
) -> LlmResult:
    """Call a provider with optional efficiency controls and tolerate older test doubles."""

    clean_options = {key: value for key, value in options.items() if value is not None and value != ""}
    try:
        return await provider.complete(messages, **clean_options)
    except TypeError as exc:
        if "unexpected keyword" not in str(exc):
            raise
        compatible_options = {
            key: value
            for key, value in clean_options.items()
            if key in {"tools", "tool_results", "response_schema", "reasoning_effort", "model"}
        }
        try:
            return await provider.complete(messages, **compatible_options)
        except TypeError as compatible_exc:
            if "unexpected keyword" not in str(compatible_exc):
                raise
            return await provider.complete(messages)


def _compact_cache_key(value: str) -> str:
    compact = "".join(char if char.isalnum() or char in {"-", "_", ".", ":"} else "-" for char in str(value).strip())
    return compact[:120] or "iacs-alfred"


def _compact_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    compact: dict[str, str] = {}
    for key, value in metadata.items():
        key_text = str(key).strip()[:64]
        if not key_text or value in (None, "", [], {}):
            continue
        value_text = str(value)
        if any(secret in key_text.lower() for secret in ("api_key", "password", "secret", "token", "cookie")):
            value_text = "[redacted]"
        compact[key_text] = value_text[:512]
        if len(compact) >= 16:
            break
    return compact


def _openai_usage_summary(
    usage: dict[str, Any],
    *,
    model: str,
    request_id: str | None,
    elapsed_ms: float,
    request_purpose: str | None,
    prompt_cache_key: str | None,
    prompt_cache_retention: str | None,
) -> dict[str, Any]:
    input_details = as_dict(usage.get("input_tokens_details"))
    output_details = as_dict(usage.get("output_tokens_details"))
    input_tokens = _int_usage_value(usage.get("input_tokens"))
    output_tokens = _int_usage_value(usage.get("output_tokens"))
    cached_tokens = _int_usage_value(input_details.get("cached_tokens"))
    reasoning_tokens = _int_usage_value(output_details.get("reasoning_tokens"))
    total_tokens = _int_usage_value(usage.get("total_tokens")) or input_tokens + output_tokens
    cache_hit_ratio = round(cached_tokens / input_tokens, 4) if input_tokens else 0.0
    return {
        "provider": "openai",
        "model": model,
        "request_id": request_id or "",
        "purpose": request_purpose or "",
        "latency_ms": round(elapsed_ms, 1),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "reasoning_tokens": reasoning_tokens,
        "cache_hit_ratio": cache_hit_ratio,
        "prompt_cache_key": prompt_cache_key or "",
        "prompt_cache_retention": prompt_cache_retention or "",
    }


def _int_usage_value(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _emit_provider_usage_audit(usage_summary: dict[str, Any]) -> None:
    try:
        from app.services.telemetry import TELEMETRY_CATEGORY_ALFRED, emit_audit_log

        emit_audit_log(
            category=TELEMETRY_CATEGORY_ALFRED,
            action="alfred.provider.openai.usage",
            actor="Alfred_AI",
            target_entity="OpenAIResponse",
            target_id=str(usage_summary.get("request_id") or "") or None,
            target_label=str(usage_summary.get("purpose") or "openai"),
            outcome="success",
            level="info",
            metadata=usage_summary,
        )
    except Exception as exc:
        logger.debug("openai_usage_audit_failed", extra={"error": str(exc)[:180]})


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
        "local": LocalDiagnosticProvider(),
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
