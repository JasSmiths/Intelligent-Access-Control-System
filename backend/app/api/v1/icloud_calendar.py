import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import admin_user
from app.core.logging import get_logger
from app.db.session import get_db_session
from app.models import User
from app.services.icloud_calendar import (
    ICloudCalendarError,
    get_icloud_calendar_service,
    serialize_icloud_account,
    serialize_icloud_sync_run,
)
from app.services.telemetry import actor_from_user

router = APIRouter()
logger = get_logger(__name__)


class ICloudAuthStartRequest(BaseModel):
    apple_id: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class ICloudAuthVerifyRequest(BaseModel):
    handshake_id: str = Field(min_length=16, max_length=80)
    code: str = Field(pattern=r"^\d{6}$")


class ICloudAccountResponse(BaseModel):
    id: str
    apple_id: str
    display_name: str
    status: str
    is_active: bool
    last_auth_at: str | None
    last_sync_at: str | None
    last_sync_status: str | None
    last_sync_summary: dict[str, Any] | None
    last_error: str | None
    created_by_user_id: str | None
    created_at: str | None
    updated_at: str | None


class ICloudSyncRunResponse(BaseModel):
    id: str
    started_at: str | None
    finished_at: str | None
    status: str
    trigger_source: str
    triggered_by_user_id: str | None
    account_count: int
    events_scanned: int
    events_matched: int
    passes_created: int
    passes_updated: int
    passes_cancelled: int
    passes_skipped: int
    account_results: list[dict[str, Any]]
    error: str | None


class ICloudAccountsPayload(BaseModel):
    accounts: list[ICloudAccountResponse]
    recent_sync_runs: list[ICloudSyncRunResponse]


def icloud_server_error(operation: str, exc: Exception) -> HTTPException:
    logger.exception("icloud_calendar_api_operation_failed", extra={"operation": operation, "error": str(exc)})
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=(
            f"Unable to {operation} iCloud Calendar because the backend could not complete "
            "the request. The failure has been logged for diagnosis."
        ),
    )


@router.get("/accounts", response_model=ICloudAccountsPayload)
async def list_icloud_calendar_accounts(
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> ICloudAccountsPayload:
    service = get_icloud_calendar_service()
    accounts = await service.list_accounts(session)
    runs = await service.recent_sync_runs(session)
    return ICloudAccountsPayload(
        accounts=[ICloudAccountResponse(**serialize_icloud_account(account)) for account in accounts],
        recent_sync_runs=[ICloudSyncRunResponse(**serialize_icloud_sync_run(run)) for run in runs],
    )


@router.post("/accounts/auth/start")
async def start_icloud_calendar_auth(
    request: ICloudAuthStartRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    service = get_icloud_calendar_service()
    try:
        result = await service.start_auth(
            session,
            apple_id=request.apple_id,
            password=request.password,
            user=user,
        )
        await session.commit()
        return result
    except ICloudCalendarError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        await session.rollback()
        raise icloud_server_error("start iCloud Calendar setup", exc) from exc


@router.post("/accounts/auth/verify")
async def verify_icloud_calendar_auth(
    request: ICloudAuthVerifyRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    service = get_icloud_calendar_service()
    try:
        result = await service.verify_auth(
            session,
            handshake_id=request.handshake_id,
            code=request.code,
            user=user,
        )
        await session.commit()
        return result
    except ICloudCalendarError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        await session.rollback()
        raise icloud_server_error("verify iCloud Calendar setup", exc) from exc


@router.delete("/accounts/{account_id}", response_model=ICloudAccountResponse)
async def remove_icloud_calendar_account(
    account_id: uuid.UUID,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> ICloudAccountResponse:
    service = get_icloud_calendar_service()
    try:
        account = await service.get_account(session, account_id)
        if not account:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="iCloud Calendar account not found.")
        removed = await service.remove_account(session, account, user=user)
        await session.commit()
        await session.refresh(removed)
        return ICloudAccountResponse(**serialize_icloud_account(removed))
    except HTTPException:
        raise
    except ICloudCalendarError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        await session.rollback()
        raise icloud_server_error("remove iCloud Calendar account", exc) from exc


@router.post("/sync", response_model=ICloudSyncRunResponse)
async def sync_icloud_calendars_now(user: User = Depends(admin_user)) -> ICloudSyncRunResponse:
    service = get_icloud_calendar_service()
    try:
        result = await service.sync_all(
            trigger_source="ui",
            triggered_by_user_id=user.id,
            actor=actor_from_user(user),
        )
        return ICloudSyncRunResponse(**result)
    except ICloudCalendarError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        raise icloud_server_error("sync iCloud Calendars", exc) from exc
