from dataclasses import dataclass
from typing import Literal, Protocol


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

    def __init__(
        self,
        message: str,
        *,
        delivery: Literal["not_sent", "rejected", "accepted", "unknown"] = "unknown",
        destination_outcomes: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.delivery = delivery
        # Providers may retain more detailed diagnostics. Dispatch only persists
        # its separately sanitised copy of this compact receipt.
        self.destination_outcomes = [dict(item) for item in destination_outcomes or [] if isinstance(item, dict)]
