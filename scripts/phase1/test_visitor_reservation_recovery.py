"""Orphan visitor recovery in isolated PostgreSQL, with inert hardware boundaries.

Fixtures represent a committed intake just before gate-parent creation. No live
historical state is reinterpreted: journal absence is valid only in the retained
lineage, and the deployment recovery hold disables the proof after a restore.
"""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
from datetime import timedelta
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import delete, func, select

from app.db.session import AsyncSessionLocal
from app.models import AccessDeviceCommandRecord, AccessEvent, AuditLog, GateCommandRecord, LprIngestEvent, MovementSagaRecord, NotificationRun, VisitorPass, VisitorPassReservationRecord
from app.models.enums import AccessDecision, AccessDirection, GateCommandState, MovementSagaState, VisitorPassStatus, VisitorPassType
from app.services.gate_commands import GateCommandCoordinator, GateCommandIntent
from app.services.movement import admission
from app.services.movement.admission import finalize_in_session, recover_unattempted_reservation_in_session, recover_unattempted_visitor_reservations
from app.services.movement_ledger import get_movement_ledger_repository, unattempted_access_gate_evidence
from app.services.notification_runs import NotificationRunStore
from app.services.visitor_passes import get_visitor_pass_service

pytestmark = pytest.mark.asyncio


async def orphan(*, identity=None, due=True):
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        captured = now - timedelta(seconds=120) if due else now
        visitor = VisitorPass(visitor_name="Synthetic Visitor", pass_type=VisitorPassType.ONE_TIME,
            status=VisitorPassStatus.ACTIVE, expected_time=now, window_minutes=30)
        session.add(visitor)
        await session.flush()
        event = AccessEvent(registration_number="SYN123", direction=AccessDirection.ENTRY,
            decision=AccessDecision.GRANTED, confidence=1, source="synthetic_lpr", occurred_at=captured,
            raw_payload={"visitor_pass": {"id": str(visitor.id), "mode": "arrival"}})
        session.add(event)
        await session.flush()
        saga = MovementSagaRecord(idempotency_key=f"synthetic:{event.id}", source="synthetic_lpr",
            occurred_at=captured, access_event_id=event.id, direction=event.direction, decision=event.decision,
            state=MovementSagaState.RECONCILIATION_REQUIRED, admission_status="pending",
            admission_evidence={"reason": "entry_verification_pending", "gate_command_id": None},
            reconciliation_required=True, intent_payload={}, decision_payload={}, state_history=[])
        reservation = VisitorPassReservationRecord(id=identity or uuid.uuid4(), visitor_pass_id=visitor.id,
            access_event_id=event.id, intent_id=uuid.uuid5(event.id, "automatic-gate-open"), pass_type="one-time",
            normalized_plate="SYN123", state="reserved", dispatch_deadline=captured + timedelta(seconds=60))
        session.add_all([saga, reservation, LprIngestEvent(idempotency_key=f"synthetic:{event.id}",
            source="synthetic_lpr", registration_number="SYN123", captured_at=captured, received_at=captured,
            status="succeeded", access_event_id=event.id)])
        await session.commit()
        return SimpleNamespace(event_id=event.id, saga_id=saga.id, visitor_id=visitor.id, id=reservation.id,
            intent_id=reservation.intent_id, deadline=reservation.dispatch_deadline,
            key=f"gate-command:open:default:event:{event.id}")


async def truth(item):
    async with AsyncSessionLocal() as session:
        return SimpleNamespace(reservation=await session.get(VisitorPassReservationRecord, item.id),
            saga=await session.get(MovementSagaRecord, item.saga_id),
            visitor=await session.get(VisitorPass, item.visitor_id),
            event=await session.get(AccessEvent, item.event_id),
            parent=await session.scalar(select(GateCommandRecord).where(GateCommandRecord.idempotency_key == item.key)))


def intent(item, **kwargs):
    return GateCommandIntent(reason="Synthetic automatic entry", source="automatic_lpr_grant",
        event_id=str(item.event_id), movement_saga_id=str(item.saga_id), intent_id=str(item.intent_id),
        expires_at=item.deadline, automatic_entry_policy=True, **kwargs)


