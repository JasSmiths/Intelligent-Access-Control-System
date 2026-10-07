import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import AutomationRule, ICloudCalendarAccount
from app.services.icloud_calendar import ICloudCalendarError, get_icloud_calendar_service
from app.services.workflows.action_definitions import (
    ACTION_BY_TYPE,
    action_definition,
    require_exhaustive_bindings,
)
from app.services.workflows.automation_definition import AutomationContext

IntegrationEnabledCheck = Callable[[], Awaitable[bool]]


class IntegrationActionHandler(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        action: dict[str, Any],
        context: AutomationContext,
        rule: AutomationRule,
        *,
        operation_id: str | None = None,
        origin: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class IntegrationActionDefinition:
    type: str
    is_enabled: IntegrationEnabledCheck
    execute: IntegrationActionHandler
    disabled_reason: str = "Integration is not configured."

    def action_catalog(self, status: "IntegrationActionStatus | None" = None) -> dict[str, Any]:
        status = status or IntegrationActionStatus(enabled=True)
        return {
            **action_definition(self.type).catalog(),
            "enabled": status.enabled, "disabled": not status.enabled,
            "disabled_reason": status.disabled_reason,
        }



@dataclass(frozen=True)
class IntegrationActionStatus:
    enabled: bool
    disabled_reason: str | None = None


async def integration_action_status(action: IntegrationActionDefinition) -> IntegrationActionStatus:
    try:
        enabled = await action.is_enabled()
    except Exception as exc:  # noqa: BLE001 - an unavailable integration cannot remain executable.
        return IntegrationActionStatus(False, f"Integration status check failed: {exc}")
    return IntegrationActionStatus(enabled, None if enabled else action.disabled_reason)


async def integration_action_catalog() -> list[dict[str, Any]]:
    action_statuses = [
        (action, await integration_action_status(action)) for action in INTEGRATION_ACTIONS
    ]
    if not action_statuses:
        return []

    providers: dict[str, dict[str, Any]] = {}
    for action, status in action_statuses:
        definition = action_definition(action.type)
        assert definition.provider is not None
        provider = providers.setdefault(
            definition.provider,
            {
                "id": definition.provider,
                "label": definition.provider_label,
                "description": definition.provider_description,
                "enabled": False,
                "disabled_reason": None,
                "actions": [],
            },
        )
        provider["enabled"] = bool(provider["enabled"] or status.enabled)
        provider["disabled_reason"] = None if provider["enabled"] else status.disabled_reason
        provider["actions"].append(action.action_catalog(status))

    return [
        {
            "id": "integrations",
            "label": "Integrations",
            "actions": [action.action_catalog(status) for action, status in action_statuses],
            "integrations": list(providers.values()),
        }
    ]


def registered_integration_action_types() -> set[str]:
    return set(INTEGRATION_ACTION_BY_TYPE)


def integration_action_for_type(action_type: str) -> IntegrationActionDefinition | None:
    return INTEGRATION_ACTION_BY_TYPE.get(action_type)


async def execute_integration_action(
    session: AsyncSession,
    action: dict[str, Any],
    context: AutomationContext,
    *,
    rule: AutomationRule,
    operation_id: str | None = None,
    origin: dict[str, Any] | None = None,
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
    status = await integration_action_status(definition)
    if not status.enabled:
        return {
            "id": action.get("id"),
            "type": action_type,
            "status": "skipped",
            "reason": "integration_disabled",
            "integration_provider": action_definition(definition.type).provider,
            "integration_action": action_definition(definition.type).integration_action,
            "disabled_reason": status.disabled_reason,
        }
    return await definition.execute(
        session, action, context, rule, operation_id=operation_id, origin=origin
    )


async def _icloud_calendar_enabled() -> bool:
    async with AsyncSessionLocal() as session:
        return bool(
            await session.scalar(
                select(ICloudCalendarAccount.id)
                .where(ICloudCalendarAccount.is_active.is_(True))
                .where(ICloudCalendarAccount.status == "connected")
                .where(ICloudCalendarAccount.encrypted_session_bundle.is_not(None))
                .limit(1)
            )
        )


async def _execute_icloud_calendar_sync(
    session: AsyncSession,
    action: dict[str, Any],
    context: AutomationContext,
    rule: AutomationRule,
    *,
    operation_id: str | None = None,
    origin: dict[str, Any] | None = None,
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

    sync_status = str(result.get("status") or "ok")
    status = "success" if sync_status == "ok" else "failed"
    response = {
        "id": action["id"],
        "type": action["type"],
        "status": status,
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
    if status == "failed":
        response["error"] = result.get("error") or f"iCloud Calendar sync returned {sync_status}."
    return response


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


INTEGRATION_ACTIONS = [
    IntegrationActionDefinition(
        type="integration.icloud_calendar.sync",
        is_enabled=_icloud_calendar_enabled,
        execute=_execute_icloud_calendar_sync,
        disabled_reason="No active connected iCloud Calendar account has a valid session.",
    ),
]

INTEGRATION_ACTION_BY_TYPE = {action.type: action for action in INTEGRATION_ACTIONS}

require_exhaustive_bindings(
    {key: definition for key, definition in ACTION_BY_TYPE.items() if definition.provider is not None},
    INTEGRATION_ACTION_BY_TYPE,
    owner="Integration actions",
)
