"""Notification text and preview helpers with no delivery or durable effects."""

import re
from datetime import UTC, datetime
from typing import Any

from app.modules.notifications.base import ComposedNotification, NotificationContext
from app.services.workflows import notification_payloads
from app.services.workflows.context import canonical_key
from app.services.workflows.notification_payloads import _duration_label_from_seconds
from app.services.workflows.vehicle_away import vehicle_time_away_label

GATE_MALFUNCTION_UPDATE_PREFIX = "Gate Malfunction Update:"
GATE_MALFUNCTION_VOICE_PREFIX = "Attention."


def composed_from_context(context: NotificationContext) -> ComposedNotification:
    variables = context_variables(context)
    return ComposedNotification(
        title=context.subject,
        body=variables.get("Message") or context.subject,
    )



def context_variables(context: NotificationContext) -> dict[str, str]:
    facts = {
        canonical_key(key): "" if value is None else str(value)
        for key, value in context.facts.items()
    }

    def pick(*keys: str, default: str = "") -> str:
        for key in keys:
            value = facts.get(canonical_key(key))
            if value:
                return value
        return default

    display_name = pick("display_name", "person", "person_name")
    first_name = pick("first_name", "person_first_name")
    last_name = pick("last_name", "person_last_name")
    if display_name and not first_name:
        first_name = display_name.split(" ", 1)[0]
    if display_name and not last_name and " " in display_name:
        last_name = display_name.split(" ", 1)[1]

    visitor_pass_registration = pick(
        "visitor_pass_registration",
        "visitor_pass_vehicle_registration",
        "visitor_pass_registration_number",
        "number_plate",
        "vehicle_registration_number",
        "registration_number",
    )
    visitor_pass_make = pick("visitor_pass_vehicle_make", "visitor_pass_make", "vehicle_make", "make")
    visitor_pass_colour = pick(
        "visitor_pass_vehicle_colour",
        "visitor_pass_vehicle_color",
        "visitor_pass_colour",
        "visitor_pass_color",
        "vehicle_colour",
        "vehicle_color",
        "colour",
        "color",
    )
    visitor_pass_duration = pick("visitor_pass_duration_on_site", "duration_human", "duration_on_site")
    if not visitor_pass_duration:
        visitor_pass_duration = _duration_label_from_seconds(
            pick("visitor_pass_duration_on_site_seconds", "duration_on_site_seconds")
        )
    visitor_pass_time_window = pick(
        "visitor_pass_time_window",
        "visitor_pass_window_label",
        "visitor_pass_current_window",
    )

    vehicle_name = pick(
        "vehicle_name",
        "vehicle_display_name",
        "vehicle_description",
        "visitor_pass_vehicle_make",
        "visitor_pass_vehicle_registration",
        "vehicle_make",
        "make",
        "registration_number",
        default=context.subject,
    )
    occurred_at = pick("occurred_at", "created_at")
    if context.event_type == "unauthorized_plate":
        vehicle_color = pick(
            "detected_vehicle_colour",
            "detected_vehicle_color",
            "observed_vehicle_colour",
            "observed_vehicle_color",
            "vehicle_colour",
            "vehicle_color",
            "colour",
            "color",
        )
    else:
        vehicle_color = pick(
            "visitor_pass_vehicle_colour",
            "visitor_pass_vehicle_color",
            "vehicle_color",
            "vehicle_colour",
            "detected_vehicle_color",
            "detected_vehicle_colour",
            "color",
            "colour",
        )
    return {
        "FirstName": first_name,
        "FirstNamePossessive": _possessive(first_name),
        "ObjectPronoun": pick("object_pronoun", "pronoun_object", default="them"),
        "PossessiveDeterminer": pick("possessive_determiner", "pronoun_possessive", default="their"),
        "LastName": last_name,
        "DisplayName": display_name or first_name or "Unknown visitor",
        "GroupName": pick("group_name", "group"),
        "Registration": pick(
            "visitor_pass_vehicle_registration",
            "vehicle_registration_number",
            "registration_number",
            "vrn",
            default=context.subject,
        ),
        "VehicleRegistrationNumber": pick(
            "visitor_pass_vehicle_registration",
            "vehicle_registration_number",
            "registration_number",
            "vrn",
            default=context.subject,
        ),
        "VehicleName": vehicle_name,
        "VehicleDisplayName": vehicle_name,
        "VehicleTimeAway": vehicle_time_away_label(pick("vehicle_time_away_seconds")),
        "VehicleMake": pick("visitor_pass_vehicle_make", "vehicle_make", "make"),
        "VehicleType": pick("vehicle_type", "detected_vehicle_type", "observed_vehicle_type"),
        "VehicleModel": pick("vehicle_model", "model"),
        "VehicleColor": vehicle_color,
        "VehicleColour": vehicle_color,
        "MotStatus": pick("mot_status", "motStatus"),
        "MotExpiry": pick("mot_expiry", "motExpiry", "mot_expiry_date"),
        "TaxStatus": pick("tax_status", "taxStatus"),
        "TaxExpiry": pick("tax_expiry", "taxExpiry", "tax_due_date", "taxDueDate"),
        "Direction": pick("direction"),
        "Decision": pick("decision"),
        "TimingClassification": pick("timing_classification"),
        "Source": pick("source"),
        "Severity": context.severity.title(),
        "EventType": context.event_type.replace("_", " ").title(),
        "Subject": context.subject,
        "Message": pick("message", default=context.subject),
        "OccurredAt": occurred_at,
        "Time": _time_label(occurred_at),
        "GateStatus": pick("gate_status", "gate_state"),
        "IntegrationName": pick("integration_name", "integration", "provider_name"),
        "IntegrationStatus": pick("integration_status", "status"),
        "IntegrationReason": pick("integration_reason", "degraded_reason", "failure_reason", "reason"),
        "IntegrationLastConnectedAt": pick("integration_last_connected_at", "last_connected_at"),
        "IntegrationLastFailureAt": pick("integration_last_failure_at", "last_failure_at"),
        "GarageDoor": pick("garage_door"),
        "EntityId": pick("entity_id"),
        "VisitorName": pick("visitor_name", "visitor_pass_name", default=display_name or context.subject),
        "VisitorPassName": pick("visitor_pass_name", "visitor_name", default=display_name or context.subject),
        "VisitorPassRegistration": visitor_pass_registration,
        "VisitorPassTimeWindow": visitor_pass_time_window,
        "VisitorPassVehicleRegistration": visitor_pass_registration,
        "VisitorPassVehicleMake": visitor_pass_make,
        "VisitorPassVehicleColour": visitor_pass_colour,
        "VisitorPassDurationOnSite": visitor_pass_duration,
        "NewWinnerName": pick("new_winner_name", "winner_name"),
        "OvertakenName": pick("overtaken_name", "previous_winner_name"),
        "ReadCount": pick("read_count", "leaderboard_read_count"),
        "MaintenanceModeReason": pick("maintenance_mode_reason", "maintenance_reason", "reason"),
        "MalfunctionDuration": pick("malfunction_duration"),
        "MalfunctionOpenedTime": pick("malfunction_opened_time"),
        "MalfunctionFixAttemptTime": pick("malfunction_fix_attempt_time"),
        "MalfunctionFixAttempts": pick("malfunction_fix_attempts"),
        "MalfunctionResolutionTime": pick("malfunction_resolution_time"),
        "MalfunctionStage": pick("malfunction_stage"),
        "LastKnownVehicle": pick("last_known_vehicle"),
    }



