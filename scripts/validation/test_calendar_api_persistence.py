"""Actual ASGI routes/middleware and operator removal around inert SDK work."""
from test_recovery_boundaries import isolated_resources as isolated_resources
from test_calendar_sync_store_persistence import calendar_resources as calendar_resources
from test_calendar_sync_store_persistence import allowed, setup_accounts, work
from test_calendar_authentication_persistence import InertAuthClient
from test_calendar_sync_engine_persistence import InertCalendarClient, setup as setup_sync

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from threading import Event

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import icloud_calendar as routes
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models import ActionConfirmation, AuditLog, ICloudCalendarAccount, ICloudCalendarSyncRun, User, VisitorPass
from app.models.enums import VisitorPassStatus, VisitorPassType
from app.services import action_confirmations, auth, calendar_accounts
from app.services.calendar_accounts import CalendarAccountStore
from app.services.calendar_request_authority import CalendarConfirmedAuthority, CalendarRequestAuthority
from app.services.calendar_sync_store import CalendarSyncStore
from app.services.icloud_calendar import ICloudCalendarService

pytestmark = pytest.mark.asyncio
PREFIX = "/api/v1/integrations/icloud-calendar"


def token(actor):
    return auth._encode_jwt({"sub": str(actor), "ver": 0, "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()), "jti": str(uuid.uuid4())})


def http(actor):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://inert.test", headers={"Authorization": "Bearer " + token(actor)})


async def confirm(actor, action, payload):
    from app.models import User
    async with AsyncSessionLocal() as session:
        return (await action_confirmations.create_action_confirmation(session, user=await session.get(User, actor), action=action, payload=payload))["confirmation_token"]


def service_with(client):
    return ICloudCalendarService(client)


async def test_actual_auth_route_has_no_checked_out_auth_transaction_during_sdk_and_busy_rejects(monkeypatch):
    actor, _ = await setup_accounts(0)
    client = InertAuthClient()
    client.block_start = True
    service = service_with(client)
    monkeypatch.setattr(routes, "get_icloud_calendar_service", lambda: service)
    async with http(actor) as caller:
        first = asyncio.create_task(caller.post(PREFIX + "/accounts/auth/start", json={"apple_id": "first@example.invalid", "password": "inert"}))
        assert await asyncio.to_thread(client.started.wait, 3)
        assert engine.pool.checkedout() == 0
        busy = await caller.post(PREFIX + "/accounts/auth/start", json={"apple_id": "second@example.invalid", "password": "inert"})
        assert busy.status_code == 503 and busy.headers["retry-after"] == "5"
        assert client.calls == [("start", "first@example.invalid")]
        client.release.set()
        response = await first
        assert response.status_code == 200 and response.json()["status"] == "connected"
    assert engine.pool.checkedout() == 0 and service.sdk._active is None
    await service.stop()


async def test_actual_verify_route_and_observer_failure_preserve_accepted_auth_receipt(monkeypatch):
    actor, _ = await setup_accounts(0)
    client = InertAuthClient(two_factor=True)
    service = service_with(client)
    monkeypatch.setattr(routes, "get_icloud_calendar_service", lambda: service)
    async def observer(*args, **kwargs):
        raise RuntimeError("inert observer failure")
    monkeypatch.setattr("app.services.icloud_calendar.event_bus.publish", observer)
    async with http(actor) as caller:
        first = await caller.post(PREFIX + "/accounts/auth/start", json={"apple_id": "first@example.invalid", "password": "inert"})
        assert first.status_code == 200 and first.json()["requires_2fa"]
        second = await caller.post(PREFIX + "/accounts/auth/verify", json={"handshake_id": first.json()["handshake_id"], "code": "123456"})
        assert second.status_code == 200 and second.json()["status"] == "connected"
    assert [name for name, _ in client.calls] == ["start", "request", "activate", "validate", "trust"]
    assert engine.pool.checkedout() == 0
    await service.stop()


async def test_actual_http_disconnect_stops_owned_native_work_and_drains_before_slot_release(monkeypatch):
    actor, _ = await setup_accounts(0)
    client = InertAuthClient()
    client.block_start = True
    service = service_with(client)
    monkeypatch.setattr(routes, "get_icloud_calendar_service", lambda: service)
    messages = asyncio.Queue()
    body = json.dumps({"apple_id": "disconnect@example.invalid", "password": "inert"}).encode()
    await messages.put({"type": "http.request", "body": body, "more_body": False})
    sent = []
    async def receive(): return await messages.get()
    async def send(message): sent.append(message)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http", "path": PREFIX + "/accounts/auth/start", "raw_path": (PREFIX + "/accounts/auth/start").encode(), "query_string": b"", "root_path": "", "server": ("inert.test", 80), "client": ("127.0.0.1", 1234), "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"authorization", ("Bearer " + token(actor)).encode())]}
    request = asyncio.create_task(app(scope, receive, send))
    assert await asyncio.to_thread(client.started.wait, 3)
    assert engine.pool.checkedout() == 0
    await messages.put({"type": "http.disconnect"})
    for _ in range(100):
        if service.sdk._native.work._stop.is_set(): break
        await asyncio.sleep(.005)
    assert service.sdk._native.work._stop.is_set() and service.sdk._active is not None
    client.release.set()
    await asyncio.wait_for(request, 5)
    assert len(client.cleaned) == 1 and service.sdk._active is None and engine.pool.checkedout() == 0
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(ICloudCalendarAccount)) == 0
    await service.stop()


