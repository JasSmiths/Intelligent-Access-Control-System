"""Visitor notification facts and window formatting without messaging/provider I/O."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.modules.notifications.base import NotificationContext
from app.services.workflows.notification_payloads import (
    _duration_label_from_seconds,
    trigger_severity,
)

if TYPE_CHECKING:
    from app.services.event_bus import RealtimeEvent


def visitor_pass_notification_contexts_from_event(
    event: RealtimeEvent,
) -> list[NotificationContext]:
    if not event.type.startswith("visitor_pass."):
        return []
    payload = event.payload if isinstance(event.payload, dict) else {}
    visitor_pass = (
        payload.get("visitor_pass") if isinstance(payload.get("visitor_pass"), dict) else None
    )
    if not visitor_pass:
        return []

    if event.type == "visitor_pass.created":
        event_types = ["visitor_pass_created"]
    elif event.type == "visitor_pass.cancelled":
        event_types = ["visitor_pass_cancelled"]
    elif event.type == "visitor_pass.status_changed":
        if str(visitor_pass.get("status") or "").strip().lower() != "expired":
            return []
        event_types = ["visitor_pass_expired"]
    elif event.type == "visitor_pass.used":
        event_types = ["visitor_pass_used", "visitor_pass_vehicle_arrived"]
    elif event.type == "visitor_pass.departure_recorded":
        event_types = ["visitor_pass_vehicle_exited"]
    else:
        return []

    source = str(payload.get("source") or visitor_pass.get("creation_source") or "visitor_pass")
    return [
        NotificationContext(
            event_type=event_type,
            subject=_visitor_pass_notification_subject(event_type, visitor_pass),
            severity=trigger_severity(event_type),
            facts=_visitor_pass_notification_facts(event_type, visitor_pass, source=source),
        )
        for event_type in event_types
    ]


def _visitor_pass_notification_facts(
    event_type: str,
    visitor_pass: dict[str, Any],
    *,
    source: str,
) -> dict[str, str]:
    plate = _visitor_pass_text(visitor_pass.get("number_plate"))
    make = _visitor_pass_text(visitor_pass.get("vehicle_make"))
    colour = _visitor_pass_text(visitor_pass.get("vehicle_colour"))
    time_window = _visitor_pass_time_window(visitor_pass)
    duration = _visitor_pass_text(
        visitor_pass.get("duration_human")
    ) or _duration_label_from_seconds(visitor_pass.get("duration_on_site_seconds"))
    occurred_at = _visitor_pass_occurred_at(event_type, visitor_pass)
    access_event_id = (
        _visitor_pass_text(visitor_pass.get("departure_event_id"))
        if event_type == "visitor_pass_vehicle_exited"
        else _visitor_pass_text(visitor_pass.get("arrival_event_id"))
    )
    return {
        "message": _visitor_pass_notification_message(event_type, visitor_pass),
        "subject": _visitor_pass_notification_subject(event_type, visitor_pass),
        "visitor_name": _visitor_pass_name(visitor_pass),
        "display_name": _visitor_pass_name(visitor_pass),
        "visitor_pass_id": _visitor_pass_text(visitor_pass.get("id")),
        "visitor_pass_status": _visitor_pass_text(visitor_pass.get("status")),
        "visitor_pass_creation_source": _visitor_pass_text(visitor_pass.get("creation_source")),
        "visitor_pass_source": source,
        "visitor_pass_expected_time": _visitor_pass_text(visitor_pass.get("expected_time")),
        "visitor_pass_window_start": _visitor_pass_text(visitor_pass.get("window_start")),
        "visitor_pass_window_end": _visitor_pass_text(visitor_pass.get("window_end")),
        "visitor_pass_valid_from": _visitor_pass_text(visitor_pass.get("valid_from")),
        "visitor_pass_valid_until": _visitor_pass_text(visitor_pass.get("valid_until")),
        "visitor_pass_registration": plate,
        "visitor_pass_time_window": time_window,
        "visitor_pass_window_label": time_window,
        "visitor_pass_vehicle_registration": plate,
        "visitor_pass_vehicle_make": make,
        "visitor_pass_vehicle_colour": colour,
        "visitor_pass_duration_on_site": duration,
        "visitor_pass_duration_on_site_seconds": _visitor_pass_text(
            visitor_pass.get("duration_on_site_seconds")
        ),
        "vehicle_registration_number": plate,
        "registration_number": plate,
        "vehicle_make": make,
        "vehicle_color": colour,
        "vehicle_colour": colour,
        "duration_human": duration,
        "duration_on_site_seconds": _visitor_pass_text(
            visitor_pass.get("duration_on_site_seconds")
        ),
        "access_event_id": access_event_id,
        "arrival_event_id": _visitor_pass_text(visitor_pass.get("arrival_event_id")),
        "departure_event_id": _visitor_pass_text(visitor_pass.get("departure_event_id")),
        "telemetry_trace_id": _visitor_pass_text(visitor_pass.get("telemetry_trace_id")),
        "occurred_at": occurred_at,
        "source": source,
    }


def _visitor_pass_notification_subject(event_type: str, visitor_pass: dict[str, Any]) -> str:
    visitor_name = _visitor_pass_name(visitor_pass)
    if event_type == "visitor_pass_created":
        return f"Visitor Pass created for {visitor_name}"
    if event_type == "visitor_pass_cancelled":
        return f"Visitor Pass cancelled for {visitor_name}"
    if event_type == "visitor_pass_expired":
        return f"Visitor Pass expired for {visitor_name}"
    if event_type in {"visitor_pass_used", "visitor_pass_vehicle_arrived"}:
        return f"Visitor Pass vehicle arrived for {visitor_name}"
    if event_type == "visitor_pass_vehicle_exited":
        return f"Visitor Pass vehicle exited for {visitor_name}"
    return f"Visitor Pass update for {visitor_name}"


def _visitor_pass_notification_message(event_type: str, visitor_pass: dict[str, Any]) -> str:
    visitor_name = _visitor_pass_name(visitor_pass)
    vehicle = _visitor_pass_vehicle_label(visitor_pass)
    duration = _visitor_pass_text(
        visitor_pass.get("duration_human")
    ) or _duration_label_from_seconds(visitor_pass.get("duration_on_site_seconds"))
    if event_type == "visitor_pass_created":
        return f"Visitor Pass created for {visitor_name}."
    if event_type == "visitor_pass_cancelled":
        return f"Visitor Pass for {visitor_name} was cancelled."
    if event_type == "visitor_pass_expired":
        return f"Visitor Pass for {visitor_name} expired without a matching vehicle detection."
    if event_type == "visitor_pass_used":
        return f"Visitor Pass for {visitor_name} was used{f' by {vehicle}' if vehicle else ''}."
    if event_type == "visitor_pass_vehicle_arrived":
        return f"{visitor_name} arrived{f' in {vehicle}' if vehicle else ''}."
    if event_type == "visitor_pass_vehicle_exited":
        duration_suffix = f" after {duration}" if duration else ""
        return f"{visitor_name} exited{f' in {vehicle}' if vehicle else ''}{duration_suffix}."
    return f"Visitor Pass updated for {visitor_name}."


def _visitor_pass_occurred_at(event_type: str, visitor_pass: dict[str, Any]) -> str:
    if event_type == "visitor_pass_created":
        return _visitor_pass_text(visitor_pass.get("created_at"))
    if event_type == "visitor_pass_cancelled":
        return _visitor_pass_text(visitor_pass.get("updated_at"))
    if event_type == "visitor_pass_expired":
        return _visitor_pass_text(
            visitor_pass.get("window_end")
            or visitor_pass.get("valid_until")
            or visitor_pass.get("updated_at")
        )
    if event_type in {"visitor_pass_used", "visitor_pass_vehicle_arrived"}:
        return _visitor_pass_text(
            visitor_pass.get("arrival_time") or visitor_pass.get("updated_at")
        )
    if event_type == "visitor_pass_vehicle_exited":
        return _visitor_pass_text(
            visitor_pass.get("departure_time") or visitor_pass.get("updated_at")
        )
    return _visitor_pass_text(visitor_pass.get("updated_at") or visitor_pass.get("created_at"))


def _visitor_pass_vehicle_label(visitor_pass: dict[str, Any]) -> str:
    plate = _visitor_pass_text(visitor_pass.get("number_plate"))
    make = _visitor_pass_text(visitor_pass.get("vehicle_make"))
    colour = _visitor_pass_text(visitor_pass.get("vehicle_colour"))
    description = " ".join(part for part in [colour, make] if part)
    if description and plate:
        return f"{description} with registration {plate}"
    return description or plate


def _visitor_pass_time_window(visitor_pass: dict[str, Any]) -> str:
    explicit = _visitor_pass_text(
        visitor_pass.get("time_window") or visitor_pass.get("window_label")
    )
    if explicit:
        return explicit
    return visitor_window_label_from_values(
        visitor_pass.get("valid_from")
        or visitor_pass.get("window_start")
        or visitor_pass.get("expected_time"),
        visitor_pass.get("valid_until") or visitor_pass.get("window_end"),
    )


def _visitor_pass_name(visitor_pass: dict[str, Any]) -> str:
    return _visitor_pass_text(visitor_pass.get("visitor_name")) or "Unknown visitor"


def _visitor_pass_text(value: Any) -> str:
    return "" if value is None else str(value)


def visitor_window_label_from_values(
    start_value: Any, end_value: Any, timezone_name: str | None = "Europe/London"
) -> str:
    timezone = safe_zoneinfo(timezone_name)
    start = parse_datetime_value(start_value)
    end = parse_datetime_value(end_value)
    if not start:
        return ""
    start_text = start.astimezone(timezone).strftime("%d %b %Y, %H:%M")
    if not end:
        return start_text
    end_text = end.astimezone(timezone).strftime("%d %b %Y, %H:%M")
    return f"{start_text} to {end_text}"


def parse_datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_aware_utc(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _ensure_aware_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def _ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def safe_zoneinfo(timezone_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(str(timezone_name or "Europe/London"))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/London")
