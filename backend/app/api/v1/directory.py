import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import current_user
from app.db.session import AsyncSessionLocal, get_db_session
from app.models import Group, Person, Schedule, User, Vehicle, VehiclePersonAssignment
from app.models.enums import GroupCategory
from app.modules.dvla.vehicle_enquiry import DvlaVehicleEnquiryError, friendly_vehicle_text
from app.services.dvla import NormalizedDvlaVehicle, lookup_normalized_vehicle_registration
from app.services.profile_photos import ProfilePhotoError, normalize_profile_photo_data_url
from app.services.settings import get_runtime_config
from app.services.telemetry import (
    TELEMETRY_CATEGORY_CRUD,
    actor_from_user,
    audit_diff,
    write_audit_log,
)

router = APIRouter()


class PersonVehicleResponse(BaseModel):
    id: str
    registration_number: str
    description: str | None
    vehicle_photo_data_url: str | None
    make: str | None
    model: str | None
    color: str | None
    fuel_type: str | None = None
    mot_status: str | None = None
    tax_status: str | None = None
    mot_expiry: date | None = None
    tax_expiry: date | None = None
    last_dvla_lookup_date: date | None = None
    schedule_id: str | None = None
    schedule: str | None = None


class PersonResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    display_name: str
    pronouns: str | None
    profile_photo_data_url: str | None
    group_id: str | None
    group: str | None
    category: str | None
    schedule_id: str | None
    schedule: str | None
    is_active: bool
    notes: str | None
    garage_door_entity_ids: list[str]
    home_assistant_mobile_app_notify_service: str | None
    vehicles: list[PersonVehicleResponse]


class CreatePersonRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    pronouns: str | None = Field(default=None, max_length=24)
    profile_photo_data_url: str | None = Field(default=None, max_length=11_200_000)
    group_id: uuid.UUID | None = None
    schedule_id: uuid.UUID | None = None
    vehicle_ids: list[uuid.UUID] = Field(default_factory=list)
    garage_door_entity_ids: list[str] = Field(default_factory=list)
    home_assistant_mobile_app_notify_service: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool = True


class UpdatePersonRequest(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    pronouns: str | None = Field(default=None, max_length=24)
    profile_photo_data_url: str | None = Field(default=None, max_length=11_200_000)
    group_id: uuid.UUID | None = None
    schedule_id: uuid.UUID | None = None
    vehicle_ids: list[uuid.UUID] | None = None
    garage_door_entity_ids: list[str] | None = None
    home_assistant_mobile_app_notify_service: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None


class VehicleResponse(BaseModel):
    id: str
    registration_number: str
    vehicle_photo_data_url: str | None
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
    person_id: str | None
    owner: str | None
    person_ids: list[str]
    owners: list[str]
    schedule_id: str | None
    schedule: str | None
    is_active: bool


class CreateVehicleRequest(BaseModel):
    registration_number: str = Field(min_length=1, max_length=32)
    vehicle_photo_data_url: str | None = Field(default=None, max_length=11_200_000)
    make: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=120)
    color: str | None = Field(default=None, max_length=80)
    fuel_type: str | None = Field(default=None, max_length=80)
    mot_status: str | None = Field(default=None, max_length=80)
    tax_status: str | None = Field(default=None, max_length=80)
    mot_expiry: date | None = None
    tax_expiry: date | None = None
    last_dvla_lookup_date: date | None = None
    description: str | None = Field(default=None, max_length=255)
    person_id: uuid.UUID | None = None
    person_ids: list[uuid.UUID] | None = None
    schedule_id: uuid.UUID | None = None
    is_active: bool = True


class UpdateVehicleRequest(BaseModel):
    registration_number: str | None = Field(default=None, min_length=1, max_length=32)
    vehicle_photo_data_url: str | None = Field(default=None, max_length=11_200_000)
    make: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=120)
    color: str | None = Field(default=None, max_length=80)
    fuel_type: str | None = Field(default=None, max_length=80)
    mot_status: str | None = Field(default=None, max_length=80)
    tax_status: str | None = Field(default=None, max_length=80)
    mot_expiry: date | None = None
    tax_expiry: date | None = None
    last_dvla_lookup_date: date | None = None
    description: str | None = Field(default=None, max_length=255)
    person_id: uuid.UUID | None = None
    person_ids: list[uuid.UUID] | None = None
    schedule_id: uuid.UUID | None = None
    is_active: bool | None = None


