"""Automation Alfred tool handlers."""
# ruff: noqa: F403, F405

from __future__ import annotations

from typing import Any

from app.ai.tool_groups._shared import *


async def _resolve_automation_rule(session, arguments: dict[str, Any]) -> AutomationRule | None:
    rule_id = _uuid_from_value(
        arguments.get("automation_id")
        or arguments.get("automation_rule_id")
        or arguments.get("rule_id")
        or arguments.get("id")
    )
    if rule_id:
        return await session.get(AutomationRule, rule_id)

    rule_name = _normalize(
        arguments.get("automation_name")
        or arguments.get("automation_rule_name")
        or arguments.get("rule_name")
        or arguments.get("name")
    )
    if not rule_name:
        return None
    rules = (await session.scalars(select(AutomationRule).order_by(AutomationRule.name))).all()
    exact = [rule for rule in rules if rule.name.lower() == rule_name]
    if exact:
        return exact[0]
    partial = [rule for rule in rules if rule_name in f"{rule.name} {rule.description or ''}".lower()]
    return partial[0] if len(partial) == 1 else None


async def query_automation_catalog(_arguments: dict[str, Any]) -> dict[str, Any]:
    catalog = await get_automation_service().catalog()
    return {
        **catalog,
        "rule_shape": {
            "triggers": [{"type": "vehicle.outside_schedule", "config": {"person_id": "<resolved-person-id>"}}],
            "conditions": [{"type": "maintenance_mode.disabled", "config": {}}],
                "actions": [{"type": "gate.open", "config": {}, "reason_template": "@DisplayName arrived outside schedule."}],
                "integration_action_example": [
                    {
                        "type": "integration.icloud_calendar.sync",
                        "config": {"provider": "icloud_calendar", "action": "sync_calendars"},
                        "reason_template": "Automation synced iCloud Calendar.",
                    }
                ],
            },
        "example": (
            "For 'open the gate if Steph arrives outside her schedule', resolve Steph first, then create "
            "a rule with trigger vehicle.outside_schedule filtered by person_id and action gate.open."
        ),
    }


async def query_automations(arguments: dict[str, Any]) -> dict[str, Any]:
    trigger_filter = str(arguments.get("trigger_key") or "").strip()
    active_filter = arguments.get("is_active")
    search = _normalize(arguments.get("search"))
    limit = _bounded_int(arguments.get("limit"), default=20, minimum=1, maximum=100)
    async with AsyncSessionLocal() as session:
        query = select(AutomationRule)
        if trigger_filter:
            query = query.where(AutomationRule.trigger_keys.contains([trigger_filter]))
        if isinstance(active_filter, bool):
            query = query.where(AutomationRule.is_active.is_(active_filter))
        if search:
            pattern = f"%{search}%"
            search_filters = [
                func.lower(AutomationRule.name).like(pattern),
                func.lower(func.coalesce(AutomationRule.description, "")).like(pattern),
                AutomationRule.trigger_keys.contains([search]),
            ]
            query = query.where(or_(*search_filters))
        rules = (
            await session.scalars(
                query.order_by(AutomationRule.created_at.desc(), AutomationRule.name).limit(limit)
            )
        ).all()

    automations = []
    for rule in rules:
        serialized = serialize_automation_rule(rule)
        automations.append(serialized)
    return {"automations": automations, "count": len(automations)}


async def get_automation(arguments: dict[str, Any]) -> dict[str, Any]:
    include_dry_run = bool(arguments.get("include_dry_run", True))
    async with AsyncSessionLocal() as session:
        rule = await _resolve_automation_rule(session, arguments)
        if not rule:
            return {"found": False, "error": "Automation rule not found."}
        serialized = serialize_automation_rule(rule)
    result: dict[str, Any] = {"found": True, "automation": serialized}
    if include_dry_run:
        result["dry_run"] = await get_automation_service().dry_run_rule(serialized)
    return result


