"""Full access persistence regressions, exclusively in the isolated harness."""
from test_recovery_boundaries import isolated_resources as isolated_resources
import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

assert {p.name for p in Path('/sys/class/net').iterdir()} == {'lo'}
assert '@127.0.0.1:5432/iacs_p1_' in os.environ.get('IACS_DATABASE_URL', '')

from app.db.session import AsyncSessionLocal, engine
from app.models import AccessDevice, AccessDeviceCommandRecord, AccessDeviceProviderBinding, AccessEvent, Anomaly, AuditLog, GateCommandRecord, LprIngestEvent, MovementSagaRecord, MovementSessionRecord, NotificationRun, Person, Presence, Schedule, Vehicle, VisitorPass, VisitorPassReservationRecord
from app.models.enums import AccessDecision, AccessDirection, MovementSagaState, PresenceState, VisitorPassStatus, VisitorPassType
from app.modules.gate.base import CommandDelivery, GateState
from app.modules.access_devices.base import AccessDeviceCommandResult, AccessDeviceStateObservation
from app.modules.gate import access_devices as gate_adapter
from app.services import access_devices as devices_owner, access_device_configuration as configuration_owner
from app.services.access_device_commands import AccessDeviceCommandJournal
from app.services.access_devices import AccessDeviceService
from app.modules.lpr.base import PlateRead
from app.services import access_events as owner
from app.services.access import hardware, enrichment, execution, authorization
from app.services.access.reads import GATE_OBSERVATION_PAYLOAD_KEY
from app.services import movement_reconciliation as reconciliation
from app.services.dvla import NormalizedDvlaVehicle
from app.services.gate_commands import GateCommandCoordinator
from app.services.notifications import NotificationService
from app.services.settings import get_runtime_config

pytestmark = pytest.mark.asyncio


class Trace:
    trace_id = 'synthetic-access-trace'
    def start_span(self, *args, **kwargs): return self
    def record_span(self, *args, **kwargs): pass
    def finish(self, *args, **kwargs): pass


class Harness:
    def __init__(self, runtime):
        self.runtime = runtime
        self.gate_calls = []
        self.enrichment_calls = []
        self.events = []
        self.presence_effects = []
        self.gate_accepted = True
        self.result_state = GateState.OPENING
        self.physical_state = GateState.CLOSED
        self.garage_provider = None
        self.publications = []
        self.devices = AccessDeviceService()
        self.delay = 0
        self.started = perf_counter()
        self.gate_times = []
        self.on_gate = None
        self.notifications = NotificationService()

    async def current_state(self):
        return self.physical_state

    async def observe_state(self, binding, *, runtime_config=None):
        if binding.external_id == "cover.synthetic_garage":
            assert self.garage_provider is not None
            return await self.garage_provider.observe_state(binding, runtime_config=runtime_config)
        assert binding.external_id == "cover.synthetic_entry"
        return AccessDeviceStateObservation(self.physical_state, datetime.now(UTC))

    async def command_cover(self, binding, action, reason, *, runtime_config=None):
        if binding.external_id == "cover.synthetic_garage":
            assert self.garage_provider is not None
            return await self.garage_provider.command_cover(binding, action, reason, runtime_config=runtime_config)
        assert binding.external_id == "cover.synthetic_entry" and action == "open"
        self.gate_times.append(perf_counter() - self.started)
        self.gate_calls.append(reason)
        # Real adapter/coordinator/journal must commit authorization and the
        # exact target attempt before any provider I/O becomes possible.
        async with AsyncSessionLocal() as session:
            child = await session.scalar(select(AccessDeviceCommandRecord).where(
                AccessDeviceCommandRecord.gate_command_id.is_not(None)).order_by(AccessDeviceCommandRecord.created_at.desc()).limit(1))
            assert child and child.state == "attempting" and child.attempted_at
            command = await session.get(GateCommandRecord, child.gate_command_id)
            assert command and command.movement_saga_id and command.access_event_id
            event = await session.get(AccessEvent, command.access_event_id)
            assert event.decision == AccessDecision.GRANTED
            saga = await session.get(MovementSagaRecord, command.movement_saga_id)
            assert saga.admission_status == "pending" and not saga.presence_committed
            reservation = await session.scalar(select(VisitorPassReservationRecord).where(
                VisitorPassReservationRecord.access_event_id == event.id))
            if reservation:
                visitor = await session.get(VisitorPass, reservation.visitor_pass_id)
                assert reservation.state == "reserved" and visitor.arrival_event_id is None
        if self.on_gate:
            await self.on_gate()
        if self.gate_accepted:
            self.physical_state = self.result_state
        return AccessDeviceCommandResult(self.gate_accepted, self.physical_state, "Synthetic gate result",
            provider="home_assistant", external_id=binding.external_id,
            delivery=CommandDelivery.ACCEPTED if self.gate_accepted else CommandDelivery.REJECTED)

    async def record_open_observation(self):
        self.physical_state = GateState.OPEN
        async with AsyncSessionLocal() as session:
            child = await session.scalar(select(AccessDeviceCommandRecord).where(
                AccessDeviceCommandRecord.gate_command_id.is_not(None)))
        assert child is not None
        await AccessDeviceCommandJournal().reconcile_observation(child.id,
            binding_fingerprint=child.binding_fingerprint,
            observation={"provider": "home_assistant", "state": "open", "observed_at": datetime.now(UTC)})

    async def lookup(self, registration_number, **kwargs):
        self.enrichment_calls.append('dvla')
        await asyncio.sleep(self.delay)
        return NormalizedDvlaVehicle(registration_number=registration_number, make='Synthetic', colour='Blue', mot_status='Valid', tax_status='Taxed', mot_expiry=None, tax_expiry=None)

    async def snapshot(self, event, **kwargs):
        self.enrichment_calls.append('snapshot')
        await asyncio.sleep(self.delay)

    async def visual(self, *args, **kwargs):
        self.enrichment_calls.append('visual')
        return {'observed_vehicle_color': 'Blue', 'source': 'synthetic'}

    async def publish(self, name, payload):
        self.events.append(name)
        self.publications.append((name, payload))

    async def presence_effect(self, person, event, **kwargs):
        async with AsyncSessionLocal() as s:
            presence = await s.get(Presence, person.id)
            assert presence and presence.last_event_id == event.id
        self.presence_effects.append(event.id)

    def service(self):
        service = owner.AccessEventService()
        service._runtime = self.runtime
        return service