class GroupResponse(BaseModel):
    id: str
    name: str
    category: str
    subtype: str | None
    description: str | None
    people_count: int


class CreateGroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: GroupCategory
    subtype: str | None = Field(default=None, max_length=120)
    description: str | None = None


class UpdateGroupRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: GroupCategory | None = None
    subtype: str | None = Field(default=None, max_length=120)
    description: str | None = None


def compose_person_name(first_name: str, last_name: str) -> str:
    return f"{first_name.strip()} {last_name.strip()}".strip()


def serialize_group(group: Group) -> dict:
    return {
        "id": str(group.id),
        "name": group.name,
        "category": group.category.value,
        "subtype": group.subtype,
        "description": group.description,
        "people_count": len(group.people),
    }


def normalize_registration_number(registration_number: str) -> str:
    return registration_number.strip().upper().replace(" ", "")


def normalize_vehicle_text(value: str | None) -> str | None:
    return friendly_vehicle_text(value) if value else None


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def local_today(timezone_name: str | None) -> date:
    try:
        timezone = ZoneInfo(timezone_name or "Europe/London")
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("UTC")
    return datetime.now(tz=timezone).date()


def apply_dvla_vehicle_details(
    vehicle: Vehicle,
    normalized: NormalizedDvlaVehicle,
    *,
    lookup_date: date,
) -> None:
    if normalized.make:
        vehicle.make = normalize_vehicle_text(normalized.make)
    if normalized.colour:
        vehicle.color = normalize_vehicle_text(normalized.colour)
    vehicle.mot_status = normalize_vehicle_text(normalized.mot_status)
    vehicle.tax_status = normalize_vehicle_text(normalized.tax_status)
    vehicle.fuel_type = normalize_vehicle_text(normalized.fuel_type)
    vehicle.mot_expiry = normalized.mot_expiry
    vehicle.tax_expiry = normalized.tax_expiry
    vehicle.last_dvla_lookup_date = lookup_date


def normalize_home_assistant_mobile_notify_service(service_name: str | None) -> str | None:
    service_name = normalize_optional_text(service_name)
    if service_name and not service_name.startswith("notify.mobile_app_"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Home Assistant mobile notification target must be a notify.mobile_app_* service.",
        )
    return service_name


def normalize_person_pronouns(pronouns: str | None) -> str | None:
    pronouns = normalize_optional_text(pronouns)
    if pronouns is None:
        return None
    normalized = pronouns.casefold()
    if normalized not in {"he/him", "she/her"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Person pronouns must be he/him or she/her.",
        )
    return normalized


def normalize_profile_photo_or_400(profile_photo_data_url: str | None) -> str | None:
    try:
        return normalize_profile_photo_data_url(profile_photo_data_url)
    except ProfilePhotoError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Profile photo could not be processed.",
        ) from exc


def assigned_people_for_vehicle(vehicle: Vehicle) -> list[Person]:
    people = [
        assignment.person
        for assignment in getattr(vehicle, "person_assignments", []) or []
        if assignment.person
    ]
    if people:
        return sorted(people, key=lambda person: person.display_name)
    return [vehicle.owner] if vehicle.owner else []


def assigned_vehicles_for_person(person: Person) -> list[Vehicle]:
    vehicles = [
        assignment.vehicle
        for assignment in getattr(person, "vehicle_assignments", []) or []
        if assignment.vehicle
    ]
    if vehicles:
        return sorted(vehicles, key=lambda vehicle: vehicle.registration_number)
    return list(person.vehicles or [])


