import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.confirmations import require_confirmed_action
from app.api.dependencies import admin_user, current_user
from app.db.session import get_db_session
from app.models import Schedule, User
from app.services.schedules import empty_time_blocks, normalize_time_blocks, schedule_dependencies
from app.services.telemetry import (
    TELEMETRY_CATEGORY_CRUD,
    actor_from_user,
    audit_diff,
    write_audit_log,
)

router = APIRouter()


class ScheduleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    time_blocks: dict[str, list[dict[str, str]]] = Field(default_factory=empty_time_blocks)
    confirmation_token: str | None = Field(default=None, max_length=160)


class ScheduleDeleteRequest(BaseModel):
    confirmation_token: str | None = Field(default=None, max_length=160)


class ScheduleResponse(BaseModel):
    id: str
    name: str
    description: str | None
    time_blocks: dict[str, list[dict[str, str]]]
    created_at: str
    updated_at: str


class ScheduleDependenciesResponse(BaseModel):
    people: list[dict[str, str | None]]
    vehicles: list[dict[str, str | None]]
    doors: list[dict[str, str | None]]


def serialize_schedule(schedule: Schedule) -> dict[str, Any]:
    return {
        "id": str(schedule.id),
        "name": schedule.name,
        "description": schedule.description,
        "time_blocks": normalize_time_blocks(schedule.time_blocks),
        "created_at": schedule.created_at.isoformat(),
        "updated_at": schedule.updated_at.isoformat(),
    }


def schedule_audit_snapshot(schedule: Schedule) -> dict[str, Any]:
    return {
        "id": str(schedule.id),
        "name": schedule.name,
        "description": schedule.description,
        "time_blocks": normalize_time_blocks(schedule.time_blocks),
    }


@router.get("", response_model=list[ScheduleResponse])
async def list_schedules(
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[ScheduleResponse]:
    schedules = (await session.scalars(select(Schedule).order_by(Schedule.name))).all()
    return [ScheduleResponse(**serialize_schedule(schedule)) for schedule in schedules]


@router.post("", response_model=ScheduleResponse, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    request: ScheduleRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> ScheduleResponse:
    confirmation_payload = request.model_dump(exclude={"confirmation_token"}, exclude_none=True)
    await require_confirmed_action(
        session,
        user=user,
        action="schedule.create",
        payload=confirmation_payload,
        confirmation_token=request.confirmation_token,
    )
    try:
        time_blocks = normalize_time_blocks(request.time_blocks)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    schedule = Schedule(
        name=request.name.strip(),
        description=request.description.strip() if request.description else None,
        time_blocks=time_blocks,
    )
    session.add(schedule)
    try:
        await session.flush()
        await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_CRUD,
            action="schedule.create",
            actor=actor_from_user(user),
            actor_user_id=user.id,
            target_entity="Schedule",
            target_id=schedule.id,
            target_label=schedule.name,
            diff={"old": {}, "new": schedule_audit_snapshot(schedule)},
        )
        await session.commit()
        await session.refresh(schedule)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Schedule already exists") from exc

    return ScheduleResponse(**serialize_schedule(schedule))


@router.get("/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(
    schedule_id: uuid.UUID,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ScheduleResponse:
    schedule = await session.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    return ScheduleResponse(**serialize_schedule(schedule))


@router.patch("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: uuid.UUID,
    request: ScheduleRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> ScheduleResponse:
    schedule = await session.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    confirmation_payload = request.model_dump(exclude={"confirmation_token"}, exclude_none=True)
    confirmation_payload["schedule_id"] = str(schedule_id)
    await require_confirmed_action(
        session,
        user=user,
        action="schedule.update",
        payload=confirmation_payload,
        confirmation_token=request.confirmation_token,
    )
    before = schedule_audit_snapshot(schedule)
    try:
        schedule.time_blocks = normalize_time_blocks(request.time_blocks)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    schedule.name = request.name.strip()
    schedule.description = request.description.strip() if request.description else None
    try:
        await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_CRUD,
            action="schedule.update",
            actor=actor_from_user(user),
            actor_user_id=user.id,
            target_entity="Schedule",
            target_id=schedule.id,
            target_label=schedule.name,
            diff=audit_diff(before, schedule_audit_snapshot(schedule)),
        )
        await session.commit()
        await session.refresh(schedule)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Schedule already exists") from exc
    return ScheduleResponse(**serialize_schedule(schedule))


@router.get("/{schedule_id}/dependencies", response_model=ScheduleDependenciesResponse)
async def get_schedule_dependencies(
    schedule_id: uuid.UUID,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ScheduleDependenciesResponse:
    schedule = await session.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    dependencies = await schedule_dependencies(session, schedule_id)
    return ScheduleDependenciesResponse(**dependencies)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: uuid.UUID,
    request: ScheduleDeleteRequest | None = Body(default=None),
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    schedule = await session.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    await require_confirmed_action(
        session,
        user=user,
        action="schedule.delete",
        payload={"schedule_id": str(schedule_id)},
        confirmation_token=request.confirmation_token if request else None,
    )

    dependencies = await schedule_dependencies(session, schedule_id)
    if any(dependencies.values()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Schedule is currently in use by {_dependency_summary(dependencies)}.",
        )

    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_CRUD,
        action="schedule.delete",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="Schedule",
        target_id=schedule.id,
        target_label=schedule.name,
        diff={"old": schedule_audit_snapshot(schedule), "new": {}},
    )
    await session.delete(schedule)
    await session.commit()


def _dependency_summary(dependencies: dict[str, list[dict[str, str | None]]]) -> str:
    parts: list[str] = []
    labels = {"people": "people", "vehicles": "vehicles", "doors": "doors"}
    for key, rows in dependencies.items():
        if rows:
            names = ", ".join(str(row.get("name") or row.get("entity_id") or row.get("id")) for row in rows[:4])
            suffix = f" and {len(rows) - 4} more" if len(rows) > 4 else ""
            parts.append(f"{labels.get(key, key)}: {names}{suffix}")
    return "; ".join(parts) or "assigned entities"