@pytest_asyncio.fixture
async def h(monkeypatch):
    async with AsyncSessionLocal() as s:
        await s.execute(text('TRUNCATE people, vehicles, access_events, visitor_passes, visitor_pass_reservations, movement_sagas, movement_sessions, gate_command_records, access_device_command_records, access_devices, gate_state_observations, lpr_ingest_events, notification_runs, audit_logs CASCADE'))
        await s.commit()
    runtime = replace(await get_runtime_config(), site_timezone='Europe/London', schedule_default_policy='allow', lpr_zone_filter_mode='shadow', home_assistant_url='http://synthetic.invalid', home_assistant_token='synthetic-only', gate_control_provider='home_assistant', gate_failover_provider='none', gate_admission_device_key='synthetic_entry')
    fake = Harness(runtime)
    async with AsyncSessionLocal() as session:
        gate = AccessDevice(key="synthetic_entry", name="Synthetic entry", kind="gate", enabled=True, open_for_access=True)
        gate.provider_bindings = [AccessDeviceProviderBinding(provider="home_assistant", external_id="cover.synthetic_entry", enabled=True, config={})]
        session.add(gate)
        await session.commit()
    async def current_runtime(*_args):
        return fake.runtime
    monkeypatch.setattr(owner, 'get_runtime_config', current_runtime)
    for module in (execution, authorization, devices_owner, configuration_owner):
        monkeypatch.setattr(module, 'get_runtime_config_for_session', current_runtime)
    monkeypatch.setattr(devices_owner, 'get_runtime_config', current_runtime)
    monkeypatch.setattr(devices_owner, 'get_access_device_provider', lambda name: fake)
    monkeypatch.setattr(devices_owner, 'COMMAND_CONFIRMATION_TIMEOUT_SECONDS', 0)
    monkeypatch.setattr(devices_owner, 'CLOSE_COMMAND_CONFIRMATION_TIMEOUT_SECONDS', 0)
    monkeypatch.setattr(devices_owner, 'emit_audit_log', lambda **kwargs: None)
    monkeypatch.setattr(gate_adapter, 'get_access_device_service', lambda: fake.devices)
    monkeypatch.setattr(owner, 'is_maintenance_mode_active', AsyncMock(return_value=False))
    monkeypatch.setattr(owner.telemetry, 'start_trace', lambda *a, **kw: Trace())
    monkeypatch.setattr(enrichment, 'lookup_normalized_vehicle_registration', fake.lookup)
    monkeypatch.setattr(enrichment, 'capture_access_event_snapshot', fake.snapshot)
    monkeypatch.setattr(enrichment, 'get_vehicle_visual_detection_recorder', lambda: SimpleNamespace(recent_match=fake.visual))
    monkeypatch.setattr(owner, 'get_lpr_zone_shadow_service', lambda: SimpleNamespace(record_decision=AsyncMock()))
    monkeypatch.setattr(enrichment, 'get_leaderboard_service', lambda: SimpleNamespace(evaluate_known_overtake=AsyncMock()))
    monkeypatch.setattr(owner, 'get_gate_controller', lambda name: fake)
    monkeypatch.setattr(owner, 'get_notification_service', lambda: fake.notifications)
    monkeypatch.setattr(enrichment, 'get_notification_service', lambda: fake.notifications)
    monkeypatch.setattr(enrichment, 'get_lpr_zone_shadow_service', lambda: SimpleNamespace(record_decision=AsyncMock()))
    monkeypatch.setattr(hardware, 'get_gate_command_coordinator', lambda: GateCommandCoordinator(lambda name: gate_adapter.AccessDeviceGateController()))
    monkeypatch.setattr(hardware, 'get_access_device_service', lambda: fake.devices)
    monkeypatch.setattr(owner.event_bus, 'publish', fake.publish)
    monkeypatch.setattr(enrichment, 'apply_person_presence_input_boolean_actions', fake.presence_effect)
    monkeypatch.setattr(reconciliation, 'apply_person_presence_input_boolean_actions', fake.presence_effect)
    yield fake
    await engine.dispose()


