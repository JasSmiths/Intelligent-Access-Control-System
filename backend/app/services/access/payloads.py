from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from app.models import Person, Vehicle, VisitorPass
from app.modules.lpr.base import PlateRead
from app.services.access.reads import (
    EXTERNAL_ADMISSION_PAYLOAD_KEY,
    VEHICLE_VISUAL_DETECTION_PAYLOAD_KEY,
    VISITOR_PASS_PAYLOAD_KEY,
    WEBHOOK_TRACE_PAYLOAD_KEY,
    DebounceWindow,
    _external_admission_from_read,
    _gate_malfunction_from_read,
    _visitor_pass_plate_match_from_read,
    _webhook_trace_from_read,
)

if TYPE_CHECKING:
    from app.services.gate_commands import GateCommandOutcome
from app.services.movement.sessions import (
    candidate_registration_numbers as _candidate_registration_numbers,
)
from app.services.movement.sessions import (
    detected_registration_number as _detected_registration_number,
)
from app.services.movement.sessions import explicit_direction_from_read, gate_observation_from_read
from app.services.movement.sessions import (
    known_vehicle_plate_match_from_read as _known_vehicle_plate_match_from_read,
)
from app.services.movement_ledger import movement_saga_summary
from app.services.schedules import ScheduleEvaluation, schedule_evaluation_payload
from app.services.snapshots import access_event_snapshot_payload

PERSON_NOTIFICATION_PRONOUNS = {
    "he/him": ("him", "his"),
    "she/her": ("her", "her"),
}

SUGGESTED_PERSON_PRONOUNS_BY_FIRST_NAME = {
    **dict.fromkeys(("jason", "john", "james", "david", "michael", "paul", "mark", "peter", "stephen", "steven"), "he/him"),
    **dict.fromkeys(("sarah", "steph", "stephanie", "sylvia", "emma", "olivia", "amelia", "ava", "charlotte", "grace"), "she/her"),
}

def access_event_realtime_payload(
    event: Any,
    *,
    anomaly_count: int,
    visitor_pass: Any | None,
    visitor_pass_mode: str | None,
) -> dict[str, Any]:
    external_admission = external_admission_from_event(event)
    payload = {
        "event_id": str(event.id),
        "access_event_id": str(event.id),
        "person_id": str(event.person_id) if event.person_id else None,
        "vehicle_id": str(event.vehicle_id) if event.vehicle_id else None,
        "registration_number": event.registration_number,
        "direction": event.direction.value,
        "decision": event.decision.value,
        "confidence": event.confidence,
        "source": event.source,
        "occurred_at": event.occurred_at.isoformat(),
        "event_type": "access_event.finalized",
        "timing_classification": event.timing_classification.value,
        "anomaly_count": anomaly_count,
        "visitor_pass_id": str(visitor_pass.id) if visitor_pass else None,
        "visitor_name": visitor_pass.visitor_name if visitor_pass else None,
        "visitor_pass_mode": visitor_pass_mode if visitor_pass else None,
        "external_admission_mode": external_admission.get("mode") if external_admission else None,
        "external_admission_source": external_admission.get("source") if external_admission else None,
    }
    payload.update(access_event_snapshot_payload(event))
    return payload

