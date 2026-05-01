from dataclasses import dataclass
from typing import Any

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
        *,
        image_url: str | None = None,
        image_content_type: str | None = None,
        actions: list[dict[str, Any]] | None = None,
    ) -> None:
        if not target.service_name.startswith("notify.mobile_app_"):
            raise NotificationDeliveryError("Home Assistant target must be a notify.mobile_app_* service.")

        data = {
            "tag": f"iacs-{context.event_type}",
            "group": "iacs",
        }
        if image_url:
            data["image"] = image_url
            data["attachment"] = {
                "url": image_url,
                "content-type": _attachment_content_type(image_content_type),
            }
        if actions:
            data["actions"] = actions

        try:
            await self._client.call_service(
                target.service_name,
                {
                    "title": title or context.subject,
                    "message": body or context.subject,
                    "data": data,
                },
            )
        except HomeAssistantError as exc:
            raise NotificationDeliveryError(str(exc)) from exc


def _attachment_content_type(content_type: str | None) -> str:
    normalized = (content_type or "").lower()
    if "png" in normalized:
        return "png"
    if "gif" in normalized:
        return "gif"
    return "jpeg"
