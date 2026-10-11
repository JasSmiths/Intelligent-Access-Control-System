"""Current automation authority shared by command and notification checkpoints.

This owner imports no executor, provider, notification service or chat facade.
Rule writers and dispatchers serialize on the rule row; retained origin facts
never grant authority after the rule or current domain authorization changes.
"""

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutomationRule, AutomationRun, MaintenanceModeState, Presence, Vehicle
from app.models.enums import PresenceState
from app.services.automation_policy import (
    HARDWARE_DENIAL_DETAILS,
    REQUESTER_CONFIRMATION_REQUIRED,
    action_paused_by_maintenance_mode,
    hardware_action_denial,
)
from app.services.workflows.automation_definition import (
    AutomationContext,
    context_missing_references,
    normalize_conditions,
    restored_automation_context,
)
from app.services.workflows.context import workflow_action_result


def automation_rule_fingerprint(rule: AutomationRule) -> str:
    # CRUD owns normalization. Fingerprint the actual stored behavior, without
    # interpreting a changed configuration as equivalent to captured inputs.
    value = {
        "name": rule.name,
        "triggers": rule.triggers,
        "conditions": rule.conditions,
        "actions": rule.actions,
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def current_rule_denial(rule: AutomationRule | None, fingerprint: str | None) -> str | None:
    if rule is None or not rule.is_active:
        return "rule_inactive"
    if not fingerprint or automation_rule_fingerprint(rule) != fingerprint:
        return "rule_changed"
    return None


async def evaluate_current_condition(
    session: AsyncSession, condition: dict[str, Any], entities: dict[str, Any]
) -> dict[str, Any]:
    kind, config = str(condition.get("type") or ""), condition.get("config") or {}
    details: dict[str, Any]
    if kind in {"person.on_site", "person.off_site"}:
        identity = str(config.get("person_id") or entities.get("person_id") or "")
        present = await person_is_present(session, identity)
        passed, details = (
            present is (kind == "person.on_site"),
            {"person_id": identity, "present": present},
        )
    elif kind in {"vehicle.on_site", "vehicle.off_site"}:
        identity = str(config.get("vehicle_id") or entities.get("vehicle_id") or "")
        parsed = _uuid(identity)
        vehicle = await session.get(Vehicle, parsed, populate_existing=True) if parsed else None
        present = bool(
            vehicle
            and vehicle.person_id
            and await person_is_present(session, str(vehicle.person_id))
        )
        passed, details = (
            present is (kind == "vehicle.on_site"),
            {"vehicle_id": identity, "present": present},
        )
    elif kind in {"maintenance_mode.enabled", "maintenance_mode.disabled"}:
        row = await session.get(MaintenanceModeState, 1, populate_existing=True)
        active = bool(row and row.is_active)
        passed, details = (
            active is (kind == "maintenance_mode.enabled"),
            {"maintenance_mode_active": active},
        )
    else:
        return {
            "id": condition.get("id"),
            "type": kind,
            "passed": False,
            "reason": "unknown_condition",
        }
    return {"id": condition.get("id"), "type": kind, "passed": passed, "details": details}


async def person_is_present(session: AsyncSession, identity: str) -> bool:
    parsed = _uuid(identity)
    row = await session.get(Presence, parsed, populate_existing=True) if parsed else None
    return bool(row and row.state == PresenceState.PRESENT)


async def notification_origin_denial(
    session: AsyncSession,
    payload: dict[str, Any],
    notification_id: uuid.UUID,
    *,
    authorize_recognition: Callable[..., Awaitable[object]],
) -> str | None:
    """Reject unsupported notification handoffs; ordinary domain notices remain independent.

    No retained automation action creates notification delivery handoffs. Stored
    or unrecognized origins cannot gain authority after their producer retires.
    Shared automation rule and recognition checks remain with command execution.
    """
    return "unsupported_automation_notification_origin" if "automation_origin" in payload else None


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


@dataclass(frozen=True)
class AutomationPreflightAuthority:
    denial: dict[str, Any] | None
    condition_results: list[dict[str, Any]] | None = None


async def evaluate_automation_condition(
    session: AsyncSession, condition: dict[str, Any], context: AutomationContext,
) -> dict[str, Any]:
    missing = context_missing_references(context, condition)
    if missing:
        return {"id": condition["id"], "type": condition["type"], "passed": False,
                "reason": "context_missing", "missing_variables": missing}
    return await evaluate_current_condition(session, condition, context.entities)


async def preflight_current_action(
    session: AsyncSession, run: AutomationRun, item: dict[str, Any],
    *, rule: AutomationRule | None,
    maintenance_active: Callable[[], Awaitable[bool]],
    evaluate_condition: Callable[[AsyncSession, dict[str, Any], AutomationContext], Awaitable[dict[str, Any]]],
) -> AutomationPreflightAuthority:
    """One current rule/context/condition policy for all attempted action owners.

    Callers choose their row-lock timing; this function neither acquires a second
    run claim nor mutates its action/condition receipt. Hardware-specific deadline
    and recognition policy remains with its existing checkpoint adapter.
    """
    action = item["action"]
    def skip(reason: str, *, review: bool = False, **extra: Any) -> AutomationPreflightAuthority:
        return AutomationPreflightAuthority(workflow_action_result(action, "skipped",
            reason=reason, reason_code=reason, command_sent=False,
            requires_review=review, **extra))

    rule_denial = current_rule_denial(rule, run.context.get("rule_fingerprint"))
    if rule_denial:
        return skip("rule_not_active" if rule_denial == "rule_inactive" else "rule_changed_since_occurrence",
                    review=rule_denial == "rule_changed")
    if rule is None:
        return skip("rule_not_active")
    context = restored_automation_context(run.context)
    denial = hardware_action_denial(action["type"], context.provenance)
    if denial:
        return skip(denial, detail=HARDWARE_DENIAL_DETAILS[denial],
                    requires_confirmation=denial == REQUESTER_CONFIRMATION_REQUIRED)
    missing = context_missing_references(context, action)
    if missing:
        return skip("context_missing", missing_variables=missing)
    if (await maintenance_active() and action["type"] != "maintenance_mode.disable"
        and action_paused_by_maintenance_mode(action["type"])):
        return skip("maintenance_mode")
    conditions = [await evaluate_condition(session, condition, context)
                  for condition in normalize_conditions(rule.conditions)]
    if any(not result.get("passed") for result in conditions):
        outcome = skip("condition_failed")
        return AutomationPreflightAuthority(outcome.denial, conditions)
    return AutomationPreflightAuthority(None, conditions)
