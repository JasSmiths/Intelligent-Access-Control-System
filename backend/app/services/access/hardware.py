from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import Person, Vehicle
from app.modules.gate.base import GateState
from app.services.access.authorization import (
    RecognitionAuthorizationDenied,
    assert_current_recognition_authorization,
    recognition_deadline_for_event,
)
from app.services.access.delivery import reserve_garage_outcome_outputs
from app.services.access_devices import get_access_device_service
from app.services.event_bus import event_bus
from app.services.gate_commands import (
    GateCommandIntent,
    GateCommandOutcome,
    get_gate_command_coordinator,
)
from app.services.telemetry import (
    TELEMETRY_CATEGORY_INTEGRATIONS,
    audit_log_event_payload,
    write_audit_log,
)


async def open_gate_for_access_event(
    event: Any,
    person: Any | None,
    *,
    open_garage_doors: bool,
    trace: Any | None = None,
    dvla_enrichment: dict[str, str | None] | None = None,
    movement_saga_id: str | None = None,
) -> GateCommandOutcome:
    reason = f"Automatic LPR grant for {event.registration_number}{f' ({person.display_name})' if person else ''}"
    gate_span = (
        trace.start_span(
            "Gate Command Saga - Open",
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            attributes={"event_id": str(event.id), "registration_number": event.registration_number, "controller": "configured"},
            input_payload={"reason": reason},
        )
        if trace
        else None
    )
    expires_at = await _recognition_expiry(event)

    async def authorize_dispatch(session):
        await assert_current_recognition_authorization(session, event_id=event.id)

    outcome = await get_gate_command_coordinator().execute_open(
        GateCommandIntent(
            reason=reason,
            source="automatic_lpr_grant",
            event_id=str(event.id),
            movement_saga_id=movement_saga_id,
            registration_number=event.registration_number,
            actor="Access Event Automation",
            idempotency_key=f"gate-command:open:default:event:{event.id}",
            intent_id=str(uuid.uuid5(uuid.UUID(str(event.id)), "automatic-gate-open")),
            expires_at=expires_at,
            automatic_entry_policy=True,
            authorize_dispatch=authorize_dispatch,
            metadata={
                "movement_saga_id": movement_saga_id,
                "person_id": str(getattr(person, "id", "")) if person else None,
                "vehicle_id": str(getattr(event, "vehicle_id", "")) if getattr(event, "vehicle_id", None) else None,
            },
        )
    )
    if gate_span:
        completed_without_failure = outcome.accepted or (
            outcome.delivery == "not_sent" and outcome.admission_verified
        )
        gate_span.finish(
            status="ok" if completed_without_failure else "error",
            output_payload=outcome.as_payload(),
            error=None if completed_without_failure else outcome.detail,
        )
    await _record_gate_outcome(event, person, reason, outcome, dvla_enrichment)
    precondition = outcome.metadata.get("automatic_entry_precondition") or {}
    if outcome.admission_verified and open_garage_doors and precondition.get("mode") == "fanout":
        await open_garage_doors_for_access_event(
            event,
            person,
            reason,
            trace=trace,
            dvla_enrichment=dvla_enrichment,
        )
    return outcome


async def _recognition_expiry(event: Any) -> datetime:
    """Capture the same durable cutoff for every target, including fallbacks.

    Missing intake provenance produces an expired intent, retaining a refusal
    without reaching hardware. Current mutable authority is checked separately
    inside each target's attempt transaction.
    """
    async with AsyncSessionLocal() as session:
        try:
            return await recognition_deadline_for_event(session, event)
        except RecognitionAuthorizationDenied:
            return datetime.min.replace(tzinfo=UTC)


async def publish_gate_open_skipped(event: Any, direction_resolution: dict[str, Any], person: Any | None = None) -> None:
    gate_observation = direction_resolution.get("gate_observation") or {}
    detail = "External admission was recorded without an IACS hardware command."
    await audit_automatic_hardware_command(
        action="gate.open.automatic",
        event=event,
        person=person,
        target_entity="Gate",
        target_label="Automatic Gate",
        outcome="skipped",
        level="warning",
        metadata={
            "controller": "configured",
            "reason": "external_admission_hardware_suppressed",
            "state": gate_observation.get("state") or GateState.UNKNOWN.value,
            "gate_observation": gate_observation,
            "direction_resolution": direction_resolution,
            "detail": detail,
            "garage_doors_skipped": True,
        },
    )
    await _publish(
        "gate.open_skipped",
        {
            "event_id": str(event.id),
            "registration_number": event.registration_number,
            "state": gate_observation.get("state") or GateState.UNKNOWN.value,
            "detail": detail,
        },
    )


