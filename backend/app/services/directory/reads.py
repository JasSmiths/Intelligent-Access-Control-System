"""Bounded directory reads shared by HTTP adapters and application consumers."""

import base64
import hashlib
import json
import uuid

from sqlalchemy import Select, String, case, func, literal, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.models import Group, Person, Vehicle, VehiclePersonAssignment
from app.schemas.directory import DirectoryPage, GroupResponse, PersonResponse, VehicleResponse

from .errors import DirectoryOperationError
from .representation import (
    DirectoryPhotoReferences,
    assigned_vehicles_for_person,
    latest_vehicle_snapshot_urls,
    serialize_person,
    serialize_vehicle,
)

PERSON_LOAD = (
    selectinload(Person.group),
    selectinload(Person.schedule),
    selectinload(Person.vehicles).selectinload(Vehicle.schedule),
    selectinload(Person.vehicle_assignments)
    .selectinload(VehiclePersonAssignment.vehicle)
    .selectinload(Vehicle.schedule),
)
VEHICLE_LOAD = (
    selectinload(Vehicle.owner),
    selectinload(Vehicle.schedule),
    selectinload(Vehicle.person_assignments).selectinload(VehiclePersonAssignment.person),
)


def _photo_reference(column: ColumnElement[str | None]) -> ColumnElement[str | None]:
    # Stored data URLs become a small presence marker understood by stored_image_url.
    # Existing external/path references retain their original URL semantics.
    return case((func.left(column, 5) == "data:", literal("data:")), else_=column)


def _scope(kind: str, q: str, active: bool | None, ids: list[uuid.UUID] | None) -> str:
    value = [
        kind,
        q.strip().casefold(),
        active,
        sorted(str(item) for item in ids) if ids is not None else None,
    ]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()[:24]


def encode_cursor(label: str, item_id: uuid.UUID, scope: str) -> str:
    return (
        base64.urlsafe_b64encode(json.dumps([label, str(item_id), scope]).encode())
        .decode()
        .rstrip("=")
    )


def decode_cursor(cursor: str, scope: str) -> tuple[str, uuid.UUID]:
    try:
        label, item_id, captured_scope = json.loads(
            base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        )
        if not isinstance(label, str) or not isinstance(item_id, str) or captured_scope != scope:
            raise ValueError("Cursor does not match this directory query")
        return label, uuid.UUID(item_id)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise DirectoryOperationError(
            status_code=400, detail="Invalid directory cursor for this query"
        ) from exc


def _validate_read(limit: int, ids: list[uuid.UUID] | None) -> None:
    if not 1 <= limit <= 200 or (ids is not None and len(ids) > 200):
        raise DirectoryOperationError(
            status_code=422, detail="Directory reads are limited to 200 records"
        )


def _page_query[Row: tuple[object, ...]](
    query: Select[Row],
    label: ColumnElement[str],
    model: type[Person] | type[Vehicle],
    cursor: str | None,
    scope: str,
    limit: int,
) -> Select[Row]:
    if cursor:
        after_label, after_id = decode_cursor(cursor, scope)
        query = query.where(tuple_(label, model.id) > tuple_(after_label, after_id))
    return query.order_by(label, model.id).limit(limit + 1)


