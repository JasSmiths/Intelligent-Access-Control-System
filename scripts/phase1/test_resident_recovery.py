"""Resident recovery transaction contracts, disposable PostgreSQL only.

Collection requires the existing network-none synthetic guard. Every gate,
mobile transport, camera and provider is inert; no lifespan/worker starts.
"""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text, update

from app.db.session import AsyncSessionLocal
from app.models import (AccessEvent, MissedExitRecoveryAttempt, MovementSagaRecord, NotificationActionContext,
                        NotificationRun, Person, ResidentRecoveryJourney, Vehicle, LprIngestEvent)
from app.models.enums import AccessDecision, AccessDirection, MovementSagaState, TimingClassification
from app.services import resident_recovery as recovery
from app.services.resident_recovery_evidence import configuration_binding
from app.services.access_device_configuration import AccessDeviceConfiguration
from app.services.gate_commands import GateCommandOutcome
from app.modules.gate.base import GateState, CommandDelivery
from app.services.movement.admission import finalize_in_session
from test_movement_admission import evidence as gate_evidence

pytestmark = pytest.mark.asyncio


def config():
    return SimpleNamespace(missed_exit_recovery_enabled=True, missed_exit_recovery_gate_latitude=51.0,
        missed_exit_recovery_gate_longitude=-1.0, gate_admission_device_key="synthetic_entry",
        home_assistant_url="https://synthetic.invalid", home_assistant_token="synthetic-only",
        apprise_urls="", site_timezone="Europe/London", schedule_default_policy="allow")


def target_plan():
    return {"version": 1, "target_device_key": "synthetic_entry", "automatic_entry_policy": True,
            "require_admission": True, "targets": [{"target_device_id": "00000000-0000-0000-0000-000000000001",
            "device_key": "synthetic_entry", "binding_fingerprint": "synthetic-gate-binding-v1"}],
            "admission_target_device_id": "00000000-0000-0000-0000-000000000001"}


@pytest_asyncio.fixture(autouse=True)
async def resources(isolated_resources, monkeypatch):
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE people, access_events, movement_sagas, notification_action_contexts, notification_runs CASCADE"))
        await session.commit()
    runtime = config()
    monkeypatch.setattr(recovery, "get_runtime_config_for_session", AsyncMock(return_value=runtime))
    monkeypatch.setattr(AccessDeviceConfiguration, "preview_gate_open", AsyncMock(return_value=target_plan()))
    from app.modules.home_assistant.client import home_assistant_connection_fingerprint
    recovery.invalidate_connection(connected=True, connection_binding=home_assistant_connection_fingerprint(runtime))
    yield runtime
    recovery.invalidate_connection()


async def fixture(runtime, *, capture_age=0, allow=True, owner_id=None, plate="SYNREC1"):
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        person = await session.get(Person, owner_id) if owner_id else Person(display_name="Synthetic Recovery",
            is_active=True, missed_exit_recovery_enabled=True, missed_exit_recovery_tracker_entity_id="device_tracker.synthetic",
            home_assistant_mobile_app_notify_service="notify.mobile_app_synthetic_recovery")
        if not owner_id:
            session.add(person)
            await session.flush()
        vehicle = Vehicle(registration_number=plate, person_id=person.id, is_active=True)
        session.add(vehicle)
        await session.flush()
        event = AccessEvent(vehicle_id=vehicle.id, person_id=person.id, registration_number=plate,
            direction=AccessDirection.ENTRY, decision=AccessDecision.DENIED, confidence=1,
            source="synthetic_lpr", occurred_at=now-timedelta(seconds=capture_age),
            timing_classification=TimingClassification.UNKNOWN, raw_payload={"direction_resolution": {
                "missed_exit_recovery": {"approval_eligible": allow}}})
        session.add(event)
        await session.flush()
        saga = MovementSagaRecord(idempotency_key=f"synthetic:{event.id}", source="synthetic_lpr",
            access_event_id=event.id, person_id=person.id, vehicle_id=vehicle.id, registration_number=plate,
            direction=AccessDirection.ENTRY, decision=AccessDecision.DENIED, occurred_at=event.occurred_at,
            state=MovementSagaState.FAILED, admission_status="denied", intent_payload={}, decision_payload={}, state_history=[])
        session.add(saga)
        session.add(LprIngestEvent(idempotency_key=str(uuid.uuid4()), source="synthetic_lpr", registration_number=plate,
            captured_at=event.occurred_at, received_at=event.occurred_at, access_event_id=event.id, status="succeeded"))
        await session.flush()
        attempt = MissedExitRecoveryAttempt(id=uuid.uuid4(), occurred_at=event.occurred_at,
            owner_id=person.id, owner_name=person.display_name, vehicle_id=vehicle.id,
            registration_number=plate, event_id=event.id, saga_id=saga.id,
            outcome="denied", reason="phone_journey_missing", checks={}, timeline=[], notification={})
        session.add(attempt)
        await session.flush()
        await recovery.lock_owner(session, person.id)
        if allow:
            await recovery.reserve_approval(session, attempt=attempt, person=person, config=runtime, now=now)
        await session.commit()
        return SimpleNamespace(person=person, vehicle=vehicle, event=event, saga=saga, attempt=attempt)


