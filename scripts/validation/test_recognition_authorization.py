"""Current authorization checks against isolated synthetic PostgreSQL only."""
from test_recovery_boundaries import isolated_resources as isolated_resources

from datetime import UTC, datetime, timedelta
import uuid

import pytest
from sqlalchemy import update

from app.db.session import AsyncSessionLocal
from app.models import AccessEvent, LprIngestEvent, Person, Schedule, Vehicle, VisitorPass
from app.models.enums import AccessDecision, AccessDirection, VisitorPassStatus, VisitorPassType
from app.services.access.authorization import RecognitionAuthorizationDenied, assert_current_recognition_authorization
from app.services.visitor_passes import VisitorPassError

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


async def observation(*, known=True, receipt=True, occurred_at=NOW, source="synthetic_lpr", payload=None):
    async with AsyncSessionLocal() as session:
        person = Person(display_name="Synthetic Person") if known else None
        if person:
            session.add(person)
            await session.flush()
        vehicle = Vehicle(registration_number="SYN123", person_id=person.id) if person else None
        if vehicle:
            session.add(vehicle)
            await session.flush()
        event = AccessEvent(vehicle_id=vehicle.id if vehicle else None, person_id=person.id if person else None,
            registration_number="SYN123", direction=AccessDirection.ENTRY, decision=AccessDecision.GRANTED,
            confidence=1, source=source, occurred_at=occurred_at, raw_payload=payload or {})
        session.add(event)
        await session.flush()
        if receipt:
            session.add(LprIngestEvent(idempotency_key=str(uuid.uuid4()), source=source, registration_number="SYN123",
                captured_at=occurred_at, received_at=NOW, status="succeeded", access_event_id=event.id))
        await session.commit()
        return event


@pytest.mark.parametrize("offset,allowed", [(60, True), (60.000001, False)])
async def test_exactly_sixty_seconds_remains_eligible(offset, allowed):
    event = await observation()
    async with AsyncSessionLocal() as session:
        if allowed:
            result = await assert_current_recognition_authorization(session, event_id=event.id, now=NOW + timedelta(seconds=offset))
            assert result.expires_at == NOW + timedelta(seconds=60)
        else:
            with pytest.raises(RecognitionAuthorizationDenied, match="older than 60"):
                await assert_current_recognition_authorization(session, event_id=event.id, now=NOW + timedelta(seconds=offset))


async def test_future_camera_timestamp_does_not_extend_receipt_eligibility():
    event = await observation(occurred_at=NOW + timedelta(days=1))
    async with AsyncSessionLocal() as session:
        with pytest.raises(RecognitionAuthorizationDenied, match="older than 60"):
            await assert_current_recognition_authorization(session, event_id=event.id, now=NOW + timedelta(seconds=61))


@pytest.mark.parametrize("kwargs", [{"receipt": False}, {"source": "historical_backfill"}, {"payload": {"backfill": {}}},
                                    {"payload": {"skip_automation_actions": True}}, {"known": False}])
async def test_missing_receipt_historical_and_unknown_observations_cannot_authorize(kwargs):
    event = await observation(**kwargs)
    async with AsyncSessionLocal() as session:
        with pytest.raises(RecognitionAuthorizationDenied):
            await assert_current_recognition_authorization(session, event_id=event.id, now=NOW)


@pytest.mark.parametrize("model,changed", [(Person, {"is_active": False}), (Vehicle, {"is_active": False}),
                                          (Vehicle, {"registration_number": "CHANGED"})])
async def test_changed_current_identity_reloads_an_existing_session(model, changed):
    event = await observation()
    async with AsyncSessionLocal() as observer:
        await assert_current_recognition_authorization(observer, event_id=event.id, now=NOW)
        await observer.commit()  # End the prior attempt checkpoint; keep its identity map.
        async with AsyncSessionLocal() as mutation:
            identity = event.person_id if model is Person else event.vehicle_id
            await mutation.execute(update(model).where(model.id == identity).values(**changed))
            await mutation.commit()
        with pytest.raises(RecognitionAuthorizationDenied, match="identity"):
            await assert_current_recognition_authorization(observer, event_id=event.id, now=NOW)


async def test_current_schedule_is_rechecked_and_explicit_standing_override_is_scoped():
    event = await observation()
    async with AsyncSessionLocal() as mutation:
        schedule = Schedule(name="Synthetic deny all", time_blocks={})
        mutation.add(schedule)
        await mutation.flush()
        await mutation.execute(update(Vehicle).where(Vehicle.id == event.vehicle_id).values(schedule_id=schedule.id))
        await mutation.commit()
    async with AsyncSessionLocal() as session:
        with pytest.raises(RecognitionAuthorizationDenied, match="permissions"):
            await assert_current_recognition_authorization(session, event_id=event.id, now=NOW)
        checkpoint = await assert_current_recognition_authorization(session, event_id=event.id, now=NOW,
                                                                   allow_vehicle_schedule_override=True)
        assert checkpoint.vehicle_id == event.vehicle_id


