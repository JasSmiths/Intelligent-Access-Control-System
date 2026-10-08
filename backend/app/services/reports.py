from __future__ import annotations

import copy
import re
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import settings
from app.models import AccessEvent, Presence, ReportExport, User, VisitorPass
from app.models.enums import AccessDecision, AccessDirection, UserRole
from app.services.report_contracts import ReportDownload, ReportExportError
from app.services.report_durations import (
    MovementPoint,
    MovementRecord,
    build_duration_lookup,
    is_movement_event,
    normalize_plate,
)
from app.services.report_reads import (
    ReportEvent,
    ReportPerson,
    ReportVehicle,
    load_report_events,
    load_report_person,
    load_report_timeline,
    person_event_filter,
    remaining_activity_rows,
    report_read_transaction,
)
from app.services.settings import get_runtime_config
from app.services.snapshots import access_event_snapshot_payload

REPORT_TYPE_PERSON_MOVEMENTS = "person_movements"
REPORT_ID_RE = re.compile(r"^\d{4,12}$")
REPORT_ID_LENGTH = 6
CREST_HOUSE_ADDRESS = [
    "157 Rugeley Road,",
    "Burntwood,",
    "WS7 9HA",
]

async def report_preview_context() -> dict[str, str]:
    config = await get_runtime_config()
    return {
        "site_timezone": _timezone(config.site_timezone).key,
        "now": datetime.now(tz=UTC).isoformat(),
    }


def resolve_report_boundary(
    value: datetime, *, timezone: ZoneInfo, field: str, fold: int | None = None,
) -> tuple[datetime | None, dict[str, Any] | None]:
    """Resolve report civil input without guessing a repeated or missing hour."""
    if value.tzinfo is not None:
        return value.astimezone(UTC), None
    candidates: dict[int, tuple[datetime, int]] = {}
    for candidate_fold in (0, 1):
        local = value.replace(tzinfo=timezone, fold=candidate_fold)
        instant = local.astimezone(UTC)
        if instant.astimezone(timezone).replace(tzinfo=None) == value:
            utc_offset = local.utcoffset()
            candidates[candidate_fold] = (instant, int(utc_offset.total_seconds() / 60) if utc_offset else 0)
    if not candidates:
        boundary = "Start" if field == "period_start" else "End"
        raise ReportExportError(
            f"{boundary} time does not exist in {timezone.key} because the clocks change. Choose a valid local time."
        )
    instants = {instant for instant, _offset in candidates.values()}
    if len(instants) == 1:
        return next(iter(instants)), None
    if fold in candidates:
        return candidates[fold][0], None
    choices = []
    for candidate_fold, (_instant, offset) in candidates.items():
        sign = "+" if offset >= 0 else "-"
        hours, minutes = divmod(abs(offset), 60)
        occurrence = "First" if candidate_fold == 0 else "Second"
        choices.append({
            "fold": candidate_fold,
            "utc_offset_minutes": offset,
            "label": f"{occurrence} occurrence (UTC{sign}{hours:02}:{minutes:02})",
        })
    return None, {"field": field, "local_time": value.isoformat(), "choices": choices}


async def preview_person_movement_report(
    session: AsyncSession, *, person_id: uuid.UUID | None = None,
    visitor_pass_id: uuid.UUID | None = None, period_start: datetime,
    period_end: datetime, include_denied: bool, include_snapshots: bool,
    include_confidence: bool, period_start_fold: int | None = None,
    period_end_fold: int | None = None,
) -> dict[str, Any]:
    async with report_read_transaction(session) as config:
        timezone = _timezone(config.site_timezone)
        start, start_choice = resolve_report_boundary(
            period_start, timezone=timezone, field="period_start", fold=period_start_fold,
        )
        end, end_choice = resolve_report_boundary(
            period_end, timezone=timezone, field="period_end", fold=period_end_fold,
        )
        choices = [choice for choice in (start_choice, end_choice) if choice is not None]
        if choices:
            return {"status": "time_choice_required", "site_timezone": timezone.key, "time_choices": choices}
        if start is None or end is None or end <= start:
            raise ReportExportError("Report end time must be after the start time.")
        snapshot = await build_movement_report_snapshot(
            session, person_id=person_id, visitor_pass_id=visitor_pass_id,
            report_number=None, period_start=start, period_end=end,
            options={"include_denied": include_denied, "include_snapshots": include_snapshots,
                     "include_confidence": include_confidence},
            timezone=timezone, verify_snapshot_availability=False,
        )
        return {"status": "ready", "complete": True, "report": public_report_snapshot(snapshot)}


