"""Schedule Alfred tool handlers."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.ai.context import get_chat_tool_context
from app.ai.tool_groups._shared import (
    _agent_datetime_display,
    _agent_datetime_iso,
    _bounded_int,
    _cover_target_match_score,
    _normalize,
    _parse_agent_datetime,
    _person_record_matches,
    _schedule_answer_artifacts,
    _uuid_from_value,
)
from app.db.session import AsyncSessionLocal
from app.models import (
    Person,
    Schedule,
    User,
    Vehicle,
)
from app.modules.access_devices.base import AccessDeviceEntity
from app.modules.dvla.vehicle_enquiry import normalize_registration_number
from app.modules.home_assistant.covers import cover_entity_state_payload
from app.services import schedule_operations
from app.services.access_devices import get_access_device_service
from app.services.schedule_assignments import set_schedule_assignment
from app.services.schedule_operations import ScheduleOperationError
from app.services.schedule_overrides import (
    create_schedule_override,
    publish_schedule_override_created,
)
from app.services.schedules import (
    evaluate_person_schedule,
    evaluate_schedule_id,
    evaluate_vehicle_schedule,
    normalize_time_blocks,
    schedule_allows_at,
    schedule_dependencies,
)
from app.services.settings import get_runtime_config

NATURAL_SCHEDULE_DAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "weds": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}

NATURAL_SCHEDULE_DAY_PATTERN = (
    r"mon(?:day)?(?:'s|s)?|"
    r"tue(?:s|sday)?(?:'s|s)?|"
    r"wed(?:s|nesday)?(?:'s|s)?|"
    r"thu(?:r|rs|rsday)?(?:'s|s)?|"
    r"fri(?:day)?(?:'s|s)?|"
    r"sat(?:urday)?(?:'s|s)?|"
    r"sun(?:day)?(?:'s|s)?"
)


async def _resolve_schedule(session, arguments: dict[str, Any]) -> Schedule | None:
    schedule_id = _uuid_from_value(arguments.get("schedule_id"))
    if schedule_id:
        return await session.get(Schedule, schedule_id)

    schedule_name = _normalize(arguments.get("schedule_name") or arguments.get("name"))
    if not schedule_name:
        return None
    schedules = (await session.scalars(select(Schedule).order_by(Schedule.name))).all()
    exact = [schedule for schedule in schedules if schedule.name.lower() == schedule_name]
    if exact:
        return exact[0]
    partial = [schedule for schedule in schedules if schedule_name in schedule.name.lower()]
    return partial[0] if len(partial) == 1 else None


async def _resolve_person(session, arguments: dict[str, Any]) -> Person | None:
    person_id = _uuid_from_value(arguments.get("entity_id") or arguments.get("person_id"))
    if person_id:
        return await session.scalar(
            select(Person)
            .options(selectinload(Person.schedule), selectinload(Person.group))
            .where(Person.id == person_id)
        )

    person_name = _normalize(
        arguments.get("entity_name")
        or arguments.get("person")
        or arguments.get("person_name")
        or arguments.get("name")
    )
    if not person_name:
        return None
    people = (
        await session.scalars(
            select(Person)
            .options(selectinload(Person.schedule), selectinload(Person.group))
            .order_by(Person.display_name)
        )
    ).all()
    exact = [person for person in people if person.display_name.lower() == person_name]
    if exact:
        return exact[0]
    partial = [
        person
        for person in people
        if _person_record_matches(
            {"display_name": person.display_name, "group": person.group.name if person.group else ""},
            person_name,
        )
    ]
    return partial[0] if len(partial) == 1 else None


async def _resolve_vehicle(session, arguments: dict[str, Any]) -> Vehicle | None:
    vehicle_id = _uuid_from_value(arguments.get("entity_id") or arguments.get("vehicle_id"))
    query = select(Vehicle).options(
        selectinload(Vehicle.schedule),
        selectinload(Vehicle.owner).selectinload(Person.schedule),
    )
    if vehicle_id:
        return await session.scalar(query.where(Vehicle.id == vehicle_id))

    registration_number = str(arguments.get("registration_number") or "").strip()
    if registration_number:
        normalized = normalize_registration_number(registration_number)
        return await session.scalar(query.where(Vehicle.registration_number == normalized))
    return None


async def _load_vehicle_with_schedule(session, vehicle_id: UUID) -> Vehicle | None:
    return await session.scalar(
        select(Vehicle)
        .options(
            selectinload(Vehicle.schedule),
            selectinload(Vehicle.owner).selectinload(Person.schedule),
        )
        .execution_options(populate_existing=True)
        .where(Vehicle.id == vehicle_id)
    )


async def _load_person_with_schedule(session, person_id: UUID) -> Person | None:
    return await session.scalar(
        select(Person)
        .options(selectinload(Person.schedule), selectinload(Person.group))
        .execution_options(populate_existing=True)
        .where(Person.id == person_id)
    )


def _serialize_schedule_for_agent(schedule: Schedule) -> dict[str, Any]:
    time_blocks = normalize_time_blocks(schedule.time_blocks)
    return {
        "id": str(schedule.id),
        "name": schedule.name,
        "description": schedule.description,
        "time_blocks": time_blocks,
        "summary": _schedule_summary(time_blocks),
        "created_at": _agent_datetime_iso(schedule.created_at),
        "created_at_display": _agent_datetime_display(schedule.created_at),
        "updated_at": _agent_datetime_iso(schedule.updated_at),
        "updated_at_display": _agent_datetime_display(schedule.updated_at),
    }


def _serialize_person_schedule_target(person: Person) -> dict[str, Any]:
    return {
        "id": str(person.id),
        "name": person.display_name,
        "group": person.group.name if person.group else None,
        "schedule_id": str(person.schedule_id) if person.schedule_id else None,
        "schedule_name": person.schedule.name if person.schedule else None,
        "is_active": person.is_active,
    }


def _serialize_vehicle_schedule_target(vehicle: Vehicle) -> dict[str, Any]:
    owner_schedule_id = vehicle.owner.schedule_id if vehicle.owner else None
    owner_schedule_name = vehicle.owner.schedule.name if vehicle.owner and vehicle.owner.schedule else None
    return {
        "id": str(vehicle.id),
        "registration_number": vehicle.registration_number,
        "owner": vehicle.owner.display_name if vehicle.owner else None,
        "schedule_id": str(vehicle.schedule_id) if vehicle.schedule_id else None,
        "schedule_name": vehicle.schedule.name if vehicle.schedule else None,
        "inherits_from_owner": vehicle.schedule_id is None and owner_schedule_id is not None,
        "owner_schedule_id": str(owner_schedule_id) if owner_schedule_id else None,
        "owner_schedule_name": owner_schedule_name,
        "is_active": vehicle.is_active,
    }


def _schedule_device_payload(device: AccessDeviceEntity) -> dict[str, Any]:
    return {**cover_entity_state_payload({
        "entity_id": device.key, "name": device.name, "enabled": device.enabled,
        "schedule_id": device.schedule_id,
    }), "kind": device.kind}


async def _resolve_schedule_device(arguments: dict[str, Any], entity_type: str) -> AccessDeviceEntity | None:
    requested_id = str(arguments.get("entity_id") or "").strip()
    requested_name = _normalize(arguments.get("entity_name") or arguments.get("name") or arguments.get("target"))
    matches: list[tuple[int, AccessDeviceEntity]] = []
    for device in await get_access_device_service().list_devices():
        if entity_type not in {"door", device.kind}:
            continue
        if requested_id:
            if device.key == requested_id:
                return device
            continue
        score = _cover_target_match_score(requested_name, device.name, device.key, device.kind)
        if score >= 2:
            matches.append((score, device))
    matches.sort(key=lambda match: match[0], reverse=True)
    if matches and (len(matches) == 1 or matches[0][0] > matches[1][0]):
        return matches[0][1]
    return None


async def _schedule_door_targets(*, entity_type: str, search: str) -> list[dict[str, Any]]:
    schedule_names = await _schedule_name_map()
    return [
        {**_schedule_device_payload(device), "schedule_name": schedule_names.get(str(device.schedule_id))}
        for device in await get_access_device_service().list_devices()
        if entity_type in {"", "all", "door", device.kind}
        and (not search or search in f"{device.key} {device.name}".lower())
    ]


async def _schedule_name_map() -> dict[str, str]:
    async with AsyncSessionLocal() as session:
        schedules = (await session.scalars(select(Schedule))).all()
    return {str(schedule.id): schedule.name for schedule in schedules}


def _schedule_summary(time_blocks: dict[str, list[dict[str, str]]]) -> str:
    selected_slots = 0
    active_days = 0
    for intervals in time_blocks.values():
        day_slots = 0
        for interval in intervals:
            start = _parse_schedule_minute(interval["start"])
            end = _parse_schedule_minute(interval["end"])
            day_slots += max(0, (end - start) // 30)
        if day_slots:
            active_days += 1
            selected_slots += day_slots
    if not selected_slots:
        return "No allowed time"
    if selected_slots == 48 * 7:
        return "24/7"
    hours = selected_slots / 2
    display_hours = int(hours) if hours.is_integer() else round(hours, 1)
    return f"{display_hours}h across {active_days} day{'s' if active_days != 1 else ''}"


def _schedule_has_allowed_time(time_blocks: dict[str, list[dict[str, str]]]) -> bool:
    return any(bool(intervals) for intervals in time_blocks.values())


def _time_blocks_from_agent_arguments(arguments: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    raw_time_blocks = arguments.get("time_blocks")
    natural_text = _natural_schedule_text_from_arguments(arguments)
    if natural_text:
        parsed = _parse_natural_schedule_time_blocks(natural_text)
        if parsed:
            return normalize_time_blocks(parsed)
    return normalize_time_blocks(raw_time_blocks)


def _natural_schedule_text_from_arguments(arguments: dict[str, Any]) -> str:
    return " ".join(
        str(arguments.get(key) or "").strip()
        for key in ("time_description", "description")
        if str(arguments.get(key) or "").strip()
    ).strip()


def _parse_natural_schedule_time_blocks(text: str) -> dict[str, list[dict[str, str]]] | None:
    lower = text.lower()
    if any(token in lower for token in ["24/7", "24-7", "24 hours", "all day every day"]):
        return {str(day): [{"start": "00:00", "end": "24:00"}] for day in range(7)}

    days = _natural_schedule_days(lower)
    time_range = _natural_schedule_time_range(lower)
    if not days or not time_range:
        return None

    start, end = time_range
    blocks: dict[str, list[dict[str, str]]] = {str(day): [] for day in range(7)}
    for day in days:
        blocks[str(day)].append({"start": start, "end": end})
    return blocks


def _natural_schedule_days(lower: str) -> list[int]:
    if any(phrase in lower for phrase in ["weekday", "week day", "workday", "work day"]):
        return list(range(5))
    if any(phrase in lower for phrase in ["weekend", "saturday and sunday", "sat and sun"]):
        return [5, 6]
    if any(phrase in lower for phrase in ["every day", "daily", "all week", "each day", "mon-sun", "monday to sunday"]):
        return list(range(7))

    range_match = re.search(
        rf"\b({NATURAL_SCHEDULE_DAY_PATTERN})\b"
        r"\s*(?:-|to|through|until|thru)\s*"
        rf"\b({NATURAL_SCHEDULE_DAY_PATTERN})\b",
        lower,
    )
    if range_match:
        start = _natural_schedule_day_index(range_match.group(1))
        end = _natural_schedule_day_index(range_match.group(2))
        if start is not None and end is not None:
            if start <= end:
                return list(range(start, end + 1))
            return list(range(start, 7)) + list(range(end + 1))

    days: list[int] = []
    for token in re.findall(rf"\b({NATURAL_SCHEDULE_DAY_PATTERN})\b", lower):
        day = _natural_schedule_day_index(token)
        if day is not None and day not in days:
            days.append(day)
    return days


def _natural_schedule_day_index(value: str) -> int | None:
    normalized = re.sub(r"(?:'s|s)$", "", value.lower())
    if normalized == "wed":
        normalized = "wednesday"
    return NATURAL_SCHEDULE_DAY_ALIASES.get(normalized, NATURAL_SCHEDULE_DAY_ALIASES.get(normalized[:3]))


def _natural_schedule_time_range(lower: str) -> tuple[str, str] | None:
    match = re.search(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:-|to|until|through|thru)\s*"
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
        lower,
    )
    if not match:
        return None

    start = _natural_schedule_minute(match.group(1), match.group(2), match.group(3))
    end = _natural_schedule_minute(match.group(4), match.group(5), match.group(6))
    if start is None or end is None:
        return None
    if end <= start and not match.group(3) and not match.group(6) and int(match.group(4)) <= 12:
        end += 12 * 60
    if start < 0 or end > 24 * 60 or end <= start:
        return None
    if start % 30 or end % 30:
        return None
    return _format_natural_schedule_minute(start), _format_natural_schedule_minute(end)


def _natural_schedule_minute(hour_text: str, minute_text: str | None, meridiem: str | None) -> int | None:
    hour = int(hour_text)
    minute = int(minute_text or "0")
    if minute not in {0, 30}:
        return None
    if meridiem:
        if hour < 1 or hour > 12:
            return None
        if meridiem == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
    if hour < 0 or hour > 24:
        return None
    return hour * 60 + minute


def _format_natural_schedule_minute(minute: int) -> str:
    if minute == 24 * 60:
        return "24:00"
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _parse_schedule_minute(value: str) -> int:
    if value in {"24:00", "23:59"}:
        return 24 * 60
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


async def override_schedule(arguments: dict[str, Any]) -> dict[str, Any]:
    person_id = _uuid_from_value(arguments.get("person_id"))
    if not person_id:
        return {
            "created": False,
            "requires_details": True,
            "detail": "A person_id from actor context or resolve_human_entity is required.",
        }
    config = await get_runtime_config()
    try:
        starts_at = _parse_agent_datetime(arguments.get("time"), config.site_timezone)
    except (TypeError, ValueError) as exc:
        return {"created": False, "error": f"Invalid override time: {exc}"}
    duration_minutes = _bounded_int(arguments.get("duration_minutes"), default=60, minimum=1, maximum=1440)
    ends_at = starts_at + timedelta(minutes=duration_minutes)
    reason = str(arguments.get("reason") or "Temporary access override from Alfred").strip()

    async with AsyncSessionLocal() as session:
        person = await _load_person_with_schedule(session, person_id)
        if not person:
            return {"created": False, "error": "Person not found."}
        if not bool(arguments.get("confirm")):
            return {
                "created": False,
                "requires_confirmation": True,
                "confirmation_field": "confirm",
                "target": person.display_name,
                "person_id": str(person.id),
                "starts_at": _agent_datetime_iso(starts_at, config.site_timezone),
                "starts_at_display": _agent_datetime_display(starts_at, config.site_timezone),
                "ends_at": _agent_datetime_iso(ends_at, config.site_timezone),
                "ends_at_display": _agent_datetime_display(ends_at, config.site_timezone),
                "duration_minutes": duration_minutes,
                "detail": f"Create a temporary access override for {person.display_name}?",
            }

        try:
            override = await create_schedule_override(
                session, person_id=person.id, starts_at=starts_at,
                duration_minutes=duration_minutes, reason=reason,
                user=await _schedule_actor(session), source="alfred",
            )
        except ScheduleOperationError as exc:
            return _schedule_operation_error(exc, "created")

    await publish_schedule_override_created({
        "override_id": str(override.id),
        "person_id": str(person.id),
        "person": person.display_name,
        "starts_at": _agent_datetime_iso(starts_at, config.site_timezone),
        "ends_at": _agent_datetime_iso(ends_at, config.site_timezone),
        "source": "alfred",
    })
    return {
        "created": True,
        "override_id": str(override.id),
        "person_id": str(person.id),
        "person": person.display_name,
        "starts_at": _agent_datetime_iso(starts_at, config.site_timezone),
        "starts_at_display": _agent_datetime_display(starts_at, config.site_timezone),
        "ends_at": _agent_datetime_iso(ends_at, config.site_timezone),
        "ends_at_display": _agent_datetime_display(ends_at, config.site_timezone),
        "duration_minutes": duration_minutes,
        "reason": reason,
    }


async def query_schedules(arguments: dict[str, Any]) -> dict[str, Any]:
    search = _normalize(arguments.get("search"))
    include_dependencies = bool(arguments.get("include_dependencies"))
    async with AsyncSessionLocal() as session:
        schedules = (await session.scalars(select(Schedule).order_by(Schedule.name))).all()
        records: list[dict[str, Any]] = []
        for schedule in schedules:
            serialized = _serialize_schedule_for_agent(schedule)
            if search and search not in f"{schedule.name} {schedule.description or ''}".lower():
                continue
            if include_dependencies:
                dependencies = await schedule_dependencies(session, schedule.id)
                serialized["dependency_counts"] = {
                    key: len(rows)
                    for key, rows in dependencies.items()
                }
            records.append(serialized)
    return {"schedules": records, "count": len(records)}


async def get_schedule(arguments: dict[str, Any]) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        schedule = await _resolve_schedule(session, arguments)
        if not schedule:
            return {"error": "Schedule not found."}
        dependencies = await schedule_dependencies(session, schedule.id)
        return {
            "schedule": _serialize_schedule_for_agent(schedule),
            "dependencies": dependencies,
        }


async def _schedule_actor(session) -> User:
    user_id = _uuid_from_value(get_chat_tool_context().get("user_id"))
    user = await session.get(User, user_id) if user_id else None
    if user is None:
        raise ScheduleOperationError("forbidden", "Admin access required")
    return user


def _schedule_operation_error(exc: ScheduleOperationError, outcome: str) -> dict[str, Any]:
    result: dict[str, Any] = {outcome: False, "error": str(exc).rstrip(".") + "."}
    if exc.dependencies:
        result["dependencies"] = exc.dependencies
    return result


async def create_schedule(arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        values = schedule_operations.validate_schedule_values({
            "name": arguments.get("name"),
            "description": arguments.get("description"),
            "time_blocks": _time_blocks_from_agent_arguments(arguments),
        })
    except (TypeError, ValueError) as exc:
        return {"created": False, "error": str(exc)}
    if not _schedule_has_allowed_time(values.time_blocks):
        return {
            "created": False,
            "requires_details": True,
            "detail": "I need at least one allowed day and time before I create a schedule.",
        }
    if not bool(arguments.get("confirm")):
        return {
            "created": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": values.name,
            "detail": f"Create schedule {values.name}?",
        }

    async with AsyncSessionLocal() as session:
        try:
            schedule = await schedule_operations.create_schedule(
                session, values, user=await _schedule_actor(session), source="alfred",
            )
        except ScheduleOperationError as exc:
            result = _schedule_operation_error(exc, "created")
            if exc.code == "schedule_exists":
                result.update(error_code="schedule_exists", schedule_name=values.name)
            return result
        return {"created": True, "schedule": _serialize_schedule_for_agent(schedule)}


async def update_schedule(arguments: dict[str, Any]) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        schedule = await _resolve_schedule(session, arguments)
        if not schedule:
            return {"updated": False, "error": "Schedule not found."}

        changes: dict[str, Any] = {key: arguments[key] for key in ("name", "description") if key in arguments}
        try:
            if (
                "time_blocks" in arguments
                or bool(arguments.get("time_description"))
                or _parse_natural_schedule_time_blocks(_natural_schedule_text_from_arguments(arguments)) is not None
            ):
                changes["time_blocks"] = _time_blocks_from_agent_arguments(arguments)
            # Validate the preview without changing the ORM object. The operation
            # merges again against the locked current record at execution time.
            schedule_operations.validate_schedule_values({
                **schedule_operations.schedule_audit_snapshot(schedule), **changes,
            })
        except (TypeError, ValueError) as exc:
            return {"updated": False, "error": str(exc)}

        if not bool(arguments.get("confirm")):
            return {
                "updated": False,
                "requires_confirmation": True,
                "confirmation_field": "confirm",
                "target": schedule.name,
                "schedule_name": schedule.name,
                "detail": f"Update the {schedule.name} schedule?",
            }

        try:
            schedule = await schedule_operations.update_schedule(
                session, schedule.id, changes, user=await _schedule_actor(session), source="alfred",
            )
        except ScheduleOperationError as exc:
            return _schedule_operation_error(exc, "updated")
        return {"updated": True, "schedule": _serialize_schedule_for_agent(schedule)}


async def delete_schedule(arguments: dict[str, Any]) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        schedule = await _resolve_schedule(session, arguments)
        if not schedule:
            return {"deleted": False, "error": "Schedule not found."}
        dependencies = await schedule_dependencies(session, schedule.id)
        if any(dependencies.values()):
            return {
                "deleted": False,
                "error": "Schedule is currently assigned and cannot be deleted.",
                "schedule_name": schedule.name,
                "dependencies": dependencies,
            }
        serialized = _serialize_schedule_for_agent(schedule)
        if not bool(arguments.get("confirm")):
            return {
                "deleted": False,
                "requires_confirmation": True,
                "confirmation_field": "confirm",
                "schedule_name": schedule.name,
                "schedule": serialized,
                "detail": f"Delete the {schedule.name} schedule? This cannot be undone.",
            }
        try:
            await schedule_operations.delete_schedule(
                session, schedule.id, user=await _schedule_actor(session), source="alfred",
            )
        except ScheduleOperationError as exc:
            return {**_schedule_operation_error(exc, "deleted"), "schedule_name": serialized["name"]}
        return {"deleted": True, "schedule": serialized}


async def query_schedule_targets(arguments: dict[str, Any]) -> dict[str, Any]:
    entity_type = _normalize(arguments.get("entity_type") or "all")
    search = _normalize(arguments.get("search"))
    limit = _bounded_int(arguments.get("limit"), default=25, minimum=1, maximum=100)
    include_people = entity_type in {"", "all", "person"}
    include_vehicles = entity_type in {"", "all", "vehicle"}
    include_doors = entity_type in {"", "all", "gate", "garage_door", "door"}

    async with AsyncSessionLocal() as session:
        people: list[dict[str, Any]] = []
        vehicles: list[dict[str, Any]] = []
        if include_people:
            person_rows = (
                await session.scalars(
                    select(Person)
                    .options(selectinload(Person.schedule), selectinload(Person.group))
                    .order_by(Person.display_name)
                )
            ).all()
            people = [
                _serialize_person_schedule_target(person)
                for person in person_rows
                if not search or search in f"{person.display_name} {person.group.name if person.group else ''}".lower()
            ][:limit]
        if include_vehicles:
            vehicle_rows = (
                await session.scalars(
                    select(Vehicle)
                    .options(
                        selectinload(Vehicle.schedule),
                        selectinload(Vehicle.owner).selectinload(Person.schedule),
                    )
                    .order_by(Vehicle.registration_number)
                )
            ).all()
            vehicles = [
                _serialize_vehicle_schedule_target(vehicle)
                for vehicle in vehicle_rows
                if not search or search in f"{vehicle.registration_number} {vehicle.owner.display_name if vehicle.owner else ''}".lower()
            ][:limit]

    doors = (await _schedule_door_targets(entity_type=entity_type, search=search))[:limit] if include_doors else []
    return {
        "people": people,
        "vehicles": vehicles,
        "doors": doors,
        "counts": {
            "people": len(people),
            "vehicles": len(vehicles),
            "doors": len(doors),
        },
    }


async def assign_schedule_to_entity(arguments: dict[str, Any]) -> dict[str, Any]:
    entity_type = _normalize(arguments.get("entity_type"))
    clear_schedule = bool(arguments.get("clear_schedule"))

    async with AsyncSessionLocal() as session:
        schedule = None if clear_schedule else await _resolve_schedule(session, arguments)
        if not clear_schedule and not schedule:
            return {"assigned": False, "error": "Schedule not found. Supply schedule_id or schedule_name, or set clear_schedule=true."}
        if not bool(arguments.get("confirm")):
            target = str(
                arguments.get("entity_name")
                or arguments.get("registration_number")
                or arguments.get("entity_id")
                or entity_type
                or "schedule target"
            ).strip()
            schedule_label = "clear the schedule" if clear_schedule else f"assign {schedule.name if schedule else 'the schedule'}"
            return {
                "assigned": False,
                "requires_confirmation": True,
                "confirmation_field": "confirm",
                "target": target,
                "entity_type": entity_type,
                "schedule_name": schedule.name if schedule else None,
                "detail": f"Confirm {schedule_label} for {target}?",
            }

        if entity_type == "person":
            person = await _resolve_person(session, arguments)
            if not person:
                return {"assigned": False, "error": "Person not found."}
            try:
                await set_schedule_assignment(session, person, schedule.id if schedule else None,
                                              user=await _schedule_actor(session), source="alfred")
                await session.commit()
            except ScheduleOperationError as exc:
                return _schedule_operation_error(exc, "assigned")
            refreshed = await _load_person_with_schedule(session, person.id)
            return {
                "assigned": True,
                "entity_type": "person",
                "person": _serialize_person_schedule_target(refreshed or person),
            }

        if entity_type == "vehicle":
            vehicle = await _resolve_vehicle(session, arguments)
            if not vehicle:
                return {"assigned": False, "error": "Vehicle not found."}
            try:
                await set_schedule_assignment(session, vehicle, schedule.id if schedule else None,
                                              user=await _schedule_actor(session), source="alfred")
                await session.commit()
            except ScheduleOperationError as exc:
                return _schedule_operation_error(exc, "assigned")
            refreshed = await _load_vehicle_with_schedule(session, vehicle.id)
            return {
                "assigned": True,
                "entity_type": "vehicle",
                "vehicle": _serialize_vehicle_schedule_target(refreshed or vehicle),
                "inheritance": "inherits owner schedule when schedule_id is null",
            }

        if entity_type in {"gate", "garage_door", "door"}:
            device = await _resolve_schedule_device(arguments, entity_type)
            if device is None:
                return {"assigned": False, "error": "Door/gate target not found."}
            try:
                updated_device = await get_access_device_service().assign_schedule(
                    device.key, str(schedule.id) if schedule else None,
                    user=await _schedule_actor(session), source="alfred",
                )
            except (ScheduleOperationError, LookupError) as exc:
                return {"assigned": False, "error": str(exc)}
            return {"assigned": True, "entity_type": updated_device.kind, "door": _schedule_device_payload(updated_device)}

    return {"assigned": False, "error": "entity_type must be person, vehicle, gate, garage_door, or door."}


async def verify_schedule_access(arguments: dict[str, Any]) -> dict[str, Any]:
    entity_type = _normalize(arguments.get("entity_type") or "schedule")
    config = await get_runtime_config()
    try:
        occurred_at = _parse_agent_datetime(arguments.get("at"), config.site_timezone)
    except (TypeError, ValueError) as exc:
        return {"verified": False, "error": f"Invalid at datetime: {exc}"}

    async with AsyncSessionLocal() as session:
        if entity_type == "schedule":
            schedule = await _resolve_schedule(session, arguments)
            if not schedule:
                return {"verified": False, "error": "Schedule not found."}
            allowed = schedule_allows_at(schedule, occurred_at, config.site_timezone)
            payload = {
                "verified": True,
                "allowed": allowed,
                "source": "schedule",
                "checked_at": _agent_datetime_iso(occurred_at, config.site_timezone),
                "checked_at_display": _agent_datetime_display(occurred_at, config.site_timezone),
                "timezone": config.site_timezone,
                "schedule": _serialize_schedule_for_agent(schedule),
                "reason": f"{schedule.name} allows this time." if allowed else f"{schedule.name} does not allow this time.",
            }
            payload["answer_artifacts"] = _schedule_answer_artifacts(payload, subject=schedule.name)
            return payload

        if entity_type == "person":
            person = await _resolve_person(session, arguments)
            if not person:
                return {"verified": False, "error": "Person not found."}
            evaluation = await evaluate_person_schedule(
                session,
                person,
                occurred_at,
                timezone_name=config.site_timezone,
                default_policy=config.schedule_default_policy,
            )
            payload = {
                "verified": True,
                "entity_type": "person",
                "person": person.display_name,
                "allowed": evaluation.allowed,
                "source": evaluation.source,
                "schedule_id": str(evaluation.schedule_id) if evaluation.schedule_id else None,
                "schedule_name": evaluation.schedule_name,
                "override_id": str(evaluation.override_id) if evaluation.override_id else None,
                "override_ends_at": _agent_datetime_iso(evaluation.override_ends_at, config.site_timezone) if evaluation.override_ends_at else None,
                "checked_at": _agent_datetime_iso(occurred_at, config.site_timezone),
                "checked_at_display": _agent_datetime_display(occurred_at, config.site_timezone),
                "timezone": config.site_timezone,
                "reason": evaluation.reason,
            }
            payload["answer_artifacts"] = _schedule_answer_artifacts(payload, subject=person.display_name)
            return payload

        if entity_type == "vehicle":
            vehicle = await _resolve_vehicle(session, arguments)
            if not vehicle:
                return {"verified": False, "error": "Vehicle not found."}
            evaluation = await evaluate_vehicle_schedule(
                session,
                vehicle,
                occurred_at,
                timezone_name=config.site_timezone,
                default_policy=config.schedule_default_policy,
            )
            payload = {
                "verified": True,
                "entity_type": "vehicle",
                "registration_number": vehicle.registration_number,
                "owner": vehicle.owner.display_name if vehicle.owner else None,
                "allowed": evaluation.allowed,
                "source": evaluation.source,
                "schedule_id": str(evaluation.schedule_id) if evaluation.schedule_id else None,
                "schedule_name": evaluation.schedule_name,
                "override_id": str(evaluation.override_id) if evaluation.override_id else None,
                "override_ends_at": _agent_datetime_iso(evaluation.override_ends_at, config.site_timezone) if evaluation.override_ends_at else None,
                "checked_at": _agent_datetime_iso(occurred_at, config.site_timezone),
                "checked_at_display": _agent_datetime_display(occurred_at, config.site_timezone),
                "timezone": config.site_timezone,
                "reason": evaluation.reason,
            }
            payload["answer_artifacts"] = _schedule_answer_artifacts(payload, subject=vehicle.registration_number)
            return payload

    if entity_type in {"gate", "garage_door", "door"}:
        door = await _resolve_schedule_device(arguments, entity_type)
        if not door:
            return {"verified": False, "error": "Door/gate target not found."}
        async with AsyncSessionLocal() as session:
            evaluation = await evaluate_schedule_id(
                session,
                door.schedule_id,
                occurred_at,
                timezone_name=config.site_timezone,
                default_policy=config.schedule_default_policy,
                source=door.kind,
            )
        payload = {
            "verified": True,
            "entity_type": door.kind,
            "entity_id": door.key,
            "name": door.name,
            "allowed": evaluation.allowed,
            "source": evaluation.source,
            "schedule_id": str(evaluation.schedule_id) if evaluation.schedule_id else None,
            "schedule_name": evaluation.schedule_name,
            "checked_at": _agent_datetime_iso(occurred_at, config.site_timezone),
            "checked_at_display": _agent_datetime_display(occurred_at, config.site_timezone),
            "timezone": config.site_timezone,
            "reason": evaluation.reason,
        }
        payload["answer_artifacts"] = _schedule_answer_artifacts(payload, subject=door.name)
        return payload

    return {"verified": False, "error": "entity_type must be schedule, person, vehicle, gate, garage_door, or door."}
