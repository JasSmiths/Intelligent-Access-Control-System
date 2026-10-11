"""Run/account ownership only; current authority and sync application are separate gates."""
from test_recovery_boundaries import isolated_resources as isolated_resources

import uuid
from datetime import UTC, datetime, timedelta
from time import monotonic

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.db.session import AsyncSessionLocal, engine
from app.models import ICloudCalendarAccount, ICloudCalendarSyncRun, SystemSetting, User
from app.models.enums import UserRole
from app.modules.icloud_calendar.contracts import CalendarWork
from app.services.calendar_sync_contracts import CalendarOutcomeUnknown, CalendarOwnershipLost, CalendarRunConflict, CalendarRunContext
from app.services.calendar_sync_store import CalendarSyncStore
from app.services.icloud_calendar_sdk import CalendarSDKBusy

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def calendar_resources(isolated_resources):
    yield
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE system_settings,icloud_calendar_accounts,icloud_calendar_sync_runs CASCADE"))
        await session.commit()


def work():
    return CalendarWork(monotonic() + 120)


async def allowed(session, final):
    # Inert storage-boundary port; real UI/workflow authority is NOT proved here.
    assert engine.pool.checkedout() == 1


async def setup_accounts(count=2):
    actor = User(id=uuid.uuid4(), username="inert-calendar-admin", full_name="Inert Calendar Admin",
        password_hash="unusable", role=UserRole.ADMIN, is_active=True)
    accounts = [ICloudCalendarAccount(id=uuid.uuid4(), apple_id=f"inert{index}@example.invalid",
        encrypted_session_bundle="synthetic-ciphertext-not-used-by-storage") for index in range(count)]
    async with AsyncSessionLocal() as session:
        session.add_all([actor, *accounts])
        await session.commit()
    return actor.id, [item.id for item in accounts]


async def admit(store, actor, run_id=None):
    return await store.admit(run_id or uuid.uuid4(), origin="ui", actor_user_id=actor,
        execution=None, authorize=allowed, work=work())


async def test_known_ui_uuid_replay_retains_original_manifest_horizon_and_refuses_other_origin():
    actor, ids = await setup_accounts()
    store = CalendarSyncStore()
    first = await admit(store, actor)
    assert first.proceed and first.context.account_ids == tuple(ids)
    async with AsyncSessionLocal() as session:
        session.add(ICloudCalendarAccount(id=uuid.uuid4(), apple_id="later@example.invalid"))
        await session.commit()
    replay = await admit(store, actor, first.run_id)
    assert not replay.proceed and replay.context == first.context
    execution = {"run_id": str(uuid.uuid4()), "claim_token": str(uuid.uuid4()), "index": 0,
                 "operation_id": str(first.run_id), "idempotency_key": "inert:operation:0"}
    with pytest.raises(CalendarRunConflict, match="another request or origin"):
        await store.admit(first.run_id, origin="workflow", actor_user_id=actor,
                          execution=execution, authorize=allowed, work=work())
    assert engine.pool.checkedout() == 0


async def test_workflow_operation_binding_replay_and_changed_execution_fail_before_provider():
    actor, _ = await setup_accounts()
    store, identity = CalendarSyncStore(), uuid.uuid4()
    execution = {"run_id": str(uuid.uuid4()), "claim_token": str(uuid.uuid4()), "index": 2,
                 "operation_id": str(identity), "idempotency_key": "inert:operation:2"}
    first = await store.admit(identity, origin="workflow", actor_user_id=actor,
        execution=execution, authorize=allowed, work=work())
    repeated = await store.admit(identity, origin="workflow", actor_user_id=actor,
        execution=execution, authorize=allowed, work=work())
    assert first.proceed and not repeated.proceed
    with pytest.raises(CalendarRunConflict, match="another request or origin"):
        await store.admit(identity, origin="workflow", actor_user_id=actor,
            execution={**execution, "claim_token": str(uuid.uuid4())}, authorize=allowed, work=work())
    with pytest.raises(CalendarRunConflict, match="operation ID"):
        await store.admit(uuid.uuid4(), origin="workflow", actor_user_id=actor,
            execution=execution, authorize=allowed, work=work())


