"""Bounded PostgreSQL contracts for notification delivery truth and rule drift."""
from test_recovery_boundaries import isolated_resources as isolated_resources

import uuid
from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.db.session import AsyncSessionLocal
from app.models import AuditLog, NotificationRule, NotificationRun, User
from app.models.enums import UserRole
from app.modules.home_assistant.client import HomeAssistantError
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError
from app.modules.notifications.home_assistant_mobile import HomeAssistantMobileAppNotifier
from app.services import action_confirmations
from app.services import notifications as notification_owner
from app.services.notification_runs import NotificationRunStore, run_summary, safe_destination_outcomes
from app.services.notifications import NotificationActionOutcome, NotificationService


pytestmark = pytest.mark.asyncio
EVENT_TYPE = "notification_dispatch_truth"
CONFIRMED_ACTION = "notification.synthetic_dispatch_truth"


@pytest_asyncio.fixture(autouse=True)
async def notification_dispatch_resources(isolated_resources, monkeypatch):
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("TRUNCATE notification_runs, notification_rules, action_confirmations, audit_logs CASCADE")
        )
        await session.commit()
    runtime = SimpleNamespace(apprise_urls="")
    monkeypatch.setattr(notification_owner, "get_runtime_config", AsyncMock(return_value=runtime))
    monkeypatch.setattr(notification_owner, "get_runtime_config_for_session", AsyncMock(return_value=runtime))
    yield
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("TRUNCATE notification_runs, notification_rules, action_confirmations, audit_logs CASCADE")
        )
        await session.commit()


def context() -> NotificationContext:
    return NotificationContext(
        event_type=EVENT_TYPE,
        subject="Synthetic notification dispatch truth",
        severity="info",
        facts={"message": "Synthetic notification dispatch truth"},
    )


def mobile_action(action_id: str, *targets: str) -> dict[str, object]:
    return {
        "id": action_id,
        "type": "mobile",
        "target_mode": "selected",
        "target_ids": [f"home_assistant_mobile:{target}" for target in targets],
        "title_template": "Synthetic delivery",
        "message_template": "Synthetic delivery body",
    }


def in_app_action(action_id: str) -> dict[str, object]:
    return {
        "id": action_id,
        "type": "in_app",
        "title_template": "Synthetic delivery",
        "message_template": "Synthetic delivery body",
    }


def rule_payload(rule_id: uuid.UUID, action: dict[str, object], *, active: bool = True) -> dict[str, object]:
    return {
        "id": str(rule_id),
        "name": f"Synthetic rule {rule_id.hex[:8]}",
        "trigger_event": EVENT_TYPE,
        "conditions": [],
        "actions": [action],
        "is_active": active,
    }


async def save_rule(rule_id: uuid.UUID, action: dict[str, object], *, active: bool = True) -> None:
    payload = rule_payload(rule_id, action, active=active)
    async with AsyncSessionLocal() as session:
        session.add(
            NotificationRule(
                id=rule_id,
                name=str(payload["name"]),
                trigger_event=EVENT_TYPE,
                conditions=[],
                actions=[action],
                is_active=active,
            )
        )
        await session.commit()


def service(monkeypatch) -> NotificationService:
    value = NotificationService(run_store=NotificationRunStore())
    for name in ("publish_planned_outcome", "publish_planned_failure", "publish_plan_completion"):
        monkeypatch.setattr(value, name, AsyncMock())
    return value


class InertMobileClient:
    def __init__(self, failed_delivery: Literal["unknown", "rejected"]) -> None:
        self.failed_delivery = failed_delivery
        self.calls: list[str] = []

    async def call_service(self, service_name, _payload, **_kwargs):
        self.calls.append(service_name)
        if service_name == "notify.mobile_app_accepted":
            return {}
        message = (
            "synthetic response lost after acceptance"
            if self.failed_delivery == "unknown"
            else "synthetic provider rejected the request"
        )
        raise HomeAssistantError(message, delivery=self.failed_delivery)


def mobile_notifier(client: InertMobileClient) -> HomeAssistantMobileAppNotifier:
    return HomeAssistantMobileAppNotifier(client)


