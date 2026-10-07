"""Confirmed notification intake/recovery on guarded disposable PostgreSQL.

Actual confirmation, authority, audit, prepared journal and dispatch owners run.
Only provider transports and optional publication are inert; no worker starts.
The imported guard refuses collection before application imports elsewhere.
"""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text, update

from app.api.confirmations import send_confirmed_notification
from app.api.v1 import integrations as integrations_api
from app.db.session import AsyncSessionLocal
from app.models import ActionConfirmation, AuditLog, MaintenanceModeState, NotificationRun, SystemSetting, User
from app.models.enums import UserRole
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError
from app.services import action_confirmations, notifications, settings
from app.services.mutation_context import MutationError
from app.services.notification_runs import MAX_DISPATCH_AGE_SECONDS, NotificationRunStore
from app.services.notifications import NotificationService

pytestmark = pytest.mark.asyncio
ACTION = "notification.synthetic_confirmed"
BODY = "  Literal {registration_number} {{message}}\n£12 & <tag> — unchanged.  "
WAIT_SECONDS = 8


async def bounded(awaitable):
    return await asyncio.wait_for(awaitable, WAIT_SECONDS)


def context():
    return NotificationContext("integration_test", "Synthetic confirmed message", "info",
        {"registration_number": "MUSTNOTRENDER"})


def direct(kind="voice", *, body=BODY):
    return {"type": kind, "delivery_mode": "literal", "message": body, "title": "Literal {subject}",
        "target": "media_player.synthetic" if kind == "voice" else "notify.mobile_app_synthetic"}


async def set_default_media_player(target):
    key = "home_assistant_default_media_player"
    async with AsyncSessionLocal() as session:
        setting = await session.get(SystemSetting, key)
        if setting is None:
            category, _, description = settings.DEFAULT_DYNAMIC_SETTINGS[key]
            setting = SystemSetting(key=key, category=category, description=description, is_secret=False)
            session.add(setting)
        setting.value = settings.setting_payload(key, target)
        await session.commit()


async def announcement_confirmation(user, *, entity_id=None):
    payload = {"message": BODY}
    if entity_id is not None:
        payload["entity_id"] = entity_id
    async with AsyncSessionLocal() as session:
        approval = await action_confirmations.create_action_confirmation(
            session, user=user, action="announcement.say", payload=payload,
        )
    return approval, payload


def announcement_action(target, *, configured_default):
    return {"type": "voice", "delivery_mode": "literal", "target": target,
            "title": "Announcement", "message": BODY, "configured_default": configured_default}


async def announce_via_route(user, confirmation_token, *, entity_id=None):
    request = integrations_api.AnnouncementRequest(
        message=BODY, entity_id=entity_id, confirmation_token=confirmation_token,
    )
    async with AsyncSessionLocal() as session:
        return await integrations_api.say_announcement(request, user=user, session=session)




@pytest_asyncio.fixture(autouse=True)
async def confirmed_resources(isolated_resources, monkeypatch):
    async def clear():
        async with AsyncSessionLocal() as session:
            await session.execute(text("TRUNCATE system_settings, action_confirmations, notification_runs, maintenance_mode_state CASCADE"))
            await session.commit()
        settings.invalidate_runtime_config_cache()

    await clear()
    async with AsyncSessionLocal() as session:
        for key, value in {
            "home_assistant_url": "http://synthetic.invalid", "home_assistant_token": "synthetic-saved-ha-secret",
            "home_assistant_tts_service": "tts.synthetic_say",
        }.items():
            category, _, description = settings.DEFAULT_DYNAMIC_SETTINGS[key]
            session.add(SystemSetting(key=key, category=category, description=description,
                value=settings.setting_payload(key, value), is_secret=key in settings.SECRET_KEYS))
        await session.commit()
    monkeypatch.setattr(action_confirmations, "emit_audit_log", lambda **kwargs: None)
    yield
    await clear()


@pytest.fixture
def delivery(monkeypatch):
    service = NotificationService(run_store=NotificationRunStore())
    providers = SimpleNamespace(announce=AsyncMock(), mobile=AsyncMock())
    monkeypatch.setattr(notifications, "HomeAssistantTtsAnnouncer", lambda: SimpleNamespace(announce=providers.announce))
    monkeypatch.setattr(notifications, "HomeAssistantMobileAppNotifier", lambda: SimpleNamespace(send=providers.mobile))
    for name in ("publish_planned_outcome", "publish_planned_failure", "publish_plan_completion"):
        monkeypatch.setattr(service, name, AsyncMock())
    yield service, providers
    assert service.dispatcher._task is None, "These contracts must not start a dispatcher worker"