async def build_movement_report_snapshot(
    session: AsyncSession, *, person_id: uuid.UUID | None,
    visitor_pass_id: uuid.UUID | None, report_number: str | None,
    period_start: datetime, period_end: datetime, options: dict[str, bool],
    timezone: ZoneInfo, verify_snapshot_availability: bool = True,
    include_profile_source: bool = False,
) -> dict[str, Any]:
    """One subject/history policy for previews and persisted exports."""
    if visitor_pass_id:
        visitor_pass = await _get_report_visitor_pass(session, visitor_pass_id)
        if not visitor_pass:
            raise ReportExportError("Visitor Pass was not found.")
        return await build_visitor_pass_movement_report_snapshot(
            session, visitor_pass=visitor_pass, report_number=report_number,
            period_start=period_start, period_end=period_end, options=options,
            timezone=timezone, verify_snapshot_availability=verify_snapshot_availability,
        )
    if not person_id:
        raise ReportExportError("Person or Visitor Pass must be selected.")
    person = await load_report_person(session, person_id, include_profile_source=include_profile_source)
    if not person:
        raise ReportExportError("Person was not found.")
    return await build_person_movement_report_snapshot(
        session, person=person, report_number=report_number,
        period_start=period_start, period_end=period_end, options=options,
        timezone=timezone, verify_snapshot_availability=verify_snapshot_availability,
    )


def _report_export_visibility(actor: User) -> ColumnElement[bool]:
    return true() if actor.role == UserRole.ADMIN else ReportExport.created_by_user_id == actor.id


async def load_report_export(session: AsyncSession, report_number: str, *, actor: User) -> ReportExport | None:
    if not REPORT_ID_RE.fullmatch(report_number.strip()):
        return None
    return await session.scalar(
        select(ReportExport).where(ReportExport.report_number == report_number.strip(), _report_export_visibility(actor))
    )


async def load_report_download(session: AsyncSession, report_number: str, *, actor: User) -> ReportDownload | None:
    if not REPORT_ID_RE.fullmatch(report_number.strip()):
        return None
    row = (await session.execute(
        select(ReportExport.report_number, ReportExport.pdf_path).where(
            ReportExport.report_number == report_number.strip(), _report_export_visibility(actor),
        )
    )).one_or_none()
    return ReportDownload(row.report_number, row.pdf_path) if row else None


def report_export_payload(row: ReportExport) -> dict[str, Any]:
    return {
        "report_id": row.report_number,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "download_url": f"/api/v1/reports/{row.report_number}/pdf",
        "pdf_bytes": row.pdf_bytes,
        "report": public_report_snapshot(row.snapshot or {}),
    }


def public_report_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    public = copy.deepcopy(snapshot)
    person = public.get("person")
    if isinstance(person, dict):
        person.pop("profile_photo_data_url", None)
        person.pop("pdf_profile_photo_data_url", None)
        for vehicle in person.get("vehicles", []):
            if isinstance(vehicle, dict):
                vehicle.pop("vehicle_photo_data_url", None)
    for event in public.get("events", []):
        if isinstance(event, dict):
            event.pop("_snapshot_path", None)
            event.pop("_snapshot_content_type", None)
            event.pop("pdf_snapshot_data_url", None)
    return public