async def create_automation(arguments: dict[str, Any]) -> dict[str, Any]:
    if not bool(arguments.get("confirm")):
        return {
            "created": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "automation_name": str(arguments.get("name") or "automation").strip(),
            "detail": "Create this automation? Active rules may later perform real system actions.",
        }
    name = str(arguments.get("name") or "").strip()
    triggers = normalize_automation_triggers(arguments.get("triggers"))
    actions = normalize_automation_actions(arguments.get("actions"))
    if not name:
        return {"created": False, "error": "Automation name is required."}
    if not triggers:
        return {"created": False, "error": "At least one automation trigger is required."}
    if not actions:
        return {"created": False, "error": "At least one automation action is required."}

    user = await _chat_context_user()
    async with AsyncSessionLocal() as session:
        try:
            rule = await get_automation_service().create_rule(
                session,
                name=name,
                description=_optional_text(arguments.get("description")),
                triggers=triggers,
                conditions=normalize_automation_conditions(arguments.get("conditions")),
                actions=actions,
                is_active=arguments.get("is_active", True) is not False,
                created_by=user,
            )
            await session.commit()
            await session.refresh(rule)
        except (AutomationError, IntegrityError) as exc:
            await session.rollback()
            return {"created": False, "error": str(exc)}
        serialized = serialize_automation_rule(rule)
    return {
        "created": True,
        "automation": serialized,
        "dry_run": await get_automation_service().dry_run_rule(serialized),
    }


async def edit_automation(arguments: dict[str, Any]) -> dict[str, Any]:
    if not bool(arguments.get("confirm")):
        return {
            "updated": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "automation_name": str(arguments.get("automation_name") or arguments.get("name") or "automation").strip(),
            "detail": "Update this automation? Future matching events may use the changed rule.",
        }
    user = await _chat_context_user()
    async with AsyncSessionLocal() as session:
        rule = await _resolve_automation_rule(session, arguments)
        if not rule:
            return {"updated": False, "error": "Automation rule not found."}
        try:
            await get_automation_service().update_rule(
                session,
                rule,
                actor=user,
                name=str(arguments["name"]).strip() if "name" in arguments else None,
                description=str(arguments.get("description") or "").strip() if "description" in arguments else None,
                triggers=normalize_automation_triggers(arguments.get("triggers")) if "triggers" in arguments else None,
                conditions=normalize_automation_conditions(arguments.get("conditions")) if "conditions" in arguments else None,
                actions=normalize_automation_actions(arguments.get("actions")) if "actions" in arguments else None,
                is_active=bool(arguments.get("is_active")) if "is_active" in arguments else None,
            )
            await session.commit()
            await session.refresh(rule)
        except (AutomationError, IntegrityError) as exc:
            await session.rollback()
            return {"updated": False, "error": str(exc)}
        serialized = serialize_automation_rule(rule)
    return {
        "updated": True,
        "automation": serialized,
        "dry_run": await get_automation_service().dry_run_rule(serialized),
    }


async def delete_automation(arguments: dict[str, Any]) -> dict[str, Any]:
    if not bool(arguments.get("confirm")):
        return {
            "deleted": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "automation_name": str(arguments.get("automation_name") or arguments.get("automation_id") or "automation").strip(),
            "detail": "Delete this automation rule?",
        }
    user = await _chat_context_user()
    async with AsyncSessionLocal() as session:
        rule = await _resolve_automation_rule(session, arguments)
        if not rule:
            return {"deleted": False, "error": "Automation rule not found."}
        serialized = serialize_automation_rule(rule)
        await get_automation_service().delete_rule(session, rule, actor=user)
        await session.commit()
    return {"deleted": True, "automation": serialized}


async def enable_automation(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _set_automation_active(arguments, active=True)


async def disable_automation(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _set_automation_active(arguments, active=False)


async def _set_automation_active(arguments: dict[str, Any], *, active: bool) -> dict[str, Any]:
    if not bool(arguments.get("confirm")):
        return {
            "updated": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "automation_name": str(arguments.get("automation_name") or arguments.get("automation_id") or "automation").strip(),
            "detail": f"{'Enable' if active else 'Disable'} this automation rule?",
        }
    user = await _chat_context_user()
    async with AsyncSessionLocal() as session:
        rule = await _resolve_automation_rule(session, arguments)
        if not rule:
            return {"updated": False, "error": "Automation rule not found."}
        await get_automation_service().update_rule(session, rule, actor=user, is_active=active)
        await session.commit()
        await session.refresh(rule)
        return {"updated": True, "automation": serialize_automation_rule(rule)}