async def prepared_saved_run(value: NotificationService) -> tuple[uuid.UUID, NotificationRun, list[dict[str, object]]]:
    identity, claimed = await value.run_store.reserve(notification_owner.notification_context_payload(context()))
    assert claimed is not None
    plan = await value.prepare_delivery_plan(claimed)
    await value.run_store.save_plan(identity, claimed.claim_token, plan)
    claimed.delivery_plan = plan
    return identity, claimed, plan


async def draft_admin() -> User:
    user = User(
        username="synthetic-dispatch-" + uuid.uuid4().hex,
        first_name="Synthetic",
        last_name="Dispatch Admin",
        full_name="Synthetic Dispatch Admin",
        password_hash="inert-unused",
        role=UserRole.ADMIN,
        is_active=True,
    )
    async with AsyncSessionLocal() as session:
        session.add(user)
        await session.commit()
    return user


async def test_mixed_home_assistant_fanout_records_accepted_and_unknown_without_reclaim(monkeypatch):
    rule_id = uuid.uuid4()
    await save_rule(
        rule_id,
        mobile_action("mixed-mobile", "notify.mobile_app_accepted", "notify.mobile_app_lost"),
    )
    client = InertMobileClient("unknown")
    monkeypatch.setattr(notification_owner, "HomeAssistantMobileAppNotifier", lambda: mobile_notifier(client))
    value = service(monkeypatch)
    captured_outcomes: list[NotificationActionOutcome] = []
    deliver_planned_action = value.deliver_planned_action

    async def capture_outcome(*args, **kwargs):
        outcome = await deliver_planned_action(*args, **kwargs)
        captured_outcomes.append(outcome)
        return outcome

    monkeypatch.setattr(value, "deliver_planned_action", capture_outcome)
    result = await value.send_notification_now_with_result(context())
    row = await value.run_store.get(uuid.UUID(result.run_id))
    action = row.delivery_plan[0]

    assert result.status == "review_required"
    assert row.status == "review_required"
    assert row.delivered_count == 1 and row.failed_count == 1
    assert action["state"] == "accepted" and action["review_required"] is True
    assert action["destination_outcomes"] == [
        {"target": "notify.mobile_app_accepted", "delivery": "accepted"},
        {"target": "notify.mobile_app_lost", "delivery": "unknown"},
    ]
    assert client.calls == ["notify.mobile_app_accepted", "notify.mobile_app_lost"]
    assert captured_outcomes[0].metadata["review_required"] is True
    assert captured_outcomes[0].metadata["delivery_uncertain"] is True
    assert await value.run_store.claim(row.id) is None
    assert not await value.dispatcher.run_once(row.id)
    assert client.calls == ["notify.mobile_app_accepted", "notify.mobile_app_lost"]


async def test_definite_mobile_rejection_is_failed_without_reclaim_or_resend(monkeypatch):
    rule_id = uuid.uuid4()
    await save_rule(rule_id, mobile_action("rejected-mobile", "notify.mobile_app_rejected"))
    client = InertMobileClient("rejected")
    monkeypatch.setattr(notification_owner, "HomeAssistantMobileAppNotifier", lambda: mobile_notifier(client))
    value = service(monkeypatch)

    result = await value.send_notification_now_with_result(context())
    row = await value.run_store.get(uuid.UUID(result.run_id))
    action = row.delivery_plan[0]

    assert result.status == "failed"
    assert row.status == "failed" and row.delivered_count == 0 and row.failed_count == 1
    assert action["state"] == "failed" and action["review_required"] is False
    assert action["destination_outcomes"] == [{"target": "notify.mobile_app_rejected", "delivery": "rejected"}]
    assert await value.run_store.claim(row.id) is None
    assert not await value.dispatcher.run_once(row.id)
    assert client.calls == ["notify.mobile_app_rejected"]


