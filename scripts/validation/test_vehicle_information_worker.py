"""Fresh durable worker fences in an explicitly disposable PostgreSQL namespace."""
import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, text
from test_recovery_boundaries import isolated_resources as isolated_resources
from test_vehicle_information import lookup

from app.core.task_lifecycle import drain_owned
from app.db.session import AsyncSessionLocal, engine
from app.models import AccessEvent, VehicleInformationJob
from app.models.enums import AccessDecision, AccessDirection
from app.services import vehicle_information_jobs as jobs
from app.services.access.pipeline_contracts import PipelineLeaseLost
from app.services.access.pipeline_store import database_now

pytestmark = pytest.mark.asyncio


async def seed(count=1, *, offset=0, accepted=True):
    async with AsyncSessionLocal() as session:
        now = await database_now(session)
        identities = []
        for index in range(count):
            identity = uuid.uuid4()
            plate = "SYN" + identity.hex[:12].upper()
            origin = now + timedelta(seconds=offset)
            session.add(AccessEvent(id=identity, registration_number=plate,
                direction=AccessDirection.ENTRY, decision=AccessDecision.GRANTED,
                confidence=1, source="synthetic", occurred_at=origin, raw_payload={}))
            await session.flush()
            session.add(VehicleInformationJob(event_id=identity, registration_number=plate,
                created_at=now+timedelta(microseconds=index), origin_at=origin,
                deadline=origin+timedelta(seconds=300), readiness="committed_decision", status="queued",
                accepted_result_revision=1 if accepted else 0,
                accepted_result_at=origin if accepted else None,
                accepted_result={"make":"Retained", "colour":"Blue"} if accepted else None))
            identities.append(identity)
        await session.commit()
        return identities


async def claimed():
    identity = (await seed())[0]
    worker = jobs.VehicleInformationWorker()
    job = (await worker._claim())[0]
    claim, _ = await worker._prepare(*job)
    return worker, identity, job, claim


async def assert_retained(identity):
    async with AsyncSessionLocal() as session:
        job = await session.get(VehicleInformationJob, identity)
        assert job.accepted_result_revision == 1
        assert job.accepted_result == {"make":"Retained", "colour":"Blue"}
        assert job.accepted_result_at == job.origin_at
        return job


@pytest.mark.parametrize("capacity", [0, 1, 2])
async def test_metadata_scan_never_leases_beyond_actual_free_capacity(capacity):
    await seed(5)
    worker = jobs.VehicleInformationWorker()
    picked = await worker._claim(capacity)
    assert len(picked) == capacity
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(VehicleInformationJob)
                                    .where(VehicleInformationJob.status == "processing")) == capacity
        assert await session.scalar(select(func.count()).select_from(VehicleInformationJob)
                                    .where(VehicleInformationJob.status == "queued")) == 5-capacity


async def test_competing_process_owners_claim_disjoint_slots():
    await seed(6)
    first, second = await asyncio.gather(jobs.VehicleInformationWorker()._claim(),
                                         jobs.VehicleInformationWorker()._claim())
    assert len(first) == len(second) == 2
    assert not {row[0] for row in first}.intersection(row[0] for row in second)
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(VehicleInformationJob)
                                    .where(VehicleInformationJob.status == "processing")) == 4


async def test_terminal_candidate_page_continues_to_ready_work_without_exhausting_backlog(monkeypatch):
    expired = await seed(32, offset=-301)
    ready = (await seed())[0]
    entered = asyncio.Event()
    worker = jobs.VehicleInformationWorker()

    async def process(identity, *_args):
        assert identity == ready
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "_process", process)
    await worker.start()
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert len(worker._operations) == 1
        async with AsyncSessionLocal() as session:
            assert await session.scalar(select(func.count()).select_from(VehicleInformationJob)
                                        .where(VehicleInformationJob.event_id.in_(expired),
                                               VehicleInformationJob.status == "expired")) == 32
        await assert_retained(expired[0])
    finally:
        await worker.stop()


