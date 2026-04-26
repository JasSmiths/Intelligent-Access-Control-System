from dataclasses import dataclass

from app.modules.home_assistant.client import HomeAssistantClient, HomeAssistantError
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError


@dataclass(frozen=True)
class HomeAssistantMobileAppTarget:
    service_name: str


class HomeAssistantMobileAppNotifier:
    """Home Assistant mobile_app notify sender."""

    def __init__(self, client: HomeAssistantClient | None = None) -> None:
        self._client = client or HomeAssistantClient()

    async def send(
        self,
        target: HomeAssistantMobileAppTarget,
        title: str,
        body: str,
        context: NotificationContext,
    ) -> None:
        if not target.service_name.startswith("notify.mobile_app_"):
            raise NotificationDeliveryError("Home Assistant target must be a notify.mobile_app_* service.")

        try:
            await self._client.call_service(
                target.service_name,
                {
                    "title": title or context.subject,
                    "message": body or context.subject,
                    "data": {
                        "tag": f"iacs-{context.event_type}",
                        "group": "iacs",
                    },
                },
            )
        except HomeAssistantError as exc:
            raise NotificationDeliveryError(str(exc)) from exc