def discord_runtime(token: str) -> SimpleNamespace:
    return SimpleNamespace(
        discord_bot_token=token,
        discord_guild_allowlist=[],
        discord_channel_allowlist=["123456789012345678"],
        discord_user_allowlist=[],
        discord_role_allowlist=[],
        discord_admin_role_ids=[],
        discord_default_notification_channel_id="123456789012345678",
        discord_allow_direct_messages=False,
        discord_require_mention=True,
    )


async def test_normal_discord_send_uses_the_post_lock_authorized_runtime_snapshot(monkeypatch):
    before_lock, after_lock = discord_runtime("before-lock"), discord_runtime("after-lock")
    captured_authorization_configs: list[object] = []
    captured_send_configs: list[object] = []

    class FakeDiscordService:
        async def authorize_notification_action_in_session(self, _session, _action, *, config):
            captured_authorization_configs.append(config)
            return None

        async def send_notification_action(self, _action, _context, *, attachment_paths=None, config):
            assert attachment_paths == []
            captured_send_configs.append(config)
            return {
                "destination_outcomes": [{"target": "123456789012345678", "delivery": "accepted"}],
                "partial_failure": False,
                "failure_count": 0,
            }

    monkeypatch.setattr(notification_owner, "get_runtime_config", AsyncMock(return_value=before_lock))
    monkeypatch.setattr(
        notification_owner,
        "get_runtime_config_for_session",
        AsyncMock(side_effect=[before_lock, after_lock]),
    )
    monkeypatch.setattr(notification_owner, "get_discord_messaging_service", lambda: FakeDiscordService())
    value = service(monkeypatch)
    monkeypatch.setattr(value, "_snapshot_attachments", AsyncMock(return_value=[]))
    identity, claimed = await value.run_store.reserve(notification_owner.notification_context_payload(context()))
    assert claimed is not None
    plan = [
        {
            "rule": {"id": "synthetic-discord", "name": "Synthetic Discord", "trigger_event": EVENT_TYPE},
            "action": {
                "id": "discord-action",
                "type": "discord",
                "title": "Synthetic delivery",
                "message": "Synthetic delivery body",
                "frozen_discord_channel_ids": ["123456789012345678"],
                "frozen_discord_configuration_binding": "synthetic-binding",
            },
            "state": "pending",
        }
    ]
    await value.run_store.save_plan(identity, claimed.claim_token, plan)
    claimed.delivery_plan = plan

    assert await value.dispatcher.run_once(identity, claimed=claimed)
    row = await value.run_store.get(identity)
    assert row.status == "provider_accepted" and row.delivery_plan[0]["state"] == "accepted"
    assert len(captured_authorization_configs) == 2
    assert captured_send_configs == [captured_authorization_configs[-1]]
    assert captured_send_configs[0].bot_token == "after-lock"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("inactive", "notification_rule_inactive"),
        ("deleted", "notification_rule_deleted"),
        ("changed", "notification_rule_changed"),
    ],
)
async def test_saved_rule_changes_skip_only_the_unattempted_action(monkeypatch, change, reason):
    rule_id = uuid.uuid4()
    await save_rule(rule_id, in_app_action("saved-rule-action"))
    value = service(monkeypatch)
    deliveries: list[str] = []

    async def deliver(item, _row, _config, **_kwargs):
        deliveries.append(str(item["rule"]["id"]))
        return NotificationActionOutcome(delivered=True)

    monkeypatch.setattr(value, "deliver_planned_action", deliver)
    identity, claimed, plan = await prepared_saved_run(value)
    assert plan[0]["rule_origin"]["rule_id"] == str(rule_id)
    async with AsyncSessionLocal() as session:
        current = await session.get(NotificationRule, rule_id)
        assert current is not None
        if change == "inactive":
            current.is_active = False
        elif change == "deleted":
            await session.delete(current)
        else:
            current.actions = [in_app_action("saved-rule-action-changed")]
        await session.commit()

    assert await value.dispatcher.run_once(identity, claimed=claimed)
    row = await value.run_store.get(identity)
    action = row.delivery_plan[0]
    assert row.status == "skipped" and row.delivered_count == 0 and row.skipped_count == 1
    assert action["state"] == "skipped" and action["reason"] == reason
    assert deliveries == []


