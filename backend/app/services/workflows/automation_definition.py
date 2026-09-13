"""Automation definitions and captured context; no database, provider or executor imports.

Normalization is shared by editing, trigger intake, previews and execution.
Captured facts are data, never authority to execute a hardware action.
"""
from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.services.automation_policy import TriggerProvenance
from app.services.type_helpers import as_dict
from app.services.workflows.catalog import (
    automation_action_catalog, automation_condition_catalog, automation_trigger_catalog, automation_variables,
)
from app.services.workflows.context import canonical_key, normalize_string_list, referenced_variable_names

WEBHOOK_KEY_PREFIX = "whk_"
WEBHOOK_KEY_RANDOM_BYTES = 32
WEBHOOK_HMAC_WINDOW_SECONDS = 300
WEBHOOK_RATE_LIMIT_PER_MINUTE = 60
TRIGGER_CATALOG = automation_trigger_catalog()
CONDITION_CATALOG = automation_condition_catalog()
ACTION_CATALOG = automation_action_catalog()
VARIABLES = automation_variables()
VARIABLE_BY_NAME = {variable.name.lower(): variable for variable in VARIABLES}
TRIGGER_SCOPES = {trigger["type"]: set(trigger.get("scopes") or []) for group in TRIGGER_CATALOG for trigger in group["triggers"]}
TIME_TRIGGER_KEYS = {"time.specific_datetime", "time.every_x", "time.cron", "time.ai_text"}
INTEGRATION_ACTION_KEYS = {
    "integration.icloud_calendar.sync": ("icloud_calendar", "sync_calendars"),
    "integration.whatsapp.send_message": ("whatsapp", "send_message"),
}

@dataclass
class AutomationContext:
    trigger_key: str
    subject: str
    trigger_payload: dict[str, Any]
    facts: dict[str, Any] = field(default_factory=dict)
    entities: dict[str, str] = field(default_factory=dict)
    scopes: set[str] = field(default_factory=set)
    variables: dict[str, str] = field(default_factory=dict)
    missing_required_variables: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    provenance: TriggerProvenance = field(init=False)

    def __post_init__(self) -> None:
        self.provenance = TriggerProvenance.from_trigger(self.trigger_key, self.trigger_payload)


def normalize_rule_payload(value: dict[str, Any]) -> dict[str, Any]:
    triggers = normalize_triggers(value.get("triggers"))
    return {
        "id": str(value.get("id") or uuid.uuid4()),
        "name": str(value.get("name") or "Automation Rule").strip()[:160],
        "description": str(value.get("description") or "").strip(),
        "is_active": value.get("is_active", True) is not False,
        "triggers": triggers,
        "trigger_keys": trigger_keys_for_triggers(triggers),
        "conditions": normalize_conditions(value.get("conditions")),
        "actions": normalize_actions(value.get("actions")),
        "next_run_at": value.get("next_run_at"),
        "last_fired_at": value.get("last_fired_at"),
        "last_run_status": value.get("last_run_status"),
        "last_error": value.get("last_error"),
        "run_count": int(value.get("run_count") or 0),
    }


def normalize_triggers(value: Any, *, generate_webhook_keys: bool = False) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    allowed = {trigger_type for group in TRIGGER_CATALOG for trigger_type in [item["type"] for item in group["triggers"]]}
    normalized = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        trigger_type = str(raw.get("type") or "").strip()
        if trigger_type not in allowed:
            continue
        config = as_dict(raw.get("config"))
        normalized.append(
            {
                "id": str(raw.get("id") or f"trigger-{index + 1}"),
                "type": trigger_type,
                "config": normalize_trigger_config(
                    trigger_type,
                    config,
                    generate_webhook_key=generate_webhook_keys,
                ),
            }
        )
    return normalized


