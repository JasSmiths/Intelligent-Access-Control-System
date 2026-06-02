from __future__ import annotations

from typing import Any

from app.db.session import AsyncSessionLocal
from app.models.enums import AnomalySeverity
from app.modules.gate.base import GateState
from app.modules.notifications.base import NotificationContext
from app.services.access.payloads import notification_facts
from app.services.access_devices import get_access_device_service
from app.services.event_bus import event_bus
from app.services.gate_commands import GateCommandIntent, GateCommandOutcome, get_gate_command_coordinator
from app.services.notifications import get_notification_service
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
    outcome = await get_gate_command_coordinator().execute_open(
        GateCommandIntent(
            reason=reason,
            source="automatic_lpr_grant",
            event_id=str(event.id),
            movement_saga_id=movement_saga_id,
            registration_number=event.registration_number,
            actor="Access Event Automation",
            idempotency_key=f"gate-command:open:default:event:{event.id}",
            metadata={
                "movement_saga_id": movement_saga_id,
                "person_id": str(getattr(person, "id", "")) if person else None,
                "vehicle_id": str(getattr(event, "vehicle_id", "")) if getattr(event, "vehicle_id", None) else None,
            },
        )
    )
    if gate_span:
        gate_span.finish(
            status="ok" if outcome.accepted else "error",
            output_payload=outcome.as_payload(),
            error=None if outcome.accepted else outcome.detail,
        )
    await _record_gate_outcome(event, person, reason, outcome, dvla_enrichment)
    if outcome.accepted and open_garage_doors:
        await open_garage_doors_for_access_event(
            event,
            person,
            reason,
            trace=trace,
            dvla_enrichment=dvla_enrichment,
        )
    return outcome


async def publish_gate_open_skipped(event: Any, direction_resolution: dict[str, Any], person: Any | None = None) -> None:
    gate_observation = direction_resolution.get("gate_observation") or {}
    detail = "Automatic gate and garage-door commands require the top gate to be closed at plate-read time."
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
            "reason": "gate_state_not_closed_at_plate_read_time",
            "state": gate_observation.get("state") or GateState.UNKNOWN.value,
            "gate_observation": gate_observation,
            "direction_resolution": direction_resolution,
            "detail": detail,
            "garage_doors_skipped": True,
        },
    )
    await event_bus.publish(
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
        outcome = await service.command_device(device.key, "open", reason, schedule_source="garage_door")
        if span:
            span.finish(
                status="ok" if outcome.accepted else "error",
                output_payload=outcome.as_payload(),
                error=None if outcome.accepted else outcome.detail,
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
    vehicle = getattr(event, "vehicle", None)
    vehicle_id = getattr(event, "vehicle_id", None) or getattr(vehicle, "id", None)
    person_id = getattr(person, "id", None) or getattr(event, "person_id", None)
    async with AsyncSessionLocal() as session:
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
    await event_bus.publish("audit.log.created", audit_log_event_payload(row))


async def _record_gate_outcome(
    event: Any,
    person: Any | None,
    reason: str,
    outcome: GateCommandOutcome,
    dvla_enrichment: dict[str, str | None] | None,
) -> None:
    audit_outcome = "accepted" if outcome.accepted else "rejected"
    audit_level = "info" if outcome.accepted else "warning"
    if outcome.exception_class == "UnsupportedModuleError":
        audit_outcome = "failed"
        audit_level = "error"
    await audit_automatic_hardware_command(
        action="gate.open.automatic",
        event=event,
        person=person,
        target_entity="Gate",
        target_label="Automatic Gate",
        outcome=audit_outcome,
        level=audit_level,
        metadata={"controller": "configured", "reason": reason, **outcome.as_payload()},
    )
    await event_bus.publish(
        "gate.open_requested" if outcome.accepted else "gate.open_failed",
        {
            "event_id": str(event.id),
            "registration_number": event.registration_number,
            "accepted": outcome.accepted,
            "state": outcome.state.value,
            "detail": outcome.detail,
            "intent_id": outcome.intent.intent_id,
            "mechanically_confirmed": outcome.mechanically_confirmed,
            "requires_reconciliation": outcome.requires_reconciliation,
        },
    )
    if not outcome.accepted:
        await get_notification_service().notify(
            NotificationContext(
                event_type="gate_open_failed",
                subject=event.registration_number,
                severity=AnomalySeverity.CRITICAL.value,
                facts=notification_facts(
                    event,
                    person,
                    getattr(event, "vehicle", None),
                    outcome.detail or "Automatic gate open command failed.",
                    dvla_enrichment=dvla_enrichment,
                ),
            )
        )


async def _record_garage_outcome(
    event: Any,
    person: Any,
    device: Any,
    outcome: Any,
    reason: str,
    dvla_enrichment: dict[str, str | None] | None,
) -> None:
    schedule_denied = bool(outcome.metadata.get("schedule_denied")) if hasattr(outcome, "metadata") else False
    await audit_automatic_hardware_command(
        action="garage_door.open.automatic",
        event=event,
        person=person,
        target_entity="GarageDoor",
        target_id=device.key,
        target_label=device.name,
        outcome="accepted" if outcome.accepted else "rejected" if schedule_denied else "failed",
        level="info" if outcome.accepted else "warning" if schedule_denied else "error",
        metadata={
            "controller": outcome.used_provider or outcome.primary_provider or "configured",
            "reason": reason,
            "accepted": outcome.accepted,
            "state": "schedule_denied" if schedule_denied else outcome.state.value,
            "detail": outcome.detail,
            "failover_used": outcome.failover_used,
            "attempts": [attempt.__dict__ for attempt in outcome.attempts],
            **({"schedule_denied": True} if schedule_denied else {}),
        },
    )
    await event_bus.publish(
        "garage_door.open_requested" if outcome.accepted else "garage_door.open_failed",
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
        },
    )
    if not outcome.accepted:
        await get_notification_service().notify(
            NotificationContext(
                event_type="garage_door_open_failed",
                subject=event.registration_number,
                severity=AnomalySeverity.CRITICAL.value,
                facts=notification_facts(
                    event,
                    person,
                    getattr(event, "vehicle", None),
                    outcome.detail or f"Automatic garage door open command failed for {device.name}.",
                    dvla_enrichment=dvla_enrichment,
                    garage_door=device.name,
                    entity_id=device.key,
                ),
            )
        )
