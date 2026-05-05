from __future__ import annotations

import base64
import copy
import re
import secrets
import uuid
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image, ImageOps, UnidentifiedImageError
from playwright.async_api import async_playwright
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import AccessEvent, Person, Presence, ReportExport, User, Vehicle, VisitorPass
from app.models.enums import AccessDecision, AccessDirection
from app.services.settings import get_runtime_config
from app.services.snapshots import access_event_snapshot_payload, get_snapshot_manager
from app.services.telemetry import TELEMETRY_CATEGORY_ACCESS, actor_from_user, write_audit_log

REPORT_TYPE_PERSON_MOVEMENTS = "person_movements"
REPORT_ID_RE = re.compile(r"^\d{4,12}$")
DATA_URL_RE = re.compile(r"^data:(?P<content_type>[^;,]+)(?P<base64>;base64)?,(?P<data>.*)$", re.DOTALL)
REPORT_ID_LENGTH = 6
PDF_SAFE_PROFILE_IMAGE_MAX_EDGE_PX = 640
CREST_HOUSE_ADDRESS = [
    "157 Rugeley Road,",
    "Burntwood,",
    "WS7 9HA",
]

_TEMPLATE_ROOT = Path(__file__).resolve().parent / "report_templates"
_TEMPLATES = Environment(
    loader=FileSystemLoader(_TEMPLATE_ROOT),
    autoescape=select_autoescape(("html", "xml")),
    trim_blocks=True,
    lstrip_blocks=True,
)


class ReportExportError(Exception):
    """Raised when a report export request cannot be completed."""


async def create_person_movement_report_export(
    session: AsyncSession,
    *,
    person_id: uuid.UUID | None = None,
    visitor_pass_id: uuid.UUID | None = None,
    period_start: datetime,
    period_end: datetime,
    include_denied: bool,
    include_snapshots: bool,
    include_confidence: bool,
    actor: User,
) -> ReportExport:
    start = _ensure_aware(period_start)
    end = _ensure_aware(period_end)
    if end <= start:
        raise ReportExportError("Report end time must be after the start time.")

    config = await get_runtime_config()
    timezone = _timezone(config.site_timezone)
    report_number = await generate_report_number(session)
    options = {
        "include_denied": include_denied,
        "include_snapshots": include_snapshots,
        "include_confidence": include_confidence,
    }
    subject_type = "person"
    subject_id: uuid.UUID | None = person_id
    if visitor_pass_id:
        visitor_pass = await _get_report_visitor_pass(session, visitor_pass_id)
        if not visitor_pass:
            raise ReportExportError("Visitor Pass was not found.")
        subject_type = "visitor_pass"
        subject_id = visitor_pass.id
        snapshot = await build_visitor_pass_movement_report_snapshot(
            session,
            visitor_pass=visitor_pass,
            report_number=report_number,
            period_start=start,
            period_end=end,
            options=options,
            timezone=timezone,
        )
        row_person_id = None
        target_label = f"Visitor pass movement report {report_number}"
    else:
        if not person_id:
            raise ReportExportError("Person or Visitor Pass must be selected.")
        person = await _get_report_person(session, person_id)
        if not person:
            raise ReportExportError("Person was not found.")
        snapshot = await build_person_movement_report_snapshot(
            session,
            person=person,
            report_number=report_number,
            period_start=start,
            period_end=end,
            options=options,
            timezone=timezone,
        )
        row_person_id = person.id
        target_label = f"Person movement report {report_number}"

    relative_pdf_path = _report_pdf_relative_path(report_number)
    pdf_path = _resolve_report_path(relative_pdf_path, must_exist=False)
    pdf_bytes = await render_person_movement_report_pdf(snapshot, pdf_path, timezone=timezone)

    row = ReportExport(
        report_number=report_number,
        report_type=REPORT_TYPE_PERSON_MOVEMENTS,
        person_id=row_person_id,
        period_start=start,
        period_end=end,
        options=options,
        snapshot=snapshot,
        pdf_path=relative_pdf_path,
        pdf_bytes=pdf_bytes,
        created_by_user_id=actor.id,
    )
    session.add(row)
    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_ACCESS,
        action="report.export",
        actor=actor_from_user(actor),
        actor_user_id=actor.id,
        target_entity="ReportExport",
        target_id=report_number,
        target_label=target_label,
        metadata={
            "report_number": report_number,
            "report_type": REPORT_TYPE_PERSON_MOVEMENTS,
            "subject_type": subject_type,
            "subject_id": str(subject_id) if subject_id else None,
            "person_id": str(row_person_id) if row_person_id else None,
            "visitor_pass_id": str(visitor_pass_id) if visitor_pass_id else None,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "pdf_bytes": pdf_bytes,
            "options": options,
        },
    )
    await session.commit()
    await session.refresh(row)
    return row


