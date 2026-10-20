"""Inert scheduling regressions; PostgreSQL commit/index evidence lives in validation."""
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.services.access_events import AccessEventService
from app.services.automations import AutomationService
from app.services.durable_work import WorkReadiness, WorkTopic, WorkWake, wait_with_capacity
from app.services.ha_presence_worker import HaPresenceWorker
from app.services.visitor_lifecycle import LANES, due_passes
from app.services.visitor_passes import VisitorPassStatus, status_for_values


@pytest.mark.asyncio
async def test_generation_latch_retains_hint_between_probe_and_wait():
    wake = WorkWake()
    wake.connected = True
    generation = wake.generation
    for _ in range(1000):
        wake.set()
    await asyncio.wait_for(wake.wait(generation, WorkReadiness(), fallback_seconds=.5), .1)
    assert wake.generation == 1000
    assert not wake.fallback_waits


@pytest.mark.asyncio
async def test_detected_outage_shortens_wait_and_records_fallback():
    wake = WorkWake()
    await wake.wait(wake.generation, WorkReadiness(), fallback_seconds=.001)
    assert wake.fallback_waits == 1
    with pytest.raises(ValueError):
        WorkTopic('provider_payload')


@pytest.mark.asyncio
async def test_partial_capacity_wakes_for_new_work_without_waiting_for_active_child():
    wake = WorkWake()
    wake.connected = True
    release = asyncio.Event()
    operation = asyncio.create_task(release.wait())
    operations = {operation}
    generation = wake.generation
    waiting = asyncio.create_task(wait_with_capacity(
        wake, generation, WorkReadiness(), operations, full=False, fallback_seconds=.5,
    ))
    await asyncio.sleep(0)
    wake.set()
    await asyncio.wait_for(waiting, .1)
    assert not operation.done()
    release.set()
    await operation


@pytest.mark.asyncio
async def test_full_capacity_waits_for_actual_child_cleanup():
    wake = WorkWake()
    cleaning, release = asyncio.Event(), asyncio.Event()

    async def child():
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    operation = asyncio.create_task(child())
    await asyncio.sleep(0)
    operation.cancel()
    await cleaning.wait()
    waiting = asyncio.create_task(wait_with_capacity(
        wake, wake.generation, WorkReadiness(True, 0), {operation}, full=True, fallback_seconds=.5,
    ))
    wake.set()
    await asyncio.sleep(0)
    assert not waiting.done()
    release.set()
    await waiting
    await asyncio.gather(operation, return_exceptions=True)


class StopAtWait(WorkWake):
    def __init__(self):
        super().__init__()
        self.connected = True
        self.waits = []

    async def wait(self, generation, pending, *, fallback_seconds):
        self.waits.append(pending)
        raise asyncio.CancelledError


@pytest.mark.asyncio
async def test_ha_busy_attempt_uses_contention_delay_with_healthy_listener(monkeypatch):
    wake = StopAtWait()
    store = SimpleNamespace()

    async def ready():
        return WorkReadiness(True, 0)

    async def recover():
        return None

    async def busy():
        return False

    store.readiness = ready
    worker = HaPresenceWorker(store, wake=wake)
    monkeypatch.setattr(worker, '_recover', recover)
    monkeypatch.setattr(worker, '_process_owned', busy)
    with pytest.raises(asyncio.CancelledError):
        await worker._run()
    assert len(wake.waits) == 1
    assert wake.waits[0].delay_seconds == .5


@pytest.mark.asyncio
async def test_locked_due_automation_rule_does_not_spin(monkeypatch):
    wake = StopAtWait()
    service = AutomationService()
    calls = 0

    async def ready():
        return WorkReadiness(True, 0)

    async def locked():
        nonlocal calls
        calls += 1
        return 0

    monkeypatch.setattr(service.time_intake, 'readiness', ready)
    monkeypatch.setattr(service, '_process_due_rules', locked)
    monkeypatch.setattr('app.services.automations.work_wake', lambda topic: wake)
    with pytest.raises(asyncio.CancelledError):
        await service._run_scheduler()
    assert calls == 1
    assert wake.waits[0].delay_seconds == 15