async def actor():
    async with AsyncSessionLocal() as session:
        user = User(username="synthetic-notify-" + uuid.uuid4().hex,
            first_name="Synthetic", last_name="Notification Admin", full_name="Synthetic Notification Admin",
            password_hash="inert-unused", role=UserRole.ADMIN, is_active=True)
        session.add(user)
        await session.commit()
    return user


async def confirmation(user, action):
    payload = {"action": deepcopy(action)}
    async with AsyncSessionLocal() as session:
        approval = await action_confirmations.create_action_confirmation(session, user=user, action=ACTION, payload=payload)
    return approval, payload


async def reserve(service, user, approval, payload, action):
    async with AsyncSessionLocal() as session:
        identity, claimed = await service.reserve_confirmed_request(session, user=user, action=ACTION,
            payload=payload, confirmation_token=approval["confirmation_token"], context=context(),
            direct_action=action)
        await session.commit()
    return identity, claimed


async def accepted(service, *, kind="voice"):
    user, action = await actor(), direct(kind)
    approval, payload = await confirmation(user, action)
    identity, claimed = await reserve(service, user, approval, payload, action)
    return SimpleNamespace(user=user, action=action, approval=approval, payload=payload, identity=identity, claimed=claimed)


async def snapshot():
    async with AsyncSessionLocal() as session:
        return {model.__tablename__: [dict(row) for row in (await session.execute(
            select(model.__table__).order_by(model.id if hasattr(model, "id") else model.key))).mappings()]
            for model in (ActionConfirmation, AuditLog, NotificationRun, SystemSetting)}


async def expire(identity):
    async with AsyncSessionLocal() as session:
        await session.execute(update(NotificationRun).where(NotificationRun.id == identity)
            .values(lease_expires_at=text("clock_timestamp() - interval '1 second'")))
        await session.commit()


async def test_confirmation_consumption_request_audit_and_prepared_claim_share_the_callers_transaction(delivery):
    service, providers = delivery
    user, action = await actor(), direct()
    approval, payload = await confirmation(user, action)
    before = await snapshot()
    async with AsyncSessionLocal() as session:
        identity, claim = await service.reserve_confirmed_request(session, user=user, action=ACTION, payload=payload,
            confirmation_token=approval["confirmation_token"], context=context(), direct_action=action)
        assert claim is not None and claim.status == "processing"
        assert identity == uuid.uuid5(uuid.UUID(approval["confirmation_id"]), "notification-delivery")
        assert await snapshot() == before, "Uncommitted intake leaked a confirmation, audit or run"
        await session.commit()
    persisted = await snapshot()
    assert len(persisted["notification_runs"]) == 1
    actions = [row["action"] for row in persisted["audit_logs"]]
    assert actions.count("real_world_action.confirmation.consumed") == 1
    assert actions.count(ACTION + ".requested") == 1
    row = await service.run_store.get(identity)
    assert row.delivery_plan[0]["state"] == "pending" and row.claim_count == 1
    assert row.context["confirmed_delivery"]["operation_id"] == approval["confirmation_id"]
    providers.announce.assert_not_awaited()


@pytest.mark.parametrize("stage", ["consumption_audit", "request_audit", "prepared_run", "outer_rollback"])
async def test_any_intake_failure_rolls_back_confirmation_request_audit_and_run(monkeypatch, delivery, stage):
    service, providers = delivery
    user, action = await actor(), direct()
    approval, payload = await confirmation(user, action)
    before = await snapshot()
    if stage in {"consumption_audit", "request_audit"}:
        module = action_confirmations if stage == "consumption_audit" else notifications
        original = module.write_audit_log
        async def fail_audit(session, **kwargs):
            await original(session, **kwargs)
            await session.flush()
            raise RuntimeError("Synthetic mandatory intake failure")
        monkeypatch.setattr(module, "write_audit_log", fail_audit)
    elif stage == "prepared_run":
        original = service.run_store.reserve_prepared_in_session
        async def fail_run(session, *args, **kwargs):
            await original(session, *args, **kwargs)
            await session.flush()
            raise RuntimeError("Synthetic mandatory intake failure")
        monkeypatch.setattr(service.run_store, "reserve_prepared_in_session", fail_run)
    with pytest.raises(RuntimeError, match="mandatory intake failure"):
        async with AsyncSessionLocal() as session:
            await service.reserve_confirmed_request(session, user=user, action=ACTION, payload=payload,
                confirmation_token=approval["confirmation_token"], context=context(), direct_action=action)
            if stage == "outer_rollback":
                raise RuntimeError("Synthetic mandatory intake failure")
            await session.commit()
    assert await snapshot() == before
    providers.announce.assert_not_awaited()