async def resident(state=PresenceState.EXITED):
    async with AsyncSessionLocal() as s:
        person = Person(display_name='Synthetic resident', is_active=True)
        vehicle = Vehicle(registration_number='SYNTH01', owner=person, is_active=True)
        s.add_all([person, vehicle]); await s.flush()
        s.add(Presence(person_id=person.id, state=state, last_changed_at=datetime.now(UTC)-timedelta(days=1)))
        await s.commit()
        return person.id, vehicle.id


def read(plate='SYNTH01', gate='closed', *, at=None, source='synthetic-access', **payload):
    at = at or datetime.now(UTC)
    return PlateRead(registration_number=plate, confidence=.99, source=source, captured_at=at,
                     raw_payload={GATE_OBSERVATION_PAYLOAD_KEY: {'state': gate, 'observed_at': at.isoformat()}, **payload})


async def process(service, plate_read):
    await service.enqueue_plate_read(plate_read)
    if service._queue.empty(): return
    queued = service._queue.get_nowait()
    assert await service._claim_lpr_ingest_for_processing(queued)
    # Use the same classification/finalization owners as the worker, with retries
    # exposed as failures so a swallowed error cannot produce a passing test.
    async def finalize(window, *, reason):
        await service._finalize_window(window)
        return True
    service._finalize_window_or_fail = finalize
    await service._handle_queued_read(queued)
    await service._flush_all_pending()


async def rows(model):
    async with AsyncSessionLocal() as s:
        return list((await s.scalars(select(model))).all())


@pytest.mark.parametrize('accepted,verified', [(True,True),(False,False),(True,False)])
async def test_arrival_persists_decision_before_gate_and_presence_after_outcome(h, accepted, verified):
    person_id, _ = await resident(); h.gate_accepted=accepted; h.result_state=GateState.OPENING if verified else GateState.CLOSED
    await process(h.service(), read())
    event, = await rows(AccessEvent); saga, = await rows(MovementSagaRecord); ingest, = await rows(LprIngestEvent); presence, = await rows(Presence); command, = await rows(GateCommandRecord)
    assert event.decision == AccessDecision.GRANTED and event.direction == AccessDirection.ENTRY
    assert ingest.status == 'succeeded' and ingest.access_event_id == event.id and ingest.movement_saga_id == saga.id
    assert saga.access_event_id == event.id and command.movement_saga_id == saga.id
    assert presence.person_id == person_id
    assert presence.state == (PresenceState.PRESENT if verified else PresenceState.EXITED)
    assert saga.presence_committed is verified
    assert saga.state == (MovementSagaState.COMPLETED if verified else MovementSagaState.RECONCILIATION_REQUIRED if accepted else MovementSagaState.FAILED)
    assert len(h.gate_calls) == 1 and len(h.presence_effects) == int(verified)
    # Every classified observation owns durable suppression state, including rejection.
    assert len(await rows(MovementSessionRecord)) == 1
    assert any(a.action=='gate.open.automatic' for a in await rows(AuditLog))


async def test_unknown_denial_commits_and_durable_ingest_replay_never_opens_hardware(h):
    service=h.service(); plate_read=read('ZZSYN99')
    await process(service, plate_read)
    await process(h.service(), plate_read)
    event, = await rows(AccessEvent); saga, = await rows(MovementSagaRecord); ingest, = await rows(LprIngestEvent)
    assert event.decision == AccessDecision.DENIED and event.direction == AccessDirection.DENIED
    assert saga.state == MovementSagaState.COMPLETED and not saga.gate_command_required
    assert ingest.status == 'succeeded' and ingest.access_event_id == event.id
    assert not await rows(Presence) and not await rows(GateCommandRecord) and not h.gate_calls
    anomaly, = await rows(Anomaly); assert anomaly.event_id == event.id
    notification, = await rows(NotificationRun); assert notification.recovery_version==1 and notification.status=='queued'