async def test_overdue_unattempted_arrival_commits_no_send_release_and_required_outputs_once():
    item = await orphan()
    assert await recover_unattempted_visitor_reservations() == (1, None)
    result = await truth(item)
    assert result.reservation.state == "released" and result.reservation.gate_command_id == result.parent.id
    assert result.visitor.status == VisitorPassStatus.ACTIVE and result.visitor.arrival_time is None
    assert result.visitor.number_plate is None
    assert result.saga.admission_status == "denied" and not result.saga.presence_committed
    assert not result.saga.reconciliation_required and result.saga.state == MovementSagaState.FAILED
    assert result.parent.accepted is False and result.parent.started_at is None
    assert result.parent.state == GateCommandState.REJECTED and not result.parent.requires_reconciliation
    assert unattempted_access_gate_evidence(result.parent, event_id=item.event_id, saga_id=item.saga_id)
    assert result.event.raw_payload["movement_saga"]["gate"]["delivery"] == "not_sent"
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AccessDeviceCommandRecord)) == 0
        audit = await session.get(AuditLog, uuid.uuid5(result.parent.id, "access.gate.outcome.audit"))
        assert audit.action == "gate.open.automatic" and audit.outcome == "not_sent"
        notice = await session.get(NotificationRun, uuid.uuid5(result.parent.id, "access.gate.outcome.notification"))
        assert notice.trigger_event == "gate_open_failed"
        assert (await finalize_in_session(session, saga_id=item.saga_id)).changed is False
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(NotificationRun)) == 1
    assert await recover_unattempted_visitor_reservations() == (0, None)
    assert (await truth(item)).reservation.completed_at == result.reservation.completed_at


async def test_delayed_core_worker_observes_terminal_receipt_and_never_constructs_controller():
    item = await orphan()
    await recover_unattempted_visitor_reservations()

    def forbidden(_name):
        raise AssertionError("No hardware controller may be created for a retained terminal operation")

    outcome = await GateCommandCoordinator(controller_factory=forbidden).execute_open(intent(item))
    assert outcome.delivery.value == "not_sent" and outcome.accepted is False
    assert not outcome.requires_reconciliation and not outcome.admission_verified
    assert outcome.command_id == str((await truth(item)).parent.id)


@pytest.mark.parametrize("offset,expected", [(-1, False), (0, False), (1, True)])
async def test_recovery_requires_strictly_after_deadline_at_database_checkpoint(monkeypatch, offset, expected):
    item = await orphan()
    async with AsyncSessionLocal() as session:
        scalar = session.scalar

        async def fixed_clock(statement, *args, **kwargs):
            if str(statement) == "SELECT clock_timestamp() AS clock_timestamp_1":
                return item.deadline + timedelta(seconds=offset)
            return await scalar(statement, *args, **kwargs)

        monkeypatch.setattr(session, "scalar", fixed_clock)
        assert await recover_unattempted_reservation_in_session(session, reservation_id=item.id) is expected
        await session.commit()
    assert (await truth(item)).reservation.state == ("released" if expected else "reserved")


async def test_notification_failure_rolls_back_parent_saga_pass_and_audit(monkeypatch):
    item = await orphan()

    async def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic required delivery fault")

    with monkeypatch.context() as patch:
        patch.setattr(NotificationRunStore, "enqueue_in_session", fail)
        with pytest.raises(RuntimeError, match="required delivery"):
            await recover_unattempted_visitor_reservations()
    result = await truth(item)
    assert result.parent is None and result.reservation.state == "reserved"
    assert result.saga.admission_status == "pending" and result.visitor.arrival_time is None
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0
        assert await session.scalar(select(func.count()).select_from(NotificationRun)) == 0
    assert await recover_unattempted_visitor_reservations() == (1, None)


@pytest.mark.parametrize("defect,reason", [
    ("missing_event", "origin_missing"), ("missing_pass", "origin_missing"), ("missing_saga", "origin_missing"),
    ("missing_ingest", "recognition_origin_missing"), ("intent", "origin_identity_conflict"),
    ("event_visitor", "origin_identity_conflict"), ("deadline", "dispatch_deadline_conflict"),
    ("previous_parent", "previously_linked_parent_missing"), ("orphan_child", "orphan_target_command"),
    ("conflicting_parent", "command_identity_conflict"),
])
async def test_missing_or_conflicting_history_remains_durable_review_hold(defect, reason):
    item = await orphan()
    async with AsyncSessionLocal() as session:
        reservation = await session.get(VisitorPassReservationRecord, item.id)
        if defect == "missing_event":
            await session.execute(delete(AccessEvent).where(AccessEvent.id == item.event_id))
        elif defect == "missing_pass":
            await session.execute(delete(VisitorPass).where(VisitorPass.id == item.visitor_id))
        elif defect == "missing_saga":
            await session.execute(delete(MovementSagaRecord).where(MovementSagaRecord.id == item.saga_id))
        elif defect == "missing_ingest":
            await session.execute(delete(LprIngestEvent).where(LprIngestEvent.access_event_id == item.event_id))
        elif defect == "intent":
            reservation.intent_id = uuid.uuid4()
        elif defect == "event_visitor":
            event = await session.get(AccessEvent, item.event_id)
            event.raw_payload = {"visitor_pass": {"id": str(uuid.uuid4()), "mode": "arrival"}}
        elif defect == "deadline":
            reservation.dispatch_deadline += timedelta(seconds=1)
        elif defect == "previous_parent":
            reservation.gate_command_id = uuid.uuid4()
        elif defect == "orphan_child":
            session.add(AccessDeviceCommandRecord(target_device_id=uuid.uuid4(), device_key="synthetic",
                action="open", intent_id=str(item.intent_id), idempotency_key=str(uuid.uuid4()), state="unknown",
                binding_snapshot={}, binding_fingerprint="a" * 64))
        else:
            parent = get_movement_ledger_repository()._new_gate_command_record(intent(item), idempotency_key=item.key)
            parent.command_metadata = {**parent.command_metadata, "intent_id": str(uuid.uuid4())}
            session.add(parent)
        await session.commit()
    assert await recover_unattempted_visitor_reservations() == (1, None)
    result = await truth(item)
    assert result.reservation.state == "held" and result.reservation.completed_at is None
    assert result.reservation.completion_reason == "orphan_review:" + reason
    assert await recover_unattempted_visitor_reservations() == (0, None)
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "visitor_pass.reservation_review_required")) == 1
        assert await session.scalar(select(func.count()).select_from(NotificationRun)) == 0