async def test_queued_retry_due_and_original_deadline_are_preserved():
    identity = (await seed())[0]
    async with AsyncSessionLocal() as session:
        job = await session.get(VehicleInformationJob, identity)
        deadline = job.deadline
        job.lease_until = await database_now(session) + timedelta(seconds=30)
        await session.commit()
    worker = jobs.VehicleInformationWorker()
    assert not await worker._claim()
    async with AsyncSessionLocal() as session:
        job = await session.get(VehicleInformationJob, identity)
        job.lease_until = await database_now(session) - timedelta(seconds=1)
        await session.commit()
    assert len(await worker._claim(1)) == 1
    job = await assert_retained(identity)
    assert job.deadline == deadline


@pytest.mark.parametrize("operation", ["renew", "fail"])
async def test_lock_wait_past_original_lease_cannot_renew_or_write_failure(operation):
    worker, identity, item, claim = await claimed()
    async with AsyncSessionLocal() as session:
        job = await session.get(VehicleInformationJob, identity)
        old_until = await database_now(session) + timedelta(seconds=.3)
        job.lease_until = old_until
        await session.commit()
    async with AsyncSessionLocal() as locker, AsyncSessionLocal() as observer:
        await locker.get(VehicleInformationJob, identity, with_for_update=True)
        transaction_time = await locker.scalar(select(func.now()))
        action = worker._renew_claim(claim) if operation == "renew" else worker._fail(*item, RuntimeError("synthetic"), claim)
        task = asyncio.create_task(action)
        try:
            async def blocked():
                while True:
                    # PostgreSQL caches activity snapshots for this observer
                    # transaction; each poll must actually observe the contender.
                    await observer.execute(text("SELECT pg_stat_clear_snapshot()"))
                    waiting = await observer.scalar(text("""SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                        WHERE datname=current_database() AND pid<>pg_backend_pid()
                        AND wait_event_type='Lock' AND query LIKE '%vehicle_information_jobs%')"""))
                    if waiting:
                        break
                    await asyncio.sleep(.01)
            await asyncio.wait_for(blocked(), 3)
            async def expired():
                while await database_now(observer) <= old_until:
                    await asyncio.sleep(.01)
            await asyncio.wait_for(expired(), 3)
            assert transaction_time < old_until
            await locker.rollback()
            if operation == "renew":
                with pytest.raises(PipelineLeaseLost):
                    await asyncio.wait_for(task, 3)
            else:
                await asyncio.wait_for(task, 3)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    saved = await assert_retained(identity)
    assert saved.lease_until == old_until and saved.lease_token == item[1]
    assert saved.status == "processing"


@pytest.mark.parametrize("operation", ["renew", "fail"])
async def test_all_worker_writes_flush_before_final_fresh_clock_and_expiry_rolls_back(monkeypatch, operation):
    worker, identity, item, claim = await claimed()
    async with AsyncSessionLocal() as session:
        old = (await session.get(VehicleInformationJob, identity)).lease_until
    calls = 0

    async def clock(session):
        nonlocal calls
        calls += 1
        if calls == 2:
            assert not session.dirty and not session.new and not session.deleted
            return old + timedelta(seconds=1)
        return await database_now(session)

    monkeypatch.setattr(jobs, "database_now", clock)
    with pytest.raises(PipelineLeaseLost):
        if operation == "renew":
            await worker._renew_claim(claim)
        else:
            await worker._fail(*item, RuntimeError("synthetic"), claim)
    assert calls == 2
    saved = await assert_retained(identity)
    assert saved.status == "processing" and saved.lease_until == old and saved.lease_token == item[1]


async def test_failure_after_takeover_cannot_overwrite_new_claim_or_accepted_projection():
    old_worker, identity, original, claim = await claimed()
    async with AsyncSessionLocal() as session:
        job = await session.get(VehicleInformationJob, identity)
        job.lease_until = await database_now(session)-timedelta(seconds=1)
        await session.commit()
    replacement = (await jobs.VehicleInformationWorker()._claim())[0]
    assert replacement[1] != original[1]
    await old_worker._fail(*original, RuntimeError("late old failure"), claim)
    saved = await assert_retained(identity)
    assert saved.lease_token == replacement[1] and saved.status == "processing"


