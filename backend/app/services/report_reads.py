"""Bounded report projections; no full event payload, anomaly or vehicle photo load."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime

from sqlalchemy import case, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models import AccessEvent, Anomaly, Group, Person, Presence, Vehicle
from app.models.enums import AccessDecision, AccessDirection, TimingClassification
from app.services.profile_photos import stored_image_url
from app.services.report_contracts import (
    MAX_REPORT_ACTIVITY_ROWS,
    MAX_REPORT_IMAGE_BYTES,
    MAX_REPORT_VEHICLES,
    ReportLimitError,
    ReportTimeoutError,
    ReportWork,
)
from app.services.report_durations import MovementPoint, is_movement_event
from app.services.settings import RuntimeConfig, get_runtime_config_for_session


@dataclass(frozen=True)
class ReportEvent:
    id: uuid.UUID
    registration_number: str
    direction: AccessDirection
    decision: AccessDecision
    confidence: float
    source: str
    occurred_at: datetime
    timing_classification: TimingClassification
    snapshot_path: str | None
    snapshot_content_type: str | None
    snapshot_bytes: int | None
    snapshot_width: int | None
    snapshot_height: int | None
    snapshot_captured_at: datetime | None
    snapshot_camera: str | None
    visitor_pass_id: str | None
    visitor_name: str | None
    visitor_pass_mode: str | None
    anomaly_count: int = 0


@dataclass(frozen=True)
class ReportVehicle:
    id: uuid.UUID
    registration_number: str
    description: str | None
    make: str | None
    model: str | None
    color: str | None
    fuel_type: str | None
    mot_status: str | None
    tax_status: str | None
    mot_expiry: date | None
    tax_expiry: date | None
    last_dvla_lookup_date: date | None
    vehicle_photo_url: str | None


@dataclass(frozen=True)
class ReportPerson:
    id: uuid.UUID
    first_name: str
    last_name: str
    display_name: str
    pronouns: str | None
    profile_photo_data_url: str | None
    updated_at: datetime
    group_name: str | None
    group_category: str | None
    vehicles: list[ReportVehicle]
    presence: Presence | None
    profile_photo_url: str | None = None


def person_event_filter(person_id: uuid.UUID) -> ColumnElement[bool]:
    """Exactly Person.vehicles membership, including inactive owner vehicles."""
    vehicles = select(Vehicle.id).where(Vehicle.person_id == person_id)
    plates = select(func.upper(func.regexp_replace(
        Vehicle.registration_number, "[^a-zA-Z0-9]", "", "g",
    ))).where(Vehicle.person_id == person_id)
    return or_(AccessEvent.person_id == person_id, AccessEvent.vehicle_id.in_(vehicles),
               AccessEvent.registration_number.in_(plates))


def remaining_activity_rows(events: Sequence[ReportEvent]) -> int:
    # Movement rows are emitted again in timeline.selected, not free references.
    used = len(events) + sum(1 for event in events if is_movement_event(event))
    if used > MAX_REPORT_ACTIVITY_ROWS:
        raise ReportLimitError("Report exceeds 10,000 combined activity rows; choose a shorter period.")
    return MAX_REPORT_ACTIVITY_ROWS - used


async def load_report_events(
    session: AsyncSession, *filters: ColumnElement[bool], include_denied: bool,
) -> list[ReportEvent]:
    visitor = AccessEvent.raw_payload["visitor_pass"]
    columns = (
        AccessEvent.id, AccessEvent.registration_number, AccessEvent.direction, AccessEvent.decision,
        AccessEvent.confidence, AccessEvent.source, AccessEvent.occurred_at,
        AccessEvent.timing_classification, AccessEvent.snapshot_path, AccessEvent.snapshot_content_type,
        AccessEvent.snapshot_bytes, AccessEvent.snapshot_width, AccessEvent.snapshot_height,
        AccessEvent.snapshot_captured_at, AccessEvent.snapshot_camera,
        visitor["id"].astext, visitor["visitor_name"].astext, visitor["mode"].astext,
    )
    query = select(*columns).where(*filters)
    if not include_denied:
        query = query.where(AccessEvent.decision != AccessDecision.DENIED)
    rows = (await session.execute(query.order_by(AccessEvent.occurred_at, AccessEvent.id)
                                  .limit(MAX_REPORT_ACTIVITY_ROWS + 1))).all()
    if len(rows) > MAX_REPORT_ACTIVITY_ROWS:
        raise ReportLimitError("Report exceeds 10,000 combined activity rows; choose a shorter period.")
    events = [ReportEvent(*row) for row in rows]
    remaining_activity_rows(events)
    if not events:
        return events
    counts = dict((await session.execute(
        select(Anomaly.event_id, func.count(Anomaly.id))
        .where(Anomaly.event_id.in_([event.id for event in events])).group_by(Anomaly.event_id)
    )).all())
    return [replace(event, anomaly_count=counts.get(event.id, 0)) for event in events]


async def load_report_timeline(
    session: AsyncSession, *filters: ColumnElement[bool], remaining: int,
) -> list[MovementPoint]:
    rows = (await session.execute(select(
        AccessEvent.id, AccessEvent.registration_number, AccessEvent.direction,
        AccessEvent.decision, AccessEvent.occurred_at,
    ).where(*filters).order_by(AccessEvent.occurred_at, AccessEvent.id).limit(remaining + 1))).all()
    if len(rows) > remaining:
        raise ReportLimitError("Report exceeds 10,000 combined activity rows; choose a shorter period.")
    return [MovementPoint(*row) for row in rows]


async def load_report_person(
    session: AsyncSession, person_id: uuid.UUID, *, include_profile_source: bool = False,
) -> ReportPerson | None:
    # Preview needs public photo presence/URL, never embedded profile bytes.
    profile_is_data = func.left(Person.profile_photo_data_url, 5) == "data:"
    photo_value = Person.profile_photo_data_url if include_profile_source else case(
        (profile_is_data, "data:"), else_=Person.profile_photo_data_url,
    )
    photo_size = func.coalesce(func.octet_length(Person.profile_photo_data_url), 0)
    if not include_profile_source:
        photo_size = case((profile_is_data, 0), else_=photo_size)
    row = (await session.execute(select(
        Person.id, Person.first_name, Person.last_name, Person.display_name, Person.pronouns,
        case((photo_size <= MAX_REPORT_IMAGE_BYTES, photo_value), else_=None),
        photo_size, Person.updated_at, Group.name, Group.category,
    ).outerjoin(Group, Person.group_id == Group.id).where(Person.id == person_id))).first()
    if row is None:
        return None
    if row[6] > MAX_REPORT_IMAGE_BYTES:
        raise ReportLimitError("Report image source exceeds 25 MiB; use a smaller profile photo.")
    # First bound cardinality and non-data URL bytes; the next projection cannot
    # accidentally hydrate base64 vehicle images, even if those blobs are huge.
    is_data = func.left(Vehicle.vehicle_photo_data_url, 5) == "data:"
    external_size = case((is_data, 0), else_=func.coalesce(func.octet_length(Vehicle.vehicle_photo_data_url), 0))
    sizes = (await session.execute(select(Vehicle.id, external_size)
                                   .where(Vehicle.person_id == person_id)
                                   .order_by(Vehicle.id).limit(MAX_REPORT_VEHICLES + 1))).all()
    if len(sizes) > MAX_REPORT_VEHICLES:
        raise ReportLimitError("Reports support up to 10,000 vehicles per subject. Choose another subject or contact an administrator.")
    if row[6] + sum(size for _id, size in sizes) > MAX_REPORT_IMAGE_BYTES:
        raise ReportLimitError("Report image sources exceed 25 MiB; use smaller image references.")
    vehicle_rows = (await session.execute(select(
        Vehicle.id, Vehicle.registration_number, Vehicle.description, Vehicle.make, Vehicle.model,
        Vehicle.color, Vehicle.fuel_type, Vehicle.mot_status, Vehicle.tax_status, Vehicle.mot_expiry,
        Vehicle.tax_expiry, Vehicle.last_dvla_lookup_date,
        case((is_data, "data:"), else_=Vehicle.vehicle_photo_data_url), Vehicle.updated_at,
    ).where(Vehicle.person_id == person_id).order_by(Vehicle.id).limit(MAX_REPORT_VEHICLES))).all()
    vehicles = [ReportVehicle(vehicle[0], vehicle[1], vehicle[2], vehicle[3], vehicle[4], vehicle[5],
                              vehicle[6], vehicle[7], vehicle[8], vehicle[9], vehicle[10], vehicle[11], stored_image_url(
        vehicle[12], f"/api/v1/vehicles/{vehicle[0]}/photo", vehicle[13],
    )) for vehicle in vehicle_rows]
    presence = await session.get(Presence, person_id)
    return ReportPerson(row[0], row[1], row[2], row[3], row[4],
                        row[5] if include_profile_source else None, row[7], row[8],
                        row[9].value if row[9] is not None else None, vehicles, presence,
                        profile_photo_url=stored_image_url(row[5], f"/api/v1/people/{person_id}/photo", row[7]))


@asynccontextmanager
async def report_read_transaction(
    session: AsyncSession, work: ReportWork | None = None,
) -> AsyncIterator[RuntimeConfig]:
    """One consistent read snapshot, closed before detached rendering starts.

    Preview's request dependency may have used this session for read-only auth.
    End that earlier read before setting the report's isolation level.
    """
    if session.in_transaction():
        await session.rollback()
    if work is not None:
        work.check()
    async with session.begin():
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        # Connection acquisition has no per-call native deadline. The shielded
        # pipeline owns it until it returns, then checks stop/budget before SQL.
        if work is not None:
            work.check()
        if work is None:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            yield await get_runtime_config_for_session(session)
        else:
            try:
                async with asyncio.timeout(work.remaining()):
                    await session.execute(text("SET TRANSACTION READ ONLY"))
                    await session.execute(select(func.set_config(
                        "statement_timeout", str(work.remaining_milliseconds()), True,
                    )))
                    yield await get_runtime_config_for_session(session)
                    work.check()
            except TimeoutError as exc:
                raise ReportTimeoutError("Report source read exceeded its 120-second limit. Choose a shorter period.") from exc
