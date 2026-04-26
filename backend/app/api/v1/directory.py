import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import current_user
from app.db.session import AsyncSessionLocal, get_db_session
from app.models import Group, Person, Schedule, TimeSlot, User, Vehicle
from app.models.enums import GroupCategory
from app.modules.dvla.vehicle_enquiry import friendly_vehicle_text
from app.services.settings import get_runtime_config

router = APIRouter()


class PersonVehicleResponse(BaseModel):
    id: str
    registration_number: str
    description: str | None
    vehicle_photo_data_url: str | None
    make: str | None
    model: str | None
    color: str | None
    schedule_id: str | None = None
    schedule: str | None = None


class PersonResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    display_name: str
    profile_photo_data_url: str | None
    group_id: str | None
    group: str | None
    category: str | None
    schedule_id: str | None
    schedule: str | None
    is_active: bool
    garage_door_entity_ids: list[str]
    vehicles: list[PersonVehicleResponse]


class CreatePersonRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    profile_photo_data_url: str | None = Field(default=None, max_length=11_200_000)
    group_id: uuid.UUID | None = None
    schedule_id: uuid.UUID | None = None
    vehicle_ids: list[uuid.UUID] = Field(default_factory=list)
    garage_door_entity_ids: list[str] = Field(default_factory=list)
    is_active: bool = True


class UpdatePersonRequest(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    profile_photo_data_url: str | None = Field(default=None, max_length=11_200_000)
    group_id: uuid.UUID | None = None
    schedule_id: uuid.UUID | None = None
    vehicle_ids: list[uuid.UUID] | None = None
    garage_door_entity_ids: list[str] | None = None
    is_active: bool | None = None


class VehicleResponse(BaseModel):
    id: str
    registration_number: str
    vehicle_photo_data_url: str | None
    description: str | None
    make: str | None
    model: str | None
    color: str | None
    person_id: str | None
    owner: str | None
    schedule_id: str | None
    schedule: str | None
    is_active: bool


class CreateVehicleRequest(BaseModel):
    registration_number: str = Field(min_length=1, max_length=32)
    vehicle_photo_data_url: str | None = Field(default=None, max_length=11_200_000)
    make: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=120)
    color: str | None = Field(default=None, max_length=80)
    person_id: uuid.UUID | None = None
    schedule_id: uuid.UUID | None = None
    is_active: bool = True


class UpdateVehicleRequest(BaseModel):
    registration_number: str | None = Field(default=None, min_length=1, max_length=32)
    vehicle_photo_data_url: str | None = Field(default=None, max_length=11_200_000)
    make: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=120)
    color: str | None = Field(default=None, max_length=80)
    person_id: uuid.UUID | None = None
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


def serialize_vehicle(vehicle: Vehicle) -> dict:
    return {
        "id": str(vehicle.id),
        "registration_number": vehicle.registration_number,
        "vehicle_photo_data_url": vehicle.vehicle_photo_data_url,
        "description": vehicle.description,
        "make": vehicle.make,
        "model": vehicle.model,
        "color": vehicle.color,
        "person_id": str(vehicle.person_id) if vehicle.person_id else None,
        "owner": vehicle.owner.display_name if vehicle.owner else None,
        "schedule_id": str(vehicle.schedule_id) if vehicle.schedule_id else None,
        "schedule": vehicle.schedule.name if vehicle.schedule else None,
        "is_active": vehicle.is_active,
    }