def report_pdf_path(row: ReportDownload) -> Path:
    return _resolve_report_path(row.pdf_path, must_exist=True)


async def generate_report_number(session: AsyncSession) -> str:
    for _ in range(30):
        report_number = f"{secrets.randbelow(900_000) + 100_000:06d}"
        existing = await session.scalar(
            select(ReportExport.id).where(ReportExport.report_number == report_number)
        )
        if not existing:
            return report_number
    raise ReportExportError("Could not allocate a unique report ID.")


async def build_person_movement_report_snapshot(
    session: AsyncSession,
    *,
    person: ReportPerson,
    report_number: str | None,
    period_start: datetime,
    period_end: datetime,
    options: dict[str, bool],
    timezone: ZoneInfo,
    verify_snapshot_availability: bool = True,
) -> dict[str, Any]:
    selected_filter = person_event_filter(person.id)
    report_events, serialized_events, all_timeline_events, summary = await _collect_movement_report_events(
        session,
        selected_filter=selected_filter,
        period_start=period_start,
        period_end=period_end,
        options=options,
        timezone=timezone,
        verify_snapshot_availability=verify_snapshot_availability,
    )
    presence = person.presence or await session.get(Presence, person.id)
    generated_at = datetime.now(tz=UTC)

    return {
        "report_id": report_number,
        "report_type": REPORT_TYPE_PERSON_MOVEMENTS,
        "subject_type": "person",
        "subject": {"type": "person", "id": str(person.id), "label": person.display_name},
        "generated_at": generated_at.isoformat(),
        "generated_label": _format_generated_at(generated_at, timezone),
        "brand": {
            "system": "Intelligent Access Control",
            "site": "Crest House",
            "address": CREST_HOUSE_ADDRESS,
        },
        "person": serialize_report_person(person),
        "period": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
            "label": f"{_format_period_datetime(period_start, timezone)} to {_format_period_datetime(period_end, timezone)}",
            "start_label": _format_date_only(period_start, timezone),
            "end_label": _format_date_only(period_end, timezone),
            "duration_label": _period_duration_label(period_start, period_end),
            "timezone": timezone.key,
        },
        "presence": {
            "state": presence.state.value if presence else "unknown",
            "last_changed_at": presence.last_changed_at.isoformat() if presence and presence.last_changed_at else None,
        },
        "options": options,
        "summary": summary,
        "events": serialized_events,
        "timeline": {
            "all": [serialize_timeline_event(event, timezone=timezone) for event in all_timeline_events],
            "selected": [
                serialize_timeline_event(event, timezone=timezone)
                for event in report_events
                if is_movement_event(event)
            ],
        },
    }


async def build_visitor_pass_movement_report_snapshot(
    session: AsyncSession,
    *,
    visitor_pass: VisitorPass,
    report_number: str | None,
    period_start: datetime,
    period_end: datetime,
    options: dict[str, bool],
    timezone: ZoneInfo,
    verify_snapshot_availability: bool = True,
) -> dict[str, Any]:
    selected_filter = _visitor_pass_event_filter(visitor_pass)
    report_events, serialized_events, all_timeline_events, summary = await _collect_movement_report_events(
        session,
        selected_filter=selected_filter,
        period_start=period_start,
        period_end=period_end,
        options=options,
        timezone=timezone,
        verify_snapshot_availability=verify_snapshot_availability,
    )
    generated_at = datetime.now(tz=UTC)

    return {
        "report_id": report_number,
        "report_type": REPORT_TYPE_PERSON_MOVEMENTS,
        "subject_type": "visitor_pass",
        "subject": {"type": "visitor_pass", "id": str(visitor_pass.id), "label": visitor_pass.visitor_name},
        "generated_at": generated_at.isoformat(),
        "generated_label": _format_generated_at(generated_at, timezone),
        "brand": {
            "system": "Intelligent Access Control",
            "site": "Crest House",
            "address": CREST_HOUSE_ADDRESS,
        },
        "person": serialize_report_visitor_pass(visitor_pass),
        "period": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
            "label": f"{_format_period_datetime(period_start, timezone)} to {_format_period_datetime(period_end, timezone)}",
            "start_label": _format_date_only(period_start, timezone),
            "end_label": _format_date_only(period_end, timezone),
            "duration_label": _period_duration_label(period_start, period_end),
            "timezone": timezone.key,
        },
        "presence": _visitor_pass_presence_payload(visitor_pass),
        "options": options,
        "summary": summary,
        "events": serialized_events,
        "timeline": {
            "all": [serialize_timeline_event(event, timezone=timezone) for event in all_timeline_events],
            "selected": [
                serialize_timeline_event(event, timezone=timezone)
                for event in report_events
                if is_movement_event(event)
            ],
        },
    }


