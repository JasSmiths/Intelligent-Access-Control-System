from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class NotificationContext:
    """Structured notification input shared by workflows and AI tools."""

    event_type: str
    subject: str
    severity: str
    facts: dict[str, str]


@dataclass(frozen=True)
class ComposedNotification:
    title: str
    body: str


class NotificationSender(Protocol):
    async def send(
        self,
        title: str,
        body: str,
        context: NotificationContext,
        *,
        attachments: list[str] | None = None,
    ) -> None:
        """Send a contextual notification."""


class NotificationDeliveryError(RuntimeError):
    """Raised when a configured notification sender cannot deliver."""