async def invoke_action(action_id):
    from app.services import gate_commands
    from app.services.movement.admission import AdmissionSessionInput
    return await recovery.handle_resident_action(action_id,
        gate_intent_factory=gate_commands.GateCommandIntent,
        execute_gate=gate_commands.get_gate_command_coordinator().execute_open,
        finalize_admission=finalize_in_session, admission_input_factory=AdmissionSessionInput)


async def rows(model):
    async with AsyncSessionLocal() as session:
        return list((await session.scalars(select(model))).all())


async def action_for(item):
    context = next(c for c in await rows(NotificationActionContext) if c.id == item.attempt.action_context_id)
    return recovery.PREFIX+recovery.token_for(context.id), context


async def inert_command(intent):
    # Verify linked durable decision and spent context already committed before I/O.
    async with AsyncSessionLocal() as session:
        await intent.authorize_dispatch(session)
        event = await session.get(AccessEvent, uuid.UUID(intent.event_id))
        assert event.decision == AccessDecision.GRANTED
        context = await session.get(NotificationActionContext, uuid.UUID(intent.metadata["resident_context_id"]))
        assert context.consumed_at and context.outcome == "authorized"
    now = datetime.now(UTC)
    return GateCommandOutcome(intent=intent, accepted=False, state=GateState.UNKNOWN,
        detail="Synthetic no-send", started_at=now, completed_at=now, command_id=None,
        delivery=CommandDelivery.NOT_SENT, reconciliation_required=False)


async def test_approval_issuance_is_atomic_and_bounded(resources):
    item = await fixture(resources, capture_age=10)
    contexts, runs = await rows(NotificationActionContext), await rows(NotificationRun)
    assert len(contexts) == len(runs) == 1
    assert contexts[0].expires_at == item.event.occurred_at+timedelta(seconds=120)
    assert contexts[0].actor_user_id is None
    assert contexts[0].notify_service == item.person.home_assistant_mobile_app_notify_service
    assert "target_plan" in contexts[0].metadata_
    assert recovery.token_for(contexts[0].id) not in str(runs[0].context)
    actions = await recovery.resolve_mobile_actions(runs[0].delivery_plan[0]["action"])
    assert actions[0]["authenticationRequired"] is True
    assert actions[0]["title"] == "Allow entry"