async def list_people(
    session: AsyncSession,
    *,
    q: str = "",
    active: bool | None = None,
    cursor: str | None = None,
    limit: int = 50,
    ids: list[uuid.UUID] | None = None,
    group_id: uuid.UUID | None = None,
    include_media: bool = False,
) -> DirectoryPage[PersonResponse]:
    _validate_read(limit, ids)
    label = func.lower(Person.display_name).label("sort_label")
    query = select(Person, label)
    if not include_media:
        query = query.add_columns(_photo_reference(Person.profile_photo_data_url))
    if group_id is not None:
        query = query.where(Person.group_id == group_id)
    if active is not None:
        query = query.where(Person.is_active == active)
    if ids is not None:
        query = query.where(Person.id.in_(ids))
    if q.strip():
        text = q.strip()
        group_match = or_(
            Group.name.icontains(text, autoescape=True),
            Group.category.cast(String).icontains(text, autoescape=True),
        )
        plate_match = Vehicle.registration_number.icontains(text.replace(" ", ""), autoescape=True)
        query = query.where(
            or_(
                Person.display_name.icontains(text, autoescape=True),
                Person.group.has(group_match),
                Person.vehicles.any(plate_match),
                Person.home_assistant_mobile_app_notify_service.icontains(text, autoescape=True),
                Person.garage_door_entity_ids.cast(String).icontains(text, autoescape=True),
                Person.home_assistant_presence_input_boolean_entity_ids.cast(String).icontains(
                    text, autoescape=True
                ),
                Person.vehicle_assignments.any(VehiclePersonAssignment.vehicle.has(plate_match)),
            )
        )
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    scope = _scope("people", q, active, ids) + str(group_id)
    loaders = list(PERSON_LOAD)
    if not include_media:
        loaders.extend(
            [
                defer(Person.profile_photo_data_url, raiseload=True),
                selectinload(Person.vehicles).defer(Vehicle.vehicle_photo_data_url, raiseload=True),
                selectinload(Person.vehicle_assignments)
                .selectinload(VehiclePersonAssignment.vehicle)
                .defer(Vehicle.vehicle_photo_data_url, raiseload=True),
            ]
        )
    selected_rows = list(
        (
            await session.execute(
                _page_query(
                    query.options(*loaders).execution_options(populate_existing=True),
                    label,
                    Person,
                    cursor,
                    scope,
                    limit,
                )
            )
        ).all()
    )
    rows = [item[0] for item in selected_rows]
    more = len(rows) > limit
    rows = rows[:limit]
    assigned_vehicles = [
        vehicle for person in rows for vehicle in assigned_vehicles_for_person(person)
    ]
    photo_references = None
    if not include_media:
        vehicle_ids = list({vehicle.id for vehicle in assigned_vehicles})
        vehicle_references = (
            (
                await session.execute(
                    select(Vehicle.id, _photo_reference(Vehicle.vehicle_photo_data_url)).where(
                        Vehicle.id.in_(vehicle_ids)
                    )
                )
            ).all()
            if vehicle_ids
            else []
        )
        photo_references = DirectoryPhotoReferences(
            people={str(item[0].id): item[2] for item in selected_rows[:limit]},
            vehicles={str(item_id): reference for item_id, reference in vehicle_references},
        )
    photo_urls = await latest_vehicle_snapshot_urls(session, assigned_vehicles, photo_references)
    return DirectoryPage(
        items=[
            PersonResponse(
                **serialize_person(
                    person,
                    include_media=include_media,
                    fallback_vehicle_photo_urls=photo_urls,
                    photo_references=photo_references,
                )
            )
            for person in rows
        ],
        total=total,
        next_cursor=encode_cursor(selected_rows[limit - 1][1], rows[-1].id, scope)
        if more
        else None,
    )


