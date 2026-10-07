"""Visitor reservation/revocation/consumption against isolated synthetic PostgreSQL."""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
from datetime import UTC, datetime, timedelta
import uuid

import pytest
from sqlalchemy import func, select

from app.db.session import AsyncSessionLocal
from app.models import AccessDeviceCommandRecord, AccessEvent, AutomationRule, AutomationRun, GateCommandRecord, MovementSagaRecord, NotificationRun, Person, Presence, VisitorPass, VisitorPassReservationRecord
from app.models.enums import AccessDecision, AccessDirection, MovementSagaState, VisitorPassStatus, VisitorPassType
from app.modules.gate.base import CommandDelivery, GateState
from app.services.access_device_commands import AccessDeviceCommandJournal
from app.services.movement.admission import finalize_in_session
from app.services.visitor_passes import VisitorPassError, VisitorPassReservationConflict, get_visitor_pass_service
from test_movement_admission import evidence

pytestmark = pytest.mark.asyncio


async def context(*, pass_type=VisitorPassType.ONE_TIME, status=VisitorPassStatus.ACTIVE):
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        visitor = VisitorPass(visitor_name="Synthetic Visitor", pass_type=pass_type, status=status,
            expected_time=now, window_minutes=30, number_plate="SYN123" if pass_type == VisitorPassType.DURATION else None)
        session.add(visitor)
        await session.commit()
        return visitor.id, now


async def event_for(visitor_id, occurred, *, person_id=None):
    async with AsyncSessionLocal() as session:
        event = AccessEvent(registration_number="SYN123", direction=AccessDirection.ENTRY,
            decision=AccessDecision.GRANTED, confidence=1, source="synthetic", occurred_at=occurred,
            person_id=person_id, raw_payload={"visitor_pass": {"id": str(visitor_id), "mode": "arrival"}})
        session.add(event)
        await session.flush()
        saga = MovementSagaRecord(idempotency_key=f"synthetic:{event.id}", source="synthetic", occurred_at=occurred,
            access_event_id=event.id, direction=event.direction, decision=event.decision,
            state=MovementSagaState.PHYSICAL_COMMAND_PENDING, intent_payload={}, decision_payload={}, state_history=[])
        session.add(saga)
        await session.commit()
        return event, saga.id


async def reserve(visitor_id, event):
    async with AsyncSessionLocal() as session:
        result = await get_visitor_pass_service().reserve_arrival_in_session(session,
            visitor_pass_id=visitor_id, event=event, intent_id=uuid.uuid5(event.id, "automatic-gate-open"),
            dispatch_deadline=event.occurred_at + timedelta(seconds=60))
        await session.commit()
        return result.id


async def finalized(saga_id):
    async with AsyncSessionLocal() as session:
        result = await finalize_in_session(session, saga_id=saga_id)
        await session.commit()
        return result


async def rows(visitor_id, reservation_id):
    async with AsyncSessionLocal() as session:
        return await session.get(VisitorPass, visitor_id), await session.get(VisitorPassReservationRecord, reservation_id)


async def test_reservation_is_exclusive_and_does_not_consume_or_bind_pass():
    visitor_id, now = await context()
    events = [await event_for(visitor_id, now) for _ in range(2)]
    start = asyncio.Event()

    async def attempt(event):
        await start.wait()
        return await reserve(visitor_id, event)

    tasks = [asyncio.create_task(attempt(event)) for event, _saga in events]
    start.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 8)
    assert sum(isinstance(item, uuid.UUID) for item in results) == 1
    assert sum(isinstance(item, VisitorPassReservationConflict) for item in results) == 1
    identity = next(item for item in results if isinstance(item, uuid.UUID))
    visitor, reservation = await rows(visitor_id, identity)
    assert visitor.status == VisitorPassStatus.ACTIVE and visitor.number_plate is None
    assert visitor.arrival_time is None and visitor.arrival_event_id is None
    assert reservation.state == "reserved"


@pytest.mark.parametrize("delivery,expected", [(CommandDelivery.ACCEPTED, "held"),
    (CommandDelivery.UNKNOWN, "held"), (CommandDelivery.NOT_SENT, "released"), (CommandDelivery.REJECTED, "released")])