async def test_actual_sync_route_known_uuid_replay_and_exact_read_are_inert(monkeypatch):
    actor, ids, client, store, sdk, owner = await setup_sync(1)
    service = service_with(client)
    monkeypatch.setattr(routes, "get_icloud_calendar_service", lambda: service)
    identity = str(uuid.uuid4())
    async with http(actor) as caller:
        first = await caller.post(PREFIX + "/sync", json={"run_id": identity})
        assert first.status_code == 200, first.text
        assert first.json()["recovery"]["completed_account_ids"] == [str(ids[0])]
        assert first.json()["recovery"]["remaining_account_ids"] == []
        read = await caller.get(PREFIX + "/sync/" + identity)
        replay = await caller.post(PREFIX + "/sync", json={"run_id": identity})
        assert read.json() == replay.json() and len(client.calls) == 1
        assert "attempt_token" not in json.dumps(read.json()) and "encrypted_session" not in read.text
    await service.stop()


async def test_confirmed_authority_rollback_requires_consumption_again_then_commits_once():
    actor, _ = await setup_accounts(0)
    payload = {"run_id": str(uuid.uuid4())}
    confirmation = await confirm(actor, "icloud_calendar.sync.resume", payload)
    authority = CalendarConfirmedAuthority(CalendarRequestAuthority(actor, token(actor)), "icloud_calendar.sync.resume", payload, confirmation)
    async with AsyncSessionLocal() as session:
        await authority.authorize(session, False)
        await authority.authorize(session, True)
        await session.rollback()
    async with AsyncSessionLocal() as session:
        await authority.authorize(session, False)
        await authority.authorize(session, True)
        await session.commit()
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "real_world_action.confirmation.consumed")) == 1
        row = await session.scalar(select(ActionConfirmation))
        assert row.consumed_at is not None


@pytest.mark.parametrize("missing", [True, False])
async def test_remove_rejection_is_audited_after_actor_lock_release_and_missing_is_not(monkeypatch, missing):
    actor, ids = await setup_accounts(1)
    service = service_with(InertAuthClient())
    monkeypatch.setattr(routes, "get_icloud_calendar_service", lambda: service)
    async with http(actor) as caller:
        response = await asyncio.wait_for(caller.request("DELETE", PREFIX + "/accounts/" + str(ids[0]), json={"confirmation_token": None if missing else "invalid-inert-token"}), 4)
        assert response.status_code == (428 if missing else 403), response.text
    async with AsyncSessionLocal() as session:
        assert (await session.get(ICloudCalendarAccount, ids[0])).is_active
        audits = list((await session.scalars(select(AuditLog).where(AuditLog.action == "real_world_action.confirmation.rejected"))).all())
        assert len(audits) == (0 if missing else 1)
    assert engine.pool.checkedout() == 0
    await service.stop()