async def test_changed_saved_rule_does_not_cancel_an_unrelated_pending_action(monkeypatch):
    first_id, second_id = uuid.uuid4(), uuid.uuid4()
    await save_rule(first_id, in_app_action("first-action"))
    await save_rule(second_id, in_app_action("second-action"))
    value = service(monkeypatch)
    deliveries: list[str] = []

    async def deliver(item, _row, _config, **_kwargs):
        deliveries.append(str(item["rule"]["id"]))
        return NotificationActionOutcome(delivered=True)

    monkeypatch.setattr(value, "deliver_planned_action", deliver)
    identity, claimed, plan = await prepared_saved_run(value)
    assert {item["rule_origin"]["rule_id"] for item in plan} == {str(first_id), str(second_id)}
    async with AsyncSessionLocal() as session:
        first = await session.get(NotificationRule, first_id)
        assert first is not None
        first.is_active = False
        await session.commit()

    assert await value.dispatcher.run_once(identity, claimed=claimed)
    row = await value.run_store.get(identity)
    by_rule = {item["rule"]["id"]: item for item in row.delivery_plan}
    assert row.status == "provider_accepted"
    assert row.delivered_count == 1 and row.failed_count == 0 and row.skipped_count == 1
    assert by_rule[str(first_id)]["state"] == "skipped"
    assert by_rule[str(second_id)]["state"] == "accepted"
    assert deliveries == [str(second_id)]


async def test_confirmed_inactive_draft_rule_uses_its_explicit_authority(monkeypatch):
    value = service(monkeypatch)
    user = await draft_admin()
    draft_id = uuid.uuid4()
    draft = rule_payload(draft_id, in_app_action("confirmed-draft"), active=False)
    payload = {"rules": [draft]}
    async with AsyncSessionLocal() as session:
        approval = await action_confirmations.create_action_confirmation(
            session,
            user=user,
            action=CONFIRMED_ACTION,
            payload=payload,
        )
    async with AsyncSessionLocal() as session:
        identity, claimed = await value.reserve_confirmed_request(
            session,
            user=user,
            action=CONFIRMED_ACTION,
            payload=payload,
            confirmation_token=approval["confirmation_token"],
            context=context(),
            rules_override=[draft],
        )
        await session.commit()

    assert claimed is not None
    assert "rule_origin" not in claimed.delivery_plan[0]
    result = await value.dispatch_reserved(identity, claimed)
    row = await value.run_store.get(identity)
    assert row.context["confirmed_delivery"]["action"] == CONFIRMED_ACTION
    assert row.status == "provider_accepted" and row.delivery_plan[0]["state"] == "accepted"
    assert result.status == "sent"


async def test_destination_receipts_reject_service_prefixed_urls_and_private_suffixes():
    receipts = safe_destination_outcomes(
        [
            {"target": "apprise", "delivery": "accepted"},
            {"target": "notify.mobile_app_living_room", "delivery": "accepted"},
            {"target": "123456789012345678", "delivery": "rejected"},
            {"target": "notify.mobile_app_https://private.example/token", "delivery": "unknown"},
            {"target": "notify.mobile_app_living_room?private=token", "delivery": "unknown"},
            {"target": "１２３４５６", "delivery": "accepted"},
        ]
    )

    assert receipts == [
        {"target": "apprise", "delivery": "accepted"},
        {"target": "notify.mobile_app_living_room", "delivery": "accepted"},
        {"target": "123456789012345678", "delivery": "rejected"},
    ]
    assert "private.example" not in str(receipts)
    assert "private=token" not in str(receipts)


