"""Atomic movement contracts; collection refuses all but the isolated PG namespace."""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
from datetime import UTC, datetime, timedelta
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.db.session import AsyncSessionLocal
from app.models import AccessEvent, AuditLog, GateCommandRecord, MovementSagaRecord, MovementSessionRecord, NotificationRun, Person, Presence
from app.models.enums import AccessDecision, AccessDirection, GateCommandState, MovementSagaState
from app.services.access.historical import HistoricalSessionInput, persist_historical_event_in_session
from app.services.access_device_commands import AccessDeviceCommandJournal
from app.services.movement.admission import AdmissionSessionInput, finalize_in_session
from app.modules.lpr.base import PlateRead
from app.modules.gate.base import CommandDelivery, GateState

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


async def person():
    async with AsyncSessionLocal() as session:
        row = Person(display_name="Synthetic Person")
        session.add(row)
        await session.commit()
        return row.id


async def movement(person_id, *, at=NOW, created=NOW, direction=AccessDirection.ENTRY, identity=None):
    async with AsyncSessionLocal() as session:
        event = AccessEvent(id=identity or uuid.uuid4(), person_id=person_id, registration_number="SYN123",
            direction=direction, decision=AccessDecision.GRANTED, confidence=1, source="synthetic",
            occurred_at=at, created_at=created, raw_payload={})
        session.add(event)
        await session.flush()
        saga = MovementSagaRecord(idempotency_key=f"synthetic:{event.id}", source="synthetic", access_event_id=event.id,
            person_id=person_id, registration_number="SYN123", direction=direction, decision=event.decision,
            occurred_at=at, state=MovementSagaState.PHYSICAL_COMMAND_PENDING,
            intent_payload={}, decision_payload={}, state_history=[])
        session.add(saga)
        await session.commit()
        return event.id, saga.id


async def final(saga_id, *, historical=False):
    async with AsyncSessionLocal() as session:
        result = await finalize_in_session(session, saga_id=saga_id,
            mode="historical" if historical else "live", historical_evidence={"source": "synthetic-history"} if historical else None)
        await session.commit()
        return result


async def evidence(event_id, saga_id, *, delivery=CommandDelivery.ACCEPTED, verified=False,
                   designation_matches=True, unresolved=False):
    journal = AccessDeviceCommandJournal()
    selected = {"target_device_id": str(uuid.uuid4()), "device_key": "synthetic_entry", "kind": "gate",
        "binding_fingerprint": "a" * 64,
        "binding_snapshot": {"providers": [{"provider": "home_assistant", "external_id": "cover.synthetic_entry"}]}}
    identity = str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        parent = GateCommandRecord(idempotency_key=identity, source="synthetic", controller="configured", reason="Synthetic",
            state=GateCommandState.RECONCILIATION_REQUIRED, movement_saga_id=saga_id, access_event_id=event_id,
            command_metadata={"intent_id": identity, "recovery_version": 2, "target_plan": {
                "targets": [selected], "admission_target_device_id": selected["target_device_id"] if designation_matches else str(uuid.uuid4())},
                "automatic_entry_precondition": {"mode": "unresolved" if unresolved else "fanout"}})
        session.add(parent)
        await session.flush()
        claim = await journal.claim(session, target=selected, action="open", intent_id=identity,
            operation_key=identity, gate_command_id=str(parent.id), expires_at=None)
        await session.commit()
    if unresolved:
        await journal.finish_without_send(claim, detail="Synthetic missing entry state")
    else:
        async with AsyncSessionLocal() as session:
            assert await journal.begin_attempt(session, claim, provider="home_assistant", external_id="cover.synthetic_entry")
            await session.commit()
        await journal.finish_attempt(claim, delivery=delivery, state=GateState.UNKNOWN, detail="Synthetic receipt",
            observation={"provider": "home_assistant", "state": "open", "observed_at": datetime.now(tz=UTC)} if verified else None)
        if verified and delivery == CommandDelivery.UNKNOWN:
            await journal.reconcile_observation(claim.record.id, binding_fingerprint=selected["binding_fingerprint"],
                observation={"provider": "home_assistant", "state": "open", "observed_at": datetime.now(tz=UTC)})
    return parent.id


