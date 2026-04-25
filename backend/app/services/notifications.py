from functools import lru_cache

from app.modules.notifications.apprise_client import AppriseNotificationSender
from app.core.logging import get_logger
from app.modules.notifications.base import ComposedNotification, NotificationContext, NotificationDeliveryError, NotificationSender
from app.services.event_bus import event_bus
from app.services.settings import get_runtime_config

logger = get_logger(__name__)


class NotificationComposer:
    """Turns structured event context into notification text.

    Phase 4 can replace or augment this with an LLM naturalizer. The structured
    input remains the contract so agent-generated text never has to infer facts
    from free-form log lines.
    """

    def compose(self, context: NotificationContext) -> ComposedNotification:
        severity = context.severity.title()
        title = f"{severity}: {context.subject}"
        facts = ", ".join(f"{key}: {value}" for key, value in context.facts.items())
        body = f"{context.event_type.replace('_', ' ').title()}"
        if facts:
            body = f"{body}. {facts}"
        return ComposedNotification(title=title, body=body)


class NotificationService:
    def __init__(
        self,
        sender: NotificationSender | None = None,
        composer: NotificationComposer | None = None,
    ) -> None:
        self._sender = sender or AppriseNotificationSender()
        self._composer = composer or NotificationComposer()

    async def notify(self, context: NotificationContext, *, raise_on_failure: bool = False) -> ComposedNotification:
        notification = self._composer.compose(context)
        delivered = True
        try:
            await self._sender.send(notification.title, notification.body, context)
        except NotificationDeliveryError as exc:
            delivered = False
            logger.warning(
                "notification_delivery_failed",
                extra={"event_type": context.event_type, "severity": context.severity, "error": str(exc)},
            )
            if raise_on_failure:
                raise
        config = await get_runtime_config()
        await event_bus.publish(
            "notification.sent" if delivered else "notification.failed",
            {
                "title": notification.title,
                "body": notification.body,
                "event_type": context.event_type,
                "severity": context.severity,
                "configured": bool(config.apprise_urls),
                "delivered": delivered,
            },
        )
        return notification


@lru_cache
def get_notification_service() -> NotificationService:
    return NotificationService()