async def _collect_movement_report_events(
    session: AsyncSession,
    *,
    selected_filter: Any,
    period_start: datetime,
    period_end: datetime,
    options: dict[str, bool],
    timezone: ZoneInfo,
    verify_snapshot_availability: bool = True,
) -> tuple[list[ReportEvent], list[dict[str, Any]], list[MovementPoint], dict[str, Any]]:
    movement_filters = (
        AccessEvent.decision == AccessDecision.GRANTED,
        AccessEvent.direction.in_([AccessDirection.ENTRY, AccessDirection.EXIT]),
    )
    report_events = await load_report_events(
        session,
        selected_filter,
        AccessEvent.occurred_at >= period_start,
        AccessEvent.occurred_at <= period_end,
        include_denied=options["include_denied"],
    )

    # Duration needs only the latest prior arrival/departure for each report
    # plate, plus the latest subject-wide pair for cross-vehicle departures.
    predecessors = await _load_duration_predecessors(
        session, selected_filter=selected_filter, period_start=period_start,
        plates={normalize_plate(event.registration_number) for event in report_events
                if event.direction == AccessDirection.ENTRY and is_movement_event(event)},
    )
    all_timeline_events = await load_report_timeline(
        session,
        AccessEvent.occurred_at >= period_start,
        AccessEvent.occurred_at <= period_end,
        *movement_filters,
        remaining=remaining_activity_rows(report_events),
    )
    duration_lookup = build_duration_lookup(report_events, [*predecessors, *report_events], timezone=timezone)
    serialized_events = [
        serialize_report_event(
            event,
            include_snapshots=options["include_snapshots"],
            duration=duration_lookup.get(str(event.id)),
            timezone=timezone,
            verify_snapshot_availability=verify_snapshot_availability,
        )
        for event in sorted(report_events, key=lambda item: (item.occurred_at, item.id), reverse=True)
    ]
    return report_events, serialized_events, all_timeline_events, _direction_summary(report_events, timezone)


_MOVEMENT_COLUMNS = (
    AccessEvent.id, AccessEvent.registration_number, AccessEvent.direction,
    AccessEvent.decision, AccessEvent.occurred_at,
)


async def _load_duration_predecessors(
    session: AsyncSession, *, selected_filter: Any, period_start: datetime, plates: set[str],
) -> list[MovementPoint]:
    filters = (
        selected_filter, AccessEvent.occurred_at < period_start,
        AccessEvent.decision == AccessDecision.GRANTED,
        AccessEvent.direction.in_([AccessDirection.ENTRY, AccessDirection.EXIT]),
    )
    points: dict[uuid.UUID, MovementPoint] = {}
    # Global predecessors preserve the existing subject-wide departure policy.
    for direction in (AccessDirection.ENTRY, AccessDirection.EXIT):
        row = (await session.execute(
            select(*_MOVEMENT_COLUMNS).where(*filters, AccessEvent.direction == direction)
            .order_by(AccessEvent.occurred_at.desc()).limit(1)
        )).first()
        if row is not None:
            point = MovementPoint(*row)
            points[point.id] = point
    if plates:
        plate = func.upper(func.regexp_replace(AccessEvent.registration_number, "[^a-zA-Z0-9]", "", "g"))
        # PostgreSQL DISTINCT ON returns at most two state rows per report plate.
        query = (select(*_MOVEMENT_COLUMNS)
                 .where(*filters, plate.in_(sorted(plates)))
                 .distinct(plate, AccessEvent.direction)
                 .order_by(plate, AccessEvent.direction, AccessEvent.occurred_at.desc()))
        for row in await session.execute(query):
            point = MovementPoint(*row)
            points[point.id] = point
    return list(points.values())


