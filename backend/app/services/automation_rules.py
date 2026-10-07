"""Automation definition mutation and durable audit ownership."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutomationRule, User
from app.services.automation_errors import AutomationError
from app.services.automation_policy import hardware_configuration_error
from app.services.automation_scheduling import next_run_for_triggers
from app.services.automation_serialization import serialize_rule
from app.services.automation_webhooks import harden_webhook_triggers_for_actions
from app.services.mutation_context import require_active_admin
from app.services.telemetry import TELEMETRY_CATEGORY_CRUD, actor_from_user, audit_diff
from app.services.workflows.automation_definition import (
    normalize_actions,
    normalize_conditions,
    normalize_triggers,
    trigger_keys_for_triggers,
)


class RuleAuditWriter(Protocol):
    async def __call__(self, session: AsyncSession, **kwargs: Any) -> Any: ...


class AutomationRuleService:
    def __init__(self, *, audit: RuleAuditWriter) -> None:
        self.audit = audit

    async def list_rules(self, session: AsyncSession) -> list[AutomationRule]:
        return (
            await session.scalars(
                select(AutomationRule).order_by(
                    AutomationRule.created_at.desc(), AutomationRule.name
                )
            )
        ).all()

    async def create_rule(
        self,
        session: AsyncSession,
        *,
        name: str,
        description: str | None = None,
        triggers: Any,
        conditions: Any,
        actions: Any,
        is_active: bool = True,
        created_by: User | None = None,
    ) -> AutomationRule:
        created_by = require_active_admin(created_by)
        name = validated_rule_name(name)
        normalized_triggers = normalize_triggers(triggers, generate_webhook_keys=True)
        normalized_actions = normalize_actions(actions)
        if error := hardware_configuration_error(
            (trigger["type"] for trigger in normalized_triggers),
            (action["type"] for action in normalized_actions),
        ):
            raise AutomationError(error)
        harden_webhook_triggers_for_actions(normalized_triggers, normalized_actions)
        if not normalized_triggers:
            raise AutomationError("At least one automation trigger is required.")
        if not normalized_actions:
            raise AutomationError("At least one automation action is required.")
        now = datetime.now(tz=UTC)
        rule = AutomationRule(
            name=name,
            description=(description or "").strip() or None,
            is_active=is_active,
            triggers=normalized_triggers,
            trigger_keys=trigger_keys_for_triggers(normalized_triggers),
            conditions=normalize_conditions(conditions),
            actions=normalized_actions,
            next_run_at=next_run_for_triggers(normalized_triggers, now=now),
            created_by_user_id=getattr(created_by, "id", None),
        )
        session.add(rule)
        await session.flush()
        await self.audit(
            session,
            category=TELEMETRY_CATEGORY_CRUD,
            action="automation_rule.create",
            actor=actor_from_user(created_by),
            actor_user_id=getattr(created_by, "id", None),
            target_entity="AutomationRule",
            target_id=rule.id,
            target_label=rule.name,
            diff={"old": {}, "new": serialize_rule(rule)},
        )
        return rule

    async def update_rule(
        self,
        session: AsyncSession,
        rule: AutomationRule,
        *,
        actor: User | None = None,
        name: str | None = None,
        description: str | None = None,
        triggers: Any = None,
        conditions: Any = None,
        actions: Any = None,
        is_active: bool | None = None,
    ) -> AutomationRule:
        actor = require_active_admin(actor)
        await session.refresh(rule, with_for_update=True)
        before = serialize_rule(rule)
        normalized_triggers = (
            normalize_triggers(triggers, generate_webhook_keys=True)
            if triggers is not None
            else normalize_triggers(rule.triggers)
        )
        normalized_actions = (
            normalize_actions(actions) if actions is not None else normalize_actions(rule.actions)
        )
        error = hardware_configuration_error(
            (trigger["type"] for trigger in normalized_triggers),
            (action["type"] for action in normalized_actions),
        )
        # Keep an existing unsafe row readable and allow disabling it without
        # rewriting history. New or edited trigger/action combinations still
        # require valid policy; re-enabling also checks the merged configuration.
        disabling_existing = is_active is False and triggers is None and actions is None
        if error and not disabling_existing:
            raise AutomationError(error)
        if name is not None:
            rule.name = validated_rule_name(name)
        if description is not None:
            rule.description = description.strip() or None
        harden_webhook_triggers_for_actions(normalized_triggers, normalized_actions)
        rule.triggers = normalized_triggers
        if triggers is not None:
            if not rule.triggers:
                raise AutomationError("At least one automation trigger is required.")
            rule.trigger_keys = trigger_keys_for_triggers(rule.triggers)
            rule.next_run_at = next_run_for_triggers(
                rule.triggers, now=datetime.now(tz=UTC), last_fired_at=rule.last_fired_at
            )
        if conditions is not None:
            rule.conditions = normalize_conditions(conditions)
        if actions is not None:
            rule.actions = normalized_actions
            if not rule.actions:
                raise AutomationError("At least one automation action is required.")
        if is_active is not None:
            rule.is_active = is_active
            if is_active:
                rule.next_run_at = next_run_for_triggers(
                    rule.triggers,
                    now=datetime.now(tz=UTC),
                    last_fired_at=rule.last_fired_at,
                )
        await self.audit(
            session,
            category=TELEMETRY_CATEGORY_CRUD,
            action="automation_rule.update",
            actor=actor_from_user(actor),
            actor_user_id=getattr(actor, "id", None),
            target_entity="AutomationRule",
            target_id=rule.id,
            target_label=rule.name,
            diff=audit_diff(before, serialize_rule(rule)),
        )
        return rule

    async def delete_rule(
        self, session: AsyncSession, rule: AutomationRule, *, actor: User | None = None
    ) -> None:
        actor = require_active_admin(actor)
        await session.refresh(rule, with_for_update=True)
        before = serialize_rule(rule)
        await self.audit(
            session,
            category=TELEMETRY_CATEGORY_CRUD,
            action="automation_rule.delete",
            actor=actor_from_user(actor),
            actor_user_id=getattr(actor, "id", None),
            target_entity="AutomationRule",
            target_id=rule.id,
            target_label=rule.name,
            diff={"old": before, "new": {}},
        )
        await session.delete(rule)


def validated_rule_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 160:
        raise AutomationError("Automation name must contain 1–160 characters.")
    return name.strip()
