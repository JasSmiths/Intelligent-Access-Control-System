"""Provider-neutral messaging contracts and adapters."""

from app.modules.messaging.base import (
    IncomingChatMessage,
    MessagingActor,
    MessagingBridgeResult,
)

__all__ = [
    "IncomingChatMessage",
    "MessagingActor",
    "MessagingBridgeResult",
]
