"""WhatsApp vendor boundary contracts; MockTransport cannot reach a network."""
import httpx
import pytest
from dataclasses import asdict
from types import SimpleNamespace

from app.modules.messaging.whatsapp import WhatsAppIntegrationConfig, WhatsAppTransport
from app.modules.notifications.base import NotificationDeliveryError


def config():
    return WhatsAppIntegrationConfig(True, "synthetic-token", "synthetic-number", "synthetic-business",
        "synthetic-verify", "synthetic-secret", "25.0", "synthetic-template", "en")


def test_authorized_runtime_conversion_is_pure_and_preserves_exact_values(monkeypatch):
    from app.services.messaging import whatsapp_configuration as owner

    def forbidden_read(*args, **kwargs):
        raise AssertionError("The authorized snapshot must not reread settings")

    monkeypatch.setattr(owner, "get_runtime_config", forbidden_read)
    monkeypatch.setattr(owner, "get_runtime_config_for_session", forbidden_read)
    values = asdict(config())
    runtime = SimpleNamespace(**{f"whatsapp_{key}": value for key, value in values.items()})
    result = owner.whatsapp_config_from_runtime(runtime)
    assert asdict(result) == {**values, "graph_api_version": "v25.0"}
    overridden = owner.whatsapp_config_from_runtime(runtime, {"whatsapp_phone_number_id": "other-synthetic-number"})
    assert overridden.phone_number_id == "other-synthetic-number" and overridden.access_token == values["access_token"]


async def test_send_and_connection_check_use_one_owned_inert_client():
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json={"messages": [{"id": "synthetic-result"}]})

    transport = WhatsAppTransport()
    client = httpx.AsyncClient(transport=httpx.MockTransport(provider), trust_env=False)
    transport._client = client
    try:
        receipt = await transport.send(config(), {"type": "text", "to": "447700900123"})
        await transport.test_connection(config())
        assert receipt == {"messages": [{"id": "synthetic-result"}]}
        assert [request.method for request in calls] == ["POST", "GET"]
        assert calls[0].url.path == "/v25.0/synthetic-number/messages"
        assert calls[1].url.params["fields"] == "id,display_phone_number,verified_name"
        assert transport.last_error is None
    finally:
        await transport.stop()
    assert client.is_closed and transport._client is None


async def test_provider_rejection_is_failure_and_retained_as_status():
    transport = WhatsAppTransport()
    transport._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(403, text="synthetic rejection")), trust_env=False)
    try:
        with pytest.raises(NotificationDeliveryError, match="403"):
            await transport.send(config(), {"type": "text"})
        assert transport.last_error == "HTTP 403: synthetic rejection"
    finally:
        await transport.stop()


async def test_response_loss_propagates_without_resending():
    attempts = []

    def provider(request):
        attempts.append(request)
        raise httpx.ReadTimeout("synthetic response loss", request=request)

    transport = WhatsAppTransport()
    transport._client = httpx.AsyncClient(transport=httpx.MockTransport(provider), trust_env=False)
    try:
        with pytest.raises(httpx.ReadTimeout):
            await transport.send(config(), {"type": "text"})
        assert len(attempts) == 1
    finally:
        await transport.stop()
