"""Automatic phone recovery through real PostgreSQL decision/claim transactions.

The guarded namespace permits only disposable PostgreSQL/Redis. No worker,
lifespan, camera, notification sender or actuator is started by these tests.
"""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
import json
import statistics
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text

from app.db.session import AsyncSessionLocal
from app.models import (AccessEvent, LprIngestEvent, MissedExitRecoveryAttempt,
                        MovementSagaRecord, Person, Presence, ResidentRecoveryJourney,
                        SystemSetting, Vehicle)
from app.models.enums import AccessDecision, AccessDirection, PresenceState, TimingClassification
from app.modules.gate.base import CommandDelivery, GateState
from app.modules.lpr.base import PlateRead
from app.services import resident_recovery as recovery
from app.services.access import authorization, evidence, execution, hardware
from app.services.access.evidence import AccessEvidenceResolver
from app.services.access.execution import AccessExecution
from app.services.access.reads import (DebounceWindow, GATE_OBSERVATION_PAYLOAD_KEY,
    KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY, LPR_INGEST_EVENT_PAYLOAD_KEY)
from app.services.access_device_configuration import AccessDeviceConfiguration
from app.services.gate_commands import GateCommandOutcome
from app.services.lpr_ingest import LprIngestRepository
from app.services.movement.sessions import MovementSessionService
from app.services.movement_ledger import get_movement_ledger_repository
from app.services.resident_recovery_evidence import configuration_binding
from app.services.settings import get_runtime_config_for_session as load_runtime
from test_resident_recovery import target_plan

pytestmark = pytest.mark.asyncio
SETTINGS = {"missed_exit_recovery_enabled": True,
    "missed_exit_recovery_gate_latitude": 51.0, "missed_exit_recovery_gate_longitude": -1.0,
    "gate_admission_device_key": "synthetic_entry"}


class Trace:
    trace_id = "synthetic-automatic-recovery"

    def start_span(self, *args, **kwargs):
        return SimpleNamespace(finish=lambda **kwargs: None)


class Harness:
    def __init__(self):
        self.transport = {"home_assistant_url": "https://synthetic.invalid",
                          "home_assistant_token": "synthetic-only", "apprise_urls": ""}
        self.intents = []
        self.authorized = []
        self.refusals = []
        self.pause_config = None
        self.ingest = LprIngestRepository()
        self.service = AccessExecution(get_movement_ledger_repository(),
            MovementSessionService(), self.ingest)

    async def runtime(self, session):
        pause, self.pause_config = self.pause_config, None
        if pause:
            entered, release = pause
            entered.set()
            await release.wait()
        return replace(await load_runtime(session), site_timezone="UTC",
                       schedule_default_policy="allow", **self.transport)

    async def current_runtime(self):
        async with AsyncSessionLocal() as session:
            return await self.runtime(session)

    async def execute_open(self, intent):
        self.intents.append(intent)
        # Exercise the real final automatic authority seam after the decision,
        # intake success and one-shot journey claim have committed independently.
        async with AsyncSessionLocal() as session:
            event = await session.get(AccessEvent, uuid.UUID(intent.event_id))
            intake = await session.scalar(select(LprIngestEvent).where(
                LprIngestEvent.access_event_id == event.id))
            trip = await session.get(ResidentRecoveryJourney, event.person_id)
            assert event.decision == AccessDecision.GRANTED
            assert intake.status == "succeeded"
            assert trip.claimed_event_id == event.id and trip.consumed_at is not None
            durable_recovery = event.raw_payload["direction_resolution"].get("missed_exit_recovery")
            if durable_recovery:
                assert durable_recovery["target_plan"] == target_plan() == intent.target_plan
            try:
                await intent.authorize_dispatch(session)
            except ValueError as exc:
                self.refusals.append(str(exc))
            else:
                self.authorized.append(event.id)
            await session.rollback()
        now = datetime.now(UTC)
        return GateCommandOutcome(intent=intent, accepted=False, state=GateState.UNKNOWN,
            detail="Synthetic no-send; final authority checked", started_at=now, completed_at=now,
            command_id=None, delivery=CommandDelivery.NOT_SENT, reconciliation_required=False)

    async def execute(self, read):
        return await self.service.execute(DebounceWindow(read.captured_at, read.captured_at, [read]),
            read=read, direction_read=read, runtime=await self.current_runtime(), trace=Trace(),
            finalize_started_at=datetime.now(UTC), webhook_trace={})

    async def assert_dispatch(self, intent):
        async with AsyncSessionLocal() as session:
            await intent.authorize_dispatch(session)
            await session.rollback()


