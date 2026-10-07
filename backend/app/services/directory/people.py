import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Person, User, Vehicle, VehiclePersonAssignment
from app.schemas.directory import CreatePersonRequest, PersonResponse, UpdatePersonRequest
from app.services.settings import get_runtime_config
from app.services.telemetry import (
    TELEMETRY_CATEGORY_CRUD,
    actor_from_user,
    audit_diff,
    write_audit_log,
)

from .assignments import (
    get_group,
    get_vehicles,
    set_directory_schedule_assignment,
    set_person_vehicle_assignments,
    validate_garage_door_entity_ids,
)
from .errors import DirectoryOperationError
from .representation import (
    compose_person_name,
    normalize_home_assistant_mobile_notify_service,
    normalize_optional_text,
    normalize_person_presence_input_boolean_action,
    normalize_person_presence_input_boolean_entity_ids,
    normalize_person_pronouns,
    normalize_profile_photo,
    person_audit_snapshot,
    serialize_person,
)


async def add_person(
    request: CreatePersonRequest,
    user: User,
    session: AsyncSession,
) -> PersonResponse:
    group = await get_group(session, request.group_id)
    vehicles = await get_vehicles(session, request.vehicle_ids)
    garage_door_entity_ids = await validate_garage_door_entity_ids(request.garage_door_entity_ids)

    person = Person(
        first_name=request.first_name.strip(),
        last_name=request.last_name.strip(),
        display_name=compose_person_name(request.first_name, request.last_name),
        pronouns=normalize_person_pronouns(request.pronouns),
        profile_photo_data_url=await normalize_profile_photo(request.profile_photo_data_url),
        group_id=group.id if group else None,
        garage_door_entity_ids=garage_door_entity_ids,
        home_assistant_mobile_app_notify_service=normalize_home_assistant_mobile_notify_service(
            request.home_assistant_mobile_app_notify_service
        ),
        home_assistant_presence_input_boolean_entity_ids=normalize_person_presence_input_boolean_entity_ids(
            request.home_assistant_presence_input_boolean_entity_ids
        ),
        home_assistant_presence_input_boolean_entry_action=normalize_person_presence_input_boolean_action(
            request.home_assistant_presence_input_boolean_entry_action
        ),
        home_assistant_presence_input_boolean_exit_action=normalize_person_presence_input_boolean_action(
            request.home_assistant_presence_input_boolean_exit_action
        ),
        missed_exit_recovery_enabled=request.missed_exit_recovery_enabled,
        missed_exit_recovery_tracker_entity_id=request.missed_exit_recovery_tracker_entity_id,
        notes=normalize_optional_text(request.notes),
        is_active=request.is_active,
    )
    session.add(person)
    await session.flush()

    from app.services.resident_recovery import validate_person_configuration

    try:
        await validate_person_configuration(session, person)
    except ValueError as exc:
        raise DirectoryOperationError(status_code=400, detail=str(exc)) from exc
    await set_person_vehicle_assignments(session, person, vehicles)
    await set_directory_schedule_assignment(session, person, request.schedule_id, user)

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
        raise DirectoryOperationError(status_code=500, detail="Unable to load saved person")

    return PersonResponse(**serialize_person(refreshed_person, include_media=False, timezone_name=(await get_runtime_config()).site_timezone))


async def update_person(
    person_id: uuid.UUID,
    request: UpdatePersonRequest,
    user: User,
    session: AsyncSession,
) -> PersonResponse:
    person = await session.get(Person, person_id)
    if not person:
        raise DirectoryOperationError(status_code=404, detail="Person not found")
    # Recovery dispatch holds this same owner row through its durable checkpoint.
    await session.refresh(person, with_for_update=True)
    before = person_audit_snapshot(person)

    if "group_id" in request.model_fields_set:
        group = await get_group(session, request.group_id)
        person.group_id = group.id if group else None

    if "schedule_id" in request.model_fields_set:
        await set_directory_schedule_assignment(session, person, request.schedule_id, user)

    if request.vehicle_ids is not None:
        vehicles = await get_vehicles(session, request.vehicle_ids)
        await set_person_vehicle_assignments(session, person, vehicles)

    if request.garage_door_entity_ids is not None:
        person.garage_door_entity_ids = await validate_garage_door_entity_ids(
            request.garage_door_entity_ids
        )

    if "home_assistant_mobile_app_notify_service" in request.model_fields_set:
        person.home_assistant_mobile_app_notify_service = (
            normalize_home_assistant_mobile_notify_service(
                request.home_assistant_mobile_app_notify_service
            )
        )

    if "home_assistant_presence_input_boolean_entity_ids" in request.model_fields_set:
        person.home_assistant_presence_input_boolean_entity_ids = (
            normalize_person_presence_input_boolean_entity_ids(
                request.home_assistant_presence_input_boolean_entity_ids
            )
        )

    if "home_assistant_presence_input_boolean_entry_action" in request.model_fields_set:
        person.home_assistant_presence_input_boolean_entry_action = (
            normalize_person_presence_input_boolean_action(
                request.home_assistant_presence_input_boolean_entry_action
            )
        )

    if "home_assistant_presence_input_boolean_exit_action" in request.model_fields_set:
        person.home_assistant_presence_input_boolean_exit_action = (
            normalize_person_presence_input_boolean_action(
                request.home_assistant_presence_input_boolean_exit_action
            )
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
        person.profile_photo_data_url = await normalize_profile_photo(
            request.profile_photo_data_url
        )
    if "notes" in request.model_fields_set:
        person.notes = normalize_optional_text(request.notes)
    if request.is_active is not None:
        person.is_active = request.is_active

    if request.missed_exit_recovery_enabled is not None:
        person.missed_exit_recovery_enabled = request.missed_exit_recovery_enabled
    if "missed_exit_recovery_tracker_entity_id" in request.model_fields_set:
        person.missed_exit_recovery_tracker_entity_id = (
            request.missed_exit_recovery_tracker_entity_id
        )
    from app.services.resident_recovery import validate_person_configuration

    try:
        await validate_person_configuration(session, person)
    except ValueError as exc:
        raise DirectoryOperationError(status_code=400, detail=str(exc)) from exc
    if any(
        key in request.model_fields_set
        for key in (
            "missed_exit_recovery_enabled",
            "missed_exit_recovery_tracker_entity_id",
            "home_assistant_mobile_app_notify_service",
            "is_active",
        )
    ):
        from app.models import ResidentRecoveryJourney

        journey = await session.get(ResidentRecoveryJourney, person.id, with_for_update=True)
        if journey:
            journey.samples, journey.invalid_reason = [], "resident_configuration_changed"
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
        raise DirectoryOperationError(status_code=500, detail="Unable to load saved person")

    return PersonResponse(**serialize_person(refreshed_person, include_media=False, timezone_name=(await get_runtime_config()).site_timezone))
