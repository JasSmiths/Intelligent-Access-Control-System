"""Real multi-account transactions around an inert synchronous vendor port."""
from test_recovery_boundaries import isolated_resources as isolated_resources
from test_calendar_sync_store_persistence import calendar_resources as calendar_resources
from test_calendar_sync_store_persistence import allowed, setup_accounts

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic

import pytest
from sqlalchemy import select

from app.core.crypto import encrypt_secret
from app.db.session import AsyncSessionLocal, engine
from app.models import ICloudCalendarAccount, ICloudCalendarSyncRun, VisitorPass
from app.modules.icloud_calendar.contracts import (
    CalendarSnapshot, ICloudCalendarEvent, ICloudCalendarIncomplete, ICloudCalendarReauthRequired,
)
from app.services.auth_root import reload_auth_root_for_transaction
from app.services.calendar_sync_contracts import CalendarOutcomeUnknown, CalendarRunContext
from app.services.calendar_sync_engine import CalendarSyncEngine
from app.services.calendar_sync_store import CalendarSyncStore
from app.services.icloud_calendar_sdk import CalendarSDKBusy, CalendarSDKOwner

pytestmark = pytest.mark.asyncio


class InertCalendarClient:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.blocked = None
        self.blocked_on = None
        self.release = Event()
        self.work_ids = set()
    def fetch_snapshot(self, *, apple_id, session_bundle, coverage, operation):
        assert engine.pool.checkedout() == 0
        assert session_bundle == {"inert": "session"}
        self.calls.append(apple_id)
        self.work_ids.add(id(operation.work))
        if self.blocked is not None and (self.blocked_on is None or self.blocked_on == apple_id):
            self.blocked.set()
            assert self.release.wait(3), "fixture must release actual vendor cleanup"
        if self.failure is not None and self.failure[0] == apple_id:
            raise self.failure[1]
        starts = datetime.now(UTC) + timedelta(hours=2)
        return CalendarSnapshot(coverage, (ICloudCalendarEvent("inert-home", "inert-event",
            "Clinic: Vicky Thompson", starts, starts + timedelta(hours=1), "Open Gate", ""),), 0)
    def cleanup_auth_session(self, session):
        raise AssertionError("Sync owns no retained auth client")
    def detach(self, session):
        raise AssertionError("Sync owns no pending auth client")


async def setup(count=2):
    actor, ids = await setup_accounts(count)
    async with AsyncSessionLocal() as session:
        await reload_auth_root_for_transaction(session)
        for identity in ids:
            row = await session.get(ICloudCalendarAccount, identity)
            row.encrypted_session_bundle = encrypt_secret('{"inert":"session"}')
        await session.commit()
    client, store = InertCalendarClient(), CalendarSyncStore()
    sdk = CalendarSDKOwner(client)
    return actor, ids, client, store, sdk, CalendarSyncEngine(store, client, sdk)


async def run(owner, actor, identity=None, *, resume=False):
    return await owner.run(identity or uuid.uuid4(), origin="ui", actor="Inert Calendar Admin",
        actor_user_id=actor, execution=None, authorize=allowed, resume=resume, resume_authorize=allowed if resume else None)


async def test_two_accounts_commit_independently_one_original_work_and_ordinary_replay_is_inert():
    actor, ids, client, store, sdk, owner = await setup()
    identity = uuid.uuid4()
    receipt = await run(owner, actor, identity)
    assert [item.account_id for item in receipt.checkpoints] == ids
    assert [item.status for item in receipt.checkpoints] == ["ok", "ok"]
    assert len(receipt.events) == 2 and len(client.work_ids) == 1
    assert not receipt.interrupted and not receipt.cleanup_failed
    assert len(client.calls) == 2 and engine.pool.checkedout() == 0
    replay = await run(owner, actor, identity)
    assert replay.checkpoints == receipt.checkpoints and len(client.calls) == 2
    async with AsyncSessionLocal() as session:
        row = await session.get(ICloudCalendarSyncRun, identity)
        assert row.passes_created == 2 and row.status == "ok"
        assert CalendarRunContext.stored(row.run_context).attempt is None
        assert all([(await session.get(ICloudCalendarAccount, item)).sync_lease_token is None for item in ids])


