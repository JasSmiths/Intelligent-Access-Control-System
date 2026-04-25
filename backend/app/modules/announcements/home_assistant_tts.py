from dataclasses import dataclass

from app.modules.home_assistant.client import HomeAssistantClient
from app.services.settings import get_runtime_config


@dataclass(frozen=True)
class AnnouncementTarget:
    entity_id: str


class HomeAssistantTtsAnnouncer:
    """Phase 3 announcer for Home Assistant `tts.cloud_say` service calls."""

    def __init__(self, client: HomeAssistantClient | None = None) -> None:
        self._client = client or HomeAssistantClient()

    async def announce(self, target: AnnouncementTarget, message: str) -> None:
        config = await get_runtime_config()
        await self._client.call_service(
            config.home_assistant_tts_service,
            {
                "entity_id": target.entity_id,
                "message": message,
            },
        )

    async def announce_default(self, message: str) -> None:
        config = await get_runtime_config()
        if not config.home_assistant_default_media_player:
            raise ValueError("Default Home Assistant media_player is not configured.")
        await self.announce(AnnouncementTarget(config.home_assistant_default_media_player), message)
