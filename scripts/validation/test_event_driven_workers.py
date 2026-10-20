"""Commit-coupled wakes and actual durable workers in isolated PostgreSQL.

The inherited namespace/socket guard runs before application imports. Listener,
producer and observer use independent database connections; providers stay inert.
"""
from test_recovery_boundaries import isolated_resources as isolated_resources
from test_idle_background_work import statements

import asyncio
import os
from contextlib import asynccontextmanager

import asyncpg
import pytest
from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.models import AutomationRule
from app.services import automations, vehicle_information_jobs
from app.services.automation_execution import AutomationRunStore
from app.services.durable_work import CHANNEL, DurableWorkListener, WorkReadiness, WorkTopic, notify_work, work_wake

pytestmark = pytest.mark.asyncio


async def until(check, timeout=8):
    async with asyncio.timeout(timeout):
        while not await check():
            await asyncio.sleep(.01)


@asynccontextmanager
async def listening(*, reconnect_gate=None):
    connections = []
    async def connect():
        if connections and reconnect_gate is not None:
            await reconnect_gate.wait()
        connection = await asyncpg.connect(os.environ["IACS_DATABASE_URL"].replace("+asyncpg", ""), timeout=5)
        connections.append(connection)
        return connection
    listener = DurableWorkListener(connect)
    await listener.start()
    try:
        assert listener.diagnostics()["connected"]
        yield listener, connections
    finally:
        await listener.stop()
        assert all(connection.is_closed() for connection in connections)


async def scalar(query, **parameters):
    async with AsyncSessionLocal() as session:
        return await session.scalar(text(query), parameters)


async def rule(session, *, due=False):
    row = AutomationRule(name="Synthetic committed wake", is_active=due,
        triggers=[], trigger_keys=[], conditions=[], actions=[])
    session.add(row)
    await session.flush()
    return row


async def reserve_runs(session, count):
    owner = await rule(session)
    store = AutomationRunStore()
    return [await store.reserve(session, rule_id=owner.id, trigger_key="synthetic.wake",
        occurrence=f"synthetic:{owner.id}:{index}", context={"version": 1}, planned=[],
        trigger_payload={}, actor="Synthetic", source="synthetic") for index in range(count)]


async def test_origin_transaction_notifies_only_after_commit_and_rollback_is_silent():
    async with listening() as (listener, connections):
        observer = await asyncpg.connect(os.environ["IACS_DATABASE_URL"].replace("+asyncpg", ""), timeout=5)
        try:
            wake = work_wake(WorkTopic.AUTOMATION)
            before = wake.generation
            async with AsyncSessionLocal() as session:
                identities = await reserve_runs(session, 1)
                # The real store owns notification; no separate notify_work here.
                assert not await observer.fetchval("SELECT EXISTS(SELECT 1 FROM automation_runs WHERE id=$1)", identities[0])
                await asyncio.sleep(.05)
                assert wake.generation == before
                await session.rollback()
            await asyncio.sleep(.05)
            assert wake.generation == before
            async with AsyncSessionLocal() as session:
                identities = await reserve_runs(session, 1)
                await session.commit()
            async def delivered():
                return wake.generation != before
            await until(delivered)
            assert await observer.fetchval("SELECT EXISTS(SELECT 1 FROM automation_runs WHERE id=$1)", identities[0])
            assert connections[0].get_server_pid() != observer.get_server_pid()
            assert listener.diagnostics()["hints"] >= 1
        finally:
            await observer.close()


async def test_commit_between_readiness_and_wait_is_not_lost():
    async with listening():
        wake = work_wake(WorkTopic.AUTOMATION)
        generation = wake.generation
        pending = await AutomationRunStore().readiness()
        assert not pending.ready
        async with AsyncSessionLocal() as session:
            await reserve_runs(session, 1)
            await session.commit()
        async def delivered():
            return wake.generation != generation
        await until(delivered)
        # A healthy latch would otherwise wait sixty seconds on this empty probe.
        await asyncio.wait_for(wake.wait(generation, pending, fallback_seconds=2), .5)
        assert (await AutomationRunStore().readiness()).ready