async def test_concurrent_same_token_reserves_exactly_one_run_and_one_request_audit(delivery):
    service, _ = delivery
    user, action = await actor(), direct()
    approval, payload = await confirmation(user, action)
    start = asyncio.Event()
    async def caller():
        await start.wait()
        return await reserve(service, user, approval, payload, action)
    tasks = [asyncio.create_task(caller()) for _ in range(3)]
    try:
        start.set()
        results = await bounded(asyncio.gather(*tasks, return_exceptions=True))
    finally:
        for task in tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert sum(isinstance(result, tuple) for result in results) == 1
    assert sum(isinstance(result, action_confirmations.ActionConfirmationError) for result in results) == 2
    rows = await snapshot()
    assert len(rows["notification_runs"]) == 1
    assert sum(row["action"] == ACTION + ".requested" for row in rows["audit_logs"]) == 1


async def test_duplicate_stable_origin_retains_claim_plan_timestamps_and_audit(delivery):
    service, _ = delivery
    data = await accepted(service)
    before = await snapshot()
    async with AsyncSessionLocal() as session:
        identity, claim = await service.reserve_confirmed_in_session(session, actor_user_id=data.user.id,
            auth_version=data.user.auth_session_version, operation_id=data.approval["confirmation_id"],
            action=ACTION, context=context(), direct_action=data.action)
        await session.commit()
    assert identity == data.identity and claim is None
    assert await snapshot() == before


@pytest.mark.parametrize("change", ["other_actor", "unconsumed", "wrong_action"])
async def test_confirmed_origin_requires_the_actual_consumed_requester_bound_approval(delivery, change):
    service, providers = delivery
    data = await accepted(service)
    user = await actor() if change == "other_actor" else data.user
    if change == "unconsumed":
        async with AsyncSessionLocal() as session:
            approval = await session.get(ActionConfirmation, uuid.UUID(data.approval["confirmation_id"]))
            approval.consumed_at = None
            approval.outcome = None
            await session.commit()
    before = await snapshot()
    with pytest.raises(MutationError):
        async with AsyncSessionLocal() as session:
            await service.reserve_confirmed_in_session(session, actor_user_id=user.id,
                auth_version=user.auth_session_version, operation_id=data.approval["confirmation_id"],
                action="different.action" if change == "wrong_action" else ACTION,
                context=context(), direct_action=data.action)
            await session.commit()
    assert await snapshot() == before
    providers.announce.assert_not_awaited()


@pytest.mark.parametrize("change", ["message", "target", "actor", "authority"])
async def test_prepared_duplicate_origin_cannot_rebind_content_or_authority(delivery, change):
    service, _ = delivery
    data = await accepted(service)
    row = await service.run_store.get(data.identity)
    payload, plan = deepcopy(row.context), deepcopy(row.delivery_plan)
    payload.pop("prepared_plan_hash")
    if change in {"message", "target"}:
        plan[0]["action"][change] += " changed"
    else:
        payload["confirmed_delivery"]["user_id" if change == "actor" else "authority"] = str(uuid.uuid4()) if change == "actor" else "unsupported"
    before = await snapshot()
    with pytest.raises(ValueError, match="different content or authority"):
        async with AsyncSessionLocal() as session:
            await service.run_store.enqueue_prepared_in_session(session, payload, run_id=data.identity, plan=plan)
            await session.commit()
    assert await snapshot() == before