async def test_only_verified_admission_consumes_and_conclusive_failure_releases(delivery, expected):
    visitor_id, now = await context()
    event, saga_id = await event_for(visitor_id, now)
    identity = await reserve(visitor_id, event)
    await evidence(event.id, saga_id, delivery=delivery)
    await finalized(saga_id)
    visitor, reservation = await rows(visitor_id, identity)
    assert reservation.state == expected
    assert visitor.status == VisitorPassStatus.ACTIVE and visitor.arrival_time is None and visitor.number_plate is None
    if expected == "released":
        another, _ = await event_for(visitor_id, now)
        assert await reserve(visitor_id, another) != identity
    else:
        another, _ = await event_for(visitor_id, now)
        with pytest.raises(VisitorPassReservationConflict):
            await reserve(visitor_id, another)


@pytest.mark.parametrize("pass_type", [VisitorPassType.ONE_TIME, VisitorPassType.DURATION])
async def test_verified_entry_consumes_once_and_replay_cannot_retime_arrival(pass_type):
    visitor_id, now = await context(pass_type=pass_type)
    event, saga_id = await event_for(visitor_id, now)
    identity = await reserve(visitor_id, event)
    await evidence(event.id, saga_id, verified=True)
    first = await finalized(saga_id)
    visitor, reservation = await rows(visitor_id, identity)
    assert reservation.state == "consumed" and reservation.verification_evidence["state"] == "open"
    assert visitor.arrival_event_id == event.id and visitor.arrival_time == event.occurred_at
    assert visitor.number_plate == "SYN123"
    assert visitor.status == (VisitorPassStatus.USED if pass_type == VisitorPassType.ONE_TIME else VisitorPassStatus.ACTIVE)
    completed = reservation.completed_at
    assert not (await finalized(saga_id)).changed
    assert (await rows(visitor_id, identity))[1].completed_at == completed
    async with AsyncSessionLocal() as session:
        with pytest.raises(VisitorPassError):
            await get_visitor_pass_service().assert_dispatch_validity(session, event=event,
                visitor_pass_id=visitor_id, checked_at=now)
        await session.rollback()
        assert await get_visitor_pass_service().assert_arrival_notification_validity(session, event=event,
            visitor_pass_id=visitor_id, checked_at=now) == visitor_id


@pytest.mark.parametrize("terminal", [VisitorPassStatus.CANCELLED, VisitorPassStatus.EXPIRED])
async def test_revoked_or_expired_held_arrival_settles_late_evidence_without_renewing_pass(terminal):
    visitor_id, now = await context()
    event, saga_id = await event_for(visitor_id, now)
    identity = await reserve(visitor_id, event)
    parent_id = await evidence(event.id, saga_id, delivery=CommandDelivery.UNKNOWN)
    await finalized(saga_id)
    async with AsyncSessionLocal() as session:
        visitor = await session.get(VisitorPass, visitor_id)
        if terminal == VisitorPassStatus.CANCELLED:
            await get_visitor_pass_service().cancel_pass(session, visitor)
        else:
            await get_visitor_pass_service().refresh_statuses(session=session, now=now + timedelta(hours=1), publish=False)
        await session.commit()
        assert visitor.status == terminal
        child = await session.scalar(select(AccessDeviceCommandRecord).where(AccessDeviceCommandRecord.gate_command_id == parent_id))
    await AccessDeviceCommandJournal().reconcile_observation(child.id, binding_fingerprint=child.binding_fingerprint,
        observation={"provider": "home_assistant", "state": "open", "observed_at": datetime.now(tz=UTC)})
    assert (await finalized(saga_id)).admission_status == "verified"
    visitor, reservation = await rows(visitor_id, identity)
    assert visitor.status == terminal and reservation.state == "consumed"
    assert visitor.arrival_event_id == event.id and reservation.verification_evidence["target_device_id"]


@pytest.mark.parametrize("operation", ["update", "plate", "delete"])
async def test_mutations_cannot_rebind_or_delete_reserved_arrival(operation):
    visitor_id, now = await context()
    event, _ = await event_for(visitor_id, now)
    await reserve(visitor_id, event)
    async with AsyncSessionLocal() as session:
        visitor = await session.get(VisitorPass, visitor_id)
        service = get_visitor_pass_service()
        with pytest.raises(VisitorPassError, match="resolved"):
            if operation == "update":
                await service.update_pass(session, visitor, visitor_name="Changed")
            elif operation == "plate":
                await service.update_visitor_plate(session, visitor, new_plate="OTHER")
            else:
                await service.delete_pass(session, visitor)
        await session.rollback()


