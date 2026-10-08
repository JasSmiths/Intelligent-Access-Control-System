"""Thin calendar adapters; no request-yield transaction spans actual SDK work."""
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import defer

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import ICloudCalendarAccount, ICloudCalendarSyncRun
from app.models.enums import UserRole
from app.modules.icloud_calendar.contracts import ICloudCalendarClientError
from app.services.action_confirmations import ActionConfirmationError
from app.services.auth import authenticated_token, extract_http_token
from app.services.auth_root import reload_auth_root_for_transaction
from app.services.calendar_accounts import (
    CalendarAccountAuthUnknown,
    serialize_icloud_account,
    serialize_icloud_sync_run,
)
from app.services.calendar_request_authority import (
    CalendarAuthorityError,
    CalendarConfirmedAuthority,
    CalendarRequestAuthority,
)
from app.services.calendar_sync_contracts import CalendarOutcomeUnknown, CalendarRunConflict
from app.services.calendar_sync_store import calendar_session, calendar_sql_budget
from app.services.icloud_calendar import get_icloud_calendar_service
from app.services.icloud_calendar_sdk import CalendarSDKBusy
from app.services.telemetry import actor_from_user

router = APIRouter()
logger = get_logger(__name__)


@dataclass(frozen=True)
class CalendarAPIActor:
    authority: CalendarRequestAuthority
    label: str


async def calendar_actor(request: Request) -> CalendarAPIActor:
    token = await extract_http_token(request)
    async with calendar_session(AsyncSessionLocal) as session:
        await calendar_sql_budget(session, None)
        await reload_auth_root_for_transaction(session)
        await calendar_sql_budget(session, None)
        verified = await authenticated_token(session, token)
        if verified is None or token is None:
            raise HTTPException(401, "Sign in before managing iCloud Calendar.")
        if verified.user.role != UserRole.ADMIN:
            raise HTTPException(403, "Admin permission is required.")
        actor = CalendarAPIActor(CalendarRequestAuthority(verified.user.id, token), actor_from_user(verified.user))
    return actor


def disconnect_port(request: Request) -> Callable[[], Awaitable[None]]:
    async def disconnected() -> None:
        # FastAPI has consumed the body. SDKOwner creates this sole waiter only
        # after admission, and drains it before releasing actual SDK capacity.
        while (await request.receive())["type"] != "http.disconnect":
            pass
    return disconnected


class ICloudAuthStartRequest(BaseModel):
    apple_id: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class ICloudAuthVerifyRequest(BaseModel):
    handshake_id: str = Field(min_length=16, max_length=80)
    code: str = Field(pattern=r"^\d{6}$")


class CalendarSyncRequest(BaseModel):
    run_id: uuid.UUID


class CalendarResumeRequest(BaseModel):
    confirmation_token: str | None = Field(default=None, max_length=160)


class CalendarRemoveRequest(BaseModel):
    confirmation_token: str | None = Field(default=None, max_length=160)