async def test_no_plan_parent_metadata_is_not_a_no_attempt_proof():
    item = await orphan()
    ledger = get_movement_ledger_repository()
    async with AsyncSessionLocal() as session:
        parent = ledger._new_gate_command_record(intent(item, metadata={"delivery": "not_sent",
            "no_attempt_evidence": {"kind": "expired_unattempted_access_gate"}}), idempotency_key=item.key)
        assert "no_attempt_evidence" not in parent.command_metadata
        parent.state, parent.accepted = GateCommandState.REJECTED, False
        session.add(parent)
        await session.commit()
        result = await finalize_in_session(session, saga_id=item.saga_id)
        await session.commit()
        assert result.admission_status == "pending" and result.requires_reconciliation
    assert (await truth(item)).reservation.state == "held"


async def test_current_parent_is_left_to_normal_receipt_reconciliation():
    item = await orphan()
    lease = await get_movement_ledger_repository().claim_gate_command(intent(item))
    assert await recover_unattempted_visitor_reservations() == (0, None)
    result = await truth(item)
    assert result.parent.state == GateCommandState.LEASED and result.parent.lease_token == lease.lease_token
    assert result.reservation.state == "reserved"


async def test_concurrent_recoverers_create_one_parent_and_notice():
    item = await orphan()
    results = await asyncio.wait_for(asyncio.gather(
        recover_unattempted_visitor_reservations(), recover_unattempted_visitor_reservations()), 8)
    assert sum(value[0] for value in results) == 1
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(GateCommandRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(NotificationRun)) == 1
    assert (await truth(item)).reservation.state == "released"


async def test_dispatch_operation_lock_wins_without_origin_lock_inversion():
    item = await orphan()
    ledger = get_movement_ledger_repository()
    async with AsyncSessionLocal() as dispatcher:
        await ledger.lock_gate_operation_in_session(dispatcher, item.key)
        recovery = asyncio.create_task(recover_unattempted_visitor_reservations())
        assert await asyncio.wait_for(recovery, 3) == (0, None)
        # If recovery held the event while waiting for the operation, this FK
        # insertion would deadlock. Recovery instead skipped without origin locks.
        parent = ledger._new_gate_command_record(intent(item), idempotency_key=item.key)
        parent.state = GateCommandState.LEASED
        parent.lease_token = "synthetic-active"
        parent.lease_expires_at = item.deadline + timedelta(hours=1)
        dispatcher.add(parent)
        await asyncio.wait_for(dispatcher.commit(), 3)
    assert await recover_unattempted_visitor_reservations() == (0, None)


async def test_recovery_lock_wins_delayed_claim_waits_and_reads_terminal_truth():
    item = await orphan()
    ledger = get_movement_ledger_repository()
    async with AsyncSessionLocal() as recovery:
        await ledger.lock_gate_operation_in_session(recovery, item.key)
        delayed = asyncio.create_task(ledger.claim_gate_command(intent(item)))
        assert await recover_unattempted_reservation_in_session(recovery, reservation_id=item.id)
        await recovery.commit()
        lease = await asyncio.wait_for(delayed, 5)
    assert lease.already_completed and lease.record.state == GateCommandState.REJECTED
    assert lease.record.started_at is None


@pytest.mark.parametrize("status", [VisitorPassStatus.CANCELLED, VisitorPassStatus.EXPIRED])
async def test_cancel_or_expiry_is_preserved_when_proven_unattempted_reservation_releases(status):
    item = await orphan()
    async with AsyncSessionLocal() as session:
        visitor = await session.get(VisitorPass, item.visitor_id)
        if status == VisitorPassStatus.CANCELLED:
            await get_visitor_pass_service().cancel_pass(session, visitor)
        else:
            await get_visitor_pass_service().refresh_statuses(session=session,
                now=visitor.expected_time + timedelta(hours=1), publish=False)
        await session.commit()
    assert await recover_unattempted_visitor_reservations() == (1, None)
    result = await truth(item)
    assert result.visitor.status == status and result.reservation.state == "released"
    assert result.visitor.arrival_event_id is None and result.visitor.arrival_time is None


