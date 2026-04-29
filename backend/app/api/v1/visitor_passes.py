import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import current_user
from app.core.logging import get_logger
from app.db.session import get_db_session
from app.models import User
from app.models.enums import VisitorPassStatus
from app.services.event_bus import event_bus
from app.services.telemetry import actor_from_user
from app.services.visitor_passes import (
    DEFAULT_WINDOW_MINUTES,
    VisitorPassError,
    get_visitor_pass_service,
    serialize_visitor_pass,
)

router = APIRouter()
logger = get_logger(__name__)


class VisitorPassCreateRequest(BaseModel):
    visitor_name: str = Field(min_length=1, max_length=160)
    expected_time: datetime
    window_minutes: int = Field(default=DEFAULT_WINDOW_MINUTES, ge=1, le=1440)


class VisitorPassUpdateRequest(BaseModel):
    visitor_name: str | None = Field(default=None, min_length=1, max_length=160)
    expected_time: datetime | None = None
    window_minutes: int | None = Field(default=None, ge=1, le=1440)


class VisitorPassCancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class VisitorPassResponse(BaseModel):
    id: str
    visitor_name: str
    expected_time: str
    window_minutes: int
    window_start: str
    window_end: str
    status: VisitorPassStatus
    creation_source: str
    created_by_user_id: str | None
    created_by: str | None
    arrival_time: str | None
    departure_time: str | None
    number_plate: str | None
    vehicle_make: str | None
    vehicle_colour: str | None
    duration_on_site_seconds: int | None
    duration_human: str | None
    arrival_event_id: str | None
    departure_event_id: str | None
    telemetry_trace_id: str | None
    created_at: str
    updated_at: str


def visitor_pass_response(payload: dict[str, Any]) -> VisitorPassResponse:
    return VisitorPassResponse(**payload)


def visitor_pass_server_error(operation: str, exc: Exception) -> HTTPException:
    logger.exception(
        "visitor_pass_api_operation_failed",
        extra={"operation": operation, "error": str(exc)},
    )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=(
            f"Unable to {operation} Visitor Pass because the backend could not complete "
            "the request. The failure has been logged for diagnosis."
        ),
    )


@router.get("", response_model=list[VisitorPassResponse])
async def list_visitor_passes(
    statuses: Annotated[list[VisitorPassStatus] | None, Query(alias="status")] = None,
    q: str | None = Query(default=None, max_length=160),
    limit: int = Query(default=100, ge=1, le=500),
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[VisitorPassResponse]:
    service = get_visitor_pass_service()
    try:
        changed = await service.refresh_statuses(session=session, publish=False)
        if changed:
            await session.commit()
        passes = await service.list_passes(session, statuses=statuses or None, search=q, limit=limit)
        return [visitor_pass_response(serialize_visitor_pass(pass_)) for pass_ in passes]
    except Exception as exc:
        await session.rollback()
        raise visitor_pass_server_error("load", exc) from exc


@router.post("", response_model=VisitorPassResponse, status_code=status.HTTP_201_CREATED)
async def create_visitor_pass(
    request: VisitorPassCreateRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> VisitorPassResponse:
    service = get_visitor_pass_service()
    try:
        visitor_pass = await service.create_pass(
            session,
            visitor_name=request.visitor_name,
            expected_time=request.expected_time,
            window_minutes=request.window_minutes,
            source="ui",
            created_by_user_id=user.id,
            actor=actor_from_user(user),
        )
        await session.commit()
        await session.refresh(visitor_pass)
        payload = serialize_visitor_pass(visitor_pass)
        await event_bus.publish("visitor_pass.created", {"visitor_pass": payload})
        return visitor_pass_response(payload)
    except VisitorPassError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        await session.rollback()
        raise visitor_pass_server_error("create", exc) from exc


@router.get("/{pass_id}", response_model=VisitorPassResponse)
async def get_visitor_pass(
    pass_id: uuid.UUID,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> VisitorPassResponse:
    service = get_visitor_pass_service()
    try:
        await service.refresh_statuses(session=session, publish=False)
        visitor_pass = await service.get_pass(session, pass_id)
        if not visitor_pass:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Visitor pass not found")
        await session.commit()
        return visitor_pass_response(serialize_visitor_pass(visitor_pass))
    except HTTPException:
        raise
    except Exception as exc:
        await session.rollback()
        raise visitor_pass_server_error("load", exc) from exc


@router.patch("/{pass_id}", response_model=VisitorPassResponse)
async def update_visitor_pass(
    pass_id: uuid.UUID,
    request: VisitorPassUpdateRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> VisitorPassResponse:
    service = get_visitor_pass_service()
    try:
        visitor_pass = await service.get_pass(session, pass_id)
        if not visitor_pass:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Visitor pass not found")
        await service.update_pass(
            session,
            visitor_pass,
            visitor_name=request.visitor_name,
            expected_time=request.expected_time,
            window_minutes=request.window_minutes,
            actor=actor_from_user(user),
            actor_user_id=user.id,
        )
        await session.commit()
        await session.refresh(visitor_pass)
        payload = serialize_visitor_pass(visitor_pass)
        await event_bus.publish("visitor_pass.updated", {"visitor_pass": payload})
        return visitor_pass_response(payload)
    except VisitorPassError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        await session.rollback()
        raise visitor_pass_server_error("update", exc) from exc


@router.post("/{pass_id}/cancel", response_model=VisitorPassResponse)
async def cancel_visitor_pass(
    pass_id: uuid.UUID,
    request: VisitorPassCancelRequest | None = None,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> VisitorPassResponse:
    service = get_visitor_pass_service()
    try:
        visitor_pass = await service.get_pass(session, pass_id)
        if not visitor_pass:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Visitor pass not found")
        await service.cancel_pass(
            session,
            visitor_pass,
            actor=actor_from_user(user),
            actor_user_id=user.id,
            reason=request.reason if request else None,
        )
        await session.commit()
        await session.refresh(visitor_pass)
        payload = serialize_visitor_pass(visitor_pass)
        await event_bus.publish("visitor_pass.cancelled", {"visitor_pass": payload})
        return visitor_pass_response(payload)
    except VisitorPassError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        await session.rollback()
        raise visitor_pass_server_error("cancel", exc) from exc
