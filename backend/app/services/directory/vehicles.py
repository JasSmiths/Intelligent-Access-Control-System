import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, selectinload

from app.models import User, Vehicle, VehicleInformationSnapshot, VehiclePersonAssignment
from app.schemas.directory import CreateVehicleRequest, UpdateVehicleRequest, VehicleResponse
from app.services.mutation_context import MutationError, load_active_admin
from app.services.settings import get_runtime_config
from app.services.telemetry import (
    TELEMETRY_CATEGORY_CRUD,
    actor_from_user,
    audit_diff,
    write_audit_log,
)
from app.services.vehicle_information import get_vehicle_information_service
from app.services.vehicle_information_contracts import MotHistoryPage
from app.services.vehicle_information_store import apply_information, read_mot_history

from .assignments import (
    derived_vehicle_person_id,
    get_people,
    requested_vehicle_person_ids,
    set_directory_schedule_assignment,
    set_vehicle_person_assignments,
)
from .errors import DirectoryOperationError
from .reads import get_vehicle
from .representation import (
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
    config = await get_runtime_config()
    lookup = await get_vehicle_information_service().lookup(request.registration_number, cached_only=True, config=config)

    vehicle = Vehicle(
        person_id=derived_vehicle_person_id([person.id for person in people]),
        registration_number=normalize_registration_number(request.registration_number),
        vehicle_photo_data_url=await normalize_profile_photo(request.vehicle_photo_data_url),
        make=normalize_vehicle_text(request.make),
        model=normalize_vehicle_text(request.model),
        color=normalize_vehicle_text(request.color),
        fuel_type=normalize_vehicle_text(request.fuel_type),
        description=normalize_optional_text(request.description),
        is_active=request.is_active,
    )
    session.add(vehicle)
    try:
        await session.flush()
        await apply_information(session, vehicle, lookup, timezone=config.site_timezone)
        for field in ("make", "model", "color", "fuel_type"):
            if getattr(request, field):
                setattr(vehicle, field, normalize_vehicle_text(getattr(request, field)))
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

    return VehicleResponse(**serialize_vehicle(refreshed_vehicle, include_media=False, timezone_name=(await get_runtime_config()).site_timezone))


async def update_vehicle(
    vehicle_id: uuid.UUID,
    request: UpdateVehicleRequest,
    user: User,
    session: AsyncSession,
) -> VehicleResponse:
    vehicle = await session.get(Vehicle, vehicle_id, with_for_update=True)
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
        plate = normalize_registration_number(request.registration_number)
        if plate != vehicle.registration_number:
            snapshot = await session.get(VehicleInformationSnapshot, vehicle.id)
            if snapshot:
                await session.delete(snapshot)
            for field in ("mot_status", "mot_expiry", "tax_status", "tax_expiry", "last_dvla_lookup_date",
                          "mot_source", "mot_checked_at", "mot_valid_until", "mot_expiry_kind", "information_checked_at", "information_outcome"):
                setattr(vehicle, field, None)
            vehicle.registration_number = plate
            config = await get_runtime_config()
            lookup = await get_vehicle_information_service().lookup(plate, cached_only=True, config=config)
            await apply_information(session, vehicle, lookup, timezone=config.site_timezone)
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

    return VehicleResponse(**serialize_vehicle(refreshed_vehicle, include_media=False, timezone_name=(await get_runtime_config()).site_timezone))


async def refresh_vehicle_information(
    vehicle_id: uuid.UUID,
    user: User,
    session: AsyncSession,
) -> VehicleResponse:
    vehicle = await session.scalar(select(Vehicle).options(defer(Vehicle.vehicle_photo_data_url)).where(Vehicle.id == vehicle_id))
    if not vehicle:
        raise DirectoryOperationError(status_code=404, detail="Vehicle not found")

    registration = vehicle.registration_number
    before = vehicle_audit_snapshot(vehicle)
    # Commit confirmation and accepted-operation audit before releasing the connection for I/O.
    await write_audit_log(session, category=TELEMETRY_CATEGORY_CRUD,
        action="vehicle.information_refresh.requested", actor=actor_from_user(user), actor_user_id=user.id,
        target_entity="Vehicle", target_id=vehicle.id, target_label=registration)
    await session.commit()
    config = await get_runtime_config()
    lookup = await get_vehicle_information_service().lookup(registration, force=True, config=config)
    vehicle = await session.scalar(select(Vehicle).options(defer(Vehicle.vehicle_photo_data_url)).where(Vehicle.id == vehicle_id)
        .with_for_update().execution_options(populate_existing=True))
    if not vehicle or vehicle.registration_number != registration:
        raise DirectoryOperationError(status_code=409, detail="Vehicle registration changed during refresh")
    try:
        await load_active_admin(session, user.id)
    except MutationError as exc:
        raise DirectoryOperationError(status_code=403, detail=str(exc)) from exc
    await apply_information(session, vehicle, lookup, timezone=config.site_timezone)
    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_CRUD,
        action="vehicle.information_refresh",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="Vehicle",
        target_id=vehicle.id,
        target_label=vehicle.registration_number,
        diff=audit_diff(before, vehicle_audit_snapshot(vehicle)),
    )
    await session.commit()

    return await get_vehicle(session, vehicle_id)


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


async def mot_history(session: AsyncSession, vehicle_id: uuid.UUID, *, cursor: str | None, limit: int) -> MotHistoryPage:
    try:
        return await read_mot_history(session, vehicle_id, cursor=cursor, limit=limit)
    except LookupError as exc:
        raise DirectoryOperationError(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise DirectoryOperationError(status_code=400, detail=str(exc)) from exc