async def connect(runtime):
    # Vendor lifecycle binds the authenticated socket to the current transport.
    from app.modules.home_assistant.client import home_assistant_connection_fingerprint
    recovery.invalidate_connection(connected=True,
        connection_binding=home_assistant_connection_fingerprint(runtime))


@pytest_asyncio.fixture(autouse=True)
async def h(isolated_resources, monkeypatch):
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE people, vehicles, access_events, movement_sagas, movement_sessions, lpr_ingest_events, notification_action_contexts, notification_runs CASCADE"))
        session.add_all([SystemSetting(key=key, category="missed_exit_recovery", value={"plain": value},
            is_secret=False) for key, value in SETTINGS.items()])
        await session.commit()
    value = Harness()
    for module in (recovery, execution, authorization):
        monkeypatch.setattr(module, "get_runtime_config_for_session", value.runtime)
    monkeypatch.setattr(evidence, "get_runtime_config", value.current_runtime)
    monkeypatch.setattr(AccessDeviceConfiguration, "preview_gate_open", AsyncMock(return_value=target_plan()))
    monkeypatch.setattr(AccessEvidenceResolver, "_resolve_duplicate_arrival_with_camera",
        AsyncMock(return_value={"direction": "unknown", "confidence": 0.0,
                               "reason": "Synthetic inert camera"}))
    monkeypatch.setattr(hardware, "get_gate_command_coordinator",
        lambda: SimpleNamespace(execute_open=value.execute_open))
    await connect(await value.current_runtime())
    try:
        yield value
    finally:
        recovery.invalidate_connection()
        async with AsyncSessionLocal() as session:
            await session.execute(delete(SystemSetting).where(SystemSetting.key.in_(SETTINGS)))
            await session.commit()


async def seed(h, *, trip=True):
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        person = Person(display_name="Synthetic automatic owner", is_active=True,
            missed_exit_recovery_enabled=True, missed_exit_recovery_tracker_entity_id="device_tracker.synthetic",
            home_assistant_mobile_app_notify_service="notify.mobile_app_synthetic_automatic")
        session.add(person)
        await session.flush()
        vehicles = [Vehicle(registration_number=plate, person_id=person.id, is_active=True)
                    for plate in ("SYNPA1", "SYNPA2")]
        session.add_all(vehicles)
        await session.flush()
        entries = [AccessEvent(vehicle_id=vehicle.id, person_id=person.id,
            registration_number=vehicle.registration_number, direction=AccessDirection.ENTRY,
            decision=AccessDecision.GRANTED, confidence=1, source="synthetic_prior_lpr",
            occurred_at=now-timedelta(hours=1), timing_classification=TimingClassification.UNKNOWN,
            raw_payload={}) for vehicle in vehicles]
        session.add_all(entries)
        await session.flush()
        session.add(Presence(person_id=person.id, state=PresenceState.PRESENT,
            last_changed_at=entries[0].occurred_at, last_event_id=entries[0].id))
        if trip:
            samples = [{"at": (now-timedelta(seconds=age)).isoformat(), "distance_m": distance,
                "accuracy_m": 20} for age,distance in [(420,1500), (120,700), (60,350), (5,100)]]
            session.add(ResidentRecoveryJourney(person_id=person.id, epoch=recovery._epoch,
                binding=configuration_binding(person, await h.runtime(session)), samples=samples,
                latest_at=now-timedelta(seconds=5)))
        await session.commit()
        return SimpleNamespace(person=person, vehicles=vehicles, now=now)