@pytest.mark.parametrize("change", ["role", "auth_version", "inactive", "configuration"])
async def test_current_authority_or_configuration_change_denies_attempt_before_transport(delivery, change):
    service, providers = delivery
    data = await accepted(service)
    async with AsyncSessionLocal() as session:
        if change == "configuration":
            row = await session.get(SystemSetting, "home_assistant_token")
            row.value = settings.setting_payload(row.key, "synthetic-new-provider-token")
        else:
            user = await session.get(User, data.user.id)
            if change == "role": user.role = UserRole.STANDARD
            elif change == "auth_version": user.auth_session_version += 1
            else: user.is_active = False
        await session.commit()
    await service.dispatch_reserved(data.identity, data.claimed)
    row = await service.run_store.get(data.identity)
    assert row.status == "skipped" and row.delivered_count == 0
    assert row.delivery_plan[0]["state"] == "skipped"
    assert row.delivery_plan[0]["reason"] == ("notification_configuration_changed" if change == "configuration" else "confirmed_actor_no_longer_authorized")
    providers.announce.assert_not_awaited()


async def test_dispatch_rechecks_original_900_second_age_at_attempt_and_never_refreshes_it(delivery, monkeypatch):
    assert MAX_DISPATCH_AGE_SECONDS == 900, "Confirmed delivery preserves the accepted 15-minute contract"
    service, providers = delivery
    data = await accepted(service)
    original = service.delivery_config
    async def delayed_config():
        config = await original()
        async with AsyncSessionLocal() as session:
            await session.execute(update(NotificationRun).where(NotificationRun.id == data.identity)
                .values(queued_at=text("clock_timestamp() - interval '901 seconds'")))
            await session.commit()
        return config
    monkeypatch.setattr(service, "delivery_config", delayed_config)
    await service.dispatch_reserved(data.identity, data.claimed)
    row = await service.run_store.get(data.identity)
    assert row.status == "review_required" and row.review_reason == "dispatch_age_exceeded"
    providers.announce.assert_not_awaited()
    assert await service.run_store.claim(data.identity) is None


async def test_unattempted_expired_lease_recovers_once_without_resetting_queue_age(delivery):
    service, providers = delivery
    data = await accepted(service)
    queued_at = data.claimed.queued_at
    assert not await service.dispatcher.run_once(data.identity)
    await expire(data.identity)
    assert await service.dispatcher.run_once(data.identity)
    row = await service.run_store.get(data.identity)
    assert row.status == "provider_accepted" and row.claim_count == 2
    assert row.queued_at == queued_at and row.claim_token is None
    providers.announce.assert_awaited_once()
    assert not await service.dispatcher.run_once(data.identity)
    await service.dispatch_reserved(data.identity, data.claimed)
    providers.announce.assert_awaited_once()


@pytest.mark.parametrize("failure", ["lost_response", "client_cancel"])
async def test_attempted_delivery_interruption_requires_review_and_never_replays(delivery, failure):
    service, providers = delivery
    data = await accepted(service)
    entered = asyncio.Event()
    async def uncertain(*args, **kwargs):
        entered.set()
        if failure == "lost_response":
            raise TimeoutError("Synthetic response lost after possible acceptance")
        await asyncio.Event().wait()
    providers.announce.side_effect = uncertain
    task = asyncio.create_task(service.dispatch_reserved(data.identity, data.claimed))
    try:
        await bounded(entered.wait())
        if failure == "client_cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await bounded(task)
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    row = await service.run_store.get(data.identity)
    assert row.status == "review_required" and row.delivery_plan[0]["state"] == "unknown"
    assert row.review_reason == "provider_outcome_unknown"
    assert await service.run_store.claim(data.identity) is None
    await service.dispatch_reserved(data.identity, data.claimed)
    providers.announce.assert_awaited_once()




@pytest.mark.parametrize("kind", ["voice", "mobile"])
async def test_literal_body_and_validated_configuration_reach_transport_unchanged(delivery, monkeypatch, kind):
    service, providers = delivery
    data = await accepted(service, kind=kind)
    snapshots = []
    original = service.authorize_confirmed_attempt
    async def authorize(*args, **kwargs):
        config, denial = await original(*args, **kwargs)
        snapshots.append(config)
        return config, denial
    monkeypatch.setattr(service, "authorize_confirmed_attempt", authorize)
    # The final authoritative transaction must replace this deliberately stale
    # preflight snapshot, then hand that exact validated object to transport.
    monkeypatch.setattr(service, "delivery_config", AsyncMock(return_value=SimpleNamespace(stale=True)))
    await service.dispatch_reserved(data.identity, data.claimed)
    row = await service.run_store.get(data.identity)
    assert row.status == "provider_accepted" and snapshots and snapshots[-1] is not None
    provider = providers.announce if kind == "voice" else providers.mobile
    provider.assert_awaited_once()
    args, kwargs = provider.await_args
    assert args[1 if kind != "mobile" else 2] == BODY
    assert kwargs["runtime_config"] is snapshots[-1]
    assert kwargs["runtime_config"].home_assistant_token == "synthetic-saved-ha-secret"


