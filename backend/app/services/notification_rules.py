"""Notification rule mutations. No dispatch or provider calls."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NotificationRule, User
from app.services.auth_root import reload_auth_root_for_transaction
from app.services.mutation_context import MutationError, load_active_admin, require_active_admin
from app.services.notification_recipient_binding import (
    recipient_binding_error,
    referenced_recipients,
)
from app.services.settings import get_runtime_config_for_session
from app.services.telemetry import (
    TELEMETRY_CATEGORY_CRUD,
    actor_from_user,
    audit_diff,
    write_audit_log,
)
from app.services.workflows.action_definitions import WorkflowConfigurationError
from app.services.workflows.channel_definitions import validate_notification_actions
from app.services.workflows.notification_payloads import normalize_rule_payload
from app.services.workflows.template_recipients import TemplateRecipientError


async def _fresh_actor(session: AsyncSession, user: User) -> User:
    identity, version = user.id, user.auth_session_version
    # Settings writes take this same root fence before mutable configuration.
    # No provider discovery/I/O belongs inside this transaction.
    await reload_auth_root_for_transaction(session)
    return await load_active_admin(session, identity, auth_version=version, lock=True)


async def _validate_recipients(session: AsyncSession, actions: list[dict[str, Any]]) -> None:
    try:
        validate_notification_actions(actions)
    except WorkflowConfigurationError as exc:
        raise MutationError("invalid_actions", str(exc)) from exc
    positional = any(any(ref.startswith("apprise:") for ref in referenced_recipients(action))
                     or action.get("frozen_apprise_indexes") for action in actions)
    configured = (await get_runtime_config_for_session(session)).apprise_urls if positional else ""
    for action in actions:
        if reason := recipient_binding_error(action, configured):
            raise MutationError("recipient_binding", "Notification recipients need review. Select current destinations again (" + reason + ").")


def rule_values(payload: dict[str, Any]) -> dict[str, Any]:
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 160:
        raise MutationError("invalid_rule", "Workflow name must contain 1–160 characters.")
    try:
        normalized = normalize_rule_payload({
            **payload, "name": name.strip(),
            "actions": validate_notification_actions(payload.get("actions")),
        })
    except WorkflowConfigurationError as exc:
        raise MutationError("invalid_actions", str(exc)) from exc
    except TemplateRecipientError as exc:
        raise MutationError("invalid_variable_recipients", str(exc)) from exc
    if not normalized["trigger_event"]:
        raise MutationError("invalid_rule", "A notification trigger is required.")
    if not normalized["actions"]:
        raise MutationError("invalid_rule", "At least one notification action is required.")
    return {
        key: normalized[key]
        for key in ("name", "trigger_event", "conditions", "actions", "is_active")
    }


def rule_audit_snapshot(rule: NotificationRule) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "trigger_event": rule.trigger_event,
        "conditions": rule.conditions,
        "actions": rule.actions,
        "is_active": rule.is_active,
    }


async def _locked_rule(session: AsyncSession, rule_id: UUID) -> NotificationRule:
    rule = await session.scalar(
        select(NotificationRule)
        .where(NotificationRule.id == rule_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if rule is None:
        raise MutationError("not_found", "Notification workflow not found.")
    return rule


async def set_automation_activation(
    session: AsyncSession,
    *,
    reference: dict[str, Any],
    active: bool,
) -> dict[str, Any]:
    """Participate in a standing automation's transaction without an Admin identity.

    The caller commits this state change with its AutomationRun and machine
    audit, or rolls them all back. This path cannot edit notification content,
    dispatch a notification, commit independently, or grant interactive access.
    """
    try:
        rule_id = UUID(str(reference.get("notification_rule_id")))
    except (TypeError, ValueError):
        rule_id = None
    if rule_id is None:
        name = str(reference.get("notification_rule_name") or "").strip().lower()
        if not name:
            raise MutationError("not_found", "Notification workflow not found.")
        # Preserve exact-name preference and the existing unique-partial match
        # contract. Lock and refresh the selected row before changing its state.
        rules = (
            await session.scalars(select(NotificationRule).order_by(NotificationRule.name))
        ).all()
        exact = [rule for rule in rules if rule.name.lower() == name]
        partial = [rule for rule in rules if name in rule.name.lower()]
        selected = exact[0] if exact else partial[0] if len(partial) == 1 else None
        if selected is None:
            raise MutationError("not_found", "Notification workflow not found.")
        rule_id = selected.id
    rule = await _locked_rule(session, rule_id)
    if active:
        await _validate_recipients(session, rule.actions)
    rule.is_active = active
    return {"notification_rule_id": str(rule.id), "is_active": rule.is_active}


async def _audit(
    session: AsyncSession,
    rule: NotificationRule,
    user: User,
    action: str,
    before: dict[str, Any],
    source: str,
) -> None:
    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_CRUD,
        action="notification_rule." + action,
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="NotificationRule",
        target_id=rule.id,
        target_label=rule.name,
        diff=audit_diff(before, {} if action == "delete" else rule_audit_snapshot(rule)),
        metadata={"source": source},
    )


async def create_rule(
    session: AsyncSession, payload: dict[str, Any], *, user: User | None, source: str
) -> NotificationRule:
    actor = require_active_admin(user)
    values = rule_values(payload)
    try:
        actor = await _fresh_actor(session, actor)
        await _validate_recipients(session, values["actions"])
        rule = NotificationRule(**values)
        session.add(rule)
        await session.flush()
        await _audit(session, rule, actor, "create", {}, source)
        await session.flush()
        await session.refresh(rule)
        await session.commit()
        return rule
    except Exception:
        await session.rollback()
        raise


async def update_rule(
    session: AsyncSession, rule_id: UUID, changes: dict[str, Any], *, user: User | None, source: str
) -> NotificationRule:
    actor = require_active_admin(user)
    try:
        actor = await _fresh_actor(session, actor)
        rule = await _locked_rule(session, rule_id)
        before = rule_audit_snapshot(rule)
        values = rule_values({**before, **changes})
        if "actions" in changes or changes.get("is_active") is True:
            await _validate_recipients(session, values["actions"])
        for key, value in values.items():
            setattr(rule, key, value)
        await _audit(session, rule, actor, "update", before, source)
        await session.flush()
        await session.refresh(rule)
        await session.commit()
        return rule
    except Exception:
        await session.rollback()
        raise


async def delete_rule(
    session: AsyncSession, rule_id: UUID, *, user: User | None, source: str
) -> NotificationRule:
    actor = require_active_admin(user)
    try:
        actor = await _fresh_actor(session, actor)
        rule = await _locked_rule(session, rule_id)
        await _audit(session, rule, actor, "delete", rule_audit_snapshot(rule), source)
        await session.delete(rule)
        await session.commit()
        return rule
    except Exception:
        await session.rollback()
        raise