async def test_approval_can_use_separate_120_second_authority_after_lpr_60_seconds(resources, monkeypatch):
    item = await fixture(resources, capture_age=75)
    action, context = await action_for(item)
    execute = AsyncMock(side_effect=inert_command)
    from app.services import gate_commands
    monkeypatch.setattr(gate_commands, "get_gate_command_coordinator", lambda: SimpleNamespace(execute_open=execute))
    assert await invoke_action(action)
    assert execute.await_count == 1
    intent = execute.await_args.args[0]
    assert intent.expires_at == context.expires_at
    assert intent.target_plan == target_plan()
    events = await rows(AccessEvent)
    assert next(e for e in events if e.id == item.event.id).decision == AccessDecision.DENIED
    assert len([e for e in events if e.source == "resident_missed_exit_recovery"]) == 1
    assert (await rows(MissedExitRecoveryAttempt))[0].method == "resident_confirmation"
    assert len([s for s in await rows(MovementSagaRecord) if s.source == "resident_missed_exit_recovery" and s.gate_command_required]) == 1


async def test_duplicate_concurrent_taps_never_replay_gate(resources, monkeypatch):
    item = await fixture(resources)
    action, _ = await action_for(item)
    execute = AsyncMock(side_effect=inert_command)
    from app.services import gate_commands
    monkeypatch.setattr(gate_commands, "get_gate_command_coordinator", lambda: SimpleNamespace(execute_open=execute))
    await asyncio.wait_for(asyncio.gather(invoke_action(action), invoke_action(action)), timeout=8)
    await invoke_action(action)
    assert execute.await_count == 1
    assert len([e for e in await rows(AccessEvent) if e.source == "resident_missed_exit_recovery"]) == 1


@pytest.mark.parametrize("mutation", ["expired", "owner_inactive", "config_disabled", "target_changed", "other_movement"])
async def test_expiry_replay_and_current_revocation_never_dispatch(resources, monkeypatch, mutation):
    item = await fixture(resources)
    action, context = await action_for(item)
    async with AsyncSessionLocal() as session:
        if mutation == "expired":
            await session.execute(update(NotificationActionContext).where(NotificationActionContext.id == context.id)
                .values(expires_at=datetime.now(UTC)-timedelta(seconds=1)))
        elif mutation == "owner_inactive":
            await session.execute(update(Person).where(Person.id == item.person.id).values(is_active=False))
        elif mutation == "other_movement":
            session.add(AccessEvent(vehicle_id=item.vehicle.id, person_id=item.person.id, registration_number=item.vehicle.registration_number,
                direction=AccessDirection.EXIT, decision=AccessDecision.GRANTED, confidence=1, source="synthetic_lpr",
                occurred_at=item.event.occurred_at-timedelta(seconds=30), raw_payload={}))
        await session.commit()
    if mutation == "config_disabled":
        resources.missed_exit_recovery_enabled = False
    if mutation == "target_changed":
        changed = target_plan()
        changed["targets"][0]["binding_fingerprint"] = "different"
        monkeypatch.setattr(AccessDeviceConfiguration, "preview_gate_open", AsyncMock(return_value=changed))
    execute = AsyncMock(side_effect=inert_command)
    from app.services import gate_commands
    monkeypatch.setattr(gate_commands, "get_gate_command_coordinator", lambda: SimpleNamespace(execute_open=execute))
    await invoke_action(action)
    assert execute.await_count == 0
    assert len([e for e in await rows(AccessEvent) if e.source == "resident_missed_exit_recovery"]) == 0


async def test_owner_contention_defers_tap_without_spending_capability(resources, monkeypatch):
    item = await fixture(resources)
    action, context = await action_for(item)
    execute = AsyncMock(side_effect=inert_command)
    from app.services import gate_commands
    monkeypatch.setattr(gate_commands, "get_gate_command_coordinator", lambda: SimpleNamespace(execute_open=execute))
    async with AsyncSessionLocal() as mutation:
        await recovery.lock_owner(mutation, item.person.id)
        await asyncio.wait_for(invoke_action(action), timeout=8)
        assert execute.await_count == 0
        await mutation.rollback()
    contexts = await rows(NotificationActionContext)
    assert next(c for c in contexts if c.id == context.id).consumed_at is None
    await invoke_action(action)
    assert execute.await_count == 1


