import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

import app.api.v1.integrations as integrations_api
import app.api.v1.health as health_api
import app.services.home_assistant as home_assistant_module
from app.modules.home_assistant.client import HomeAssistantClient, HomeAssistantError, HomeAssistantState
from app.services.home_assistant import HomeAssistantIntegrationService


def _ha_runtime(**overrides):
    values = {
        "home_assistant_url": "http://homeassistant.local:8123",
        "home_assistant_token": "secret-token",
        "home_assistant_gate_entities": [
            {
                "entity_id": "cover.top_gate",
                "name": "Top Gate",
                "enabled": True,
                "open_service": "cover.open_cover",
            }
        ],
        "home_assistant_gate_entity_id": "",
        "home_assistant_gate_open_service": "cover.open_cover",
        "home_assistant_garage_door_entities": [],
        "home_assistant_default_media_player": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def test_home_assistant_status_reports_degraded_refresh_without_leaking_token(monkeypatch) -> None:
    async def fake_runtime():
        return _ha_runtime()

    class FailingClient:
        async def get_state(self, _entity_id: str):
            raise HomeAssistantError("Home Assistant returned 401: Unauthorized")

    monkeypatch.setattr(home_assistant_module, "get_runtime_config", fake_runtime)
    service = HomeAssistantIntegrationService(FailingClient())

    status = await service.status(refresh=True)

    assert status["configured"] is True
    assert status["connected"] is False
    assert status["degraded"] is True
    assert "Unauthorized" in status["last_error"]
    assert "secret-token" not in json.dumps(status)


async def test_home_assistant_degraded_transition_publishes_notification_trigger(monkeypatch) -> None:
    async def fake_runtime():
        return _ha_runtime()

    class FailingClient:
        async def get_state(self, _entity_id: str):
            raise HomeAssistantError("Unable to reach Home Assistant: All connection attempts failed")

    published = []

    async def fake_publish(event_type: str, payload: dict):
        published.append((event_type, payload))

    monkeypatch.setattr(home_assistant_module, "get_runtime_config", fake_runtime)
    monkeypatch.setattr(home_assistant_module, "event_bus", SimpleNamespace(publish=fake_publish))
    service = HomeAssistantIntegrationService(FailingClient())

    await service.status(refresh=True)
    await service.status(refresh=True)

    status_events = [payload for event_type, payload in published if event_type == "home_assistant.status"]
    notification_events = [payload for event_type, payload in published if event_type == "notification.trigger"]
    assert len(status_events) == 1
    assert len(notification_events) == 1
    payload = notification_events[0]
    assert payload["event_type"] == "integration_degraded"
    assert payload["subject"] == "Home Assistant degraded"
    assert payload["severity"] == "warning"
    assert payload["facts"]["integration_name"] == "Home Assistant"
    assert payload["facts"]["integration_reason"] == "Unable to reach Home Assistant: All connection attempts failed"
    assert "secret-token" not in json.dumps(payload)


async def test_home_assistant_status_reports_connected_after_state_refresh(monkeypatch) -> None:
    async def fake_runtime():
        return _ha_runtime()

    class WorkingClient:
        async def get_state(self, entity_id: str):
            state = "closed" if entity_id == "cover.top_gate" else "off"
            return HomeAssistantState(entity_id=entity_id, state=state, attributes={})

    monkeypatch.setattr(home_assistant_module, "get_runtime_config", fake_runtime)
    service = HomeAssistantIntegrationService(WorkingClient())

    status = await service.status(refresh=True)

    assert status["configured"] is True
    assert status["connected"] is True
    assert status["degraded"] is False
    assert status["last_error"] is None
    assert status["current_gate_state"] == "closed"
    assert status["state_refreshed_at"]


async def test_health_rollup_surfaces_degraded_integrations(monkeypatch) -> None:
    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _statement):
            return 1

    class FakeHomeAssistant:
        async def status(self, *, refresh: bool = False):
            return {
                "configured": True,
                "connected": False,
                "degraded": True,
                "last_error": "Home Assistant returned 401: Unauthorized",
                "listener_running": False,
            }

    class FakeDiscord:
        async def status(self):
            return {
                "configured": True,
                "connected": True,
                "guild_count": 1,
                "channel_count": 2,
                "last_error": None,
            }

    class FakeWhatsApp:
        async def status(self):
            return {
                "enabled": False,
                "configured": False,
                "webhook_configured": False,
                "signature_configured": False,
                "admin_target_count": 0,
                "last_error": None,
            }

    async def fake_maintenance_status():
        return {"is_active": True, "enabled_by": "Admin", "enabled_at": "2026-05-10T10:00:00+00:00"}

    monkeypatch.setattr(health_api, "AsyncSessionLocal", lambda: FakeSession())
    monkeypatch.setattr(
        health_api,
        "event_bus",
        SimpleNamespace(status=lambda: {"started": True, "connections": 1, "listeners": 3}),
    )
    monkeypatch.setattr(
        health_api,
        "get_access_event_service",
        lambda: SimpleNamespace(
            status=lambda: {
                "status": "ok",
                "worker_running": True,
                "queue_depth": 0,
                "pending_windows": 0,
                "last_error": None,
            }
        ),
    )
    monkeypatch.setattr(health_api, "get_maintenance_status", fake_maintenance_status)
    monkeypatch.setattr(health_api, "get_home_assistant_service", lambda: FakeHomeAssistant())
    monkeypatch.setattr(health_api, "get_discord_messaging_service", lambda: FakeDiscord())
    monkeypatch.setattr(health_api, "get_whatsapp_messaging_service", lambda: FakeWhatsApp())

    result = await health_api.health()

    assert result["status"] == "degraded"
    assert result["checks"]["database"]["status"] == "ok"
    assert result["checks"]["access_events"]["status"] == "ok"
    assert result["checks"]["access_events"]["worker_running"] is True
    assert result["checks"]["maintenance"]["status"] == "maintenance"
    assert result["checks"]["home_assistant"]["status"] == "degraded"
    assert result["checks"]["home_assistant"]["last_error"] == "Home Assistant returned 401: Unauthorized"
    assert result["discord"] == {
        "configured": True,
        "connected": True,
        "guild_count": 1,
        "channel_count": 2,
    }


