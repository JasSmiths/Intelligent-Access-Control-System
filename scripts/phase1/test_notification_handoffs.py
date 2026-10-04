"""Origin transaction handoffs; collection requires the inert PostgreSQL guard."""

from test_recovery_boundaries import isolated_resources as isolated_resources

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models import NotificationRun
from app.services.notification_runs import NotificationRunStore
from app.services.notifications import NotificationService
from app.modules.notifications.base import NotificationContext

pytestmark = pytest.mark.asyncio


def payload():
    return {"event_type": "synthetic.required", "subject": "Synthetic notice", "severity": "info", "facts": {}}


async def test_reservation_is_invisible_until_origin_commit_and_rolls_back_with_origin():
    store, identity = NotificationRunStore(), uuid.uuid4()
    async with AsyncSessionLocal() as origin:
        assert await store.enqueue_in_session(origin, payload(), run_id=identity) == identity
        async with AsyncSessionLocal() as observer:
            assert await observer.get(NotificationRun, identity) is None
        await origin.rollback()
    assert await store.claim(identity) is None
    async with AsyncSessionLocal() as observer:
        assert await observer.get(NotificationRun, identity) is None


async def test_committed_handoff_is_discovered_without_wakeup_and_duplicate_is_immutable():
    store, identity = NotificationRunStore(), uuid.uuid4()
    override = [{"id": "synthetic-rule", "actions": []}]
    async with AsyncSessionLocal() as origin:
        await store.enqueue_in_session(origin, payload(), run_id=identity, rules_override=override)
        await origin.commit()
    before = await store.get(identity)
    assert before.status == "queued" and before.claim_count == 0 and before.claim_token is None
    async with AsyncSessionLocal() as origin:
        await store.enqueue_in_session(origin, {**payload(), "subject": "Must not replace"}, run_id=identity, rules_override=[])
        await origin.commit()
    retained = await store.get(identity)
    assert retained.context == payload() and retained.rules_override == override and retained.queued_at == before.queued_at
    claimed = await NotificationRunStore().claim(identity)
    assert claimed.id == identity and claimed.status == "processing" and claimed.claim_count == 1


async def test_service_participant_never_wakes_or_publishes_before_commit(monkeypatch):
    service, identity = NotificationService(), uuid.uuid4()
    def forbidden(*args, **kwargs):
        raise AssertionError("A transaction participant must not wake or perform external I/O")
    monkeypatch.setattr(service.dispatcher, "wake", forbidden)
    from app.services import notifications
    monkeypatch.setattr(notifications.event_bus, "publish", forbidden)
    context = NotificationContext(event_type="synthetic.required", subject="Synthetic notice", severity="info", facts={})
    async with AsyncSessionLocal() as origin:
        assert await service.enqueue_in_session(origin, context, dispatch_id=identity) == identity
        assert await origin.scalar(select(NotificationRun.id).where(NotificationRun.id == identity)) == identity
        await origin.rollback()
    with pytest.raises(LookupError):
        await service.run_store.get(identity)


async def test_existing_immediate_reservation_still_claims_atomically():
    store = NotificationRunStore()
    identity, claimed = await store.reserve(payload())
    assert claimed.id == identity and claimed.status == "processing" and claimed.claim_count == 1
    assert await NotificationRunStore().claim(identity) is None
    repeated, duplicate_claim = await store.reserve(payload(), run_id=identity)
    assert repeated == identity and duplicate_claim is None


async def test_arrival_captures_vehicle_absence_in_committed_notification():
    from app.models import AccessEvent, Person, Vehicle
    from app.models.enums import AccessDecision, AccessDirection
    from app.services.access.delivery import reserve_verified_arrival_notification
    from app.services.notifications import context_variables, render_template
    from app.services.workflows.vehicle_away import vehicle_time_away_seconds

    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    async with AsyncSessionLocal() as session:
        person = Person(display_name="Synthetic Away", first_name="Synthetic")
        session.add(person)
        await session.flush()
        vehicle = Vehicle(person_id=person.id, registration_number="AWAY" + uuid.uuid4().hex[:8].upper())
        other = Vehicle(person_id=person.id, registration_number="OTHER" + uuid.uuid4().hex[:8].upper())
        session.add_all([vehicle, other])
        await session.flush()

        def movement(car, direction, at, decision=AccessDecision.GRANTED):
            return AccessEvent(person_id=person.id, vehicle_id=car.id,
                registration_number=car.registration_number, direction=direction, decision=decision,
                occurred_at=at, confidence=1, source="synthetic-away", raw_payload={})

        departure = movement(vehicle, AccessDirection.EXIT, now - timedelta(hours=2, minutes=20))
        arrival = movement(vehicle, AccessDirection.ENTRY, now)
        session.add_all([departure, arrival,
            movement(other, AccessDirection.EXIT, now - timedelta(minutes=10)),
            movement(vehicle, AccessDirection.ENTRY, now - timedelta(minutes=5), AccessDecision.DENIED),
            movement(vehicle, AccessDirection.EXIT, now + timedelta(hours=1))])
        await session.flush()
        assert await vehicle_time_away_seconds(session, arrival) == 8400
        await reserve_verified_arrival_notification(session, arrival)
        await session.commit()
        run_id = uuid.uuid5(arrival.id, "access.authorized_entry.notification")
    async with AsyncSessionLocal() as observer:
        run = await observer.get(NotificationRun, run_id)
        assert run.context["facts"]["vehicle_time_away_seconds"] == "8400.0"
        context = NotificationContext(**{key: run.context[key] for key in ("event_type", "subject", "severity", "facts")})
        assert render_template("@FirstName arrived @VehicleTimeAway", context_variables(context)) == "Synthetic arrived after 2hrs 20m"
        # A later entry before this arrival prevents reusing an older departure.
        intervening = movement(vehicle, AccessDirection.ENTRY, now - timedelta(minutes=2))
        observer.add(intervening)
        await observer.flush()
        assert await vehicle_time_away_seconds(observer, arrival) is None
        await observer.rollback()