@pytest.mark.parametrize("kind", ["voice", "mobile"])
async def test_confirmed_workflow_freezes_selected_audience_and_reuses_validated_provider_config(delivery, monkeypatch, kind):
    service, providers = delivery
    user = await actor()
    target = "media_player.original" if kind == "voice" else "notify.mobile_app_original"
    discovery_name = "all_media_player_targets" if kind == "voice" else "all_home_assistant_mobile_targets"
    discovered = AsyncMock(return_value=[target])
    monkeypatch.setattr(service.recipients, discovery_name, discovered)
    monkeypatch.setattr(service, "_voice_announcements_preflight", AsyncMock(return_value=None))
    rules = [{"id": "synthetic-rule", "name": "Synthetic workflow", "trigger_event": "integration_test",
        "is_active": True, "conditions": [], "actions": [{"id": "synthetic-action", "type": kind,
            "target_mode": "all", "target_ids": [], "title_template": "Workflow title",
            "message_template": "Synthetic workflow body"}]}]
    payload = {"rules": rules}
    async with AsyncSessionLocal() as session:
        approval = await action_confirmations.create_action_confirmation(session, user=user, action=ACTION, payload=payload)
    async with AsyncSessionLocal() as session:
        identity, claimed = await service.reserve_confirmed_request(session, user=user, action=ACTION, payload=payload,
            confirmation_token=approval["confirmation_token"], context=context(), rules_override=rules)
        await session.commit()
    frozen_key = "frozen_voice_targets" if kind == "voice" else "frozen_mobile_targets"
    assert claimed.delivery_plan[0]["action"][frozen_key] == [target]
    discovered.assert_awaited_once()
    discovered.side_effect = AssertionError("Accepted workflow re-resolved an expanded recipient audience")
    validated = []
    original = service.authorize_confirmed_attempt
    async def authorize(*args, **kwargs):
        config, denial = await original(*args, **kwargs)
        validated.append(config)
        return config, denial
    monkeypatch.setattr(service, "authorize_confirmed_attempt", authorize)
    await service.dispatch_reserved(identity, claimed)
    row = await service.run_store.get(identity)
    assert row.status == "provider_accepted" and validated and validated[-1] is not None
    provider = providers.announce if kind == "voice" else providers.mobile
    provider.assert_awaited_once()
    args, kwargs = provider.await_args
    assert (args[0].entity_id if kind == "voice" else args[0].service_name) == target
    assert kwargs["runtime_config"] is validated[-1]
    discovered.assert_awaited_once()


async def test_announcement_reserved_outside_maintenance_is_not_sent_when_recovery_finds_maintenance_active(delivery):
    service, providers = delivery
    user, action = await actor(), direct()
    payload = {"action": deepcopy(action)}
    async with AsyncSessionLocal() as session:
        session.add(MaintenanceModeState(id=1, is_active=False, source="synthetic-confirmed-contract"))
        await session.commit()
        approval = await action_confirmations.create_action_confirmation(session, user=user,
            action="announcement.say", payload=payload)
    async with AsyncSessionLocal() as session:
        identity, claim = await service.reserve_confirmed_request(session, user=user, action="announcement.say",
            payload=payload, confirmation_token=approval["confirmation_token"], context=context(), direct_action=action)
        await session.commit()
    assert claim.delivery_plan[0]["state"] == "pending"
    await expire(identity)
    async with AsyncSessionLocal() as session:
        state = await session.get(MaintenanceModeState, 1)
        state.is_active = True
        await session.commit()
    assert await service.dispatcher.run_once(identity)
    row = await service.run_store.get(identity)
    assert row.status == "skipped" and row.claim_count == 2
    assert row.delivery_plan[0]["state"] == "skipped"
    assert row.delivery_plan[0]["reason"] == "maintenance_mode_active"
    assert row.delivered_count == 0 and row.queued_at == claim.queued_at
    providers.announce.assert_not_awaited()
    assert not await service.dispatcher.run_once(identity)


