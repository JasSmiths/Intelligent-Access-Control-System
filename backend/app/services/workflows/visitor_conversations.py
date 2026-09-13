"""Pure visitor-conversation policy and retained metadata contracts.

Window limits, pending consent and abuse thresholds have one owner independent
of messaging adapters. Existing metadata keys remain stable for persisted passes.
Model and provider dependencies are deliberately absent.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
import uuid

from app.services.workflows.visitor_notifications import _ensure_aware_utc, parse_datetime_value, safe_zoneinfo

VISITOR_TIMEFRAME_AUTO_LIMIT_SECONDS = 60 * 60


VISITOR_ABUSE_WINDOW_SECONDS = 10 * 60


VISITOR_ABUSE_MUTE_SECONDS = 30 * 60


VISITOR_POST_COMPLETE_REPLY_LIMIT = 4


VISITOR_PLATE_CHANGE_LIMIT = 3


def normalize_contact_phone(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def visitor_status_metadata(
    current: dict[str, Any],
    status: str,
    *,
    detail: str | None = None,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = {
        **current,
        "whatsapp_concierge_status": status,
        "whatsapp_status_updated_at": datetime.now(tz=UTC).isoformat(),
    }
    if detail is not None:
        metadata["whatsapp_concierge_status_detail"] = detail
    if error is not None:
        metadata["whatsapp_last_error"] = error
    if extra:
        metadata.update(extra)
    return metadata


def visitor_pending_plate_metadata(current: dict[str, Any], **updates: Any) -> dict[str, Any]:
    metadata = {
        **current,
        "whatsapp_pending_plate": None,
        "whatsapp_pending_nonce": None,
        "whatsapp_pending_vehicle_make": None,
        "whatsapp_pending_vehicle_colour": None,
        "whatsapp_pending_vehicle_lookup_error": None,
        "whatsapp_status_updated_at": datetime.now(tz=UTC).isoformat(),
        **updates,
    }
    return {key: value for key, value in metadata.items() if value is not None}


def normalize_llm_timeframe_change_payload(payload: dict[str, Any], timezone_name: str | None = None) -> dict[str, Any] | None:
    requested_from = parse_llm_datetime_value(payload.get("valid_from"), timezone_name)
    requested_until = parse_llm_datetime_value(payload.get("valid_until"), timezone_name)
    if not requested_from or not requested_until or requested_until <= requested_from:
        return None
    normalized: dict[str, Any] = {
        "action": "timeframe_change",
        "valid_from": requested_from.isoformat(),
        "valid_until": requested_until.isoformat(),
        "summary": str(payload.get("summary") or "Visitor requested a timeframe change.")[:500],
    }
    source = str(payload.get("source") or "").strip()
    if source:
        normalized["source"] = source[:80]
    if truthy_value(payload.get("direct_apply")) or source == "dashboard_custom_proposal":
        normalized["direct_apply"] = True
    return normalized


def truthy_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_llm_datetime_value(value: Any, timezone_name: str | None = None) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=safe_zoneinfo(timezone_name)).astimezone(UTC)
        return _ensure_aware_utc(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=safe_zoneinfo(timezone_name))
    return _ensure_aware_utc(parsed)


def visitor_timeframe_original_window(
    metadata: dict[str, Any],
    current_start: datetime,
    current_end: datetime,
) -> tuple[datetime, datetime]:
    candidates = (
        ("whatsapp_timeframe_original_window", "valid_from", "valid_until"),
        ("whatsapp_timeframe_confirmation", "original_valid_from", "original_valid_until"),
        ("whatsapp_timeframe_request", "original_valid_from", "original_valid_until"),
        ("whatsapp_timeframe_confirmation", "current_valid_from", "current_valid_until"),
        ("whatsapp_timeframe_request", "current_valid_from", "current_valid_until"),
    )
    for key, start_key, end_key in candidates:
        payload = metadata.get(key)
        if not isinstance(payload, dict):
            continue
        original_start = parse_datetime_value(payload.get(start_key))
        original_end = parse_datetime_value(payload.get(end_key))
        if original_start and original_end and original_end > original_start:
            return original_start, original_end
    return _ensure_aware_utc(current_start), _ensure_aware_utc(current_end)


def visitor_timeframe_window_payload(start: datetime, end: datetime) -> dict[str, str]:
    return {"valid_from": start.isoformat(), "valid_until": end.isoformat()}


def visitor_timeframe_request_payload(
    request_id: str,
    text: str,
    summary: Any,
    current: tuple[datetime, datetime],
    original: tuple[datetime, datetime],
    requested: tuple[datetime, datetime],
) -> dict[str, str]:
    payload = {
        "id": request_id,
        "status": "pending",
        "requested_at": datetime.now(tz=UTC).isoformat(),
        "visitor_message": text[:500],
        "summary": str(summary or "Visitor requested a timeframe change.")[:500],
    }
    for prefix, (start, end) in {"current": current, "original": original, "requested": requested}.items():
        payload[f"{prefix}_valid_from"] = start.isoformat()
        payload[f"{prefix}_valid_until"] = end.isoformat()
    return payload


def timeframe_change_within_auto_limit(
    current_start: datetime,
    current_end: datetime,
    requested_start: datetime,
    requested_end: datetime,
) -> bool:
    return (
        abs((_ensure_aware_utc(requested_start) - _ensure_aware_utc(current_start)).total_seconds()) <= VISITOR_TIMEFRAME_AUTO_LIMIT_SECONDS
        and abs((_ensure_aware_utc(requested_end) - _ensure_aware_utc(current_end)).total_seconds()) <= VISITOR_TIMEFRAME_AUTO_LIMIT_SECONDS
    )


def visitor_pending_timeframe_request(metadata: dict[str, Any]) -> bool:
    return _visitor_pending_timeframe(metadata, "whatsapp_timeframe_request")


def _visitor_pending_timeframe(metadata: dict[str, Any], key: str) -> bool:
    request = metadata.get(key)
    return isinstance(request, dict) and str(request.get("status") or "").strip().lower() == "pending"


def recent_iso_timestamps(value: Any, *, now: datetime, window_seconds: int = VISITOR_ABUSE_WINDOW_SECONDS) -> list[str]:
    if not isinstance(value, list):
        return []
    threshold = now - timedelta(seconds=window_seconds)
    timestamps: list[str] = []
    for item in value:
        parsed = parse_datetime_value(item)
        if parsed and parsed >= threshold:
            timestamps.append(parsed.isoformat())
    return timestamps


def visitor_abuse_status_detail(reason: str) -> str:
    if reason == "plate_changes":
        return "Visitor sent repeated registration changes; replies are paused for 30 minutes."
    return "Visitor sent repeated post-confirmation replies; replies are paused for 30 minutes."


def visitor_vehicle_metadata_text(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    return text[:80] or None



def masked_contact_phone(value: Any) -> str:
    digits = normalize_contact_phone(value)
    if not digits:
        return ""
    return f"+...{digits[-4:]}" if len(digits) > 4 else "+..." + digits


def visitor_plate_pending_status_detail(vehicle_make: Any = None, vehicle_colour: Any = None) -> str:
    vehicle = visitor_vehicle_label(vehicle_make, vehicle_colour)
    if vehicle:
        return f"Visitor replied with a vehicle registration; identified {vehicle}; awaiting confirmation."
    return "Visitor replied with a vehicle registration; awaiting confirmation."


def visitor_vehicle_label(vehicle_make: Any = None, vehicle_colour: Any = None) -> str:
    make = visitor_vehicle_metadata_text(vehicle_make)
    colour = visitor_vehicle_metadata_text(vehicle_colour)
    if make and colour:
        return f"{colour} {make}"
    if make:
        return make
    if colour:
        return f"{colour} vehicle"
    return ""


def coerce_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