async def test_busy_first_page_advances_without_mutating_unresolved_rows():
    items = [await orphan(identity=uuid.UUID(int=value)) for value in range(1, 27)]
    ledger = get_movement_ledger_repository()
    async with AsyncSessionLocal() as active:
        for item in items[:25]:
            await ledger.lock_gate_operation_in_session(active, item.key)
        count, cursor = await recover_unattempted_visitor_reservations()
        assert count == 0 and cursor == items[24].id
        assert await recover_unattempted_visitor_reservations(after_id=cursor) == (1, None)
        assert (await truth(items[0])).reservation.state == "reserved"
        assert (await truth(items[-1])).reservation.state == "released"
    assert (await recover_unattempted_visitor_reservations())[0] == 25


async def test_restore_hold_permits_no_absence_proof_or_review_mutation(monkeypatch):
    item = await orphan()
    monkeypatch.setattr(admission, "is_recovery_hold", lambda: True)
    assert await recover_unattempted_visitor_reservations() == (0, None)
    async with AsyncSessionLocal() as session:
        assert not await recover_unattempted_reservation_in_session(session, reservation_id=item.id)
        await session.commit()
    result = await truth(item)
    assert result.parent is None and result.reservation.state == "reserved"
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0


async def test_concurrent_cancellation_commits_before_recovery_settlement(monkeypatch):
    item = await orphan()
    ledger = get_movement_ledger_repository()
    original = ledger.lock_gate_operation_in_session
    acquired = asyncio.Event()

    async def observed_lock(session, key, *, wait=True):
        result = await original(session, key, wait=wait)
        if result and key == item.key and not wait:
            acquired.set()
        return result

    monkeypatch.setattr(ledger, "lock_gate_operation_in_session", observed_lock)
    async with AsyncSessionLocal() as cancellation:
        visitor = await cancellation.get(VisitorPass, item.visitor_id, with_for_update=True)
        await get_visitor_pass_service().cancel_pass(cancellation, visitor)
        pending = asyncio.create_task(recover_unattempted_visitor_reservations())
        await asyncio.wait_for(acquired.wait(), 3)
        await cancellation.commit()
        assert await asyncio.wait_for(pending, 5) == (1, None)
    result = await truth(item)
    assert result.visitor.status == VisitorPassStatus.CANCELLED and result.reservation.state == "released"


async def test_completion_metadata_cannot_forge_ledger_absence_evidence():
    item = await orphan()
    ledger = get_movement_ledger_repository()
    lease = await ledger.claim_gate_command(intent(item))
    row = await ledger.complete_gate_command(lease.record.id, lease_token=lease.lease_token,
        accepted=False, gate_state="unknown", detail="Synthetic provider rejection",
        mechanically_confirmed=False, requires_reconciliation=False,
        metadata={"delivery": "not_sent", "no_attempt_evidence": {"kind": "expired_unattempted_access_gate"}})
    assert "no_attempt_evidence" not in row.command_metadata
    assert unattempted_access_gate_evidence(row, event_id=item.event_id, saga_id=item.saga_id) is None


@pytest.mark.parametrize("payload", [["invalid-origin"], {"visitor_pass": ["invalid-visitor"]}])
async def test_malformed_retained_origin_is_held_and_does_not_starve_later_candidate(payload):
    malformed = await orphan(identity=uuid.UUID(int=1))
    valid = await orphan(identity=uuid.UUID(int=2))
    async with AsyncSessionLocal() as session:
        event = await session.get(AccessEvent, malformed.event_id)
        event.raw_payload = payload
        await session.commit()
    assert await recover_unattempted_visitor_reservations() == (2, None)
    assert (await truth(malformed)).reservation.completion_reason == "orphan_review:origin_identity_conflict"
    assert (await truth(valid)).reservation.state == "released"


async def test_recovered_no_attempt_receipt_is_available_by_command_intent_and_page():
    item = await orphan()
    await recover_unattempted_visitor_reservations()
    coordinator = GateCommandCoordinator()
    result = await truth(item)
    by_id = await coordinator.get_receipt(result.parent.id)
    by_intent = await coordinator.get_receipt(intent_id=str(item.intent_id))
    page = await coordinator.list_receipts()
    assert by_id == by_intent == page["items"][0]
    assert by_id["delivery"] == "not_sent" and by_id["admission_verified"] is False
    assert by_id["target_receipts"] == [] and not by_id["requires_reconciliation"]