async def test_unregistered_vehicle_absence_ignores_unverified_entries():
    from app.models import AccessEvent, MovementSagaRecord
    from app.models.enums import AccessDecision, AccessDirection
    from app.services.workflows.vehicle_away import vehicle_time_away_seconds

    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    plate = "VISIT" + uuid.uuid4().hex[:8].upper()
    async with AsyncSessionLocal() as session:
        def movement(direction, at):
            return AccessEvent(registration_number=plate, direction=direction, decision=AccessDecision.GRANTED,
                occurred_at=at, confidence=1, source="synthetic-away", raw_payload={})
        departure = movement(AccessDirection.EXIT, now - timedelta(days=3, hours=7))
        pending = movement(AccessDirection.ENTRY, now - timedelta(hours=1))
        arrival = movement(AccessDirection.ENTRY, now)
        session.add_all([departure, pending, arrival])
        await session.flush()
        saga = MovementSagaRecord(idempotency_key="synthetic-away:" + uuid.uuid4().hex,
            source="synthetic-away", access_event_id=pending.id, occurred_at=pending.occurred_at,
            admission_status="pending")
        session.add(saga)
        await session.flush()
        assert await vehicle_time_away_seconds(session, arrival) == (3 * 24 + 7) * 3600
        saga.admission_status = "verified"
        await session.flush()
        assert await vehicle_time_away_seconds(session, arrival) is None
        await session.rollback()


async def test_variable_audience_is_audited_and_survives_rule_and_run_roundtrips():
    from app.models import AuditLog, NotificationRule, User
    from app.models.enums import UserRole
    from app.services.notification_rules import create_rule

    jason, other = "home_assistant_mobile:notify.mobile_app_synthetic_jason", "home_assistant_mobile:notify.mobile_app_synthetic_other"
    restriction = {"message_template": [{"occurrence": 1, "name": "VehicleTimeAway", "target_ids": [jason]}]}
    async with AsyncSessionLocal() as session:
        user = User(username="audience-" + uuid.uuid4().hex, full_name="Synthetic Admin", password_hash="synthetic-unused", role=UserRole.ADMIN, is_active=True)
        session.add(user)
        await session.flush()
        rule = await create_rule(session, {"name": "Synthetic scoped arrival", "trigger_event": "synthetic.audience", "actions": [{
            "id": "scoped", "type": "mobile", "target_mode": "selected", "target_ids": [jason, other],
            "message_template": "@FirstName arrived @VehicleTimeAway.", "variable_recipients": restriction,
        }]}, user=user, source="API")
        identity = rule.id
    async with AsyncSessionLocal() as observer:
        stored = await observer.get(NotificationRule, identity)
        assert stored.actions[0]["variable_recipients"] == restriction
        audit = await observer.scalar(select(AuditLog).where(AuditLog.target_id == str(identity)))
        assert "variable_recipients" in str(audit.diff)
    store = NotificationRunStore()
    run_id, claimed = await store.reserve({"event_type": "synthetic.audience", "subject": "Arrival", "severity": "info", "facts": {
        "first_name": "Sam", "vehicle_time_away_seconds": "8400",
    }})
    row = await store.get(run_id)
    plan = await NotificationService().prepare_delivery_plan(row)
    assert plan[0]["action"]["message"] == "Sam arrived."
    assert plan[0]["action"]["recipient_content"][jason]["message"] == "Sam arrived after 2hrs 20m."
    await store.save_plan(run_id, claimed.claim_token, plan)
    assert (await store.get(run_id)).delivery_plan[0]["action"]["recipient_content"][jason]["message"] == "Sam arrived after 2hrs 20m."
