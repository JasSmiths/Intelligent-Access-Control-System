from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import current_user
from app.models import User
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