async def list_vehicles(
    session: AsyncSession,
    *,
    q: str = "",
    active: bool | None = None,
    cursor: str | None = None,
    limit: int = 50,
    ids: list[uuid.UUID] | None = None,
    registrations: list[str] | None = None,
    include_media: bool = False,
) -> DirectoryPage[VehicleResponse]:
    _validate_read(limit, ids)
    if registrations is not None and len(registrations) > 200:
        raise DirectoryOperationError(
            status_code=422, detail="Directory reads are limited to 200 registrations"
        )
    query = select(Vehicle)
    if not include_media:
        query = query.add_columns(_photo_reference(Vehicle.vehicle_photo_data_url))
    if registrations is not None:
        query = query.where(
            Vehicle.registration_number.in_(
                [value.strip().upper().replace(" ", "") for value in registrations]
            )
        )
    if active is not None:
        query = query.where(Vehicle.is_active == active)
    if ids is not None:
        query = query.where(Vehicle.id.in_(ids))
    if q.strip():
        text = q.strip()
        person_match = or_(
            Person.display_name.icontains(text, autoescape=True),
            Person.group.has(
                or_(
                    Group.name.icontains(text, autoescape=True),
                    Group.category.cast(String).icontains(text, autoescape=True),
                )
            ),
        )
        query = query.where(
            or_(
                Vehicle.registration_number.icontains(text.replace(" ", ""), autoescape=True),
                Vehicle.make.icontains(text, autoescape=True),
                Vehicle.model.icontains(text, autoescape=True),
                Vehicle.color.icontains(text, autoescape=True),
                Vehicle.description.icontains(text, autoescape=True),
                Vehicle.owner.has(person_match),
                Vehicle.person_assignments.any(VehiclePersonAssignment.person.has(person_match)),
            )
        )
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    scope = (
        _scope("vehicles", q, active, ids)
        + hashlib.sha256(json.dumps(registrations).encode()).hexdigest()[:12]
    )
    loaders = list(VEHICLE_LOAD)
    if not include_media:
        loaders.extend(
            [
                defer(Vehicle.vehicle_photo_data_url, raiseload=True),
                selectinload(Vehicle.owner).load_only(
                    Person.id, Person.display_name, raiseload=True
                ),
                selectinload(Vehicle.person_assignments)
                .selectinload(VehiclePersonAssignment.person)
                .load_only(Person.id, Person.display_name, raiseload=True),
            ]
        )
    selected_rows = list(
        (
            await session.execute(
                _page_query(
                    query.options(*loaders).execution_options(populate_existing=True),
                    Vehicle.registration_number,
                    Vehicle,
                    cursor,
                    scope,
                    limit,
                )
            )
        ).all()
    )
    rows = [item[0] for item in selected_rows]
    more = len(rows) > limit
    rows = rows[:limit]
    photo_references = (
        DirectoryPhotoReferences(
            vehicles={str(item[0].id): item[1] for item in selected_rows[:limit]}
        )
        if not include_media
        else None
    )
    photo_urls = await latest_vehicle_snapshot_urls(session, rows, photo_references)
    return DirectoryPage(
        items=[
            VehicleResponse(
                **serialize_vehicle(
                    vehicle,
                    include_media=include_media,
                    fallback_photo_urls=photo_urls,
                    photo_references=photo_references,
                )
            )
            for vehicle in rows
        ],
        total=total,
        next_cursor=encode_cursor(rows[-1].registration_number, rows[-1].id, scope)
        if more
        else None,
    )


async def get_person(session: AsyncSession, item_id: uuid.UUID) -> PersonResponse:
    page = await list_people(session, ids=[item_id], limit=1)
    if not page.items:
        raise DirectoryOperationError(status_code=404, detail="Person not found")
    return page.items[0]


async def get_vehicle(session: AsyncSession, item_id: uuid.UUID) -> VehicleResponse:
    page = await list_vehicles(session, ids=[item_id], limit=1)
    if not page.items:
        raise DirectoryOperationError(status_code=404, detail="Vehicle not found")
    return page.items[0]


async def list_groups(session: AsyncSession) -> list[GroupResponse]:
    rows = await session.execute(
        select(Group, func.count(Person.id))
        .outerjoin(Person, Person.group_id == Group.id)
        .group_by(Group.id)
        .order_by(Group.category, Group.name)
    )
    return [
        GroupResponse(
            id=str(group.id),
            name=group.name,
            category=group.category.value,
            subtype=group.subtype,
            description=group.description,
            people_count=count,
        )
        for group, count in rows
    ]


async def get_group_response(session: AsyncSession, item_id: uuid.UUID) -> GroupResponse:
    row = (
        await session.execute(
            select(Group, func.count(Person.id))
            .outerjoin(Person, Person.group_id == Group.id)
            .where(Group.id == item_id)
            .group_by(Group.id)
        )
    ).one_or_none()
    if row is None:
        raise DirectoryOperationError(status_code=404, detail="Group not found")
    group, count = row
    return GroupResponse(
        id=str(group.id),
        name=group.name,
        category=group.category.value,
        subtype=group.subtype,
        description=group.description,
        people_count=count,
    )


async def ensure_exists(
    session: AsyncSession, model: type[Person] | type[Vehicle] | type[Group], item_id: uuid.UUID
) -> None:
    if await session.scalar(select(model.id).where(model.id == item_id)) is None:
        raise DirectoryOperationError(status_code=404, detail=f"{model.__name__} not found")
