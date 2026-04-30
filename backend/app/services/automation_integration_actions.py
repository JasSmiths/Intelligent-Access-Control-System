import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import AutomationRule, ICloudCalendarAccount
from app.services.icloud_calendar import ICloudCalendarError, get_icloud_calendar_service


IntegrationEnabledCheck = Callable[[], Awaitable[bool]]
IntegrationActionHandler = Callable[
    [AsyncSession, dict[str, Any], Any, AutomationRule],
    Awaitable[dict[str, Any]],
]


@dataclass(frozen=True)
class IntegrationActionDefinition:
    type: str
    provider: str
    provider_label: str
    provider_description: str
    action: str
    label: str
    description: str
    is_enabled: IntegrationEnabledCheck
    execute: IntegrationActionHandler

    def action_catalog(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "label": self.label,
            "description": self.description,
            "integration_action": True,
            "integration_provider": self.provider,
            "integration_provider_label": self.provider_label,
            "integration_action_key": self.action,
            "default_config": {
                "provider": self.provider,
                "action": self.action,
            },
        }


async def integration_action_catalog() -> list[dict[str, Any]]:
    enabled_actions = [
        action
        for action in INTEGRATION_ACTIONS
        if await action.is_enabled()
    ]
    if not enabled_actions:
        return []

    providers: dict[str, dict[str, Any]] = {}
    for action in enabled_actions:
        provider = providers.setdefault(
            action.provider,
            {
                "id": action.provider,
                "label": action.provider_label,
                "description": action.provider_description,
                "actions": [],
            },
        )
        provider["actions"].append(action.action_catalog())

    return [
        {
            "id": "integrations",
            "label": "Integrations",
            "actions": [action.action_catalog() for action in enabled_actions],
            "integrations": list(providers.values()),
        }
    ]


def registered_integration_action_types() -> set[str]:
    return set(INTEGRATION_ACTION_BY_TYPE)


def integration_action_for_type(action_type: str) -> IntegrationActionDefinition | None:
    return INTEGRATION_ACTION_BY_TYPE.get(action_type)


def integration_action_config(action_type: str, config: dict[str, Any]) -> dict[str, Any]:
    action = integration_action_for_type(action_type)
    if not action:
        return {}
    return {
        "provider": str(config.get("provider") or action.provider),
        "action": str(config.get("action") or action.action),
    }


async def execute_integration_action(
    session: AsyncSession,
    action: dict[str, Any],
    context: Any,
    *,
    rule: AutomationRule,
) -> dict[str, Any]:
    action_type = str(action.get("type") or "")
    definition = integration_action_for_type(action_type)
    if not definition:
        return {
            "id": action.get("id"),
            "type": action_type,
            "status": "failed",
            "error": "integration_action_not_registered",
        }
    return await definition.execute(session, action, context, rule)


async def _icloud_calendar_enabled() -> bool:
    async with AsyncSessionLocal() as session:
        return bool(
            await session.scalar(
                select(ICloudCalendarAccount.id)
                .where(ICloudCalendarAccount.is_active.is_(True))
                .limit(1)
            )
        )


async def _execute_icloud_calendar_sync(
    _session: AsyncSession,
    action: dict[str, Any],
    _context: Any,
    rule: AutomationRule,
) -> dict[str, Any]:
    try:
        result = await get_icloud_calendar_service().sync_all(
            trigger_source="automation",
            triggered_by_user_id=_coerce_uuid(getattr(rule, "created_by_user_id", None)),
            actor="Automation Engine",
        )
    except ICloudCalendarError as exc:
        return {
            "id": action["id"],
            "type": action["type"],
            "status": "failed",
            "integration_provider": "icloud_calendar",
            "integration_action": "sync_calendars",
            "error": str(exc),
        }

    return {
        "id": action["id"],
        "type": action["type"],
        "status": "success",
        "integration_provider": "icloud_calendar",
        "integration_action": "sync_calendars",
        "sync": result,
        "account_count": result.get("account_count", 0),
        "events_scanned": result.get("events_scanned", 0),
        "events_matched": result.get("events_matched", 0),
        "passes_created": result.get("passes_created", 0),
        "passes_updated": result.get("passes_updated", 0),
        "passes_cancelled": result.get("passes_cancelled", 0),
        "passes_skipped": result.get("passes_skipped", 0),
    }


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


INTEGRATION_ACTIONS = [
    IntegrationActionDefinition(
        type="integration.icloud_calendar.sync",
        provider="icloud_calendar",
        provider_label="iCloud Calendar",
        provider_description="Create or update Visitor Passes from connected iCloud Calendar accounts.",
        action="sync_calendars",
        label="Sync Calendars Now",
        description="Scan connected iCloud Calendars for Open Gate events.",
        is_enabled=_icloud_calendar_enabled,
        execute=_execute_icloud_calendar_sync,
    ),
]

INTEGRATION_ACTION_BY_TYPE = {action.type: action for action in INTEGRATION_ACTIONS}
