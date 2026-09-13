"""Schedule assignment rules shared by directory, devices, and Alfred.

Stage the change and its audit in the caller's aggregate transaction. No commit
occurs here: a failed person/device edit must also roll back its assignment.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccessDevice, Person, Schedule, User, Vehicle
from app.services.schedule_operations import ScheduleOperationError, require_schedule_admin
from app.services.telemetry import TELEMETRY_CATEGORY_CRUD, actor_from_user, write_audit_log


async def set_schedule_assignment(
    session: AsyncSession, target: Person | Vehicle | AccessDevice, schedule_id: Any, *,
    user: User, source: Literal["api", "alfred"],
) -> None:
    require_schedule_admin(user)
    try:
        reference = uuid.UUID(str(schedule_id)) if schedule_id not in (None, "") else None
    except (ValueError, TypeError) as exc:
        raise ScheduleOperationError("invalid_schedule", "Invalid schedule ID") from exc
    with session.no_autoflush:
        # All assignment writers lock the reference before the target. A delete
        # holding the schedule lock cannot race a newly committed assignment.
        if reference is not None:
            schedule = await session.scalar(select(Schedule).where(Schedule.id == reference).with_for_update())
            if schedule is None:
                raise ScheduleOperationError("schedule_not_found", "Schedule not found")
        if inspect(target).persistent:
            await session.refresh(target, ["schedule_id"], with_for_update=True)
    previous = target.schedule_id
    if previous == reference:
        return
    target.schedule_id = reference
    await session.flush()
    label = target.display_name if isinstance(target, Person) else (
        target.registration_number if isinstance(target, Vehicle) else target.name
    )
    await write_audit_log(
        session, category=TELEMETRY_CATEGORY_CRUD, action="schedule.assign",
        actor=actor_from_user(user), actor_user_id=user.id,
        target_entity=type(target).__name__, target_id=target.id, target_label=label,
        diff={"old": {"schedule_id": str(previous) if previous else None},
              "new": {"schedule_id": str(reference) if reference else None}},
        metadata={"source": source},
    )