def notification_facts(
    event: Any,
    person: Any | None,
    vehicle: Any | None,
    message: str,
    *,
    dvla_enrichment: dict[str, Any] | None = None,
    schedule_allowed: bool | None = None,
    garage_binding: str | None = None,
    compliance_summary: str | None = None,
    garage_door: str | None = None,
    entity_id: str | None = None,
) -> dict[str, Any]:
    dvla = dvla_enrichment or {}
    external_admission = external_admission_from_event(event)
    visual = vehicle_visual_detection_from_event(event)
    detected_vehicle_type = fact_text(
        visual.get("observed_vehicle_type")
        or visual.get("vehicle_type")
        or visual.get("detected_vehicle_type")
    )
    detected_vehicle_colour = fact_text(
        visual.get("observed_vehicle_color")
        or visual.get("observed_vehicle_colour")
        or visual.get("vehicle_color")
        or visual.get("vehicle_colour")
        or visual.get("detected_vehicle_color")
        or visual.get("detected_vehicle_colour")
    )
    vehicle_display = vehicle_display_name(vehicle, event.registration_number)
    vehicle_make = fact_text(dvla.get("make")) or (getattr(vehicle, "make", "") if vehicle else "") or ""
    dvla_colour = fact_text(dvla.get("colour"))
    vehicle_colour = (
        (dvla_colour or (getattr(vehicle, "color", "") if vehicle else "") or detected_vehicle_colour)
        if vehicle
        else (detected_vehicle_colour or dvla_colour)
    )
    object_pronoun, possessive_determiner = person_notification_pronouns(person)
    group_name = getattr(getattr(person, "group", None), "name", "") if person else ""
    facts = {
        "message": message,
        "access_event_id": str(event.id),
        "telemetry_trace_id": str(((event.raw_payload or {}).get("telemetry") or {}).get("trace_id") or ""),
        "first_name": getattr(person, "first_name", "") if person else "",
        "last_name": getattr(person, "last_name", "") if person else "",
        "display_name": getattr(person, "display_name", "") if person else "",
        "group_name": group_name,
        "vehicle_registration_number": event.registration_number,
        "registration_number": event.registration_number,
        "vehicle_display_name": vehicle_display,
        "vehicle_make": vehicle_make,
        "vehicle_type": detected_vehicle_type,
        "vehicle_model": getattr(vehicle, "model", "") if vehicle and getattr(vehicle, "model", None) else "",
        "vehicle_color": vehicle_colour or "",
        "vehicle_colour": vehicle_colour or "",
        "detected_vehicle_type": detected_vehicle_type,
        "detected_vehicle_color": detected_vehicle_colour,
        "detected_vehicle_colour": detected_vehicle_colour,
        "mot_status": fact_text(dvla.get("mot_status")),
        "mot_expiry": fact_text(dvla.get("mot_expiry")),
        "tax_status": fact_text(dvla.get("tax_status")),
        "tax_expiry": fact_text(dvla.get("tax_expiry")),
        "object_pronoun": object_pronoun,
        "possessive_determiner": possessive_determiner,
        "direction": event.direction.value,
        "decision": event.decision.value,
        "source": event.source,
        "external_admission_mode": fact_text(external_admission.get("mode") if external_admission else None),
        "external_admission_source": fact_text(external_admission.get("source") if external_admission else None),
        "timing_classification": event.timing_classification.value,
        "occurred_at": event.occurred_at.isoformat(),
    }
    extras = {
        "schedule_allowed": schedule_allowed,
        "garage_binding": garage_binding,
        "compliance_summary": compliance_summary,
        "garage_door": garage_door,
        "entity_id": entity_id,
    }
    facts.update({key: fact_text(value) for key, value in extras.items() if value is not None})
    return facts

def authorized_entry_message(person: Any, vehicle: Any | None) -> str:
    first_name = person.first_name or person.display_name.split(" ", 1)[0]
    possessive = f"{first_name}'" if first_name.lower().endswith("s") else f"{first_name}'s"
    object_pronoun, _possessive_determiner = person_notification_pronouns(person)
    vehicle_label = vehicle_display_name(vehicle, "")
    if vehicle_label:
        return (
            f"{possessive} {vehicle_label} has been detected at the gate. "
            f"I've let {object_pronoun} in."
        )
    return f"{person.display_name} has been detected at the gate. I've let {object_pronoun} in."

def vehicle_display_name(vehicle: Any | None, fallback: str) -> str:
    if not vehicle:
        return fallback
    label = " ".join(part for part in [getattr(vehicle, "make", None), getattr(vehicle, "model", None)] if part)
    return label or getattr(vehicle, "description", None) or getattr(vehicle, "registration_number", None) or fallback

def vehicle_visual_detection_from_event(event: Any) -> dict[str, Any]:
    return dict((event.raw_payload or {}).get("vehicle_visual_detection") or {})

def external_admission_from_event(event: Any) -> dict[str, Any] | None:
    raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    payload = raw_payload.get("external_admission")
    return payload if isinstance(payload, dict) else None

def fact_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value).strip()

def person_notification_pronouns(person: Any | None) -> tuple[str, str]:
    pronouns = str(getattr(person, "pronouns", None) or "").strip().casefold()
    if not pronouns and person:
        first_name = str(getattr(person, "first_name", "") or "").strip().casefold()
        pronouns = SUGGESTED_PERSON_PRONOUNS_BY_FIRST_NAME.get(first_name, "")
    return PERSON_NOTIFICATION_PRONOUNS.get(pronouns, ("them", "their"))