@pytest.mark.parametrize("delivery,verified,status", [
    (CommandDelivery.ACCEPTED, True, "verified"), (CommandDelivery.ACCEPTED, False, "pending"),
    (CommandDelivery.UNKNOWN, True, "verified"), (CommandDelivery.UNKNOWN, False, "pending"),
    (CommandDelivery.REJECTED, False, "denied"), (CommandDelivery.NOT_SENT, False, "denied"),
])
async def test_admission_uses_exact_verified_entry_truth_not_provider_acceptance(delivery, verified, status):
    owner = await person()
    event_id, saga_id = await movement(owner)
    await evidence(event_id, saga_id, delivery=delivery, verified=verified)
    result = await final(saga_id)
    assert result.admission_status == status and result.presence_changed is verified
    async with AsyncSessionLocal() as session:
        current = await session.get(Presence, owner)
        assert bool(current) is verified
        if verified:
            assert current.last_event_id == event_id
            assert result.admission_evidence["observed_at"] and result.admission_evidence["target_device_id"]


@pytest.mark.parametrize("kind", ["no_command", "unresolved_entry_state", "different_target"])
async def test_no_command_and_unrelated_physical_state_never_imply_admission(kind):
    owner = await person()
    event_id, saga_id = await movement(owner)
    if kind != "no_command":
        await evidence(event_id, saga_id, verified=kind == "different_target",
            designation_matches=kind != "different_target", unresolved=kind == "unresolved_entry_state")
    result = await final(saga_id)
    assert result.admission_status != "verified" and result.presence_changed is False
    if kind != "different_target":
        assert result.admission_status == "pending"


async def test_simultaneous_first_presence_inserts_are_serialized_and_latest_event_wins():
    owner = await person()
    entry, a = await movement(owner)
    exit_event, b = await movement(owner, at=NOW + timedelta(seconds=1), direction=AccessDirection.EXIT)
    start = asyncio.Event()

    async def finalize_after_start(saga):
        await start.wait()
        return await final(saga, historical=True)

    workers = [asyncio.create_task(finalize_after_start(saga)) for saga in (a, b)]
    start.set()
    await asyncio.wait_for(asyncio.gather(*workers), 8)
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Presence).where(Presence.person_id == owner)) == 1
        current = await session.get(Presence, owner)
        assert current.last_event_id == exit_event and current.state == "exited"


@pytest.mark.parametrize("reverse", [False, True])
async def test_equal_time_order_is_deterministic_created_at_then_uuid(reverse):
    owner = await person()
    identities = [uuid.UUID(int=10), uuid.UUID(int=20), uuid.UUID(int=1)]
    pairs = [await movement(owner, created=NOW, identity=identities[0]),
             await movement(owner, created=NOW, identity=identities[1], direction=AccessDirection.EXIT),
             await movement(owner, created=NOW + timedelta(seconds=1), identity=identities[2])]
    for _event, saga in reversed(pairs) if reverse else pairs:
        await final(saga, historical=True)
    async with AsyncSessionLocal() as session:
        current = await session.get(Presence, owner)
        assert current.last_event_id == identities[2] and current.state == "present"


async def test_late_verified_arrival_cannot_overwrite_newer_exit_and_replay_is_idempotent():
    owner = await person()
    event_id, saga_id = await movement(owner)
    exit_id, exit_saga = await movement(owner, at=NOW + timedelta(minutes=2), direction=AccessDirection.EXIT)
    await final(exit_saga)
    await evidence(event_id, saga_id, verified=True)
    first = await final(saga_id)
    again = await final(saga_id)
    assert first.admission_status == "verified" and first.presence_result == "stale"
    assert not first.presence_changed and not again.changed
    async with AsyncSessionLocal() as session:
        assert (await session.get(Presence, owner)).last_event_id == exit_id
        count = await session.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "movement.admission.finalized", AuditLog.target_id == str(saga_id)))
        assert count == 1


