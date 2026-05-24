from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import current_user
from app.models import User
from app.services.lpr_zone_shadow import get_lpr_zone_shadow_service
from app.services.lpr_timing import get_lpr_timing_recorder

router = APIRouter()


@router.get("/lpr-timing")
async def lpr_timing_observations(
    limit: int = Query(default=200, ge=1, le=2000),
    _: User = Depends(current_user),
) -> dict[str, list[dict[str, Any]]]:
    return {"observations": await get_lpr_timing_recorder().recent(limit=limit)}


@router.delete("/lpr-timing", status_code=204)
async def clear_lpr_timing_observations(_: User = Depends(current_user)) -> None:
    await get_lpr_timing_recorder().clear()


@router.get("/lpr-zone-shadow")
async def lpr_zone_shadow_observations(
    limit: int = Query(default=200, ge=1, le=2000),
    plate: str | None = Query(default=None, min_length=1, max_length=32),
    status: str | None = Query(default=None, min_length=1, max_length=40),
    decision: str | None = Query(default=None, min_length=1, max_length=80),
    time_of_day: str | None = Query(default=None, min_length=1, max_length=20),
    _: User = Depends(current_user),
) -> dict[str, list[dict[str, Any]]]:
    return {
        "observations": await get_lpr_zone_shadow_service().recent(
            limit=limit,
            plate=plate,
            status=status,
            decision=decision,
            time_of_day=time_of_day,
        )
    }
