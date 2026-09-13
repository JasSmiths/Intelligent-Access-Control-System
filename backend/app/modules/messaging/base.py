from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
import uuid


@dataclass(frozen=True)
class MessagingAuthorityBinding:
    """Authority captured by trusted intake, never supplied by message content."""

    user_id: str | None
    user_role: str
    auth_session_version: int | None

    def __post_init__(self) -> None:
        if self.user_role not in {"admin", "standard"}:
            raise ValueError("Invalid messaging authority role")
        if self.user_id is None:
            if self.user_role != "standard" or self.auth_session_version is not None:
                raise ValueError("Unlinked messaging authority must remain standard")
        elif (str(uuid.UUID(self.user_id)) != self.user_id
              or type(self.auth_session_version) is not int or self.auth_session_version < 0):
            raise ValueError("Invalid linked messaging authority")


class MessagingAuthorityChanged(RuntimeError):
    """A queued message must not inherit a different current actor's authority."""


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
