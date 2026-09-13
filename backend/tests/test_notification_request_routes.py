"""HTTP adapters retain the real confirmation/commit/dispatch helper.

Only the durable service and database transport are inert here. Real approval,
row-lock, recovery and rollback behavior belongs to the isolated PostgreSQL suite.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.dependencies import current_user
from app.api.v1 import integrations, notifications as notification_api
from app.db.session import get_db_session
from app.models import NotificationRule, User
from app.models.enums import UserRole
from app.modules.notifications.base import ComposedNotification
from app.services import notifications
from app.services.action_confirmations import ActionConfirmationError
from app.services.mutation_context import MutationError
from app.services.notifications import NotificationWorkflowResult


pytestmark = pytest.mark.asyncio
BODY = "  Literal @RegistrationNumber {message}\n£12 & <tag> — keep punctuation.  "
TOKEN = "synthetic-confirmation-token"


@pytest.fixture
def boundary(monkeypatch):
    """Record ordering without replacing send_confirmed_notification itself."""
    trace = []
    identity = uuid.uuid4()
    claimed = object()
    user = User(id=uuid.uuid4(), username="synthetic-admin", full_name="Synthetic Admin",
                role=UserRole.ADMIN, is_active=True, auth_session_version=4, password_hash="inert")
    result = NotificationWorkflowResult(
        notification=ComposedNotification("Rendered title", BODY),
        run_id=str(identity), recovery_status="provider_accepted", delivered_count=1,
    )
    state = SimpleNamespace(trace=trace, identity=identity, claimed=claimed, user=user,
                            result=result, reserve_error=None, commit_error=None, dispatch_error=None)

    async def reserve(session, **kwargs):
        trace.append("reserve")
        assert session is state.session
        if state.reserve_error is not None:
            raise state.reserve_error
        return identity, claimed

    async def commit():
        trace.append("commit")
        if state.commit_error is not None:
            raise state.commit_error

    async def rollback():
        trace.append("rollback")

    async def dispatch(run_id, reserved_claim, *, ephemeral_config=None):
        trace.append("dispatch")
        assert trace == ["reserve", "commit", "dispatch"]
        assert run_id == identity and reserved_claim is claimed
        assert ephemeral_config is None
        if state.dispatch_error is not None:
            raise state.dispatch_error
        return state.result

    state.session = SimpleNamespace(commit=AsyncMock(side_effect=commit), rollback=AsyncMock(side_effect=rollback),
                                    get=AsyncMock(return_value=None))
    state.service = SimpleNamespace(
        reserve_confirmed_request=AsyncMock(side_effect=reserve),
        dispatch_reserved=AsyncMock(side_effect=dispatch),
        preview_rule=AsyncMock(return_value={"actions": [{"title": "Rendered title", "message": BODY}]}),
    )
    monkeypatch.setattr(notifications, "get_notification_service", lambda: state.service)
    monkeypatch.setattr(notification_api, "get_notification_service", lambda: state.service)
    monkeypatch.setattr(integrations, "get_notification_service", lambda: state.service)
    monkeypatch.setattr(integrations, "is_maintenance_mode_active", AsyncMock(return_value=False))
    state.config = SimpleNamespace(home_assistant_default_media_player="media_player.default_synthetic",
                                   apprise_urls="json://synthetic.invalid")
    monkeypatch.setattr(integrations, "get_runtime_config", AsyncMock(return_value=state.config))
    app = FastAPI()
    app.include_router(integrations.router, prefix="/api/v1/integrations")
    app.include_router(notification_api.router, prefix="/api/v1/notifications")
    app.dependency_overrides[current_user] = lambda: state.user
    app.dependency_overrides[get_db_session] = lambda: state.session
    state.app = app
    return state


async def post(state, path, body):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=state.app), base_url="http://test") as client:
        return await client.post(path, json=body)


@pytest.mark.parametrize("selected", [None, "media_player.selected_synthetic"])
async def test_announcement_reserves_exact_literal_body_and_target_before_dispatch(boundary, selected):
    body = {"message": BODY, "confirmation_token": TOKEN}
    if selected is not None:
        body["entity_id"] = selected
    response = await post(boundary, "/api/v1/integrations/announcements/say", body)
    target = selected or boundary.config.home_assistant_default_media_player
    assert response.status_code == 200
    assert response.json() == {"status": "sent", "entity_id": target,
                               "notification_run_id": str(boundary.identity)}
    call = boundary.service.reserve_confirmed_request.await_args.kwargs
    assert call["user"] is boundary.user
    assert call["action"] == "announcement.say" and call["confirmation_token"] == TOKEN
    assert call["payload"] == {key: value for key, value in body.items() if key != "confirmation_token"}
    assert call["direct_action"] == {"type": "voice", "delivery_mode": "literal", "target": target,
                                      "title": "Announcement", "message": BODY,
                                      "configured_default": selected is None}
    assert call["context"].facts == {"message": BODY}
    assert boundary.trace == ["reserve", "commit", "dispatch"]
    boundary.session.rollback.assert_not_awaited()


@pytest.mark.parametrize("person_name,expected_name", [("  Synthetic Person  ", "Synthetic Person"), ("  ", "this person")])
async def test_mobile_test_reserves_selected_native_service_and_exact_body(boundary, person_name, expected_name):
    target = "notify.mobile_app_selected_synthetic"
    body = {"service_name": target, "person_name": person_name, "confirmation_token": TOKEN}
    response = await post(boundary, "/api/v1/integrations/home-assistant/mobile-notifications/test", body)
    assert response.status_code == 200
    assert response.json() == {"status": "sent", "service_name": target,
                               "notification_run_id": str(boundary.identity)}
    call = boundary.service.reserve_confirmed_request.await_args.kwargs
    assert call["action"] == "notification.mobile_test"
    assert call["confirmation_token"] == TOKEN
    assert call["payload"] == {"service_name": target, "person_name": person_name}
    assert call["direct_action"] == {"type": "mobile", "delivery_mode": "literal", "target": target,
                                      "title": "IACS Home Assistant test",
                                      "message": f"Mobile notifications are linked for {expected_name}."}
    assert boundary.trace == ["reserve", "commit", "dispatch"]


async def test_general_notification_test_projects_the_same_durable_result(boundary):
    response = await post(boundary, "/api/v1/integrations/notifications/test",
                          {"subject": "Synthetic test", "severity": "warning", "message": BODY,
                           "confirmation_token": TOKEN})
    assert response.status_code == 200
    assert response.json() == {"status": "sent", "delivery_status": "sent",
                               "notification_run_id": str(boundary.identity), "title": "Rendered title", "body": BODY}
    call = boundary.service.reserve_confirmed_request.await_args.kwargs
    assert call["action"] == "notification.test"
    assert call["payload"] == {"subject": "Synthetic test", "severity": "warning", "message": BODY}
    assert call["direct_action"] is None and call["rules_override"] is None
    assert call["context"].subject == "Synthetic test" and call["context"].facts == {"message": BODY}
    assert boundary.trace == ["reserve", "commit", "dispatch"]


@pytest.mark.parametrize("stored", [False, True])
async def test_rule_tests_reserve_selected_workflow_and_return_its_run(boundary, stored):
    rule_id = uuid.uuid4()
    rule = {"id": str(rule_id), "name": "Synthetic workflow", "trigger_event": "authorized_entry",
            "conditions": [], "actions": [{"type": "mobile", "target_mode": "selected",
                "target_ids": ["notify.mobile_app_selected_synthetic"],
                "title_template": "@Subject", "message_template": "@Message"}], "is_active": True}
    body = {"confirmation_token": TOKEN}
    if stored:
        now = datetime(2026, 9, 13, tzinfo=UTC)
        boundary.session.get.return_value = NotificationRule(
            **{**rule, "id": rule_id}, created_at=now, updated_at=now,
        )
        path = f"/api/v1/notifications/rules/{rule_id}/test"
    else:
        body["rule"] = rule
        body["context"] = {"event_type": "authorized_entry", "subject": "Synthetic arrival",
                           "severity": "info", "facts": {"message": BODY}}
        path = "/api/v1/notifications/rules/test"
    response = await post(boundary, path, body)
    assert response.status_code == 200
    assert response.json() == {"status": "sent", "delivery_status": "sent",
                               "notification_run_id": str(boundary.identity), "title": "Rendered title", "body": BODY,
                               "preview": boundary.service.preview_rule.return_value}
    call = boundary.service.reserve_confirmed_request.await_args.kwargs
    assert call["action"] == "notification_rule.test" and call["confirmation_token"] == TOKEN
    assert call["direct_action"] is None
    assert call["payload"] == ({"rule_id": str(rule_id)} if stored else {"rule": rule, "context": body["context"]})
    selected = call["rules_override"][0]
    assert selected["id"] == str(rule_id)
    assert selected["actions"][0]["target_ids"] == ["notify.mobile_app_selected_synthetic"]
    boundary.service.preview_rule.assert_awaited_once_with(selected, call["context"])
    assert boundary.trace == ["reserve", "commit", "dispatch"]


@pytest.mark.parametrize("outcome", ["failed", "review_required", "queued", "processing", "skipped", "partial"])
async def test_non_success_is_not_reported_as_sent_and_keeps_the_committed_run_id(boundary, outcome):
    boundary.result.delivered_count = int(outcome == "partial")
    boundary.result.failed_count = int(outcome in {"failed", "partial"})
    boundary.result.recovery_status = "provider_accepted" if outcome == "partial" else outcome
    boundary.result.failures = ["Synthetic provider rejection"] if outcome == "failed" else []
    boundary.result.skipped_reasons = ["confirmed_actor_no_longer_authorized"] if outcome == "skipped" else []
    response = await post(boundary, "/api/v1/integrations/announcements/say",
                          {"message": BODY, "confirmation_token": TOKEN})
    assert response.status_code == 503
    assert response.headers["X-IACS-Notification-Run-ID"] == str(boundary.identity)
    detail = response.json()["detail"]
    if outcome == "failed":
        assert detail == "Synthetic provider rejection"
    elif outcome == "skipped":
        assert detail == "confirmed_actor_no_longer_authorized"
    else:
        assert "Inspect its delivery record before sending again" in detail
    assert boundary.trace == ["reserve", "commit", "dispatch"]
    boundary.service.dispatch_reserved.assert_awaited_once_with(boundary.identity, boundary.claimed, ephemeral_config=None)
    boundary.session.rollback.assert_not_awaited()


@pytest.mark.parametrize("error,expected", [
    (ActionConfirmationError("Fresh confirmation required", status_code=409), 409),
    (MutationError("actor_changed", "Actor is no longer authorized"), 403),
])
async def test_rejected_confirmation_or_authority_rolls_back_and_never_dispatches(boundary, error, expected):
    boundary.reserve_error = error
    response = await post(boundary, "/api/v1/integrations/announcements/say",
                          {"message": BODY, "confirmation_token": TOKEN})
    assert response.status_code == expected and response.json() == {"detail": str(error)}
    assert "X-IACS-Notification-Run-ID" not in response.headers
    assert boundary.trace == ["reserve", "rollback"]
    boundary.session.commit.assert_not_awaited()
    boundary.service.dispatch_reserved.assert_not_awaited()


async def test_failed_commit_cannot_reach_dispatch(boundary):
    boundary.commit_error = RuntimeError("Synthetic database commit failure")
    with pytest.raises(RuntimeError, match="Synthetic database commit failure"):
        await post(boundary, "/api/v1/integrations/announcements/say", {"message": BODY, "confirmation_token": TOKEN})
    assert boundary.trace[:2] == ["reserve", "commit"]
    boundary.session.commit.assert_awaited_once()
    boundary.service.dispatch_reserved.assert_not_awaited()


async def test_dispatch_interruption_does_not_repeat_or_roll_back_accepted_work(boundary):
    boundary.dispatch_error = RuntimeError("Synthetic dispatcher checkpoint unavailable")
    with pytest.raises(RuntimeError, match="Synthetic dispatcher checkpoint unavailable"):
        await post(boundary, "/api/v1/integrations/announcements/say", {"message": BODY, "confirmation_token": TOKEN})
    assert boundary.trace == ["reserve", "commit", "dispatch"]
    boundary.service.dispatch_reserved.assert_awaited_once()
    boundary.session.rollback.assert_not_awaited()


@pytest.mark.parametrize("path,body", [
    ("/api/v1/integrations/announcements/say", {"message": BODY}),
    ("/api/v1/integrations/home-assistant/mobile-notifications/test", {"service_name": "notify.mobile_app_synthetic"}),
    ("/api/v1/integrations/notifications/test", {"message": BODY}),
    ("/api/v1/notifications/rules/test", {"rule": {}}),
])
async def test_standard_user_cannot_reserve_any_notification_test(boundary, path, body):
    boundary.user.role = UserRole.STANDARD
    response = await post(boundary, path, {**body, "confirmation_token": TOKEN})
    assert response.status_code == 403 and boundary.trace == []
    boundary.service.reserve_confirmed_request.assert_not_awaited()


async def test_maintenance_blocks_announcement_before_reservation(boundary, monkeypatch):
    monkeypatch.setattr(integrations, "is_maintenance_mode_active", AsyncMock(return_value=True))
    response = await post(boundary, "/api/v1/integrations/announcements/say", {"message": BODY, "confirmation_token": TOKEN})
    assert response.status_code == 423 and boundary.trace == []
    boundary.service.reserve_confirmed_request.assert_not_awaited()


async def test_missing_default_announcement_target_does_not_consume_confirmation(boundary):
    boundary.config.home_assistant_default_media_player = ""
    response = await post(boundary, "/api/v1/integrations/announcements/say", {"message": BODY, "confirmation_token": TOKEN})
    assert response.status_code == 400 and boundary.trace == []
    boundary.service.reserve_confirmed_request.assert_not_awaited()