def _access_event_raw_payload(
    *,
    window: DebounceWindow,
    read: PlateRead,
    schedule_evaluation: ScheduleEvaluation | None,
    direction_resolution: dict[str, Any],
    vehicle_visual_detection: dict[str, Any] | None,
    visitor_pass: VisitorPass | None,
    visitor_pass_mode: str | None,
    trace_id: str,
    finalize_started_at: datetime,
    webhook_trace: dict[str, Any],
    external_admission: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "best": read.raw_payload,
        WEBHOOK_TRACE_PAYLOAD_KEY: webhook_trace,
        "schedule": schedule_evaluation_payload(schedule_evaluation, missing_reason="No active vehicle identity matched."),
        "debounce": {
            "candidate_count": len(window.reads),
            "first_seen": window.first_seen.isoformat(),
            "updated_at": window.updated_at.isoformat(),
            "finalize_started_at": finalize_started_at.isoformat(),
            "candidates": [
                {
                    "registration_number": item.registration_number,
                    "detected_registration_number": _detected_registration_number(item),
                    "confidence": item.confidence,
                    "captured_at": item.captured_at.isoformat(),
                    "candidate_registration_numbers": list(_candidate_registration_numbers(item)),
                    "known_vehicle_plate_match": _known_vehicle_plate_match_from_read(item),
                    "visitor_pass_plate_match": _visitor_pass_plate_match_from_read(item),
                    WEBHOOK_TRACE_PAYLOAD_KEY: _webhook_trace_from_read(item),
                }
                for item in window.reads
            ],
        },
        "direction_resolution": direction_resolution,
        VEHICLE_VISUAL_DETECTION_PAYLOAD_KEY: vehicle_visual_detection,
        VISITOR_PASS_PAYLOAD_KEY: _visitor_pass_payload(visitor_pass, visitor_pass_mode),
        EXTERNAL_ADMISSION_PAYLOAD_KEY: external_admission,
        "telemetry": {"trace_id": trace_id},
    }

def _movement_intent_payload(
    read: PlateRead,
    person: Person | None,
    vehicle: Vehicle | None,
    allowed: bool,
) -> dict[str, Any]:
    explicit_direction = explicit_direction_from_read(read)
    return {
        "source": read.source,
        "captured_at": read.captured_at.isoformat(),
        "registration_number": read.registration_number,
        "allowed": allowed,
        "person_id": str(person.id) if person else None,
        "vehicle_id": str(vehicle.id) if vehicle else None,
        "gate_observation": gate_observation_from_read(read),
        "explicit_direction": explicit_direction.value if explicit_direction else None,
        "known_vehicle_plate_match": _known_vehicle_plate_match_from_read(read),
        "visitor_pass_plate_match": _visitor_pass_plate_match_from_read(read),
        "external_admission": _external_admission_from_read(read),
        "gate_malfunction": _gate_malfunction_from_read(read),
    }

def _raw_payload_with_movement_saga(
    raw_payload: dict[str, Any] | None,
    *,
    state: str,
    gate_command_required: bool,
    presence_committed: bool,
    gate_outcome: GateCommandOutcome | None = None,
    movement_saga: Any | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    payload = dict(raw_payload or {})
    summary = movement_saga_summary(movement_saga)
    payload["movement_saga"] = {
        **(summary or {}),
        "state": state,
        "gate_command_required": gate_command_required,
        "presence_committed": presence_committed,
        "detail": detail,
        "gate": gate_outcome.as_payload() if gate_outcome else None,
    }
    return payload

def _visitor_pass_payload(
    visitor_pass: VisitorPass | None,
    mode: str | None,
) -> dict[str, Any] | None:
    if not visitor_pass:
        return None
    return {
        "id": str(visitor_pass.id),
        "visitor_name": visitor_pass.visitor_name,
        "pass_type": visitor_pass.pass_type.value,
        "status": visitor_pass.status.value,
        "mode": mode,
        "expected_time": visitor_pass.expected_time.isoformat(),
        "window_minutes": visitor_pass.window_minutes,
        "number_plate": visitor_pass.number_plate,
    }
