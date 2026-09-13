"""Required access delivery intents, committed with their originating records.

These participants capture event facts and stable identities only. Dispatchers
own delivery and its current-authority checks; no provider or worker is invoked.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionLocal
from app.models import AccessDeviceCommandRecord, AccessEvent, Anomaly, AuditLog, GateCommandRecord, Person, Vehicle, VisitorPass
from app.modules.notifications.base import NotificationContext
from app.models.enums import GateCommandState
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS, write_audit_log
from app.services.access.payloads import access_event_realtime_payload, authorized_entry_message, notification_facts
from app.services.automation_intake import reserve_trigger
from app.services.access_device_commands import AccessDeviceCommandJournal, device_command_receipt
from app.services.notification_runs import NotificationRunStore
from app.services.workflows.automation_definition import automation_triggers_for_origin
from app.services.workflows.notification_payloads import notification_context_payload


async def reserve_observation_outputs(
    session: AsyncSession, *, event: AccessEvent, person: Person | None,
    vehicle: Vehicle | None, visitor_pass: VisitorPass | None,
    visitor_pass_mode: str | None, anomalies: list[Anomaly],
) -> None:
    await session.flush()  # Origin-created event/anomaly UUIDs are assigned before dedupe keys.
    payload = access_event_realtime_payload(event, anomaly_count=len(anomalies),
        visitor_pass=visitor_pass, visitor_pass_mode=visitor_pass_mode)
    for trigger, captured in automation_triggers_for_origin(
        "access_event.finalized", payload, occurred_at=event.occurred_at.isoformat()):
        await reserve_trigger(session, trigger, captured, origin_kind="access_event",
            origin_id=str(event.id), source="access_event",
            trace_id=str(((event.raw_payload or {}).get("telemetry") or {}).get("trace_id") or "") or None)
    store = NotificationRunStore()
    for anomaly in anomalies:
        context = NotificationContext(event_type=anomaly.anomaly_type.value,
            subject=event.registration_number, severity=anomaly.severity.value,
            facts=notification_facts(event, person, vehicle, anomaly.message))
        await store.enqueue_in_session(session, notification_context_payload(context),
            run_id=uuid.uuid5(anomaly.id, "access.anomaly.notification"))


async def reserve_verified_arrival_notification(session: AsyncSession, event: AccessEvent) -> None:
    """Called only by the admission finalizer after designated-entry verification.

    Idempotent across synchronous completion, late reconciliation and restart.
    Facts reflect the committed admission; optional enrichment is not required.
    """
    if event.person_id is None:
        return
    person = await session.scalar(select(Person).where(Person.id == event.person_id).options(selectinload(Person.group)))
    if person is None:
        return
    vehicle = await session.get(Vehicle, event.vehicle_id) if event.vehicle_id else None
    context = NotificationContext(event_type="authorized_entry",
        subject=f"{person.display_name} arrived at the gate", severity="info",
        facts=notification_facts(event, person, vehicle, authorized_entry_message(person, vehicle)))
    await NotificationRunStore().enqueue_in_session(session, notification_context_payload(context),
        run_id=uuid.uuid5(event.id, "access.authorized_entry.notification"))


async def reserve_gate_outcome_outputs(
    session: AsyncSession, *, event: AccessEvent, parent: GateCommandRecord,
    receipt: dict,
) -> None:
    """Audit and required notice recover with admission after the command checkpoint.

    A leased parent has not finished collecting its targets. Its retained claim
    and target journal remain the audit evidence until completion/reconciliation.
    One parent-locked finalizer writes the initial outcome and notice atomically;
    subsequent physical evidence has the separate admission transition audit.
    """
    if parent.state == GateCommandState.LEASED:
        return
    identity = uuid.uuid5(parent.id, "access.gate.outcome.audit")
    if await session.get(AuditLog, identity) is not None:
        return
    delivery = str(receipt.get("delivery") or (parent.command_metadata or {}).get("delivery") or "unknown")
    verified_without_send = delivery == "not_sent" and bool(receipt.get("admission_verified"))
    warning = bool(receipt.get("requires_reconciliation")) or delivery != "accepted" and not verified_without_send
    person = await session.scalar(select(Person).where(Person.id == event.person_id).options(selectinload(Person.group))) if event.person_id else None
    vehicle = await session.get(Vehicle, event.vehicle_id) if event.vehicle_id else None
    audit = await write_audit_log(session, category=TELEMETRY_CATEGORY_INTEGRATIONS,
        action="gate.open.automatic", actor="Access Event Automation", target_entity="Gate",
        target_label="Automatic Gate", outcome=delivery, level="warning" if warning else "info",
        metadata={"source": "automatic_lpr_grant", "controller": parent.controller, "access_event_id": str(event.id),
            "registration_number": event.registration_number, "person_id": str(event.person_id) if event.person_id else None,
            "vehicle_id": str(event.vehicle_id) if event.vehicle_id else None, **receipt})
    audit.id = identity
    if delivery != "accepted" and not verified_without_send:
        description = {
            "unknown": "Automatic gate request outcome is uncertain; review its receipts before another command.",
            "partial": "Automatic gate request has different target outcomes; review each target receipt.",
            "not_sent": "Automatic gate request was not sent. " + (parent.detail or "Review its preconditions."),
            "rejected": "Automatic gate request was rejected. " + (parent.detail or "Review its receipt."),
        }.get(delivery, "Automatic gate request needs review.")
        context = NotificationContext(event_type="gate_open_failed", subject=event.registration_number,
            severity="critical", facts=notification_facts(event, person, vehicle, description))
        await NotificationRunStore().enqueue_in_session(session, notification_context_payload(context),
            run_id=uuid.uuid5(parent.id, "access.gate.outcome.notification"))
    await session.flush()


async def reserve_garage_outcome_outputs(session: AsyncSession, *, command_id: uuid.UUID) -> bool:
    """Capture the initial automatic garage outcome atomically, without I/O.

    The journal retains transport/physical evidence and immutable origin. Its
    output checkpoint, audit and required notification commit or roll back
    together. Later physical evidence updates the receipt without replaying this
    initial notice. Historical unattributed commands remain inert.
    """
    command = await session.get(AccessDeviceCommandRecord, command_id,
                               with_for_update=True, populate_existing=True)
    if (command is None or command.origin_context is None or command.outcome_recorded_at is not None
            or command.origin_context.get("kind") != "automatic_access_garage"):
        return False
    now = await session.scalar(select(func.clock_timestamp()))
    if (command.state in {"prepared", "attempting"} and command.lease_expires_at
            and command.lease_expires_at > now):
        return False
    # Expiration can prove a prepared reservation was not sent, or retain an
    # attempted operation as unknown. It never makes an uncertain target retryable.
    AccessDeviceCommandJournal.expire(command, now)
    receipt = device_command_receipt(command)
    origin = command.origin_context
    event = await session.get(AccessEvent, uuid.UUID(origin["access_event_id"]))
    person = (await session.scalar(select(Person).where(Person.id == event.person_id)
                                  .options(selectinload(Person.group))) if event and event.person_id else None)
    vehicle = await session.get(Vehicle, event.vehicle_id) if event and event.vehicle_id else None
    delivery = receipt["delivery"]
    verified_without_send = delivery == "not_sent" and receipt["verified"]
    review_reason = "origin_event_missing" if event is None else None
    warning = bool(review_reason or receipt["requires_reconciliation"]
                   or delivery != "accepted" and not verified_without_send)
    audit_id = uuid.uuid5(command.id, "access.garage.outcome.audit")
    if await session.get(AuditLog, audit_id) is None:
        audit = await write_audit_log(session, category=TELEMETRY_CATEGORY_INTEGRATIONS,
            action="garage_door.open.automatic", actor="Access Event Automation",
            target_entity="GarageDoor", target_id=command.device_key, target_label=origin["target_label"],
            outcome=delivery, level="warning" if warning else "info",
            metadata={"source": "automatic_lpr_grant", "access_event_id": origin["access_event_id"],
                "registration_number": event.registration_number if event else None,
                "person_id": str(event.person_id) if event and event.person_id else None,
                "vehicle_id": str(event.vehicle_id) if event and event.vehicle_id else None,
                "controller": (command.provider_receipts or [{}])[-1].get("provider", "configured"),
                "origin_resolution": "event_missing" if event is None else "retained_event",
                "review_reason": review_reason, **receipt})
        audit.id = audit_id
    if delivery != "accepted" and not verified_without_send:
        description = {
            "unknown": "Automatic garage request outcome is uncertain; review its receipt before another command.",
            "not_sent": "Automatic garage request was not sent. " + (command.detail or "Review its preconditions."),
            "rejected": "Automatic garage request was rejected. " + (command.detail or "Review its receipt."),
        }.get(delivery, "Automatic garage request needs review.")
        if event is None:
            description += " Its originating access event is no longer retained."
            facts = {"message": description, "access_event_id": origin["access_event_id"],
                     "garage_door": origin["target_label"], "entity_id": command.device_key}
        else:
            facts = notification_facts(event, person, vehicle, description,
                                       garage_door=origin["target_label"], entity_id=command.device_key)
        facts.update({"command_id": str(command.id), "target_device_id": str(command.target_device_id),
                      "delivery": delivery, "verified": receipt["verified"],
                      "requires_reconciliation": receipt["requires_reconciliation"]})
        context = NotificationContext(event_type="garage_door_open_failed",
            subject=event.registration_number if event else origin["target_label"], severity="critical", facts=facts)
        await NotificationRunStore().enqueue_in_session(session, notification_context_payload(context),
            run_id=uuid.uuid5(command.id, "access.garage.outcome.notification"))
    command.outcome_recorded_at = await session.scalar(select(func.clock_timestamp()))
    await session.flush()
    return True


async def recover_garage_outcome_outputs(*, limit: int = 25) -> int:
    """One bounded DB-only batch; active commands cannot starve completed outputs."""
    if not 1 <= limit <= 100:
        raise ValueError("Garage output recovery batch size must be between 1 and 100.")
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        ids = list((await session.scalars(select(AccessDeviceCommandRecord.id).where(
            AccessDeviceCommandRecord.origin_context.is_not(None),
            AccessDeviceCommandRecord.origin_context["kind"].astext == "automatic_access_garage",
            AccessDeviceCommandRecord.outcome_recorded_at.is_(None),
            or_(AccessDeviceCommandRecord.state.not_in(["prepared", "attempting"]),
                AccessDeviceCommandRecord.lease_expires_at.is_(None),
                AccessDeviceCommandRecord.lease_expires_at <= now),
        ).order_by(AccessDeviceCommandRecord.created_at, AccessDeviceCommandRecord.id)
          .limit(limit).with_for_update(skip_locked=True))).all())
        count = 0
        for identity in ids:
            count += await reserve_garage_outcome_outputs(session, command_id=identity)
        await session.commit()
        return count