def serialize_vehicle(vehicle: Vehicle) -> dict:
    assigned_people = assigned_people_for_vehicle(vehicle)
    owners = [person.display_name for person in assigned_people]
    return {
        "id": str(vehicle.id),
        "registration_number": vehicle.registration_number,
        "vehicle_photo_data_url": vehicle.vehicle_photo_data_url,
        "description": vehicle.description,
        "make": vehicle.make,
        "model": vehicle.model,
        "color": vehicle.color,
        "fuel_type": vehicle.fuel_type,
        "mot_status": vehicle.mot_status,
        "tax_status": vehicle.tax_status,
        "mot_expiry": vehicle.mot_expiry,
        "tax_expiry": vehicle.tax_expiry,
        "last_dvla_lookup_date": vehicle.last_dvla_lookup_date,
        "person_id": str(vehicle.person_id) if vehicle.person_id else None,
        "owner": vehicle.owner.display_name if vehicle.owner else (owners[0] if len(owners) == 1 else None),
        "person_ids": [str(person.id) for person in assigned_people],
        "owners": owners,
        "schedule_id": str(vehicle.schedule_id) if vehicle.schedule_id else None,
        "schedule": vehicle.schedule.name if vehicle.schedule else None,
        "is_active": vehicle.is_active,
    }


def serialize_person(person: Person) -> dict:
    assigned_vehicles = assigned_vehicles_for_person(person)
    return {
        "id": str(person.id),
        "first_name": person.first_name,
        "last_name": person.last_name,
        "display_name": person.display_name,
        "pronouns": person.pronouns,
        "profile_photo_data_url": person.profile_photo_data_url,
        "group_id": str(person.group_id) if person.group_id else None,
        "group": person.group.name if person.group else None,
        "category": person.group.category.value if person.group else None,
        "schedule_id": str(person.schedule_id) if person.schedule_id else None,
        "schedule": person.schedule.name if person.schedule else None,
        "is_active": person.is_active,
        "notes": person.notes,
        "garage_door_entity_ids": list(person.garage_door_entity_ids or []),
        "home_assistant_mobile_app_notify_service": person.home_assistant_mobile_app_notify_service,
        "vehicles": [
                {
                    "id": str(vehicle.id),
                    "registration_number": vehicle.registration_number,
                    "description": vehicle.description,
                    "vehicle_photo_data_url": vehicle.vehicle_photo_data_url,
                    "make": vehicle.make,
                    "model": vehicle.model,
                    "color": vehicle.color,
                    "fuel_type": vehicle.fuel_type,
                    "mot_status": vehicle.mot_status,
                    "tax_status": vehicle.tax_status,
                    "mot_expiry": vehicle.mot_expiry,
                    "tax_expiry": vehicle.tax_expiry,
                    "last_dvla_lookup_date": vehicle.last_dvla_lookup_date,
                    "schedule_id": str(vehicle.schedule_id) if vehicle.schedule_id else None,
                    "schedule": vehicle.schedule.name if vehicle.schedule else None,
                }
                for vehicle in assigned_vehicles
            ],
    }


def person_audit_snapshot(person: Person) -> dict:
    return {
        "id": str(person.id),
        "first_name": person.first_name,
        "last_name": person.last_name,
        "display_name": person.display_name,
        "pronouns": person.pronouns,
        "group_id": str(person.group_id) if person.group_id else None,
        "schedule_id": str(person.schedule_id) if person.schedule_id else None,
        "garage_door_entity_ids": list(person.garage_door_entity_ids or []),
        "home_assistant_mobile_app_notify_service": person.home_assistant_mobile_app_notify_service,
        "notes": person.notes,
        "is_active": person.is_active,
    }


def vehicle_audit_snapshot(vehicle: Vehicle) -> dict:
    return {
        "id": str(vehicle.id),
        "registration_number": vehicle.registration_number,
        "person_id": str(vehicle.person_id) if vehicle.person_id else None,
        "schedule_id": str(vehicle.schedule_id) if vehicle.schedule_id else None,
        "make": vehicle.make,
        "model": vehicle.model,
        "color": vehicle.color,
        "fuel_type": vehicle.fuel_type,
        "mot_status": vehicle.mot_status,
        "tax_status": vehicle.tax_status,
        "mot_expiry": vehicle.mot_expiry.isoformat() if vehicle.mot_expiry else None,
        "tax_expiry": vehicle.tax_expiry.isoformat() if vehicle.tax_expiry else None,
        "last_dvla_lookup_date": vehicle.last_dvla_lookup_date.isoformat() if vehicle.last_dvla_lookup_date else None,
        "description": vehicle.description,
        "is_active": vehicle.is_active,
    }


def group_audit_snapshot(group: Group) -> dict:
    return {
        "id": str(group.id),
        "name": group.name,
        "category": group.category.value,
        "subtype": group.subtype,
        "description": group.description,
    }


