from __future__ import annotations

from datetime import date, datetime
from typing import Any

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