async def test_rollback_after_finalizer_restores_presence_saga_session_and_audit():
    owner = await person()
    event_id, saga_id = await movement(owner, direction=AccessDirection.EXIT)
    async with AsyncSessionLocal() as session:
        read = PlateRead("SYN123", 1, "synthetic", NOW, {})
        result = await finalize_in_session(session, saga_id=saga_id, session_input=AdmissionSessionInput(
            [read], NOW, NOW, read, SimpleNamespace(lpr_debounce_max_seconds=10, lpr_vehicle_session_idle_seconds=90)))
        assert result.presence_changed
        assert await session.scalar(select(func.count()).select_from(MovementSessionRecord)) == 1
        await session.rollback()
    async with AsyncSessionLocal() as session:
        assert await session.get(Presence, owner) is None
        assert (await session.get(MovementSagaRecord, saga_id)).admission_status is None
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0
        assert await session.scalar(select(func.count()).select_from(MovementSessionRecord)) == 0


async def test_stale_session_identity_map_is_refreshed_after_other_writer_commits():
    owner = await person()
    first_id, first_saga = await movement(owner, direction=AccessDirection.EXIT)
    await final(first_saga)
    older_id, older_saga = await movement(owner, at=NOW + timedelta(seconds=1))
    newest_id, newest_saga = await movement(owner, at=NOW + timedelta(seconds=2), direction=AccessDirection.EXIT)
    async with AsyncSessionLocal() as observer:
        assert (await observer.get(Presence, owner)).last_event_id == first_id
        await final(newest_saga)
        result = await finalize_in_session(observer, saga_id=older_saga, mode="historical", historical_evidence={"source": "synthetic"})
        await observer.commit()
        assert result.presence_result == "stale"
    async with AsyncSessionLocal() as session:
        assert (await session.get(Presence, owner)).last_event_id == newest_id


async def test_historical_owner_persists_saga_session_and_never_consumes_visitor():
    from app.models import VisitorPass, VisitorPassReservationRecord
    from app.models.enums import VisitorPassStatus

    owner = await person()
    async with AsyncSessionLocal() as session:
        visitor = VisitorPass(visitor_name="Synthetic Visitor", expected_time=NOW, status=VisitorPassStatus.ACTIVE)
        session.add(visitor)
        await session.flush()
        event = AccessEvent(person_id=owner, registration_number="SYN123", direction=AccessDirection.ENTRY,
            decision=AccessDecision.GRANTED, confidence=1, source="synthetic_history", occurred_at=NOW,
            raw_payload={"visitor_pass": {"id": str(visitor.id)}})
        session.add(event)
        await session.flush()
        result = await persist_historical_event_in_session(session, event,
            idempotency_key=f"synthetic-history:{event.id}", evidence={"source": "synthetic_history"},
            session_input=HistoricalSessionInput(debounce_seconds=10, idle_seconds=90))
        await session.commit()
        assert result.admission_status == "historical" and result.presence_changed
        await session.refresh(visitor)
        assert visitor.status == VisitorPassStatus.ACTIVE and visitor.arrival_event_id is None
        assert await session.scalar(select(func.count()).select_from(VisitorPassReservationRecord).where(
            VisitorPassReservationRecord.visitor_pass_id == visitor.id)) == 0
        assert await session.scalar(select(func.count()).select_from(MovementSessionRecord).where(
            MovementSessionRecord.access_event_id == event.id)) == 1


async def test_failed_live_entry_cannot_be_promoted_by_historical_finalizer():
    owner = await person()
    event_id, saga_id = await movement(owner)
    await evidence(event_id, saga_id, delivery=CommandDelivery.REJECTED)
    assert (await final(saga_id)).admission_status == "denied"
    async with AsyncSessionLocal() as session:
        with pytest.raises(ValueError, match="cannot be reclassified"):
            await finalize_in_session(session, saga_id=saga_id, mode="historical",
                                      historical_evidence={"source": "synthetic"})
        await session.rollback()
        assert await session.get(Presence, owner) is None


