from fastapi import APIRouter, Query

from app.services.leaderboard import get_leaderboard_service

router = APIRouter()


@router.get("/leaderboard")
async def leaderboard(
    limit: int = Query(default=25, ge=1, le=100),
    enrich_unknowns: bool = Query(default=True),
) -> dict:
    return await get_leaderboard_service().get_leaderboard(
        limit=limit,
        enrich_unknowns=enrich_unknowns,
    )
