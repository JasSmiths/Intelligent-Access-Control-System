import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Group, Person, User, Vehicle, VehiclePersonAssignment
from app.schemas.directory import CreateVehicleRequest, UpdateVehicleRequest
from app.services.access_devices import get_access_device_service
from app.services.schedule_assignments import set_schedule_assignment
from app.services.schedule_operations import ScheduleOperationError

from .errors import DirectoryOperationError


async def get_group(session: AsyncSession, group_id: uuid.UUID | None) -> Group | None:
    group = await session.get(Group, group_id) if group_id else None
    if group_id and not group:
        raise DirectoryOperationError(status_code=404, detail="Group not found")
    return group


async def set_directory_schedule_assignment(
    session: AsyncSession, target: Person | Vehicle, schedule_id: uuid.UUID | None, user: User
) -> None:
    try:
        await set_schedule_assignment(session, target, schedule_id, user=user, source="api")
    except ScheduleOperationError as exc:
        code = 404 if exc.code == "schedule_not_found" else 403 if exc.code == "forbidden" else 422
        raise DirectoryOperationError(status_code=code, detail=str(exc)) from exc


async def get_vehicles(session: AsyncSession, vehicle_ids: list[uuid.UUID]) -> list[Vehicle]:
    selected_vehicle_ids = list(dict.fromkeys(vehicle_ids))
    if not selected_vehicle_ids:
        return []
    vehicles = (
        await session.scalars(select(Vehicle).where(Vehicle.id.in_(selected_vehicle_ids)))
    ).all()
    if len(vehicles) != len(selected_vehicle_ids):
        raise DirectoryOperationError(status_code=404, detail="One or more vehicles were not found")
    return list(vehicles)


async def get_people(session: AsyncSession, person_ids: list[uuid.UUID]) -> list[Person]:
    selected_person_ids = list(dict.fromkeys(person_ids))
    if not selected_person_ids:
        return []
    people = (await session.scalars(select(Person).where(Person.id.in_(selected_person_ids)))).all()
    people_by_id = {person.id: person for person in people}
    if len(people_by_id) != len(selected_person_ids):
        raise DirectoryOperationError(status_code=404, detail="One or more people were not found")
    return [people_by_id[person_id] for person_id in selected_person_ids]


def requested_vehicle_person_ids(
    request: CreateVehicleRequest | UpdateVehicleRequest,
) -> list[uuid.UUID] | None:
    if "person_ids" in request.model_fields_set:
        return list(request.person_ids or [])
    if "person_id" in request.model_fields_set:
        return [request.person_id] if request.person_id else []
    return None


def derived_vehicle_person_id(person_ids: list[uuid.UUID]) -> uuid.UUID | None:
    return person_ids[0] if len(person_ids) == 1 else None


async def set_vehicle_person_assignments(
    session: AsyncSession,
    vehicle: Vehicle,
    people: list[Person],
) -> None:
    selected_person_ids = {person.id for person in people}
    current_assignments = (
        await session.scalars(
            select(VehiclePersonAssignment).where(VehiclePersonAssignment.vehicle_id == vehicle.id)
        )
    ).all()
    current_person_ids = {assignment.person_id for assignment in current_assignments}

    for assignment in current_assignments:
        if assignment.person_id not in selected_person_ids:
            await session.delete(assignment)
    for person in people:
        if person.id not in current_person_ids:
            session.add(VehiclePersonAssignment(vehicle_id=vehicle.id, person_id=person.id))

    vehicle.person_id = derived_vehicle_person_id([person.id for person in people])


async def recompute_vehicle_person_id(session: AsyncSession, vehicle: Vehicle) -> None:
    person_ids = (
        await session.scalars(
            select(VehiclePersonAssignment.person_id).where(
                VehiclePersonAssignment.vehicle_id == vehicle.id
            )
        )
    ).all()
    vehicle.person_id = derived_vehicle_person_id(person_ids)


async def set_person_vehicle_assignments(
    session: AsyncSession,
    person: Person,
    vehicles: list[Vehicle],
) -> None:
    selected_vehicle_ids = {vehicle.id for vehicle in vehicles}
    current_assignments = (
        await session.scalars(
            select(VehiclePersonAssignment).where(VehiclePersonAssignment.person_id == person.id)
        )
    ).all()
    current_vehicle_ids = {assignment.vehicle_id for assignment in current_assignments}
    affected_vehicle_ids = set(selected_vehicle_ids) | current_vehicle_ids

    for assignment in current_assignments:
        if assignment.vehicle_id not in selected_vehicle_ids:
            await session.delete(assignment)
    for vehicle in vehicles:
        if vehicle.id not in current_vehicle_ids:
            session.add(VehiclePersonAssignment(vehicle_id=vehicle.id, person_id=person.id))

    await session.flush()
    affected_vehicles = (
        await session.scalars(select(Vehicle).where(Vehicle.id.in_(affected_vehicle_ids)))
    ).all()
    for vehicle in affected_vehicles:
        await recompute_vehicle_person_id(session, vehicle)


async def validate_garage_door_entity_ids(entity_ids: list[str]) -> list[str]:
    selected_entity_ids = list(
        dict.fromkeys(entity_id.strip() for entity_id in entity_ids if entity_id.strip())
    )
    if not selected_entity_ids:
        return []

    configured_ids = {
        device.key
        for device in await get_access_device_service().list_devices(
            kind="garage_door", enabled_only=True
        )
    }
    unknown = [entity_id for entity_id in selected_entity_ids if entity_id not in configured_ids]
    if unknown:
        raise DirectoryOperationError(
            status_code=404,
            detail=f"Garage door entity is not configured: {unknown[0]}",
        )
    return selected_entity_ids