async def plate_read(h, item, index=0, *, gate="closed", explicit=None):
    async with AsyncSessionLocal() as session:
        captured = await session.scalar(select(func.clock_timestamp()))
    vehicle = item.vehicles[index]
    raw = {"cameraId": "synthetic-camera", "eventId": str(uuid.uuid4()),
        GATE_OBSERVATION_PAYLOAD_KEY: {"state": gate, "observed_at": captured.isoformat()},
        KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY: {"exact": True,
            "registration_number": vehicle.registration_number,
            "observed_registration_number": vehicle.registration_number}}
    if explicit:
        raw["direction"] = explicit
    read = PlateRead(vehicle.registration_number, 1.0, "synthetic_automatic_lpr", captured, raw)
    intake, created = await h.ingest.persist_read(read, received_at=captured,
        idempotency_key=f"synthetic-auto:{raw['eventId']}", normalized_payload={
            "registration_number": read.registration_number, "captured_at": captured.isoformat(),
            "source": read.source, "confidence": read.confidence, "raw_payload": raw})
    assert created
    return replace(read, raw_payload={**raw, LPR_INGEST_EVENT_PAYLOAD_KEY: {"id": str(intake.id)}})


async def rows(model):
    async with AsyncSessionLocal() as session:
        return list((await session.scalars(select(model))).all())


async def trip_for(item):
    async with AsyncSessionLocal() as session:
        return await session.get(ResidentRecoveryJourney, item.person.id)


async def gps_state(latitude):
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
    return {"state": "not_home", "last_updated": now.isoformat(), "attributes": {
        "source_type": "gps", "latitude": latitude, "longitude": -1.0, "gps_accuracy": 20}}


async def test_warm_ready_phone_local_evaluation_records_timings(h, capsys):
    item = await seed(h)
    read = await plate_read(h, item)
    measurements = []
    async with AsyncSessionLocal() as session:
        config = await h.runtime(session)
        for index in range(23):
            ready, reason, checks = await recovery.evaluate_phone(session,
                person=item.person, vehicle=item.vehicles[0], read=read, config=config)
            assert ready and reason == "phone_return_corroborated"
            if index >= 3:
                measurements.append(checks["phone_evaluation_ms"])
        await session.rollback()
    # Evidence, not a flaky wall-clock assertion or physical gate latency claim.
    with capsys.disabled():
        print("WARM_PHONE_EVALUATION " + json.dumps({"samples_ms": measurements,
            "median_ms": statistics.median(measurements), "max_ms": max(measurements)}))


async def test_concurrent_exact_reads_for_two_owned_cars_claim_at_most_one_return_and_replay_is_inert(h, monkeypatch):
    item = await seed(h)
    observations = [await plate_read(h, item, index) for index in (0, 1)]
    original = AccessEvidenceResolver.prepare_camera_evidence
    arrived = 0
    release = asyncio.Event()

    async def both_preflights(self, *args, **kwargs):
        nonlocal arrived
        result = await original(self, *args, **kwargs)
        arrived += 1
        if arrived == 2:
            release.set()
        await release.wait()
        return result

    monkeypatch.setattr(AccessEvidenceResolver, "prepare_camera_evidence", both_preflights)
    results = await asyncio.wait_for(asyncio.gather(*(h.execute(read) for read in observations)), 8)
    assert all(result is not None for result in results)
    attempts = await rows(MissedExitRecoveryAttempt)
    grants = [attempt for attempt in attempts if attempt.method == "phone_automatic"]
    assert len(attempts) == 2 and len(grants) == 1
    trip = await trip_for(item)
    assert trip.claimed_event_id == grants[0].event_id
    assert trip.consumed_at is not None and trip.samples == []
    live_sagas = [row for row in await rows(MovementSagaRecord) if row.source == "synthetic_automatic_lpr"]
    assert len(live_sagas) == 2 and sum(row.gate_command_required for row in live_sagas) == 1
    assert len(h.intents) == 1 and len(h.authorized) <= 1
    winner = next(read for read in observations if read.registration_number == grants[0].registration_number)
    assert await h.execute(winner) is None
    assert len(await rows(MissedExitRecoveryAttempt)) == 2
    assert len(h.intents) == 1
    assert all(row.status == "succeeded" and row.access_event_id for row in await rows(LprIngestEvent))


