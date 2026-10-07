"""Narrow application-owned ports for integration observations and durable handoffs."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.base import NotificationContext

MAINTENANCE_HA_ENTITY_ID = "input_boolean.top_gate_maintenance_mode"


class NotificationIntake(Protocol):
    async def reserve(
        self, session: AsyncSession, context: NotificationContext, *, dispatch_id: uuid.UUID,
    ) -> None: ...

    def wake(self) -> None: ...


@dataclass(frozen=True)
class HomeAssistantEffects:
    mobile_action: Callable[[str, dict[str, Any]], Awaitable[bool]]
    maintenance_state: Callable[[bool], Awaitable[None]]
    degraded_notification: Callable[[NotificationContext], Awaitable[None]]


HomeAssistantStatus = Callable[[bool], Awaitable[dict[str, Any]]]