def calendar_error(operation: str, exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, CalendarSDKBusy):
        return HTTPException(503, str(exc), headers={"Retry-After": "5"})
    if isinstance(exc, ActionConfirmationError):
        return HTTPException(exc.status_code, exc.detail)
    if isinstance(exc, (CalendarAccountAuthUnknown, CalendarOutcomeUnknown)):
        return HTTPException(409, str(exc))
    if isinstance(exc, CalendarRunConflict):
        return HTTPException(409, str(exc))
    if isinstance(exc, CalendarAuthorityError):
        return HTTPException(403, str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    if isinstance(exc, (ValueError, ICloudCalendarClientError)):
        return HTTPException(422, str(exc))
    # Never log password/code/token or raw SDK exception text.
    logger.warning("Calendar API operation failed", extra={"operation": operation, "error_type": type(exc).__name__})
    return HTTPException(500, f"Unable to {operation} iCloud Calendar. Check the known account/run state before repeating a mutation.")


@router.get("/accounts")
async def list_icloud_calendar_accounts(actor: Annotated[CalendarAPIActor, Depends(calendar_actor)]) -> dict[str, Any]:
    async with calendar_session(AsyncSessionLocal) as session:
        await calendar_sql_budget(session, None)
        accounts = list((await session.scalars(select(ICloudCalendarAccount)
            .options(defer(ICloudCalendarAccount.encrypted_session_bundle))
            .where(ICloudCalendarAccount.is_active.is_(True)).order_by(ICloudCalendarAccount.apple_id))).all())
        await calendar_sql_budget(session, None)
        runs = list((await session.scalars(select(ICloudCalendarSyncRun)
            .order_by(ICloudCalendarSyncRun.started_at.desc()).limit(5))).all())
        return {"accounts": [serialize_icloud_account(row) for row in accounts],
                "recent_sync_runs": [serialize_icloud_sync_run(row) for row in runs]}


@router.get("/sync/{run_id}")
async def read_icloud_calendar_run(run_id: uuid.UUID, actor: Annotated[CalendarAPIActor, Depends(calendar_actor)]) -> dict[str, Any]:
    async with calendar_session(AsyncSessionLocal) as session:
        await calendar_sql_budget(session, None)
        row = await session.get(ICloudCalendarSyncRun, run_id)
        if row is None:
            raise HTTPException(404, "Calendar run not found. The known request ID has not been admitted.")
        return serialize_icloud_sync_run(row)


@router.post("/accounts/auth/start")
async def start_icloud_calendar_auth(body: ICloudAuthStartRequest, request: Request,
    actor: Annotated[CalendarAPIActor, Depends(calendar_actor)]) -> dict[str, Any]:
    try:
        return await get_icloud_calendar_service().start_auth(apple_id=body.apple_id, password=body.password,
            actor=actor.label, actor_user_id=actor.authority.actor_user_id,
            authorize=actor.authority.authorize, disconnect=disconnect_port(request))
    except Exception as exc:
        raise calendar_error("start setup for", exc) from exc


@router.post("/accounts/auth/verify")
async def verify_icloud_calendar_auth(body: ICloudAuthVerifyRequest, request: Request,
    actor: Annotated[CalendarAPIActor, Depends(calendar_actor)]) -> dict[str, Any]:
    try:
        return await get_icloud_calendar_service().verify_auth(handshake_id=body.handshake_id, code=body.code,
            actor=actor.label, actor_user_id=actor.authority.actor_user_id,
            authorize=actor.authority.authorize, disconnect=disconnect_port(request))
    except Exception as exc:
        raise calendar_error("verify setup for", exc) from exc


@router.delete("/accounts/{account_id}")
async def remove_icloud_calendar_account(account_id: uuid.UUID, body: CalendarRemoveRequest, request: Request,
    actor: Annotated[CalendarAPIActor, Depends(calendar_actor)]) -> dict[str, Any]:
    authority = CalendarConfirmedAuthority(actor.authority, "icloud_calendar.account.remove",
        {"account_id": str(account_id)}, body.confirmation_token)
    try:
        return await get_icloud_calendar_service().remove_account(account_id, actor=actor.label,
            actor_user_id=actor.authority.actor_user_id, authorize=authority.authorize,
            disconnect=disconnect_port(request))
    except Exception as exc:
        raise calendar_error("remove an account from", exc) from exc


@router.post("/sync")
async def sync_icloud_calendars_now(body: CalendarSyncRequest, request: Request,
    actor: Annotated[CalendarAPIActor, Depends(calendar_actor)]) -> dict[str, Any]:
    try:
        return await get_icloud_calendar_service().sync_all(run_id=body.run_id, origin="ui",
            actor_user_id=actor.authority.actor_user_id, actor=actor.label, execution=None,
            authorize=actor.authority.authorize, disconnect=disconnect_port(request))
    except Exception as exc:
        raise calendar_error("synchronize", exc) from exc


@router.post("/sync/{run_id}/resume")
async def resume_icloud_calendar_run(run_id: uuid.UUID, body: CalendarResumeRequest, request: Request,
    actor: Annotated[CalendarAPIActor, Depends(calendar_actor)]) -> dict[str, Any]:
    authority = CalendarConfirmedAuthority(actor.authority, "icloud_calendar.sync.resume",
        {"run_id": str(run_id)}, body.confirmation_token)
    try:
        return await get_icloud_calendar_service().sync_all(run_id=run_id, origin="ui",
            actor_user_id=actor.authority.actor_user_id, actor=actor.label, execution=None,
            authorize=actor.authority.authorize, resume_authorize=authority.authorize, resume=True, disconnect=disconnect_port(request))
    except Exception as exc:
        raise calendar_error("resume", exc) from exc
