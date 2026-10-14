from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.api.v1 import settings as endpoint
from app.models import User
from app.services import integration_testing
from app.services.settings import DEFAULT_DYNAMIC_SETTINGS, runtime_config_from_values


def runtime():
    return runtime_config_from_values({key: default for key, (_, default, _) in DEFAULT_DYNAMIC_SETTINGS.items()})


@pytest.mark.parametrize("provider,values,path", [
    ("home_assistant", {"home_assistant_url": "https://synthetic.invalid", "home_assistant_token": "synthetic-only"}, "/api/"),
    ("openai", {"openai_base_url": "https://synthetic.invalid", "openai_api_key": "synthetic-only"}, "/models"),
    ("gemini", {"gemini_base_url": "https://synthetic.invalid", "gemini_api_key": "synthetic-only"}, "/models"),
    ("claude", {"anthropic_base_url": "https://synthetic.invalid", "anthropic_api_key": "synthetic-only"}, "/models"),
    ("ollama", {"ollama_base_url": "https://synthetic.invalid"}, "/api/tags"),
])
async def test_connection_io_is_owned_by_adapter_and_uses_draft_config(monkeypatch, provider, values, path):
    monkeypatch.setattr(integration_testing, "get_runtime_config", AsyncMock(return_value=runtime()))
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    await integration_testing.test_integration_connection(provider, values)
    assert len(requests) == 1 and requests[0].url.path == path
    assert requests[0].url.host == "synthetic.invalid"
    assert "synthetic-only" not in str(requests[0].url)


async def test_blank_secret_test_placeholder_retains_current_credential(monkeypatch):
    from dataclasses import replace
    config = replace(runtime(), home_assistant_url="https://synthetic.invalid", home_assistant_token="synthetic-only")
    monkeypatch.setattr(integration_testing, "get_runtime_config", AsyncMock(return_value=config))
    test = AsyncMock()
    monkeypatch.setattr(integration_testing, "test_home_assistant", test)
    await integration_testing.test_integration_connection("home_assistant", {"home_assistant_token": ""})
    assert test.await_args.args[0].home_assistant_token == "synthetic-only"


async def test_route_retains_confirmation_audit_and_redacts_provider_failure(monkeypatch):
    confirmation, audit = AsyncMock(), AsyncMock()
    monkeypatch.setattr(endpoint, "consume_action_confirmation", confirmation)
    monkeypatch.setattr(endpoint, "record_audit_log", audit)
    monkeypatch.setattr(endpoint, "test_integration_connection", AsyncMock(side_effect=RuntimeError("private-provider-payload")))
    with pytest.raises(Exception) as error:
        await endpoint.test_connection(endpoint.ConnectionTestRequest(integration="openai", values={}, confirmation_token="synthetic"),
            User(username="synthetic", full_name="Synthetic", is_active=True), SimpleNamespace())
    assert confirmation.await_count == audit.await_count == 1
    assert "private-provider-payload" not in str(error.value)
    assert "private-provider-payload" not in str(audit.await_args)
