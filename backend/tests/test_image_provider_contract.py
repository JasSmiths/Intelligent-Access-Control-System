"""Camera image analysis preserves inputs and never retries rejected requests."""

import asyncio
from types import SimpleNamespace

import pytest

from app.ai.providers import ImageAnalysisUnsupportedError, analyze_image_with_provider


@pytest.mark.asyncio
async def test_image_analysis_preserves_inputs_and_returns_provider_result(monkeypatch):
    calls = []
    result = SimpleNamespace(text="synthetic camera evidence")

    async def analyze(prompt, image_bytes, mime_type):
        calls.append((prompt, image_bytes, mime_type))
        return result

    provider = SimpleNamespace(name="synthetic", analyze_image=analyze)
    monkeypatch.setattr("app.ai.providers.get_image_provider", lambda _name: provider)
    output = await analyze_image_with_provider(
        "synthetic", prompt="Inspect this camera image", image_bytes=b"synthetic-image", mime_type="image/png",
    )
    assert output is result
    assert calls == [("Inspect this camera image", b"synthetic-image", "image/png")]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", [SimpleNamespace(name="synthetic"), SimpleNamespace(name="synthetic", analyze_image=None)])
async def test_provider_without_image_support_is_rejected(monkeypatch, provider):
    monkeypatch.setattr("app.ai.providers.get_image_provider", lambda _name: provider)
    with pytest.raises(ImageAnalysisUnsupportedError, match="does not support image analysis"):
        await analyze_image_with_provider("synthetic", prompt="Inspect", image_bytes=b"synthetic-image")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TypeError("synthetic rejection"), RuntimeError("synthetic rejection"), asyncio.CancelledError()])
async def test_image_provider_failure_propagates_after_one_attempt(monkeypatch, failure):
    attempts = []

    async def analyze(*args):
        attempts.append(args)
        raise failure

    monkeypatch.setattr("app.ai.providers.get_image_provider", lambda _name: SimpleNamespace(name="synthetic", analyze_image=analyze))
    with pytest.raises(type(failure)) as caught:
        await analyze_image_with_provider("synthetic", prompt="Inspect", image_bytes=b"synthetic-image")
    assert caught.value is failure
    assert attempts == [("Inspect", b"synthetic-image", "image/jpeg")]


@pytest.mark.asyncio
async def test_unknown_image_provider_is_rejected(monkeypatch):
    def unknown_provider(_name):
        raise ValueError("Unsupported image provider")

    monkeypatch.setattr("app.ai.providers.get_image_provider", unknown_provider)
    with pytest.raises(ValueError, match="Unsupported image provider"):
        await analyze_image_with_provider("unknown", prompt="Inspect", image_bytes=b"synthetic-image")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["openai", "gemini", "claude", "ollama"])
async def test_native_image_requests_keep_camera_bytes_and_extract_provider_text(monkeypatch, name):
    from app.ai import providers

    config = SimpleNamespace(
        openai_api_key="synthetic", openai_model="synthetic-openai", openai_base_url="http://synthetic.invalid/openai",
        gemini_api_key="synthetic", gemini_model="synthetic-gemini", gemini_base_url="http://synthetic.invalid/gemini",
        anthropic_api_key="synthetic", anthropic_model="synthetic-claude", anthropic_base_url="http://synthetic.invalid/claude",
        ollama_model="llava-synthetic", ollama_base_url="http://synthetic.invalid/ollama",
    )
    async def runtime(): return config
    response = {
        "output_text": "Synthetic vehicle", "candidates": [{"content": {"parts": [{"text": "Synthetic vehicle"}]}}],
        "content": [{"type": "text", "text": "Synthetic vehicle"}], "message": {"content": "Synthetic vehicle"},
    }
    calls = []
    async def post(_self, url, *, headers=None, json_body):
        calls.append((url, headers, json_body))
        return response
    monkeypatch.setattr(providers, "get_runtime_config", runtime)
    monkeypatch.setattr(providers.BaseHttpProvider, "_post", post)
    result = await providers.analyze_image_with_provider(name, prompt="Inspect", image_bytes=b"image", mime_type="image/png")
    assert result.text == "Synthetic vehicle" and result.raw is response
    assert len(calls) == 1
    url, _, body = calls[0]
    assert not {"tools", "tool_choice", "parallel_tool_calls"}.intersection(body)
    if name == "openai":
        assert url.endswith("/responses")
        assert body["input"][0]["content"] == [
            {"type": "input_text", "text": "Inspect"},
            {"type": "input_image", "image_url": "data:image/png;base64,aW1hZ2U=", "detail": "auto"},
        ]
    elif name == "gemini":
        assert url.endswith("/models/synthetic-gemini:generateContent?key=synthetic")
        assert body["contents"][0]["parts"] == [{"text": "Inspect"}, {"inline_data": {"mime_type": "image/png", "data": "aW1hZ2U="}}]
    elif name == "claude":
        assert url.endswith("/messages")
        assert body["messages"][0]["content"] == [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aW1hZ2U="}},
            {"type": "text", "text": "Inspect"},
        ]
    else:
        assert url.endswith("/api/chat") and body["stream"] is False
        assert body["messages"] == [{"role": "user", "content": "Inspect", "images": ["aW1hZ2U="]}]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["openai", "gemini", "claude"])
async def test_unconfigured_camera_provider_fails_before_transport(monkeypatch, name):
    from app.ai import providers

    async def runtime(): return SimpleNamespace(openai_api_key="", gemini_api_key="", anthropic_api_key="")
    async def forbidden(*args, **kwargs): raise AssertionError("Unconfigured providers must never send")
    monkeypatch.setattr(providers, "get_runtime_config", runtime)
    monkeypatch.setattr(providers.BaseHttpProvider, "_post", forbidden)
    with pytest.raises(providers.ProviderNotConfiguredError):
        await providers.analyze_image_with_provider(name, prompt="Inspect", image_bytes=b"image")


@pytest.mark.asyncio
async def test_local_selection_disables_remote_camera_analysis():
    with pytest.raises(ImageAnalysisUnsupportedError):
        await analyze_image_with_provider("local", prompt="Inspect", image_bytes=b"image")


@pytest.mark.asyncio
async def test_non_vision_ollama_model_fails_before_transport(monkeypatch):
    from app.ai import providers

    async def runtime(): return SimpleNamespace(ollama_model="llama3.1")
    async def forbidden(*args, **kwargs): raise AssertionError("Non-vision models must never receive images")
    monkeypatch.setattr(providers, "get_runtime_config", runtime)
    monkeypatch.setattr(providers.BaseHttpProvider, "_post", forbidden)
    with pytest.raises(ImageAnalysisUnsupportedError):
        await providers.analyze_image_with_provider("ollama", prompt="Inspect", image_bytes=b"image")