async def open_garage_doors_for_access_event(
    event: Any,
    person: Any | None,
    reason: str,
    *,
    trace: Any | None = None,
    dvla_enrichment: dict[str, str | None] | None = None,
) -> None:
    if not person or not person.garage_door_entity_ids:
        return
    selected_ids = set(person.garage_door_entity_ids)
    service = get_access_device_service()
    devices = [
        device
        for device in await service.list_devices(kind="garage_door", enabled_only=True)
        if device.key in selected_ids
    ]
    for device in devices:
        span = (
            trace.start_span(
                "Garage Door Command",
                category=TELEMETRY_CATEGORY_INTEGRATIONS,
                attributes={"event_id": str(event.id), "entity_id": device.key, "name": device.name},
                input_payload={"reason": reason, "action": "open"},
            )
            if trace
            else None
        )
        async def authorize_dispatch(session, selected_key=device.key):
            await assert_current_recognition_authorization(session, event_id=event.id)
            current_person = await session.get(Person, event.person_id, populate_existing=True)
            if current_person is None or selected_key not in (current_person.garage_door_entity_ids or []):
                raise ValueError("The garage is no longer assigned to the recognized person.")

        operation_id = str(uuid.uuid5(uuid.UUID(str(event.id)), f"automatic-garage-open:{device.device_id}"))
        outcome = await service.command_device(
            device.key, "open", reason, schedule_source="garage_door", intent_id=operation_id,
            idempotency_key=f"garage-command:open:{device.device_id}:event:{event.id}",
            expires_at=await _recognition_expiry(event), authorize_dispatch=authorize_dispatch,
            origin_context={"kind": "automatic_access_garage", "access_event_id": str(event.id),
                            "target_label": device.name},
        )
        if span:
            completed_without_failure = outcome.accepted or (
                outcome.delivery == "not_sent" and outcome.verified
            )
            span.finish(
                status="ok" if completed_without_failure else "error",
                output_payload=outcome.as_payload(),
                error=None if completed_without_failure else outcome.detail,
            )
        await _record_garage_outcome(event, person, device, outcome, reason, dvla_enrichment)


async def audit_automatic_hardware_command(
    *,
    action: str,
    event: Any,
    person: Any | None,
    target_entity: str,
    outcome: str,
    level: str,
    target_id: str | None = None,
    target_label: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    vehicle_id = getattr(event, "vehicle_id", None)
    person_id = getattr(person, "id", None) or getattr(event, "person_id", None)
    async with AsyncSessionLocal() as session:
        vehicle = await session.get(Vehicle, vehicle_id) if vehicle_id else None
        row = await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            action=action,
            actor="Access Event Automation",
            target_entity=target_entity,
            target_id=target_id,
            target_label=target_label,
            outcome=outcome,
            level=level,
            metadata={
                "source": "automatic_lpr_grant",
                "access_event_id": str(event.id),
                "registration_number": event.registration_number,
                "direction": event.direction.value if hasattr(event.direction, "value") else str(event.direction),
                "decision": event.decision.value if hasattr(event.decision, "value") else str(event.decision),
                "person_id": str(person_id) if person_id else None,
                "person": person.display_name if person else None,
                "vehicle_id": str(vehicle_id) if vehicle_id else None,
                "vehicle_registration_number": getattr(vehicle, "registration_number", None),
                "event_source": getattr(event, "source", None),
                "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
                **(metadata or {}),
            },
        )
        await session.commit()
        await session.refresh(row)
    await _publish("audit.log.created", audit_log_event_payload(row))


async def _record_gate_outcome(
    event: Any,
    person: Any | None,
    reason: str,
    outcome: GateCommandOutcome,
    dvla_enrichment: dict[str, str | None] | None,
) -> None:
    # Required audit/notice is recovered by the admission transaction participant.
    # Realtime is a projection of the receipt and cannot reinterpret its boolean.
    await _publish(
        "gate.open_requested" if outcome.accepted or outcome.admission_verified else "gate.open_failed",
        {"event_id": str(event.id), "registration_number": event.registration_number,
         **outcome.as_payload()},
    )


async def _record_garage_outcome(
    event: Any,
    person: Any,
    device: Any,
    outcome: Any,
    reason: str,
    dvla_enrichment: dict[str, str | None] | None,
) -> None:
    delivery = str(outcome.delivery)
    verified = bool(getattr(outcome, "verified", False) or outcome.metadata.get("verified"))
    command_id = uuid.UUID(str(outcome.metadata["command_id"]))
    async with AsyncSessionLocal() as session:
        await reserve_garage_outcome_outputs(session, command_id=command_id)
        await session.commit()
    await _publish(
        "garage_door.open_requested" if outcome.accepted or verified else "garage_door.open_failed",
        {
            "event_id": str(event.id),
            "registration_number": event.registration_number,
            "person_id": str(person.id),
            "person": person.display_name,
            "entity_id": device.key,
            "name": device.name,
            "accepted": outcome.accepted,
            "state": outcome.state.value,
            "detail": outcome.detail,
            "delivery": delivery,
            "requires_reconciliation": outcome.requires_reconciliation,
            "receipt": outcome.as_payload(),
        },
    )


logger = get_logger(__name__)


async def _publish(name: str, payload: dict[str, Any]) -> None:
    try:
        await event_bus.publish(name, payload)
    except Exception as exc:  # noqa: BLE001 - isolate provider/reporting failure; cancellation propagates
        logger.warning("access_hardware_publication_failed", extra={"event_type": name, "exception_class": type(exc).__name__})
