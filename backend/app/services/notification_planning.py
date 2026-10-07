"""Notification rule planning and condition evaluation; no delivery or claims."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import NotificationRule, Presence, Schedule
from app.models.enums import PresenceState
from app.modules.notifications.base import NotificationContext
from app.services.notification_rendering import (
    context_occurred_at,
    context_variables,
    gate_malfunction_notification_content,
    snapshot_payload,
)
from app.services.schedules import schedule_allows_at
from app.services.settings import RuntimeConfig
from app.services.workflow_session import SessionFactory
from app.services.workflows import notification_payloads
from app.services.workflows.catalog import GATE_MALFUNCTION_EVENT_TYPE
from app.services.workflows.context import normalize_string_list, render_template
from app.services.workflows.execution_contracts import (
    NotificationPlanItem,
    checked_notification_plan,
)
from app.services.workflows.template_recipients import recipient_content

logger = get_logger(__name__)


class NotificationPlanner:
    def __init__(
        self, *, sessions: SessionFactory, config: Callable[[], Awaitable[RuntimeConfig]]
    ) -> None:
        self.sessions = sessions
        self.config = config

    async def build(
        self, context: NotificationContext, rules_override: list[dict[str, Any]] | None
    ) -> list[NotificationPlanItem]:
        rules = await self._rules_for_context(context, rules_override)
        plan: list[dict[str, Any]] = []
        for rule in rules:
            origin = (
                notification_rule_origin(rule)
                if rules_override is None and isinstance(rule, NotificationRule)
                else None
            )
            rendered = self.render_rule(rule, context)
            if not await self.conditions_match(rule, context):
                plan.append({"rule": rendered, "state": "skipped", "reason": "conditions_not_met"})
                continue
            if context.event_type == GATE_MALFUNCTION_EVENT_TYPE:
                rendered["actions"] = await self._gate_malfunction_actions_for_delivery(
                    rendered["actions"], context
                )
            for action in rendered["actions"]:
                item = {
                    "rule": {key: value for key, value in rendered.items() if key != "actions"},
                    "action": action,
                    "state": "pending",
                }
                if origin is not None:
                    item["rule_origin"] = origin
                plan.append(item)
        return checked_notification_plan(
            plan
            or [
                {
                    "state": "skipped",
                    "reason": "no_matching_workflow"
                    if not rules
                    else "no_workflow_actions_delivered",
                }
            ]
        )

    async def _rules_for_context(
        self,
        context: NotificationContext,
        rules_override: list[dict[str, Any]] | None,
    ) -> list[NotificationRule | dict[str, Any]]:
        if rules_override is not None:
            return [notification_payloads.normalize_rule_payload(rule) for rule in rules_override]
        async with self.sessions() as session:
            return (
                await session.scalars(
                    select(NotificationRule)
                    .where(
                        NotificationRule.trigger_event == context.event_type,
                        NotificationRule.is_active.is_(True),
                    )
                    .order_by(NotificationRule.created_at)
                )
            ).all()

    async def conditions_match(
        self,
        rule: NotificationRule | dict[str, Any],
        context: NotificationContext,
    ) -> bool:
        conditions = rule_conditions(rule)
        if not conditions:
            return True
        occurred_at = context_occurred_at(context)
        config = await self.config()
        async with self.sessions() as session:
            for condition in conditions:
                if not await self._condition_matches(
                    session, condition, context, occurred_at, config
                ):
                    return False
        return True

    def render_rule(
        self,
        rule: NotificationRule | dict[str, Any],
        context: NotificationContext,
    ) -> dict[str, Any]:
        active_context = context
        variables = context_variables(active_context)
        rendered_actions: list[dict[str, Any]] = []
        for action in rule_actions(rule):
            action_type = str(action.get("type") or "")
            media = notification_payloads.normalize_media(action.get("media"))
            title_template = str(action.get("title_template") or "")
            message_template = str(action.get("message_template") or "")
            gate_malfunction_stages = notification_payloads.normalize_gate_malfunction_stages(
                action.get("gate_malfunction_stages")
            )
            if active_context.event_type == GATE_MALFUNCTION_EVENT_TYPE:
                content = gate_malfunction_notification_content(
                    action_type,
                    active_context,
                    previous_notification=_context_bool(
                        active_context.facts.get("malfunction_has_previous_notification")
                    ),
                )
                rendered_title = content["title"]
                rendered_message = content["body"]
            else:
                rendered_title = render_template(title_template, variables)
                rendered_message = render_template(message_template, variables)
            scoped_content = None
            if action.get("variable_recipients"):
                default_content, scoped_content = recipient_content(action, variables)
                rendered_title, rendered_message = (
                    default_content["title"],
                    default_content["message"],
                )
            rendered_actions.append(
                {
                    "id": str(action.get("id") or f"action-{len(rendered_actions) + 1}"),
                    "type": action_type,
                    "target_mode": str(action.get("target_mode") or "all"),
                    "target_ids": normalize_string_list(
                        action.get("target_ids"), allow_scalar=False
                    ),
                    "title": rendered_title,
                    "message": rendered_message,
                    "title_template": title_template,
                    "message_template": message_template,
                    "gate_malfunction_stages": gate_malfunction_stages,
                    "media": media,
                    "actionable": notification_payloads.normalize_actionable(
                        action.get("actionable")
                    ),
                    "snapshot": snapshot_payload(media),
                    **(
                        {
                            "variable_recipients": action["variable_recipients"],
                            "recipient_content": scoped_content,
                        }
                        if scoped_content is not None
                        else {}
                    ),
                }
            )
        return {
            "id": rule_id(rule),
            "name": rule_name(rule),
            "trigger_event": rule_trigger_event(rule),
            "is_active": rule_is_active(rule),
            "conditions": rule_conditions(rule),
            "actions": rendered_actions,
        }

    async def _condition_matches(
        self,
        session: AsyncSession,
        condition: dict[str, Any],
        context: NotificationContext,
        occurred_at: datetime,
        config: RuntimeConfig,
    ) -> bool:
        condition_type = str(condition.get("type") or "")
        if condition_type == "schedule":
            schedule_id = str(condition.get("schedule_id") or "")
            try:
                parsed_schedule_id = uuid.UUID(schedule_id)
            except ValueError:
                return False
            schedule = await session.get(Schedule, parsed_schedule_id)
            if not schedule:
                return False
            return schedule_allows_at(schedule, occurred_at, config.site_timezone)

        if condition_type == "presence":
            rows = (await session.scalars(select(Presence))).all()
            present_ids = {str(row.person_id) for row in rows if row.state == PresenceState.PRESENT}
            return presence_condition_matches(condition, present_ids)

        logger.warning(
            "notification_condition_unknown",
            extra={"condition_type": condition_type, "event_type": context.event_type},
        )
        return False

    async def _gate_malfunction_actions_for_delivery(
        self,
        actions: list[dict[str, Any]],
        context: NotificationContext,
    ) -> list[dict[str, Any]]:
        stage = notification_payloads.normalize_gate_malfunction_stage(
            context.facts.get("malfunction_stage")
        )
        selected: list[dict[str, Any]] = []
        for action in actions:
            if not gate_malfunction_action_supports_stage(action, stage):
                continue
            content = gate_malfunction_notification_content(
                str(action.get("type") or ""),
                context,
                previous_notification=_context_bool(
                    context.facts.get("malfunction_has_previous_notification")
                ),
            )
            selected.append(
                {
                    **action,
                    "title": content["title"],
                    "message": content["body"],
                }
            )
        return selected


def gate_malfunction_action_supports_stage(action: dict[str, Any], stage: str) -> bool:
    stages = notification_payloads.normalize_gate_malfunction_stages(
        action.get("gate_malfunction_stages")
    )
    return not stages or notification_payloads.normalize_gate_malfunction_stage(stage) in stages


def _context_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def presence_condition_matches(condition: dict[str, Any], present_person_ids: set[str]) -> bool:
    mode = str(condition.get("mode") or "")
    if mode == "no_one_home":
        return not present_person_ids
    if mode == "someone_home":
        return bool(present_person_ids)
    if mode == "person_home":
        return str(condition.get("person_id") or "") in present_person_ids
    return False


def rule_id(rule: NotificationRule | dict[str, Any]) -> str:
    return str(rule.id if isinstance(rule, NotificationRule) else rule.get("id") or "")


def rule_name(rule: NotificationRule | dict[str, Any]) -> str:
    return str(
        rule.name
        if isinstance(rule, NotificationRule)
        else rule.get("name") or "Notification Workflow"
    )


def rule_trigger_event(rule: NotificationRule | dict[str, Any]) -> str:
    return str(
        rule.trigger_event
        if isinstance(rule, NotificationRule)
        else rule.get("trigger_event") or ""
    )


def rule_conditions(rule: NotificationRule | dict[str, Any]) -> list[dict[str, Any]]:
    return notification_payloads.normalize_conditions(
        rule.conditions if isinstance(rule, NotificationRule) else rule.get("conditions")
    )


def rule_actions(rule: NotificationRule | dict[str, Any]) -> list[dict[str, Any]]:
    return notification_payloads.normalize_actions(
        rule.actions if isinstance(rule, NotificationRule) else rule.get("actions")
    )


def rule_is_active(rule: NotificationRule | dict[str, Any]) -> bool:
    return bool(
        rule.is_active if isinstance(rule, NotificationRule) else rule.get("is_active", True)
    )


def notification_rule_origin(rule: NotificationRule) -> dict[str, str]:
    definition = {
        "id": str(rule.id),
        "name": rule.name,
        "trigger_event": rule.trigger_event,
        "conditions": rule.conditions,
        "actions": rule.actions,
        "is_active": rule.is_active,
    }
    return {
        "rule_id": str(rule.id),
        "definition_fingerprint": notification_payloads.notification_rule_definition_fingerprint(
            definition
        ),
    }
