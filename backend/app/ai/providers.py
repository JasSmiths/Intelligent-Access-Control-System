"""Camera image analysis through the configured provider's native image API."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.services.settings import get_runtime_config


@dataclass(frozen=True)
class ImageAnalysisResult:
    text: str
    raw: dict[str, Any] | None = None


class ImageAnalysisProvider(Protocol):
    name: str

    async def analyze_image(self, prompt: str, image_bytes: bytes, mime_type: str) -> ImageAnalysisResult:
        """Return camera analysis without agent or tool execution."""


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
            raise RuntimeError(f"{self.name} returned {response.status_code}.")
        return response.json()


class OpenAIResponsesProvider(BaseHttpProvider):
    name = "openai"

    async def analyze_image(self, prompt: str, image_bytes: bytes, mime_type: str) -> ImageAnalysisResult:
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
        return ImageAnalysisResult(text=self._extract_text(data), raw=data)

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


class GeminiProvider(BaseHttpProvider):
    name = "gemini"

    async def analyze_image(self, prompt: str, image_bytes: bytes, mime_type: str) -> ImageAnalysisResult:
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
        return ImageAnalysisResult(text=self._extract_text(data), raw=data)

    def _extract_text(self, data: dict[str, Any]) -> str:
        candidates = data.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "\n".join(part.get("text", "") for part in parts).strip()


class ClaudeProvider(BaseHttpProvider):
    name = "claude"

    async def analyze_image(self, prompt: str, image_bytes: bytes, mime_type: str) -> ImageAnalysisResult:
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
        return ImageAnalysisResult(
            text="\n".join(
                item.get("text", "") for item in data.get("content", []) if item.get("type") == "text"
            ).strip(),
            raw=data,
        )


class OllamaProvider(BaseHttpProvider):
    name = "ollama"

    async def analyze_image(self, prompt: str, image_bytes: bytes, mime_type: str) -> ImageAnalysisResult:
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
        return ImageAnalysisResult(text=data.get("message", {}).get("content", ""), raw=data)


class LocalImageProvider:
    """The existing local selection disables external camera analysis."""

    name = "local"

    async def analyze_image(self, prompt: str, image_bytes: bytes, mime_type: str) -> ImageAnalysisResult:
        raise ImageAnalysisUnsupportedError("The local provider does not support image analysis.")


async def analyze_image_with_provider(
    provider_name: str,
    *,
    prompt: str,
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
) -> ImageAnalysisResult:
    provider = get_image_provider(provider_name)
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


def get_image_provider(provider_name: str) -> ImageAnalysisProvider:
    provider = provider_name.lower()
    providers: dict[str, ImageAnalysisProvider] = {
        "local": LocalImageProvider(),
        "openai": OpenAIResponsesProvider(),
        "gemini": GeminiProvider(),
        "claude": ClaudeProvider(),
        "anthropic": ClaudeProvider(),
        "ollama": OllamaProvider(),
    }
    try:
        return providers[provider]
    except KeyError as exc:
        raise ValueError(f"Unsupported image provider: {provider}") from exc