def normalize_trigger_config(
    trigger_type: str,
    config: dict[str, Any],
    *,
    generate_webhook_key: bool = False,
) -> dict[str, Any]:
    if trigger_type == "time.every_x":
        unit = str(config.get("unit") or "minutes").lower()
        if unit not in {"minutes", "hours", "days"}:
            unit = "minutes"
        return {
            "interval": safe_int(config.get("interval"), default=1, minimum=1),
            "unit": unit,
            "start_at": optional_text(config.get("start_at")),
            "end_at": optional_text(config.get("end_at")),
        }
    if trigger_type in {"time.cron", "time.ai_text"}:
        return {
            "cron_expression": optional_text(config.get("cron_expression")),
            "timezone": optional_text(config.get("timezone")) or "Europe/London",
            "start_at": optional_text(config.get("start_at")),
            "end_at": optional_text(config.get("end_at")),
            "natural_text": optional_text(config.get("natural_text")),
            "summary": optional_text(config.get("summary")),
        }
    if trigger_type == "time.specific_datetime":
        recurrence = str(config.get("recurrence") or "none").lower()
        if recurrence not in {"none", "daily", "weekly", "monthly"}:
            recurrence = "none"
        return {
            "run_at": optional_text(config.get("run_at")),
            "single_use": config.get("single_use", recurrence == "none") is not False,
            "recurrence": recurrence,
            "end_at": optional_text(config.get("end_at")),
        }
    if trigger_type == "webhook.received":
        webhook_key = optional_text(config.get("webhook_key"))
        key_was_generated = False
        if generate_webhook_key and not is_high_entropy_webhook_key(webhook_key):
            webhook_key = generate_automation_webhook_key()
            key_was_generated = True
        return {
            "webhook_key": webhook_key,
            "webhook_key_strength": "server_generated"
            if key_was_generated or is_high_entropy_webhook_key(webhook_key)
            else "legacy",
            "require_hmac": bool_config(config.get("require_hmac")),
            "allowed_source_ips": normalize_string_list(config.get("allowed_source_ips")),
            "rate_limit_per_minute": safe_int(
                config.get("rate_limit_per_minute"),
                default=WEBHOOK_RATE_LIMIT_PER_MINUTE,
                minimum=1,
            ),
            "replay_window_seconds": safe_int(
                config.get("replay_window_seconds"),
                default=WEBHOOK_HMAC_WINDOW_SECONDS,
                minimum=30,
            ),
            "source_ip": optional_text(config.get("source_ip")),
        }
    return {
        key: item
        for key, item in config.items()
        if key
        in {
            "person_id",
            "vehicle_id",
            "registration_number",
            "visitor_pass_id",
            "phrase",
            "match_mode",
            "webhook_key",
            "source_ip",
        }
    }


def normalize_conditions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    allowed = {condition["type"] for group in CONDITION_CATALOG for condition in group["conditions"]}
    conditions = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        condition_type = str(raw.get("type") or "").strip()
        if condition_type not in allowed:
            continue
        config = as_dict(raw.get("config"))
        conditions.append(
            {
                "id": str(raw.get("id") or f"condition-{index + 1}"),
                "type": condition_type,
                "config": {
                    key: item
                    for key, item in config.items()
                    if key in {"person_id", "vehicle_id"}
                },
            }
        )
    return conditions


def normalize_actions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    allowed = {
        action["type"]
        for group in ACTION_CATALOG
        for action in group["actions"]
    } | set(INTEGRATION_ACTION_KEYS)
    actions = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        action_type = str(raw.get("type") or "").strip()
        if action_type not in allowed:
            continue
        config = as_dict(raw.get("config"))
        actions.append(
            {
                "id": str(raw.get("id") or f"action-{index + 1}"),
                "type": action_type,
                "config": normalize_action_config(action_type, config),
                "reason_template": str(raw.get("reason_template") or ""),
            }
        )
    return actions


def normalize_action_config(action_type: str, config: dict[str, Any]) -> dict[str, Any]:
    if action_type.startswith("notification."):
        return {
            "notification_rule_id": optional_text(config.get("notification_rule_id")),
            "notification_rule_name": optional_text(config.get("notification_rule_name")),
        }
    if action_type.startswith("garage_door."):
        return {
            "target_entity_ids": normalize_string_list(config.get("target_entity_ids"))
        }
    if action_type in INTEGRATION_ACTION_KEYS:
        return integration_action_config(action_type, config)
    return {}


def generate_automation_webhook_key() -> str:
    return f"{WEBHOOK_KEY_PREFIX}{secrets.token_urlsafe(WEBHOOK_KEY_RANDOM_BYTES)}"


def is_high_entropy_webhook_key(value: Any) -> bool:
    text = optional_text(value)
    return text.startswith(WEBHOOK_KEY_PREFIX) and len(text) >= len(WEBHOOK_KEY_PREFIX) + 40