async def wait_for_database_lock(waiter_pid, holder_pid):
    """The database blocking graph is the ordering barrier, never a sleep."""
    async with asyncio.timeout(WAIT_SECONDS):
        async with AsyncSessionLocal() as observer:
            while True:
                blockers = await observer.scalar(select(func.pg_blocking_pids(waiter_pid)))
                if holder_pid in blockers:
                    return
                await asyncio.sleep(0)


@pytest.mark.parametrize("held_row", ["actor", "notification_run"])
async def test_config_changed_while_dispatch_waits_for_authority_or_final_run_lock_never_reaches_transport(delivery, monkeypatch, held_row):
    service, providers = delivery
    data = await accepted(service)
    entered = asyncio.Event()
    attempt_pid = None
    validated = []
    original = service.authorize_confirmed_attempt

    async def authorize(session, *args, **kwargs):
        nonlocal attempt_pid
        attempt_pid = await session.scalar(select(func.pg_backend_pid()))
        entered.set()
        config, denial = await original(session, *args, **kwargs)
        validated.append((config, denial))
        return config, denial

    monkeypatch.setattr(service, "authorize_confirmed_attempt", authorize)
    task = None
    async with AsyncSessionLocal() as holder:
        holder_pid = await holder.scalar(select(func.pg_backend_pid()))
        model, identity = (User, data.user.id) if held_row == "actor" else (NotificationRun, data.identity)
        await holder.scalar(select(model).where(model.id == identity).with_for_update())
        try:
            task = asyncio.create_task(service.dispatch_reserved(data.identity, data.claimed))
            await bounded(entered.wait())
            await bounded(wait_for_database_lock(attempt_pid, holder_pid))
            async with AsyncSessionLocal() as writer:
                setting = await writer.get(SystemSetting, "home_assistant_token")
                setting.value = settings.setting_payload(setting.key, "synthetic-changed-during-lock-wait")
                await writer.commit()
        finally:
            await holder.rollback()
            if task is not None:
                try:
                    await bounded(task)
                finally:
                    if not task.done(): task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    row = await service.run_store.get(data.identity)
    assert row.status == "skipped" and row.delivered_count == 0
    assert row.delivery_plan[0]["state"] == "skipped"
    assert row.delivery_plan[0]["reason"] == "notification_configuration_changed"
    providers.announce.assert_not_awaited()
    assert validated[-1][1] == "notification_configuration_changed"
    assert validated[-1][0].home_assistant_token == "synthetic-changed-during-lock-wait"
    if held_row == "notification_run":
        assert any(config.home_assistant_token == "synthetic-saved-ha-secret" and denial is None
                   for config, denial in validated[:-1])




async def test_default_announcement_service_rejects_stale_preparation_before_confirmation_consumption(delivery, monkeypatch):
    service, providers = delivery
    monkeypatch.setattr(notifications, "get_notification_service", lambda: service)
    user = await actor()
    await set_default_media_player("media_player.nr02_a")
    settings.invalidate_runtime_config_cache()
    cached = await settings.get_runtime_config()
    assert cached.home_assistant_default_media_player == "media_player.nr02_a"
    await set_default_media_player("media_player.nr02_b")
    approval, payload = await announcement_confirmation(user)
    before = await snapshot()

    with pytest.raises(NotificationDeliveryError, match="default announcement destination changed") as caught:
        async with AsyncSessionLocal() as session:
            await send_confirmed_notification(
                session, user=user, action="announcement.say", payload=payload,
                confirmation_token=approval["confirmation_token"],
                context=NotificationContext(
                    event_type="integration_test", subject="Announcement", severity="info",
                    facts={"message": BODY},
                ),
                direct_action=announcement_action("media_player.nr02_a", configured_default=True),
            )

    assert caught.value.delivery == "not_sent"
    assert await snapshot() == before
    async with AsyncSessionLocal() as session:
        confirmation = await session.get(ActionConfirmation, uuid.UUID(approval["confirmation_id"]))
        assert confirmation.consumed_at is None and confirmation.outcome is None
        assert await session.scalar(select(func.count()).select_from(NotificationRun)) == 0
        setting = await session.get(SystemSetting, "home_assistant_default_media_player")
        assert setting.value["plain"] == "media_player.nr02_b"
    providers.announce.assert_not_awaited()


