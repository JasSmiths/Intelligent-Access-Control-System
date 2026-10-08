"""Scalar chart reads shared by public presentation and winner evaluation."""
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any, TypedDict

from sqlalchemy import case, func, literal, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccessEvent, Person, Vehicle
from app.models.enums import AccessDecision, AccessDirection
from app.services.profile_photos import stored_image_url
from app.services.snapshots import access_event_snapshot_url, snapshot_path_available


class KnownLeaderPerson(TypedDict):
    id: str | None
    first_name: str
    last_name: str
    display_name: str
    profile_photo_data_url: None
    profile_photo_url: str | None


class KnownLeaderVehicle(TypedDict):
    id: str
    registration_number: str
    vehicle_photo_data_url: None
    vehicle_photo_url: str | None
    make: str | None
    model: str | None
    color: str | None
    description: str | None
    display_name: str


class KnownLeader(TypedDict):
    rank: int
    registration_number: str
    read_count: int
    last_seen_at: str | None
    vehicle_id: str
    person_id: str
    first_name: str
    display_name: str
    vehicle_name: str
    person: KnownLeaderPerson
    vehicle: KnownLeaderVehicle


class Snapshot(TypedDict):
    event_id: str
    url: str
    captured_at: str | None
    bytes: int | None
    width: int | None
    height: int | None
    camera: str | None


class PendingInformation(TypedDict):
    status: str
    information: None
    label: str


class UnknownLeader(TypedDict):
    rank: int
    registration_number: str
    read_count: int
    first_seen_at: str | None
    last_seen_at: str | None
    snapshot: Snapshot | None
    vehicle_information: PendingInformation


def _metadata_columns() -> tuple[Any, ...]:
    # CASE preserves external references but never transfers embedded photo bodies.
    return (
        Person.id.label("person_row_id"), Person.first_name, Person.last_name,
        Person.display_name.label("person_display_name"),
        case((func.left(Person.profile_photo_data_url, 5) == "data:", literal("data:")),
             else_=Person.profile_photo_data_url).label("person_photo"),
        Person.updated_at.label("person_updated_at"),
        Vehicle.id.label("vehicle_row_id"),
        Vehicle.registration_number.label("vehicle_registration_number"),
        Vehicle.make, Vehicle.model, Vehicle.color, Vehicle.description,
        case((func.left(Vehicle.vehicle_photo_data_url, 5) == "data:", literal("data:")),
             else_=Vehicle.vehicle_photo_data_url).label("vehicle_photo"),
        Vehicle.updated_at.label("vehicle_updated_at"),
    )


async def known_leaders_in_session(session: AsyncSession, limit: int) -> list[KnownLeader]:
    """Keep full-history ranking; materialize only the selected scalar metadata."""
    _validate_limit(limit)
    aggregate = (
        select(AccessEvent.registration_number.label("registration_number"),
               AccessEvent.vehicle_id.label("vehicle_id"), AccessEvent.person_id.label("person_id"),
               func.count(AccessEvent.id).label("read_count"),
               func.max(AccessEvent.occurred_at).label("last_seen_at"))
        .where(AccessEvent.vehicle_id.is_not(None), AccessEvent.decision == AccessDecision.GRANTED,
               AccessEvent.direction == AccessDirection.ENTRY)
        .group_by(AccessEvent.registration_number, AccessEvent.vehicle_id, AccessEvent.person_id)
        .subquery()
    )
    query = (
        select(aggregate.c.registration_number, aggregate.c.vehicle_id, aggregate.c.person_id,
               aggregate.c.read_count, aggregate.c.last_seen_at, *_metadata_columns())
        .select_from(aggregate)
        .outerjoin(Vehicle, Vehicle.id == aggregate.c.vehicle_id)
        .outerjoin(Person, Person.id == aggregate.c.person_id)
        .order_by(aggregate.c.read_count.desc(), aggregate.c.last_seen_at.desc(),
                  aggregate.c.registration_number.asc()).limit(limit)
    )
    rows = (await session.execute(query)).mappings().all()
    return [_known_row(row, rank=index, registration_number=str(row["registration_number"]),
                       vehicle_id=row["vehicle_id"], person_id=row["person_id"],
                       read_count=int(row["read_count"] or 0), last_seen_at=row["last_seen_at"])
            for index, row in enumerate(rows, start=1)]


async def known_leader_from_state_in_session(
    session: AsyncSession, *, registration_number: str, vehicle_id: uuid.UUID | None,
    person_id: uuid.UUID | None, read_count: int,
) -> KnownLeader:
    """Hydrate the exact prior winner identity without re-ranking or loading entities."""
    seed = select(literal(1).label("one")).subquery()
    row = (await session.execute(
        select(*_metadata_columns()).select_from(seed)
        .outerjoin(Vehicle, Vehicle.id == vehicle_id)
        .outerjoin(Person, Person.id == person_id)
    )).mappings().one()
    return _known_row(row, rank=1, registration_number=registration_number,
                      vehicle_id=vehicle_id, person_id=person_id, read_count=read_count,
                      last_seen_at=None)