async def test_departure_persists_presence_without_hardware_or_dvla(h):
    await resident(PresenceState.PRESENT)
    await process(h.service(), read(gate='closing'))
    event, = await rows(AccessEvent); presence, = await rows(Presence)
    assert event.direction==AccessDirection.EXIT and presence.state==PresenceState.EXITED and presence.last_event_id==event.id
    assert not h.gate_calls and not await rows(GateCommandRecord) and 'dvla' not in h.enrichment_calls


async def test_gate_already_open_arrival_records_verified_no_send_receipt_and_presence(h):
    await resident()
    h.physical_state = GateState.OPEN
    await process(h.service(), read(gate='open'))
    event, = await rows(AccessEvent); presence, = await rows(Presence); saga, = await rows(MovementSagaRecord)
    assert event.direction==AccessDirection.ENTRY and presence.state==PresenceState.PRESENT
    assert saga.state==MovementSagaState.COMPLETED and saga.admission_status == "verified"
    assert not h.gate_calls
    command, = await rows(GateCommandRecord)
    child, = await rows(AccessDeviceCommandRecord)
    assert command.command_metadata["automatic_entry_precondition"]["mode"] == "observe_only"
    assert child.state == "verified" and child.attempted_at is None and child.provider_receipts == []
    assert child.verification_evidence["state"] == "open"
    assert any(a.action=='gate.open.automatic' for a in await rows(AuditLog))


async def test_denied_schedule_never_changes_presence_or_commands(h):
    await resident(); h.runtime=replace(h.runtime,schedule_default_policy='deny')
    await process(h.service(), read())
    event, = await rows(AccessEvent); presence, = await rows(Presence)
    assert event.decision==AccessDecision.DENIED and presence.state==PresenceState.EXITED
    assert not h.gate_calls and not await rows(GateCommandRecord)


async def test_exact_session_suppression_is_durable_across_service_instances(h):
    await resident(); first=read()
    await process(h.service(),first)
    await process(h.service(),read(at=first.captured_at+timedelta(seconds=1)))
    assert len(await rows(AccessEvent))==1 and len(h.gate_calls)==1
    sagas=await rows(MovementSagaRecord); suppressed=[s for s in sagas if s.state==MovementSagaState.SUPPRESSED]
    assert len(suppressed)==1 and suppressed[0].decision_payload['suppression_reason']
    assert all(r.status=='succeeded' for r in await rows(LprIngestEvent))


async def test_visitor_arrival_and_departure_keep_pass_links_and_audit(h):
    first=read('VISIT01')
    async with AsyncSessionLocal() as s:
        visitor=VisitorPass(visitor_name='Synthetic visitor', expected_time=first.captured_at, status=VisitorPassStatus.ACTIVE, window_minutes=30)
        s.add(visitor); await s.commit(); identity=visitor.id
    await process(h.service(),first)
    await process(h.service(),read('VISIT01',gate='closing',at=first.captured_at+timedelta(minutes=5)))
    async with AsyncSessionLocal() as s:
        visitor=await s.get(VisitorPass,identity)
        assert visitor.status==VisitorPassStatus.USED and visitor.number_plate=='VISIT01'
        assert visitor.arrival_event_id and visitor.departure_event_id and visitor.duration_on_site_seconds==300
        assert visitor.vehicle_make=='Synthetic' and visitor.vehicle_colour=='Blue'
    assert len(await rows(AccessEvent))==2 and len(h.gate_calls)==1 and not await rows(Presence)
    assert {'visitor_pass.reserved','visitor_pass.reservation_settled','visitor_pass.arrival_linked','visitor_pass.departure_linked'} <= {a.action for a in await rows(AuditLog)}


async def test_unverified_arrival_reconciles_presence_without_repeating_command(h):
    await resident(); h.result_state=GateState.CLOSED
    await process(h.service(), read())
    await h.record_open_observation()
    service=reconciliation.MovementReconciliationService()
    assert await service.reconcile_once()==1
    assert await service.reconcile_once()==0
    presence, = await rows(Presence); saga, = await rows(MovementSagaRecord)
    assert presence.state==PresenceState.PRESENT and saga.presence_committed and not saga.reconciliation_required
    assert len(h.gate_calls)==1


@pytest.mark.parametrize('sample',range(3))
async def test_synthetic_gate_latency_measurement(h,record_property,sample):
    await resident(); h.delay=.04; h.started=perf_counter()
    await process(h.service(),read())
    record_property('gate_latency_ms',round(h.gate_times[0]*1000,3))
    record_property('total_latency_ms',round((perf_counter()-h.started)*1000,3))
    assert len(h.gate_calls)==1


