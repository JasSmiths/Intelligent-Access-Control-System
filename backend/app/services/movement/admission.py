"""Atomic movement admission, session and presence finalization; no external I/O."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.recovery_hold import is_recovery_hold
from app.db.session import AsyncSessionLocal
from app.models import AccessDeviceCommandRecord, AccessEvent, GateCommandRecord, MovementSagaRecord, VisitorPass, VisitorPassReservationRecord
from app.models.enums import AccessDecision, AccessDirection, MovementSagaState
from app.modules.lpr.base import PlateRead
from app.modules.dvla.vehicle_enquiry import normalize_registration_number
from app.services.access_device_commands import AccessDeviceCommandJournal
from app.services.movement.presence import PresenceTransition, apply_eligible_event_in_session
from app.services.movement.sessions import MovementSessionService
from app.services.movement_ledger import get_movement_ledger_repository, movement_saga_summary, unattempted_access_gate_evidence
from app.services.telemetry import TELEMETRY_CATEGORY_ACCESS, write_audit_log


@dataclass(frozen=True)
class AdmissionSessionInput:
    reads: Sequence[PlateRead]
    first_seen: datetime
    updated_at: datetime
    read: PlateRead
    runtime: Any


@dataclass(frozen=True)
class AdmissionResult:
    event: AccessEvent
    saga: MovementSagaRecord
    admission_status: str
    admission_evidence: dict[str, Any] | None
    presence_changed: bool
    presence_result: str
    requires_reconciliation: bool
    changed: bool


async def finalize_in_session(
    session: AsyncSession, *, saga_id: uuid.UUID | str,
    mode: Literal["live", "historical", "external"] = "live",
    gate_command_id: uuid.UUID | str | None = None,
    historical_evidence: dict[str, Any] | None = None,
    session_input: AdmissionSessionInput | None = None,
) -> AdmissionResult:
    """Caller commits event/saga/admission/presence/audit together.

    Lock order: saga -> event -> parent -> ordered physical targets/children ->
    visitor reservation (P06 participant) -> per-person presence -> session.
    A gate receipt is evidence of opening, never proof of physical passage.
    """
    if mode not in {"live", "historical", "external"}:
        raise ValueError("An explicit supported admission mode is required.")
    saga = await session.get(MovementSagaRecord, uuid.UUID(str(saga_id)),
                             with_for_update=True, populate_existing=True)
    if saga is None or saga.access_event_id is None:
        raise ValueError("Admission requires an existing movement saga and access event.")
    event = await session.get(AccessEvent, saga.access_event_id, with_for_update=True, populate_existing=True)
    if event is None:
        raise ValueError("The movement access event is no longer available.")
    if saga.admission_status == "historical" and mode == "live":
        raise ValueError("Historical movement cannot be promoted into live admission.")
    before = (saga.admission_status, saga.admission_evidence, saga.state,
              saga.presence_committed, saga.reconciliation_required)
    projection = None
    parent = None
    if mode == "live" and event.decision == AccessDecision.GRANTED and event.direction == AccessDirection.ENTRY:
        statement = select(GateCommandRecord).where(
            GateCommandRecord.movement_saga_id == saga.id, GateCommandRecord.access_event_id == event.id)
        if gate_command_id is not None:
            statement = statement.where(GateCommandRecord.id == uuid.UUID(str(gate_command_id)))
        parent = await session.scalar(statement.order_by(GateCommandRecord.created_at.desc(),
            GateCommandRecord.id.desc()).limit(1).with_for_update().execution_options(populate_existing=True))
        if gate_command_id is not None and parent is None:
            raise ValueError("The gate command does not belong to this movement and event.")
        if parent is not None:
            projection = await AccessDeviceCommandJournal().reconcile_parent_in_session(session, parent)
    status, evidence = _admission_classification(event, saga, mode=mode, parent=parent,
        projection=projection, historical_evidence=historical_evidence)
    if mode == "live" and event.direction == AccessDirection.ENTRY:
        from app.services.visitor_passes import get_visitor_pass_service

        entry_delivery = None
        if projection and parent:
            designated = ((parent.command_metadata or {}).get("target_plan") or {}).get("admission_target_device_id")
            receipt = next((item for item in projection["target_receipts"] if item["target_device_id"] == designated), None)
            entry_delivery = receipt["delivery"] if receipt else projection["delivery"]
        elif parent:
            entry_delivery = ("not_sent" if unattempted_access_gate_evidence(parent, event_id=event.id,
                saga_id=saga.id) else "unknown")
        await get_visitor_pass_service().settle_admission_in_session(session, event=event,
            gate_command_id=parent.id if parent else None, admission_status=status,
            verification_evidence=evidence if status == "verified" else None, entry_delivery=entry_delivery)
    transition = PresenceTransition(False, "admission_pending" if status == "pending" else "ineligible")
    if status in {"verified", "not_required", "historical"}:
        transition = await apply_eligible_event_in_session(session, event)
    saga.admission_status, saga.admission_evidence = status, evidence
    saga.presence_committed = bool(saga.presence_committed or transition.changed
                                   or transition.result == "already_applied")
    reconciliation = status == "pending" or bool(projection and projection["requires_reconciliation"])
    target_state = (MovementSagaState.RECONCILIATION_REQUIRED if status == "pending"
                    else MovementSagaState.FAILED if status == "denied" and event.decision == AccessDecision.GRANTED
                    else MovementSagaState.COMPLETED)
    after = (status, evidence, target_state, saga.presence_committed, reconciliation)
    changed = before != after
    if changed:
        await get_movement_ledger_repository().transition_movement_saga(session, saga, target_state,
            detail=f"admission_{status}:{transition.result}", presence_committed=saga.presence_committed,
            reconciliation_required=reconciliation,
            failure_detail="admission_not_verified" if target_state == MovementSagaState.FAILED else None)
        await write_audit_log(session, category=TELEMETRY_CATEGORY_ACCESS, action="movement.admission.finalized",
            actor="IACS", target_entity="MovementSaga", target_id=saga.id,
            metadata={"access_event_id": str(event.id), "admission_status": status,
                "admission_evidence": evidence, "presence_result": transition.result,
                "presence_changed": transition.changed, "mode": mode,
                "gate_command_id": str(parent.id) if parent else None})
    if session_input is not None:
        await MovementSessionService().remember_session_in_db(session, event, session_input.reads,
            first_seen=session_input.first_seen, updated_at=session_input.updated_at,
            read=session_input.read, movement_saga_id=saga.id, runtime=session_input.runtime)
    previous_summary = (event.raw_payload or {}).get("movement_saga") or {}
    gate_receipt = ({**get_movement_ledger_repository().outcome_payload_from_record(parent), **(projection or {}),
                     "source": parent.source, "action": parent.action, "gate_key": parent.gate_key,
                     "event_id": str(event.id)} if parent else previous_summary.get("gate"))
    if parent and projection is None:
        no_attempt = unattempted_access_gate_evidence(parent, event_id=event.id, saga_id=saga.id)
        gate_receipt.update(delivery="not_sent" if no_attempt else "accepted" if parent.accepted else "unknown",
            admission_verified=False, target_receipts=[], requires_reconciliation=not bool(no_attempt))
    event.raw_payload = {**(event.raw_payload or {}), "movement_saga": {
        **previous_summary, **movement_saga_summary(saga), "gate": gate_receipt,
        "detail": parent.detail if parent else (evidence or {}).get("reason")}}
    if parent is not None:
        from app.services.access.delivery import reserve_gate_outcome_outputs

        await reserve_gate_outcome_outputs(session, event=event, parent=parent, receipt=gate_receipt)
    if status == "verified":
        from app.services.access.delivery import reserve_verified_arrival_notification

        await reserve_verified_arrival_notification(session, event)
    await session.flush()
    return AdmissionResult(event, saga, status, evidence, transition.changed, transition.result,
                           reconciliation, changed)


def _admission_classification(event: AccessEvent, saga: MovementSagaRecord, *, mode: str,
    parent: GateCommandRecord | None, projection: dict[str, Any] | None,
    historical_evidence: dict[str, Any] | None,
) -> tuple[str, dict[str, Any] | None]:
    if mode in {"historical", "external"}:
        if not historical_evidence:
            raise ValueError("Historical/external admission requires explicit retained provenance.")
        if saga.admission_status is not None and saga.admission_status != "historical":
            raise ValueError("A live admission cannot be reclassified as historical or external.")
        return "historical", {**historical_evidence, "mode": mode, "hardware_actions_suppressed": True,
                              "visitor_consumption_suppressed": True}
    if event.decision != AccessDecision.GRANTED:
        return "denied", None
    if event.direction != AccessDirection.ENTRY:
        return "not_required", {"reason": "authorized_non_entry"}
    # Retained verified evidence survives command/observation history retention.
    if saga.admission_status == "verified" and saga.admission_evidence:
        return "verified", saga.admission_evidence
    if parent and (proof := unattempted_access_gate_evidence(parent, event_id=event.id, saga_id=saga.id)):
        return "denied", {"reason": "expired_before_command_reservation", "gate_command_id": str(parent.id),
                          "no_attempt_evidence": proof}
    if projection and projection["admission_verified"]:
        plan = (parent.command_metadata or {})["target_plan"]
        receipt = next(item for item in projection["target_receipts"]
                       if item["target_device_id"] == plan["admission_target_device_id"])
        return "verified", {"gate_command_id": str(parent.id), "target_command_id": receipt["command_id"],
                            **receipt["verification_evidence"]}
    if parent and (parent.command_metadata or {}).get("automatic_entry_precondition", {}).get("mode") == "unresolved":
        return "pending", {"reason": "entry_state_requires_review", "gate_command_id": str(parent.id)}
    if projection and not projection["requires_reconciliation"]:
        return "denied", {"reason": "designated_entry_not_verified", "gate_command_id": str(parent.id)}
    return "pending", {"reason": "entry_verification_pending", "gate_command_id": str(parent.id) if parent else None}


async def recover_unattempted_visitor_reservations(
    *, limit: int = 25, after_id: uuid.UUID | None = None,
) -> tuple[int, uuid.UUID | None]:
    """Bounded fair recovery in an intact journal timeline; never dispatch hardware.

    Restore activation must remain in IACS_RECOVERY_HOLD until operator review.
    The hold cannot detect a restored database, nor can an absent row prove that
    an operation was never sent outside the retained journal lineage.
    """
    if not 1 <= limit <= 100:
        raise ValueError("Reservation recovery limit must be between 1 and 100.")
    if is_recovery_hold():
        return 0, after_id
    async with AsyncSessionLocal() as session:
        identities = list((await session.scalars(select(VisitorPassReservationRecord.id).where(
            VisitorPassReservationRecord.state == "reserved",
            VisitorPassReservationRecord.dispatch_deadline < func.clock_timestamp(),
            VisitorPassReservationRecord.id > after_id if after_id else True)
            .order_by(VisitorPassReservationRecord.id).limit(limit))).all())
    count = 0
    for identity in identities:
        async with AsyncSessionLocal() as session:
            count += int(await recover_unattempted_reservation_in_session(session, reservation_id=identity))
            await session.commit()
    return count, identities[-1] if len(identities) == limit else None


async def recover_unattempted_reservation_in_session(
    session: AsyncSession, *, reservation_id: uuid.UUID,
) -> bool:
    """Operation advisory -> saga -> event -> parent -> visitor -> reservation.

    The lock is nonblocking: an active dispatch cannot hold up the next candidate.
    Caller commits the retained no-send parent, admission, reservation and required
    delivery intents together. Missing/conflicting history is a durable review hold.
    """
    if is_recovery_hold():
        return False
    candidate = await session.get(VisitorPassReservationRecord, reservation_id)
    if candidate is None or candidate.state != "reserved":
        return False
    event_id, visitor_id = candidate.access_event_id, candidate.visitor_pass_id
    intent_id = uuid.uuid5(event_id, "automatic-gate-open")
    key = f"gate-command:open:default:event:{event_id}"
    ledger = get_movement_ledger_repository()
    if not await ledger.lock_gate_operation_in_session(session, key, wait=False):
        return False
    sagas = list((await session.scalars(select(MovementSagaRecord).where(
        MovementSagaRecord.access_event_id == event_id).order_by(MovementSagaRecord.id)
        .with_for_update().execution_options(populate_existing=True))).all())
    event = await session.get(AccessEvent, event_id, with_for_update=True, populate_existing=True)
    parents = list((await session.scalars(select(GateCommandRecord).where(or_(
        GateCommandRecord.idempotency_key == key, GateCommandRecord.access_event_id == event_id,
        GateCommandRecord.movement_saga_id.in_([row.id for row in sagas]),
        GateCommandRecord.command_metadata["intent_id"].astext == str(intent_id),
        GateCommandRecord.id == candidate.gate_command_id if candidate.gate_command_id else False))
        .order_by(GateCommandRecord.id).with_for_update().execution_options(populate_existing=True))).all())
    visitor = await session.get(VisitorPass, visitor_id, with_for_update=True, populate_existing=True)
    reservation = await session.get(VisitorPassReservationRecord, reservation_id,
                                   with_for_update=True, populate_existing=True)
    now = await session.scalar(select(func.clock_timestamp()))
    if (reservation is None or reservation.state != "reserved" or now <= reservation.dispatch_deadline
            or is_recovery_hold()):
        return False
    from app.services.access.authorization import RecognitionAuthorizationDenied, recognition_deadline_for_event
    from app.services.visitor_passes import get_visitor_pass_service

    reason = None
    saga = sagas[0] if len(sagas) == 1 else None
    raw_payload = event.raw_payload if event is not None else None
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    visitor_payload = payload.get("visitor_pass")
    visitor_payload = visitor_payload if isinstance(visitor_payload, dict) else {}
    if event is None or visitor is None or not sagas:
        reason = "origin_missing"
    elif (saga is None or reservation.intent_id != intent_id or reservation.access_event_id != event_id
            or reservation.visitor_pass_id != visitor_id
            or reservation.normalized_plate != normalize_registration_number(event.registration_number)
            or reservation.pass_type != visitor.pass_type.value
            or str(visitor_payload.get("id")) != str(visitor_id)
            or visitor_payload.get("mode") != "arrival"
            or event.decision != AccessDecision.GRANTED or event.direction != AccessDirection.ENTRY
            or saga.decision != event.decision or saga.direction != event.direction
            or saga.admission_status not in {None, "pending"}
            or "backfill" in event.source.casefold() or "backfill" in payload
            or payload.get("backfilled") or payload.get("skip_automation_actions")):
        reason = "origin_identity_conflict"
    else:
        try:
            deadline = await recognition_deadline_for_event(session, event)
        except RecognitionAuthorizationDenied:
            reason = "recognition_origin_missing"
        else:
            if deadline != reservation.dispatch_deadline:
                reason = "dispatch_deadline_conflict"
    if reason is None and reservation.gate_command_id and not any(
            row.id == reservation.gate_command_id for row in parents):
        reason = "previously_linked_parent_missing"
    if reason is None and parents:
        if (len(parents) != 1 or parents[0].idempotency_key != key
                or parents[0].movement_saga_id != saga.id or parents[0].access_event_id != event_id
                or parents[0].source != "automatic_lpr_grant" or parents[0].action != "open"
                or not isinstance(parents[0].command_metadata, dict)
                or parents[0].command_metadata.get("intent_id") != str(intent_id)
                or (reservation.gate_command_id and parents[0].id != reservation.gate_command_id)):
            reason = "command_identity_conflict"
        else:
            return False  # Retained parent/children belong to normal receipt reconciliation.
    if reason is None and await session.scalar(select(AccessDeviceCommandRecord.id).where(
            AccessDeviceCommandRecord.intent_id == str(intent_id)).limit(1)) is not None:
        reason = "orphan_target_command"
    if reason:
        return await get_visitor_pass_service().hold_reservation_for_review_in_session(
            session, reservation=reservation, reason=reason)
    parent = await ledger.record_unattempted_access_gate_in_session(session, event=event, saga=saga,
        reservation_id=reservation.id, intent_id=intent_id, dispatch_deadline=reservation.dispatch_deadline)
    await finalize_in_session(session, saga_id=saga.id, gate_command_id=parent.id)
    return True
