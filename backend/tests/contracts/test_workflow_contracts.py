from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.automations import AutomationRuleRequest, create_automation_rule
from app.modules.notifications.base import NotificationContext
from app.services import automations as automations_module
from app.services import home_assistant as home_assistant_module
from app.services.automations import AutomationService
from app.services.home_assistant import HomeAssistantIntegrationService
from app.services.notifications import NotificationService

from .helpers import assert_contract_subset, load_contract_fixture


class _AsyncSessionContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_notification_rule_evaluation_contract_renders_channels_preview_and_action_context() -> None:
    rule = {
        "id": "rule-authorized-entry",
        "name": "Resident arrival alert",
        "trigger_event": "authorized_entry",
        "is_active": True,
        "conditions": [],
        "actions": [
            {
                "id": "action-1",
                "type": "in_app",
                "target_mode": "all",
                "target_ids": [],
                "title_template": "@FirstName arrived",
                "message_template": "@DisplayName arrived in @VehicleName at @Time",
                "media": {"mode": "none"},
                "actionable": {"enabled": False},
            }
        ],
    }
    context = NotificationContext(
        event_type="authorized_entry",
        subject="Steph arrived",
        severity="info",
        facts={
            "first_name": "Steph",
            "display_name": "Steph",
            "vehicle_display_name": "Tesla Model Y",
            "registration_number": "PE70DHX",
            "occurred_at": "2026-05-31T08:15:00+00:00",
            "direction": "entry",
            "decision": "granted",
            "source": "ubiquiti",
            "message": "Steph arrived",
        },
    )

    preview = await NotificationService().preview_rule(rule, context)

    assert_contract_subset(preview, load_contract_fixture("workflows/notification_authorized_entry.json"))


@pytest.mark.asyncio
async def test_automation_workflow_contract_preserves_dry_run_without_side_effects(monkeypatch) -> None:
    monkeypatch.setattr(automations_module, "AsyncSessionLocal", lambda: _AsyncSessionContext())
    rule = {
        "id": "automation-gate-open-dry-run",
        "name": "Open gate for known plate",
        "is_active": True,
        "triggers": [{"id": "trigger-known-plate", "type": "vehicle.known_plate", "config": {}}],
        "conditions": [],
        "actions": [
            {
                "id": "action-1",
                "type": "gate.open",
                "reason_template": "@FirstName arrived in @VehicleName",
            }
        ],
    }
    trigger_payload = {
        "subject": "Steph arrived",
        "person_id": "22222222-2222-2222-2222-222222222222",
        "vehicle_id": "33333333-3333-3333-3333-333333333333",
        "access_event_id": "11111111-1111-1111-1111-111111111111",
        "facts": {
            "first_name": "Steph",
            "display_name": "Steph",
            "vehicle_display_name": "Tesla Model Y",
            "registration_number": "PE70DHX",
            "occurred_at": "2026-05-31T08:15:00+00:00",
        },
    }

    dry_run = await AutomationService().dry_run_rule(
        rule,
        trigger_key="vehicle.known_plate",
        trigger_payload=trigger_payload,
    )

    assert_contract_subset(dry_run, load_contract_fixture("workflows/automation_gate_open_dry_run.json"))
    assert dry_run["executed"] is False
    assert all(action["executed"] is False for action in dry_run["action_previews"])


@pytest.mark.asyncio
async def test_workflow_creation_contract_requires_confirmation_before_mutation() -> None:
    request = AutomationRuleRequest(
        name="Open gate for known plate",
        triggers=[{"type": "vehicle.known_plate", "config": {}}],
        actions=[{"type": "gate.open", "reason_template": "@FirstName arrived"}],
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_automation_rule(request, user=SimpleNamespace(id=uuid.uuid4()), session=SimpleNamespace())

    assert exc_info.value.status_code == 428
    assert "confirmation" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_integration_status_display_contract_exposes_safe_redacted_health(monkeypatch) -> None:
    async def fake_runtime_config():
        return SimpleNamespace(
            home_assistant_url="http://homeassistant.local:8123",
            home_assistant_token="super-secret-token",
            home_assistant_gate_entities=[
                {
                    "entity_id": "cover.top_gate",
                    "name": "Top Gate",
                    "open_service": "cover.open_cover",
                }
            ],
            home_assistant_gate_open_service="cover.open_cover",
            home_assistant_garage_door_entities=[],
            home_assistant_default_media_player="media_player.kitchen",
        )

    monkeypatch.setattr(home_assistant_module, "get_runtime_config", fake_runtime_config)
    service = HomeAssistantIntegrationService(client=SimpleNamespace())
    service._connected = False
    service._last_error = "401 Unauthorized"
    service._state_cache["cover.top_gate"] = {
        "state": "closed",
        "last_changed": "2026-05-31T08:00:00+00:00",
    }
    service._state_cache["binary_sensor.front_door"] = {
        "state": "off",
        "last_changed": "2026-05-31T08:00:00+00:00",
    }
    service._state_cache["binary_sensor.back_door"] = {
        "state": "off",
        "last_changed": "2026-05-31T08:00:00+00:00",
    }
    service._state_cache["input_boolean.top_gate_maintenance_mode"] = {
        "state": "off",
        "last_changed": "2026-05-31T08:00:00+00:00",
    }

    status = await service.status(refresh=False)

    assert status["configured"] is True
    assert status["connected"] is False
    assert status["degraded"] is True
    assert status["last_error"] == "401 Unauthorized"
    assert status["gate_entities"][0]["entity_id"] == "cover.top_gate"
    assert "super-secret-token" not in json.dumps(status)
