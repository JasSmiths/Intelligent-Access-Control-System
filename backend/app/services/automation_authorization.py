"""Current automation authority shared by command and notification checkpoints.

This owner imports no executor, provider, notification service or chat facade.
Rule writers and dispatchers serialize on the rule row; retained origin facts
never grant authority after the rule or current domain authorization changes.
"""

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutomationRule, AutomationRun, MaintenanceModeState, Presence, Vehicle
from app.models.enums import PresenceState


def automation_rule_fingerprint(rule: AutomationRule) -> str:
    # CRUD owns normalization. Fingerprint the actual stored behavior, without
    # interpreting a changed configuration as equivalent to captured inputs.
    value = {"name": rule.name, "triggers": rule.triggers, "conditions": rule.conditions, "actions": rule.actions}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def current_rule_denial(rule: AutomationRule | None, fingerprint: str | None) -> str | None:
    if rule is None or not rule.is_active:
        return "rule_inactive"
    if not fingerprint or automation_rule_fingerprint(rule) != fingerprint:
        return "rule_changed"
    return None


async def evaluate_current_condition(session: AsyncSession, condition: dict[str, Any], entities: dict[str, Any]) -> dict[str, Any]:
    kind, config = str(condition.get("type") or ""), condition.get("config") or {}
    details: dict[str, Any]
    if kind in {"person.on_site", "person.off_site"}:
        identity = str(config.get("person_id") or entities.get("person_id") or "")
        present = await person_is_present(session, identity)
        passed, details = present is (kind == "person.on_site"), {"person_id": identity, "present": present}
    elif kind in {"vehicle.on_site", "vehicle.off_site"}:
        identity = str(config.get("vehicle_id") or entities.get("vehicle_id") or "")
        parsed = _uuid(identity)
        vehicle = await session.get(Vehicle, parsed, populate_existing=True) if parsed else None
        present = bool(vehicle and vehicle.person_id and await person_is_present(session, str(vehicle.person_id)))
        passed, details = present is (kind == "vehicle.on_site"), {"vehicle_id": identity, "present": present}
    elif kind in {"maintenance_mode.enabled", "maintenance_mode.disabled"}:
        row = await session.get(MaintenanceModeState, 1, populate_existing=True)
        active = bool(row and row.is_active)
        passed, details = active is (kind == "maintenance_mode.enabled"), {"maintenance_mode_active": active}
    else:
        return {"id": condition.get("id"), "type": kind, "passed": False, "reason": "unknown_condition"}
    return {"id": condition.get("id"), "type": kind, "passed": passed, "details": details}


async def person_is_present(session: AsyncSession, identity: str) -> bool:
    parsed = _uuid(identity)
    row = await session.get(Presence, parsed, populate_existing=True) if parsed else None
    return bool(row and row.state == PresenceState.PRESENT)


async def notification_origin_denial(session: AsyncSession, payload: dict[str, Any], notification_id: uuid.UUID, *, authorize_recognition) -> str | None:
    """Lock rule -> run before the notification row; the final send owns commit.

    Operational notices without an automation origin are independent. An
    automation origin is created only by its transactional handoff, never read
    from a trigger's arbitrary payload or requester-supplied role fields.
    """
    origin = payload.get("automation_origin")
    if origin is None:
        return None
    if not isinstance(origin, dict):
        return "automation_origin_invalid"
    rule_id, run_id, operation_id = (_uuid(origin.get(key)) for key in ("rule_id", "run_id", "operation_id"))
    if not all((rule_id, run_id, operation_id)) or operation_id != notification_id:
        return "automation_origin_invalid"
    rule = await session.scalar(select(AutomationRule).where(AutomationRule.id == rule_id)
                                .with_for_update().execution_options(populate_existing=True))
    run = await session.scalar(select(AutomationRun).where(AutomationRun.id == run_id)
                               .with_for_update().execution_options(populate_existing=True))
    denial = current_rule_denial(rule, origin.get("rule_fingerprint"))
    if denial:
        return denial
    if (run is None or run.rule_id != rule_id or run.recovery_version != 1
            or run.context.get("rule_fingerprint") != origin.get("rule_fingerprint")):
        return "automation_origin_invalid"
    action = next((item for item in run.action_plan or [] if item.get("operation_id") == str(operation_id)), None)
    if (not action or action.get("state") != "succeeded"
            or action.get("action", {}).get("type") != "integration.whatsapp.send_message"
            or action.get("result", {}).get("notification_run_id") != str(notification_id)):
        return "automation_handoff_not_accepted"
    captured = run.context.get("dispatch") or {}
    for index, raw in enumerate(rule.conditions or []):
        result = await evaluate_current_condition(session, {"id": f"condition-{index + 1}", **raw}, captured.get("entities") or {})
        if not result["passed"]:
            return "condition_failed"
    # Unknown-denied observations still produce their intended notifications.
    # Recognition authority is required for a formerly authorized identified
    # vehicle/visitor, never fabricated for schedules, phrases or other origins.
    if run.trigger_key in {"vehicle.known_plate", "vehicle.outside_schedule", "visitor_pass.used", "visitor_pass.detected"}:
        now = await session.scalar(select(func.clock_timestamp()))
        try:
            await authorize_recognition(session,
                event_id=(captured.get("provenance") or {}).get("event_id"),
                allow_vehicle_schedule_override=run.trigger_key == "vehicle.outside_schedule", now=now)
        except ValueError:
            return "recognition_authorization_changed"
    return None


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None