@router.get("/people")
async def list_people() -> list[PersonResponse]:
    async with AsyncSessionLocal() as session:
        people = (
            await session.scalars(
                select(Person)
                .options(
                    selectinload(Person.group),
                    selectinload(Person.schedule),
                    selectinload(Person.vehicles).selectinload(Vehicle.schedule),
                    selectinload(Person.vehicle_assignments)
                    .selectinload(VehiclePersonAssignment.vehicle)
                    .selectinload(Vehicle.schedule),
                )
                .order_by(Person.display_name)
            )
        ).all()

    return [PersonResponse(**serialize_person(person)) for person in people]


async def get_group_or_404(session: AsyncSession, group_id: uuid.UUID | None) -> Group | None:
    group = await session.get(Group, group_id) if group_id else None
    if group_id and not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group


async def get_schedule_or_404(session: AsyncSession, schedule_id: uuid.UUID | None) -> Schedule | None:
    schedule = await session.get(Schedule, schedule_id) if schedule_id else None
    if schedule_id and not schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    return schedule


async def get_vehicles_or_404(session: AsyncSession, vehicle_ids: list[uuid.UUID]) -> list[Vehicle]:
    selected_vehicle_ids = list(dict.fromkeys(vehicle_ids))
    if not selected_vehicle_ids:
        return []
    vehicles = (
        await session.scalars(select(Vehicle).where(Vehicle.id.in_(selected_vehicle_ids)))
    ).all()
    if len(vehicles) != len(selected_vehicle_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="One or more vehicles were not found")
    return list(vehicles)


async def get_people_or_404(session: AsyncSession, person_ids: list[uuid.UUID]) -> list[Person]:
    selected_person_ids = list(dict.fromkeys(person_ids))
    if not selected_person_ids:
        return []
    people = (
        await session.scalars(select(Person).where(Person.id.in_(selected_person_ids)))
    ).all()
    people_by_id = {person.id: person for person in people}
    if len(people_by_id) != len(selected_person_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="One or more people were not found")
    return [people_by_id[person_id] for person_id in selected_person_ids]


def requested_vehicle_person_ids(request: CreateVehicleRequest | UpdateVehicleRequest) -> list[uuid.UUID] | None:
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
    selected_entity_ids = list(dict.fromkeys(entity_id.strip() for entity_id in entity_ids if entity_id.strip()))
    if not selected_entity_ids:
        return []

    config = await get_runtime_config()
    configured_ids = {
        str(entity["entity_id"])
        for entity in config.home_assistant_garage_door_entities
    }
    unknown = [entity_id for entity_id in selected_entity_ids if entity_id not in configured_ids]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Garage door entity is not configured: {unknown[0]}",
        )
    return selected_entity_ids


@router.post("/people", response_model=PersonResponse, status_code=status.HTTP_201_CREATED)
async def add_person(
    request: CreatePersonRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> PersonResponse:
    group = await get_group_or_404(session, request.group_id)
    schedule = await get_schedule_or_404(session, request.schedule_id)
    vehicles = await get_vehicles_or_404(session, request.vehicle_ids)
    garage_door_entity_ids = await validate_garage_door_entity_ids(request.garage_door_entity_ids)

    person = Person(
        first_name=request.first_name.strip(),
        last_name=request.last_name.strip(),
        display_name=compose_person_name(request.first_name, request.last_name),
        pronouns=normalize_person_pronouns(request.pronouns),
        profile_photo_data_url=normalize_profile_photo_or_400(request.profile_photo_data_url),
        group_id=group.id if group else None,
        schedule_id=schedule.id if schedule else None,
        garage_door_entity_ids=garage_door_entity_ids,
        home_assistant_mobile_app_notify_service=normalize_home_assistant_mobile_notify_service(
            request.home_assistant_mobile_app_notify_service
        ),
        notes=normalize_optional_text(request.notes),
        is_active=request.is_active,
    )
    session.add(person)
    await session.flush()

    await set_person_vehicle_assignments(session, person, vehicles)

    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_CRUD,
        action="person.create",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="Person",
        target_id=person.id,
        target_label=person.display_name,
        diff={"old": {}, "new": person_audit_snapshot(person)},
    )
    await session.commit()
    refreshed_person = await session.scalar(
        select(Person)
        .options(
            selectinload(Person.group),
            selectinload(Person.schedule),
            selectinload(Person.vehicles).selectinload(Vehicle.schedule),
            selectinload(Person.vehicle_assignments)
            .selectinload(VehiclePersonAssignment.vehicle)
            .selectinload(Vehicle.schedule),
        )
        .where(Person.id == person.id)
    )
    if not refreshed_person:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to load saved person")

    return PersonResponse(**serialize_person(refreshed_person))


