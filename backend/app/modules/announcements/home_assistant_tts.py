from dataclasses import dataclass

from app.modules.home_assistant.client import (
    HomeAssistantClient,
    HomeAssistantError,
    get_home_assistant_client,
)
from app.modules.notifications.base import NotificationDeliveryError
from app.services.settings import get_runtime_config


@dataclass(frozen=True)
class AnnouncementTarget:
    entity_id: str


class HomeAssistantTtsAnnouncer:
    """Announcer for Home Assistant TTS service calls."""

    def __init__(self, client: HomeAssistantClient | None = None) -> None:
        self._client = client or get_home_assistant_client()

    async def announce(self, target: AnnouncementTarget, message: str, *, runtime_config=None) -> None:
        config = runtime_config if runtime_config is not None else await get_runtime_config()
        options = {"runtime_config": config} if runtime_config is not None else {}
        try:
            await self._client.call_service(
                config.home_assistant_tts_service,
                {"entity_id": target.entity_id, "message": message},
                **options,
            )
        except HomeAssistantError as exc:
            raise NotificationDeliveryError("Home Assistant announcement request failed.", delivery=exc.delivery) from exc

    async def announce_default(self, message: str) -> None:
        config = await get_runtime_config()
        if not config.home_assistant_default_media_player:
            raise ValueError("Default Home Assistant media_player is not configured.")
        await self.announce(AnnouncementTarget(config.home_assistant_default_media_player), message)