def serialize_report_person(person: ReportPerson) -> dict[str, Any]:
    return {
        "id": str(person.id),
        "first_name": person.first_name,
        "last_name": person.last_name,
        "display_name": person.display_name,
        "pronouns": person.pronouns,
        "profile_photo_data_url": person.profile_photo_data_url,
        "profile_photo_url": person.profile_photo_url,
        "group": person.group_name,
        "category": person.group_category,
        "vehicles": [
            {
                "id": str(vehicle.id),
                "registration_number": vehicle.registration_number,
                "description": vehicle.description,
                "title": _vehicle_title(vehicle),
                "vehicle_photo_url": vehicle.vehicle_photo_url,
                "make": vehicle.make,
                "model": vehicle.model,
                "color": vehicle.color,
                "fuel_type": vehicle.fuel_type,
                "is_electric": _vehicle_is_electric(vehicle),
                "mot_status": vehicle.mot_status,
                "tax_status": vehicle.tax_status,
                "mot_expiry": vehicle.mot_expiry.isoformat() if vehicle.mot_expiry else None,
                "tax_expiry": vehicle.tax_expiry.isoformat() if vehicle.tax_expiry else None,
                "mot_label": _format_date_value(vehicle.mot_expiry) if vehicle.mot_expiry else "No data",
                "tax_label": _format_date_value(vehicle.tax_expiry) if vehicle.tax_expiry else "No data",
                "mot_tone": _compliance_tone(vehicle.mot_status),
                "tax_tone": _compliance_tone(vehicle.tax_status),
                "last_dvla_lookup_date": vehicle.last_dvla_lookup_date.isoformat() if vehicle.last_dvla_lookup_date else None,
            }
            for vehicle in person.vehicles
        ],
    }


def serialize_report_visitor_pass(visitor_pass: VisitorPass) -> dict[str, Any]:
    plate = _optional_text(visitor_pass.number_plate)
    vehicle_title = _optional_text(visitor_pass.vehicle_make) or "Visitor Vehicle"
    return {
        "id": str(visitor_pass.id),
        "first_name": visitor_pass.visitor_name,
        "last_name": "",
        "display_name": visitor_pass.visitor_name,
        "pronouns": None,
        "profile_photo_data_url": None,
        "profile_photo_url": None,
        "group": "Visitor Pass",
        "category": visitor_pass.pass_type.value,
        "vehicles": [
            {
                "id": f"visitor-pass-{visitor_pass.id}",
                "registration_number": plate,
                "description": "Visitor Pass Vehicle",
                "title": vehicle_title,
                "vehicle_photo_data_url": None,
                "vehicle_photo_url": None,
                "make": visitor_pass.vehicle_make,
                "model": None,
                "color": visitor_pass.vehicle_colour,
                "fuel_type": None,
                "is_electric": False,
                "mot_status": None,
                "tax_status": None,
                "mot_expiry": None,
                "tax_expiry": None,
                "mot_label": "No data",
                "tax_label": "No data",
                "mot_tone": "muted",
                "tax_tone": "muted",
                "last_dvla_lookup_date": None,
            }
        ] if plate else [],
    }


