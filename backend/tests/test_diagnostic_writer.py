import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models import TelemetrySpan, TelemetryTrace
from app.services import telemetry as owner


class Session:
    def __init__(self, *, commit_gate=None, close_gate=None, fail=False):
        self.rows = []
        self.commit_gate = commit_gate
        self.close_gate = close_gate
        self.fail = fail
        self.committing = asyncio.Event()
        self.closing = asyncio.Event()
        self.closed = False

    async def execute(self, _statement):
        pass

    def add(self, row):
        self.rows.append(row)

    async def commit(self):
        self.committing.set()
        if self.commit_gate:
            await self.commit_gate.wait()
        if self.fail:
            raise RuntimeError('synthetic write failure')

    async def close(self):
        self.closing.set()
        if self.close_gate:
            await self.close_gate.wait()
        self.closed = True


def trace(number=0, **kwargs):
    return TelemetryTrace(trace_id=f'{number:032x}', name='Synthetic trace', category='fixture',
                          status='ok', level='info', started_at=datetime.now(UTC), **kwargs)


@pytest.fixture(autouse=True)
def inert_hints(monkeypatch):
    monkeypatch.setattr(owner.event_bus, 'publish', AsyncMock())


@pytest.mark.asyncio
async def test_one_inert_writer_batches_fifo_and_freezes_rows():
    sessions = []

    def factory():
        session = Session()
        sessions.append(session)
        return session

    service = owner.TelemetryService(sessions=factory)
    assert service._writer is None
    service.enqueue_trace(trace())
    assert service.status()['dropped_records'] == {'not_started_or_stopping': 1}
    await service.start()
    task = service._writer
    await service.start()
    assert service._writer is task
    for number in range(205):
        row = trace(number)
        service.enqueue_trace(row)
        row.name = 'Caller mutation'
    await service.flush()
    assert [len(session.rows) for session in sessions] == [100, 100, 5]
    rows = [row for session in sessions for row in session.rows]
    assert [row.trace_id for row in rows] == [f'{number:032x}' for number in range(205)]
    assert {row.name for row in rows} == {'Synthetic trace'}
    assert all(session.closed for session in sessions)
    assert service.status()['retained_records'] == service.status()['retained_serialized_bytes'] == 0
    await service.stop()
    assert task.done() and service._writer is None


@pytest.mark.asyncio
async def test_record_capacity_includes_inflight_database_close(monkeypatch):
    monkeypatch.setattr(owner, 'DIAGNOSTIC_RECORD_LIMIT', 4)
    close = asyncio.Event()
    session = Session(close_gate=close)
    service = owner.TelemetryService(sessions=lambda: session)
    await service.start()
    service.enqueue_span(TelemetrySpan(span_id='a' * 16, trace_id='f' * 32, name='span',
                                     category='fixture', started_at=datetime.now(UTC)))
    await session.closing.wait()
    for number in range(4):
        service.enqueue_trace(trace(number))
    status = service.status()
    assert status['queued_records'] == 3 and status['retained_records'] == 4
    assert status['dropped_records'] == {'record_capacity': 1}
    assert status['retained_serialized_bytes'] > 0
    close.set()
    await service.flush()
    await service.stop()
    assert service.status()['retained_records'] == service.status()['retained_serialized_bytes'] == 0


@pytest.mark.asyncio
async def test_serialized_byte_capacity_and_blocked_postcommit_hint(monkeypatch):
    monkeypatch.setattr(owner, 'DIAGNOSTIC_BYTE_LIMIT', 700)
    entered, release = asyncio.Event(), asyncio.Event()

    async def hint(*_args):
        entered.set()
        await release.wait()

    monkeypatch.setattr(owner.event_bus, 'publish', hint)
    service = owner.TelemetryService(sessions=Session)
    await service.start()
    service.enqueue_trace(trace(1))
    await entered.wait()
    retained = service.status()['retained_serialized_bytes']
    assert retained > 0 and service.status()['retained_records'] == 1
    service.enqueue_trace(trace(2, context={'large': 'x' * 2000}))
    assert service.status()['dropped_records'] == {'byte_capacity': 1}
    assert service.status()['retained_serialized_bytes'] == retained
    assert retained <= 700
    release.set()
    await service.flush()
    await service.stop()


@pytest.mark.asyncio
async def test_repeated_stop_cancellation_drains_actual_session_close():
    close = asyncio.Event()
    session = Session(close_gate=close)
    service = owner.TelemetryService(sessions=lambda: session)
    await service.start()
    service.enqueue_trace(trace())
    await session.closing.wait()
    stopped = asyncio.create_task(service.stop())
    await asyncio.sleep(0)
    stopped.cancel()
    await asyncio.sleep(0)
    stopped.cancel()
    await asyncio.sleep(0)
    assert not stopped.done() and not session.closed
    assert service.status()['retained_records'] == 1
    assert not service._writer.done()
    close.set()
    with pytest.raises(asyncio.CancelledError):
        await stopped
    assert session.closed and service._writer is None
    assert service.status()['retained_records'] == service.status()['retained_serialized_bytes'] == 0


@pytest.mark.asyncio
async def test_failed_batch_is_explicit_diagnostic_loss_and_next_batch_progresses():
    sessions = [Session(fail=True), Session()]
    service = owner.TelemetryService(sessions=lambda: sessions.pop(0))
    await service.start()
    service.enqueue_trace(trace(1))
    await service.flush()
    assert service.status()['dropped_records'] == {'persistence_failed': 1}
    service.enqueue_trace(trace(2))
    await service.flush()
    assert service.status()['settled_records'] == 2
    await service.stop()
    assert not hasattr(service, 'enqueue_audit') and not hasattr(service, '_tasks')


