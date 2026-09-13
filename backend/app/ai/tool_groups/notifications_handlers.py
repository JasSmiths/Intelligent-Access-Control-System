"""Notification Alfred tool handlers."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import (
    func,
    or_,
    select,
)

from app.ai.context import get_chat_tool_context
from app.ai.tool_groups._shared import (
    _agent_datetime_display,
    _agent_datetime_iso,
    _bounded_int,
    _chat_context_user,
    _compact_observation,
    _normalize,
    _require_admin_user,
    _uuid_from_value,
    logger,
)
from app.db.session import AsyncSessionLocal
from app.models import NotificationRule
from app.modules.notifications.base import (
    NotificationContext,
    NotificationDeliveryError,
)
from app.services import notification_rules
from app.services.mutation_context import MutationError
from app.services.notifications import (
    get_notification_service,
    notification_context_from_payload,
    sample_notification_context,
)
from app.services.type_helpers import (
    as_dict_list,
    as_list,
)
from app.services.workflows.notification_payloads import (
    normalize_actions,
    normalize_conditions,
    normalize_rule_payload,
)


async def _resolve_notification_rule(session, arguments: dict[str, Any]) -> NotificationRule | None:
    rule_id = _uuid_from_value(
        arguments.get("rule_id")
        or arguments.get("notification_rule_id")
        or arguments.get("id")
    )
    if rule_id:
        return await session.get(NotificationRule, rule_id)

    rule_name = _normalize(
        arguments.get("rule_name")
        or arguments.get("notification_rule_name")
        or arguments.get("name")
    )
    if not rule_name:
        return None
    rules = (await session.scalars(select(NotificationRule).order_by(NotificationRule.name))).all()
    exact = [rule for rule in rules if rule.name.lower() == rule_name]
    if exact:
        return exact[0]
    partial = [rule for rule in rules if rule_name in f"{rule.name} {rule.trigger_event}".lower()]
    return partial[0] if len(partial) == 1 else None


async def _notification_rule_payload_for_agent(arguments: dict[str, Any]) -> dict[str, Any] | None:
    raw_rule = arguments.get("rule")
    if isinstance(raw_rule, dict):
        if raw_rule.get("id"):
            return normalize_rule_payload(raw_rule)
        # Unsaved previews need a stable content identity across confirmation.
        # This is never a persisted rule ID or the delivery operation identity.
        draft = normalize_rule_payload({**raw_rule, "id": "draft"})
        draft["id"] = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            "iacs:notification-preview:" + json.dumps(draft, sort_keys=True, separators=(",", ":")),
        ))
        return draft
    async with AsyncSessionLocal() as session:
        rule = await _resolve_notification_rule(session, arguments)
        if not rule:
            return None
        return _serialize_notification_rule_for_agent(rule)


def _serialize_notification_rule_for_agent(rule: NotificationRule) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "trigger_event": rule.trigger_event,
        "conditions": normalize_conditions(rule.conditions),
        "actions": normalize_actions(rule.actions),
        "is_active": rule.is_active,
        "last_fired_at": _agent_datetime_iso(rule.last_fired_at) if rule.last_fired_at else None,
        "last_fired_at_display": _agent_datetime_display(rule.last_fired_at) if rule.last_fired_at else None,
        "created_at": _agent_datetime_iso(rule.created_at) if rule.created_at else None,
        "created_at_display": _agent_datetime_display(rule.created_at) if rule.created_at else None,
        "updated_at": _agent_datetime_iso(rule.updated_at) if rule.updated_at else None,
        "updated_at_display": _agent_datetime_display(rule.updated_at) if rule.updated_at else None,
    }


def _notification_context_for_agent(value: Any, trigger_event: str) -> NotificationContext:
    if isinstance(value, dict):
        payload = dict(value)
        payload.setdefault("event_type", trigger_event or payload.get("trigger_event") or "integration_test")
        if not isinstance(payload.get("facts"), dict):
            reserved = {"event_type", "trigger_event", "subject", "severity"}
            payload["facts"] = {
                str(key): item
                for key, item in payload.items()
                if key not in reserved
            }
        return notification_context_from_payload(payload)
    return sample_notification_context(trigger_event or "integration_test")


def _compact_notification_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    actions = as_dict_list(workflow.get("actions"))
    conditions = as_list(workflow.get("conditions"))
    return _compact_observation(
        {
            "id": workflow.get("id"),
            "name": workflow.get("name"),
            "trigger_event": workflow.get("trigger_event"),
            "is_active": workflow.get("is_active"),
            "condition_count": len(conditions),
            "action_count": len(actions),
            "channels": [
                action.get("channel") or action.get("type")
                for action in actions
                if isinstance(action, dict)
            ],
            "last_fired_at": workflow.get("last_fired_at"),
            "title_template": workflow.get("title_template"),
            "message_template": workflow.get("message_template"),
        }
    )


async def query_notification_catalog(_arguments: dict[str, Any]) -> dict[str, Any]:
    catalog = await get_notification_service().catalog()
    return {
        **catalog,
        "workflow_shape": {
            "conditions": ["schedule", "presence"],
            "actions": ["mobile", "voice", "in_app"],
            "variables": "Use @Variable tokens in title_template and message_template.",
        },
    }


async def query_notification_workflows(arguments: dict[str, Any]) -> dict[str, Any]:
    trigger_filter = str(arguments.get("trigger_event") or "").strip()
    active_filter = arguments.get("is_active")
    search = _normalize(arguments.get("search"))
    include_preview = bool(arguments.get("include_preview"))
    limit = _bounded_int(arguments.get("limit"), default=20, minimum=1, maximum=100)
    summarize_payload = arguments.get("summarize_payload")
    summarize_payload = True if summarize_payload is None else bool(summarize_payload)

    async with AsyncSessionLocal() as session:
        query = select(NotificationRule)
        if trigger_filter:
            query = query.where(NotificationRule.trigger_event == trigger_filter)
        if isinstance(active_filter, bool):
            query = query.where(NotificationRule.is_active.is_(active_filter))
        if search:
            pattern = f"%{search}%"
            query = query.where(
                or_(
                    func.lower(NotificationRule.name).like(pattern),
                    func.lower(NotificationRule.trigger_event).like(pattern),
                )
            )
        rules = (
            await session.scalars(
                query.order_by(NotificationRule.created_at.desc(), NotificationRule.name).limit(limit)
            )
        ).all()

    workflows: list[dict[str, Any]] = []
    for rule in rules:
        workflow = _serialize_notification_rule_for_agent(rule)
        if summarize_payload:
            workflow = _compact_notification_workflow(workflow)
        if include_preview:
            workflow["preview"] = await get_notification_service().preview_rule(workflow)
        workflows.append(workflow)
    return {"workflows": workflows, "count": len(workflows)}


async def get_notification_workflow(arguments: dict[str, Any]) -> dict[str, Any]:
    include_preview = bool(arguments.get("include_preview", True))
    async with AsyncSessionLocal() as session:
        rule = await _resolve_notification_rule(session, arguments)
        if not rule:
            return {"found": False, "error": "Notification workflow not found."}
        workflow = _serialize_notification_rule_for_agent(rule)

    result: dict[str, Any] = {"found": True, "workflow": workflow}
    if include_preview:
        result["preview"] = await get_notification_service().preview_rule(workflow)
    return result


async def create_notification_workflow(arguments: dict[str, Any]) -> dict[str, Any]:
    if not bool(arguments.get("confirm")):
        name = str(arguments.get("name") or "notification workflow").strip()
        return {
            "created": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "workflow_name": name,
            "detail": "Create this notification workflow? Future matching events may send real notifications.",
        }

    async with AsyncSessionLocal() as session:
        try:
            rule = await notification_rules.create_rule(session, arguments, user=await _chat_context_user(), source="alfred")
        except MutationError as exc:
            return {"created": False, "error": str(exc), "error_code": exc.code}
        workflow = _serialize_notification_rule_for_agent(rule)
    return {"created": True, "workflow": workflow, **await _optional_rule_preview(workflow)}


async def update_notification_workflow(arguments: dict[str, Any]) -> dict[str, Any]:
    if not bool(arguments.get("confirm")):
        name = str(arguments.get("rule_name") or arguments.get("name") or "notification workflow").strip()
        return {
            "updated": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "workflow_name": name,
            "detail": "Update this notification workflow? Future matching events may use the changed delivery rules.",
        }

    async with AsyncSessionLocal() as session:
        rule = await _resolve_notification_rule(session, arguments)
        if not rule:
            return {"updated": False, "error": "Notification workflow not found."}

        changes: dict[str, Any] = {key: arguments[key] for key in ("name", "trigger_event", "conditions", "actions", "is_active") if key in arguments}
        try:
            rule = await notification_rules.update_rule(session, rule.id, changes, user=await _chat_context_user(), source="alfred")
        except MutationError as exc:
            return {"updated": False, "error": str(exc), "error_code": exc.code}
        workflow = _serialize_notification_rule_for_agent(rule)
    return {"updated": True, "workflow": workflow, **await _optional_rule_preview(workflow)}


async def delete_notification_workflow(arguments: dict[str, Any]) -> dict[str, Any]:
    if not bool(arguments.get("confirm")):
        name = str(arguments.get("rule_name") or arguments.get("rule_id") or "notification workflow").strip()
        return {
            "deleted": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "workflow_name": name,
            "detail": "Delete this notification workflow?",
        }

    async with AsyncSessionLocal() as session:
        rule = await _resolve_notification_rule(session, arguments)
        if not rule:
            return {"deleted": False, "error": "Notification workflow not found."}
        try:
            rule = await notification_rules.delete_rule(session, rule.id, user=await _chat_context_user(), source="alfred")
        except MutationError as exc:
            return {"deleted": False, "error": str(exc), "error_code": exc.code}
        workflow = _serialize_notification_rule_for_agent(rule)
    return {"deleted": True, "workflow": workflow}


async def _optional_rule_preview(workflow: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"preview": await get_notification_service().preview_rule(workflow)}
    except Exception:  # noqa: BLE001 - Preview failure must not undo a committed rule.
        logger.exception("notification_rule_saved_preview_failed")
        return {"warnings": ["The workflow was saved, but its preview is unavailable."]}


async def preview_notification_workflow(arguments: dict[str, Any]) -> dict[str, Any]:
    rule = await _notification_rule_payload_for_agent(arguments)
    if not rule:
        return {"previewed": False, "error": "Supply rule payload, rule_id, or rule_name."}
    context = _notification_context_for_agent(arguments.get("context"), rule["trigger_event"])
    return {
        "previewed": True,
        "preview": await get_notification_service().preview_rule(rule, context),
    }


async def test_notification_workflow(arguments: dict[str, Any]) -> dict[str, Any]:
    user = await _require_admin_user("notification tests")
    if isinstance(user, dict):
        return {"sent": False, "error": user["error"]}
    rule = await _notification_rule_payload_for_agent(arguments)
    if not rule:
        return {"sent": False, "error": "Supply rule payload, rule_id, or rule_name."}
    if not rule["trigger_event"]:
        return {"sent": False, "error": "A trigger_event is required before sending a test."}
    if not rule["actions"]:
        return {"sent": False, "error": "At least one notification action is required before sending a test."}
    context = _notification_context_for_agent(arguments.get("context"), rule["trigger_event"])
    service = get_notification_service()
    try:
        prepared = await service.prepare_confirmed_delivery(
            context, action="notification_rule.test", rules_override=[rule],
        )
    except (NotificationDeliveryError, ValueError) as exc:
        return {"sent": False, "error": str(exc)}
    preview = {"plan": prepared[0], "configuration_binding": prepared[1]}
    if not bool(arguments.get("confirm_send")):
        return {
            "sent": False, "requires_confirmation": True, "confirmation_field": "confirm_send",
            "workflow_name": rule.get("name") or "notification workflow", "prepared_delivery": preview,
            "detail": "Send a real test notification for this workflow?",
        }
    request_context = get_chat_tool_context()
    approval = request_context.get("approval") or {}
    if (approval.get("requester_user_id") != str(user.id)
            or approval.get("requester_auth_session_version") != user.auth_session_version
            or approval.get("tool_name") != "test_notification_workflow"
            or (approval.get("preview_output") or {}).get("prepared_delivery") != preview
            or not request_context.get("intent_id")):
        return {"sent": False, "error": "Create a fresh requester-bound notification preview before sending."}
    try:
        async with AsyncSessionLocal() as session:
            identity, claimed = await service.reserve_confirmed_in_session(
                session, actor_user_id=user.id, auth_version=user.auth_session_version,
                operation_id=request_context["intent_id"], authority="alfred", action="notification_rule.test",
                context=context, rules_override=[rule], prepared=prepared,
            )
            await session.commit()
        result = await service.dispatch_reserved(identity, claimed)
    except (MutationError, NotificationDeliveryError, ValueError) as exc:
        return {"sent": False, "error": str(exc)}
    return {
        "sent": result.status == "sent", "title": result.notification.title, "body": result.notification.body,
        "notification_run_id": result.run_id, "delivery_status": result.status,
        "preview": await service.preview_rule(rule, context),
    }
