"""Supported full-refresh shape; undecodable rows cannot become absence."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.modules.icloud_calendar.contracts import (
    CalendarCoverage,
    CalendarSnapshot,
    ICloudCalendarEvent,
    ICloudCalendarIncomplete,
)

_START = ("startDate", "start_date", "localStartDate", "local_start_date", "start", "starts_at")
_END = ("endDate", "end_date", "localEndDate", "local_end_date", "end", "ends_at")
_NOTES = ("notes", "note", "privateComments", "private_comments", "comments")


def normalize_snapshot(value: object, coverage: CalendarCoverage) -> CalendarSnapshot:
    if not isinstance(value, Mapping) or set(value) - {"Event", "Collection", "Invitee"}:
        raise _incomplete()
    rows = value.get("Event")
    if not isinstance(rows, list):
        raise _incomplete()
    for field, identities in (("Collection", ("guid",)), ("Invitee", ("guid", "pGuid"))):
        if field not in value:
            continue
        metadata = value[field]
        if not isinstance(metadata, list) or any(
            not isinstance(row, Mapping)
            or any(not isinstance(row.get(key), str) or not row[key].strip() for key in identities)
            for row in metadata
        ):
            raise _incomplete()
    events: list[ICloudCalendarEvent] = []
    excluded = 0
    for row in rows:
        event = normalize_event(row)
        if event is None:
            excluded += 1
        else:
            events.append(event)
    return CalendarSnapshot(coverage, tuple(events), excluded)


def normalize_event(value: object) -> ICloudCalendarEvent | None:
    if not isinstance(value, Mapping):
        raise _incomplete()
    title = _text(value, "title", "summary", "name")
    event_id = _text(value, "guid", "id", "event_id", "eventId")
    calendar_id = _text(value, "pguid", "pGuid", "calendar_id", "calendarId", "calendarGuid")
    timezone = _timezone(value)
    starts_at, ends_at = _datetime(_value(value, *_START), timezone), _datetime(_value(value, *_END), timezone)
    if not title or not event_id or starts_at is None or ends_at is None or ends_at <= starts_at:
        raise _incomplete()
    all_day = _value(value, "allDay", "all_day", "is_all_day")
    if all_day is not None and type(all_day) is not bool:
        raise _incomplete()
    description, notes = _text(value, "description", "desc"), _text(value, *_NOTES)
    if all_day:
        return None
    return ICloudCalendarEvent(calendar_id or "calendar", event_id, title, starts_at,
                               ends_at, description, notes)


def _value(value: Mapping[object, object], *names: str) -> object:
    for name in names:
        if name in value:
            return value[name]
    return None


def _text(value: Mapping[object, object], *names: str) -> str:
    result = _value(value, *names)
    if result is None:
        return ""
    if not isinstance(result, str):
        raise _incomplete()
    return " ".join(result.strip().split())


def _timezone(value: Mapping[object, object]) -> ZoneInfo | None:
    for names in (("tz", "timezone", "timeZone", "time_zone"), ("tzname", "time_zone_name")):
        candidate = _text(value, *names)
        if candidate:
            try:
                return ZoneInfo(candidate)
            except (ValueError, ZoneInfoNotFoundError) as exc:
                raise _incomplete() from exc
    return None


def _datetime(value: object, timezone: ZoneInfo | None) -> datetime | None:
    try:
        if isinstance(value, datetime):
            return value.replace(tzinfo=timezone or UTC) if value.tzinfo is None else value.astimezone(UTC)
        if isinstance(value, (list, tuple)) and len(value) >= 6:
            if any(not isinstance(part, (int, str)) or isinstance(part, bool) for part in value[1:6]):
                return None
            year, month, day, hour, minute = (int(part) for part in value[1:6])
            return datetime(year, month, day, hour, minute, tzinfo=timezone or UTC)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            seconds = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(seconds, tz=UTC)
        if isinstance(value, str) and value.strip():
            parsed = datetime.fromisoformat(value.strip())
            return parsed.replace(tzinfo=timezone or UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return None


def _incomplete() -> ICloudCalendarIncomplete:
    return ICloudCalendarIncomplete(
        "iCloud returned an unsupported or incomplete calendar response. "
        "No visitor passes were changed for this account."
    )