async def test_current_cancellation_prevents_dispatch_without_destroying_reservation_evidence():
    visitor_id, now = await context()
    event, _ = await event_for(visitor_id, now)
    identity = await reserve(visitor_id, event)
    async with AsyncSessionLocal() as reader:
        assert await get_visitor_pass_service().assert_dispatch_validity(reader, event=event,
            visitor_pass_id=visitor_id, checked_at=now) == visitor_id
        await reader.commit()
        async with AsyncSessionLocal() as writer:
            visitor = await writer.get(VisitorPass, visitor_id)
            await get_visitor_pass_service().cancel_pass(writer, visitor)
            await writer.commit()
        with pytest.raises(VisitorPassError):
            await get_visitor_pass_service().assert_dispatch_validity(reader, event=event,
                visitor_pass_id=visitor_id, checked_at=now)
    visitor, reservation = await rows(visitor_id, identity)
    assert visitor.status == VisitorPassStatus.CANCELLED and reservation.state == "reserved"


async def test_legacy_used_is_never_available_for_a_new_reservation_or_hardware():
    visitor_id, now = await context(status=VisitorPassStatus.USED)
    event, _ = await event_for(visitor_id, now)
    with pytest.raises(VisitorPassReservationConflict):
        await reserve(visitor_id, event)
    async with AsyncSessionLocal() as session:
        visitor = await session.get(VisitorPass, visitor_id)
        visitor.arrival_event_id, visitor.arrival_time = event.id, now
        await session.commit()
        with pytest.raises(VisitorPassError):
            await get_visitor_pass_service().assert_dispatch_validity(session, event=event,
                visitor_pass_id=visitor_id, checked_at=now)
        await session.rollback()
        assert await get_visitor_pass_service().assert_arrival_notification_validity(session, event=event,
            visitor_pass_id=visitor_id, checked_at=now) == visitor_id


async def test_required_visitor_created_handoff_rolls_back_with_origin_and_survives_commit():
    async with AsyncSessionLocal() as session:
        rule = AutomationRule(name="Synthetic visitor observer", is_active=True,
            triggers=[{"type": "visitor_pass.created", "config": {}}], trigger_keys=["visitor_pass.created"],
            conditions=[], actions=[])
        session.add(rule)
        await session.commit()
    async with AsyncSessionLocal() as session:
        await get_visitor_pass_service().create_pass(session, visitor_name="Synthetic Visitor", expected_time=datetime.now(tz=UTC))
        assert await session.scalar(select(func.count()).select_from(AutomationRun).where(AutomationRun.rule_id == rule.id)) == 1
        await session.rollback()
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AutomationRun).where(AutomationRun.rule_id == rule.id)) == 0
        visitor = await get_visitor_pass_service().create_pass(session, visitor_name="Synthetic Visitor", expected_time=datetime.now(tz=UTC))
        await session.commit()
        run = await session.scalar(select(AutomationRun).where(AutomationRun.rule_id == rule.id))
        assert run.trigger_payload["visitor_pass"]["id"] == str(visitor.id)
        assert run.status == "queued"


async def add_secondary_receipt(parent_id, *, delivery):
    journal = AccessDeviceCommandJournal()
    target = {"target_device_id": str(uuid.uuid4()), "device_key": "synthetic_secondary", "kind": "gate",
        "binding_fingerprint": "b" * 64,
        "binding_snapshot": {"providers": [{"provider": "home_assistant", "external_id": "cover.synthetic_secondary"}]}}
    async with AsyncSessionLocal() as session:
        parent = await session.get(GateCommandRecord, parent_id, with_for_update=True)
        metadata = dict(parent.command_metadata)
        metadata["target_plan"] = {**metadata["target_plan"], "targets": [*metadata["target_plan"]["targets"], target]}
        parent.command_metadata = metadata
        claim = await journal.claim(session, target=target, action="open", intent_id=metadata["intent_id"],
            operation_key=metadata["intent_id"], gate_command_id=str(parent_id), expires_at=None)
        await session.commit()
    async with AsyncSessionLocal() as session:
        assert await journal.begin_attempt(session, claim, provider="home_assistant", external_id="cover.synthetic_secondary")
        await session.commit()
    await journal.finish_attempt(claim, delivery=delivery, state=GateState.UNKNOWN, detail="Synthetic secondary receipt")


