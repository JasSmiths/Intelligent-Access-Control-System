"""Current provider calls preserve options and never retry by weakening the request."""

import asyncio
from inspect import signature

import pytest

from app.ai.providers import (
    ChatMessageInput,
    ClaudeProvider,
    GeminiProvider,
    LlmProvider,
    LlmResult,
    LocalDiagnosticProvider,
    OllamaProvider,
    OpenAIResponsesProvider,
    complete_with_provider_options,
)


class RecordingProvider:
    name = "synthetic"

    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure
        self.result = LlmResult(text="synthetic response")

    async def complete(self, messages, tools=None, tool_results=None, **options):
        self.calls.append((messages, tools, tool_results, options))
        if self.failure is not None:
            raise self.failure
        return self.result


@pytest.mark.asyncio
async def test_options_are_forwarded_without_dropping_schema_or_efficiency_controls():
    provider = RecordingProvider()
    messages = [ChatMessageInput(role="user", content="synthetic request")]
    tools = [{"name": "synthetic_tool"}]
    tool_results = []
    options = {
        "response_schema": {"type": "object"},
        "reasoning_effort": "low",
        "model": "synthetic-model",
        "max_output_tokens": 64,
        "prompt_cache_key": "synthetic-cache",
        "prompt_cache_retention": "24h",
        "metadata": {"source": "synthetic"},
        "request_purpose": "synthetic-test",
    }
    result = await complete_with_provider_options(
        provider, messages, tools=tools, tool_results=tool_results, **options,
    )
    assert result is provider.result
    assert provider.calls == [(messages, tools, tool_results, options)]


@pytest.mark.asyncio
async def test_absent_options_are_omitted_but_empty_collections_and_zero_are_preserved():
    provider = RecordingProvider()
    await complete_with_provider_options(provider, [], tools=[], tool_results=[], model=None,
                                         reasoning_effort="", max_output_tokens=0, metadata={})
    assert provider.calls == [([], [], [], {"max_output_tokens": 0, "metadata": {}})]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    TypeError("unexpected keyword argument 'prompt_cache_key'"),
    RuntimeError("synthetic provider rejection"),
    asyncio.CancelledError("synthetic cancellation"),
])
async def test_provider_failure_propagates_from_one_attempt(failure):
    provider = RecordingProvider(failure)
    with pytest.raises(type(failure)) as caught:
        await complete_with_provider_options(provider, [], response_schema={"type": "object"},
                                             prompt_cache_key="synthetic-cache")
    assert caught.value is failure
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_outdated_double_is_rejected_instead_of_called_without_options():
    class OutdatedDouble:
        name = "outdated-test-only"
        calls = 0

        async def complete(self, messages):
            self.calls += 1
            return LlmResult(text="must not be returned")

    provider = OutdatedDouble()
    with pytest.raises(TypeError, match="unexpected keyword"):
        await complete_with_provider_options(provider, [], response_schema={"type": "object"})
    assert provider.calls == 0


@pytest.mark.parametrize("provider_type", [
    OpenAIResponsesProvider, GeminiProvider, ClaudeProvider, OllamaProvider, LocalDiagnosticProvider,
])
def test_supported_provider_signatures_accept_every_current_protocol_option(provider_type):
    contract = signature(LlmProvider.complete)
    # Signature binding does not construct a provider or contact an external service.
    options = {name: None for name in contract.parameters if name not in {"self", "messages"}}
    signature(provider_type.complete).bind(object(), [], **options)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TypeError("unexpected keyword argument 'model'"), RuntimeError("synthetic rejection")])
async def test_planner_propagates_provider_failure_without_a_reduced_argument_retry(failure):
    from app.services.alfred.planner import plan_with_llm

    provider = RecordingProvider(failure)
    with pytest.raises(type(failure)) as caught:
        await plan_with_llm(provider, message="synthetic request", actor_context={}, memories=[],
                            session_memory={}, tools=[], attachments=[], model="synthetic-model")
    assert caught.value is failure
    assert len(provider.calls) == 1
    assert provider.calls[0][3]["model"] == "synthetic-model"
    assert "response_schema" in provider.calls[0][3]
