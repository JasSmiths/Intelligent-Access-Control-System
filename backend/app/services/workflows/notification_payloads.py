"""Notification rule payload and action schema, independent of dispatch and persistence.

CRUD, Alfred previews and notification execution share these normalization rules.
This module imports no provider, runtime configuration, chat or ORM owner.
"""

import hashlib
import json
import uuid
from typing import Any

from app.modules.notifications.base import NotificationContext
from app.services.workflows.catalog import notification_trigger_catalog
from app.services.workflows.context import normalize_string_list

GATE_OPEN_ACTION = "gate.open"

GATE_MALFUNCTION_STAGE_LABELS = {
    "initial": "Initial malfunction",
    "30m": "30 minutes stuck",
    "60m": "60 minutes stuck",
    "2hrs": "2 hours stuck",
    "fubar": "FUBAR",
    "resolved": "Resolved",
}


GATE_MALFUNCTION_STAGE_ORDER = {
    stage: index
    for index, stage in enumerate(
        ["initial", "30m", "60m", "2hrs", "fubar", "resolved"]
    )
}


GATE_MALFUNCTION_STAGES = [
    {
        "value": stage,
        "label": label,
    }
    for stage, label in GATE_MALFUNCTION_STAGE_LABELS.items()
]


def normalize_trigger_event(value: Any) -> str:
    return str(value or "").strip()


def normalize_gate_malfunction_stage(value: Any) -> str:
    stage = str(value or "").strip().lower()
    return stage if stage in GATE_MALFUNCTION_STAGE_ORDER else "initial"


def normalize_gate_malfunction_stages(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    stages: list[str] = []
    for item in value:
        stage = str(item or "").strip().lower()
        if stage in GATE_MALFUNCTION_STAGE_ORDER and stage not in stages:
            stages.append(stage)
    return stages


def normalize_rule_payload(value: dict[str, Any]) -> dict[str, Any]:
    actions = normalize_actions(value.get("actions"))
    return {
        "id": str(value.get("id") or uuid.uuid4()),
        "name": str(value.get("name") or "Notification Workflow").strip()[:160],
        "trigger_event": normalize_trigger_event(value.get("trigger_event")),
        "conditions": normalize_conditions(value.get("conditions")),
        "actions": actions,
        "is_active": value.get("is_active", True) is not False,
    }


def notification_rule_definition_fingerprint(value: dict[str, Any]) -> str:
    """Fingerprint the canonical, behavior-bearing notification rule fields."""
    normalized = normalize_rule_payload(value)
    return hashlib.sha256(
        json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def normalize_conditions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    conditions: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        condition_type = str(raw.get("type") or "")
        if condition_type == "schedule":
            conditions.append(
                {
                    "id": str(raw.get("id") or f"condition-{index + 1}"),
                    "type": "schedule",
                    "schedule_id": str(raw.get("schedule_id") or ""),
                }
            )
        elif condition_type == "presence":
            conditions.append(
                {
                    "id": str(raw.get("id") or f"condition-{index + 1}"),
                    "type": "presence",
                    "mode": str(raw.get("mode") or "someone_home"),
                    "person_id": str(raw.get("person_id") or ""),
                }
            )
    return conditions


def normalize_actions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    actions: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        action_type = str(raw.get("type") or "")
        if action_type not in {"mobile", "voice", "in_app", "discord", "whatsapp"}:
            continue
        actions.append(
            {
                "id": str(raw.get("id") or f"action-{index + 1}"),
                "type": action_type,
                "target_mode": str(raw.get("target_mode") or "all"),
                "target_ids": normalize_string_list(raw.get("target_ids"), allow_scalar=False),
                "title_template": str(raw.get("title_template") or ""),
                "message_template": str(raw.get("message_template") or ""),
                "gate_malfunction_stages": normalize_gate_malfunction_stages(
                    raw.get("gate_malfunction_stages")
                ),
                "media": normalize_media(raw.get("media")),
                "actionable": normalize_actionable(raw.get("actionable")),
            }
        )
    return actions


def normalize_media(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "attach_camera_snapshot": bool(raw.get("attach_camera_snapshot")),
        "camera_id": str(raw.get("camera_id") or ""),
    }


def normalize_actionable(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    action = str(raw.get("action") or "")
    return {
        "enabled": bool(raw.get("enabled")) and action == GATE_OPEN_ACTION,
        "action": action if action == GATE_OPEN_ACTION else "",
    }



def notification_context_payload(
    context: NotificationContext,
    *,
    notification_run_id: str | None = None,
) -> dict[str, Any]:
    facts = dict(context.facts)
    if notification_run_id:
        facts["notification_run_id"] = notification_run_id
    return {
        "event_type": context.event_type,
        "subject": context.subject,
        "severity": context.severity,
        "facts": facts,
        "notification_run_id": notification_run_id or facts.get("notification_run_id"),
    }



def trigger_severity(trigger_event: str) -> str:
    for group in notification_trigger_catalog():
        for event in group["events"]:
            if event["value"] == trigger_event:
                return str(event["severity"])
    return "info"


def _duration_label_from_seconds(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        total_seconds = max(0, int(value))
    except (TypeError, ValueError):
        return str(value)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"