def serialize_report_event(
    event: ReportEvent,
    *,
    include_snapshots: bool,
    duration: dict[str, Any] | None,
    timezone: ZoneInfo,
    verify_snapshot_availability: bool = True,
) -> dict[str, Any]:
    snapshot = access_event_snapshot_payload(event, verify_available=verify_snapshot_availability) if include_snapshots else {}
    return {
        "id": str(event.id),
        "registration_number": event.registration_number,
        "direction": event.direction.value,
        "decision": event.decision.value,
        "confidence": event.confidence,
        "confidence_percent": round(event.confidence * 100),
        "source": event.source,
        "source_label": source_label(event.source),
        "occurred_at": event.occurred_at.isoformat(),
        "occurred_label": _format_period_datetime(event.occurred_at, timezone),
        "type_label": event_label(event.direction, event.decision),
        "detail": "Access granted" if event.decision == AccessDecision.GRANTED else "Access denied",
        "tone": event_tone(event.direction, event.decision),
        "timing_classification": event.timing_classification.value,
        "anomaly_count": event.anomaly_count,
        "visitor_pass_id": _optional_text(event.visitor_pass_id),
        "visitor_name": _optional_text(event.visitor_name),
        "visitor_pass_mode": _optional_text(event.visitor_pass_mode),
        "duration": duration or {"label": "N/A", "tone": "muted"},
        "snapshot_url": snapshot.get("snapshot_url"),
        "snapshot_captured_at": snapshot.get("snapshot_captured_at"),
        "snapshot_bytes": snapshot.get("snapshot_bytes"),
        "snapshot_width": snapshot.get("snapshot_width"),
        "snapshot_height": snapshot.get("snapshot_height"),
        "snapshot_camera": snapshot.get("snapshot_camera"),
        "_snapshot_path": event.snapshot_path if include_snapshots else None,
        "_snapshot_content_type": event.snapshot_content_type if include_snapshots else None,
    }


def serialize_timeline_event(event: MovementRecord, *, timezone: ZoneInfo) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "registration_number": event.registration_number,
        "direction": event.direction.value,
        "decision": event.decision.value,
        "occurred_at": event.occurred_at.isoformat(),
        "label": event_label(event.direction, event.decision),
        "tone": event_tone(event.direction, event.decision),
        "progress": _day_progress(event.occurred_at, timezone),
    }


def source_label(source: str | None) -> str:
    trimmed = (source or "").strip()
    normalized = trimmed.lower()
    if not normalized:
        return "Gate LPR"
    if "ubiquiti" in normalized or "unifi" in normalized or "top gate" in normalized or "gate lpr" in normalized:
        return "Gate LPR"
    return trimmed


def event_label(direction: AccessDirection | str, decision: AccessDecision | str) -> str:
    decision_value = decision.value if isinstance(decision, AccessDecision) else decision
    direction_value = direction.value if isinstance(direction, AccessDirection) else direction
    if decision_value == AccessDecision.DENIED.value:
        return "Denied"
    return "Arrival" if direction_value == AccessDirection.ENTRY.value else "Departure"


def event_tone(direction: AccessDirection | str, decision: AccessDecision | str) -> str:
    decision_value = decision.value if isinstance(decision, AccessDecision) else decision
    direction_value = direction.value if isinstance(direction, AccessDirection) else direction
    if decision_value == AccessDecision.DENIED.value:
        return "red"
    return "green" if direction_value == AccessDirection.ENTRY.value else "blue"


async def _get_report_visitor_pass(session: AsyncSession, visitor_pass_id: uuid.UUID) -> VisitorPass | None:
    return await session.get(VisitorPass, visitor_pass_id)


def _visitor_pass_event_filter(visitor_pass: VisitorPass) -> Any:
    clauses: list[Any] = [
        AccessEvent.raw_payload.contains({"visitor_pass": {"id": str(visitor_pass.id)}})
    ]
    event_ids = [
        event_id
        for event_id in [visitor_pass.arrival_event_id, visitor_pass.departure_event_id]
        if event_id
    ]
    if event_ids:
        clauses.append(AccessEvent.id.in_(event_ids))
    plate = _optional_text(visitor_pass.number_plate)
    if plate:
        clauses.append(AccessEvent.registration_number == normalize_plate(plate))
    return or_(*clauses)


