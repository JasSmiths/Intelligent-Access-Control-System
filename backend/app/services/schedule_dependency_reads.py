"""Bounded, photo-free schedule assignment reads for display and deletion."""
import uuid
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import String, cast, literal, or_, select, tuple_, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccessDevice, Person, Vehicle


class _Assignment(BaseModel):
    id: str
    name: str
    kind: str


class _VehicleAssignment(_Assignment):
    registration_number: str
    owner: str | None


class _DoorAssignment(_Assignment):
    entity_id: str


class ScheduleDependencyPage(BaseModel):
    people: list[_Assignment] = Field(default_factory=list)
    vehicles: list[_VehicleAssignment] = Field(default_factory=list)
    doors: list[_DoorAssignment] = Field(default_factory=list)
    next_cursor: str | None = None


class _Cursor(BaseModel):
    schedule: uuid.UUID
    group: Literal[0, 1, 2]
    position: int
    name: str
    row_id: uuid.UUID


async def has_schedule_assignments(session: AsyncSession, schedule_id: uuid.UUID) -> bool:
    return bool(await session.scalar(select(or_(
        select(Person.id).where(Person.schedule_id == schedule_id).exists(),
        select(Vehicle.id).where(Vehicle.schedule_id == schedule_id).exists(),
        select(AccessDevice.id).where(AccessDevice.schedule_id == schedule_id).exists(),
    ))))


async def schedule_dependency_page(
    session: AsyncSession, schedule_id: uuid.UUID, cursor: str | None, limit: int = 25,
) -> ScheduleDependencyPage:
    after = _Cursor.model_validate_json(cursor) if cursor else None
    if after and after.schedule != schedule_id:
        raise ValueError("Assignment cursor belongs to a different schedule")
    people = select(literal(0).label("group_no"), literal(0).label("position"), Person.display_name.label("name"),
                    Person.id.label("row_id"), literal("person").label("kind"),
                    cast(Person.id, String).label("public_id"), literal(None, String).label("owner")).where(
                        Person.schedule_id == schedule_id)
    vehicles = select(literal(1), literal(0), Vehicle.registration_number, Vehicle.id, literal("vehicle"),
                      cast(Vehicle.id, String), Person.display_name).select_from(Vehicle).outerjoin(
                          Person, Vehicle.person_id == Person.id).where(Vehicle.schedule_id == schedule_id)
    doors = select(literal(2), AccessDevice.sort_order, AccessDevice.name, AccessDevice.id,
                   AccessDevice.kind, AccessDevice.key, literal(None, String)).where(AccessDevice.schedule_id == schedule_id)
    rows = union_all(people, vehicles, doors).subquery()
    order = (rows.c.group_no, rows.c.position, rows.c.name, rows.c.row_id)
    query = select(rows).order_by(*order).limit(limit + 1)
    if after:
        query = query.where(tuple_(*order) > tuple_(after.group, after.position, after.name, after.row_id))
    records = list((await session.execute(query)).mappings())
    page = ScheduleDependencyPage()
    for row in records[:limit]:
        item = {"id": row["public_id"], "name": row["name"], "kind": row["kind"]}
        if row["group_no"] == 0:
            page.people.append(_Assignment(**item))
        elif row["group_no"] == 1:
            page.vehicles.append(_VehicleAssignment(**item, registration_number=row["name"], owner=row["owner"]))
        else:
            page.doors.append(_DoorAssignment(**item, entity_id=row["public_id"]))
    if len(records) > limit:
        last = records[limit - 1]
        page.next_cursor = _Cursor(schedule=schedule_id, group=last["group_no"], position=last["position"],
                                   name=last["name"], row_id=last["row_id"]).model_dump_json()
    return page