async def test_other_vehicle_cannot_rebind_pending_request(resources):
    original = await fixture(resources)
    another = await fixture(resources, owner_id=original.person.id, plate="SYNREC2")
    assert another.attempt.outcome == "denied"
    assert another.attempt.reason == "other_vehicle_request_pending"
    assert len(await rows(NotificationActionContext)) == 1
    assert len(await rows(NotificationRun)) == 1


async def test_claim_and_outbox_roll_back_with_decision(resources):
    item = await fixture(resources, allow=False)
    async with AsyncSessionLocal() as session:
        person = await session.get(Person, item.person.id)
        session.add(ResidentRecoveryJourney(person_id=person.id, epoch=recovery._epoch,
            binding=configuration_binding(person, resources), samples=[], latest_at=datetime.now(UTC)))
        await session.commit()
        event = await session.get(AccessEvent, item.event.id)
        await recovery.invalidate_owner_in_session(session, person.id, event=event)
        attempt = await session.get(MissedExitRecoveryAttempt, item.attempt.id)
        now = await session.scalar(select(func.clock_timestamp()))
        await recovery.reserve_approval(session, attempt=attempt, person=person, config=resources, now=now)
        await session.rollback()
    assert not await rows(NotificationActionContext)
    assert not await rows(NotificationRun)
    assert (await rows(ResidentRecoveryJourney))[0].consumed_at is None


@pytest.mark.parametrize("delivery,verified,expected", [(CommandDelivery.ACCEPTED, False, "pending_verification"),
    (CommandDelivery.REJECTED, False, "rejected"), (CommandDelivery.ACCEPTED, True, "verified")])
async def test_reconciliation_reserves_apology_once_only_after_verified_gate(resources, delivery, verified, expected):
    item = await fixture(resources, allow=False)
    async with AsyncSessionLocal() as session:
        event = await session.get(AccessEvent, item.event.id)
        event.decision = AccessDecision.GRANTED
        event.raw_payload = {"direction_resolution": {"missed_exit_recovery": {"method": "phone_automatic"}}}
        saga = await session.get(MovementSagaRecord, item.saga.id)
        saga.decision, saga.state, saga.admission_status, saga.gate_command_required = AccessDecision.GRANTED, MovementSagaState.PHYSICAL_COMMAND_PENDING, None, True
        attempt = await session.get(MissedExitRecoveryAttempt, item.attempt.id)
        attempt.method, attempt.outcome = "phone_automatic", "authorized"
        await session.commit()
    command_id = await gate_evidence(item.event.id, item.saga.id, delivery=delivery, verified=verified)
    for _ in range(2):
        async with AsyncSessionLocal() as session:
            await finalize_in_session(session, saga_id=item.saga.id, gate_command_id=command_id)
            await session.commit()
    attempt = (await rows(MissedExitRecoveryAttempt))[0]
    assert attempt.outcome == expected
    assert attempt.command_id == command_id
    runs = await rows(NotificationRun)
    apology = [r for r in runs if r.context.get("resident_recovery_origin", {}).get("kind") == "apology"]
    assert len(apology) == int(verified)
    if apology:
        expiry = datetime.fromisoformat(apology[0].context["resident_recovery_origin"]["expires_at"])
        assert 0 < (expiry-apology[0].queued_at).total_seconds() <= 301


@pytest.mark.parametrize("payload", [{"backfilled": True}, {"backfill": {}}, {"skip_automation_actions": True}])
async def test_manual_authority_cannot_launder_historical_origin(resources, monkeypatch, payload):
    item = await fixture(resources)
    action, _ = await action_for(item)
    async with AsyncSessionLocal() as session:
        event = await session.get(AccessEvent, item.event.id)
        event.raw_payload = {**event.raw_payload, **payload}
        await session.commit()
    execute = AsyncMock(side_effect=inert_command)
    from app.services import gate_commands
    monkeypatch.setattr(gate_commands, "get_gate_command_coordinator", lambda: SimpleNamespace(execute_open=execute))
    await invoke_action(action)
    assert execute.await_count == 0
    assert (await rows(MissedExitRecoveryAttempt))[0].reason == "historical_origin_ineligible"
