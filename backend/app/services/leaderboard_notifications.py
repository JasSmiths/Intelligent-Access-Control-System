"""Immutable live chart-notification provenance shared by producer and authority."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

CHART_OPTIONAL_SECONDS = 300
_CHART_NOTIFICATION_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "iacs:leaderboard:overtake:v1")


def chart_notification_id(generation: int) -> uuid.UUID:
    if isinstance(generation, bool) or generation < 1:
        raise ValueError("A positive chart generation is required")
    return uuid.uuid5(_CHART_NOTIFICATION_NAMESPACE, str(generation))


def chart_notification_origin(
    generation: int, event_id: uuid.UUID, origin_at: datetime
) -> dict[str, Any]:
    """A new generation retains its original live clock, never the enqueue clock."""
    chart_notification_id(generation)
    if origin_at.tzinfo is None or origin_at.utcoffset() is None:
        raise ValueError("An aware chart origin is required")
    origin = origin_at.astimezone(UTC)
    return {
        "version": 1,
        "generation": generation,
        "event_id": str(event_id),
        "origin_at": origin.isoformat(),
        "deadline": (origin + timedelta(seconds=CHART_OPTIONAL_SECONDS)).isoformat(),
    }


def chart_notification_requires_check(payload: dict[str, Any]) -> bool:
    # Confirmed sends have their own actor/token/configuration authority. A manual
    # test may deliberately use this event type without being a live chart notice.
    if isinstance(payload.get("confirmed_delivery"), dict):
        return False
    return "leaderboard_origin" in payload or payload.get("event_type") == "leaderboard_overtake"


def _aware_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def chart_notification_denial(
    payload: dict[str, Any], run_id: uuid.UUID, now: datetime
) -> str | None:
    if not chart_notification_requires_check(payload):
        return None
    origin = payload.get("leaderboard_origin")
    if origin is None:
        return "leaderboard_origin_unavailable"
    if not isinstance(origin, dict) or payload.get("event_type") != "leaderboard_overtake":
        return "leaderboard_origin_invalid"
    generation = origin.get("generation")
    started = _aware_timestamp(origin.get("origin_at"))
    deadline = _aware_timestamp(origin.get("deadline"))
    if (
        type(origin.get("version")) is not int
        or origin.get("version") != 1
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
        or started is None
        or deadline is None
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        return "leaderboard_origin_invalid"
    try:
        uuid.UUID(str(origin["event_id"]))
        expected_deadline = started + timedelta(seconds=CHART_OPTIONAL_SECONDS)
    except (KeyError, ValueError, OverflowError):
        return "leaderboard_origin_invalid"
    if deadline != expected_deadline or run_id != chart_notification_id(generation) or started > now:
        return "leaderboard_origin_invalid"
    if now >= deadline:
        return "leaderboard_origin_expired"
    return None
