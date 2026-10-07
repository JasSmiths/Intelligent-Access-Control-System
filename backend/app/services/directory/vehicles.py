import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import User, Vehicle, VehiclePersonAssignment
from app.modules.dvla.vehicle_enquiry import DvlaVehicleEnquiryError
from app.schemas.directory import CreateVehicleRequest, UpdateVehicleRequest, VehicleResponse
from app.services.dvla import lookup_normalized_vehicle_registration
from app.services.settings import get_runtime_config
from app.services.telemetry import (
    TELEMETRY_CATEGORY_CRUD,
    actor_from_user,
    audit_diff,
    write_audit_log,
)

from .assignments import (
    derived_vehicle_person_id,
    get_people,
    requested_vehicle_person_ids,
    set_directory_schedule_assignment,
    set_vehicle_person_assignments,
)
from .errors import DirectoryOperationError
from .representation import (
    apply_dvla_vehicle_details,
    local_today,
    normalize_optional_text,
    normalize_profile_photo,
    normalize_registration_number,
    normalize_vehicle_text,
    serialize_vehicle,
    vehicle_audit_snapshot,
)


async def add_vehicle(
    request: CreateVehicleRequest,
    user: User,
    session: AsyncSession,
) -> VehicleResponse:
    person_ids = requested_vehicle_person_ids(request) or []
    people = await get_people(session, person_ids)

    vehicle = Vehicle(
        person_id=derived_vehicle_person_id([person.id for person in people]),
        registration_number=normalize_registration_number(request.registration_number),
        vehicle_photo_data_url=await normalize_profile_photo(request.vehicle_photo_data_url),
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
        await set_directory_schedule_assignment(session, vehicle, request.schedule_id, user)
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
        raise DirectoryOperationError(status_code=409, detail="Vehicle already exists") from exc

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
        raise DirectoryOperationError(status_code=500, detail="Unable to load saved vehicle")

    return VehicleResponse(**serialize_vehicle(refreshed_vehicle, include_media=False))


async def update_vehicle(
    vehicle_id: uuid.UUID,
    request: UpdateVehicleRequest,
    user: User,
    session: AsyncSession,
) -> VehicleResponse:
    vehicle = await session.get(Vehicle, vehicle_id)
    if not vehicle:
        raise DirectoryOperationError(status_code=404, detail="Vehicle not found")
    before = vehicle_audit_snapshot(vehicle)

    person_ids = requested_vehicle_person_ids(request)
    if person_ids is not None:
        people = await get_people(session, person_ids)
        await set_vehicle_person_assignments(session, vehicle, people)

    if "schedule_id" in request.model_fields_set:
        await set_directory_schedule_assignment(session, vehicle, request.schedule_id, user)

    if request.registration_number is not None:
        vehicle.registration_number = normalize_registration_number(request.registration_number)
    if "vehicle_photo_data_url" in request.model_fields_set:
        vehicle.vehicle_photo_data_url = await normalize_profile_photo(
            request.vehicle_photo_data_url
        )
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
        raise DirectoryOperationError(status_code=409, detail="Vehicle already exists") from exc

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
        raise DirectoryOperationError(status_code=500, detail="Unable to load saved vehicle")

    return VehicleResponse(**serialize_vehicle(refreshed_vehicle, include_media=False))


async def refresh_vehicle_dvla(
    vehicle_id: uuid.UUID,
    user: User,
    session: AsyncSession,
) -> VehicleResponse:
    vehicle = await session.get(Vehicle, vehicle_id)
    if not vehicle:
        raise DirectoryOperationError(status_code=404, detail="Vehicle not found")

    before = vehicle_audit_snapshot(vehicle)
    config = await get_runtime_config()
    lookup_date = local_today(config.site_timezone)
    try:
        normalized = await lookup_normalized_vehicle_registration(
            vehicle.registration_number, today=lookup_date
        )
    except DvlaVehicleEnquiryError as exc:
        status_code = exc.status_code if exc.status_code and exc.status_code >= 400 else 503
        raise DirectoryOperationError(status_code=status_code, detail=str(exc)) from exc

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
        raise DirectoryOperationError(status_code=500, detail="Unable to load saved vehicle")

    return VehicleResponse(**serialize_vehicle(refreshed_vehicle, include_media=False))


async def delete_vehicle(
    vehicle_id: uuid.UUID,
    user: User,
    session: AsyncSession,
) -> None:
    vehicle = await session.get(Vehicle, vehicle_id)
    if not vehicle:
        raise DirectoryOperationError(status_code=404, detail="Vehicle not found")

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
