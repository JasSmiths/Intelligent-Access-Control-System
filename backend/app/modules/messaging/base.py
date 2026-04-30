from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class IncomingChatMessage:
    """Provider-neutral chat message consumed by Alfred-facing services."""

    provider: str
    provider_message_id: str
    provider_channel_id: str
    author_provider_id: str
    author_display_name: str
    text: str
    is_direct_message: bool
    mentioned_bot: bool
    raw_payload: dict[str, Any]
    received_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    provider_guild_id: str | None = None
    author_role_ids: list[str] = field(default_factory=list)
    author_is_provider_admin: bool = False
    provider_thread_id: str | None = None


@dataclass(frozen=True)
class MessagingActor:
    provider: str
    provider_user_id: str
    display_name: str
    user_id: str | None = None
    user_role: str | None = None
    person_id: str | None = None
    is_admin: bool = False


@dataclass(frozen=True)
class MessagingBridgeResult:
    session_id: str
    response_text: str
    pending_action: dict[str, Any] | None = None
    actor: MessagingActor | None = None


class MessagingProvider(Protocol):
    provider_name: str

    async def start(self) -> None:
        """Start provider I/O."""

    async def stop(self) -> None:
        """Stop provider I/O."""

    async def send_message(
        self,
        provider_channel_id: str,
        text: str,
        *,
        embeds: list[Any] | None = None,
        view: Any | None = None,
        files: list[Any] | None = None,
    ) -> None:
        """Send a provider-specific message."""