async def test_normal_committed_owner_entry_consumes_old_trip_across_all_vehicles(h):
    item = await seed(h)
    async with AsyncSessionLocal() as session:
        person = await session.get(Person, item.person.id)
        person.missed_exit_recovery_enabled = False
        # The ordinary open-gate arrival path requires an absent owner. A
        # PRESENT owner at an open gate is intentionally classified as EXIT,
        # before considering the camera's explicit direction hint.
        presence = await session.get(Presence, item.person.id)
        presence.state = PresenceState.EXITED
        await session.commit()
    observation = await plate_read(h, item, gate="open", explicit="entry")
    result = await h.execute(observation)
    assert result.event.decision == AccessDecision.GRANTED and result.event.direction == AccessDirection.ENTRY
    assert "missed_exit_recovery" not in result.direction_resolution
    trip = await trip_for(item)
    assert trip.samples == [] and trip.consumed_at is not None
    assert trip.claimed_event_id == result.event.id
    assert len(h.intents) == 1 and h.authorized == [result.event.id], h.refusals
    assert h.intents[0].target_plan is None  # Ordinary coordinator authority.


@pytest.mark.parametrize("mutation", ["near_gate", "new_away", "config", "reconnect"])
async def test_final_automatic_dispatch_uses_current_one_shot_trip_authority(h, mutation):
    item = await seed(h)
    result = await h.execute(await plate_read(h, item))
    assert result.direction_resolution["missed_exit_recovery"]["method"] == "phone_automatic"
    assert h.authorized == [result.event.id], h.refusals
    intent = h.intents[0]
    claimed = await trip_for(item)
    if mutation in {"near_gate", "new_away"}:
        new = await gps_state(51.0008 if mutation == "near_gate" else 51.01)
        old = await gps_state(51.001)
        await recovery.observe_tracker(item.person.missed_exit_recovery_tracker_entity_id, new, old)
    elif mutation == "config":
        async with AsyncSessionLocal() as session:
            setting = await session.get(SystemSetting, "missed_exit_recovery_gate_latitude")
            setting.value = {"plain": 51.01}
            await session.commit()
    else:
        await connect(await h.current_runtime())
    if mutation == "near_gate":
        current = await trip_for(item)
        assert current.claimed_event_id == claimed.claimed_event_id
        assert current.latest_at == claimed.latest_at and current.consumed_at == claimed.consumed_at
        await h.assert_dispatch(intent)
    else:
        with pytest.raises(ValueError):
            await h.assert_dispatch(intent)
    assert len(h.intents) == 1  # Dispatch checks never replay the inert command.


async def test_inflight_tracker_update_cannot_seed_an_epoch_created_while_waiting(h):
    item = await seed(h, trip=False)
    new, old = await gps_state(51.01), await gps_state(51.012)
    entered, release = asyncio.Event(), asyncio.Event()
    h.pause_config = entered, release
    task = asyncio.create_task(recovery.observe_tracker(item.person.missed_exit_recovery_tracker_entity_id, new, old))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        # Reconnect invalidates the already-received callback's epoch.
        await connect(await h.current_runtime())
        release.set()
        await asyncio.wait_for(task, 8)
        assert await trip_for(item) is None
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_old_authenticated_socket_cannot_rebind_evidence_after_transport_configuration_changes(h):
    item = await seed(h)
    original = await trip_for(item)
    h.transport["home_assistant_token"] = "synthetic-replacement-value"
    new, old = await gps_state(51.0008), await gps_state(51.001)
    await recovery.observe_tracker(item.person.missed_exit_recovery_tracker_entity_id, new, old)
    current = await trip_for(item)
    assert current.epoch == original.epoch and current.binding == original.binding
    assert current.samples == original.samples and current.latest_at == original.latest_at