@router.patch("/people/{person_id}", response_model=PersonResponse)
async def update_person(
    person_id: uuid.UUID,
    request: UpdatePersonRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> PersonResponse:
    person = await session.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Person not found")
    before = person_audit_snapshot(person)

    if "group_id" in request.model_fields_set:
        group = await get_group_or_404(session, request.group_id)
        person.group_id = group.id if group else None

    if "schedule_id" in request.model_fields_set:
        schedule = await get_schedule_or_404(session, request.schedule_id)
        person.schedule_id = schedule.id if schedule else None

    if request.vehicle_ids is not None:
        vehicles = await get_vehicles_or_404(session, request.vehicle_ids)
        await set_person_vehicle_assignments(session, person, vehicles)

    if request.garage_door_entity_ids is not None:
        person.garage_door_entity_ids = await validate_garage_door_entity_ids(request.garage_door_entity_ids)

    if "home_assistant_mobile_app_notify_service" in request.model_fields_set:
        person.home_assistant_mobile_app_notify_service = normalize_home_assistant_mobile_notify_service(
            request.home_assistant_mobile_app_notify_service
        )

    if request.first_name is not None:
        person.first_name = request.first_name.strip()
    if request.last_name is not None:
        person.last_name = request.last_name.strip()
    if request.first_name is not None or request.last_name is not None:
        person.display_name = compose_person_name(person.first_name, person.last_name)
    if "pronouns" in request.model_fields_set:
        person.pronouns = normalize_person_pronouns(request.pronouns)
    if "profile_photo_data_url" in request.model_fields_set:
        person.profile_photo_data_url = normalize_profile_photo_or_400(request.profile_photo_data_url)
    if "notes" in request.model_fields_set:
        person.notes = normalize_optional_text(request.notes)
    if request.is_active is not None:
        person.is_active = request.is_active

    after = person_audit_snapshot(person)
    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_CRUD,
        action="person.update",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="Person",
        target_id=person.id,
        target_label=person.display_name,
        diff=audit_diff(before, after),
    )
    await session.commit()
    refreshed_person = await session.scalar(
        select(Person)
        .options(
            selectinload(Person.group),
            selectinload(Person.schedule),
            selectinload(Person.vehicles).selectinload(Vehicle.schedule),
            selectinload(Person.vehicle_assignments)
            .selectinload(VehiclePersonAssignment.vehicle)
            .selectinload(Vehicle.schedule),
        )
        .where(Person.id == person.id)
    )
    if not refreshed_person:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to load saved person")

    return PersonResponse(**serialize_person(refreshed_person))


@router.get("/vehicles")
async def list_vehicles() -> list[VehicleResponse]:
    async with AsyncSessionLocal() as session:
        vehicles = (
            await session.scalars(
                select(Vehicle)
                .options(
                    selectinload(Vehicle.owner),
                    selectinload(Vehicle.schedule),
                    selectinload(Vehicle.person_assignments).selectinload(VehiclePersonAssignment.person),
                )
                .order_by(Vehicle.registration_number)
            )
        ).all()

    return [VehicleResponse(**serialize_vehicle(vehicle)) for vehicle in vehicles]