def trigger_keys_for_triggers(triggers: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(str(trigger["type"]) for trigger in triggers if trigger.get("type")))


def facts_from_payload(trigger_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    facts = as_dict(payload.get("facts"))
    visitor_pass = as_dict(payload.get("visitor_pass"))
    body = as_dict(payload.get("payload"))
    merged = {**payload, **facts}
    if visitor_pass:
        merged.update(
            {
                "visitor_pass_id": visitor_pass.get("id"),
                "visitor_name": visitor_pass.get("visitor_name"),
                "visitor_pass_status": visitor_pass.get("status"),
                "visitor_pass_expected_time": visitor_pass.get("expected_time"),
                "visitor_pass_vehicle_registration": visitor_pass.get("number_plate"),
                "visitor_pass_vehicle_make": visitor_pass.get("vehicle_make"),
                "visitor_pass_vehicle_colour": visitor_pass.get("vehicle_colour"),
                "visitor_pass_duration_on_site": visitor_pass.get("duration_human"),
                "visitor_pass_duration_on_site_seconds": visitor_pass.get("duration_on_site_seconds"),
                "registration_number": visitor_pass.get("number_plate"),
                "vehicle_make": visitor_pass.get("vehicle_make"),
                "vehicle_colour": visitor_pass.get("vehicle_colour"),
            }
        )
    if trigger_key.startswith("webhook."):
        merged.update(
            {
                "webhook_key": payload.get("webhook_key"),
                "webhook_sender_ip": payload.get("source_ip"),
                "message": json.dumps(body)[:500] if body else payload.get("message"),
            }
        )
    if trigger_key.startswith("ai."):
        merged.update(
            {
                "alfred_phrase": payload.get("phrase") or payload.get("message"),
                "alfred_issue": payload.get("issue") or payload.get("message"),
            }
        )
    return merged


def entities_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    facts = as_dict(payload.get("facts"))
    visitor_pass = as_dict(payload.get("visitor_pass"))
    merged = {**payload, **facts}
    entities = {
        "person_id": optional_text(merged.get("person_id")),
        "vehicle_id": optional_text(merged.get("vehicle_id")),
        "visitor_pass_id": optional_text(visitor_pass.get("id") or merged.get("visitor_pass_id")),
        "access_event_id": optional_text(merged.get("access_event_id") or merged.get("event_id")),
    }
    return {key: value for key, value in entities.items() if value}


def build_context_variables(context: AutomationContext) -> dict[str, str]:
    facts = {canonical_key(key): "" if value is None else str(value) for key, value in context.facts.items()}

    def pick(*keys: str, default: str = "") -> str:
        for key in keys:
            value = facts.get(canonical_key(key))
            if value:
                return value
        return default

    occurred_at = pick("occurred_at", "created_at", default=datetime.now(tz=UTC).isoformat())
    variables = {
        "FirstName": pick("first_name"),
        "LastName": pick("last_name"),
        "DisplayName": pick("display_name", "person_name"),
        "PersonId": pick("person_id", default=context.entities.get("person_id", "")),
        "Registration": pick("registration_number", "vehicle_registration_number", "visitor_pass_vehicle_registration"),
        "VehicleRegistrationNumber": pick("vehicle_registration_number", "registration_number", "visitor_pass_vehicle_registration"),
        "VehicleId": pick("vehicle_id", default=context.entities.get("vehicle_id", "")),
        "VehicleName": pick("vehicle_name", "vehicle_display_name", "vehicle_description", "registration_number"),
        "VehicleMake": pick("vehicle_make", "make", "visitor_pass_vehicle_make"),
        "VehicleColour": pick("vehicle_colour", "vehicle_color", "colour", "color", "visitor_pass_vehicle_colour"),
        "VehicleColor": pick("vehicle_color", "vehicle_colour", "color", "colour", "visitor_pass_vehicle_colour"),
        "VisitorPassId": pick("visitor_pass_id", default=context.entities.get("visitor_pass_id", "")),
        "VisitorName": pick("visitor_name"),
        "VisitorPassVehicleRegistration": pick("visitor_pass_vehicle_registration", "number_plate", "registration_number"),
        "VisitorPassVehicleMake": pick("visitor_pass_vehicle_make", "vehicle_make"),
        "VisitorPassVehicleColour": pick("visitor_pass_vehicle_colour", "vehicle_colour", "vehicle_color"),
        "VisitorPassDurationOnSite": pick("visitor_pass_duration_on_site", "duration_human"),
        "MaintenanceModeReason": pick("maintenance_mode_reason", "reason"),
        "MaintenanceModeDuration": pick("maintenance_mode_duration", "duration_label"),
        "WebhookKey": pick("webhook_key"),
        "WebhookSenderIp": pick("webhook_sender_ip", "source_ip"),
        "AlfredPhrase": pick("alfred_phrase", "phrase", "message"),
        "AlfredIssue": pick("alfred_issue", "issue", "message"),
        "OccurredAt": occurred_at,
        "Date": date_label(occurred_at),
        "Time": time_label(occurred_at),
        "EventType": context.trigger_key.replace(".", " ").replace("_", " ").title(),
        "Subject": context.subject,
        "Message": pick("message", default=context.subject),
        "Source": pick("source"),
    }
    return {key: "" if value is None else str(value) for key, value in variables.items()}


def context_missing_references(context: AutomationContext, value: Any) -> list[str]:
    missing: list[str] = []
    for name in sorted(referenced_variable_names(value)):
        variable = VARIABLE_BY_NAME.get(name.lower())
        if not variable:
            context.warnings.append(f"Unknown variable @{name}.")
            missing.append(name)
            continue
        if variable.scope not in context.scopes:
            context.warnings.append(f"Variable @{name} is not available for {context.trigger_key}.")
            missing.append(variable.name)
            continue
        if not context.variables.get(variable.name):
            missing.append(variable.name)
    if missing:
        context.missing_required_variables = sorted(set([*context.missing_required_variables, *missing]))
    return sorted(set(missing))


def trigger_matches(trigger: dict[str, Any], context: AutomationContext) -> bool:
    if trigger["type"] != context.trigger_key:
        return False
    config = as_dict(trigger.get("config"))
    facts = {canonical_key(key): str(value).lower() for key, value in context.facts.items() if value is not None}
    if config.get("person_id") and str(config["person_id"]) != context.entities.get("person_id"):
        return False
    if config.get("vehicle_id") and str(config["vehicle_id"]) != context.entities.get("vehicle_id"):
        return False
    if config.get("visitor_pass_id") and str(config["visitor_pass_id"]) != context.entities.get("visitor_pass_id"):
        return False
    if config.get("registration_number"):
        expected = str(config["registration_number"]).strip().replace(" ", "").lower()
        actual = facts.get(canonical_key("registration_number"), "").replace(" ", "")
        if expected and expected != actual:
            return False
    if config.get("webhook_key") and str(config["webhook_key"]) != str(context.facts.get("webhook_key") or ""):
        return False
    if config.get("source_ip") and str(config["source_ip"]) != str(context.facts.get("source_ip") or ""):
        return False
    phrase = str(config.get("phrase") or "").strip().lower()
    if phrase:
        actual_phrase = str(context.facts.get("alfred_phrase") or context.facts.get("message") or "").lower()
        if str(config.get("match_mode") or "contains") == "exact":
            return actual_phrase == phrase
        return phrase in actual_phrase
    return True


def subject_for_trigger(trigger_key: str, payload: dict[str, Any]) -> str:
    facts = as_dict(payload.get("facts"))
    visitor_pass = as_dict(payload.get("visitor_pass"))
    return str(
        payload.get("subject")
        or facts.get("subject")
        or payload.get("message")
        or visitor_pass.get("visitor_name")
        or trigger_key.replace(".", " ").title()
    )


def date_label(value: str) -> str:
    parsed = parse_datetime(value)
    return parsed.strftime("%Y-%m-%d") if parsed else ""


def time_label(value: str) -> str:
    parsed = parse_datetime(value)
    return parsed.strftime("%H:%M") if parsed else ""


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return ensure_aware(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return ensure_aware(parsed)
    except ValueError:
        return None


def ensure_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def optional_text(value: Any) -> str:
    return str(value or "").strip()


def bool_config(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def safe_int(value: Any, *, default: int = 1, minimum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


def captured_automation_context(trigger_key: str, payload: dict[str, Any]) -> AutomationContext:
    context = AutomationContext(trigger_key=trigger_key, subject=subject_for_trigger(trigger_key, payload),
        trigger_payload=payload, facts=facts_from_payload(trigger_key, payload), entities=entities_from_payload(payload),
        scopes=set(TRIGGER_SCOPES.get(trigger_key, {"event"})))
    if trigger_key.startswith("time."):
        context.scopes.update({"time", "event"})
    context.variables = build_context_variables(context)
    return context


def restored_automation_context(snapshot: dict[str, Any]) -> AutomationContext:
    captured = snapshot["dispatch"]
    context = AutomationContext(trigger_key=captured["trigger_key"], subject=captured["subject"], trigger_payload={},
        facts=dict(captured["facts"]), entities=dict(captured["entities"]), variables=dict(captured["variables"]),
        scopes=set(captured["scopes"]))
    context.provenance = TriggerProvenance(**captured["provenance"])
    return context


def integration_action_config(action_type: str, config: dict[str, Any]) -> dict[str, Any]:
    action = INTEGRATION_ACTION_KEYS.get(action_type)
    if not action:
        return {}
    if action_type == "integration.whatsapp.send_message":
        target_mode = str(config.get("target_mode") or "selected")
        if target_mode not in {"all", "selected", "dynamic"}:
            target_mode = "selected"
        return {
            "provider": "whatsapp",
            "action": "send_message",
            "target_mode": target_mode,
            "target_user_ids": normalize_string_list(config.get("target_user_ids"), allow_scalar=False),
            "phone_number_template": str(config.get("phone_number_template") or ""),
            "message_template": str(config.get("message_template") or "@Subject"),
        }
    return {
        "provider": str(config.get("provider") or action[0]),
        "action": str(config.get("action") or action[1]),
    }



AUTOMATION_BRIDGE_IGNORED_EVENT_TYPES = {
    "notification.trigger",
    "notification.sent",
    "notification.failed",
    "notification.skipped",
}

AUTOMATION_BRIDGE_IGNORED_EVENT_PREFIXES = ("automation.run.",)


def automation_triggers_for_origin(event_type: str, payload: dict[str, Any], *, occurred_at: str) -> list[tuple[str, dict[str, Any]]]:
    if event_type in AUTOMATION_BRIDGE_IGNORED_EVENT_TYPES or event_type.startswith(
        AUTOMATION_BRIDGE_IGNORED_EVENT_PREFIXES
    ):
        return []
    if event_type == "maintenance_mode.changed":
        return [
            (
                "maintenance_mode.enabled" if payload.get("is_active") else "maintenance_mode.disabled",
                {**payload, "occurred_at": occurred_at},
            )
        ]
    if event_type == "access_event.finalized":
        if payload.get("backfilled") or payload.get("skip_automation_actions"):
            return []
        return access_event_vehicle_triggers(payload, occurred_at=occurred_at)
    if event_type == "visitor_pass.created":
        return [("visitor_pass.created", {**payload, "occurred_at": occurred_at})]
    if event_type == "visitor_pass.used":
        return [
            ("visitor_pass.used", {**payload, "occurred_at": occurred_at}),
            ("visitor_pass.detected", {**payload, "occurred_at": occurred_at}),
        ]
    if event_type == "visitor_pass.status_changed":
        visitor_pass = as_dict(payload.get("visitor_pass"))
        if str(visitor_pass.get("status") or "").lower() == "expired":
            return [("visitor_pass.expired", {**payload, "occurred_at": occurred_at})]
    if event_type == "ai.phrase_received":
        return [("ai.phrase_received", {**payload, "occurred_at": occurred_at})]
    if event_type == "ai.issue_detected":
        return [("ai.issue_detected", {**payload, "occurred_at": occurred_at})]
    return []


def access_event_vehicle_triggers(payload: dict[str, Any], *, occurred_at: str) -> list[tuple[str, dict[str, Any]]]:
    decision = str(payload.get("decision") or "").lower()
    vehicle_id = optional_text(payload.get("vehicle_id"))
    if decision == "granted" and vehicle_id:
        trigger_key = "vehicle.known_plate"
    elif decision == "denied" and vehicle_id:
        trigger_key = "vehicle.outside_schedule"
    elif decision == "denied" and not vehicle_id:
        trigger_key = "vehicle.unknown_plate"
    else:
        return []
    return [(trigger_key, {**payload, "occurred_at": payload.get("occurred_at") or occurred_at})]

