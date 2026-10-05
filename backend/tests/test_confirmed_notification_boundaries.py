"""Database-free native-body and authoritative-config notification boundaries."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import uuid

import pytest

from app.modules.announcements import home_assistant_tts
from app.modules.notifications import home_assistant_mobile
from app.modules.notifications.base import NotificationContext
from app.services import notification_requests, notifications
from app.services.notifications import NotificationService


BODY = "  Literal {registration_number} {{message}}\n£12 & <tag> — keep punctuation.  "
TITLE = "Literal {subject}"


def runtime():
    return SimpleNamespace(home_assistant_url="http://synthetic.invalid", home_assistant_token="synthetic-ha-token",
        home_assistant_tts_service="tts.synthetic_say", home_assistant_default_media_player="media_player.synthetic", apprise_urls="",
        unrelated="before")


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


def test_configuration_binding_tracks_transport_credentials_and_not_unrelated_settings(monkeypatch):
    monkeypatch.setattr(notification_requests, "confirmation_token_hash", lambda value: "bound:" + value)
    config = runtime()
    plan = [{"action": {"type": "voice"}, "state": "pending"}]
    original = notification_requests.configuration_binding(config, plan)
    config.unrelated = "after"
    assert notification_requests.configuration_binding(config, plan) == original
    config.home_assistant_token = "synthetic-rotated-token"
    assert notification_requests.configuration_binding(config, plan) != original


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


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", [None, {}, {"rule_id": "unknown", "operation_id": "unknown"}])
async def test_unrecognized_automation_notification_origin_is_denied_without_queries(origin):
    from app.services.automation_authorization import notification_origin_denial
    session = SimpleNamespace(scalar=AsyncMock(side_effect=AssertionError("Unknown origin reached database")))
    denial = await notification_origin_denial(session, {"automation_origin": origin}, uuid.uuid4(),
        authorize_recognition=AsyncMock(side_effect=AssertionError("Unknown origin acquired recognition authority")))
    assert denial == "unsupported_automation_notification_origin"
    session.scalar.assert_not_called()