async def test_between_accounts_run_lease_blocks_resume_and_old_attempt_cannot_clear_takeover():
    actor, ids = await setup_accounts()
    store = CalendarSyncStore()
    first = await admit(store, actor)
    token = first.context.attempt.token
    with pytest.raises(CalendarSDKBusy, match="active owner"):
        await store.resume(first.run_id, recovery_actor_user_id=actor, authorize=allowed, work=work())
    # Model a dead owner's lease expiry, not a normal cleanup release.
    async with AsyncSessionLocal() as session:
        row = await session.get(ICloudCalendarSyncRun, first.run_id)
        context = CalendarRunContext.stored(row.run_context)
        row.run_context = context.model_copy(update={"attempt": context.attempt.model_copy(update={
            "lease_until": datetime.now(UTC) - timedelta(seconds=1)})}).model_dump(mode="json")
        await session.commit()
    resumed = await store.resume(first.run_id, recovery_actor_user_id=actor, authorize=allowed, work=work())
    assert resumed.context.coverage == first.context.coverage
    assert resumed.context.account_ids == first.context.account_ids
    assert resumed.context.attempt.token != token
    assert resumed.context.attempt.recovery_actor_user_id == actor
    assert not await store.renew(first.run_id, token, None)
    with pytest.raises(CalendarOwnershipLost):
        await store.claim_account(first, ids[0], authorize=allowed, work=work())
    await store.finish_attempt(first.run_id, token)
    async with AsyncSessionLocal() as session:
        row = await session.get(ICloudCalendarSyncRun, first.run_id)
        assert CalendarRunContext.stored(row.run_context).attempt == resumed.context.attempt


async def test_account_claim_is_busy_for_another_run_and_fresh_resume_uses_current_session_generation():
    actor, ids = await setup_accounts(1)
    store = CalendarSyncStore()
    first, second = await admit(store, actor), await admit(store, actor)
    claim = await store.claim_account(first, ids[0], authorize=allowed, work=work())
    assert claim.generation == 1
    assert await store.claim_account(second, ids[0], authorize=allowed, work=work()) is None
    async with AsyncSessionLocal() as session:
        row = await session.get(ICloudCalendarSyncRun, second.run_id)
        assert row.account_results[0]["status"] == "busy" and row.passes_created == 0
        account = await session.get(ICloudCalendarAccount, ids[0])
        assert account.sync_lease_token == claim.account_token
    await store.release_account(claim)
    await store.finish_attempt(second.run_id, second.context.attempt.token)
    async with AsyncSessionLocal() as session:
        account = await session.get(ICloudCalendarAccount, ids[0])
        account.encrypted_session_bundle = "new-explicit-reconnect-session"
        account.sync_generation += 1
        await session.commit()
    resumed = await store.resume(second.run_id, recovery_actor_user_id=actor, authorize=allowed, work=work())
    replacement = await store.claim_account(resumed, ids[0], authorize=allowed, work=work())
    assert replacement.generation == 3 and replacement.encrypted_session_bundle == "new-explicit-reconnect-session"
    assert not await store.renew(first.run_id, first.context.attempt.token, claim)
    await store.release_account(claim)
    async with AsyncSessionLocal() as session:
        assert (await session.get(ICloudCalendarAccount, ids[0])).sync_lease_token == replacement.account_token


@pytest.mark.parametrize("change", ["removed", "timezone", "missing_session"])
async def test_current_account_or_policy_admission_is_explicit_without_fake_accepted_counts(change):
    actor, ids = await setup_accounts(1)
    store = CalendarSyncStore()
    first = await admit(store, actor)
    async with AsyncSessionLocal() as session:
        account = await session.get(ICloudCalendarAccount, ids[0])
        if change == "removed":
            account.is_active = False
        elif change == "missing_session":
            account.encrypted_session_bundle = None
        else:
            timezone = "UTC" if first.context.site_timezone != "UTC" else "Europe/London"
            session.add(SystemSetting(key="site_timezone", category="general", value={"value": timezone},
                is_secret=False, description="inert changed calendar policy"))
        await session.commit()
    assert await store.claim_account(first, ids[0], authorize=allowed, work=work()) is None
    async with AsyncSessionLocal() as session:
        row = await session.get(ICloudCalendarSyncRun, first.run_id)
        expected = {"removed": "removed", "timezone": "stale_configuration", "missing_session": "requires_reauth"}[change]
        assert row.account_results[0]["status"] == expected
        assert row.passes_created == row.passes_updated == row.passes_cancelled == 0
        assert (await session.get(ICloudCalendarAccount, ids[0])).sync_lease_token is None
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("expiring", ["run", "account"])
async def test_slow_renewal_flush_cannot_revive_original_expired_lease(expiring, monkeypatch):
    import asyncio
    from sqlalchemy.ext.asyncio import AsyncSession

    actor, ids = await setup_accounts(1)
    store = CalendarSyncStore()
    admitted = await admit(store, actor)
    claim = await store.claim_account(admitted, ids[0], authorize=allowed, work=work())
    expiry = datetime.now(UTC) + timedelta(milliseconds=150)
    async with AsyncSessionLocal() as session:
        if expiring == "run":
            row = await session.get(ICloudCalendarSyncRun, admitted.run_id)
            context = CalendarRunContext.stored(row.run_context)
            row.run_context = context.model_copy(update={"attempt": context.attempt.model_copy(
                update={"lease_until": expiry})}).model_dump(mode="json")
        else:
            (await session.get(ICloudCalendarAccount, ids[0])).sync_lease_until = expiry
        await session.commit()
    original_flush = AsyncSession.flush

    async def slow_flush(session, *args, **kwargs):
        await original_flush(session, *args, **kwargs)
        await asyncio.sleep(0.2)

    monkeypatch.setattr(AsyncSession, "flush", slow_flush)
    assert not await store.renew(admitted.run_id, admitted.context.attempt.token, claim)
    monkeypatch.setattr(AsyncSession, "flush", original_flush)
    async with AsyncSessionLocal() as session:
        row = await session.get(ICloudCalendarSyncRun, admitted.run_id)
        account = await session.get(ICloudCalendarAccount, ids[0])
        actual = CalendarRunContext.stored(row.run_context).attempt.lease_until if expiring == "run" else account.sync_lease_until
        assert actual == expiry
    assert engine.pool.checkedout() == 0