@pytest.mark.parametrize('cancelled', [False, True])
async def test_core_failure_rolls_back_visitor_claim_and_all_effects(h, monkeypatch, cancelled):
    from app.services.access.execution import AccessExecution
    at = datetime.now(UTC)
    async with AsyncSessionLocal() as s:
        visitor = VisitorPass(visitor_name='Synthetic rollback visitor', expected_time=at,
                              status=VisitorPassStatus.ACTIVE, pass_type=VisitorPassType.ONE_TIME,
                              window_minutes=60)
        s.add(visitor); await s.commit()
    async def fail(*args, **kwargs):
        if cancelled: raise asyncio.CancelledError()
        raise RuntimeError('Synthetic transaction failure')
    monkeypatch.setattr(AccessExecution, '_build_anomalies', fail)
    with pytest.raises(asyncio.CancelledError if cancelled else RuntimeError):
        await process(h.service(), read('ROLL01', at=at))
    visitor, = await rows(VisitorPass)
    assert visitor.status == VisitorPassStatus.ACTIVE
    assert visitor.number_plate is None and visitor.arrival_event_id is None
    for model in (AccessEvent, MovementSagaRecord, MovementSessionRecord, GateCommandRecord, AccessDeviceCommandRecord, VisitorPassReservationRecord, AuditLog, Presence, NotificationRun):
        assert not await rows(model)
    assert not h.gate_calls and not h.enrichment_calls and not h.presence_effects


@pytest.mark.parametrize('cancelled', [False, True])
async def test_post_command_failure_leaves_durable_outcome_for_reconciliation(h, monkeypatch, cancelled):
    from app.services.access import execution as execution_owner
    await resident()
    real_finalize = execution_owner.finalize_in_session
    async def fail(*args, **kwargs):
        if not kwargs.get('gate_command_id'):
            return await real_finalize(*args, **kwargs)
        if cancelled: raise asyncio.CancelledError()
        raise RuntimeError('Synthetic outcome persistence failure')
    with monkeypatch.context() as m:
        m.setattr(execution_owner, 'finalize_in_session', fail)
        with pytest.raises(asyncio.CancelledError if cancelled else RuntimeError):
            await process(h.service(), read())
    command, = await rows(GateCommandRecord); saga, = await rows(MovementSagaRecord)
    assert command.accepted and saga.admission_status == 'pending' and not saga.presence_committed
    assert not h.enrichment_calls and len(h.gate_calls) == 1
    service = reconciliation.MovementReconciliationService()
    await service.reconcile_once()
    saga, = await rows(MovementSagaRecord); presence, = await rows(Presence)
    assert saga.state == MovementSagaState.COMPLETED and saga.presence_committed
    assert presence.state == PresenceState.PRESENT and len(h.gate_calls) == 1


async def test_same_read_concurrent_and_repeated_finalizers_create_one_event_and_command(h):
    from app.services.access.reads import DebounceWindow
    await resident(); plate_read = read()
    intake = h.service()
    await intake.enqueue_plate_read(plate_read)
    plate_read = intake._queue.get_nowait()
    assert await intake._claim_lpr_ingest_for_processing(plate_read)
    window = DebounceWindow(plate_read.captured_at, plate_read.captured_at, [plate_read])
    await asyncio.gather(h.service()._finalize_window(window), h.service()._finalize_window(window))
    await h.service()._finalize_window(window)
    for model in (AccessEvent, MovementSagaRecord, MovementSessionRecord, GateCommandRecord):
        assert len(await rows(model)) == 1
    assert len(h.gate_calls) == 1


async def test_enrichment_runs_after_durable_outcome_and_preserves_concurrent_event_fields(h, monkeypatch):
    await resident()
    async def snapshot(event, **kwargs):
        async with AsyncSessionLocal() as s:
            persisted = await s.get(AccessEvent, event.id)
            saga = await s.scalar(select(MovementSagaRecord))
            presence = await s.scalar(select(Presence))
            assert saga.state == MovementSagaState.COMPLETED and presence.last_event_id == event.id
            persisted.raw_payload = {**persisted.raw_payload, 'concurrent_owner': {'retained': True}}
            await s.commit()
        event.snapshot_path = 'synthetic/test.jpg'; event.snapshot_bytes = 123
        event.snapshot_content_type = 'image/jpeg'; event.snapshot_camera = 'camera.gate'
        event.snapshot_width = 20; event.snapshot_height = 10; event.snapshot_captured_at = event.occurred_at
    monkeypatch.setattr(enrichment, 'capture_access_event_snapshot', snapshot)
    await process(h.service(), read())
    event, = await rows(AccessEvent); vehicle, = await rows(Vehicle)
    assert event.snapshot_path == 'synthetic/test.jpg' and event.snapshot_bytes == 123
    assert event.raw_payload['concurrent_owner'] == {'retained': True}
    assert event.raw_payload['vehicle_visual_detection']['observed_vehicle_color'] == 'Blue'
    assert event.raw_payload['movement_saga']['presence_committed']
    assert vehicle.make == 'Synthetic' and vehicle.color == 'Blue'
    assert h.enrichment_calls == ['dvla', 'visual']


