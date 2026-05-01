import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import AutomationRule, ICloudCalendarAccount
from app.services.icloud_calendar import ICloudCalendarError, get_icloud_calendar_service
from app.services.whatsapp_messaging import get_whatsapp_messaging_service


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
    disabled_reason: str = "Integration is not configured."
    default_config: dict[str, Any] | None = None

    def action_catalog(self, status: "IntegrationActionStatus | None" = None) -> dict[str, Any]:
        status = status or IntegrationActionStatus(enabled=True)
        return {
            "type": self.type,
            "label": self.label,
            "description": self.description,
            "enabled": status.enabled,
            "disabled": not status.enabled,
            "disabled_reason": status.disabled_reason,
            "integration_action": True,
            "integration_provider": self.provider,
            "integration_provider_label": self.provider_label,
            "integration_action_key": self.action,
            "default_config": self.default_config or {
                "provider": self.provider,
                "action": self.action,
            },
        }


@dataclass(frozen=True)
class IntegrationActionStatus:
    enabled: bool
    disabled_reason: str | None = None


async def integration_action_status(action: IntegrationActionDefinition) -> IntegrationActionStatus:
    try:
        enabled = await action.is_enabled()
    except Exception as exc:
        return IntegrationActionStatus(False, f"Integration status check failed: {exc}")
    return IntegrationActionStatus(enabled, None if enabled else action.disabled_reason)


async def integration_action_catalog() -> list[dict[str, Any]]:
    action_statuses = [(action, await integration_action_status(action)) for action in INTEGRATION_ACTIONS]
    if not action_statuses:
        return []

    providers: dict[str, dict[str, Any]] = {}
    for action, status in action_statuses:
        provider = providers.setdefault(
            action.provider,
            {
                "id": action.provider,
                "label": action.provider_label,
                "description": action.provider_description,
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


def integration_action_config(action_type: str, config: dict[str, Any]) -> dict[str, Any]:
    action = integration_action_for_type(action_type)
    if not action:
        return {}
    if action_type == "integration.whatsapp.send_message":
        target_mode = str(config.get("target_mode") or "selected")
        if target_mode not in {"all", "selected", "dynamic"}:
            target_mode = "selected"
        return {
            "provider": "whatsapp",
            "action": "send_message",
            "target_mode": target_mode,
            "target_user_ids": normalize_string_list(config.get("target_user_ids")),
            "phone_number_template": str(config.get("phone_number_template") or ""),
            "message_template": str(config.get("message_template") or "@Subject"),
        }
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
    status = await integration_action_status(definition)
    if not status.enabled:
        return {
            "id": action.get("id"),
            "type": action_type,
            "status": "skipped",
            "reason": "integration_disabled",
            "integration_provider": definition.provider,
            "integration_action": definition.action,
            "disabled_reason": status.disabled_reason,
        }
    return await definition.execute(session, action, context, rule)


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


async def _whatsapp_enabled() -> bool:
    status = await get_whatsapp_messaging_service().status()
    return bool(status.get("configured"))


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


async def _execute_whatsapp_send_message(
    session: AsyncSession,
    action: dict[str, Any],
    context: Any,
    rule: AutomationRule,
) -> dict[str, Any]:
    return await get_whatsapp_messaging_service().execute_automation_action(
        session,
        action,
        context,
        rule=rule,
    )


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


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
        disabled_reason="No active connected iCloud Calendar account has a valid session.",
    ),
    IntegrationActionDefinition(
        type="integration.whatsapp.send_message",
        provider="whatsapp",
        provider_label="WhatsApp",
        provider_description="Send WhatsApp messages through the Meta Cloud API.",
        action="send_message",
        label="Send WhatsApp Message",
        description="Send a WhatsApp text message to Admin users or a dynamic phone-number variable.",
        is_enabled=_whatsapp_enabled,
        execute=_execute_whatsapp_send_message,
        disabled_reason="WhatsApp is not enabled or is missing an access token and phone number ID.",
        default_config={
            "provider": "whatsapp",
            "action": "send_message",
            "target_mode": "selected",
            "target_user_ids": [],
            "phone_number_template": "",
            "message_template": "@Subject",
        },
    ),
]

INTEGRATION_ACTION_BY_TYPE = {action.type: action for action in INTEGRATION_ACTIONS}