@pytest.mark.asyncio
async def test_flush_waits_only_for_prefix_accepted_before_call(monkeypatch):
    monkeypatch.setattr(owner, 'DIAGNOSTIC_BATCH_LIMIT', 1)
    first, second = asyncio.Event(), asyncio.Event()
    sessions = [Session(commit_gate=first), Session(commit_gate=second)]
    original = list(sessions)
    service = owner.TelemetryService(sessions=lambda: sessions.pop(0))
    await service.start()
    service.enqueue_trace(trace(1))
    await original[0].committing.wait()
    flushed = asyncio.create_task(service.flush())
    await asyncio.sleep(0)
    service.enqueue_trace(trace(2))
    first.set()
    await original[1].committing.wait()
    await asyncio.wait_for(flushed, timeout=1)
    assert service.status()['settled_records'] == 1
    assert service.status()['retained_records'] == 1
    second.set()
    await service.stop()


@pytest.mark.asyncio
async def test_mandatory_audit_awaits_its_own_commit_and_survives_hint_failure(monkeypatch):
    committed = asyncio.Event()
    session = Session(commit_gate=committed)
    row = SimpleNamespace(id='audit')

    async def write(actual, **kwargs):
        assert actual is session and kwargs['action'] == 'mandatory.fixture'
        actual.add(row)
        return row

    monkeypatch.setattr(owner, 'AsyncSessionLocal', lambda: session)
    monkeypatch.setattr(owner, 'write_audit_log', write)
    monkeypatch.setattr(owner, 'audit_log_event_payload', lambda actual: {'id': actual.id})
    monkeypatch.setattr(owner.event_bus, 'publish', AsyncMock(side_effect=RuntimeError('hint unavailable')))
    audit = asyncio.create_task(owner.record_audit_log(category='fixture', action='mandatory.fixture', actor='Fixture'))
    await session.committing.wait()
    assert not audit.done()
    committed.set()
    await audit
    assert session.closed and session.rows == [row]
    owner.event_bus.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_deadline_cancels_writer_once_and_drains_close_before_freeing_budget(monkeypatch):
    monkeypatch.setattr(owner, 'DIAGNOSTIC_DRAIN_SECONDS', 0.02)
    monkeypatch.setattr(owner, 'DIAGNOSTIC_BATCH_LIMIT', 1)
    commit, close = asyncio.Event(), asyncio.Event()
    session = Session(commit_gate=commit, close_gate=close)
    service = owner.TelemetryService(sessions=lambda: session)
    await service.start()
    service.enqueue_trace(trace(1))
    service.enqueue_trace(trace(2))
    await session.committing.wait()
    task = service._writer
    stopped = asyncio.create_task(service.stop())
    await session.closing.wait()
    assert not stopped.done() and task.cancelling() == 1
    assert service.status()['retained_records'] == 2
    stopped.cancel()
    await asyncio.sleep(0)
    stopped.cancel()
    await asyncio.sleep(0)
    assert task.cancelling() == 1 and not stopped.done()
    close.set()
    with pytest.raises(asyncio.CancelledError):
        await stopped
    assert session.closed and service._writer is None
    assert service.status()['dropped_records'] == {'shutdown_unconfirmed': 1, 'shutdown_queued': 1}
    assert service.status()['retained_records'] == service.status()['retained_serialized_bytes'] == 0


@pytest.mark.asyncio
async def test_shutdown_during_committed_batch_hint_never_reports_durable_rows_dropped(monkeypatch):
    monkeypatch.setattr(owner, 'DIAGNOSTIC_DRAIN_SECONDS', 0.02)
    entered = asyncio.Event()

    async def hint(*_args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(owner.event_bus, 'publish', hint)
    session = Session()
    service = owner.TelemetryService(sessions=lambda: session)
    await service.start()
    service.enqueue_trace(trace())
    await entered.wait()
    assert session.closed and service.status()['retained_records'] == 1
    await service.stop()
    assert service.status()['dropped_records'] == {}
    assert service.status()['realtime_hint_failures'] == 1
    assert service.status()['retained_records'] == service.status()['retained_serialized_bytes'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("offered", [20, 2000])
async def test_actual_record_limit_counts_blocked_inflight_and_queued(offered):
    close = asyncio.Event()
    session = Session(close_gate=close)
    service = owner.TelemetryService(sessions=lambda: session)
    await service.start()
    service.enqueue_trace(trace(0))
    await session.closing.wait()
    for number in range(1, offered):
        service.enqueue_trace(trace(number))
    status = service.status()
    retained = min(offered, 1024)
    assert status['retained_records'] == retained
    assert status['queued_records'] == retained - 1
    assert 0 < status['retained_serialized_bytes'] <= 16 * 1024 * 1024
    assert status['dropped_records'] == ({'record_capacity': offered-retained} if offered > retained else {})
    close.set()
    await service.stop()
    assert service.status()['settled_records'] == retained
    assert service.status()['retained_records'] == service.status()['retained_serialized_bytes'] == 0


@pytest.mark.asyncio
async def test_actual_byte_limit_rejects_blocked_input_before_record_limit():
    close = asyncio.Event()
    session = Session(close_gate=close)
    service = owner.TelemetryService(sessions=lambda: session)
    await service.start()
    service.enqueue_trace(trace(0, context={'fixture': 'x' * 20000}))
    await session.closing.wait()
    for number in range(1, 2000):
        service.enqueue_trace(trace(number, context={'fixture': 'x' * 20000}))
    status = service.status()
    assert 1 < status['retained_records'] < 1024
    assert status['retained_serialized_bytes'] <= 16 * 1024 * 1024
    assert status['dropped_records'] == {'byte_capacity': 2000-status['retained_records']}
    close.set()
    await service.stop()
    assert service.status()['retained_records'] == service.status()['retained_serialized_bytes'] == 0