async def test_account_claim_rolls_back_if_run_lease_expires_during_flush(monkeypatch):
    import asyncio
    from sqlalchemy.ext.asyncio import AsyncSession

    actor, ids = await setup_accounts(1)
    store = CalendarSyncStore()
    admitted = await admit(store, actor)
    async with AsyncSessionLocal() as session:
        row = await session.get(ICloudCalendarSyncRun, admitted.run_id)
        context = CalendarRunContext.stored(row.run_context)
        row.run_context = context.model_copy(update={"attempt": context.attempt.model_copy(
            update={"lease_until": datetime.now(UTC) + timedelta(milliseconds=150)})}).model_dump(mode="json")
        await session.commit()
    original_flush = AsyncSession.flush

    async def slow_flush(session, *args, **kwargs):
        await original_flush(session, *args, **kwargs)
        await asyncio.sleep(0.2)

    monkeypatch.setattr(AsyncSession, "flush", slow_flush)
    with pytest.raises(CalendarOwnershipLost):
        await store.claim_account(admitted, ids[0], authorize=allowed, work=work())
    monkeypatch.setattr(AsyncSession, "flush", original_flush)
    async with AsyncSessionLocal() as session:
        account = await session.get(ICloudCalendarAccount, ids[0])
        assert account.sync_generation == 0 and account.sync_lease_token is None


@pytest.mark.parametrize("blocked_query", [False, True])
async def test_store_cancellation_drains_actual_session_close_without_disabling_query_cancel(blocked_query):
    import asyncio
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    actor, _ = await setup_accounts(1)
    close_entered, release_close = asyncio.Event(), asyncio.Event()
    query_started = asyncio.Event()

    class CloseBarrierSession(AsyncSession):
        async def close(self):
            close_entered.set()
            await release_close.wait()
            await super().close()

    async def authorize(session, final):
        if blocked_query:
            query_started.set()
            await session.execute(text("SELECT pg_sleep(30)"))
        return None

    store = CalendarSyncStore(async_sessionmaker(engine, class_=CloseBarrierSession, expire_on_commit=False))
    identity = uuid.uuid4() if blocked_query else (await admit(CalendarSyncStore(), actor)).run_id
    task = asyncio.create_task(store.admit(identity, origin="ui", actor_user_id=actor,
        execution=None, authorize=authorize, work=work()))
    if blocked_query:
        await asyncio.wait_for(query_started.wait(), 1)
        # Enter the native asyncpg query; caller cancellation must reach SQL,
        # rather than shielding the whole store operation for its30s duration.
        await asyncio.sleep(0.05)
        task.cancel()
    await asyncio.wait_for(close_entered.wait(), 1)
    if not blocked_query:
        assert engine.pool.checkedout() == 1
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    assert not task.done()
    release_close.set()
    expected_error = CalendarOutcomeUnknown if blocked_query else asyncio.CancelledError
    with pytest.raises(expected_error) as failure:
        await asyncio.wait_for(task, 1)
    if blocked_query:
        # Native query cancellation invalidates the original connection, so
        # local reset cannot prove rollback after tracked database work.
        assert isinstance(failure.value.__cause__, asyncio.CancelledError)
    assert engine.pool.checkedout() == 0
    async with AsyncSessionLocal() as session:
        durable = await session.get(ICloudCalendarSyncRun, identity)
        assert (durable is None) == blocked_query