def _known_row(
    row: Mapping[str, Any], *, rank: int, registration_number: str,
    vehicle_id: uuid.UUID | None, person_id: uuid.UUID | None, read_count: int,
    last_seen_at: datetime | None,
) -> KnownLeader:
    person_exists = row["person_row_id"] is not None
    vehicle_exists = row["vehicle_row_id"] is not None
    label = registration_number
    if vehicle_exists:
        description = str(row["description"] or "").strip()
        parts = " ".join(str(row[key]).strip() for key in ("color", "make", "model")
                         if str(row[key] or "").strip())
        label = description or parts or row["vehicle_registration_number"] or registration_number
    return {
        "rank": rank, "registration_number": registration_number, "read_count": read_count,
        "last_seen_at": _datetime_iso(last_seen_at), "vehicle_id": _uuid_text(vehicle_id),
        "person_id": _uuid_text(person_id),
        "first_name": row["first_name"] if person_exists else "",
        "display_name": row["person_display_name"] if person_exists else registration_number,
        "vehicle_name": label,
        "person": {
            "id": str(row["person_row_id"]) if person_exists else None,
            "first_name": row["first_name"] if person_exists else "",
            "last_name": row["last_name"] if person_exists else "",
            "display_name": row["person_display_name"] if person_exists else registration_number,
            "profile_photo_data_url": None,
            "profile_photo_url": stored_image_url(row["person_photo"],
                f"/api/v1/people/{row['person_row_id']}/photo", row["person_updated_at"])
                if person_exists else None,
        },
        "vehicle": {
            "id": str(row["vehicle_row_id"]) if vehicle_exists else _uuid_text(vehicle_id),
            "registration_number": row["vehicle_registration_number"] if vehicle_exists else registration_number,
            "vehicle_photo_data_url": None,
            "vehicle_photo_url": stored_image_url(row["vehicle_photo"],
                f"/api/v1/vehicles/{row['vehicle_row_id']}/photo", row["vehicle_updated_at"])
                if vehicle_exists else None,
            "make": row["make"] if vehicle_exists else "",
            "model": row["model"] if vehicle_exists else "",
            "color": row["color"] if vehicle_exists else "",
            "description": row["description"] if vehicle_exists else "",
            "display_name": label,
        },
    }


async def unknown_leaders_in_session(session: AsyncSession, limit: int) -> list[UnknownLeader]:
    """Rank first; inspect snapshot history only for those selected plates."""
    _validate_limit(limit)
    ranked = (
        select(AccessEvent.registration_number.label("registration_number"),
               func.count(AccessEvent.id).label("read_count"),
               func.min(AccessEvent.occurred_at).label("first_seen_at"),
               func.max(AccessEvent.occurred_at).label("last_seen_at"))
        .where(AccessEvent.vehicle_id.is_(None), AccessEvent.decision == AccessDecision.DENIED)
        .group_by(AccessEvent.registration_number)
        .order_by(func.count(AccessEvent.id).desc(), func.max(AccessEvent.occurred_at).desc(),
                  AccessEvent.registration_number.asc()).limit(limit).subquery()
    )
    snapshot = (
        select(AccessEvent.id.label("snapshot_event_id"), AccessEvent.snapshot_captured_at,
               AccessEvent.snapshot_path, AccessEvent.snapshot_bytes, AccessEvent.snapshot_width,
               AccessEvent.snapshot_height, AccessEvent.snapshot_camera)
        .where(AccessEvent.registration_number == ranked.c.registration_number,
               AccessEvent.vehicle_id.is_(None), AccessEvent.decision == AccessDecision.DENIED,
               AccessEvent.snapshot_path.is_not(None))
        .order_by(AccessEvent.occurred_at.desc(), AccessEvent.created_at.desc(), AccessEvent.id.desc())
        .limit(1).lateral()
    )
    rows = (await session.execute(
        select(ranked, snapshot).select_from(ranked).outerjoin(snapshot, true())
        .order_by(ranked.c.read_count.desc(), ranked.c.last_seen_at.desc(),
                  ranked.c.registration_number.asc())
    )).mappings().all()
    return [{
        "rank": index, "registration_number": str(row["registration_number"]),
        "read_count": int(row["read_count"] or 0),
        "first_seen_at": _datetime_iso(row["first_seen_at"]),
        "last_seen_at": _datetime_iso(row["last_seen_at"]),
        "snapshot": snapshot_payload(event_id=row["snapshot_event_id"],
            captured_at=row["snapshot_captured_at"], relative_path=row["snapshot_path"],
            byte_count=row["snapshot_bytes"], width=row["snapshot_width"],
            height=row["snapshot_height"], camera=row["snapshot_camera"]),
        "vehicle_information": {"status": "pending", "information": None, "label": ""},
    } for index, row in enumerate(rows, start=1)]


def snapshot_payload(
    *, event_id: uuid.UUID | str | None, captured_at: datetime | None,
    relative_path: str | None, byte_count: int | None, width: int | None,
    height: int | None, camera: str | None,
) -> Snapshot | None:
    try:
        snapshot_event_id = uuid.UUID(str(event_id)) if event_id else None
    except ValueError:
        return None
    if not snapshot_event_id or not snapshot_path_available(relative_path):
        return None
    return {"event_id": str(event_id), "url": access_event_snapshot_url(snapshot_event_id),
            "captured_at": _datetime_iso(captured_at), "bytes": byte_count, "width": width,
            "height": height, "camera": camera}


def _datetime_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _uuid_text(value: uuid.UUID | str | None) -> str:
    return str(value) if value else ""


def _validate_limit(limit: int) -> None:
    if not 1 <= limit <= 100:
        raise ValueError("Chart reads support 1 to 100 ranked identities")