@router.post("/vehicles", response_model=VehicleResponse, status_code=status.HTTP_201_CREATED)
async def add_vehicle(
    request: CreateVehicleRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> VehicleResponse:
    person_ids = requested_vehicle_person_ids(request) or []
    people = await get_people_or_404(session, person_ids)
    schedule = await get_schedule_or_404(session, request.schedule_id)

    vehicle = Vehicle(
        person_id=derived_vehicle_person_id([person.id for person in people]),
        schedule_id=schedule.id if schedule else None,
        registration_number=normalize_registration_number(request.registration_number),
        vehicle_photo_data_url=request.vehicle_photo_data_url,
        make=normalize_vehicle_text(request.make),
        model=normalize_vehicle_text(request.model),
        color=normalize_vehicle_text(request.color),
        fuel_type=normalize_vehicle_text(request.fuel_type),
        mot_status=normalize_vehicle_text(request.mot_status),
        tax_status=normalize_vehicle_text(request.tax_status),
        mot_expiry=request.mot_expiry,
        tax_expiry=request.tax_expiry,
        last_dvla_lookup_date=request.last_dvla_lookup_date,
        description=normalize_optional_text(request.description),
        is_active=request.is_active,
    )
    session.add(vehicle)
    try:
        await session.flush()
        await set_vehicle_person_assignments(session, vehicle, people)
        await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_CRUD,
            action="vehicle.create",
            actor=actor_from_user(user),
            actor_user_id=user.id,
            target_entity="Vehicle",
            target_id=vehicle.id,
            target_label=vehicle.registration_number,
            diff={"old": {}, "new": vehicle_audit_snapshot(vehicle)},
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Vehicle already exists") from exc

    refreshed_vehicle = await session.scalar(
        select(Vehicle)
        .options(
            selectinload(Vehicle.owner),
            selectinload(Vehicle.schedule),
            selectinload(Vehicle.person_assignments).selectinload(VehiclePersonAssignment.person),
        )
        .where(Vehicle.id == vehicle.id)
    )
    if not refreshed_vehicle:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to load saved vehicle")

    return VehicleResponse(**serialize_vehicle(refreshed_vehicle))


@router.patch("/vehicles/{vehicle_id}", response_model=VehicleResponse)
async def update_vehicle(
    vehicle_id: uuid.UUID,
    request: UpdateVehicleRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> VehicleResponse:
    vehicle = await session.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")
    before = vehicle_audit_snapshot(vehicle)

    person_ids = requested_vehicle_person_ids(request)
    if person_ids is not None:
        people = await get_people_or_404(session, person_ids)
        await set_vehicle_person_assignments(session, vehicle, people)

    if "schedule_id" in request.model_fields_set:
        schedule = await get_schedule_or_404(session, request.schedule_id)
        vehicle.schedule_id = schedule.id if schedule else None

    if request.registration_number is not None:
        vehicle.registration_number = normalize_registration_number(request.registration_number)
    if "vehicle_photo_data_url" in request.model_fields_set:
        vehicle.vehicle_photo_data_url = request.vehicle_photo_data_url
    if "make" in request.model_fields_set:
        vehicle.make = normalize_vehicle_text(request.make)
    if "model" in request.model_fields_set:
        vehicle.model = normalize_vehicle_text(request.model)
    if "color" in request.model_fields_set:
        vehicle.color = normalize_vehicle_text(request.color)
    if "fuel_type" in request.model_fields_set:
        vehicle.fuel_type = normalize_vehicle_text(request.fuel_type)
    if "mot_status" in request.model_fields_set:
        vehicle.mot_status = normalize_vehicle_text(request.mot_status)
    if "tax_status" in request.model_fields_set:
        vehicle.tax_status = normalize_vehicle_text(request.tax_status)
    if "mot_expiry" in request.model_fields_set:
        vehicle.mot_expiry = request.mot_expiry
    if "tax_expiry" in request.model_fields_set:
        vehicle.tax_expiry = request.tax_expiry
    if "last_dvla_lookup_date" in request.model_fields_set:
        vehicle.last_dvla_lookup_date = request.last_dvla_lookup_date
    if "description" in request.model_fields_set:
        vehicle.description = normalize_optional_text(request.description)
    if request.is_active is not None:
        vehicle.is_active = request.is_active

    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_CRUD,
        action="vehicle.update",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="Vehicle",
        target_id=vehicle.id,
        target_label=vehicle.registration_number,
        diff=audit_diff(before, vehicle_audit_snapshot(vehicle)),
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Vehicle already exists") from exc

    refreshed_vehicle = await session.scalar(
        select(Vehicle)
        .options(
            selectinload(Vehicle.owner),
            selectinload(Vehicle.schedule),
            selectinload(Vehicle.person_assignments).selectinload(VehiclePersonAssignment.person),
        )
        .where(Vehicle.id == vehicle.id)
    )
    if not refreshed_vehicle:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to load saved vehicle")

    return VehicleResponse(**serialize_vehicle(refreshed_vehicle))


@router.post("/vehicles/{vehicle_id}/dvla-refresh", response_model=VehicleResponse)
async def refresh_vehicle_dvla(
    vehicle_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> VehicleResponse:
    vehicle = await session.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")

    before = vehicle_audit_snapshot(vehicle)
    config = await get_runtime_config()
    lookup_date = local_today(config.site_timezone)
    try:
        normalized = await lookup_normalized_vehicle_registration(vehicle.registration_number, today=lookup_date)
    except DvlaVehicleEnquiryError as exc:
        status_code = exc.status_code if exc.status_code and exc.status_code >= 400 else status.HTTP_503_SERVICE_UNAVAILABLE
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    apply_dvla_vehicle_details(vehicle, normalized, lookup_date=lookup_date)
    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_CRUD,
        action="vehicle.dvla_refresh",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="Vehicle",
        target_id=vehicle.id,
        target_label=vehicle.registration_number,
        diff=audit_diff(before, vehicle_audit_snapshot(vehicle)),
    )
    await session.commit()

    refreshed_vehicle = await session.scalar(
        select(Vehicle)
        .options(
            selectinload(Vehicle.owner),
            selectinload(Vehicle.schedule),
            selectinload(Vehicle.person_assignments).selectinload(VehiclePersonAssignment.person),
        )
        .where(Vehicle.id == vehicle.id)
    )
    if not refreshed_vehicle:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to load saved vehicle")

    return VehicleResponse(**serialize_vehicle(refreshed_vehicle))


@router.delete("/vehicles/{vehicle_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vehicle(
    vehicle_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    vehicle = await session.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")

    before = vehicle_audit_snapshot(vehicle)
    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_CRUD,
        action="vehicle.delete",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="Vehicle",
        target_id=vehicle.id,
        target_label=vehicle.registration_number,
        diff={"old": before, "new": {}},
    )
    await session.delete(vehicle)
    await session.commit()


@router.get("/groups")
async def list_groups() -> list[GroupResponse]:
    async with AsyncSessionLocal() as session:
        groups = (
            await session.scalars(
                select(Group)
                .options(selectinload(Group.people))
                .order_by(Group.category, Group.name)
            )
        ).all()

    return [GroupResponse(**serialize_group(group)) for group in groups]


@router.post("/groups", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
async def add_group(
    request: CreateGroupRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> GroupResponse:
    group = Group(
        name=request.name.strip(),
        category=request.category,
        subtype=request.subtype.strip() if request.subtype else None,
        description=request.description.strip() if request.description else None,
    )
    session.add(group)
    try:
        await session.flush()
        await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_CRUD,
            action="group.create",
            actor=actor_from_user(user),
            actor_user_id=user.id,
            target_entity="Group",
            target_id=group.id,
            target_label=group.name,
            diff={"old": {}, "new": group_audit_snapshot(group)},
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group already exists") from exc

    refreshed_group = await session.scalar(
        select(Group)
        .options(selectinload(Group.people))
        .where(Group.id == group.id)
    )
    if not refreshed_group:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to load saved group")

    return GroupResponse(**serialize_group(refreshed_group))


@router.patch("/groups/{group_id}", response_model=GroupResponse)
async def update_group(
    group_id: uuid.UUID,
    request: UpdateGroupRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> GroupResponse:
    group = await session.get(Group, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    before = group_audit_snapshot(group)

    if request.name is not None:
        group.name = request.name.strip()
    if request.category is not None:
        group.category = request.category
    if "subtype" in request.model_fields_set:
        group.subtype = request.subtype.strip() if request.subtype else None
    if "description" in request.model_fields_set:
        group.description = request.description.strip() if request.description else None

    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_CRUD,
        action="group.update",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="Group",
        target_id=group.id,
        target_label=group.name,
        diff=audit_diff(before, group_audit_snapshot(group)),
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group already exists") from exc

    refreshed_group = await session.scalar(
        select(Group)
        .options(selectinload(Group.people))
        .where(Group.id == group.id)
    )
    if not refreshed_group:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to load saved group")

    return GroupResponse(**serialize_group(refreshed_group))