def context_occurred_at(context: NotificationContext) -> datetime:
    raw = context.facts.get("occurred_at") or context.facts.get("created_at") or ""
    if raw:
        try:
            parsed = datetime.fromisoformat(str(raw))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(tz=UTC)



def snapshot_payload(media: dict[str, Any]) -> dict[str, str | bool] | None:
    if not media.get("attach_camera_snapshot") or not media.get("camera_id"):
        return None
    camera_id = str(media["camera_id"])
    return {
        "enabled": True,
        "camera_id": camera_id,
        "image_url": f"/api/v1/integrations/unifi-protect/cameras/{camera_id}/snapshot?width=960&height=540",
    }



def gate_malfunction_notification_content(
    channel: str,
    context: NotificationContext,
    *,
    previous_notification: bool,
) -> dict[str, str]:
    facts = context.facts
    stage = notification_payloads.normalize_gate_malfunction_stage(facts.get("malfunction_stage"))
    stage_label = notification_payloads.GATE_MALFUNCTION_STAGE_LABELS.get(stage, stage)
    if stage == "resolved":
        title = "Gate malfunction resolved"
    elif stage == "fubar":
        title = "Gate malfunction needs attention"
    elif stage == "initial":
        title = "Gate malfunction detected"
    else:
        title = f"Gate malfunction {stage_label}"
    body = gate_malfunction_plain_body(stage)
    return {
        "title": title[:160],
        "body": postprocess_gate_malfunction_body(
            channel,
            body,
            previous_notification=previous_notification,
            default_body=body or context.subject,
        ),
    }



def gate_malfunction_plain_body(stage: str) -> str:
    normalized_stage = notification_payloads.normalize_gate_malfunction_stage(stage)
    if normalized_stage == "initial":
        return "The gate has malfunctioned and is stuck open. Automatic recovery is trying to resolve it."
    if normalized_stage == "30m":
        return "The gate is still stuck open. Automatic recovery is still working on it."
    if normalized_stage == "60m":
        return "The gate has been stuck open for about an hour. It is not looking good, but automatic recovery is still running."
    if normalized_stage == "2hrs":
        return "The gate has been stuck open for over two hours. Automatic recovery has not been able to fix it yet."
    if normalized_stage == "fubar":
        return "The gate is still stuck open and Automatic recovery has exhausted its available fixes. Please check the gate when you can."
    if normalized_stage == "resolved":
        return "The gate malfunction has been resolved and the gate is closed again."
    return "The gate has malfunctioned and is stuck open. Automatic recovery is trying to resolve it."



def clean_notification_text(value: str) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) > 1 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    return text



def postprocess_gate_malfunction_body(
    channel: str,
    body: str,
    *,
    previous_notification: bool,
    default_body: str,
) -> str:
    text = clean_notification_text(body) or clean_notification_text(default_body)
    text = strip_gate_malfunction_prefixes(text)
    if previous_notification:
        text = f"{GATE_MALFUNCTION_UPDATE_PREFIX} {text}".strip()
    if channel == "voice":
        text = f"{GATE_MALFUNCTION_VOICE_PREFIX} {strip_attention_prefix(text)}".strip()
    return text[:500]



def strip_gate_malfunction_prefixes(value: str) -> str:
    text = clean_notification_text(value)
    while True:
        next_text = strip_attention_prefix(text)
        next_text = strip_update_prefix(next_text)
        if next_text == text:
            return text
        text = next_text



def strip_attention_prefix(value: str) -> str:
    return re.sub(r"^\s*attention\.\s*", "", value, flags=re.IGNORECASE).strip()



def strip_update_prefix(value: str) -> str:
    return re.sub(
        r"^\s*gate\s+malfunction\s+update:\s*",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()



def _possessive(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    return f"{cleaned}'" if cleaned.lower().endswith("s") else f"{cleaned}'s"



def _time_label(value: str) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%H:%M")
    except ValueError:
        return value

