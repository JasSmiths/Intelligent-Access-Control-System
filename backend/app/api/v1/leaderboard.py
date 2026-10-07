from typing import Annotated

from fastapi import APIRouter, Query

from app.services.leaderboard import get_leaderboard_service

router = APIRouter()


@router.get("/leaderboard")
async def leaderboard(
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    enrich_unknowns: Annotated[bool, Query()] = True,
) -> dict:
    return await get_leaderboard_service().get_leaderboard(
        limit=limit,
        enrich_unknowns=enrich_unknowns,
    )