async def test_failed_optional_providers_and_realtime_do_not_block_other_stages_or_enqueue(h, monkeypatch):
    await resident()
    async def fail(*args, **kwargs): raise RuntimeError('Synthetic optional failure')
    monkeypatch.setattr(enrichment, 'lookup_normalized_vehicle_registration', fail)
    monkeypatch.setattr(enrichment, 'event_bus', SimpleNamespace(publish=fail))
    monkeypatch.setattr(hardware, 'event_bus', SimpleNamespace(publish=fail))
    await process(h.service(), read())
    event, = await rows(AccessEvent); presence, = await rows(Presence)
    assert event.raw_payload['vehicle_visual_detection']['observed_vehicle_color'] == 'Blue'
    assert 'snapshot' in h.enrichment_calls and presence.last_event_id == event.id
    assert len(h.gate_calls) == 1
    assert any(row.trigger_event == 'authorized_entry' for row in await rows(NotificationRun))


async def test_cancelled_enrichment_keeps_committed_access_and_does_not_replay(h, monkeypatch):
    await resident(); plate_read = read()
    async def cancel(*args, **kwargs): raise asyncio.CancelledError()
    monkeypatch.setattr(enrichment, 'lookup_normalized_vehicle_registration', cancel)
    with pytest.raises(asyncio.CancelledError): await process(h.service(), plate_read)
    event, = await rows(AccessEvent); saga, = await rows(MovementSagaRecord); ingest, = await rows(LprIngestEvent)
    assert saga.state == MovementSagaState.COMPLETED and saga.presence_committed
    assert ingest.status == 'succeeded' and ingest.access_event_id == event.id
    await process(h.service(), plate_read)
    assert len(await rows(AccessEvent)) == 1 and len(h.gate_calls) == 1
    notifications = await rows(NotificationRun)
    assert len(notifications) == 1 and notifications[0].trigger_event == 'authorized_entry'
    assert notifications[0].status == 'queued'  # Required intent survives optional enrichment cancellation.


async def test_ocr_variant_suppression_is_durable_across_services(h):
    await resident(); at = datetime.now(UTC)
    await process(h.service(), read(at=at))
    variant = read('SYNTHO1', gate='opening', at=at+timedelta(seconds=1))
    await process(h.service(), variant)
    assert len(await rows(AccessEvent)) == 1 and len(h.gate_calls) == 1
    ingest_rows = await rows(LprIngestEvent)
    assert all(r.status == 'succeeded' for r in ingest_rows)
    assert len(ingest_rows) == 2
    assert any(r.state == MovementSagaState.SUPPRESSED for r in await rows(MovementSagaRecord))


async def test_external_admission_and_departure_link_without_unknown_hardware(h):
    from app.services.access.execution import AccessExecution
    from app.services.access.reads import EXTERNAL_ADMISSION_PAYLOAD_KEY
    from app.services.movement.sessions import ExternalVehicleSessionMatch
    first = read('EXT99')
    await process(h.service(), first)
    service = h.service()
    execution = AccessExecution(service._movement_ledger, service._movement_sessions, service._lpr_ingest_repo())
    async with AsyncSessionLocal() as s:
        denied = await s.scalar(select(AccessEvent))
        movement = await s.scalar(select(MovementSessionRecord))
        match = ExternalVehicleSessionMatch(movement, denied, {'present': True}, 'synthetic')
        result = await execution._persist_external_gate_open_admission(s, match,
            observed_at=first.captured_at+timedelta(seconds=10), gate_payload={'state': 'open'}, runtime=h.runtime)
        assert result is not None
        arrival = result[0]
        await s.commit()
    assert not h.gate_calls
    async with AsyncSessionLocal() as s:
        arrived_session = await s.scalar(select(MovementSessionRecord).where(MovementSessionRecord.access_event_id == arrival.id))
    await process(h.service(), read('EXT99', gate='closing', at=first.captured_at+timedelta(minutes=5),
        **{EXTERNAL_ADMISSION_PAYLOAD_KEY: {'mode': 'departure', 'source': 'external_vehicle_session',
                                         'external_admission_movement_session_id': str(arrived_session.id)}}))
    events = await rows(AccessEvent)
    assert sorted((e.direction.value, e.decision.value) for e in events) == [('denied','denied'),('entry','granted'),('exit','granted')]
    assert len(await rows(Anomaly)) == 2 and not h.gate_calls and not await rows(Presence)
    assert len(await rows(MovementSessionRecord)) == 3