async def test_rejection_audit_failure_is_visible_without_removal(monkeypatch):
    actor, ids = await setup_accounts(1)
    service = service_with(InertAuthClient())
    monkeypatch.setattr(routes, "get_icloud_calendar_service", lambda: service)
    async def audit_failure(*args, **kwargs): raise RuntimeError("synthetic required rejection audit failure")
    monkeypatch.setattr(action_confirmations, "_write_confirmation_rejection", audit_failure)
    async with http(actor) as caller:
        response = await caller.request("DELETE", PREFIX + "/accounts/" + str(ids[0]), json={"confirmation_token": "invalid-inert-token"})
        assert response.status_code == 500, response.text
    async with AsyncSessionLocal() as session:
        assert (await session.get(ICloudCalendarAccount, ids[0])).is_active
    await service.stop()


async def test_operator_removal_batches_all_eligible_source_passes_and_publishes_only_after_commit(monkeypatch):
    actor, ids = await setup_accounts(1)
    identity = ids[0]
    kept = []
    begins = datetime.now(UTC) + timedelta(days=30)
    def row_for(key, status):
        return VisitorPass(id=uuid.uuid4(), visitor_name="Inert Removal Visitor", pass_type=VisitorPassType.ONE_TIME,
            expected_time=begins, valid_from=begins - timedelta(minutes=30), valid_until=begins + timedelta(hours=1),
            creation_source="icloud_calendar", source_reference=f"icloud:{identity}:inert:{key}",
            source_metadata={"event_start": "malformed-or-outside-sync-range"}, status=status)
    async with AsyncSessionLocal() as session:
        for index in range(103):
            session.add(row_for(f"event-{index}", VisitorPassStatus.ACTIVE))
        for status in (VisitorPassStatus.USED, VisitorPassStatus.CANCELLED, VisitorPassStatus.EXPIRED):
            row = row_for(str(status), status)
            session.add(row); kept.append((row.id, status))
        await session.commit()
    service = service_with(InertAuthClient())
    monkeypatch.setattr(routes, "get_icloud_calendar_service", lambda: service)
    published = []
    async def publish(kind, data):
        async with AsyncSessionLocal() as session:
            assert not (await session.get(ICloudCalendarAccount, identity)).is_active
            assert await session.scalar(select(func.count()).select_from(VisitorPass).where(VisitorPass.status == VisitorPassStatus.ACTIVE)) == 0
        published.append((kind, data))
    monkeypatch.setattr("app.services.icloud_calendar.event_bus.publish", publish)
    confirmation = await confirm(actor, "icloud_calendar.account.remove", {"account_id": str(identity)})
    async with http(actor) as caller:
        response = await caller.request("DELETE", PREFIX + "/accounts/" + str(identity), json={"confirmation_token": confirmation})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "removed"
    async with AsyncSessionLocal() as session:
        account = await session.get(ICloudCalendarAccount, identity)
        assert not account.is_active and account.encrypted_session_bundle is None and account.sync_generation == 1
        assert await session.scalar(select(func.count()).select_from(VisitorPass).where(VisitorPass.status == VisitorPassStatus.CANCELLED)) == 104
        for pass_id, status in kept: assert (await session.get(VisitorPass, pass_id)).status == status
        assert await session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "visitor_pass.cancel")) == 103
    assert [kind for kind, data in published] == ["icloud_calendar.accounts_changed", "visitor_pass.cancelled"]
    assert published[-1][1]["cancelled_count"] == 103 and "visitor_pass" not in published[-1][1]
    await service.stop()