@pytest.mark.asyncio
async def test_lpr_renewal_failure_wakes_idle_coordinator(monkeypatch):
    wake = WorkWake()
    service = AccessEventService(wake=wake)

    async def lost(claim):
        raise RuntimeError('synthetic lost lease')

    monkeypatch.setattr(service._pipeline, 'renew_coordinator', lost)
    monkeypatch.setattr('app.services.access_events.COORDINATOR_RENEWAL_SECONDS', .001)
    generation = wake.generation
    with pytest.raises(RuntimeError, match='synthetic lost lease'):
        await service._renew_coordinator(None)
    assert wake.generation != generation


def test_visitor_due_query_is_bounded_and_retains_literal_index_predicates():
    now = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    queries = [str(due_passes(now, lane=lane).compile(
        dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True},
    )) for lane in LANES]
    assert "status='SCHEDULED'" in queries[0]
    assert "status='ACTIVE'" in queries[1]
    for sql in queries:
        assert sql.count('LIMIT 25') == 1
        assert 'SKIP LOCKED' in sql
        assert 'photo' not in sql
    assert 'AT TIME ZONE' in queries[0]


def test_visitor_legacy_inclusive_end_and_explicit_exclusive_end_remain_distinct():
    end = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    start = end - timedelta(minutes=30)
    assert status_for_values(start, 30, end) == VisitorPassStatus.ACTIVE
    assert status_for_values(start, 30, end + timedelta(microseconds=1)) == VisitorPassStatus.EXPIRED
    assert status_for_values(start, 30, end, valid_from=start, valid_until=end) == VisitorPassStatus.EXPIRED
    with pytest.raises(ValueError):
        status_for_values(start, 30, end, valid_from=end, valid_until=start)


@pytest.mark.asyncio
async def test_readiness_cancellation_drains_session_close_before_return():
    from app.services.durable_readiness import readiness

    active, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Session:
        async def __aenter__(self):
            return self

        async def execute(self, statement, parameters):
            active.set()
            await asyncio.Event().wait()

        async def __aexit__(self, *exc):
            cleaning.set()
            await release.wait()

    task = asyncio.create_task(readiness(Session, ('SELECT t.now FROM t',)))
    await active.wait()
    task.cancel()
    await cleaning.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_idle_lpr_caller_releases_lost_ownership_before_any_boundary(monkeypatch):
    from unittest.mock import AsyncMock

    from app.services.access.pipeline_contracts import PipelineLeaseLost

    wake = WorkWake()
    wake.connected = True
    service = AccessEventService(wake=wake)
    service._last_probe_generation = wake.generation
    idle, released = asyncio.Event(), asyncio.Event()
    claim = SimpleNamespace(token='synthetic', epoch=1)

    class Pipeline:
        async def readiness(self, *, owned, **kwargs):
            if owned:
                idle.set()
                return WorkReadiness()
            return WorkReadiness(True, 0)

        async def acquire_coordinator(self):
            return claim

        async def renew_coordinator(self, current):
            assert current is claim
            await idle.wait()
            raise PipelineLeaseLost('synthetic lost lease')

        async def release_coordinator(self, current):
            assert current is claim
            service._stop_event.set()
            released.set()

    service._pipeline = Pipeline()
    boundary = AsyncMock(side_effect=AssertionError('lost owner must not start boundary'))
    monkeypatch.setattr(service, '_process_boundary', boundary)
    monkeypatch.setattr('app.services.access_events.COORDINATOR_RENEWAL_SECONDS', .001)
    task = asyncio.create_task(service._process_queue())
    try:
        await asyncio.wait_for(released.wait(), .2)
        await asyncio.wait_for(task, .2)
        assert not service._coordinator_owned
        boundary.assert_not_awaited()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_actual_empty_ha_recovery_does_not_notify_itself(monkeypatch):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from app.services import ha_presence_attempts

    coordinator = SimpleNamespace(lease_token=None, lease_until=None)
    session = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
                              flush=AsyncMock(), commit=AsyncMock())

    @asynccontextmanager
    async def sessions(factory):
        yield session

    monkeypatch.setattr(ha_presence_attempts, 'ha_session', sessions)
    monkeypatch.setattr(ha_presence_attempts, 'lock_ha_coordinator', AsyncMock(return_value=coordinator))
    monkeypatch.setattr(ha_presence_attempts, 'database_now', AsyncMock(return_value=datetime.now(UTC)))
    notify = AsyncMock()
    monkeypatch.setattr(ha_presence_attempts, 'notify_work', notify)
    store = ha_presence_attempts.HaPresenceAttemptStore()
    assert await store.recover_expired_attempts() == 0
    notify.assert_not_awaited()