async def test_lost_renewal_retains_actual_provider_cleanup_and_rejects_late_result(monkeypatch):
    worker, identity, original, _ = await claimed()
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def provider(plate, **_kwargs):
        assert engine.pool.checkedout() == 0
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning.set()
            await drain_owned(release.wait())
            return lookup(plate)

    monkeypatch.setattr(jobs, "get_vehicle_information_service", lambda: SimpleNamespace(lookup=provider))
    monkeypatch.setattr(jobs, "INFORMATION_RENEWAL_SECONDS", .01)
    task = asyncio.create_task(worker._process(*original))
    await entered.wait()
    token = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        job = await session.get(VehicleInformationJob, identity)
        job.lease_token = token
        await session.commit()
    await cleaning.wait()
    assert not task.done()
    release.set()
    await asyncio.wait_for(task, 3)
    saved = await assert_retained(identity)
    assert saved.lease_token == token and saved.status == "processing"
    async with AsyncSessionLocal() as session:
        event = await session.get(AccessEvent, identity)
        assert "vehicle_information" not in event.raw_payload


async def test_current_registration_change_prevents_any_provider_call(monkeypatch):
    worker, identity, original, _ = await claimed()
    facade = SimpleNamespace(lookup=AsyncMock(side_effect=AssertionError("provider must not run")))
    monkeypatch.setattr(jobs, "get_vehicle_information_service", lambda: facade)
    async with AsyncSessionLocal() as session:
        event = await session.get(AccessEvent, identity)
        event.registration_number = "CHANGED123"
        await session.commit()
    await worker._process(*original)
    facade.lookup.assert_not_awaited()
    saved = await assert_retained(identity)
    assert saved.outcome == "registration_changed" and saved.status == "failed"


async def test_original_deadline_expired_before_prepare_never_calls_provider(monkeypatch):
    identity = (await seed(offset=-301))[0]
    token = uuid.uuid4()
    facade = SimpleNamespace(lookup=AsyncMock(side_effect=AssertionError("expired provider")))
    monkeypatch.setattr(jobs, "get_vehicle_information_service", lambda: facade)
    async with AsyncSessionLocal() as session:
        job = await session.get(VehicleInformationJob, identity)
        job.status, job.lease_token = "processing", token
        job.lease_until = await database_now(session)+timedelta(seconds=120)
        plate = job.registration_number
        await session.commit()
    await jobs.VehicleInformationWorker()._process(identity, token, plate)
    facade.lookup.assert_not_awaited()
    saved = await assert_retained(identity)
    assert saved.status == "expired" and saved.outcome == "deadline_expired"


@pytest.mark.parametrize("malformed", ["missing_origin", "deadline_mismatch", "readiness"])
async def test_malformed_original_authority_holds_without_repair_or_receipt_loss(malformed):
    identity = (await seed())[0]
    async with AsyncSessionLocal() as session:
        job = await session.get(VehicleInformationJob, identity)
        accepted_at = job.accepted_result_at
        if malformed == "missing_origin":
            job.origin_at = None
        elif malformed == "deadline_mismatch":
            job.deadline += timedelta(seconds=1)
        else:
            job.readiness = "legacy"
        deadline = job.deadline
        await session.commit()
    assert not await jobs.VehicleInformationWorker()._claim()
    async with AsyncSessionLocal() as session:
        job = await session.get(VehicleInformationJob, identity)
        assert job.status == job.readiness == "held" and job.outcome == "original_origin_unavailable"
        assert job.deadline == deadline and job.accepted_result_at == accepted_at
        assert job.accepted_result_revision == 1 and job.accepted_result == {"make":"Retained", "colour":"Blue"}