async def test_disconnect_uses_fast_fallback_and_reconnect_catches_committed_gap():
    gate = asyncio.Event()
    async with listening(reconnect_gate=gate) as (listener, connections):
        wake = work_wake(WorkTopic.AUTOMATION)
        connections[0].terminate()
        async def disconnected():
            return not wake.connected
        await until(disconnected)
        async with AsyncSessionLocal() as session:
            await reserve_runs(session, 1)
            await session.commit()
        generation = wake.generation
        await asyncio.wait_for(wake.wait(generation, WorkReadiness(), fallback_seconds=.03), .5)
        gate.set()
        async def connected():
            return listener.diagnostics()["connected"] and len(connections) == 2
        await until(connected)
        assert wake.generation != generation
        assert (await AutomationRunStore().readiness()).ready


async def test_future_deadline_wakes_without_a_second_hint():
    async with listening():
        async with AsyncSessionLocal() as session:
            owner = await rule(session, due=True)
            await session.execute(text("UPDATE automation_rules SET next_run_at=clock_timestamp()+interval '.15 seconds' WHERE id=:id"), {"id":owner.id})
            await session.commit()
        wake = work_wake(WorkTopic.AUTOMATION_CLOCK)
        pending = await automations.AutomationService().time_intake.readiness()
        assert not pending.ready and pending.delay_seconds is not None
        await asyncio.wait_for(wake.wait(wake.generation, pending, fallback_seconds=15), 1)
        assert (await automations.AutomationService().time_intake.readiness()).ready


async def test_closed_topic_payloads_are_bounded_hints_and_wait_cancellation_drains():
    async with listening() as (listener, _):
        before = listener.diagnostics()["hints"]
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT pg_notify(:channel,:payload)"), {"channel":CHANNEL,"payload":'{"secret":"synthetic-not-a-topic"}'})
            for _ in range(1000):
                await notify_work(session, WorkTopic.COMPLETION)
            await session.commit()
        async def delivered():
            return listener.diagnostics()["hints"] > before
        await until(delivered)
        # PostgreSQL may coalesce identical transaction payloads. There is one
        # process-local latch per closed topic, never one retained object per hint.
        assert listener.diagnostics()["hints"] - before == 1
        wake = work_wake(WorkTopic.COMPLETION)
        task = asyncio.create_task(wake.wait(wake.generation, WorkReadiness(), fallback_seconds=.5))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.done()


async def test_actual_automation_worker_drains_more_than_one_batch_after_one_commit():
    async with listening():
        service = automations.AutomationService()
        async with AsyncSessionLocal() as session:
            identities = await reserve_runs(session, 45)
            await session.commit()
        service.dispatcher.start()
        try:
            async def settled():
                return await scalar("SELECT count(*) FROM automation_runs WHERE status IN ('queued','processing') OR completion_audit_pending IS TRUE") == 0
            await until(settled)
            assert await scalar("SELECT count(*) FROM automation_runs WHERE status NOT IN ('queued','processing')") == len(identities)
            assert await scalar("SELECT count(*) FROM audit_logs WHERE action LIKE 'automation_rule.%'") == len(identities)
        finally:
            await service.dispatcher.stop()
        assert service.dispatcher._task is None


