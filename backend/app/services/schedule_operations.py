"""Confirmed schedule mutations shared by API and Alfred adapters.

The adapter owns confirmation. Each operation owns validation, its transaction,
and the durable audit; it never calls hardware or publishes notifications.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Schedule, User
from app.models.enums import UserRole
from app.services.schedules import empty_time_blocks, normalize_time_blocks, schedule_dependencies
from app.services.telemetry import (
    TELEMETRY_CATEGORY_CRUD,
    actor_from_user,
    audit_diff,
    write_audit_log,
)


class ScheduleOperationError(ValueError):
    def __init__(self, code: str, message: str, *, dependencies: dict | None = None):
        super().__init__(message)
        self.code = code
        self.dependencies = dependencies


class ScheduleValues(BaseModel):
    name: str = Field(max_length=120)
    description: str | None = None
    time_blocks: dict[str, list[dict[str, str]]] = Field(default_factory=empty_time_blocks)

    @field_validator("name", mode="before")
    @classmethod
    def valid_name(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Schedule name is required.")
        return value.strip()

    @field_validator("description")
    @classmethod
    def trimmed_description(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("time_blocks", mode="before")
    @classmethod
    def canonical_time_blocks(cls, value: Any) -> dict[str, list[dict[str, str]]]:
        try:
            return normalize_time_blocks(value)
        except (AttributeError, TypeError) as exc:
            raise ValueError("Schedule time blocks must contain start/end intervals.") from exc


def validate_schedule_values(values: dict[str, Any]) -> ScheduleValues:
    try:
        return ScheduleValues.model_validate(values)
    except ValidationError as exc:
        message = exc.errors(include_input=False)[0]["msg"].removeprefix("Value error, ")
        raise ScheduleOperationError("invalid_schedule", message) from exc


def schedule_audit_snapshot(schedule: Schedule) -> dict[str, Any]:
    return {
        "id": str(schedule.id),
        "name": schedule.name,
        "description": schedule.description,
        "time_blocks": normalize_time_blocks(schedule.time_blocks),
    }


def require_schedule_admin(user: User | None) -> None:
    if user is None or not user.id or not user.is_active or user.role != UserRole.ADMIN:
        raise ScheduleOperationError("forbidden", "Admin access required")


async def _locked_schedule(session: AsyncSession, schedule_id: uuid.UUID) -> Schedule:
    schedule = await session.scalar(
        select(Schedule).where(Schedule.id == schedule_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if schedule is None:
        raise ScheduleOperationError("schedule_not_found", "Schedule not found")
    return schedule


async def _commit_change(
    session: AsyncSession, schedule: Schedule, *, action: str, before: dict[str, Any],
    user: User, source: Literal["api", "alfred"], deleting: bool = False,
) -> None:
    try:
        # Flush first to obtain generated identity/timestamps; audit failure still
        # rolls the transaction back, including an already flushed schedule.
        if not deleting:
            await session.flush()
            await session.refresh(schedule)
        after = {} if deleting else schedule_audit_snapshot(schedule)
        diff = audit_diff(before, after) if action == "schedule.update" else {"old": before, "new": after}
        await write_audit_log(
            session, category=TELEMETRY_CATEGORY_CRUD, action=action,
            actor=actor_from_user(user), actor_user_id=user.id,
            target_entity="Schedule", target_id=schedule.id, target_label=schedule.name,
            diff=diff, metadata={"source": source},
        )
        if deleting:
            await session.delete(schedule)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        cause = getattr(exc.orig, "__cause__", None)
        if getattr(cause, "constraint_name", None) in {"ix_schedules_name", "schedules_name_key"}:
            raise ScheduleOperationError("schedule_exists", "Schedule already exists") from exc
        raise
    except Exception:
        await session.rollback()
        raise


async def create_schedule(
    session: AsyncSession, values: ScheduleValues, *, user: User, source: Literal["api", "alfred"],
) -> Schedule:
    require_schedule_admin(user)
    schedule = Schedule(**values.model_dump())
    session.add(schedule)
    await _commit_change(session, schedule, action="schedule.create", before={}, user=user, source=source)
    return schedule


async def update_schedule(
    session: AsyncSession, schedule_id: uuid.UUID, changes: dict[str, Any], *,
    user: User, source: Literal["api", "alfred"],
) -> Schedule:
    require_schedule_admin(user)
    schedule = await _locked_schedule(session, schedule_id)
    before = schedule_audit_snapshot(schedule)
    values = validate_schedule_values({**before, **changes})
    for key, value in values.model_dump().items():
        setattr(schedule, key, value)
    await _commit_change(session, schedule, action="schedule.update", before=before, user=user, source=source)
    return schedule


async def delete_schedule(
    session: AsyncSession, schedule_id: uuid.UUID, *, user: User, source: Literal["api", "alfred"],
) -> Schedule:
    require_schedule_admin(user)
    schedule = await _locked_schedule(session, schedule_id)
    dependencies = await schedule_dependencies(session, schedule_id)
    if any(dependencies.values()):
        raise ScheduleOperationError("schedule_in_use", "Schedule is currently assigned and cannot be deleted.",
                                     dependencies=dependencies)
    await _commit_change(session, schedule, action="schedule.delete", before=schedule_audit_snapshot(schedule),
                         user=user, source=source, deleting=True)
    return schedule
