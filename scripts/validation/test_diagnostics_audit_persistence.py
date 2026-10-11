"""Bounded diagnostic batches and mandatory audit transactions on isolated PostgreSQL."""
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, select

from app.db.session import AsyncSessionLocal, engine
from app.models import AuditLog, GateStateObservation, Person, TelemetrySpan, TelemetryTrace
from app.services import automations as automation_owner
from app.services import home_assistant as ha_owner
from app.services import telemetry as telemetry_owner

pytest_plugins = ('test_recovery_boundaries',)
pytestmark = pytest.mark.asyncio


async def count(model):
    async with AsyncSessionLocal() as session:
        return await session.scalar(select(func.count()).select_from(model))


async def test_actual_writer_batches_fifo_rows_and_audit_is_independent(isolated_resources):
    service = telemetry_owner.TelemetryService()
    inserted = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith('INSERT INTO telemetry_'):
            inserted.append(statement.split()[2])

    event.listen(engine.sync_engine, 'before_cursor_execute', observe)
    try:
        await service.start()
        now = datetime.now(UTC)
        for number in range(250):
            service.enqueue_span(TelemetrySpan(span_id=f'{number:016x}', trace_id=f'{number:032x}',
                name=f'Synthetic span {number}', category='fixture', started_at=now, step_order=number))
        await service.flush()
        assert await count(TelemetrySpan) == 250
        assert len(inserted) == 3
        assert engine.pool.checkedout() == 0
        assert service.status()['retained_records'] == service.status()['retained_serialized_bytes'] == 0
        await service.stop()
        # Diagnostic intake is closed; mandatory audit still awaits a real commit.
        service.enqueue_trace(TelemetryTrace(trace_id='a'*32, name='Dropped', category='fixture', started_at=now))
        await telemetry_owner.record_audit_log(category='fixture', action='fixture.mandatory', actor='Fixture')
        assert await count(AuditLog) == 1 and await count(TelemetryTrace) == 0
        assert service.status()['dropped_records'] == {'not_started_or_stopping': 1}
    finally:
        event.remove(engine.sync_engine, 'before_cursor_execute', observe)
        await service.stop()


async def test_gate_observation_and_audit_roll_back_and_commit_together(isolated_resources, monkeypatch):
    service = ha_owner.HomeAssistantIntegrationService(client=SimpleNamespace())
    original = ha_owner.write_audit_log
    hints = []

    async def hint(event_type, payload):
        assert engine.pool.checkedout() == 0
        hints.append((event_type, payload))

    monkeypatch.setattr(telemetry_owner.event_bus, 'publish', hint)

    async def fail(session, **kwargs):
        await original(session, **kwargs)
        await session.flush()
        raise RuntimeError('synthetic mandatory audit failure')

    monkeypatch.setattr(ha_owner, 'write_audit_log', fail)
    values = dict(previous_state='closed', last_changed=datetime.now(UTC).isoformat(), source='fixture')
    with pytest.raises(RuntimeError, match='synthetic mandatory'):
        await service._record_gate_state_observation('fixture.gate', 'Fixture', 'open', **values)
    assert await count(GateStateObservation) == await count(AuditLog) == 0
    assert hints == []
    monkeypatch.setattr(ha_owner, 'write_audit_log', original)
    await service._record_gate_state_observation('fixture.gate', 'Fixture', 'open', **values)
    await service._record_gate_state_observation('fixture.gate', 'Fixture', 'open', **values)
    assert await count(GateStateObservation) == await count(AuditLog) == 1
    assert len(hints) == 1 and hints[0][0] == 'audit.log.created'
    assert hints[0][1]['action'] == 'gate.state_changed'


async def test_unmatched_trigger_audit_joins_actual_reservation_transaction(isolated_resources, monkeypatch):
    service = automation_owner.AutomationService()
    reserve, write = automation_owner.reserve_trigger, automation_owner.write_audit_log
    marker_id = uuid.uuid4()
    seen = []
    hints = []

    async def hint(event_type, payload):
        assert engine.pool.checkedout() == 0
        hints.append((event_type, payload))

    monkeypatch.setattr(telemetry_owner.event_bus, "publish", hint)

    async def reserved(session, *args, **kwargs):
        identities = await reserve(session, *args, **kwargs)
        assert identities == []
        seen.append(await session.scalar(select(func.pg_backend_pid())))
        session.add(Person(id=marker_id, first_name='Synthetic', last_name='Transaction marker', display_name='Synthetic transaction marker',
                           garage_door_entity_ids=[]))
        return identities

    async def audit(session, **kwargs):
        seen.append(await session.scalar(select(func.pg_backend_pid())))
        await write(session, **kwargs)
        await session.flush()
        raise RuntimeError('synthetic unmatched audit failure')

    monkeypatch.setattr(automation_owner, 'reserve_trigger', reserved)
    monkeypatch.setattr(automation_owner, 'write_audit_log', audit)
    monkeypatch.setattr(service.dispatcher, 'wake', lambda: None)
    with pytest.raises(RuntimeError, match='synthetic unmatched'):
        await service.fire_trigger('fixture.unmatched', source='fixture', origin_id=str(uuid.uuid4()))
    assert seen[0] == seen[1]
    assert await count(Person) == await count(AuditLog) == 0
    assert hints == []
    monkeypatch.setattr(automation_owner, 'write_audit_log', write)
    assert await service.fire_trigger('fixture.unmatched', source='fixture', origin_id=str(uuid.uuid4())) == []
    assert await count(Person) == await count(AuditLog) == 1
    assert len(hints) == 1 and hints[0][0] == "audit.log.created"
    assert hints[0][1]["action"] == "automation_trigger.unmatched"