async def test_second_account_provider_failure_retains_first_and_explicit_same_run_resume_skips_it():
    actor, ids, client, store, sdk, owner = await setup()
    client.failure = ("inert1@example.invalid", ICloudCalendarReauthRequired("Reconnect inert account"))
    first = await run(owner, actor)
    assert [item.status for item in first.checkpoints] == ["ok", "requires_reauth"]
    assert first.checkpoints[0].passes_created == 1 and first.checkpoints[1].passes_created == 0
    client.failure = None
    second = await run(owner, actor, first.run_id, resume=True)
    assert [item.status for item in second.checkpoints] == ["ok", "ok"]
    assert client.calls == ["inert0@example.invalid", "inert1@example.invalid", "inert1@example.invalid"]
    assert second.checkpoints[0] == first.checkpoints[0]
    assert engine.pool.checkedout() == 0


async def test_incomplete_provider_never_claims_accepted_account_counters():
    actor, _, client, store, sdk, owner = await setup(1)
    client.failure = ("inert0@example.invalid", ICloudCalendarIncomplete("Unsupported complete response"))
    receipt = await run(owner, actor)
    assert receipt.checkpoints[0].status == "incomplete" and receipt.events == ()
    assert receipt.checkpoints[0].passes_created == receipt.checkpoints[0].passes_cancelled == 0
    async with AsyncSessionLocal() as session:
        assert (await session.scalars(select(VisitorPass))).all() == []


async def test_stop_and_duplicate_admission_wait_actual_vendor_thread_cleanup_and_renew_owned_lease(monkeypatch):
    import app.services.calendar_sync_engine as module
    monkeypatch.setattr(module, "CALENDAR_RENEW_SECONDS", 0.01)
    actor, ids, client, store, sdk, owner = await setup(1)
    client.blocked = Event()
    task = asyncio.create_task(run(owner, actor))
    await asyncio.wait_for(asyncio.to_thread(client.blocked.wait, 2), 3)
    await asyncio.sleep(0.04)
    stop = asyncio.create_task(sdk.stop())
    await asyncio.sleep(0.04)
    assert not stop.done() and not task.done()
    with pytest.raises(CalendarSDKBusy):
        await run(owner, actor)
    # Renewal may use a short independent transaction. No source transaction is
    # held for the vendor thread; let an in-flight renewer settle before reading.
    async with AsyncSessionLocal() as session:
        account = await session.get(ICloudCalendarAccount, ids[0])
        assert account.sync_lease_until > datetime.now(UTC) and account.sync_lease_token is not None
    client.release.set()
    receipt = await task
    await stop
    assert receipt.interrupted and receipt.checkpoints[0].status == "interrupted"
    assert len(client.calls) == 1 and engine.pool.checkedout() == 0


async def test_no_budget_reset_between_accounts_after_delayed_dispatch_preserves_first_receipt(monkeypatch):
    actor, ids, client, store, sdk, owner = await setup()
    original_read = owner.sessions.read_session
    async def expire_second(claim, **kwargs):
        detached = await original_read(claim, **kwargs)
        if claim.account_id == ids[1]:
            kwargs["work"].deadline = monotonic() - 1
        return detached
    monkeypatch.setattr(owner.sessions, "read_session", expire_second)
    receipt = await run(owner, actor)
    assert receipt.interrupted
    assert [item.status for item in receipt.checkpoints] == ["ok", "interrupted"]
    assert client.calls == ["inert0@example.invalid"]
    assert receipt.checkpoints[0].passes_created == 1 and len(client.work_ids) == 1


async def test_unknown_account_acceptance_retains_run_and_account_fences_without_finishing_or_repeating(monkeypatch):
    actor, ids, client, store, sdk, owner = await setup(1)
    async def uncertain(*args, **kwargs):
        raise CalendarOutcomeUnknown("Read known run before any recovery")
    monkeypatch.setattr(owner.commit, "apply", uncertain)
    receipt = await run(owner, actor)
    assert receipt.uncertain_account_id == ids[0] and receipt.checkpoints == ()
    async with AsyncSessionLocal() as session:
        account = await session.get(ICloudCalendarAccount, ids[0])
        row = await session.get(ICloudCalendarSyncRun, receipt.run_id)
        assert account.sync_lease_token is not None and CalendarRunContext.stored(row.run_context).attempt is not None
        assert row.finished_at is None and row.status == "running"
    await run(owner, actor, receipt.run_id)
    assert len(client.calls) == 1


