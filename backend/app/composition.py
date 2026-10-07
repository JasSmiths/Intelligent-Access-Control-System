"""Application composition: wire business effects without integration back-imports.

Construction and binding are inert. Startup remains the sole lifecycle owner.
Delegates resolve owners at invocation so tests can replace them without starting I/O.
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.base import NotificationContext
from app.services import (
    access_devices,
    actionable_notifications,
    home_assistant,
    maintenance,
    notifications,
)
from app.services.integration_effects import HomeAssistantEffects


class MaintenanceNotificationIntake:
    async def reserve(
        self, session: AsyncSession, context: NotificationContext, *, dispatch_id: uuid.UUID,
    ) -> None:
        await notifications.get_notification_service().enqueue_in_session(
            session, context, dispatch_id=dispatch_id,
        )

    def wake(self) -> None:
        notifications.get_notification_service().dispatcher.wake()


async def _mobile_action(action_id: str, data: dict[str, Any]) -> bool:
    return await actionable_notifications.get_actionable_notification_service().handle_home_assistant_action(
        action_id, data,
    )


async def _maintenance_state(active: bool) -> None:
    await maintenance.set_mode(active, actor="Home Assistant Sync", source="Home Assistant Sync",
                               reason="Synced from Home Assistant", sync_ha=False)


async def _degraded_notification(context: NotificationContext) -> None:
    await notifications.get_notification_service().enqueue_notification(context)


async def _home_assistant_status(refresh: bool) -> dict[str, Any]:
    return await home_assistant.get_home_assistant_service().status(refresh=refresh)


def wire_application() -> None:
    home_assistant.bind_home_assistant_effects(HomeAssistantEffects(
        mobile_action=_mobile_action, maintenance_state=_maintenance_state,
        degraded_notification=_degraded_notification,
    ))
    maintenance.bind_notification_intake(MaintenanceNotificationIntake())
    access_devices.bind_home_assistant_status(_home_assistant_status)