async def test_confirmed_removal_required_audit_failure_rolls_back_then_same_token_retry_is_legitimate(monkeypatch):
    actor, ids = await setup_accounts(1)
    service = service_with(InertAuthClient())
    monkeypatch.setattr(routes, "get_icloud_calendar_service", lambda: service)
    confirmation = await confirm(actor, "icloud_calendar.account.remove", {"account_id": str(ids[0])})
    original = calendar_accounts.write_audit_log
    async def fail(session, **kwargs):
        await original(session, **kwargs)
        await session.flush()
        raise RuntimeError("inert account removal required audit failure")
    monkeypatch.setattr(calendar_accounts, "write_audit_log", fail)
    async with http(actor) as caller:
        failed = await caller.request("DELETE", PREFIX + "/accounts/" + str(ids[0]), json={"confirmation_token": confirmation})
        assert failed.status_code == 500
        async with AsyncSessionLocal() as session:
            assert (await session.get(ICloudCalendarAccount, ids[0])).is_active
            assert (await session.scalar(select(ActionConfirmation))).consumed_at is None
        monkeypatch.setattr(calendar_accounts, "write_audit_log", original)
        accepted = await caller.request("DELETE", PREFIX + "/accounts/" + str(ids[0]), json={"confirmation_token": confirmation})
        assert accepted.status_code == 200 and accepted.json()["status"] == "removed"
    await service.stop()


async def test_actual_admin_same_run_recovery_adopts_workflow_origin_without_repeating_completed_account(monkeypatch):
    from app.modules.icloud_calendar.contracts import ICloudCalendarReauthRequired
    from app.services.calendar_sync_contracts import CalendarRunContext
    actor, ids, client, store, sdk, owner = await setup_sync(2)
    service = service_with(client)
    monkeypatch.setattr(routes, "get_icloud_calendar_service", lambda: service)
    identity = uuid.uuid4()
    execution = {"run_id": str(uuid.uuid4()), "claim_token": str(uuid.uuid4()), "index": 1, "operation_id": str(identity), "idempotency_key": "inert:calendar:1"}
    client.failure = ("inert1@example.invalid", ICloudCalendarReauthRequired("Reconnect second account"))
    original = await service.sync_all(run_id=identity, origin="workflow", actor="Inert workflow", actor_user_id=actor, execution=execution, authorize=allowed)
    completed = original["account_results"][0]
    assert original["recovery"]["completed_account_ids"] == [str(ids[0])]
    async with AsyncSessionLocal() as session:
        row = await session.get(ICloudCalendarAccount, ids[1])
        row.status = "connected"; row.sync_generation += 1
        await session.commit()
    client.failure = None
    confirmation = await confirm(actor, "icloud_calendar.sync.resume", {"run_id": str(identity)})
    async with http(actor) as caller:
        ordinary = await caller.post(PREFIX + "/sync", json={"run_id": str(identity)})
        assert ordinary.status_code == 409 and len(client.calls) == 2
        resumed = await caller.post(PREFIX + f"/sync/{identity}/resume", json={"confirmation_token": confirmation})
        assert resumed.status_code == 200, resumed.text
        result = resumed.json()
        assert result["account_results"][0] == completed and result["recovery"]["remaining_account_ids"] == []
        assert result["recovery"]["original_origin"] == "workflow" and result["recovery"]["workflow_operation_id"] == str(identity)
        assert result["recovery"]["recovery_actor_user_id"] == str(actor)
    assert client.calls == ["inert0@example.invalid", "inert1@example.invalid", "inert1@example.invalid"]
    async with AsyncSessionLocal() as session:
        context = CalendarRunContext.stored((await session.get(ICloudCalendarSyncRun, identity)).run_context)
        assert context.execution == execution and context.original_actor_user_id == actor
        assert await session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "icloud_calendar.sync_resumed")) == 1
    await service.stop()