@pytest.mark.parametrize("cancel", ["deadline", "stop"])
async def test_preparation_blocked_on_event_lock_cancels_active_sql_without_provider_or_new_budget(monkeypatch, cancel):
    worker, identity, item, claim = await claimed()
    facade = SimpleNamespace(lookup=AsyncMock(side_effect=AssertionError("no provider while preparation is blocked")))
    monkeypatch.setattr(jobs, "get_vehicle_information_service", lambda: facade)
    monkeypatch.setattr(jobs, "INFORMATION_TIMEOUT_SECONDS", .5)
    async with AsyncSessionLocal() as locker, AsyncSessionLocal() as observer:
        await locker.get(AccessEvent, identity, with_for_update=True)
        call = asyncio.create_task(worker._process(*item))
        try:
            async def blocked():
                while True:
                    await observer.execute(text("SELECT pg_stat_clear_snapshot()"))
                    if await observer.scalar(text("""SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                        WHERE datname=current_database() AND pid<>pg_backend_pid()
                        AND wait_event_type='Lock' AND query LIKE '%access_events%')""")):
                        return
                    await asyncio.sleep(.01)
            await asyncio.wait_for(blocked(), .4)
            if cancel == "stop":
                call.cancel()
                call.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(call, 2)
            else:
                await asyncio.wait_for(call, 2)
            # The event lock is STILL held. The active preparation SQL was
            # cancelled rather than passively drained until lock release.
            facade.lookup.assert_not_awaited()
            saved = await observer.get(VehicleInformationJob, identity)
            assert saved.origin_at == claim.origin_at and saved.deadline == claim.deadline
            assert saved.accepted_result_revision == 1
            assert saved.status == ("failed" if cancel == "deadline" else "processing")
        finally:
            await locker.rollback()
            if not call.done():
                call.cancel()
                await asyncio.gather(call, return_exceptions=True)


@pytest.mark.parametrize("boundary", ["claim", "renew", "failure"])
async def test_real_journal_session_normal_close_survives_first_and_repeated_cancel(monkeypatch, boundary):
    if boundary == "claim":
        identity = (await seed())[0]
        worker = jobs.VehicleInformationWorker()
        action = worker._claim
    else:
        worker, identity, item, claim = await claimed()
        action = (lambda: worker._renew_claim(claim)) if boundary == "renew" else (
            lambda: worker._fail(*item, RuntimeError("synthetic failure"), claim))
    entered, release, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    def session_factory():
        session = AsyncSessionLocal()
        close = session.close
        async def blocked_close():
            entered.set()
            await release.wait()
            await close()
            settled.set()
        monkeypatch.setattr(session, "close", blocked_close)
        return session

    monkeypatch.setattr(jobs, "AsyncSessionLocal", session_factory)
    call = asyncio.create_task(action())
    await asyncio.wait_for(entered.wait(), 2)
    call.cancel()
    call.cancel()
    try:
        await asyncio.sleep(.03)
        assert not call.done() and not settled.is_set()
        saved = await assert_retained(identity)
        assert saved.status == ("failed" if boundary == "failure" else "processing")
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert settled.is_set()


async def test_actual_result_commit_and_session_close_remain_owned_on_cancellation(monkeypatch):
    worker, identity, item, _ = await claimed()
    facade = SimpleNamespace(lookup=AsyncMock(return_value=lookup(item[2])))
    monkeypatch.setattr(jobs, "get_vehicle_information_service", lambda: facade)
    entered, release, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    sessions = 0

    def session_factory():
        nonlocal sessions
        sessions += 1
        session = AsyncSessionLocal()
        if sessions == 2:  # Preparation then the actual result transaction.
            close = session.close
            async def blocked_close():
                entered.set()
                await release.wait()
                await close()
                settled.set()
            monkeypatch.setattr(session, "close", blocked_close)
        return session

    monkeypatch.setattr(jobs, "AsyncSessionLocal", session_factory)
    call = asyncio.create_task(worker._process(*item))
    await asyncio.wait_for(entered.wait(), 3)
    async with AsyncSessionLocal() as observer:
        saved = await observer.get(VehicleInformationJob, identity)
        assert saved.status == "completed" and saved.accepted_result_revision == 2
    call.cancel()
    call.cancel()
    try:
        await asyncio.sleep(.03)
        assert not call.done() and not settled.is_set()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert settled.is_set()
    facade.lookup.assert_awaited_once()
