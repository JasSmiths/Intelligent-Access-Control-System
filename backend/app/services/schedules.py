import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Person, Schedule, ScheduleOverride, Vehicle
from app.services.settings import get_runtime_config

MINUTES_PER_SLOT = 30
SLOTS_PER_DAY = 48
MINUTES_PER_DAY = 24 * 60
WEEKDAY_KEYS = tuple(str(day) for day in range(7))


@dataclass(frozen=True)
class ScheduleEvaluation:
    allowed: bool
    source: str
    schedule_id: uuid.UUID | None = None
    schedule_name: str | None = None
    reason: str = ""
    override_id: uuid.UUID | None = None
    override_ends_at: datetime | None = None


def empty_time_blocks() -> dict[str, list[dict[str, str]]]:
    return {day: [] for day in WEEKDAY_KEYS}


def normalize_time_blocks(value: Any) -> dict[str, list[dict[str, str]]]:
    """Return canonical Monday-first 30-minute blocks merged into intervals."""

    slots_by_day: dict[int, set[int]] = {day: set() for day in range(7)}
    for day, intervals in _iter_raw_intervals(value):
        if day < 0 or day > 6:
            raise ValueError("Schedule days must be numbered 0 (Monday) through 6 (Sunday).")
        for interval in intervals:
            start = _parse_minute(interval.get("start"), allow_24=False)
            end = _parse_minute(interval.get("end"), allow_24=True)
            if start >= end:
                raise ValueError("Schedule time blocks must end after they start.")
            if start % MINUTES_PER_SLOT or end % MINUTES_PER_SLOT:
                raise ValueError("Schedule time blocks must align to 30-minute increments.")
            for slot in range(start // MINUTES_PER_SLOT, end // MINUTES_PER_SLOT):
                if slot < 0 or slot >= SLOTS_PER_DAY:
                    raise ValueError("Schedule time block is outside the supported day range.")
                slots_by_day[day].add(slot)

    return {
        str(day): _slots_to_intervals(slots_by_day[day])
        for day in range(7)
    }


def schedule_allows_at(schedule: Schedule, occurred_at: datetime, timezone_name: str) -> bool:
    local_at = occurred_at.astimezone(ZoneInfo(timezone_name))
    minute = local_at.hour * 60 + local_at.minute
    blocks = normalize_time_blocks(schedule.time_blocks)
    for interval in blocks.get(str(local_at.weekday()), []):
        if _parse_minute(interval["start"], allow_24=False) <= minute < _parse_minute(interval["end"], allow_24=True):
            return True
    return False


async def evaluate_vehicle_schedule(
    session: AsyncSession,
    vehicle: Vehicle,
    occurred_at: datetime,
    *,
    timezone_name: str,
    default_policy: str,
) -> ScheduleEvaluation:
    owner = vehicle.owner
    person_id = vehicle.person_id or (owner.id if owner else None)
    if person_id:
        override = await active_schedule_override(
            session,
            person_id=person_id,
            occurred_at=occurred_at,
            vehicle_id=vehicle.id,
        )
        if override:
            return _evaluate_override(override)

    vehicle_schedule = await _schedule_for_id(session, vehicle.schedule_id, vehicle.schedule)
    if vehicle.schedule_id:
        if not vehicle_schedule:
            return ScheduleEvaluation(
                allowed=False,
                source="vehicle",
                schedule_id=vehicle.schedule_id,
                reason="Vehicle schedule was not found.",
            )
        return _evaluate_schedule(vehicle_schedule, occurred_at, timezone_name, source="vehicle")

    owner_schedule = await _schedule_for_id(session, owner.schedule_id, owner.schedule) if owner else None
    if owner and owner.schedule_id:
        if not owner_schedule:
            return ScheduleEvaluation(
                allowed=False,
                source="person",
                schedule_id=owner.schedule_id,
                reason="Owner schedule was not found.",
            )
        return _evaluate_schedule(owner_schedule, occurred_at, timezone_name, source="person")

    return _evaluate_default_policy(default_policy)


async def evaluate_person_schedule(
    session: AsyncSession,
    person: Person,
    occurred_at: datetime,
    *,
    timezone_name: str,
    default_policy: str,
) -> ScheduleEvaluation:
    override = await active_schedule_override(
        session,
        person_id=person.id,
        occurred_at=occurred_at,
    )
    if override:
        return _evaluate_override(override)

    person_schedule = await _schedule_for_id(session, person.schedule_id, person.schedule)
    if person.schedule_id:
        if not person_schedule:
            return ScheduleEvaluation(
                allowed=False,
                source="person",
                schedule_id=person.schedule_id,
                reason="Person schedule was not found.",
            )
        return _evaluate_schedule(person_schedule, occurred_at, timezone_name, source="person")
    return _evaluate_default_policy(default_policy)


async def evaluate_schedule_id(
    session: AsyncSession,
    schedule_id: str | uuid.UUID | None,
    occurred_at: datetime,
    *,
    timezone_name: str,
    default_policy: str,
    source: str,
) -> ScheduleEvaluation:
    if not schedule_id:
        return _evaluate_default_policy(default_policy)
    try:
        parsed_schedule_id = schedule_id if isinstance(schedule_id, uuid.UUID) else uuid.UUID(str(schedule_id))
    except ValueError:
        return ScheduleEvaluation(
            allowed=False,
            source=source,
            reason="Assigned schedule ID is invalid.",
        )
    schedule = await session.get(Schedule, parsed_schedule_id)
    if not schedule:
        return ScheduleEvaluation(
            allowed=False,
            source=source,
            schedule_id=parsed_schedule_id,
            reason="Assigned schedule was not found.",
        )
    return _evaluate_schedule(schedule, occurred_at, timezone_name, source=source)


async def active_schedule_override(
    session: AsyncSession,
    *,
    person_id: uuid.UUID,
    occurred_at: datetime,
    vehicle_id: uuid.UUID | None = None,
) -> ScheduleOverride | None:
    checked_at = occurred_at if occurred_at.tzinfo else occurred_at.replace(tzinfo=UTC)
    query = (
        select(ScheduleOverride)
        .where(
            ScheduleOverride.person_id == person_id,
            ScheduleOverride.is_active.is_(True),
            ScheduleOverride.starts_at <= checked_at,
            ScheduleOverride.ends_at > checked_at,
        )
        .order_by(ScheduleOverride.ends_at.desc(), ScheduleOverride.created_at.desc())
    )
    overrides = (await session.scalars(query)).all()
    for override in overrides:
        if override.vehicle_id is None or (vehicle_id and override.vehicle_id == vehicle_id):
            return override
    return None


async def schedule_dependencies(session: AsyncSession, schedule_id: uuid.UUID) -> dict[str, list[dict[str, str | None]]]:
    people = (
        await session.scalars(
            select(Person).where(Person.schedule_id == schedule_id).order_by(Person.display_name)
        )
    ).all()
    vehicles = (
        await session.scalars(
            select(Vehicle)
            .options(selectinload(Vehicle.owner))
            .where(Vehicle.schedule_id == schedule_id)
            .order_by(Vehicle.registration_number)
        )
    ).all()
    config = await get_runtime_config()
    schedule_id_text = str(schedule_id)
    doors = [
        {
            "id": str(entity.get("entity_id")),
            "name": str(entity.get("name") or entity.get("entity_id")),
            "entity_id": str(entity.get("entity_id")),
            "kind": kind,
        }
        for kind, entities in (
            ("gate", config.home_assistant_gate_entities),
            ("garage_door", config.home_assistant_garage_door_entities),
        )
        for entity in entities
        if str(entity.get("schedule_id") or "") == schedule_id_text
    ]
    return {
        "people": [
            {
                "id": str(person.id),
                "name": person.display_name,
                "kind": "person",
            }
            for person in people
        ],
        "vehicles": [
            {
                "id": str(vehicle.id),
                "name": vehicle.registration_number,
                "registration_number": vehicle.registration_number,
                "owner": vehicle.owner.display_name if vehicle.owner else None,
                "kind": "vehicle",
            }
            for vehicle in vehicles
        ],
        "doors": doors,
    }


async def _schedule_for_id(
    session: AsyncSession,
    schedule_id: uuid.UUID | None,
    loaded_schedule: Schedule | None,
) -> Schedule | None:
    if not schedule_id:
        return None
    return loaded_schedule or await session.get(Schedule, schedule_id)


def _evaluate_schedule(
    schedule: Schedule,
    occurred_at: datetime,
    timezone_name: str,
    *,
    source: str,
) -> ScheduleEvaluation:
    allowed = schedule_allows_at(schedule, occurred_at, timezone_name)
    return ScheduleEvaluation(
        allowed=allowed,
        source=source,
        schedule_id=schedule.id,
        schedule_name=schedule.name,
        reason=f"{schedule.name} allowed this time." if allowed else f"{schedule.name} does not allow this time.",
    )


def _evaluate_override(override: ScheduleOverride) -> ScheduleEvaluation:
    return ScheduleEvaluation(
        allowed=True,
        source="schedule_override",
        reason="A temporary schedule override is active.",
        override_id=override.id,
        override_ends_at=override.ends_at,
    )


def _evaluate_default_policy(default_policy: str) -> ScheduleEvaluation:
    allow = default_policy.strip().lower() != "deny"
    return ScheduleEvaluation(
        allowed=allow,
        source="default",
        reason="No schedule assigned; default policy allowed access."
        if allow
        else "No schedule assigned; default policy denied access.",
    )


def _iter_raw_intervals(value: Any) -> list[tuple[int, list[dict[str, Any]]]]:
    if not value:
        return []
    if isinstance(value, dict) and isinstance(value.get("days"), list):
        return [
            (int(day.get("day")), list(day.get("intervals") or []))
            for day in value["days"]
            if isinstance(day, dict)
        ]
    if isinstance(value, list):
        return [
            (int(day.get("day")), list(day.get("intervals") or []))
            for day in value
            if isinstance(day, dict)
        ]
    if isinstance(value, dict):
        return [
            (int(day), list(intervals or []))
            for day, intervals in value.items()
            if str(day).isdigit()
        ]
    raise ValueError("Schedule time blocks must be an object keyed by weekday.")


def _parse_minute(value: Any, *, allow_24: bool) -> int:
    if value is None:
        raise ValueError("Schedule time blocks require start and end values.")
    text = str(value).strip()
    if allow_24 and text == "23:59":
        return MINUTES_PER_DAY
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError("Schedule times must use HH:MM format.")
    hour = int(parts[0])
    minute = int(parts[1])
    if allow_24 and hour == 24 and minute == 0:
        return MINUTES_PER_DAY
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("Schedule times must be within a single day.")
    return hour * 60 + minute


def _slots_to_intervals(slots: set[int]) -> list[dict[str, str]]:
    if not slots:
        return []
    intervals: list[dict[str, str]] = []
    sorted_slots = sorted(slots)
    start = sorted_slots[0]
    previous = sorted_slots[0]
    for slot in sorted_slots[1:]:
        if slot == previous + 1:
            previous = slot
            continue
        intervals.append(
            {
                "start": _format_minute(start * MINUTES_PER_SLOT),
                "end": _format_minute((previous + 1) * MINUTES_PER_SLOT),
            }
        )
        start = slot
        previous = slot
    intervals.append(
        {
            "start": _format_minute(start * MINUTES_PER_SLOT),
            "end": _format_minute((previous + 1) * MINUTES_PER_SLOT),
        }
    )
    return intervals


def _format_minute(minute: int) -> str:
    if minute == MINUTES_PER_DAY:
        return "24:00"
    return f"{minute // 60:02d}:{minute % 60:02d}"
