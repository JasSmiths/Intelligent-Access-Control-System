from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import admin_user, current_user
from app.db.session import get_db_session
from app.models import User
from app.services.action_confirmations import ActionConfirmationError, consume_action_confirmation
from app.services.maintenance import get_status, set_mode
from app.services.telemetry import actor_from_user

router = APIRouter()


class MaintenanceModeRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    confirmation_token: str | None = Field(default=None, max_length=160)


@router.get("/status")
async def maintenance_status(_: User = Depends(current_user)) -> dict[str, Any]:
    return await get_status()


@router.post("/enable")
async def enable_maintenance_mode(
    request: MaintenanceModeRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    await _require_confirmation(session, user=user, action="maintenance_mode.enable", request=request)
    return await set_mode(
        True,
        actor=actor_from_user(user),
        actor_user_id=str(user.id),
        source="UI",
        reason=request.reason or "Enabled from UI",
        sync_ha=True,
    )


@router.post("/disable")
async def disable_maintenance_mode(
    request: MaintenanceModeRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    await _require_confirmation(session, user=user, action="maintenance_mode.disable", request=request)
    return await set_mode(
        False,
        actor=actor_from_user(user),
        actor_user_id=str(user.id),
        source="UI",
        reason=request.reason or "Disabled from UI",
        sync_ha=True,
    )


async def _require_confirmation(
    session: AsyncSession,
    *,
    user: User,
    action: str,
    request: MaintenanceModeRequest,
) -> None:
    try:
        await consume_action_confirmation(
            session,
            user=user,
            action=action,
            payload=request.model_dump(exclude={"confirmation_token"}, exclude_none=True),
            confirmation_token=request.confirmation_token,
        )
    except ActionConfirmationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