@pytest.mark.parametrize("secondary", [CommandDelivery.REJECTED, CommandDelivery.UNKNOWN])
async def test_verified_designated_entry_consumes_and_advances_presence_despite_other_target(secondary):
    visitor_id, now = await context()
    async with AsyncSessionLocal() as session:
        person = Person(display_name="Synthetic Visitor Person")
        session.add(person)
        await session.commit()
    event, saga_id = await event_for(visitor_id, now, person_id=person.id)
    reservation_id = await reserve(visitor_id, event)
    parent_id = await evidence(event.id, saga_id, verified=True)
    await add_secondary_receipt(parent_id, delivery=secondary)
    result = await finalized(saga_id)
    assert result.admission_status == "verified" and result.presence_changed
    assert result.requires_reconciliation is (secondary == CommandDelivery.UNKNOWN)
    visitor, reservation = await rows(visitor_id, reservation_id)
    assert reservation.state == "consumed" and visitor.status == VisitorPassStatus.USED
    async with AsyncSessionLocal() as session:
        assert (await session.get(Presence, person.id)).last_event_id == event.id
        triggers = set((await session.scalars(select(NotificationRun.trigger_event))).all())
        assert {"authorized_entry", "visitor_pass_used", "visitor_pass_vehicle_arrived"} <= triggers


async def test_admission_consumption_presence_and_required_deliveries_rollback_together():
    visitor_id, now = await context()
    async with AsyncSessionLocal() as session:
        person = Person(display_name="Synthetic Visitor Person")
        rule = AutomationRule(name="Synthetic verified visitor observer", is_active=True,
            triggers=[{"type": "visitor_pass.used", "config": {}}], trigger_keys=["visitor_pass.used"],
            conditions=[], actions=[])
        session.add_all([person, rule])
        await session.commit()
    event, saga_id = await event_for(visitor_id, now, person_id=person.id)
    reservation_id = await reserve(visitor_id, event)
    await evidence(event.id, saga_id, verified=True)
    async with AsyncSessionLocal() as session:
        result = await finalize_in_session(session, saga_id=saga_id)
        assert result.presence_changed
        assert (await session.get(VisitorPassReservationRecord, reservation_id)).state == "consumed"
        assert await session.scalar(select(func.count()).select_from(NotificationRun)) == 3
        assert await session.scalar(select(func.count()).select_from(AutomationRun).where(AutomationRun.rule_id == rule.id)) == 1
        await session.rollback()
    visitor, reservation = await rows(visitor_id, reservation_id)
    assert reservation.state == "reserved" and visitor.arrival_event_id is None
    async with AsyncSessionLocal() as session:
        assert await session.get(Presence, person.id) is None
        assert (await session.get(MovementSagaRecord, saga_id)).admission_status is None
        assert await session.scalar(select(func.count()).select_from(NotificationRun)) == 0
        assert await session.scalar(select(func.count()).select_from(AutomationRun).where(AutomationRun.rule_id == rule.id)) == 0
    assert (await finalized(saga_id)).admission_status == "verified"
    assert not (await finalized(saga_id)).changed
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationRun)) == 3
        assert await session.scalar(select(func.count()).select_from(AutomationRun).where(AutomationRun.rule_id == rule.id)) == 1


@pytest.mark.parametrize("action,expected", [
    ("create", {"visitor_pass_created"}), ("cancel", {"visitor_pass_cancelled"}),
    ("expire", {"visitor_pass_expired"}), ("depart", {"visitor_pass_vehicle_exited"}),
])
async def test_visitor_transition_notifications_are_atomic_and_keep_existing_event_types(action, expected):
    visitor_id, now = await context()
    async with AsyncSessionLocal() as session:
        visitor = await session.get(VisitorPass, visitor_id)
        service = get_visitor_pass_service()
        if action == "create":
            await service.create_pass(session, visitor_name="Synthetic New Visitor", expected_time=now)
        elif action == "cancel":
            await service.cancel_pass(session, visitor)
        elif action == "expire":
            await service.refresh_statuses(session=session, now=now + timedelta(hours=1), publish=False)
        else:
            event = AccessEvent(registration_number="SYN123", direction=AccessDirection.EXIT,
                decision=AccessDecision.GRANTED, confidence=1, source="synthetic", occurred_at=now)
            session.add(event)
            await session.flush()
            visitor.arrival_time = now - timedelta(minutes=5)
            await session.flush()
            await service.record_departure(session, visitor, event=event)
        assert set((await session.scalars(select(NotificationRun.trigger_event))).all()) == expected
        await session.rollback()
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationRun)) == 0