async def test_accepted_account_receipt_survives_late_run_cleanup_failure(monkeypatch):
    actor, ids, client, store, sdk, owner = await setup(1)
    async def failed_cleanup(*args):
        raise RuntimeError("inert metadata cleanup fault")
    monkeypatch.setattr(store, "finish_attempt", failed_cleanup)
    receipt = await run(owner, actor)
    assert receipt.checkpoints[0].status == "ok" and receipt.checkpoints[0].passes_created == 1
    assert receipt.cleanup_failed and engine.pool.checkedout() == 0


async def test_two_request_cancels_after_real_account_commit_preserve_receipt_through_actual_close(monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    actor, ids, client, store, sdk, owner = await setup(1)
    entered, release = asyncio.Event(), asyncio.Event()
    original_close = AsyncSession.close
    original_commit = AsyncSession.commit
    async def tagged_commit(session):
        account_effect = any(isinstance(row, ICloudCalendarSyncRun) and any(
            item.get("status") == "ok" for item in (row.account_results or []))
            for row in session.identity_map.values())
        result = await original_commit(session)
        if account_effect:
            session.info["accepted_calendar_fixture"] = True
        return result
    async def blocked_close(session):
        if session.info.get("accepted_calendar_fixture"):
            entered.set()
            await release.wait()
        return await original_close(session)
    monkeypatch.setattr(AsyncSession, "commit", tagged_commit)
    monkeypatch.setattr(AsyncSession, "close", blocked_close)
    task = asyncio.create_task(run(owner, actor))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    with pytest.raises(CalendarSDKBusy):
        await run(owner, actor)
    release.set()
    receipt = await task
    assert receipt.checkpoints[0].status == "ok" and receipt.checkpoints[0].passes_created == 1
    assert engine.pool.checkedout() == 0


async def test_owned_disconnect_waiter_stops_attempt_but_waits_for_actual_vendor_cleanup():
    actor, ids, client, store, sdk, owner = await setup(1)
    client.blocked = Event()
    disconnected = asyncio.Event()
    async def receive_disconnect():
        await disconnected.wait()
    task = asyncio.create_task(owner.run(uuid.uuid4(), origin="ui", actor="Inert Calendar Admin",
        actor_user_id=actor, execution=None, authorize=allowed, disconnect=receive_disconnect))
    await asyncio.wait_for(asyncio.to_thread(client.blocked.wait, 2), 3)
    disconnected.set()
    await asyncio.sleep(0.02)
    assert not task.done()
    with pytest.raises(CalendarSDKBusy):
        await run(owner, actor)
    client.release.set()
    receipt = await task
    assert receipt.interrupted and receipt.checkpoints[0].status == "interrupted"
    assert engine.pool.checkedout() == 0


async def prepared_workflow():
    from test_calendar_workflow_authority_persistence import attempted
    from app.models import AutomationRule
    from app.services.workflow_dispatch_ports import PreparedAutomationAction
    from app.services.workflows.automation_definition import captured_automation_context
    rule_id, _, _, execution, action = await attempted()
    async with AsyncSessionLocal() as session:
        rule = await session.get(AutomationRule, rule_id)
    return PreparedAutomationAction(action, captured_automation_context("time.every_x", {}), rule, execution)


async def test_actual_checked_action_adapter_propagates_canonical_operation_without_outer_db_checkout(monkeypatch):
    from app.services import automation_integration_actions
    from app.services.automations import AutomationService
    from app.services.icloud_calendar import ICloudCalendarService
    actor, ids, client, _, _, _ = await setup(1)
    service = ICloudCalendarService(client)
    monkeypatch.setattr(automation_integration_actions, "get_icloud_calendar_service", lambda: service)
    prepared = await prepared_workflow()
    result = (await AutomationService().dispatch_prepared_action(prepared)).as_payload()
    assert result["status"] == "success" and len(client.calls) == 1
    assert result["sync"]["id"] == prepared.execution["operation_id"]
    assert "attempt_token" not in result["sync"]["account_results"][0]
    async with AsyncSessionLocal() as session:
        row = await session.get(ICloudCalendarSyncRun, uuid.UUID(prepared.execution["operation_id"]))
        context = CalendarRunContext.stored(row.run_context)
        assert context.execution == prepared.execution and context.origin == "workflow"
    assert engine.pool.checkedout() == 0


async def test_actual_adapter_refuses_missing_execution_before_any_sdk_call(monkeypatch):
    from app.services import automation_integration_actions
    from app.services.automations import AutomationService
    from app.services.icloud_calendar import ICloudCalendarService
    actor, ids, client, _, _, _ = await setup(1)
    service = ICloudCalendarService(client)
    monkeypatch.setattr(automation_integration_actions, "get_icloud_calendar_service", lambda: service)
    prepared = await prepared_workflow()
    async with AsyncSessionLocal() as session:
        result = await AutomationService()._execute_action(session, dict(prepared.action), prepared.context,
            rule=prepared.rule, execution=None)
    assert result["status"] == "failed" and result["reason"] == "calendar_authority_unavailable"
    assert client.calls == [] and engine.pool.checkedout() == 0


async def test_actual_dispatcher_whole_action_timeout_drains_sdk_and_keeps_partial_calendar_receipts(monkeypatch):
    from app.models import AutomationRule, AutomationRun
    from app.services import automation_dispatch, automation_integration_actions
    from app.services.automation_intake import reserve_occurrence
    from app.services.automations import AutomationService
    from app.services.icloud_calendar import ICloudCalendarService
    from app.services.workflows.automation_definition import captured_automation_context
    actor, ids, client, _, _, _ = await setup(2)
    client.blocked, client.blocked_on = Event(), "inert1@example.invalid"
    calendar = ICloudCalendarService(client)
    monkeypatch.setattr(automation_integration_actions, "get_icloud_calendar_service", lambda: calendar)
    from types import SimpleNamespace
    actual_timeout = asyncio.timeout
    active_timeout = []
    def captured_timeout(seconds):
        assert seconds == 120
        value = actual_timeout(seconds)
        active_timeout.append(value)
        return value
    # Exercise the production dispatcher's actual timeout context at a
    # deterministic stage boundary; no guessed setup-duration race.
    monkeypatch.setattr(automation_dispatch, "asyncio", SimpleNamespace(**(vars(asyncio) | {"timeout": captured_timeout})))
    action = {"id": "inert-calendar-action", "type": "integration.icloud_calendar.sync",
        "config": {"provider": "icloud_calendar", "action": "sync_calendars"}}
    rule = AutomationRule(name="Inert whole-action timeout", is_active=True,
        triggers=[{"id": "clock", "type": "time.every_x", "config": {"interval": 1, "unit": "minutes"}}],
        trigger_keys=["time.every_x"], conditions=[], actions=[action])
    async with AsyncSessionLocal() as session:
        session.add(rule)
        await session.flush()
        identity = await reserve_occurrence(session, rule, captured_automation_context("time.every_x", {}),
            origin_kind="synthetic", origin_id=str(uuid.uuid4()), actor="Automation Engine", source="inert")
        await session.commit()
    automation = AutomationService()
    actual_dispatch = automation.dispatch_prepared_action
    async def checked_dispatch(prepared):
        from app.services.workflows.execution_contracts import AutomationExecution
        assert set(prepared.execution) <= set(AutomationExecution.__annotations__)
        assert {"run_id", "claim_token", "index", "operation_id", "idempotency_key"} <= set(prepared.execution)
        return await actual_dispatch(prepared)
    monkeypatch.setattr(automation, "dispatch_prepared_action", checked_dispatch)
    task = asyncio.create_task(automation.dispatcher.run_once(identity))
    reached = await asyncio.wait_for(asyncio.to_thread(client.blocked.wait, 2), 3)
    if not reached:
        async with AsyncSessionLocal() as session:
            diagnostic = await session.get(AutomationRun, identity)
            pytest.fail(f"Dispatcher stopped before second provider: done={task.done()}, calls={client.calls}, plan={diagnostic.action_plan}")
    assert len(active_timeout) == 1
    active_timeout[0].reschedule(asyncio.get_running_loop().time() - 1)
    await asyncio.sleep(0.02)
    assert not task.done()
    with pytest.raises(CalendarSDKBusy):
        await run(calendar._sync_engine, actor)
    client.release.set()
    assert await task
    async with AsyncSessionLocal() as session:
        row = await session.get(AutomationRun, identity)
        assert row.action_plan[0]["state"] == "unknown"
        operation = row.action_plan[0]["operation_id"]
        sync = await session.get(ICloudCalendarSyncRun, uuid.UUID(operation))
        assert [item["status"] for item in sync.account_results] == ["ok", "interrupted"]
        assert sync.passes_created == 1
    assert engine.pool.checkedout() == 0
