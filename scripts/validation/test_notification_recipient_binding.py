"""Ordinary saved rule → confirmed settings edit → NEW durable dispatch."""
from test_integration_mutations import api as integration_api, approval, delete, delete_binding, URLS
from test_recovery_boundaries import isolated_resources as isolated_resources

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from sqlalchemy import text
from app.db.session import AsyncSessionLocal
from app.api.v1 import notifications as notification_api
from app.modules.notifications.base import NotificationContext
from app.services import notifications, settings
from app.services.notification_runs import NotificationRunStore
from app.services.notifications import NotificationService

pytestmark = pytest.mark.asyncio
EVENT = "synthetic.recipient_binding"


@pytest_asyncio.fixture
async def recipient_api(integration_api, monkeypatch):
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE notification_rules, notification_runs CASCADE"))
        await session.commit()
    await settings.update_settings({"apprise_urls": "\n".join(URLS)})
    service = NotificationService(run_store=NotificationRunStore())
    calls, contents = [], []
    class Sender:
        def __init__(self, *, urls):
            self.urls = urls.splitlines()
        async def send(self, title, body, context, *, attachments=None):
            calls.append(self.urls)
            contents.append({"targets": self.urls, "title": title, "body": body})
    monkeypatch.setattr(notifications, "AppriseNotificationSender", Sender)
    for name in ("publish_planned_outcome", "publish_planned_failure", "publish_plan_completion"):
        monkeypatch.setattr(service, name, AsyncMock())
    monkeypatch.setattr(notification_api, "get_notification_service", lambda: service)
    integration_api.app.include_router(notification_api.router, prefix="/api/v1/notifications")
    yield SimpleNamespace(api=integration_api, service=service, calls=calls, contents=contents)
    assert service.dispatcher._task is None


async def selected_action(fixture, targets):
    catalog = (await fixture.api.client.get("/api/v1/notifications/catalog")).json()
    mobile = next(item for item in catalog["integrations"] if item["id"] == "mobile")
    endpoint = next(item for item in mobile["endpoints"] if item["id"] == "apprise:1")
    action = {"id": "selected-mobile", "type": "mobile", "target_mode": "selected", "target_ids": targets,
        "title_template": "Synthetic recipient", "message_template": "Synthetic message"}
    if "apprise_revision" in endpoint:
        action["apprise_revision"] = endpoint["apprise_revision"]
    return action


async def create(fixture, action):
    payload = {"name": "Synthetic selected " + uuid.uuid4().hex[:8], "trigger_event": EVENT,
        "conditions": [], "actions": action if isinstance(action, list) else [action], "is_active": True}
    body = await approval(fixture.api, "notification_rule.create", payload)
    return await fixture.api.client.post("/api/v1/notifications/rules", json=body)


async def new_run(fixture):
    result = await fixture.service.send_notification_now_with_result(NotificationContext(EVENT, "Synthetic", "info", {}))
    return await fixture.service.run_store.get(uuid.UUID(result.run_id))


async def test_ordinary_saved_rule_cannot_substitute_c_for_selected_b_after_confirmed_delete_a(recipient_api):
    value = recipient_api
    response = await create(value, await selected_action(value, ["apprise:1"]))
    assert response.status_code == 201
    # The NEW run is created only after the settings edit; no frozen manual plan.
    rows = (await value.api.client.get("/api/v1/integrations/apprise/urls")).json()["urls"]
    body = await approval(value.api, "apprise.url.delete", delete_binding(rows[0]))
    assert (await delete(value.api, rows[0], body)).status_code == 200
    row = await new_run(value)
    assert value.calls in ([], [[URLS[1]]]), "An ordinary saved selection silently changed destination"
    if not value.calls:
        assert "recipient" in str((row.review_reason, row.skipped_reasons, row.failures))


@pytest.mark.parametrize("targets", [[], ["apprise:*"], ["apprise:999"], ["apprise:bad"]])
async def test_selected_invalid_or_empty_audience_never_becomes_broadcast(recipient_api, targets):
    value = recipient_api
    response = await create(value, await selected_action(value, targets))
    if response.status_code in (400, 409, 422):
        assert value.calls == []
        return
    assert response.status_code == 201
    row = await new_run(value)
    assert not value.calls, "An invalid selected audience widened into a send"
    assert "recipient" in str((row.review_reason, row.skipped_reasons, row.failures))


async def test_current_selected_b_remains_supported(recipient_api):
    value = recipient_api
    action = await selected_action(value, ["apprise:1"])
    assert (await create(value, action)).status_code == 201
    await new_run(value)
    assert value.calls == [[URLS[1]]]