async def test_external_arrival_cannot_command_hardware_even_with_closed_gate_evidence(h):
    from app.services.access.reads import EXTERNAL_ADMISSION_PAYLOAD_KEY
    await process(h.service(), read('EXTNEW', **{EXTERNAL_ADMISSION_PAYLOAD_KEY: {'mode':'arrival','source':'lpr_open_gate_read'}}))
    event, = await rows(AccessEvent)
    assert event.decision == AccessDecision.GRANTED and event.direction == AccessDirection.ENTRY
    assert not h.gate_calls and not await rows(GateCommandRecord)


async def test_stale_movement_keeps_newer_presence(h):
    h.physical_state = GateState.OPEN
    person_id, _ = await resident(); at = datetime.now(UTC)
    async with AsyncSessionLocal() as s:
        presence = await s.get(Presence, person_id)
        presence.last_changed_at = at+timedelta(minutes=10)
        await s.commit()
    await process(h.service(), read(gate='open', at=at))
    presence, = await rows(Presence); saga, = await rows(MovementSagaRecord)
    assert presence.state == PresenceState.EXITED and presence.last_changed_at == at+timedelta(minutes=10)
    assert not saga.presence_committed and not h.presence_effects and not h.gate_calls


async def test_historical_backfill_commits_without_hardware_and_replay_is_idempotent(h, monkeypatch):
    from app.services import restart_backfill as backfill
    await resident()
    monkeypatch.setattr(backfill, 'get_runtime_config', AsyncMock(return_value=h.runtime))
    monkeypatch.setattr(backfill.MissedAccessEventBackfillService, '_attach_protect_thumbnail', AsyncMock())
    candidate = backfill.ProtectBackfillCandidate('synthetic-backfill-1', 'SYNTH01', datetime.now(UTC)-timedelta(minutes=15),
                                                .99, None, None, {}, {})
    assert await backfill.MissedAccessEventBackfillService()._create_backfill_event(candidate)
    assert not await backfill.MissedAccessEventBackfillService()._create_backfill_event(candidate)
    event, = await rows(AccessEvent); saga, = await rows(MovementSagaRecord)
    assert 'backfill' in event.source and saga.state == MovementSagaState.COMPLETED
    assert not saga.gate_command_required and not await rows(GateCommandRecord) and not h.gate_calls
    assert any(a.action == 'access_event.restart_backfilled' for a in await rows(AuditLog))


async def test_all_simulation_scenarios_use_extracted_fakes_and_persist(h):
    from app.simulation.scenarios import (
        SCENARIO_IDS,
        SIMULATION_SOURCE,
        FullAccessFlowRequest,
        issue_isolated_full_access_flow_capability,
        run_full_access_flow,
    )

    sentinel_at = datetime.now(UTC)
    sentinel_saga_id = uuid.uuid4()
    sentinel_session_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                MovementSagaRecord(
                    id=sentinel_saga_id,
                    idempotency_key=f'simulation-cleanup-sentinel-saga:{uuid.uuid4().hex}',
                    source=SIMULATION_SOURCE,
                    state=MovementSagaState.OBSERVED,
                    occurred_at=sentinel_at,
                ),
                MovementSessionRecord(
                    id=sentinel_session_id,
                    session_key=f'simulation-cleanup-sentinel-session:{uuid.uuid4().hex}',
                    source=SIMULATION_SOURCE,
                    registration_number='SENTINEL',
                    normalized_registration_number='SENTINEL',
                    direction=AccessDirection.DENIED,
                    decision=AccessDecision.DENIED,
                    started_at=sentinel_at,
                    last_seen_at=sentinel_at,
                ),
            ]
        )
        await session.commit()

    capability = issue_isolated_full_access_flow_capability()
    report = await run_full_access_flow(FullAccessFlowRequest(cleanup=True), capability=capability)
    assert report.status == 'passed', [(i.code, i.observed) for i in report.issues]
    assert report.summary.scenarios == 7 and report.summary.simulated_gate_actions > 0
    assert tuple(result.scenario_id for result in report.scenarios) == SCENARIO_IDS
    assert all(result.status == 'passed' for result in report.scenarios)
    assert not h.gate_calls and not h.enrichment_calls
    assert not await rows(GateCommandRecord)
    assert not await rows(AccessDeviceCommandRecord)
    assert not await rows(NotificationRun)
    assert [row.id for row in await rows(MovementSagaRecord)] == [sentinel_saga_id]
    assert [row.id for row in await rows(MovementSessionRecord)] == [sentinel_session_id]