def serialize_person(person: Person) -> dict:
    return {
        "id": str(person.id),
        "first_name": person.first_name,
        "last_name": person.last_name,
        "display_name": person.display_name,
        "profile_photo_data_url": person.profile_photo_data_url,
        "group_id": str(person.group_id) if person.group_id else None,
        "group": person.group.name if person.group else None,
        "category": person.group.category.value if person.group else None,
        "schedule_id": str(person.schedule_id) if person.schedule_id else None,
        "schedule": person.schedule.name if person.schedule else None,
        "is_active": person.is_active,
        "garage_door_entity_ids": list(person.garage_door_entity_ids or []),
        "vehicles": [
                {
                    "id": str(vehicle.id),
                    "registration_number": vehicle.registration_number,
                    "description": vehicle.description,
                    "vehicle_photo_data_url": vehicle.vehicle_photo_data_url,
                    "make": vehicle.make,
                    "model": vehicle.model,
                    "color": vehicle.color,
                    "schedule_id": str(vehicle.schedule_id) if vehicle.schedule_id else None,
                    "schedule": vehicle.schedule.name if vehicle.schedule else None,
                }
                for vehicle in person.vehicles
            ],
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
    _: User = Depends(current_user),
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
        profile_photo_data_url=request.profile_photo_data_url,
        group_id=group.id if group else None,
        schedule_id=schedule.id if schedule else None,
        garage_door_entity_ids=garage_door_entity_ids,
        is_active=request.is_active,
    )
    session.add(person)
    await session.flush()

    for vehicle in vehicles:
        vehicle.person_id = person.id

    await session.commit()
    refreshed_person = await session.scalar(
        select(Person)
        .options(
            selectinload(Person.group),
            selectinload(Person.schedule),
            selectinload(Person.vehicles).selectinload(Vehicle.schedule),
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
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> PersonResponse:
    person = await session.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Person not found")

    if "group_id" in request.model_fields_set:
        group = await get_group_or_404(session, request.group_id)
        person.group_id = group.id if group else None

    if "schedule_id" in request.model_fields_set:
        schedule = await get_schedule_or_404(session, request.schedule_id)
        person.schedule_id = schedule.id if schedule else None

    if request.vehicle_ids is not None:
        vehicles = await get_vehicles_or_404(session, request.vehicle_ids)
        current_vehicles = (
            await session.scalars(select(Vehicle).where(Vehicle.person_id == person.id))
        ).all()
        selected_ids = {vehicle.id for vehicle in vehicles}
        for vehicle in current_vehicles:
            if vehicle.id not in selected_ids:
                vehicle.person_id = None
        for vehicle in vehicles:
            vehicle.person_id = person.id

    if request.garage_door_entity_ids is not None:
        person.garage_door_entity_ids = await validate_garage_door_entity_ids(request.garage_door_entity_ids)

    if request.first_name is not None:
        person.first_name = request.first_name.strip()
    if request.last_name is not None:
        person.last_name = request.last_name.strip()
    if request.first_name is not None or request.last_name is not None:
        person.display_name = compose_person_name(person.first_name, person.last_name)
    if "profile_photo_data_url" in request.model_fields_set:
        person.profile_photo_data_url = request.profile_photo_data_url
    if request.is_active is not None:
        person.is_active = request.is_active

    await session.commit()
    refreshed_person = await session.scalar(
        select(Person)
        .options(
            selectinload(Person.group),
            selectinload(Person.schedule),
            selectinload(Person.vehicles).selectinload(Vehicle.schedule),
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
                select(Vehicle).options(selectinload(Vehicle.owner)).order_by(Vehicle.registration_number)
                .options(selectinload(Vehicle.schedule))
            )
        ).all()

    return [VehicleResponse(**serialize_vehicle(vehicle)) for vehicle in vehicles]


@router.post("/vehicles", response_model=VehicleResponse, status_code=status.HTTP_201_CREATED)
async def add_vehicle(
    request: CreateVehicleRequest,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> VehicleResponse:
    owner = await session.get(Person, request.person_id) if request.person_id else None
    if request.person_id and not owner:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Person not found")
    schedule = await get_schedule_or_404(session, request.schedule_id)

    vehicle = Vehicle(
        person_id=owner.id if owner else None,
        schedule_id=schedule.id if schedule else None,
        registration_number=normalize_registration_number(request.registration_number),
        vehicle_photo_data_url=request.vehicle_photo_data_url,
        make=normalize_vehicle_text(request.make),
        model=normalize_vehicle_text(request.model),
        color=normalize_vehicle_text(request.color),
        is_active=request.is_active,
    )
    session.add(vehicle)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Vehicle already exists") from exc

    refreshed_vehicle = await session.scalar(
        select(Vehicle)
        .options(selectinload(Vehicle.owner), selectinload(Vehicle.schedule))
        .where(Vehicle.id == vehicle.id)
    )
    if not refreshed_vehicle:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to load saved vehicle")

    return VehicleResponse(**serialize_vehicle(refreshed_vehicle))


@router.patch("/vehicles/{vehicle_id}", response_model=VehicleResponse)
async def update_vehicle(
    vehicle_id: uuid.UUID,
    request: UpdateVehicleRequest,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> VehicleResponse:
    vehicle = await session.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")

    if request.person_id:
        owner = await session.get(Person, request.person_id)
        if not owner:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Person not found")
        vehicle.person_id = owner.id
    elif "person_id" in request.model_fields_set:
        vehicle.person_id = None

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
    if request.is_active is not None:
        vehicle.is_active = request.is_active

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Vehicle already exists") from exc

    refreshed_vehicle = await session.scalar(
        select(Vehicle)
        .options(selectinload(Vehicle.owner), selectinload(Vehicle.schedule))
        .where(Vehicle.id == vehicle.id)
    )
    if not refreshed_vehicle:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to load saved vehicle")

    return VehicleResponse(**serialize_vehicle(refreshed_vehicle))


@router.delete("/vehicles/{vehicle_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vehicle(
    vehicle_id: uuid.UUID,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    vehicle = await session.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")

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
    _: User = Depends(current_user),
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
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> GroupResponse:
    group = await session.get(Group, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    if request.name is not None:
        group.name = request.name.strip()
    if request.category is not None:
        group.category = request.category
    if "subtype" in request.model_fields_set:
        group.subtype = request.subtype.strip() if request.subtype else None
    if "description" in request.model_fields_set:
        group.description = request.description.strip() if request.description else None

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


@router.get("/time-slots")
async def list_time_slots() -> list[dict]:
    async with AsyncSessionLocal() as session:
        slots = (await session.scalars(select(TimeSlot).order_by(TimeSlot.name))).all()

    return [
        {
            "id": str(slot.id),
            "name": slot.name,
            "kind": slot.kind.value,
            "days_of_week": slot.days_of_week,
            "start_time": slot.start_time.isoformat() if slot.start_time else None,
            "end_time": slot.end_time.isoformat() if slot.end_time else None,
            "is_active": slot.is_active,
        }
        for slot in slots
    ]