@pytest.mark.parametrize("kind,plate,terminal,expected_mode", [
    ("one-time", "OTHER", None, "arrival"), ("duration", "SYN123", None, "arrival"),
    ("duration", "OTHER", None, None), ("one-time", None, "cancelled", None),
    ("one-time", None, "used", None), ("one-time", None, "expired", None),
])
async def test_historical_visitor_match_preserves_rules_without_mutating_pass(kind, plate, terminal, expected_mode):
    from app.models import VisitorPass
    from app.models.enums import VisitorPassStatus, VisitorPassType
    from app.services.visitor_passes import get_visitor_pass_service

    async with AsyncSessionLocal() as session:
        visitor = VisitorPass(visitor_name="Synthetic Visitor", expected_time=NOW, window_minutes=30,
            valid_from=NOW - timedelta(minutes=30), valid_until=NOW + timedelta(minutes=30),
            pass_type=VisitorPassType(kind), status=VisitorPassStatus(terminal or "scheduled"), number_plate=plate)
        session.add(visitor)
        await session.commit()
        before = {column.name: getattr(visitor, column.name) for column in VisitorPass.__table__.columns}
        matched, mode = await get_visitor_pass_service().find_historical_match(session,
            occurred_at=NOW, registration_number="SYN123")
        assert mode == expected_mode
        assert (matched.id if matched else None) == (visitor.id if expected_mode else None)
        await session.commit()
        await session.refresh(visitor)
        assert {column.name: getattr(visitor, column.name) for column in VisitorPass.__table__.columns} == before


async def test_historical_visitor_match_prefers_existing_departure_without_reopening_legacy_used_pass():
    from app.models import VisitorPass
    from app.models.enums import VisitorPassStatus
    from app.services.visitor_passes import get_visitor_pass_service

    async with AsyncSessionLocal() as session:
        used = VisitorPass(visitor_name="Synthetic used", expected_time=NOW - timedelta(hours=1),
            status=VisitorPassStatus.USED, number_plate="SYN123", arrival_time=NOW - timedelta(minutes=1))
        active = VisitorPass(visitor_name="Synthetic active", expected_time=NOW, status=VisitorPassStatus.ACTIVE)
        session.add_all([used, active])
        await session.commit()
        matched, mode = await get_visitor_pass_service().find_historical_match(session,
            occurred_at=NOW, registration_number="SYN123")
        assert matched.id == used.id and mode == "departure"
        await session.commit()
        await session.refresh(used)
        await session.refresh(active)
        assert used.status == VisitorPassStatus.USED and used.departure_time is None
        assert active.status == VisitorPassStatus.ACTIVE and active.arrival_time is None


async def test_legacy_failed_without_conclusive_command_moves_to_review_without_admission():
    owner = await person()
    _, saga_id = await movement(owner)
    async with AsyncSessionLocal() as session:
        row = await session.get(MovementSagaRecord, saga_id)
        row.state = MovementSagaState.FAILED
        await session.commit()
    result = await final(saga_id)
    assert result.admission_status == "pending" and result.requires_reconciliation
    assert result.saga.state == MovementSagaState.RECONCILIATION_REQUIRED
    assert result.saga.reconciliation_required is True and result.presence_changed is False


async def unfinished_parent(parent_id, *, active=False):
    """Retain a child checkpoint while simulating loss before parent completion."""
    async with AsyncSessionLocal() as session:
        parent = await session.get(GateCommandRecord, parent_id)
        now = await session.scalar(select(func.clock_timestamp()))
        parent.state, parent.lease_token = GateCommandState.LEASED, "synthetic-parent-owner"
        parent.lease_expires_at = now + timedelta(seconds=120) if active else now - timedelta(seconds=1)
        parent.completed_at, parent.accepted = None, None
        parent.requires_reconciliation, parent.mechanically_confirmed = False, False
        await session.commit()


@pytest.mark.parametrize("verified", [False, True])
async def test_child_checkpoint_recovers_parent_admission_and_required_outputs_once(monkeypatch, verified):
    from app.services import movement_reconciliation as recovery

    monkeypatch.setattr(recovery, "apply_person_presence_input_boolean_actions", AsyncMock())
    owner = await person()
    event_id, saga_id = await movement(owner)
    parent_id = await evidence(event_id, saga_id,
        delivery=CommandDelivery.ACCEPTED if verified else CommandDelivery.NOT_SENT, verified=verified)
    await unfinished_parent(parent_id)
    service = recovery.MovementReconciliationService()
    assert await service.reconcile_once() == 1
    assert await service.reconcile_once() == 0
    # A direct replay follows the same owner and cannot duplicate required outputs.
    assert (await final(saga_id)).changed is False
    async with AsyncSessionLocal() as session:
        parent = await session.get(GateCommandRecord, parent_id)
        assert parent.state == (GateCommandState.RECONCILED if verified else GateCommandState.REJECTED)
        assert parent.completed_at is not None
        assert parent.lease_token is None and parent.lease_expires_at is None
        assert parent.requires_reconciliation is False
        saga = await session.get(MovementSagaRecord, saga_id)
        assert saga.admission_status == ("verified" if verified else "denied")
        assert saga.reconciliation_required is False
        assert bool(await session.get(Presence, owner)) is verified
        audit = await session.get(AuditLog, uuid.uuid5(parent_id, "access.gate.outcome.audit"))
        assert audit and audit.action == "gate.open.automatic"
        assert audit.outcome == ("accepted" if verified else "not_sent")
        assert await session.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "gate.open.automatic")) == 1
        notice = await session.get(NotificationRun, uuid.uuid5(parent_id, "access.gate.outcome.notification"))
        assert bool(notice) is (not verified)
        assert await session.scalar(select(func.count()).select_from(NotificationRun).where(
            NotificationRun.trigger_event.in_(["gate_open_failed", "gate_command_reconciliation_failed"]))) == int(not verified)


