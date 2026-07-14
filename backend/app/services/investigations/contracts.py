from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


Outcome = Literal[
    "succeeded",
    "blocked",
    "skipped",
    "failed",
    "pending",
    "cancelled",
    "unknown",
]
DispatchState = Literal[
    "withheld",
    "attempted_rejected",
    "accepted_unverified",
    "verified",
    "not_applicable",
    "unknown",
]

OUTCOMES: tuple[Outcome, ...] = (
    "succeeded",
    "blocked",
    "skipped",
    "failed",
    "pending",
    "cancelled",
    "unknown",
)

DISPATCH_STATES: tuple[DispatchState, ...] = (
    "withheld",
    "attempted_rejected",
    "accepted_unverified",
    "verified",
    "not_applicable",
    "unknown",
)

RELATIVE_RANGES = {
    "today",
    "yesterday",
    "last_24_hours",
    "last_7_days",
    "custom",
}


class InvalidCursorError(ValueError):
    pass


class InvalidTimeRangeError(ValueError):
    pass


@dataclass(frozen=True)
class ActivityFilters:
    from_at: datetime | None = None
    to_at: datetime | None = None
    time_range: str | None = None
    device: str | None = None
    automation: str | None = None
    schedule: str | None = None
    integration: str | None = None
    category: str | None = None
    outcome: str | None = None
    severity: str | None = None
    actor: str | None = None
    trigger: str | None = None
    trace: str | None = None
    q: str | None = None
    include_routine: bool = False

    def as_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["from_at"] = self.from_at.isoformat() if self.from_at else None
        payload["to_at"] = self.to_at.isoformat() if self.to_at else None
        return payload


@dataclass(frozen=True)
class UnifiedCursor:
    occurred_at: datetime
    kind: Literal["trace", "audit"]
    row_id: str


def encode_cursor(cursor: UnifiedCursor) -> str:
    payload = {
        "v": 1,
        "at": cursor.occurred_at.astimezone(UTC).isoformat(),
        "kind": cursor.kind,
        "id": cursor.row_id,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(value: str | None) -> UnifiedCursor | None:
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise ValueError
        occurred_at = datetime.fromisoformat(str(payload["at"]).replace("Z", "+00:00"))
        kind = str(payload["kind"])
        row_id = str(payload["id"]).strip()
        if occurred_at.tzinfo is None or kind not in {"trace", "audit"} or not row_id:
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidCursorError("Activity cursor is invalid or expired.") from exc
    return UnifiedCursor(
        occurred_at=occurred_at.astimezone(UTC),
        kind=kind,  # type: ignore[arg-type]
        row_id=row_id,
    )


def site_zone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def resolve_time_range(
    time_range: str | None,
    *,
    from_at: datetime | None,
    to_at: datetime | None,
    timezone_name: str,
    now: datetime | None = None,
) -> tuple[datetime | None, datetime | None, str]:
    zone = site_zone(timezone_name)
    reference = now or datetime.now(tz=UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    local_now = reference.astimezone(zone)
    range_key = (time_range or "last_24_hours").strip().lower()
    if range_key not in RELATIVE_RANGES:
        raise InvalidTimeRangeError(f"Unsupported time range: {range_key}")

    if from_at is not None or to_at is not None:
        if from_at is None or to_at is None:
            raise InvalidTimeRangeError("Both from and to are required for a custom time range.")
        if from_at.tzinfo is None or to_at.tzinfo is None:
            raise InvalidTimeRangeError("Custom timestamps must include an explicit timezone offset.")
        resolved_from = from_at.astimezone(UTC)
        resolved_to = to_at.astimezone(UTC)
        if resolved_from >= resolved_to:
            raise InvalidTimeRangeError("The start of the time range must be before the end.")
        if resolved_to - resolved_from > timedelta(days=366):
            raise InvalidTimeRangeError("Custom investigation ranges cannot exceed 366 days.")
        return resolved_from, resolved_to, "custom"

    if range_key == "custom":
        raise InvalidTimeRangeError("Both from and to are required for a custom time range.")
    if range_key == "last_24_hours":
        utc_now = reference.astimezone(UTC)
        return utc_now - timedelta(hours=24), utc_now, range_key
    if range_key == "last_7_days":
        return (local_now - timedelta(days=7)).astimezone(UTC), local_now.astimezone(UTC), range_key

    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    if range_key == "today":
        return today_start.astimezone(UTC), local_now.astimezone(UTC), range_key
    yesterday_start = today_start - timedelta(days=1)
    return yesterday_start.astimezone(UTC), today_start.astimezone(UTC), range_key


def cursor_for_item(item: dict[str, Any]) -> UnifiedCursor:
    occurred_at = datetime.fromisoformat(str(item["occurred_at"]).replace("Z", "+00:00"))
    kind = str(item["kind"])
    row_id = str(item["trace_id"] if kind == "trace" else item["audit_id"])
    return UnifiedCursor(
        occurred_at=occurred_at.astimezone(UTC),
        kind=kind,  # type: ignore[arg-type]
        row_id=row_id,
    )
