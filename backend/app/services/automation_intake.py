"""Atomic automation intake, called inside the authoritative origin transaction.

No commit, wake, realtime publication, provider call or execution occurs here.
The caller supplies durable origin identity and facts captured by its mutation.
Target preview only reads configuration; dispatch revalidates it before I/O.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutomationRule
from app.services.access_device_configuration import AccessDeviceConfiguration
from app.services.automation_authorization import automation_rule_fingerprint
from app.services.automation_execution import AutomationRunStore, occurrence_key
from app.services.automation_policy import HARDWARE_ACTION_TYPES, hardware_action_denial
from app.services.telemetry import sanitize_payload
from app.services.type_helpers import as_dict
from app.services.workflows.automation_definition import (
    AutomationContext, captured_automation_context, normalize_actions, normalize_conditions, normalize_triggers, trigger_matches,
)
from app.services.workflows.context import normalize_string_list

async def reserve_trigger(
    session: AsyncSession, trigger_key: str, payload: dict[str, Any], *,
    origin_kind: str, origin_id: str, actor: str = "Automation Engine", source: str = "domain", trace_id: str | None = None,
    eligible_rule_ids: set[uuid.UUID] | None = None,
) -> list[uuid.UUID]:
    """Required-origin participant: capture facts and reserve runs before caller commit.

    The origin ID comes from a persisted server event/transition, never from
    an arbitrary transport payload. There is no publish, commit or provider
    call in this entry point. Producers may wake the dispatcher after commit.
    """
    if not origin_kind.strip() or not origin_id.strip() or not trigger_key.strip():
        raise ValueError("A stable automation origin and trigger are required.")
    context = captured_automation_context(trigger_key, payload)
    statement = select(AutomationRule).where(
        AutomationRule.is_active.is_(True), AutomationRule.trigger_keys.contains([trigger_key]))
    if eligible_rule_ids is not None:
        # The verified webhook admission owner supplies these IDs, never its body.
        statement = statement.where(AutomationRule.id.in_(eligible_rule_ids))
    rules = (await session.scalars(statement.order_by(AutomationRule.created_at, AutomationRule.id)
        .with_for_update().execution_options(populate_existing=True))).all()
    identities = []
    for rule in rules:
        if any(trigger_matches(trigger, context) for trigger in normalize_triggers(rule.triggers)):
            identities.append(await reserve_occurrence(session, rule, context,
                origin_kind=origin_kind, origin_id=origin_id, actor=actor, source=source, trace_id=trace_id))
    return identities



async def reserve_occurrence(session: AsyncSession, rule: AutomationRule, context: AutomationContext, *,
                              origin_kind: str, origin_id: str, actor: str, source: str, trace_id: str | None = None) -> uuid.UUID:
    identity = uuid.uuid4()
    planned = []
    for action in normalize_actions(rule.actions):
        item = {"action": action}
        denial = hardware_action_denial(action["type"], context.provenance)
        if not denial and action["type"] in HARDWARE_ACTION_TYPES:
            try:
                service = AccessDeviceConfiguration()
                if action["type"] == "gate.open":
                    item["automatic_entry_policy"] = (context.trigger_key.startswith("vehicle.")
                        or context.trigger_key in {"visitor_pass.used", "visitor_pass.detected"})
                    item["target_plan"] = await service.preview_gate_open(require_admission=True,
                        automatic_entry_policy=item["automatic_entry_policy"], session=session)
                else:
                    targets = await automation_garage_targets(action, session=session)
                    item["target_plans"] = [await service.preview_device_command(
                        device.key, "open" if action["type"].endswith("open") else "close", session=session) for device in targets]
                    if not targets:
                        item["preparation_error"] = "garage_door_not_configured"
            except (ValueError, LookupError) as exc:
                item["preparation_error"] = str(exc)
        planned.append(item)
    now = await session.scalar(select(func.clock_timestamp()))
    public = automation_execution_context_snapshot(context, rule, captured_at=now)
    snapshot = {**public, "version": 1, "rule_fingerprint": automation_rule_fingerprint(rule),
                "dispatch": {"trigger_key": context.trigger_key, "subject": context.subject,
                    "facts": context.facts, "entities": context.entities, "variables": context.variables,
                    "scopes": sorted(context.scopes), "provenance": asdict(context.provenance),
                    "source_time": context.trigger_payload.get("scheduled_for") or context.trigger_payload.get("occurred_at") or now.isoformat()}}
    return await AutomationRunStore().reserve(session, rule_id=rule.id, trigger_key=context.trigger_key,
        occurrence=occurrence_key(origin_kind, origin_id, rule.id, context.trigger_key), context=snapshot,
        planned=planned, trigger_payload=sanitize_payload(context.trigger_payload), actor=actor, source=source,
        trace_id=trace_id, run_id=identity)



async def automation_garage_targets(action: dict[str, Any], *, session: AsyncSession) -> list[Any]:
    action_config = as_dict(action.get("config"))
    target_ids = set(normalize_string_list(action_config.get("target_entity_ids")))
    return [
        device
        for device in await AccessDeviceConfiguration().list_devices(kind="garage_door", enabled_only=True, session=session)
        if not target_ids or device.key in target_ids
    ]



def public_automation_context(context: AutomationContext) -> dict[str, Any]:
    return {
        "trigger": {
            "key": context.trigger_key,
            "subject": context.subject,
        },
        "trigger_key": context.trigger_key,
        "subject": context.subject,
        "trigger_payload": sanitize_payload(context.trigger_payload),
        "facts": sanitize_payload(context.facts),
        "entities": context.entities,
        "scopes": sorted(context.scopes),
        "variables": context.variables,
        "missing_required_variables": sorted(set(context.missing_required_variables)),
        "warnings": context.warnings,
        "provenance": asdict(context.provenance),
    }



def automation_execution_context_snapshot(
    context: AutomationContext,
    rule: AutomationRule,
    *,
    captured_at: datetime,
) -> dict[str, Any]:
    """Persist the evaluated rule shape so later investigations do not use today's config."""

    return sanitize_payload(
        {
            **public_automation_context(context),
            "configuration_snapshot": {
                "source": "captured_at_execution",
                "captured_at": captured_at.isoformat(),
                "rule": {
                    "id": str(rule.id),
                    "name": rule.name,
                    "triggers": normalize_triggers(rule.triggers),
                    "conditions": normalize_conditions(rule.conditions),
                    "actions": normalize_actions(rule.actions),
                },
            },
        }
    )