async def load_report_export(session: AsyncSession, report_number: str) -> ReportExport | None:
    if not REPORT_ID_RE.fullmatch(report_number.strip()):
        return None
    return await session.scalar(
        select(ReportExport).where(ReportExport.report_number == report_number.strip())
    )


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
    for event in public.get("events", []):
        if isinstance(event, dict):
            event.pop("_snapshot_path", None)
            event.pop("_snapshot_content_type", None)
            event.pop("pdf_snapshot_data_url", None)
    return public


def report_pdf_path(row: ReportExport) -> Path:
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
    person: Person,
    report_number: str,
    period_start: datetime,
    period_end: datetime,
    options: dict[str, bool],
    timezone: ZoneInfo,
) -> dict[str, Any]:
    vehicle_ids = [vehicle.id for vehicle in person.vehicles]
    selected_plates = {_normalize_plate(vehicle.registration_number) for vehicle in person.vehicles}
    selected_filter = _selected_event_filter(person.id, vehicle_ids, selected_plates)

    report_events = (
        await session.scalars(
            select(AccessEvent)
            .options(selectinload(AccessEvent.anomalies))
            .where(
                selected_filter,
                AccessEvent.occurred_at >= period_start,
                AccessEvent.occurred_at <= period_end,
            )
            .order_by(AccessEvent.occurred_at.asc())
        )
    ).all()
    if not options["include_denied"]:
        report_events = [event for event in report_events if event.decision != AccessDecision.DENIED]

    selected_history = (
        await session.scalars(
            select(AccessEvent)
            .where(
                selected_filter,
                AccessEvent.occurred_at <= period_end,
                AccessEvent.decision == AccessDecision.GRANTED,
                AccessEvent.direction.in_([AccessDirection.ENTRY, AccessDirection.EXIT]),
            )
            .order_by(AccessEvent.occurred_at.asc())
        )
    ).all()
    all_timeline_events = (
        await session.scalars(
            select(AccessEvent)
            .where(
                AccessEvent.occurred_at >= period_start,
                AccessEvent.occurred_at <= period_end,
                AccessEvent.decision == AccessDecision.GRANTED,
                AccessEvent.direction.in_([AccessDirection.ENTRY, AccessDirection.EXIT]),
            )
            .order_by(AccessEvent.occurred_at.asc())
        )
    ).all()

    duration_lookup = build_duration_lookup(report_events, selected_history, timezone=timezone)
    serialized_events = [
        serialize_report_event(
            event,
            include_snapshots=options["include_snapshots"],
            duration=duration_lookup.get(str(event.id)),
            timezone=timezone,
        )
        for event in sorted(report_events, key=lambda item: item.occurred_at, reverse=True)
    ]
    presence = person.presence or await session.get(Presence, person.id)
    summary = _direction_summary(report_events, timezone)
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
                if _is_movement_event(event)
            ],
        },
    }


