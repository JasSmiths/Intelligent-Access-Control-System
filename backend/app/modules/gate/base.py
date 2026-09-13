from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class GateState(StrEnum):
    UNKNOWN = "unknown"
    CLOSED = "closed"
    OPENING = "opening"
    OPEN = "open"
    CLOSING = "closing"
    FAULT = "fault"


class CommandDelivery(StrEnum):
    """What the provider receipt establishes, independently of physical state."""

    NOT_SENT = "not_sent"
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    UNKNOWN = "unknown"


class GateCommandNotSent(ValueError):
    """The command owner refused the operation before any target attempt."""


class GateCommandDelivery(StrEnum):
    """Aggregate gate outcome; PARTIAL is never a single-provider receipt."""

    NOT_SENT = "not_sent"
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    UNKNOWN = "unknown"
    PARTIAL = "partial"


@dataclass(frozen=True)
class GateCommandResult:
    accepted: bool
    state: GateState
    detail: str | None = None
    metadata: dict[str, Any] | None = None
    delivery: GateCommandDelivery | None = None

    def __post_init__(self) -> None:
        if self.delivery is None:
            object.__setattr__(self, "delivery", GateCommandDelivery.ACCEPTED if self.accepted else GateCommandDelivery.REJECTED)


@dataclass(frozen=True)
class GateCommandContext:
    command_id: str | None
    lease_token: str
    intent_id: str
    idempotency_key: str
    target_device_key: str | None = None
    target_plan: dict[str, Any] | None = None
    expires_at: datetime | None = None
    require_admission: bool = True
    automatic_entry_policy: bool = False
    actor_user_id: str | None = None
    auth_version: int | None = None
    authorize_dispatch: Callable[[AsyncSession], Awaitable[None]] | None = None


class GateController(Protocol):
    """Contract implemented by physical or smart-home gate controllers."""

    async def preview_manual_gate_open(self) -> dict[str, Any]:
        """Resolve exact manual targets without issuing commands or claiming admission."""

    async def open_gate(self, reason: str, *, bypass_schedule: bool = False,
                        command_context: GateCommandContext) -> GateCommandResult:
        """Open the gate for an audited reason."""

    async def current_state(self) -> GateState:
        """Return the latest known gate state."""