@pytest.mark.parametrize('gate_accepted,garage_allowed', [(True,True),(True,False),(False,True)])
async def test_assigned_garage_uses_device_owner_after_verified_gate_and_schedule(h, gate_accepted, garage_allowed):
    person_id, _ = await resident()
    h.gate_accepted = gate_accepted
    provider_calls = []
    garage_state = GateState.CLOSED

    async def observe(binding, *, runtime_config=None):
        return AccessDeviceStateObservation(garage_state, datetime.now(UTC))

    async def command(binding, action, reason, *, runtime_config=None):
        nonlocal garage_state
        async with AsyncSessionLocal() as session:
            gate = await session.scalar(select(GateCommandRecord))
            assert gate.command_metadata["admission_verified"] is True
            child = await session.scalar(select(AccessDeviceCommandRecord).where(
                AccessDeviceCommandRecord.gate_command_id.is_(None)))
            assert child.state == "attempting"
        provider_calls.append((binding.external_id, action))
        garage_state = GateState.OPENING
        return AccessDeviceCommandResult(True, garage_state, 'Synthetic garage',
            provider='home_assistant', external_id=binding.external_id, delivery=CommandDelivery.ACCEPTED)

    h.garage_provider = SimpleNamespace(command_cover=command, observe_state=observe)
    async with AsyncSessionLocal() as session:
        person = await session.get(Person, person_id)
        person.garage_door_entity_ids = ['synthetic-garage']
        garage = AccessDevice(key='synthetic-garage', name='Synthetic garage', kind='garage_door')
        garage.provider_bindings = [AccessDeviceProviderBinding(provider='home_assistant', external_id='cover.synthetic_garage', enabled=True)]
        if not garage_allowed:
            # A real garage-only closed schedule must not alter the resident or gate policy.
            garage.schedule = Schedule(name=f'Synthetic denied garage {uuid.uuid4()}', time_blocks={})
        session.add(garage)
        await session.commit()
    await process(h.service(), read())
    assert len(provider_calls) == int(gate_accepted and garage_allowed)
    garage_audits = [a for a in await rows(AuditLog) if a.action == 'garage_door.open.automatic']
    assert len(garage_audits) == int(gate_accepted)
    if garage_audits:
        # Schedule refusal is definite no-send, distinct from a provider rejection.
        assert garage_audits[0].outcome == ('accepted' if garage_allowed else 'not_sent')
    direct = [row for row in await rows(AccessDeviceCommandRecord) if row.gate_command_id is None]
    assert len(direct) == int(gate_accepted)
    if direct:
        assert direct[0].state == ('verified' if garage_allowed else 'not_sent')
        assert bool(direct[0].provider_receipts) is garage_allowed


async def test_reconciliation_completed_before_finalizer_resumes_is_not_overwritten(h, monkeypatch):
    from app.services.access import execution
    await resident(); h.result_state = GateState.CLOSED
    original = execution.open_gate_for_access_event
    async def open_and_reconcile(*args, **kwargs):
        outcome = await original(*args, **kwargs)
        assert outcome.requires_reconciliation
        await h.record_open_observation()
        assert await reconciliation.MovementReconciliationService().reconcile_once() == 1
        return outcome
    monkeypatch.setattr(execution, 'open_gate_for_access_event', open_and_reconcile)
    await process(h.service(), read())
    saga, = await rows(MovementSagaRecord); event, = await rows(AccessEvent)
    assert saga.state == MovementSagaState.COMPLETED and saga.presence_committed
    assert not saga.reconciliation_required
    assert event.raw_payload['movement_saga']['state'] == 'completed'
    assert event.raw_payload['movement_saga']['presence_committed']
    assert len(h.presence_effects) == 1 and len(h.gate_calls) == 1


async def test_optional_visitor_enrichment_failure_keeps_committed_transition_snapshot(h, monkeypatch):
    first = read('VISIT02')
    async with AsyncSessionLocal() as session:
        visitor = VisitorPass(visitor_name='Synthetic visitor snapshot', expected_time=first.captured_at,
            status=VisitorPassStatus.ACTIVE, window_minutes=30)
        session.add(visitor)
        await session.commit()
    async def fail_enrichment(*args, **kwargs):
        raise RuntimeError('Synthetic optional visitor enrichment failure')
    monkeypatch.setattr(enrichment.AccessEnrichment, '_enrich_visitor', fail_enrichment)
    await process(h.service(), first)
    event, = await rows(AccessEvent)
    pass_row, = await rows(VisitorPass)
    reservation, = await rows(VisitorPassReservationRecord)
    assert reservation.state == 'consumed' and pass_row.arrival_event_id == event.id
    payload = next(payload for name, payload in h.publications if name == 'visitor_pass.used')
    assert payload['visitor_pass']['status'] == 'used'
    assert payload['visitor_pass']['arrival_event_id'] == str(event.id)
    triggers = {row.trigger_event for row in await rows(NotificationRun)}
    assert {'visitor_pass_used', 'visitor_pass_vehicle_arrived'} <= triggers