async def test_full_information_capacity_ignores_wake_bursts_and_shutdown_drains_actual_children(monkeypatch):
    from test_vehicle_information_worker import seed
    await seed(5)
    worker = vehicle_information_jobs.VehicleInformationWorker()
    entered, exited = set(), set()
    blocked = asyncio.Event()
    async def process(identity, *_args):
        entered.add(identity)
        try:
            await blocked.wait()
        finally:
            exited.add(identity)
    monkeypatch.setattr(worker, "_process", process)
    async with listening():
        await worker.start()
        try:
            async def full():
                return len(entered) == 2
            await until(full)
            with statements() as queries:
                async with AsyncSessionLocal() as session:
                    for _ in range(100):
                        await notify_work(session, WorkTopic.INFORMATION)
                    await session.commit()
                await asyncio.sleep(.1)
            # Only origin NOTIFY statements; full capacity must not run discovery
            # or acquire more work in response to irrelevant capacity-free hints.
            assert not any("vehicle_information_jobs" in sql for sql, _ in queries)
            assert len(entered) == 2
        finally:
            await worker.stop()
        assert exited == entered
        assert not worker._operations


@pytest.mark.parametrize("instant", ["2026-03-29T01:00:00+00:00", "2026-10-25T01:00:00+00:00"])
async def test_visitor_due_projection_matches_policy_at_utc_dst_and_expiry_boundaries(instant):
    from datetime import datetime, timedelta
    from app.models import VisitorPass
    from app.models.enums import VisitorPassStatus, VisitorPassType
    from sqlalchemy import select
    from app.services.visitor_passes import VisitorPassService
    now = datetime.fromisoformat(instant)
    service = VisitorPassService()
    async with AsyncSessionLocal() as session:
        rows = []
        for explicit in (False, True):
            for status in (VisitorPassStatus.SCHEDULED, VisitorPassStatus.ACTIVE):
                row = VisitorPass(visitor_name="Synthetic boundary", pass_type=VisitorPassType.ONE_TIME,
                    expected_time=now-timedelta(minutes=30), window_minutes=30, status=status,
                    creation_source="synthetic", valid_from=now-timedelta(hours=1) if explicit else None,
                    valid_until=now if explicit else None)
                session.add(row)
                rows.append(row)
        await session.commit()
    for checked in (now-timedelta(microseconds=1), now, now+timedelta(microseconds=1)):
        async with AsyncSessionLocal() as session:
            rows = list(await session.scalars(select(VisitorPass)))
            expected = {row.id for row in rows if service.status_for(row, checked) != row.status}
            if checked == now:
                # Explicit expiry is inclusive; legacy expiry retains its strict > edge.
                explicit_active = next(row for row in rows if row.valid_until is not None and row.status == VisitorPassStatus.ACTIVE)
                legacy_active = next(row for row in rows if row.valid_until is None and row.status == VisitorPassStatus.ACTIVE)
                assert service.status_for(explicit_active, now) == VisitorPassStatus.EXPIRED
                assert service.status_for(legacy_active, now) == VisitorPassStatus.ACTIVE
            changed = await service.refresh_statuses(session=session, now=checked, publish=False, batch_limit=25)
            assert {row.id for row in changed} == expected
            await session.rollback()



async def test_actual_visitor_worker_drains_more_than_twenty_five_due_rows_after_one_commit():
    from datetime import timedelta
    from sqlalchemy import func, select
    from app.models import VisitorPass
    from app.models.enums import VisitorPassStatus, VisitorPassType
    from app.services.visitor_passes import VisitorPassService
    async with listening():
        service = VisitorPassService()
        await service.start()
        try:
            async with AsyncSessionLocal() as session:
                now = await session.scalar(select(func.clock_timestamp()))
                session.add_all([VisitorPass(visitor_name=f"Synthetic batch {index}", pass_type=VisitorPassType.ONE_TIME,
                    expected_time=now, window_minutes=30, status=VisitorPassStatus.SCHEDULED,
                    creation_source="synthetic", valid_from=now-timedelta(minutes=1), valid_until=now+timedelta(hours=1))
                    for index in range(60)])
                await session.flush()
                await notify_work(session, WorkTopic.VISITOR)
                await session.commit()
            async def settled():
                return await scalar("SELECT count(*) FROM visitor_passes WHERE status='ACTIVE'") == 60
            await until(settled)
            assert await scalar("SELECT count(*) FROM audit_logs WHERE action='visitor_pass.status_refresh'") == 60
        finally:
            await service.stop()


