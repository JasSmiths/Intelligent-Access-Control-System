"""Inert provider receipt contracts; PostgreSQL fencing runs in phase1 diagnostics."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.modules.access_devices import esphome as esphome_module
from app.modules.access_devices.base import (
    AccessDeviceBinding, AccessDeviceCommandResult, AccessDeviceCommandUncertain,
    AccessDeviceEntity, AccessDeviceProviderUnavailable,
)
from app.modules.access_devices.home_assistant import HomeAssistantAccessDeviceProvider
from app.modules.gate.base import CommandDelivery, GateState
from app.modules.home_assistant.client import HomeAssistantClient
from app.services import access_devices as devices_module
from app.services.access_devices import AccessDeviceService


@pytest.mark.asyncio
@pytest.mark.parametrize("code,delivery", [
    (401, "rejected"), (403, "rejected"), (404, "rejected"), (405, "rejected"),
    (302, "unknown"), (400, "unknown"), (408, "unknown"), (429, "unknown"), (500, "unknown"), (504, "unknown"),
])
async def test_ha_http_status_is_not_blanket_proof_of_rejection(monkeypatch, code, delivery):
    requests = []

    async def transport(request):
        requests.append(request.method)
        return httpx.Response(code, text="Synthetic refusal or uncertain response")

    client = HomeAssistantClient()
    monkeypatch.setattr(client, "config", AsyncMock(return_value=SimpleNamespace(
        home_assistant_url="https://synthetic.invalid", home_assistant_token="synthetic-only",
    )))
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport), trust_env=False) as http:
        monkeypatch.setattr(client, "_request_client", AsyncMock(return_value=http))
        provider = HomeAssistantAccessDeviceProvider(client)
        binding = AccessDeviceBinding("home_assistant", "cover.synthetic")
        if delivery == "unknown":
            with pytest.raises(AccessDeviceCommandUncertain):
                await provider.command_cover(binding, "close", "Synthetic command")
        else:
            result = await provider.command_cover(binding, "close", "Synthetic command")
            assert result.accepted is False
            assert result.delivery == CommandDelivery.REJECTED
    assert requests == ["POST"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error,expected", [
    (httpx.ConnectError, AccessDeviceProviderUnavailable),
    (httpx.ConnectTimeout, AccessDeviceProviderUnavailable),
    (httpx.PoolTimeout, AccessDeviceProviderUnavailable),
    (httpx.ReadTimeout, AccessDeviceCommandUncertain),
    (httpx.WriteTimeout, AccessDeviceCommandUncertain),
    (httpx.ReadError, AccessDeviceCommandUncertain),
    (httpx.WriteError, AccessDeviceCommandUncertain),
])
async def test_ha_transport_failure_preserves_send_certainty(monkeypatch, error, expected):
    async def transport(request):
        raise error("Synthetic transport fault", request=request)

    client = HomeAssistantClient()
    monkeypatch.setattr(client, "config", AsyncMock(return_value=SimpleNamespace(
        home_assistant_url="https://synthetic.invalid", home_assistant_token="synthetic-only",
    )))
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport), trust_env=False) as http:
        monkeypatch.setattr(client, "_request_client", AsyncMock(return_value=http))
        with pytest.raises(expected):
            await HomeAssistantAccessDeviceProvider(client).command_cover(
                AccessDeviceBinding("home_assistant", "cover.synthetic"), "open", "Synthetic command",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["open", "close"])
@pytest.mark.parametrize("primary_outcome", ["unknown", "rejected"])
async def test_no_fallback_after_unknown_or_explicit_rejection(monkeypatch, action, primary_outcome):
    calls = []

    async def primary(binding, action, reason):
        calls.append("primary")
        if primary_outcome == "unknown":
            raise RuntimeError("Unexpected failure after entering provider")
        return AccessDeviceCommandResult(False, GateState.UNKNOWN, "Explicit refusal")

    async def fallback(*args):
        calls.append("fallback")
        raise AssertionError("Must not issue another physical command")

    service = AccessDeviceService()
    monkeypatch.setattr(service, "_provider_order_for_device", AsyncMock(return_value=["home_assistant", "esphome"]))
    monkeypatch.setattr(devices_module, "get_access_device_provider", lambda name: SimpleNamespace(
        command_cover=primary if name == "home_assistant" else fallback,
    ))
    device = AccessDeviceEntity("synthetic", "gate", "Synthetic", bindings={
        "home_assistant": AccessDeviceBinding("home_assistant", "cover.synthetic"),
        "esphome": AccessDeviceBinding("esphome", "synthetic"),
    })
    result = await service._command_with_failover(device, action, "Synthetic command")
    assert calls == ["primary"]
    assert result.delivery.value == primary_outcome
    assert result.requires_reconciliation is (primary_outcome == "unknown")
    assert result.verified is False


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, True])
async def test_esphome_exception_during_native_write_is_uncertain(monkeypatch, live):
    calls = []

    def send(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("Synthetic lost write outcome")

    device = {"id": "synthetic", "name": "Synthetic", "host": "synthetic.invalid"}
    client = SimpleNamespace(cover_command=send)
    provider = esphome_module.ESPHomeAccessDeviceProvider()
    binding = AccessDeviceBinding("esphome", "synthetic:gate", config={"key": 7})
    now = asyncio.get_running_loop().time()
    if live:
        session = esphome_module._ESPHomeDeviceSession(provider, device, asyncio.Queue())
        session.client, session.connected = client, True
        monkeypatch.setattr(session, "_force_disconnect", AsyncMock())
        invocation = session.command_cover(binding, "open", "Synthetic", cover=SimpleNamespace(key=7),
            started_at=now, device_resolved_at=now, cover_resolution="binding_config")
    else:
        monkeypatch.setattr(provider, "_connected_client", AsyncMock(return_value=(None, client)))
        monkeypatch.setattr(esphome_module, "_disconnect", AsyncMock())
        invocation = provider._command_cover_cold(device, binding, "open", "Synthetic",
            started_at=now, device_resolved_at=now, fallback_reason="Synthetic disconnected stream",
            configured_cover=SimpleNamespace(key=7))
    with pytest.raises(AccessDeviceCommandUncertain):
        await invocation
    assert calls == [{"key": 7, "position": 1.0}]


@pytest.mark.asyncio
async def test_ha_command_and_observation_use_the_validated_snapshot(monkeypatch):
    requests = []

    async def transport(request):
        requests.append((request.method, request.url.host))
        return httpx.Response(200, json=[] if request.method == "POST" else {
            "entity_id": "cover.synthetic", "state": "open", "attributes": {},
        })

    client = HomeAssistantClient()
    cached_config = AsyncMock(side_effect=AssertionError("Command must not use a process-local configuration cache"))
    monkeypatch.setattr(client, "config", cached_config)
    runtime = SimpleNamespace(home_assistant_url="https://validated.synthetic.invalid", home_assistant_token="synthetic-only")
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport), trust_env=False) as http:
        monkeypatch.setattr(client, "_request_client", AsyncMock(return_value=http))
        result = await HomeAssistantAccessDeviceProvider(client).command_cover(
            AccessDeviceBinding("home_assistant", "cover.synthetic"), "open", "Synthetic", runtime_config=runtime)
    assert requests == [("POST", "validated.synthetic.invalid"), ("GET", "validated.synthetic.invalid")]
    assert result.accepted and result.observation is not None
    assert result.metadata["acceptance_basis"] == "home_assistant_http_2xx"
    cached_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_esphome_state_timeout_cannot_verify_with_a_pre_command_value():
    provider = esphome_module.ESPHomeAccessDeviceProvider()
    session = esphome_module._ESPHomeDeviceSession(provider,
        {"id": "synthetic", "name": "Synthetic", "host": "synthetic.invalid"}, asyncio.Queue())
    after = asyncio.get_running_loop().time()
    session.states[7] = esphome_module._ESPHomeStateRecord(
        GateState.OPEN, "open", datetime.now(tz=UTC), after - 1)
    assert await session.wait_state_after(7, after=after, timeout=0) == GateState.UNKNOWN
    session.states[7].updated_monotonic = after
    assert await session.wait_state_after(7, after=after, timeout=0) == GateState.OPEN