async def test_home_assistant_client_wraps_network_failures_without_leaking_token(monkeypatch) -> None:
    async def fake_config():
        return _ha_runtime(home_assistant_url="http://homeassistant.local:8123", home_assistant_token="secret-token")

    class FailingAsyncClient:
        def __init__(self, *_, timeout, trust_env):
            assert timeout == 15
            assert trust_env is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, *_args, **_kwargs):
            raise httpx.ConnectError("All connection attempts failed")

    monkeypatch.setattr("app.modules.home_assistant.client.httpx.AsyncClient", FailingAsyncClient)
    client = HomeAssistantClient()
    monkeypatch.setattr(client, "config", fake_config)

    with pytest.raises(HomeAssistantError) as exc_info:
        await client.list_states()

    detail = str(exc_info.value)
    assert "Unable to reach Home Assistant" in detail
    assert "All connection attempts failed" in detail
    assert "secret-token" not in detail


async def test_home_assistant_entities_returns_service_unavailable_when_ha_is_down(monkeypatch) -> None:
    class FailingClient:
        async def list_states(self):
            raise HomeAssistantError("Unable to reach Home Assistant: All connection attempts failed")

    monkeypatch.setattr(integrations_api, "HomeAssistantClient", lambda: FailingClient())

    with pytest.raises(HTTPException) as exc_info:
        await integrations_api.home_assistant_entities(_=SimpleNamespace(), session=SimpleNamespace())

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Unable to reach Home Assistant: All connection attempts failed"
