from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.dependencies import current_user
from app.models import User
from app.services.maintenance import get_status, set_mode
from app.services.telemetry import actor_from_user

router = APIRouter()


class MaintenanceModeRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


@router.get("/status")
async def maintenance_status(_: User = Depends(current_user)) -> dict[str, Any]:
    return await get_status()


@router.post("/enable")
async def enable_maintenance_mode(
    request: MaintenanceModeRequest,
    user: User = Depends(current_user),
) -> dict[str, Any]:
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
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return await set_mode(
        False,
        actor=actor_from_user(user),
        actor_user_id=str(user.id),
        source="UI",
        reason=request.reason or "Disabled from UI",
        sync_ha=True,
    )
