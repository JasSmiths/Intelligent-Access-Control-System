import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import admin_user, current_user
from app.core.logging import get_logger
from app.db.session import get_db_session
from app.models import User
from app.models.enums import VisitorPassStatus, VisitorPassType
from app.services.event_bus import event_bus
from app.services.telemetry import actor_from_user
from app.services.visitor_passes import (
    DEFAULT_WINDOW_MINUTES,
    VisitorPassError,
    get_visitor_pass_service,
    serialize_visitor_pass,
)
from app.services.whatsapp_messaging import get_whatsapp_messaging_service

router = APIRouter()
logger = get_logger(__name__)


class VisitorPassCreateRequest(BaseModel):
    visitor_name: str = Field(min_length=1, max_length=160)
    pass_type: VisitorPassType = VisitorPassType.ONE_TIME
    visitor_phone: str | None = Field(default=None, max_length=40)
    expected_time: datetime | None = None
    window_minutes: int = Field(default=DEFAULT_WINDOW_MINUTES, ge=1, le=1440)
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class VisitorPassUpdateRequest(BaseModel):
    visitor_name: str | None = Field(default=None, min_length=1, max_length=160)
    pass_type: VisitorPassType | None = None
    visitor_phone: str | None = Field(default=None, max_length=40)
    expected_time: datetime | None = None
    window_minutes: int | None = Field(default=None, ge=1, le=1440)
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class VisitorPassCancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class VisitorPassResponse(BaseModel):
    id: str
    visitor_name: str
    pass_type: VisitorPassType
    visitor_phone: str | None
    expected_time: str
    window_minutes: int
    window_start: str
    window_end: str
    valid_from: str | None
    valid_until: str | None
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
    source_reference: str | None
    source_metadata: dict[str, Any] | None
    whatsapp_status: str | None
    whatsapp_status_label: str | None
    whatsapp_status_detail: str | None
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
            pass_type=request.pass_type,
            visitor_phone=request.visitor_phone,
            valid_from=request.valid_from,
            valid_until=request.valid_until,
            source="ui",
            created_by_user_id=user.id,
            actor=actor_from_user(user),
        )
        await session.commit()
        await session.refresh(visitor_pass)
        payload = serialize_visitor_pass(visitor_pass)
        await event_bus.publish("visitor_pass.created", {"visitor_pass": payload})
        if visitor_pass.pass_type == VisitorPassType.DURATION and visitor_pass.visitor_phone:
            try:
                await get_whatsapp_messaging_service().send_visitor_pass_outreach(visitor_pass)
            except Exception as exc:
                logger.warning(
                    "visitor_pass_whatsapp_outreach_failed",
                    extra={"visitor_pass_id": str(visitor_pass.id), "error": str(exc)[:240]},
                )
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
            pass_type=request.pass_type,
            visitor_phone=(
                request.visitor_phone
                if "visitor_phone" in request.model_fields_set
                else None
            ),
            valid_from=(
                request.valid_from
                if "valid_from" in request.model_fields_set
                else None
            ),
            valid_until=(
                request.valid_until
                if "valid_until" in request.model_fields_set
                else None
            ),
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


@router.post("/{pass_id}/timeframe-requests/{request_id}/{decision}")
async def decide_visitor_pass_timeframe_request(
    pass_id: uuid.UUID,
    request_id: str,
    decision: str,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    normalized_decision = decision.strip().lower()
    if normalized_decision not in {"allow", "deny"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Decision must be allow or deny.")
    try:
        return await get_whatsapp_messaging_service().decide_visitor_timeframe_request(
            str(pass_id),
            request_id,
            normalized_decision,
            actor_user=user,
        )
    except VisitorPassError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        raise visitor_pass_server_error("decide timeframe request", exc) from exc