@pytest.mark.parametrize("stage,state", [("snapshot","leased"), ("snapshot","waiting"), ("publication","leased")])
async def test_effective_due_order_does_not_hide_an_earlier_lease_or_wait_deadline(stage, state):
    from datetime import timedelta
    import uuid
    from sqlalchemy import func, select
    from app.models import AccessCompletionWork
    from app.services.access.completion_store import AccessCompletionStore
    from app.services.access.publication import AccessPublicationStore
    from test_access_publication_persistence import event_value
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        for due, boundary in ((now+timedelta(hours=1), now-timedelta(seconds=1)),
                              (now-timedelta(seconds=1), now+timedelta(seconds=30))):
            event = event_value()
            session.add(event)
            await session.flush()
            session.add(AccessCompletionWork(id=uuid.uuid4(),event_id=event.id,stage=stage,origin={},
                readiness="committed_decision",state=state,due_at=due,attempt_count=1,result={},
                deadline=boundary if state == "waiting" else now+timedelta(hours=2),
                lease_until=boundary if state == "leased" else None,
                lease_token=uuid.uuid4() if state == "leased" else None))
        await session.commit()
    store = AccessPublicationStore() if stage == "publication" else AccessCompletionStore()
    pending = await store.readiness()
    assert not pending.ready and pending.delay_seconds is not None
    assert 10 < pending.delay_seconds <= 30
    # The projection only schedules a later authoritative claim. No future row
    # may be leased merely because one component of its effective due time passed.
    if stage == "publication":
        assert await store.claim() is None
    else:
        assert await store.claim(1) == []


async def test_locked_first_visitor_batch_does_not_starve_later_due_rows():
    from datetime import timedelta
    from sqlalchemy import func, select
    from app.models import VisitorPass
    from app.models.enums import VisitorPassStatus, VisitorPassType
    from app.services.visitor_passes import VisitorPassService
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        session.add_all([VisitorPass(visitor_name=f"Synthetic locked {index}", pass_type=VisitorPassType.ONE_TIME,
            expected_time=now, window_minutes=30, status=VisitorPassStatus.SCHEDULED,
            creation_source="synthetic", valid_from=now-timedelta(minutes=1), valid_until=now+timedelta(hours=1))
            for index in range(60)])
        await session.commit()
    async with listening():
        service = VisitorPassService()
        async with AsyncSessionLocal() as blocker:
            # Independent connection owns the sorted prefix before the worker's
            # indexed SKIP LOCKED selection. Limit-before-lock would select only
            # these rows forever and leave all thirty-five later passes stale.
            locked = list(await blocker.scalars(select(VisitorPass.id).order_by(VisitorPass.id).limit(25).with_for_update()))
            assert len(locked) == 25
            await service.start()
            try:
                async def later_settled():
                    return await scalar("SELECT count(*) FROM visitor_passes WHERE status='ACTIVE'") == 35
                await until(later_settled)
                assert await scalar("SELECT count(*) FROM visitor_passes WHERE status='SCHEDULED'") == 25
                assert await scalar("SELECT count(*) FROM audit_logs WHERE action='visitor_pass.status_refresh'") == 35
                await blocker.rollback()
                # A committed hint accelerates the ordinary contention retry;
                # the fixed-lane timer has its separate lost-hint coverage.
                async with AsyncSessionLocal() as origin:
                    await notify_work(origin, WorkTopic.VISITOR)
                    await origin.commit()
                async def all_settled():
                    return await scalar("SELECT count(*) FROM visitor_passes WHERE status='ACTIVE'") == 60
                await until(all_settled)
                assert await scalar("SELECT count(*) FROM audit_logs WHERE action='visitor_pass.status_refresh'") == 60
            finally:
                await blocker.rollback()
                await service.stop()