async def test_filtered_unknown_receipt_still_requires_review_before_any_reclaim(monkeypatch):
    value = service(monkeypatch)
    calls: list[str] = []

    async def deliver(_item, _row, _config, **_kwargs):
        calls.append("attempted")
        return NotificationActionOutcome(
            delivered=True,
            metadata={
                "accepted_any": True,
                "partial_failure": True,
                "failure_count": 1,
                "destination_outcomes": [
                    {"target": "notify.mobile_app_accepted", "delivery": "accepted"},
                    {"target": "notify.mobile_app_https://private.example/token", "delivery": "unknown"},
                ],
            },
        )

    monkeypatch.setattr(value, "deliver_planned_action", deliver)
    result = await value.send_notification_now_with_result(
        context(),
        rules_override=[rule_payload(uuid.uuid4(), in_app_action("filtered-unknown"))],
    )
    row = await value.run_store.get(uuid.UUID(result.run_id))
    action = row.delivery_plan[0]

    assert result.status == "review_required"
    assert row.status == "review_required"
    assert row.delivered_count == 1 and row.failed_count == 1
    assert row.failures == ["provider_outcome_unknown"]
    assert action["state"] == "accepted" and action["review_required"] is True
    assert action["destination_outcomes"] == [
        {"target": "notify.mobile_app_accepted", "delivery": "accepted"},
    ]
    summary_action = run_summary(row)["actions"][0]
    assert summary_action["review_required"] is True
    assert summary_action["reason"] == "provider_outcome_unknown"
    assert "private.example" not in str(action)
    assert await value.run_store.claim(row.id) is None
    assert not await value.dispatcher.run_once(row.id)
    assert calls == ["attempted"]


async def test_unknown_receipt_after_the_display_cap_still_requires_review(monkeypatch):
    value = service(monkeypatch)
    calls: list[str] = []
    receipts = [{"target": "notify.mobile_app_accepted", "delivery": "accepted"}]
    receipts.extend(
        {"target": str(100000000000000000 + index), "delivery": "rejected"}
        for index in range(99)
    )
    receipts.append({"target": "100000000000000999", "delivery": "unknown"})
    assert len(receipts) == 101

    async def deliver(_item, _row, _config, **_kwargs):
        calls.append("attempted")
        return NotificationActionOutcome(
            delivered=True,
            metadata={
                "accepted_any": True,
                "partial_failure": True,
                "failure_count": 100,
                "destination_outcomes": receipts,
            },
        )

    monkeypatch.setattr(value, "deliver_planned_action", deliver)
    result = await value.send_notification_now_with_result(
        context(),
        rules_override=[rule_payload(uuid.uuid4(), in_app_action("capped-unknown"))],
    )
    row = await value.run_store.get(uuid.UUID(result.run_id))
    action = row.delivery_plan[0]

    assert result.status == "review_required"
    assert row.status == "review_required"
    assert row.delivered_count == 1 and row.failed_count == 100
    assert row.failures == ["provider_outcome_unknown"]
    assert action["review_required"] is True and action["failure_count"] == 100
    assert len(action["destination_outcomes"]) == 100
    assert all(receipt["delivery"] != "unknown" for receipt in action["destination_outcomes"])
    assert await value.run_store.claim(row.id) is None
    assert not await value.dispatcher.run_once(row.id)
    assert calls == ["attempted"]


class CapturingMobileClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.runtime_configs: list[object] = []

    async def call_service(self, service_name, _payload, *, runtime_config=None):
        self.calls.append(service_name)
        self.runtime_configs.append(runtime_config)
        return {}


async def test_ordinary_mobile_route_uses_the_final_authorized_runtime_snapshot(monkeypatch):
    rule_id = uuid.uuid4()
    await save_rule(rule_id, mobile_action("ordinary-mobile", "notify.mobile_app_ordinary"))
    before_lock = SimpleNamespace(apprise_urls="", marker="before-lock")
    after_lock = SimpleNamespace(apprise_urls="", marker="after-lock")
    client = CapturingMobileClient()
    monkeypatch.setattr(notification_owner, "get_runtime_config", AsyncMock(return_value=before_lock))
    monkeypatch.setattr(
        notification_owner,
        "get_runtime_config_for_session",
        AsyncMock(side_effect=[before_lock, after_lock]),
    )
    monkeypatch.setattr(notification_owner, "HomeAssistantMobileAppNotifier", lambda: mobile_notifier(client))
    value = service(monkeypatch)

    result = await value.send_notification_now_with_result(context())
    row = await value.run_store.get(uuid.UUID(result.run_id))

    assert result.status == "sent" and row.status == "provider_accepted"
    assert client.calls == ["notify.mobile_app_ordinary"]
    assert client.runtime_configs == [after_lock]