@pytest.mark.parametrize("observation", ["account_publish", "sync_metadata"])
async def test_facade_known_receipt_survives_two_cancels_while_actual_observer_cleanup_drains(monkeypatch, observation):
    monkeypatch.setattr("app.services.icloud_calendar.CALENDAR_OBSERVATION_SECONDS", .05)
    started, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancellations = []
    async def block():
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancellations.append(asyncio.current_task().cancelling())
            cleanup.set()
            await release.wait()
    if observation == "account_publish":
        actor, _ = await setup_accounts(0)
        client = InertAuthClient()
        service = service_with(client)
        async def publish(*args, **kwargs): await block()
        monkeypatch.setattr("app.services.icloud_calendar.event_bus.publish", publish)
        request = asyncio.create_task(service.start_auth(apple_id="accepted@example.invalid", password="inert", actor="Inert Admin", actor_user_id=actor, authorize=allowed))
    else:
        actor, ids, client, store, sdk, owner = await setup_sync(1)
        service = service_with(client)
        original_get = AsyncSession.get
        observation_started = False
        original_observe = service._observe_accepted
        async def observing(receipt, observe, **kwargs):
            nonlocal observation_started
            observation_started = True
            return await original_observe(receipt, observe, **kwargs)
        monkeypatch.setattr(service, "_observe_accepted", observing)
        async def get(session, model, *args, **kwargs):
            if model is ICloudCalendarSyncRun and observation_started:
                await block()
            return await original_get(session, model, *args, **kwargs)
        monkeypatch.setattr(AsyncSession, "get", get)
        request = asyncio.create_task(service.sync_all(run_id=uuid.uuid4(), origin="ui", actor="Inert Admin", actor_user_id=actor, execution=None, authorize=allowed))
    await asyncio.wait_for(started.wait(), 3)
    request.cancel(); await asyncio.wait_for(cleanup.wait(), 2)
    request.cancel(); await asyncio.sleep(.02)
    assert not request.done() and cancellations == [1]
    release.set(); result = await request
    if observation == "account_publish":
        assert result["status"] == "connected" and len(client.calls) == 1
    else:
        assert len(result["account_results"]) == 1 and result["account_results"][0]["status"] == "ok"
        assert client.calls == ["inert0@example.invalid"]
    assert engine.pool.checkedout() == 0
    await service.stop()


async def test_service_stop_before_optional_callback_keeps_accepted_auth_and_starts_no_observation(monkeypatch):
    actor, _ = await setup_accounts(0)
    client = InertAuthClient(); client.block_cleanup = True
    service = service_with(client)
    published = []
    async def publish(*args, **kwargs): published.append(args)
    monkeypatch.setattr("app.services.icloud_calendar.event_bus.publish", publish)
    request = asyncio.create_task(service.start_auth(apple_id="accepted@example.invalid", password="inert", actor="Inert Admin", actor_user_id=actor, authorize=allowed))
    assert await asyncio.to_thread(client.cleanup_started.wait, 3)
    stopped = asyncio.create_task(service.stop())
    await asyncio.sleep(.02)
    assert not stopped.done() and not request.done() and service.sdk._active is not None
    client.cleanup_release.set()
    result = await request; await stopped
    assert result["status"] == "connected" and published == [] and len(client.cleaned) == 1
    assert engine.pool.checkedout() == 0 and service.sdk._active is None


async def test_service_stop_retains_actual_observation_session_close_before_disposal(monkeypatch):
    monkeypatch.setattr("app.services.icloud_calendar.CALENDAR_OBSERVATION_SECONDS", .05)
    actor, ids, client, store, sdk, owner = await setup_sync(1)
    service = service_with(client)
    closing, release = asyncio.Event(), asyncio.Event()
    observation_started = False
    original_observe, original_close = service._observe_accepted, AsyncSession.close
    async def observing(receipt, observe, **kwargs):
        nonlocal observation_started
        observation_started = True
        return await original_observe(receipt, observe, **kwargs)
    async def close(session):
        if observation_started and session.in_transaction():
            closing.set()
            await release.wait()
            assert asyncio.current_task().cancelling() == 0
        await original_close(session)
    monkeypatch.setattr(service, "_observe_accepted", observing)
    monkeypatch.setattr(AsyncSession, "close", close)
    request = asyncio.create_task(service.sync_all(run_id=uuid.uuid4(), origin="ui", actor="Inert Admin", actor_user_id=actor, execution=None, authorize=allowed))
    await asyncio.wait_for(closing.wait(), 3)
    stopped = asyncio.create_task(service.stop())
    await asyncio.sleep(.08)
    assert engine.pool.checkedout() == 1 and not stopped.done() and not request.done() and service.sdk._active is not None
    release.set()
    result = await request; await stopped
    assert result["account_results"][0]["status"] == "ok"
    assert client.calls == ["inert0@example.invalid"] and engine.pool.checkedout() == 0 and service.sdk._active is None


