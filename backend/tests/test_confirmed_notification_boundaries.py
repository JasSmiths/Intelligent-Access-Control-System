"""Database-free native-body and authoritative-config notification boundaries."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import uuid

import pytest

from app.modules.announcements import home_assistant_tts
from app.modules.messaging.whatsapp import WhatsAppIntegrationConfig
from app.modules.notifications import home_assistant_mobile
from app.modules.notifications.base import NotificationContext
from app.services import notification_requests, notifications
from app.services.notifications import NotificationService


BODY = "  Literal {registration_number} {{message}}\n£12 & <tag> — keep punctuation.  "
TITLE = "Literal {subject}"


def runtime():
    return SimpleNamespace(home_assistant_url="http://synthetic.invalid", home_assistant_token="synthetic-ha-token",
        home_assistant_tts_service="tts.synthetic_say", home_assistant_default_media_player="media_player.synthetic", apprise_urls="",
        whatsapp_enabled=True, whatsapp_access_token="synthetic-wa-token", whatsapp_phone_number_id="synthetic-phone",
        whatsapp_business_account_id="synthetic-business", whatsapp_webhook_verify_token="", whatsapp_app_secret="",
        whatsapp_graph_api_version="v25.0", whatsapp_visitor_pass_template_name="synthetic_template",
        whatsapp_visitor_pass_template_language="en", unrelated="before")


def context():
    return NotificationContext("integration_test", "Synthetic subject", "info", {"registration_number": "MUSTNOTRENDER"})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["voice", "mobile"])
async def test_literal_ha_body_reaches_actual_adapter_without_template_or_prefix_rewriting(monkeypatch, kind):
    config = runtime()
    client = SimpleNamespace(call_service=AsyncMock())
    for adapter in (home_assistant_tts, home_assistant_mobile):
        monkeypatch.setattr(adapter, "get_home_assistant_client", lambda: client)
    monkeypatch.setattr(home_assistant_tts, "get_runtime_config", AsyncMock(side_effect=AssertionError("Config reread")))
    monkeypatch.setattr(notifications, "render_template", Mock(side_effect=AssertionError("Literal body rendered")))
    target = "media_player.synthetic" if kind == "voice" else "notify.mobile_app_synthetic"
    action = {"type": kind, "delivery_mode": "literal", "target": target, "message": BODY, "title": TITLE}
    original = deepcopy(action)
    result = await NotificationService()._deliver_literal(action, context(), config)
    assert result.delivered
    args, kwargs = client.call_service.await_args
    assert args[0] == (config.home_assistant_tts_service if kind == "voice" else target)
    assert args[1]["message"] == BODY
    if kind == "mobile":
        assert args[1]["title"] == TITLE
    assert kwargs["runtime_config"] is config
    assert action == original


@pytest.mark.asyncio
@pytest.mark.parametrize("template", [False, True])
async def test_literal_whatsapp_preserves_native_text_or_template_parameters_and_exact_ephemeral_config(monkeypatch, template):
    runtime_config = runtime()
    config = WhatsAppIntegrationConfig(True, "synthetic-ephemeral-token", "synthetic-other-phone", "", "", "",
        "v25.0", "synthetic_template", "en")
    reply = {"messages": [{"id": "synthetic-provider-message"}]}
    provider = SimpleNamespace(send_text_message=AsyncMock(return_value=reply), send_template_message=AsyncMock(return_value=reply))
    monkeypatch.setattr(notifications, "get_whatsapp_delivery_service", lambda: provider)
    monkeypatch.setattr(notifications, "render_template", Mock(side_effect=AssertionError("Literal body rendered")))
    action = {"type": "whatsapp", "delivery_mode": "whatsapp_template" if template else "literal",
        "target": "15550000001", "message": BODY, "template_name": "synthetic_template",
        "language_code": "en", "body_parameters": [BODY, "{subject}"]}
    original = deepcopy(action)
    result = await NotificationService()._deliver_literal(action, context(), runtime_config, ephemeral_config=config)
    assert result.delivered and action == original
    assert result.metadata == {"provider_message_id": "synthetic-provider-message"}
    if template:
        provider.send_text_message.assert_not_awaited()
        provider.send_template_message.assert_awaited_once_with("15550000001", template_name="synthetic_template",
            language_code="en", body_parameters=[BODY, "{subject}"], config=config)
        assert provider.send_template_message.await_args.kwargs["config"] is config
    else:
        provider.send_template_message.assert_not_awaited()
        provider.send_text_message.assert_awaited_once_with("15550000001", BODY, config=config)
        assert provider.send_text_message.await_args.kwargs["config"] is config


def test_configuration_binding_tracks_transport_credentials_and_not_unrelated_settings(monkeypatch):
    monkeypatch.setattr(notification_requests, "confirmation_token_hash", lambda value: "bound:" + value)
    config = runtime()
    plan = [{"action": {"type": "voice"}, "state": "pending"}]
    original = notification_requests.configuration_binding(config, plan)
    config.unrelated = "after"
    assert notification_requests.configuration_binding(config, plan) == original
    config.home_assistant_token = "synthetic-rotated-token"
    assert notification_requests.configuration_binding(config, plan) != original


def test_ephemeral_binding_covers_unsaved_account_and_credentials(monkeypatch):
    monkeypatch.setattr(notification_requests, "confirmation_token_hash", lambda value: "bound:" + value)
    config = WhatsAppIntegrationConfig(True, "synthetic-ephemeral-token", "synthetic-phone", "", "", "",
        "v25.0", "synthetic_template", "en")
    original = notification_requests.ephemeral_configuration_binding(config)
    for changed in (replace(config, access_token="synthetic-changed-token"),
                    replace(config, phone_number_id="synthetic-changed-phone")):
        assert notification_requests.ephemeral_configuration_binding(changed) != original


@pytest.mark.asyncio
async def test_wrong_run_identity_is_denied_before_actor_or_config_lookup(monkeypatch):
    actor = AsyncMock(side_effect=AssertionError("Wrong identity reached authority lookup"))
    monkeypatch.setattr(notification_requests, "load_active_admin", actor)
    operation = uuid.uuid4()
    result = await notification_requests.confirmed_attempt_denial(None,
        {"confirmed_delivery": {"operation_id": str(operation), "auth_version": 1}},
        uuid.uuid4(), plan=[], runtime_config=runtime())
    assert result == "confirmed_delivery_identity_mismatch"
    actor.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorized_snapshot_is_returned_unchanged_for_transport(monkeypatch):
    config = runtime()
    current = AsyncMock(return_value=config)
    denial = AsyncMock(return_value=None)
    monkeypatch.setattr(notifications, "get_runtime_config_for_session", current)
    monkeypatch.setattr(notifications, "confirmed_attempt_denial", denial)
    monkeypatch.setattr(notifications, "load_active_admin", AsyncMock())
    service = NotificationService()
    session = SimpleNamespace(scalars=AsyncMock())
    payload = {"confirmed_delivery": {"user_id": str(uuid.uuid4()), "auth_version": 1}}
    identity, plan = uuid.uuid4(), []
    actual, error = await service.authorize_confirmed_attempt(session, payload, identity, plan=plan)
    assert actual is config and error is None
    current.assert_awaited_once_with(session)
    assert denial.await_args.kwargs["runtime_config"] is config
