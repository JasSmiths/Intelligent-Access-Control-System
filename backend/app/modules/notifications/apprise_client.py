import asyncio
from urllib.parse import urlparse

import apprise

from app.core.logging import get_logger
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError, NotificationSender
from app.services.settings import get_runtime_config

logger = get_logger(__name__)


class AppriseNotificationSender(NotificationSender):
    """Apprise-backed notification sender."""

    def __init__(self, urls: str | None = None) -> None:
        self._urls = urls

    async def send(self, title: str, body: str, context: NotificationContext) -> None:
        urls = await self._parse_urls()
        if not urls:
            logger.info(
                "notification_skipped_not_configured",
                extra={
                    "title": title,
                    "event_type": context.event_type,
                    "severity": context.severity,
                },
            )
            raise NotificationDeliveryError("Apprise is not configured.")

        def notify() -> bool:
            validate_apprise_urls("\n".join(urls))
            notifier = apprise.Apprise()
            for url in urls:
                notifier.add(url)
            return notifier.notify(title=title, body=body)

        sent = await asyncio.to_thread(notify)
        logger.info(
            "notification_sent" if sent else "notification_failed",
            extra={"title": title, "event_type": context.event_type, "severity": context.severity},
        )
        if not sent:
            raise NotificationDeliveryError("Apprise accepted the URL but did not deliver the notification.")

    async def _parse_urls(self) -> list[str]:
        configured = self._urls
        if configured is None:
            configured = (await get_runtime_config()).apprise_urls
        if not configured:
            return []
        return [normalize_apprise_url(url.strip()) for url in split_apprise_urls(configured)]


def validate_apprise_urls(configured: str) -> int:
    urls = [normalize_apprise_url(url.strip()) for url in split_apprise_urls(configured)]
    if not urls:
        raise NotificationDeliveryError("At least one Apprise URL is required.")

    notifier = apprise.Apprise()
    accepted = 0
    rejected: list[str] = []
    for url in urls:
        if notifier.add(url):
            accepted += 1
        else:
            rejected.append(_mask_url(url))
    if accepted == 0:
        raise NotificationDeliveryError(
            f"Apprise could not parse any configured URL. Check the format: {', '.join(rejected)}"
        )
    return accepted


def split_apprise_urls(configured: str) -> list[str]:
    return [url.strip() for url in configured.replace("\n", ",").split(",") if url.strip()]


def normalize_apprise_url(url: str) -> str:
    """Accept common Pushover spellings and convert them to Apprise's schema."""
    parsed = urlparse(url)
    if parsed.scheme not in {"pushover", "pover"}:
        return url

    scheme = "pover"
    if "@" in parsed.netloc or not parsed.path.strip("/"):
        return f"{scheme}://{url.split('://', 1)[1]}"

    user_key = parsed.netloc
    app_token = parsed.path.strip("/").split("/", 1)[0]
    suffix = ""
    if "?" in url:
        suffix = f"?{url.split('?', 1)[1]}"
    return f"{scheme}://{user_key}@{app_token}{suffix}"


def _mask_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme:
        return "***"
    return f"{parsed.scheme}://***"