async def bulk_urls(value, urls):
    payload = {"values": {"apprise_urls": "\n".join(urls)}}
    body = await approval(value.api, "settings.update", payload)
    result = await value.api.client.patch("/api/v1/settings", json=body)
    assert result.status_code == 200 and result.json()["saved"] is True


@pytest.mark.parametrize("edit", ["reorder", "prepend", "remove-selected", "credential-change"])
async def test_confirmed_bulk_edits_hold_selected_recipient_without_substitution(recipient_api, edit):
    value = recipient_api
    assert (await create(value, await selected_action(value, ["apprise:1"]))).status_code == 201
    urls = {"reorder": [URLS[2], URLS[0], URLS[1]], "prepend": ["json://synthetic.invalid/d", *URLS],
        "remove-selected": [URLS[0], URLS[2]], "credential-change": [URLS[0], URLS[1] + "?token=synthetic-changed", URLS[2]]}[edit]
    await bulk_urls(value, urls)
    row = await new_run(value)
    assert value.calls == [] and row.status == "review_required"
    assert row.review_reason == "recipient_binding_stale"
    assert await value.service.run_store.claim(row.id) is None
    assert not await value.service.dispatcher.run_once(row.id)


async def test_stale_form_is_rejected_and_name_only_edit_does_not_rebind_old_selection(recipient_api):
    value = recipient_api
    action = await selected_action(value, ["apprise:1"])
    saved = await create(value, action)
    assert saved.status_code == 201
    await bulk_urls(value, [URLS[1], URLS[2]])
    payload = {"actions": [action]}
    body = await approval(value.api, "notification_rule.update", payload)
    result = await value.api.client.patch("/api/v1/notifications/rules/" + saved.json()["id"], json=body)
    assert result.status_code == 409
    body = await approval(value.api, "notification_rule.update", {"name": "Renamed"})
    result = await value.api.client.patch("/api/v1/notifications/rules/" + saved.json()["id"], json=body)
    assert result.status_code == 200 and result.json()["actions"][0]["apprise_revision"] == action["apprise_revision"]
    assert (await new_run(value)).status == "review_required" and not value.calls


@pytest.mark.parametrize("mode", ["all", "many"])
async def test_explicit_all_and_multiple_current_recipients_remain_supported(recipient_api, mode):
    value = recipient_api
    action = await selected_action(value, ["apprise:0", "apprise:2"])
    action["target_mode"] = mode
    if mode == "all":
        action["target_ids"] = []
    assert (await create(value, action)).status_code == 201
    row = await new_run(value)
    assert row.status == "provider_accepted"
    assert value.calls == [URLS if mode == "all" else [URLS[0], URLS[2]]]


async def test_duplicate_occurrences_keep_distinct_visibility_and_receipt_identity(recipient_api):
    value = recipient_api
    await bulk_urls(value, [URLS[0], URLS[0], URLS[2]])
    action = await selected_action(value, ["apprise:0", "apprise:1"])
    action.update(message_template="Hello @FirstName", variable_recipients={"message_template": [
        {"occurrence": 0, "name": "FirstName", "target_ids": ["apprise:1"]}]})
    assert (await create(value, action)).status_code == 201
    result = await value.service.send_notification_now_with_result(NotificationContext(EVENT, "Synthetic", "info", {"first_name": "PRIVATE"}))
    row = await value.service.run_store.get(uuid.UUID(result.run_id))
    assert value.calls == [[URLS[0]], [URLS[0]]]
    assert "PRIVATE" not in value.contents[0]["body"] and "PRIVATE" in value.contents[1]["body"]
    assert row.delivery_plan[0]["destination_outcomes"] == [
        {"target": "apprise:0", "delivery": "accepted"}, {"target": "apprise:1", "delivery": "accepted"}]


async def test_all_with_positional_visibility_is_held_after_configuration_change(recipient_api):
    value = recipient_api
    action = await selected_action(value, [])
    action.update(target_mode="all", message_template="Hello @FirstName", variable_recipients={"message_template": [
        {"occurrence": 0, "name": "FirstName", "target_ids": ["apprise:1"]}]})
    assert (await create(value, action)).status_code == 201
    await bulk_urls(value, [URLS[1], URLS[2]])
    row = await new_run(value)
    assert row.status == "review_required" and not value.calls


