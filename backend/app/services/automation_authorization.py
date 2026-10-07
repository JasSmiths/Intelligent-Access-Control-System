"""Current automation authority shared by command and notification checkpoints.

This owner imports no executor, provider, notification service or chat facade.
Rule writers and dispatchers serialize on the rule row; retained origin facts
never grant authority after the rule or current domain authorization changes.
"""

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutomationRule, MaintenanceModeState, Presence, Vehicle
from app.models.enums import PresenceState


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
    authorize_recognition,
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
