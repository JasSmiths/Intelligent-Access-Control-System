"""Pure ordered exact Protect identity selection for retained access evidence."""

from typing import Any


def protect_event_id_from_payload(raw_payload: dict[str, Any]) -> str | None:
    if not isinstance(raw_payload, dict):
        return None
    candidates = [
        _nested_value(raw_payload, ("best", "alarm", "triggers", 0, "eventId")),
        _nested_value(raw_payload, ("best", "alarm", "triggers", 0, "event_id")),
        _nested_value(raw_payload, ("best", "alarm", "eventId")),
        _nested_value(raw_payload, ("best", "alarm", "event_id")),
        _nested_value(raw_payload, ("best", "eventId")),
        _nested_value(raw_payload, ("best", "event_id")),
        _nested_value(raw_payload, ("protect_evidence", "event_id")),
        _nested_value(raw_payload, ("backfill", "protect_event_id")),
        _nested_value(raw_payload, ("snapshot_recovery", "protect_event_id")),
    ]
    vehicle_session = raw_payload.get("vehicle_session")
    if isinstance(vehicle_session, dict):
        protect_event_ids = vehicle_session.get("protect_event_ids")
        if isinstance(protect_event_ids, list):
            candidates.extend(protect_event_ids)

    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return None


def _nested_value(value: Any, path: tuple[Any, ...]) -> Any:
    current = value
    for key in path:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and isinstance(key, int) and 0 <= key < len(current):
            current = current[key]
        else:
            return None
    return current
