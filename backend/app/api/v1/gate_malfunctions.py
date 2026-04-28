from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.dependencies import admin_user, current_user
from app.models import User
from app.services.gate_malfunctions import get_gate_malfunction_service
from app.services.telemetry import actor_from_user

router = APIRouter()


class GateMalfunctionOverrideRequest(BaseModel):
    action: str = Field(pattern="^(recheck_live_state|run_attempt_now|mark_resolved|mark_fubar)$")
    reason: str = Field(default="Manual gate malfunction override", max_length=500)
    confirm: bool = False


@router.get("/active")
async def active_gate_malfunctions(
    include_timeline: bool = False,
    _: User = Depends(current_user),
) -> dict[str, Any]:
    return {
        "items": await get_gate_malfunction_service().active(include_timeline=include_timeline),
    }


@router.get("/history")
async def gate_malfunction_history(
    status: str | None = None,
    include_timeline: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    _: User = Depends(current_user),
) -> dict[str, Any]:
    return await get_gate_malfunction_service().history_page(
        status=status,
        limit=limit,
        include_timeline=include_timeline,
        cursor=cursor,
    )


@router.get("/{malfunction_id}/trace")
async def gate_malfunction_trace(
    malfunction_id: UUID,
    _: User = Depends(current_user),
) -> dict[str, Any]:
    payload = await get_gate_malfunction_service().trace(malfunction_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Gate malfunction not found.")
    return payload


@router.post("/{malfunction_id}/override")
async def override_gate_malfunction(
    malfunction_id: UUID,
    request: GateMalfunctionOverrideRequest,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    result = await get_gate_malfunction_service().override(
        malfunction_id,
        action=request.action,
        reason=request.reason,
        actor=actor_from_user(user),
        confirm=request.confirm,
    )
    if result.get("error") == "Gate malfunction not found.":
        raise HTTPException(status_code=404, detail=result["error"])
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result
