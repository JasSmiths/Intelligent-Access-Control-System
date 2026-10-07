"""Current runtime surfaces stay retired while retained integrations remain usable.

Inspect declarations and normalize inert payloads only: no app lifespan, database,
provider requests, or historical migration imports.
"""

import httpx
import pytest
from fastapi import FastAPI

import app.models as models
from app.ai.providers import get_image_provider
from app.api.router import api_router
from app.core.config import Settings
from app.db.base import Base
from app.modules.announcements.home_assistant_tts import HomeAssistantTtsAnnouncer
from app.modules.notifications.apprise_client import AppriseNotificationSender
from app.modules.notifications.home_assistant_mobile import HomeAssistantMobileAppNotifier
from app.services.settings import (
    DEFAULT_DYNAMIC_SETTINGS,
    SECRET_KEYS,
    UnknownDynamicSettingsError,
    validate_dynamic_setting_keys,
)
from app.services.workflows import automation_definition, notification_payloads
from app.services.workflows.catalog import (
    automation_action_catalog,
    automation_trigger_catalog,
    automation_variables,
    notification_trigger_catalog,
    notification_variable_groups,
)


def test_retired_models_are_absent_from_current_metadata_and_exports():
    retired_tables = {
        "chat_sessions", "chat_messages", "alfred_approvals", "alfred_memories",
        "alfred_lessons", "alfred_feedback", "alfred_eval_examples",
        "processed_messaging_messages", "messaging_identities",
    }
    assert retired_tables.isdisjoint(Base.metadata.tables)
    assert {
        "AlfredApproval", "AlfredMemory", "AlfredLesson", "AlfredFeedback",
        "AlfredEvalExample", "ChatSession", "ChatMessage", "MessagingIdentity",
        "ProcessedMessagingMessage",
    }.isdisjoint(vars(models))
    assert {"visitor_passes", "notification_runs", "automation_runs", "access_events"} <= Base.metadata.tables.keys()


async def test_retired_api_groups_and_visitor_outreach_are_not_registered():
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    paths = {path.removeprefix("/api/v1") for path in app.openapi()["paths"]}
    retired_segments = {"alfred", "discord", "whatsapp", "incoming-messages", "chat", "chat-sessions"}
    for path in paths:
        assert retired_segments.isdisjoint(path.split("/")), path
    assert {
        "/visitor-passes/{pass_id}/whatsapp-messages",
        "/visitor-passes/{pass_id}/whatsapp-unblock",
        "/visitor-passes/{pass_id}/timeframe-requests/{request_id}/{decision}",
    }.isdisjoint(paths)
    assert {"/visitor-passes", "/notifications/catalog", "/settings", "/ai/providers"} <= paths
    assert any(path.startswith("/integrations/home-assistant/") for path in paths)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for path in (
            "/alfred/status", "/discord/status", "/whatsapp/status", "/incoming-messages",
            "/visitor-passes/00000000-0000-0000-0000-000000000001/whatsapp-messages",
        ):
            assert (await client.get("/api/v1" + path)).status_code == 404, path


def test_retired_settings_are_not_environment_fields_or_dynamic_settings():
    for names in (Settings.model_fields, DEFAULT_DYNAMIC_SETTINGS, SECRET_KEYS):
        assert not any(name.startswith(("alfred_", "discord_", "whatsapp_")) for name in names)
    retained_keys = {"home_assistant_url", "home_assistant_token", "apprise_urls", "llm_provider", "openai_api_key"}
    assert retained_keys <= Settings.model_fields.keys()
    assert retained_keys <= DEFAULT_DYNAMIC_SETTINGS.keys()
    assert {"home_assistant_token", "apprise_urls", "openai_api_key"} <= SECRET_KEYS
    for key in ("alfred_enabled", "discord_bot_token", "whatsapp_enabled"):
        with pytest.raises(UnknownDynamicSettingsError) as error:
            validate_dynamic_setting_keys({key: "synthetic"})
        assert error.value.unknown_keys == [key]


def test_workflow_catalogs_and_normalizers_exclude_retired_messaging_contracts():
    notification_triggers = {
        event["value"] for group in notification_trigger_catalog() for event in group["events"]
    }
    automation_triggers = {
        trigger["type"] for group in automation_trigger_catalog() for trigger in group["triggers"]
    }
    retired_triggers = {
        "ai.phrase_received", "ai.issue_detected", "time.ai_text", "automation.whatsapp",
        "visitor_pass_arranged", "visitor_pass_timeframe_change_requested",
        "visitor_pass.arranged", "visitor_pass.timeframe_change_requested", "agent_anomaly_alert",
    }
    assert retired_triggers.isdisjoint(notification_triggers | automation_triggers)
    assert "authorized_entry" in notification_triggers
    assert "time.cron" in automation_triggers

    actions = {action["type"] for group in automation_action_catalog() for action in group["actions"]}
    assert {"discord", "whatsapp", "integration.whatsapp.send_message"}.isdisjoint(actions)
    assert {"gate.open", "notification.enable", "notification.disable"} <= actions
    variables = {item["name"].lower() for group in notification_variable_groups() for item in group["items"]}
    variables.update(variable.name.lower() for variable in automation_variables())
    assert {
        "alfredphrase", "alfredissue", "visitorpasscurrentwindow", "visitorpassrequestedwindow",
        "visitorpassoriginaltime", "visitorpassrequestedtime", "visitorpassvisitormessage", "visitormessage",
    }.isdisjoint(variables)
    assert "visitorpassregistration" in variables

    notification_actions = notification_payloads.normalize_actions([
        {"type": action} for action in ("discord", "whatsapp", "mobile", "voice", "in_app")
    ])
    assert [action["type"] for action in notification_actions] == ["mobile", "voice", "in_app"]
    automation_actions = automation_definition.normalize_actions([
        {"type": "integration.whatsapp.send_message"}, {"type": "notification.disable"},
    ])
    assert [action["type"] for action in automation_actions] == ["notification.disable"]
    triggers = automation_definition.normalize_triggers([
        {"type": "ai.phrase_received"}, {"type": "time.ai_text"},
        {"type": "time.every_x", "config": {"interval": 5, "unit": "minutes"}},
    ])
    assert [trigger["type"] for trigger in triggers] == ["time.every_x"]


def test_retained_notification_and_camera_provider_interfaces_remain_available():
    assert callable(AppriseNotificationSender.send)
    assert callable(HomeAssistantMobileAppNotifier.send)
    assert callable(HomeAssistantTtsAnnouncer.announce)
    for name in ("local", "openai", "gemini", "claude", "ollama"):
        provider = get_image_provider(name)
        assert provider.name == name
        assert callable(provider.analyze_image)