@pytest.mark.parametrize("status,offset,other_arrival,allowed", [
    (VisitorPassStatus.ACTIVE, 0, False, True),
    (VisitorPassStatus.ACTIVE, 30, False, False),
    (VisitorPassStatus.ACTIVE, 0, True, False),
    (VisitorPassStatus.USED, 0, False, False),
    (VisitorPassStatus.CANCELLED, 0, False, False),
    (VisitorPassStatus.EXPIRED, 0, False, False),
    (VisitorPassStatus.USED, 30, False, False),
    (VisitorPassStatus.USED, 0, True, False),
])
async def test_reservation_current_window_and_terminal_passes_control_new_hardware(status, offset, other_arrival, allowed):
    event = await observation(known=False)
    async with AsyncSessionLocal() as mutation:
        visitor = VisitorPass(visitor_name="Synthetic Visitor", expected_time=NOW - timedelta(minutes=1),
            window_minutes=2, valid_from=NOW - timedelta(minutes=1), valid_until=NOW + timedelta(seconds=30),
            status=status, pass_type=VisitorPassType.ONE_TIME, arrival_time=NOW,
            arrival_event_id=None if other_arrival else event.id, number_plate="DIFFERENT")
        mutation.add(visitor)
        await mutation.flush()
        await mutation.execute(update(AccessEvent).where(AccessEvent.id == event.id).values(raw_payload={"visitor_pass": {"id": str(visitor.id)}}))
        from app.models import VisitorPassReservationRecord
        mutation.add(VisitorPassReservationRecord(visitor_pass_id=visitor.id,
            access_event_id=uuid.uuid4() if other_arrival else event.id,
            intent_id=uuid.uuid5(event.id, "automatic-gate-open"), pass_type="one-time",
            normalized_plate=event.registration_number, state="reserved",
            dispatch_deadline=NOW + timedelta(seconds=60)))
        await mutation.commit()
    async with AsyncSessionLocal() as session:
        if allowed:
            checkpoint = await assert_current_recognition_authorization(session, event_id=event.id, now=NOW)
            assert checkpoint.visitor_pass_id == visitor.id
        else:
            with pytest.raises((VisitorPassError, RecognitionAuthorizationDenied)):
                await assert_current_recognition_authorization(session, event_id=event.id, now=NOW + timedelta(seconds=offset))


@pytest.mark.parametrize("model", [Person, Vehicle])
async def test_authority_lock_is_held_until_outer_attempt_commit(model):
    """A revocation after the read cannot commit before the durable checkpoint."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    event = await observation()
    identity = event.person_id if model is Person else event.vehicle_id
    async with AsyncSessionLocal() as attempt, AsyncSessionLocal() as revoke:
        await assert_current_recognition_authorization(attempt, event_id=event.id, now=NOW)
        await revoke.execute(text("SET LOCAL lock_timeout = '100ms'"))
        with pytest.raises(DBAPIError) as blocked:
            await revoke.execute(update(model).where(model.id == identity).values(is_active=False))
        assert blocked.value.orig.sqlstate == "55P03"
        await revoke.rollback()
        await attempt.commit()
        await revoke.execute(update(model).where(model.id == identity).values(is_active=False))
        await revoke.commit()
        with pytest.raises(RecognitionAuthorizationDenied, match="identity"):
            await assert_current_recognition_authorization(attempt, event_id=event.id, now=NOW)


@pytest.mark.parametrize("model", [Person, Vehicle])
async def test_revocation_winning_lock_refuses_checkpoint_without_poisoning_transaction(model):
    from sqlalchemy import select

    event = await observation()
    identity = event.person_id if model is Person else event.vehicle_id
    async with AsyncSessionLocal() as attempt, AsyncSessionLocal() as revoke:
        await revoke.execute(update(model).where(model.id == identity).values(is_active=False))
        with pytest.raises(RecognitionAuthorizationDenied, match="being changed"):
            await assert_current_recognition_authorization(attempt, event_id=event.id, now=NOW)
        assert await attempt.scalar(select(1)) == 1
        await revoke.commit()
        with pytest.raises(RecognitionAuthorizationDenied, match="identity"):
            await assert_current_recognition_authorization(attempt, event_id=event.id, now=NOW)


async def test_domain_notification_authority_does_not_shorten_dispatch_age_to_sixty_seconds():
    from app.services.access.authorization import assert_current_recognition_domain_authorization

    event = await observation()
    async with AsyncSessionLocal() as session:
        result = await assert_current_recognition_domain_authorization(session, event_id=event.id,
                                                                        now=NOW + timedelta(seconds=120))
        assert result.expires_at == NOW + timedelta(seconds=60)
        with pytest.raises(RecognitionAuthorizationDenied, match="older than 60"):
            await assert_current_recognition_authorization(session, event_id=event.id,
                                                          now=NOW + timedelta(seconds=120))
