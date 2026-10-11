import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import gate_malfunctions as malfunction_owner
from app.services import home_assistant as ha_owner
from app.services.event_bus import RealtimeEvent


@pytest.mark.asyncio
async def test_gate_hint_burst_wakes_only_existing_scheduler_and_retains_trailing_wake(monkeypatch):
    service = malfunction_owner.GateMalfunctionService(poll_interval_seconds=60)
    entered = [asyncio.Event() for _ in range(3)]
    release = [asyncio.Event() for _ in range(3)]
    running = maximum = count = 0

    async def evaluate():
        nonlocal running, maximum, count
        number = count
        count += 1
        running += 1
        maximum = max(maximum, running)
        entered[number].set()
        await release[number].wait()
        running -= 1

    monkeypatch.setattr(service, 'evaluate_once', evaluate)
    await service.start()
    try:
        await entered[0].wait()
        hint = RealtimeEvent('gate.state_changed', {}, '')
        for _ in range(100):
            await service._handle_realtime_event(hint)
        assert count == 1
        assert not any(task.get_name() == 'gate-malfunction-realtime-evaluation' for task in asyncio.all_tasks())
        release[0].set()
        await entered[1].wait()
        await service._handle_realtime_event(hint)
        release[1].set()
        await entered[2].wait()
        assert maximum == 1 and count == 3
    finally:
        service._stop_event.set()
        release[2].set()
        await service.stop()


@pytest.mark.asyncio
async def test_ha_refresh_hints_and_maintenance_effects_can_reenter_refresh_after_lock(monkeypatch):
    effects = SimpleNamespace(maintenance_state=AsyncMock())
    service = ha_owner.HomeAssistantIntegrationService(client=SimpleNamespace(), effects=effects)
    service._state_cache[ha_owner.MAINTENANCE_HA_ENTITY_ID] = {'state': 'on'}
    monkeypatch.setattr(service, '_configured_state_entity_ids', lambda *_args: ['fixture.entity'])
    monkeypatch.setattr(service, '_refresh_entity_state', AsyncMock(return_value=('fixture.entity', True, None)))
    observed = []

    async def hint(_previous):
        assert not service._state_refresh_lock.locked()
        observed.append('hint')
        await service._refresh_configured_states(gate_entities=[], garage_door_entities=[])

    async def maintenance(active):
        assert active and not service._state_refresh_lock.locked()
        observed.append('maintenance')
        await service._refresh_configured_states(gate_entities=[], garage_door_entities=[])

    monkeypatch.setattr(service, '_publish_connection_status_if_changed', hint)
    effects.maintenance_state.side_effect = maintenance
    await asyncio.wait_for(service._refresh_configured_states(
        gate_entities=[], garage_door_entities=[], force=True,
    ), timeout=1)
    assert observed == ['hint', 'maintenance']


@pytest.mark.asyncio
async def test_actual_inline_notification_listener_drains_close_before_cancellation_finishes(monkeypatch):
    import uuid
    from app.services.event_bus import EventBus

    close_entered, close_release = asyncio.Event(), asyncio.Event()
    row = SimpleNamespace(id=uuid.uuid4())

    class Session:
        closed = False

        async def get(self, *_args):
            return row

        async def commit(self):
            pass

        async def close(self):
            close_entered.set()
            await close_release.wait()
            self.closed = True

    session = Session()
    service = malfunction_owner.GateMalfunctionService()
    bus = EventBus(enable_redis=False)
    monkeypatch.setattr(malfunction_owner, 'event_bus', bus)
    monkeypatch.setattr(malfunction_owner, 'AsyncSessionLocal', lambda: session)
    monkeypatch.setattr(service, '_add_timeline_event', AsyncMock())
    monkeypatch.setattr(service, '_update_trace', AsyncMock())
    monkeypatch.setattr(service, '_serialize_malfunction', AsyncMock(return_value={'id': str(row.id)}))
    bus.subscribe(service._handle_realtime_event, event_types=frozenset({'notification.sent'}))
    payload = {'malfunction_id': str(row.id), 'malfunction_stage': 'initial'}
    published = asyncio.create_task(bus.publish('notification.sent', payload))
    await close_entered.wait()
    published.cancel()
    await asyncio.sleep(0)
    published.cancel()
    await asyncio.sleep(0)
    assert not published.done() and not session.closed
    close_release.set()
    with pytest.raises(asyncio.CancelledError):
        await published
    assert session.closed
    nested = []

    async def observed(event):
        assert session.closed and not bus._lock.locked()
        async with service._tick_lock:
            nested.append(event.type)

    bus.subscribe(observed, event_types=frozenset({'gate_malfunction.updated'}))
    await asyncio.wait_for(bus.publish('notification.sent', payload), timeout=1)
    assert nested == ['gate_malfunction.updated']
    await bus.stop()


@pytest.mark.asyncio
async def test_committed_resolution_hints_wait_for_close_even_when_next_declaration_conflicts(monkeypatch):
    import uuid
    from datetime import UTC, datetime, timedelta
    from sqlalchemy.exc import IntegrityError
    from app.modules.gate.base import GateState

    identity = uuid.uuid4()
    existing = SimpleNamespace(id=identity)

    class Session:
        closed = False
        commits = 0

        def __init__(self):
            self.values = iter([existing, None])

        async def scalar(self, _statement):
            return next(self.values)

        async def commit(self):
            self.commits += 1
            if self.commits == 2:
                raise IntegrityError('synthetic declaration conflict', {}, Exception('synthetic'))

        async def flush(self):
            pass

        async def rollback(self):
            pass

        def add(self, _row):
            pass

        async def close(self):
            self.closed = True

    session = Session()
    service = malfunction_owner.GateMalfunctionService()
    now = datetime.now(UTC)
    resolved_at = now-timedelta(minutes=12)
    monkeypatch.setattr(malfunction_owner, 'AsyncSessionLocal', lambda: session)
    monkeypatch.setattr(service, '_reopened_after_existing', AsyncMock(return_value=resolved_at))
    monkeypatch.setattr(service, '_mark_resolved', AsyncMock())
    monkeypatch.setattr(service, '_serialize_malfunction', AsyncMock(return_value={'id': str(identity)}))
    monkeypatch.setattr(service, '_last_known_vehicle_event', AsyncMock(return_value=None))
    monkeypatch.setattr(service, '_add_timeline_event', AsyncMock())
    observed = []

    async def publish(event_type, _payload):
        assert session.closed and session.commits == 2
        observed.append(event_type)

    async def queued(actual, stage, *_args):
        assert actual == identity and stage == 'resolved' and session.closed
        observed.append('queued.resolved')

    monkeypatch.setattr(malfunction_owner.event_bus, 'publish', publish)
    monkeypatch.setattr(service, '_queue_notification', queued)
    snapshot = malfunction_owner.GateSnapshot('fixture.gate', 'Fixture', GateState.OPEN,
        now-timedelta(minutes=10), now)
    await service._declare_if_needed(snapshot, now)
    assert observed == ['gate_malfunction.resolved', 'gate_malfunction.updated', 'queued.resolved']