async def confirmed_delivery(
    value: NotificationService,
    user: User,
    draft: dict[str, object],
) -> tuple[uuid.UUID, object]:
    payload = {"rules": [draft]}
    async with AsyncSessionLocal() as session:
        approval = await action_confirmations.create_action_confirmation(
            session,
            user=user,
            action=CONFIRMED_ACTION,
            payload=payload,
        )
    async with AsyncSessionLocal() as session:
        identity, claimed = await value.reserve_confirmed_request(
            session,
            user=user,
            action=CONFIRMED_ACTION,
            payload=payload,
            confirmation_token=approval["confirmation_token"],
            context=context(),
            rules_override=[draft],
        )
        await session.commit()
    assert claimed is not None
    return identity, claimed


async def delivery_checkpoint(identity: uuid.UUID, delivery: str) -> AuditLog:
    async with AsyncSessionLocal() as session:
        rows = list(
            (
                await session.scalars(
                    select(AuditLog)
                    .where(
                        AuditLog.target_id == str(identity),
                        AuditLog.action == "notification.delivery.checkpoint",
                    )
                    .order_by(AuditLog.timestamp, AuditLog.id)
                )
            ).all()
        )
    return next(row for row in rows if (row.metadata_ or {}).get("delivery") == delivery)


async def test_confirmed_rejected_checkpoint_audits_a_failed_delivery(monkeypatch):
    value = service(monkeypatch)
    user = await draft_admin()
    draft = rule_payload(uuid.uuid4(), in_app_action("confirmed-rejected"), active=False)

    async def reject(*_args, **_kwargs):
        raise NotificationDeliveryError("synthetic provider rejection", delivery="rejected")

    monkeypatch.setattr(value, "deliver_planned_action", reject)
    identity, claimed = await confirmed_delivery(value, user, draft)
    result = await value.dispatch_reserved(identity, claimed)
    row = await value.run_store.get(identity)
    checkpoint = await delivery_checkpoint(identity, "failed")

    assert result.status == "failed"
    assert row.status == "failed" and row.failed_count == 1
    assert row.failures == ["provider_rejected"]
    assert run_summary(row)["actions"][0]["reason"] == "provider_rejected"
    assert checkpoint.outcome == "failed" and checkpoint.level == "warning"
    assert checkpoint.metadata_["reason"] == "provider_rejected"
    assert checkpoint.metadata_["review_required"] is False


async def test_confirmed_accepted_unknown_checkpoint_audits_uncertainty(monkeypatch):
    value = service(monkeypatch)
    user = await draft_admin()
    draft = rule_payload(uuid.uuid4(), in_app_action("confirmed-unknown"), active=False)

    async def accepted_then_unknown(*_args, **_kwargs):
        return NotificationActionOutcome(
            delivered=True,
            reason="delivered_with_failures",
            metadata={
                "accepted_any": True,
                "partial_failure": True,
                "failure_count": 1,
                "destination_outcomes": [
                    {"target": "notify.mobile_app_accepted", "delivery": "accepted"},
                    {"target": "notify.mobile_app_lost", "delivery": "unknown"},
                ],
            },
        )

    monkeypatch.setattr(value, "deliver_planned_action", accepted_then_unknown)
    identity, claimed = await confirmed_delivery(value, user, draft)
    result = await value.dispatch_reserved(identity, claimed)
    row = await value.run_store.get(identity)
    checkpoint = await delivery_checkpoint(identity, "accepted")

    assert result.status == "review_required"
    assert row.status == "review_required"
    assert row.delivered_count == 1 and row.failed_count == 1
    assert row.failures == ["provider_outcome_unknown"]
    assert checkpoint.outcome == "uncertain" and checkpoint.level == "warning"
    assert checkpoint.metadata_["reason"] == "provider_outcome_unknown"
    assert checkpoint.metadata_["review_required"] is True