async def build_visitor_pass_movement_report_snapshot(
    session: AsyncSession,
    *,
    visitor_pass: VisitorPass,
    report_number: str,
    period_start: datetime,
    period_end: datetime,
    options: dict[str, bool],
    timezone: ZoneInfo,
) -> dict[str, Any]:
    selected_filter = _visitor_pass_event_filter(visitor_pass)
    report_events = (
        await session.scalars(
            select(AccessEvent)
            .options(selectinload(AccessEvent.anomalies))
            .where(
                selected_filter,
                AccessEvent.occurred_at >= period_start,
                AccessEvent.occurred_at <= period_end,
            )
            .order_by(AccessEvent.occurred_at.asc())
        )
    ).all()
    if not options["include_denied"]:
        report_events = [event for event in report_events if event.decision != AccessDecision.DENIED]

    selected_history = (
        await session.scalars(
            select(AccessEvent)
            .where(
                selected_filter,
                AccessEvent.occurred_at <= period_end,
                AccessEvent.decision == AccessDecision.GRANTED,
                AccessEvent.direction.in_([AccessDirection.ENTRY, AccessDirection.EXIT]),
            )
            .order_by(AccessEvent.occurred_at.asc())
        )
    ).all()
    all_timeline_events = (
        await session.scalars(
            select(AccessEvent)
            .where(
                AccessEvent.occurred_at >= period_start,
                AccessEvent.occurred_at <= period_end,
                AccessEvent.decision == AccessDecision.GRANTED,
                AccessEvent.direction.in_([AccessDirection.ENTRY, AccessDirection.EXIT]),
            )
            .order_by(AccessEvent.occurred_at.asc())
        )
    ).all()

    duration_lookup = build_duration_lookup(report_events, selected_history, timezone=timezone)
    serialized_events = [
        serialize_report_event(
            event,
            include_snapshots=options["include_snapshots"],
            duration=duration_lookup.get(str(event.id)),
            timezone=timezone,
        )
        for event in sorted(report_events, key=lambda item: item.occurred_at, reverse=True)
    ]
    summary = _direction_summary(report_events, timezone)
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
                if _is_movement_event(event)
            ],
        },
    }


def build_duration_lookup(
    report_events: list[AccessEvent],
    selected_history: list[AccessEvent],
    *,
    timezone: ZoneInfo | None = None,
) -> dict[str, dict[str, Any]]:
    durations: dict[str, dict[str, Any]] = {}
    history = sorted([event for event in selected_history if _is_movement_event(event)], key=lambda item: item.occurred_at)
    for event in report_events:
        event_id = str(event.id)
        if not _is_movement_event(event):
            durations[event_id] = {"label": "N/A", "tone": "muted"}
            continue

        event_plate = _normalize_plate(event.registration_number)
        before = [item for item in history if item.occurred_at < event.occurred_at]
        if event.direction == AccessDirection.ENTRY:
            vehicle_before = [item for item in before if _normalize_plate(item.registration_number) == event_plate]
            previous_arrival = _last_direction(vehicle_before, AccessDirection.ENTRY)
            previous_departure = _last_direction(vehicle_before, AccessDirection.EXIT)
            if not previous_arrival:
                durations[event_id] = {"label": "New Arrival", "tone": "new"}
            elif previous_departure and previous_departure.occurred_at > previous_arrival.occurred_at:
                durations[event_id] = format_duration_info(
                    previous_departure.occurred_at,
                    event.occurred_at,
                    "Time since this vehicle was last on site",
                    timezone=timezone,
                )
            else:
                durations[event_id] = {"label": "No prior departure", "tone": "muted"}
            continue

        previous_arrival = _last_direction(before, AccessDirection.ENTRY)
        previous_departure = _last_direction(before, AccessDirection.EXIT)
        if not previous_arrival:
            durations[event_id] = {"label": "No arrival found", "tone": "muted"}
        elif previous_departure and previous_departure.occurred_at > previous_arrival.occurred_at:
            durations[event_id] = {"label": "No active visit", "tone": "muted"}
        else:
            durations[event_id] = format_duration_info(
                previous_arrival.occurred_at,
                event.occurred_at,
                "Time on site since last arrival",
                timezone=timezone,
            )
    return durations


