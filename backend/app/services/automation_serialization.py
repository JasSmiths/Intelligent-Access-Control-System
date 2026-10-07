"""Public automation responses from durable rules and runs."""

from typing import Any

from app.models import AutomationRule, AutomationRun
from app.services.type_helpers import as_dict
from app.services.workflows.automation_definition import (
    normalize_actions,
    normalize_conditions,
    normalize_rule_payload,
    normalize_triggers,
    trigger_keys_for_triggers,
)


def serialize_rule(rule: AutomationRule | dict[str, Any]) -> dict[str, Any]:
    if isinstance(rule, dict):
        return normalize_rule_payload(rule)
    return {
        "id": str(rule.id),
        "name": rule.name,
        "description": rule.description or "",
        "is_active": rule.is_active,
        "triggers": normalize_triggers(rule.triggers),
        "trigger_keys": trigger_keys_for_triggers(normalize_triggers(rule.triggers)),
        "conditions": normalize_conditions(rule.conditions),
        "actions": normalize_actions(rule.actions),
        "next_run_at": rule.next_run_at.isoformat() if rule.next_run_at else None,
        "last_fired_at": rule.last_fired_at.isoformat() if rule.last_fired_at else None,
        "run_count": rule.run_count,
        "last_run_status": rule.last_run_status,
        "last_error": rule.last_error,
        "created_by_user_id": str(rule.created_by_user_id) if rule.created_by_user_id else None,
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
        "updated_at": rule.updated_at.isoformat() if rule.updated_at else None,
    }


def serialize_run(run: AutomationRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "rule_id": str(run.rule_id) if run.rule_id else None,
        "trigger_key": run.trigger_key,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "trigger_payload": run.trigger_payload,
        "context": {
            key: value
            for key, value in (run.context or {}).items()
            if key not in {"dispatch", "rule_fingerprint"}
        },
        "recovery_version": run.recovery_version,
        "review_reason": (
            "historical_unfinished"
            if run.recovery_version is None
            and run.status in {"claimed", "running", "queued", "processing"}
            else run.review_reason
        ),
        "requires_review": bool(run.review_reason)
        or (
            run.recovery_version is None
            and run.status in {"claimed", "running", "queued", "processing"}
        ),
        "action_states": [
            {
                "index": item.get("index"),
                "id": as_dict(item.get("action")).get("id"),
                "operation_id": item.get("operation_id"),
                "state": item.get("state"),
            }
            for item in (run.action_plan or [])
            if isinstance(item, dict)
        ],
        "condition_results": run.condition_results,
        "action_results": run.action_results,
        "trace_id": run.trace_id,
        "error": run.error,
        "actor": run.actor,
        "source": run.source,
    }