async def test_announcement_route_rejects_stale_cached_default_before_confirmation_consumption(delivery, monkeypatch):
    service, providers = delivery
    monkeypatch.setattr(notifications, "get_notification_service", lambda: service)
    user = await actor()
    await set_default_media_player("media_player.nr02_a")
    settings.invalidate_runtime_config_cache()
    cached = await settings.get_runtime_config()
    assert cached.home_assistant_default_media_player == "media_player.nr02_a"
    await set_default_media_player("media_player.nr02_b")
    approval, _ = await announcement_confirmation(user)
    before = await snapshot()

    with pytest.raises(NotificationDeliveryError, match="default announcement destination changed") as caught:
        await announce_via_route(user, approval["confirmation_token"])

    assert caught.value.delivery == "not_sent"
    assert await snapshot() == before
    async with AsyncSessionLocal() as session:
        confirmation = await session.get(ActionConfirmation, uuid.UUID(approval["confirmation_id"]))
        assert confirmation.consumed_at is None and confirmation.outcome is None
        assert await session.scalar(select(func.count()).select_from(NotificationRun)) == 0
        setting = await session.get(SystemSetting, "home_assistant_default_media_player")
        assert setting.value["plain"] == "media_player.nr02_b"
    providers.announce.assert_not_awaited()


async def test_explicit_announcement_entity_sends_when_cached_default_is_stale(delivery, monkeypatch):
    service, providers = delivery
    monkeypatch.setattr(notifications, "get_notification_service", lambda: service)
    user = await actor()
    await set_default_media_player("media_player.nr02_a")
    settings.invalidate_runtime_config_cache()
    cached = await settings.get_runtime_config()
    assert cached.home_assistant_default_media_player == "media_player.nr02_a"
    await set_default_media_player("media_player.nr02_b")
    explicit = "media_player.nr02_explicit"
    approval, _ = await announcement_confirmation(user, entity_id=explicit)

    result = await announce_via_route(user, approval["confirmation_token"], entity_id=explicit)

    identity = uuid.UUID(result["notification_run_id"])
    row = await service.run_store.get(identity)
    assert result == {"status": "sent", "entity_id": explicit, "notification_run_id": str(identity)}
    assert row.status == "provider_accepted" and row.delivered_count == 1 and row.failed_count == 0
    async with AsyncSessionLocal() as session:
        confirmation = await session.get(ActionConfirmation, uuid.UUID(approval["confirmation_id"]))
        assert confirmation.consumed_at is not None and confirmation.outcome == "consumed"
        setting = await session.get(SystemSetting, "home_assistant_default_media_player")
        assert setting.value["plain"] == "media_player.nr02_b"
    providers.announce.assert_awaited_once()
    target, body = providers.announce.await_args.args
    assert target.entity_id == explicit and body == BODY
    assert (await settings.get_runtime_config()).home_assistant_default_media_player == "media_player.nr02_a"


async def test_announcement_route_rejects_default_changed_after_cached_lookup_before_preparation(delivery, monkeypatch):
    service, providers = delivery
    monkeypatch.setattr(notifications, "get_notification_service", lambda: service)
    user = await actor()
    await set_default_media_player("media_player.nr02_a")
    settings.invalidate_runtime_config_cache()
    approval, _ = await announcement_confirmation(user)
    await settings.get_runtime_config()
    before = await snapshot()
    original_get_runtime_config = integrations_api.get_runtime_config
    observed = []

    async def get_cached_then_commit_new_default():
        config = await original_get_runtime_config()
        observed.append(config.home_assistant_default_media_player)
        await set_default_media_player("media_player.nr02_b")
        return config

    monkeypatch.setattr(integrations_api, "get_runtime_config", get_cached_then_commit_new_default)
    with pytest.raises(NotificationDeliveryError, match="default announcement destination changed") as caught:
        await announce_via_route(user, approval["confirmation_token"])

    assert caught.value.delivery == "not_sent"
    assert observed == ["media_player.nr02_a"]
    after = await snapshot()
    for table in ("action_confirmations", "audit_logs", "notification_runs"):
        assert after[table] == before[table]
    async with AsyncSessionLocal() as session:
        confirmation = await session.get(ActionConfirmation, uuid.UUID(approval["confirmation_id"]))
        assert confirmation.consumed_at is None and confirmation.outcome is None
        assert await session.scalar(select(func.count()).select_from(NotificationRun)) == 0
        setting = await session.get(SystemSetting, "home_assistant_default_media_player")
        assert setting.value["plain"] == "media_player.nr02_b"
    providers.announce.assert_not_awaited()