def _visitor_pass_presence_payload(visitor_pass: VisitorPass) -> dict[str, str | None]:
    if visitor_pass.departure_time:
        return {
            "state": "exited",
            "last_changed_at": visitor_pass.departure_time.isoformat(),
        }
    if visitor_pass.arrival_time:
        return {
            "state": "present",
            "last_changed_at": visitor_pass.arrival_time.isoformat(),
        }
    return {
        "state": "unknown",
        "last_changed_at": None,
    }


def _direction_summary(events: list[ReportEvent], timezone: ZoneInfo) -> dict[str, Any]:
    arrivals = [
        event for event in events
        if event.decision == AccessDecision.GRANTED and event.direction == AccessDirection.ENTRY
    ]
    departures = [
        event for event in events
        if event.decision == AccessDecision.GRANTED and event.direction == AccessDirection.EXIT
    ]
    denied = [event for event in events if event.decision == AccessDecision.DENIED]
    latest = max(events, key=lambda event: event.occurred_at, default=None)
    first = min(events, key=lambda event: event.occurred_at, default=None)
    return {
        "arrivals": len(arrivals),
        "departures": len(departures),
        "denied": len(denied),
        "total": len(events),
        "first_event": _format_period_datetime(first.occurred_at, timezone) if first else "None",
        "last_event": (
            f"{event_label(latest.direction, latest.decision)} {_format_period_datetime(latest.occurred_at, timezone)}"
            if latest else "No movement in window"
        ),
    }


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _day_progress(value: datetime, timezone: ZoneInfo) -> float:
    local = value.astimezone(timezone)
    minutes = local.hour * 60 + local.minute + local.second / 60
    return min(99.6, max(0.4, (minutes / (24 * 60)) * 100))


def _period_duration_label(start: datetime, end: datetime) -> str:
    hours = max(1, int(((end - start).total_seconds() + 3599) // 3600))
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = max(1, int((hours + 23) // 24))
    return f"{days} day{'s' if days != 1 else ''}"


def _format_period_datetime(value: datetime, timezone: ZoneInfo) -> str:
    return value.astimezone(timezone).strftime("%d %b at %H:%M")


def _format_generated_at(value: datetime, timezone: ZoneInfo) -> str:
    return value.astimezone(timezone).strftime("%d %b %Y at %H:%M")


def _format_date_only(value: datetime, timezone: ZoneInfo) -> str:
    return value.astimezone(timezone).strftime("%d %b %Y")


def _vehicle_title(vehicle: ReportVehicle) -> str:
    title = " ".join(part for part in [vehicle.make, vehicle.model] if part)
    return title or vehicle.description or "Vehicle"


def _vehicle_is_electric(vehicle: ReportVehicle) -> bool:
    return (vehicle.fuel_type or "").strip().casefold() in {"electric", "electricity", "battery electric"}


def _format_date_value(value: Any) -> str:
    return value.strftime("%d %b %Y") if hasattr(value, "strftime") else str(value)


def _compliance_tone(status: str | None) -> str:
    normalized = (status or "").strip().lower()
    if not normalized:
        return "muted"
    if any(token in normalized for token in ("untaxed", "expired", "invalid", "fail")):
        return "red"
    if normalized in {"valid", "taxed", "sorn"} or "not required" in normalized:
        return "green"
    return "muted"


def _timezone(value: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(value or "Europe/London")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _resolve_report_path(relative_path: str, *, must_exist: bool) -> Path:
    normalized = PurePosixPath(relative_path)
    if normalized.is_absolute() or ".." in normalized.parts or tuple(normalized.parts[:1]) != ("reports",):
        raise FileNotFoundError(relative_path)
    path = settings.data_dir / normalized
    if must_exist and not path.exists():
        raise FileNotFoundError(relative_path)
    return path
