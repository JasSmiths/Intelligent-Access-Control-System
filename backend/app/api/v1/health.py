from fastapi import APIRouter

from app.services.discord_messaging import get_discord_messaging_service

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, object]:
    discord_status = await get_discord_messaging_service().status()
    return {
        "status": "ok",
        "discord": {
            "configured": discord_status.get("configured"),
            "connected": discord_status.get("connected"),
            "guild_count": discord_status.get("guild_count"),
            "channel_count": discord_status.get("channel_count"),
        },
    }