async def test_partial_acceptance_survives_stale_pending_action_and_never_replays(recipient_api, monkeypatch):
    import asyncio
    value = recipient_api
    first, second = await selected_action(value, ["apprise:1"]), await selected_action(value, ["apprise:2"])
    second["id"] = "second-mobile"
    assert (await create(value, [first, second])).status_code == 201
    entered, release = asyncio.Event(), asyncio.Event()
    original = notifications.AppriseNotificationSender
    class PausedSender(original):
        async def send(self, *args, **kwargs):
            entered.set()
            await asyncio.wait_for(release.wait(), 8)
            return await super().send(*args, **kwargs)
    monkeypatch.setattr(notifications, "AppriseNotificationSender", PausedSender)
    task = asyncio.create_task(new_run(value))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        await bulk_urls(value, [URLS[1], URLS[2]])
    finally:
        release.set()
    row = await task
    assert value.calls == [[URLS[1]]] and row.status == "review_required" and row.delivered_count == 1
    assert [item["state"] for item in row.delivery_plan] == ["accepted", "pending"]
    assert row.delivery_plan[0]["destination_outcomes"] == [{"target": "apprise", "delivery": "accepted"}]
    history = await value.api.client.get("/api/v1/notifications/runs/" + str(row.id))
    assert history.status_code == 200 and history.json()["status"] == "review_required"
    assert not await value.service.dispatcher.run_once(row.id)
    assert value.calls == [[URLS[1]]]


async def test_notification_rule_crud_endpoints_use_db_workflow_shape(recipient_api):
    value = recipient_api
    saved = await create(value, await selected_action(value, ["apprise:1"]))
    assert saved.status_code == 201
    identity = saved.json()["id"]
    assert (await value.api.client.get("/api/v1/notifications/rules/" + identity)).json()["actions"][0]["target_ids"] == ["apprise:1"]
    assert [row["id"] for row in (await value.api.client.get("/api/v1/notifications/rules")).json()] == [identity]
    body = await approval(value.api, "notification_rule.update", {"name": "Updated", "is_active": False})
    result = await value.api.client.patch("/api/v1/notifications/rules/" + identity, json=body)
    assert result.status_code == 200 and not result.json()["is_active"] and result.json()["name"] == "Updated"
    body = await approval(value.api, "notification_rule.delete", {"rule_id": identity})
    assert (await value.api.client.request("DELETE", "/api/v1/notifications/rules/" + identity, json=body)).status_code == 204
    assert (await value.api.client.get("/api/v1/notifications/rules/" + identity)).status_code == 404


async def test_approval_binds_the_exact_recipient_revision(recipient_api):
    value = recipient_api
    action = await selected_action(value, ["apprise:1"])
    payload = {"name": "Synthetic revision approval", "trigger_event": EVENT, "conditions": [], "actions": [action], "is_active": True}
    body = await approval(value.api, "notification_rule.create", payload)
    body["actions"][0]["apprise_revision"] = "f" * 64
    result = await value.api.client.post("/api/v1/notifications/rules", json=body)
    assert result.status_code == 403 and not value.calls
    assert (await value.api.client.get("/api/v1/notifications/rules")).json() == []


async def test_legacy_positional_rule_without_binding_cannot_be_activated_or_dispatched(recipient_api):
    from sqlalchemy import select
    from app.models import NotificationRule
    from app.services import notification_rules
    from app.services.mutation_context import MutationError
    value = recipient_api
    action = {"id": "legacy-mobile", "type": "mobile", "target_mode": "selected", "target_ids": ["apprise:1"],
        "title_template": "Synthetic", "message_template": "Synthetic"}
    async with AsyncSessionLocal() as session:
        rule = NotificationRule(name="Legacy ambiguous", trigger_event=EVENT, conditions=[], actions=[action], is_active=False)
        session.add(rule)
        await session.commit()
        identity = rule.id
    async with AsyncSessionLocal() as session:
        with pytest.raises(MutationError, match="recipient_binding_missing"):
            await notification_rules.set_automation_activation(session, reference={"notification_rule_id": str(identity)}, active=True)
        await session.rollback()
    async with AsyncSessionLocal() as session:
        assert not (await session.get(NotificationRule, identity)).is_active
    assert not value.calls


async def test_empty_selected_voice_does_not_fall_back_to_default_speaker(recipient_api, monkeypatch):
    value = recipient_api
    await settings.update_settings({"home_assistant_url": "http://synthetic.invalid", "home_assistant_token": "synthetic-inert-token",
        "home_assistant_default_media_player": "media_player.synthetic_default"})
    announce = AsyncMock()
    monkeypatch.setattr(notifications, "HomeAssistantTtsAnnouncer", lambda: SimpleNamespace(announce=announce))
    monkeypatch.setattr(notifications, "HomeAssistantClient", lambda: SimpleNamespace(get_state=AsyncMock(return_value=SimpleNamespace(state="on"))))
    action = {"id": "empty-voice", "type": "voice", "target_mode": "selected", "target_ids": [],
        "title_template": "", "message_template": "Synthetic voice"}
    response = await create(value, action)
    if response.status_code in (400, 409, 422):
        announce.assert_not_awaited()
        return
    assert response.status_code == 201
    await new_run(value)
    announce.assert_not_awaited()