@pytest.mark.parametrize("participant", ["remove", "resume"])
@pytest.mark.parametrize("barrier", ["close", "audit"])
async def test_confirmed_rejection_remains_owned_through_two_cancels_after_actor_rollback(monkeypatch, participant, barrier):
    actor, ids = await setup_accounts(1)
    service = service_with(InertAuthClient())
    entered, release = asyncio.Event(), asyncio.Event()
    rejected_session = None
    audit_calls = []
    original_close = AsyncSession.close
    original_write = action_confirmations._write_confirmation_rejection
    original_authorize = CalendarConfirmedAuthority.authorize
    async def authorize(authority, session, final):
        nonlocal rejected_session
        # Retain a real conflicting actor lock before the rejection. The fresh
        # audit's FK cannot succeed until this original transaction releases it.
        await session.scalar(select(User.id).where(User.id == actor).with_for_update())
        rejected_session = session
        return await original_authorize(authority, session, final)
    async def close(session):
        if barrier == "close" and session is rejected_session:
            entered.set()
            await release.wait()
            assert asyncio.current_task().cancelling() == 0
        await original_close(session)
    async def write(session, rejection):
        assert session is not rejected_session
        assert not rejected_session.in_transaction()
        audit_calls.append(rejection.action)
        if barrier == "audit":
            entered.set()
            await release.wait()
            assert asyncio.current_task().cancelling() == 0
        await original_write(session, rejection)
    monkeypatch.setattr(CalendarConfirmedAuthority, "authorize", authorize)
    monkeypatch.setattr(AsyncSession, "close", close)
    monkeypatch.setattr(action_confirmations, "_write_confirmation_rejection", write)
    action = "icloud_calendar.account.remove" if participant == "remove" else "icloud_calendar.sync.resume"
    target = ids[0] if participant == "remove" else uuid.uuid4()
    authority = CalendarConfirmedAuthority(CalendarRequestAuthority(actor, token(actor)), action,
        {"account_id" if participant == "remove" else "run_id": str(target)}, "invalid-inert-token")
    if participant == "remove":
        request = asyncio.create_task(service.remove_account(target, actor="Inert Admin", actor_user_id=actor, authorize=authority.authorize))
    else:
        request = asyncio.create_task(service.sync_all(run_id=target, origin="ui", actor="Inert Admin", actor_user_id=actor,
            execution=None, authorize=allowed, resume=True, resume_authorize=authority.authorize))
    await asyncio.wait_for(entered.wait(), 3)
    request.cancel(); await asyncio.sleep(.01); request.cancel(); await asyncio.sleep(.01)
    assert not request.done() and service.sdk._active is not None
    assert audit_calls == ([] if barrier == "close" else [action])
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog)
            .where(AuditLog.action == "real_world_action.confirmation.rejected")) == 0
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert audit_calls == [action] and service.sdk._active is None
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog)
            .where(AuditLog.action == "real_world_action.confirmation.rejected")) == 1
        assert (await session.get(ICloudCalendarAccount, ids[0])).is_active
        assert await session.scalar(select(func.count()).select_from(ICloudCalendarSyncRun)) == 0
    assert engine.pool.checkedout() == 0
    await service.stop()


@pytest.mark.parametrize("participant", ["remove", "resume"])
async def test_confirmation_primary_survives_close_fault_after_positive_live_rollback(monkeypatch, participant):
    actor, ids = await setup_accounts(1)
    service = service_with(InertAuthClient())
    monkeypatch.setattr(routes, "get_icloud_calendar_service", lambda: service)
    rejected_session = None
    original_authorize, original_close = CalendarConfirmedAuthority.authorize, AsyncSession.close
    async def authorize(authority, session, final):
        nonlocal rejected_session
        rejected_session = session
        return await original_authorize(authority, session, final)
    async def close(session):
        await original_close(session)
        if session is rejected_session:
            raise RuntimeError("inert close fault after actual rollback/release")
    monkeypatch.setattr(CalendarConfirmedAuthority, "authorize", authorize)
    monkeypatch.setattr(AsyncSession, "close", close)
    async with http(actor) as caller:
        if participant == "remove":
            response = await caller.request("DELETE", PREFIX + "/accounts/" + str(ids[0]), json={"confirmation_token": "invalid-inert-token"})
        else:
            response = await caller.post(PREFIX + "/sync/" + str(uuid.uuid4()) + "/resume", json={"confirmation_token": "invalid-inert-token"})
        assert response.status_code == 403, response.text
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog)
            .where(AuditLog.action == "real_world_action.confirmation.rejected")) == 1
    assert engine.pool.checkedout() == 0
    await service.stop()