def format_duration_info(
    start: datetime,
    end: datetime,
    detail: str,
    *,
    timezone: ZoneInfo | None = None,
) -> dict[str, Any]:
    total_minutes = max(0, int((end - start).total_seconds() // 60))
    total_hours = total_minutes // 60
    total_days = total_hours // 24
    minutes = total_minutes % 60
    hours = total_hours % 24
    if total_hours < 24:
        return {"label": f"{total_hours}hr{'s' if total_hours != 1 else ''} {minutes}m" if total_hours else f"{minutes}m"}
    if total_days < 14:
        return {"label": f"{_plural(total_days, 'Day')}, {hours}hr{'s' if hours != 1 else ''} {minutes}m"}
    return {
        "label": _format_reference_date(start, timezone or _timezone(settings.site_timezone)),
        "tooltip": _verbose_duration(total_minutes),
        "tooltipDetail": detail,
    }


def serialize_report_person(person: Person) -> dict[str, Any]:
    return {
        "id": str(person.id),
        "first_name": person.first_name,
        "last_name": person.last_name,
        "display_name": person.display_name,
        "pronouns": person.pronouns,
        "profile_photo_data_url": person.profile_photo_data_url,
        "group": person.group.name if person.group else None,
        "category": person.group.category.value if person.group else None,
        "vehicles": [
            {
                "id": str(vehicle.id),
                "registration_number": vehicle.registration_number,
                "description": vehicle.description,
                "title": _vehicle_title(vehicle),
                "vehicle_photo_data_url": vehicle.vehicle_photo_data_url,
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
        "group": "Visitor Pass",
        "category": visitor_pass.pass_type.value,
        "vehicles": [
            {
                "id": f"visitor-pass-{visitor_pass.id}",
                "registration_number": plate,
                "description": "Visitor Pass Vehicle",
                "title": vehicle_title,
                "vehicle_photo_data_url": None,
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
    event: AccessEvent,
    *,
    include_snapshots: bool,
    duration: dict[str, Any] | None,
    timezone: ZoneInfo,
) -> dict[str, Any]:
    snapshot = access_event_snapshot_payload(event) if include_snapshots else {}
    visitor_pass = _event_visitor_pass_payload(event)
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
        "anomaly_count": len(event.anomalies),
        "visitor_pass_id": _optional_text(visitor_pass.get("id")),
        "visitor_name": _optional_text(visitor_pass.get("visitor_name")),
        "visitor_pass_mode": _optional_text(visitor_pass.get("mode")),
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


def serialize_timeline_event(event: AccessEvent, *, timezone: ZoneInfo) -> dict[str, Any]:
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


async def render_person_movement_report_pdf(
    snapshot: dict[str, Any],
    output_path: Path,
    *,
    timezone: ZoneInfo,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = _report_for_pdf(snapshot)
    html = _TEMPLATES.get_template("person_movements.html").render(report=report)
    footer = (
        "<div style=\"width:100%;font-family:Inter,Arial,sans-serif;font-size:8px;"
        "color:#6b7890;padding:0 14mm;display:flex;justify-content:space-between;\">"
        "<span>Crest House</span><span>Report "
        f"{snapshot.get('report_id', '')}"
        " &middot; Page <span class=\"pageNumber\"></span> of <span class=\"totalPages\"></span></span></div>"
    )
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = await browser.new_page(
                viewport={"width": 1240, "height": 1754},
                device_scale_factor=1,
            )
            await page.set_content(html, wait_until="networkidle")
            await page.emulate_media(media="print")
            await page.pdf(
                path=str(output_path),
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
                display_header_footer=True,
                header_template="<span></span>",
                footer_template=footer,
                margin={"top": "0", "right": "0", "bottom": "11mm", "left": "0"},
            )
        finally:
            await browser.close()
    return output_path.stat().st_size


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


async def _get_report_person(session: AsyncSession, person_id: uuid.UUID) -> Person | None:
    return await session.scalar(
        select(Person)
        .options(
            selectinload(Person.group),
            selectinload(Person.vehicles),
            selectinload(Person.presence),
        )
        .where(Person.id == person_id)
    )


async def _get_report_visitor_pass(session: AsyncSession, visitor_pass_id: uuid.UUID) -> VisitorPass | None:
    return await session.get(VisitorPass, visitor_pass_id)


def _selected_event_filter(
    person_id: uuid.UUID,
    vehicle_ids: list[uuid.UUID],
    selected_plates: set[str],
) -> Any:
    clauses: list[Any] = [AccessEvent.person_id == person_id]
    if vehicle_ids:
        clauses.append(AccessEvent.vehicle_id.in_(vehicle_ids))
    if selected_plates:
        clauses.append(AccessEvent.registration_number.in_(sorted(selected_plates)))
    return or_(*clauses)


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
        clauses.append(AccessEvent.registration_number == _normalize_plate(plate))
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


def _report_for_pdf(snapshot: dict[str, Any]) -> dict[str, Any]:
    report = copy.deepcopy(snapshot)
    brand = report.setdefault("brand", {})
    if isinstance(brand, dict):
        brand.setdefault("site", "Crest House")
        brand.setdefault("address", CREST_HOUSE_ADDRESS)
    person = report.get("person")
    if isinstance(person, dict):
        person["pdf_profile_photo_data_url"] = _pdf_safe_image_data_url(
            _optional_text(person.get("profile_photo_data_url"))
        )
    for event in report.get("events", []):
        if isinstance(event, dict):
            event["pdf_snapshot_data_url"] = _snapshot_data_url(event)
    return report


def _pdf_safe_image_data_url(value: str | None) -> str | None:
    if not value:
        return None
    match = DATA_URL_RE.fullmatch(value)
    if not match or not match.group("base64"):
        return value
    content_type = match.group("content_type").lower()
    if content_type in {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}:
        return value
    try:
        if content_type in {"image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence"}:
            import pillow_heif

            pillow_heif.register_heif_opener()
        raw = base64.b64decode(match.group("data"), validate=False)
        with Image.open(BytesIO(raw)) as image:
            converted = ImageOps.exif_transpose(image)
            converted.thumbnail(
                (PDF_SAFE_PROFILE_IMAGE_MAX_EDGE_PX, PDF_SAFE_PROFILE_IMAGE_MAX_EDGE_PX),
                Image.Resampling.LANCZOS,
            )
            output = BytesIO()
            has_alpha = converted.mode in {"RGBA", "LA"} or "transparency" in converted.info
            if has_alpha:
                if converted.mode != "RGBA":
                    converted = converted.convert("RGBA")
                converted.save(output, format="PNG", optimize=True)
                output_content_type = "image/png"
            else:
                if converted.mode != "RGB":
                    converted = converted.convert("RGB")
                converted.save(output, format="JPEG", quality=88, optimize=True)
                output_content_type = "image/jpeg"
    except (OSError, UnidentifiedImageError, ValueError):
        return None
    return f"data:{output_content_type};base64,{base64.b64encode(output.getvalue()).decode('ascii')}"


def _snapshot_data_url(event: dict[str, Any]) -> str | None:
    relative_path = event.get("_snapshot_path")
    if not relative_path:
        return None
    try:
        path = get_snapshot_manager().resolve_path(str(relative_path))
    except FileNotFoundError:
        return None
    content_type = str(event.get("_snapshot_content_type") or "image/jpeg")
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None
    return f"data:{content_type};base64,{encoded}"


def _direction_summary(events: list[AccessEvent], timezone: ZoneInfo) -> dict[str, Any]:
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


def _event_visitor_pass_payload(event: AccessEvent) -> dict[str, Any]:
    raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    payload = raw_payload.get("visitor_pass")
    return payload if isinstance(payload, dict) else {}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _last_direction(events: list[AccessEvent], direction: AccessDirection) -> AccessEvent | None:
    for event in reversed(events):
        if event.direction == direction:
            return event
    return None


def _is_movement_event(event: AccessEvent) -> bool:
    return event.decision == AccessDecision.GRANTED and event.direction in {
        AccessDirection.ENTRY,
        AccessDirection.EXIT,
    }


def _normalize_plate(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "", value).upper()


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


def _format_reference_date(value: datetime, timezone: ZoneInfo) -> str:
    local = value.astimezone(timezone)
    return local.strftime("%d/%m/%Y - %H:%M")


def _verbose_duration(total_minutes: int) -> str:
    total_days = max(0, total_minutes // (24 * 60))
    years = total_days // 365
    months = (total_days % 365) // 30
    days = (total_days % 365) % 30
    parts = [
        _plural(years, "Year") if years else None,
        _plural(months, "Month") if months else None,
        _plural(days, "Day") if days else None,
    ]
    return ", ".join(part for part in parts if part) or "Less than 1 Day"


def _plural(value: int, singular: str) -> str:
    return f"{value} {singular}{'' if value == 1 else 's'}"


def _vehicle_title(vehicle: Vehicle) -> str:
    title = " ".join(part for part in [vehicle.make, vehicle.model] if part)
    return title or vehicle.description or "Vehicle"


def _vehicle_is_electric(vehicle: Vehicle) -> bool:
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


def _report_pdf_relative_path(report_number: str) -> str:
    return f"reports/Crest-House-Access-Report-{report_number}.pdf"


def _resolve_report_path(relative_path: str, *, must_exist: bool) -> Path:
    normalized = PurePosixPath(relative_path)
    if normalized.is_absolute() or ".." in normalized.parts or tuple(normalized.parts[:1]) != ("reports",):
        raise FileNotFoundError(relative_path)
    path = settings.data_dir / normalized
    if must_exist and not path.exists():
        raise FileNotFoundError(relative_path)
    return path