async def test_active_parent_is_read_without_clearing_lease_or_outcome_checkpoint():
    owner = await person()
    event_id, saga_id = await movement(owner)
    parent_id = await evidence(event_id, saga_id, delivery=CommandDelivery.ACCEPTED)
    await unfinished_parent(parent_id, active=True)
    async with AsyncSessionLocal() as session:
        parent = await session.get(GateCommandRecord, parent_id)
        before = (parent.state, parent.command_metadata, parent.completed_at, parent.updated_at,
                  parent.lease_token, parent.lease_expires_at, parent.accepted)
    assert (await final(saga_id)).admission_status == "pending"
    async with AsyncSessionLocal() as session:
        parent = await session.get(GateCommandRecord, parent_id)
        assert before == (parent.state, parent.command_metadata, parent.completed_at, parent.updated_at,
                          parent.lease_token, parent.lease_expires_at, parent.accepted)
        assert await session.get(AuditLog, uuid.uuid5(parent_id, "access.gate.outcome.audit")) is None
        assert await session.get(NotificationRun, uuid.uuid5(parent_id, "access.gate.outcome.notification")) is None


async def test_parent_completion_required_outputs_and_presence_share_rollback():
    owner = await person()
    event_id, saga_id = await movement(owner)
    parent_id = await evidence(event_id, saga_id, verified=True)
    await unfinished_parent(parent_id)
    async with AsyncSessionLocal() as session:
        result = await finalize_in_session(session, saga_id=saga_id)
        assert result.admission_status == "verified" and result.presence_changed
        assert (await session.get(GateCommandRecord, parent_id)).completed_at is not None
        assert await session.get(AuditLog, uuid.uuid5(parent_id, "access.gate.outcome.audit")) is not None
        await session.rollback()
    async with AsyncSessionLocal() as session:
        parent = await session.get(GateCommandRecord, parent_id)
        assert parent.state == GateCommandState.LEASED and parent.completed_at is None
        assert parent.lease_token == "synthetic-parent-owner"
        assert (await session.get(MovementSagaRecord, saga_id)).admission_status is None
        assert await session.get(Presence, owner) is None
        assert await session.get(AuditLog, uuid.uuid5(parent_id, "access.gate.outcome.audit")) is None
        assert await session.scalar(select(func.count()).select_from(NotificationRun)) == 0


async def test_unchanged_held_parent_keeps_original_completion_and_update_timestamps():
    owner = await person()
    event_id, saga_id = await movement(owner)
    parent_id = await evidence(event_id, saga_id, delivery=CommandDelivery.UNKNOWN)
    await unfinished_parent(parent_id)
    assert (await final(saga_id)).admission_status == "pending"
    async with AsyncSessionLocal() as session:
        parent = await session.get(GateCommandRecord, parent_id)
        before = parent.updated_at, parent.completed_at, parent.command_metadata
    assert (await final(saga_id)).changed is False
    async with AsyncSessionLocal() as session:
        parent = await session.get(GateCommandRecord, parent_id)
        assert parent.state == GateCommandState.RECONCILIATION_REQUIRED
        assert parent.requires_reconciliation and parent.accepted is False
        assert (parent.updated_at, parent.completed_at, parent.command_metadata) == before