@pytest.mark.parametrize("participant", ["remove", "resume"])
async def test_unsettled_confirmation_rollback_never_opens_fresh_rejection_audit(monkeypatch, participant):
    actor, ids = await setup_accounts(1)
    service = service_with(InertAuthClient())
    monkeypatch.setattr(routes, "get_icloud_calendar_service", lambda: service)
    rejected_session = None
    original_authorize, original_rollback, original_close = CalendarConfirmedAuthority.authorize, AsyncSession.rollback, AsyncSession.close
    async def authorize(authority, session, final):
        nonlocal rejected_session
        await session.scalar(select(User.id).where(User.id == actor).with_for_update())
        rejected_session = session
        return await original_authorize(authority, session, final)
    async def rollback(session):
        if session is rejected_session:
            raise RuntimeError("inert original rollback could not prove release")
        await original_rollback(session)
    async def close(session):
        if session is rejected_session:
            raise RuntimeError("inert original close failed before actual release")
        await original_close(session)
    async def forbidden_audit(*args, **kwargs):
        raise AssertionError("unsettled original locks cannot start a fresh rejection audit")
    monkeypatch.setattr(CalendarConfirmedAuthority, "authorize", authorize)
    monkeypatch.setattr(AsyncSession, "rollback", rollback)
    monkeypatch.setattr(AsyncSession, "close", close)
    monkeypatch.setattr(action_confirmations, "_write_confirmation_rejection", forbidden_audit)
    try:
        async with http(actor) as caller:
            if participant == "remove":
                response = await caller.request("DELETE", PREFIX + "/accounts/" + str(ids[0]), json={"confirmation_token": "invalid-inert-token"})
            else:
                response = await caller.post(PREFIX + "/sync/" + str(uuid.uuid4()) + "/resume", json={"confirmation_token": "invalid-inert-token"})
            assert response.status_code == 409 and "unresolved" in response.json()["detail"], response.text
        # The owner's retained invalidate fallback releases the local resource,
        # but cannot promote an unproved rollback into a fresh rejection audit.
        assert engine.pool.checkedout() == 0
    finally:
        if rejected_session is not None:
            await original_rollback(rejected_session)
            await original_close(rejected_session)
    assert engine.pool.checkedout() == 0
    await service.stop()


async def test_pre_database_expiry_remains_definite_no_effect_without_unknown_settlement():
    from time import monotonic
    from app.modules.icloud_calendar.contracts import CalendarWork, ICloudCalendarTimeout
    from app.services.calendar_sync_store import calendar_session, calendar_sql_budget
    with pytest.raises(ICloudCalendarTimeout):
        async with calendar_session(AsyncSessionLocal) as session:
            await calendar_sql_budget(session, CalendarWork(monotonic() - 1))
    assert engine.pool.checkedout() == 0


async def test_previously_opened_transaction_reset_after_invalidation_is_not_settlement_proof():
    from sqlalchemy import text
    from app.services.calendar_sync_contracts import CalendarOutcomeUnknown
    from app.services.calendar_sync_store import calendar_session
    with pytest.raises(CalendarOutcomeUnknown) as failure:
        async with calendar_session(AsyncSessionLocal) as session:
            await session.execute(text("SELECT 1"))
            await session.invalidate()
            assert not session.in_transaction()
            raise action_confirmations.ActionConfirmationError("inert rejected operation after transport reset")
    assert isinstance(failure.value.__cause__, action_confirmations.ActionConfirmationError)
    assert engine.pool.checkedout() == 0
