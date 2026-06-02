"""Shared runtime helpers for Alfred tool-group handlers.

Concrete tool handlers live beside their group catalogs; this module keeps the
cross-domain utilities they still share during the V2 reduction pass.
"""
# ruff: noqa: F401

from __future__ import annotations

import asyncio
import csv
import io
import re
from contextvars import ContextVar, Token
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Awaitable, Callable
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import (
    AccessEvent,
    AutomationRule,
    AuditLog,
    Anomaly,
    GateStateObservation,
    NotificationRule,
    Person,
    Presence,
    Schedule,
    ScheduleOverride,
    TelemetrySpan,
    TelemetryTrace,
    User,
    Vehicle,
    VisitorPass,
)
from app.models.enums import AccessDecision, AccessDirection, PresenceState, TimingClassification, VisitorPassStatus, VisitorPassType
from app.ai.providers import ImageAnalysisUnsupportedError, analyze_image_with_provider
from app.modules.home_assistant.covers import cover_entity_state_payload
from app.modules.dvla.vehicle_enquiry import DvlaVehicleEnquiryError, display_vehicle_record, normalize_registration_number
from app.modules.unifi_protect.client import UnifiProtectError
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError
from app.services.chat_attachments import ChatAttachmentError, chat_attachment_store
from app.services.auth_secret_management import AuthSecretRotationError, auth_secret_security_status, rotate_auth_secret
from app.services.access_events import get_access_event_service
from app.services.snapshots import alert_snapshot_metadata, alert_snapshot_path
from app.services.automations import (
    AutomationError,
    get_automation_service,
    normalize_actions as normalize_automation_actions,
    normalize_conditions as normalize_automation_conditions,
    normalize_triggers as normalize_automation_triggers,
    serialize_rule as serialize_automation_rule,
)
from app.services.dvla import lookup_vehicle_registration, normalize_vehicle_enquiry_response
from app.services.dependency_updates import DependencyUpdateError, get_dependency_update_service
from app.services.discord_messaging import get_discord_messaging_service
from app.services.event_bus import event_bus
from app.services.gate_malfunctions import get_gate_malfunction_service
from app.services.home_assistant import get_home_assistant_service
from app.services.icloud_calendar import ICloudCalendarError, get_icloud_calendar_service
from app.services.leaderboard import get_leaderboard_service
from app.services.lpr_timing import get_lpr_timing_recorder
from app.services.maintenance import (
    get_status as get_maintenance_mode_status,
    is_maintenance_mode_active,
    set_mode as set_maintenance_mode,
)
from app.services.notifications import (
    get_notification_service,
    normalize_actions,
    normalize_conditions,
    normalize_rule_payload,
    notification_context_from_payload,
    sample_notification_context,
)
from app.services.schedules import (
    evaluate_schedule_id,
    evaluate_person_schedule,
    evaluate_vehicle_schedule,
    normalize_time_blocks,
    schedule_dependencies,
    schedule_allows_at,
)
from app.services.settings import UnknownDynamicSettingsError, get_runtime_config, list_settings, update_settings
from app.services.snapshots import get_snapshot_manager
from app.services.unifi_protect import get_unifi_protect_service
from app.services.telemetry import TELEMETRY_CATEGORY_ALFRED, TELEMETRY_CATEGORY_ACCESS, TELEMETRY_CATEGORY_WEBHOOKS_API, telemetry, write_audit_log
from app.services.type_helpers import as_dict, as_dict_list, as_list
from app.services.alfred.answer_contracts import artifact_payload
from app.services.visitor_passes import (
    DEFAULT_WINDOW_MINUTES,
    VisitorPassError,
    get_visitor_pass_service,
    serialize_visitor_pass,
)
from app.services.whatsapp_messaging import get_whatsapp_messaging_service

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
CHAT_TOOL_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("chat_tool_context", default={})
logger = get_logger(__name__)
DEFAULT_AGENT_TIMEZONE = "Europe/London"
SAFETY_READ_ONLY = "read_only"
SAFETY_CONFIRMATION_REQUIRED = "confirmation_required"
SAFETY_ADMIN_ONLY = "admin_only"
SAFETY_LEVELS = {SAFETY_READ_ONLY, SAFETY_CONFIRMATION_REQUIRED, SAFETY_ADMIN_ONLY}
ADMIN_PERMISSION = "admin"
SCHEDULE_TIME_BLOCKS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Monday-first schedule blocks keyed by day number 0-6, where 0 is Monday. Times must align to 30-minute increments.",
    "additionalProperties": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "HH:MM, for example 07:00."},
                "end": {"type": "string", "description": "HH:MM or 24:00, for example 19:00."},
            },
            "required": ["start", "end"],
            "additionalProperties": False,
        },
    },
}

SCHEDULE_LOOKUP_PROPERTIES: dict[str, Any] = {
    "schedule_id": {"type": "string", "description": "Schedule UUID."},
    "schedule_name": {"type": "string", "description": "Schedule name or partial name."},
}

NOTIFICATION_RULE_LOOKUP_PROPERTIES: dict[str, Any] = {
    "rule_id": {"type": "string", "description": "Notification workflow UUID."},
    "rule_name": {"type": "string", "description": "Notification workflow name or unique partial name."},
}

NOTIFICATION_CONDITION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Workflow condition. Schedule checks require schedule_id. Presence modes are no_one_home, someone_home, or person_home.",
    "properties": {
        "id": {"type": "string"},
        "type": {"type": "string", "enum": ["schedule", "presence"]},
        "schedule_id": {"type": "string"},
        "mode": {"type": "string", "enum": ["no_one_home", "someone_home", "person_home"]},
        "person_id": {"type": "string"},
    },
    "required": ["type"],
    "additionalProperties": False,
}

NOTIFICATION_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Workflow action. type is mobile for Apprise, voice for Home Assistant TTS, in_app for dashboard alerts, discord for Discord channel alerts, or whatsapp for WhatsApp Admin messages.",
    "properties": {
        "id": {"type": "string"},
        "type": {"type": "string", "enum": ["mobile", "voice", "in_app", "discord", "whatsapp"]},
        "target_mode": {"type": "string", "enum": ["all", "many", "selected"]},
        "target_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "For Discord actions, use endpoint ids in the form discord:<channel_id>. For WhatsApp actions, use whatsapp:admin:<user_id>, whatsapp:*, or whatsapp:number:@Variable.",
        },
        "title_template": {"type": "string", "description": "Title template supporting @ variables such as @FirstName."},
        "message_template": {"type": "string", "description": "Message template supporting @ variables such as @VehicleName."},
        "gate_malfunction_stages": {
            "type": "array",
            "items": {"type": "string", "enum": ["initial", "30m", "60m", "2hrs", "fubar", "resolved"]},
            "description": "For the gate_malfunction trigger, optional stages this action should deliver. Empty means all stages.",
        },
        "media": {
            "type": "object",
            "properties": {
                "attach_camera_snapshot": {"type": "boolean"},
                "camera_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    "required": ["type"],
    "additionalProperties": False,
}

NOTIFICATION_RULE_PAYLOAD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Unsaved notification workflow payload.",
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "trigger_event": {"type": "string"},
        "conditions": {"type": "array", "items": NOTIFICATION_CONDITION_SCHEMA},
        "actions": {"type": "array", "items": NOTIFICATION_ACTION_SCHEMA},
        "is_active": {"type": "boolean"},
    },
    "additionalProperties": False,
}

AUTOMATION_RULE_LOOKUP_PROPERTIES: dict[str, Any] = {
    "automation_id": {"type": "string", "description": "Automation rule UUID."},
    "automation_name": {"type": "string", "description": "Automation rule name or unique partial name."},
}

AUTOMATION_TRIGGER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Automation trigger object. type is one of the automation catalog trigger keys; config holds trigger-specific filters or schedule settings.",
    "properties": {
        "id": {"type": "string"},
        "type": {"type": "string"},
        "config": {"type": "object", "additionalProperties": True},
    },
    "required": ["type"],
    "additionalProperties": False,
}

AUTOMATION_CONDITION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Automation condition object. Supported types include person.on_site, person.off_site, vehicle.on_site, vehicle.off_site, maintenance_mode.enabled, and maintenance_mode.disabled.",
    "properties": {
        "id": {"type": "string"},
        "type": {"type": "string"},
        "config": {"type": "object", "additionalProperties": True},
    },
    "required": ["type"],
    "additionalProperties": False,
}

AUTOMATION_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Automation action object. Hardware, maintenance, notification, and integration actions run later "
        "when the saved rule fires. Integration actions use catalog types such as "
        "integration.icloud_calendar.sync with config {provider:'icloud_calendar', action:'sync_calendars'}."
    ),
    "properties": {
        "id": {"type": "string"},
        "type": {"type": "string"},
        "config": {"type": "object", "additionalProperties": True},
        "reason_template": {"type": "string", "description": "Optional audit reason template supporting @ variables."},
    },
    "required": ["type"],
    "additionalProperties": False,
}

AUTOMATION_RULE_PAYLOAD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Unsaved automation rule. Example: open the gate if Steph arrives outside schedule -> "
        "triggers=[{type:'vehicle.outside_schedule',config:{person_id:'...'}}], "
        "conditions=[], actions=[{type:'gate.open',reason_template:'@DisplayName arrived outside schedule'}]."
    ),
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "triggers": {"type": "array", "items": AUTOMATION_TRIGGER_SCHEMA},
        "conditions": {"type": "array", "items": AUTOMATION_CONDITION_SCHEMA},
        "actions": {"type": "array", "items": AUTOMATION_ACTION_SCHEMA},
        "is_active": {"type": "boolean"},
    },
    "additionalProperties": False,
}


def set_chat_tool_context(
    value: dict[str, Any],
    *,
    token: Token[dict[str, Any]] | None = None,
) -> Token[dict[str, Any]] | None:
    if token is not None:
        CHAT_TOOL_CONTEXT.reset(token)
        return None
    return CHAT_TOOL_CONTEXT.set(value)


def get_chat_tool_context() -> dict[str, Any]:
    return CHAT_TOOL_CONTEXT.get({})


async def _chat_context_user() -> User | None:
    context = get_chat_tool_context()
    user_id = _uuid_from_value(context.get("user_id"))
    if not user_id:
        return None
    async with AsyncSessionLocal() as session:
        return await session.get(User, user_id)


async def _require_admin_user(action: str) -> User | dict[str, Any]:
    context = get_chat_tool_context()
    if str(context.get("user_role") or "").lower() != "admin":
        return {"changed": False, "error": f"Admin access is required for {action}."}
    user = await _chat_context_user()
    if not user:
        return {"changed": False, "error": f"Authenticated Admin context is required for {action}."}
    return user


def _schedule_answer_artifacts(payload: dict[str, Any], *, subject: str) -> list[dict[str, Any]]:
    allowed = bool(payload.get("allowed"))
    reason = str(payload.get("reason") or "").strip()
    checked_at = _compact_time_label(payload.get("checked_at_display"))
    display = reason or (f"{subject} is allowed at {checked_at}." if allowed else f"{subject} is not allowed at {checked_at}.")
    return [
        artifact_payload(
            domain="schedules",
            answer_type="schedule_access_verification",
            subject_label=subject,
            primary_fact={
                "id": "schedule.access_allowed",
                "label": "Schedule access allowed",
                "value": allowed,
                "display_value": display,
                "kind": "boolean",
                "source": "schedules",
                "must_appear": True,
            },
            supporting_facts=[
                {
                    "id": "schedule.checked_at",
                    "label": "Checked time",
                    "value": payload.get("checked_at"),
                    "display_value": checked_at,
                    "kind": "timestamp",
                    "source": "schedules",
                    "must_appear": False,
                }
            ],
            source_records=[
                {
                    "entity_type": payload.get("entity_type") or "schedule",
                    "source": payload.get("source"),
                    "schedule_id": payload.get("schedule_id"),
                    "schedule_name": payload.get("schedule_name"),
                    "checked_at": payload.get("checked_at"),
                }
            ],
            display={"voice": "natural_concise", "no_timezone_labels": True},
            canonical_text=display,
        )
    ]


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


async def _person_map(session) -> dict[str, dict[str, str]]:
    people = (
        await session.scalars(select(Person).options(selectinload(Person.group)))
    ).all()
    return {
        str(person.id): {
            "display_name": person.display_name,
            "group": person.group.name if person.group else "",
        }
        for person in people
    }


def _person_match_key(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def _person_record_matches(person: dict[str, str], requested: str) -> bool:
    requested_key = _person_match_key(requested)
    display_key = _person_match_key(person.get("display_name", ""))
    group_key = _person_match_key(person.get("group", ""))
    if not requested_key:
        return False
    if requested_key == display_key or requested_key in display_key:
        return True
    requested_tokens = set(requested_key.split())
    display_tokens = set(display_key.split())
    return bool(requested_tokens and requested_tokens <= display_tokens) or requested_key == group_key


def _entity_match_key(value: str) -> str:
    cleaned = _person_match_key(value)
    tokens = [
        token
        for token in cleaned.split()
        if token not in {"a", "an", "my", "of", "please", "the", "that", "this", "their", "vehicle", "car"}
    ]
    return " ".join(tokens)


def _entity_match_score(query_key: str, candidate_text: str, *, exact_value: str | None = None) -> int:
    candidate_key = _entity_match_key(candidate_text)
    exact_key = _entity_match_key(exact_value or "")
    if not query_key or not candidate_key:
        return 0
    if query_key == exact_key:
        return 100
    if query_key == candidate_key:
        return 95
    if query_key in candidate_key:
        return 80
    query_tokens = set(query_key.split())
    candidate_tokens = set(candidate_key.split())
    if query_tokens and query_tokens <= candidate_tokens:
        return 75
    overlap = query_tokens & candidate_tokens
    if overlap:
        return 50 + min(20, len(overlap) * 5)
    return 0


SECRET_OR_INTERNAL_KEY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "session",
    "token",
    "x-api-key",
)
LARGE_PAYLOAD_KEY_MARKERS = (
    "image",
    "photo",
    "profile_photo_data_url",
    "snapshot",
    "thumbnail",
    "video",
    "vehicle_photo_data_url",
)


def _compact_observation(value: Any) -> Any:
    return _strip_empty(_compact_value(value))


def _compact_value(
    value: Any,
    *,
    key: str | None = None,
    depth: int = 0,
    max_depth: int = 4,
    max_list_items: int = 10,
    max_dict_keys: int = 40,
) -> Any:
    key_lower = (key or "").lower()
    if any(marker in key_lower for marker in SECRET_OR_INTERNAL_KEY_MARKERS):
        return "[redacted]"
    if any(marker in key_lower for marker in LARGE_PAYLOAD_KEY_MARKERS):
        return "[omitted_large_media]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        return value if len(value) <= 800 else f"{value[:800]}... [truncated {len(value) - 800} chars]"
    if isinstance(value, list):
        if depth >= max_depth:
            return {"type": "list", "count": len(value)}
        compacted_list = [
            _compact_value(item, key=key, depth=depth + 1, max_depth=max_depth, max_list_items=max_list_items)
            for item in value[:max_list_items]
        ]
        if len(value) > max_list_items:
            compacted_list.append({"omitted_items": len(value) - max_list_items})
        return compacted_list
    if isinstance(value, dict):
        if depth >= max_depth:
            return {"type": "object", "key_count": len(value), "keys": list(map(str, value.keys()))[:20]}
        items = list(value.items())
        compacted_dict = {
            str(item_key): _compact_value(
                item_value,
                key=str(item_key),
                depth=depth + 1,
                max_depth=max_depth,
                max_list_items=max_list_items,
            )
            for item_key, item_value in items[:max_dict_keys]
        }
        if len(items) > max_dict_keys:
            compacted_dict["omitted_keys"] = len(items) - max_dict_keys
        return compacted_dict
    return str(value)


def _strip_empty(value: Any) -> Any:
    if isinstance(value, list):
        return [
            stripped
            for item in value
            if (stripped := _strip_empty(item)) not in (None, "", [], {})
        ]
    if isinstance(value, dict):
        return {
            key: stripped
            for key, item in value.items()
            if (stripped := _strip_empty(item)) not in (None, "", [], {})
        }
    return value


def _payload_summary(value: Any) -> Any:
    if value in (None, "", [], {}):
        return None
    return _compact_value(value, max_depth=2, max_list_items=6, max_dict_keys=24)


async def _resolve_cover_target(arguments: dict[str, Any], *, entity_type: str) -> dict[str, Any] | None:
    config = await get_runtime_config()
    requested_id = str(
        arguments.get("entity_id")
        or arguments.get("home_assistant_entity_id")
        or arguments.get("cover_entity_id")
        or ""
    ).strip()
    requested_name = _normalize(arguments.get("entity_name") or arguments.get("name") or arguments.get("target"))
    matches: list[tuple[int, dict[str, Any]]] = []

    for kind, entities in _cover_entities_by_kind(config).items():
        if entity_type not in {"door", kind}:
            continue
        setting_key = "home_assistant_gate_entities" if kind == "gate" else "home_assistant_garage_door_entities"
        for entity in entities:
            entity_id = str(entity.get("entity_id") or "")
            name = str(entity.get("name") or entity_id)
            match = {"kind": kind, "setting_key": setting_key, "entity": dict(entity)}
            if requested_id and entity_id == requested_id:
                matches.append((100, match))
            elif requested_name and (requested_name == name.lower() or requested_name in f"{name} {entity_id}".lower()):
                matches.append((90, match))
            elif requested_name:
                score = _cover_target_match_score(requested_name, name, entity_id, kind)
                if score >= 2:
                    matches.append((score, match))

    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    if len(matches) == 1 or matches[0][0] > matches[1][0]:
        return matches[0][1]
    return None


def _cover_target_match_score(requested: str, name: str, entity_id: str, kind: str) -> int:
    requested_key = _cover_match_key(requested)
    name_key = _cover_match_key(name)
    entity_key = _cover_match_key(entity_id)
    kind_key = _cover_match_key(kind)
    candidate_key = " ".join(part for part in [name_key, entity_key, kind_key] if part)
    if not requested_key or not candidate_key:
        return 0
    if requested_key == name_key or requested_key == entity_key:
        return 100
    if requested_key in candidate_key or name_key and name_key in requested_key:
        return 50
    requested_tokens = set(requested_key.split())
    candidate_tokens = set(candidate_key.split())
    return len(requested_tokens & candidate_tokens)


def _cover_match_key(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", value.lower())
    tokens = [
        token
        for token in cleaned.split()
        if token not in {"the", "a", "an", "cover", "entity", "id"}
    ]
    return " ".join(tokens)


def _cover_entities_by_kind(config: Any) -> dict[str, list[dict[str, Any]]]:
    return {
        "gate": list(config.home_assistant_gate_entities),
        "garage_door": list(config.home_assistant_garage_door_entities),
    }


def _parse_agent_datetime(value: Any, timezone_name: str) -> datetime:
    if not value:
        return _agent_now(timezone_name)
    text = str(value).strip()
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_agent_timezone(timezone_name))
    return parsed.astimezone(_agent_timezone(timezone_name))


def _uuid_from_value(value: Any) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _agent_timezone(timezone_name: str | None = None) -> ZoneInfo:
    try:
        return ZoneInfo(str(timezone_name or DEFAULT_AGENT_TIMEZONE))
    except Exception:
        return ZoneInfo(DEFAULT_AGENT_TIMEZONE)


def _agent_now(timezone_name: str | None = None) -> datetime:
    return datetime.now(tz=_agent_timezone(timezone_name))


def _agent_datetime(value: datetime, timezone_name: str | None = None) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(_agent_timezone(timezone_name))


def _agent_datetime_iso(value: datetime, timezone_name: str | None = None) -> str:
    return _agent_datetime(value, timezone_name).isoformat()


def _agent_datetime_display(value: datetime, timezone_name: str | None = None) -> str:
    return _agent_datetime(value, timezone_name).strftime("%d %b %Y, %H:%M")


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def _preferred_subject_label(arguments: dict[str, Any], fallback: Any) -> str:
    for key in ("person", "visitor_name", "group", "registration_number"):
        value = str(arguments.get(key) or "").strip()
        if value:
            if key in {"person", "visitor_name"} and value == value.lower() and value.replace(" ", "").isalpha():
                return value.title()
            return value
    return str(fallback or "The matched subject").strip() or "The matched subject"


def _compact_time_label(display_value: Any) -> str:
    value = str(display_value or "").strip()
    if ", " not in value:
        return value
    return value.rsplit(", ", 1)[-1].strip() or value


__all__ = (
    "asyncio",
    "csv",
    "io",
    "re",
    "ContextVar",
    "Token",
    "UTC",
    "datetime",
    "timedelta",
    "SequenceMatcher",
    "Any",
    "Awaitable",
    "Callable",
    "UUID",
    "ZoneInfo",
    "func",
    "or_",
    "select",
    "IntegrityError",
    "selectinload",
    "get_logger",
    "AsyncSessionLocal",
    "AccessEvent",
    "AutomationRule",
    "AuditLog",
    "Anomaly",
    "GateStateObservation",
    "NotificationRule",
    "Person",
    "Presence",
    "Schedule",
    "ScheduleOverride",
    "TelemetrySpan",
    "TelemetryTrace",
    "User",
    "Vehicle",
    "VisitorPass",
    "AccessDecision",
    "AccessDirection",
    "PresenceState",
    "TimingClassification",
    "VisitorPassStatus",
    "VisitorPassType",
    "ImageAnalysisUnsupportedError",
    "analyze_image_with_provider",
    "cover_entity_state_payload",
    "DvlaVehicleEnquiryError",
    "display_vehicle_record",
    "normalize_registration_number",
    "UnifiProtectError",
    "NotificationContext",
    "NotificationDeliveryError",
    "ChatAttachmentError",
    "chat_attachment_store",
    "AuthSecretRotationError",
    "auth_secret_security_status",
    "rotate_auth_secret",
    "get_access_event_service",
    "alert_snapshot_metadata",
    "alert_snapshot_path",
    "AutomationError",
    "get_automation_service",
    "normalize_automation_actions",
    "normalize_automation_conditions",
    "normalize_automation_triggers",
    "serialize_automation_rule",
    "lookup_vehicle_registration",
    "normalize_vehicle_enquiry_response",
    "DependencyUpdateError",
    "get_dependency_update_service",
    "get_discord_messaging_service",
    "event_bus",
    "get_gate_malfunction_service",
    "get_home_assistant_service",
    "ICloudCalendarError",
    "get_icloud_calendar_service",
    "get_leaderboard_service",
    "get_lpr_timing_recorder",
    "get_maintenance_mode_status",
    "is_maintenance_mode_active",
    "set_maintenance_mode",
    "get_notification_service",
    "normalize_actions",
    "normalize_conditions",
    "normalize_rule_payload",
    "notification_context_from_payload",
    "sample_notification_context",
    "evaluate_schedule_id",
    "evaluate_person_schedule",
    "evaluate_vehicle_schedule",
    "normalize_time_blocks",
    "schedule_dependencies",
    "schedule_allows_at",
    "UnknownDynamicSettingsError",
    "get_runtime_config",
    "list_settings",
    "update_settings",
    "get_snapshot_manager",
    "get_unifi_protect_service",
    "TELEMETRY_CATEGORY_ALFRED",
    "TELEMETRY_CATEGORY_ACCESS",
    "TELEMETRY_CATEGORY_WEBHOOKS_API",
    "telemetry",
    "write_audit_log",
    "as_dict",
    "as_dict_list",
    "as_list",
    "artifact_payload",
    "DEFAULT_WINDOW_MINUTES",
    "VisitorPassError",
    "get_visitor_pass_service",
    "serialize_visitor_pass",
    "get_whatsapp_messaging_service",
    "ToolHandler",
    "CHAT_TOOL_CONTEXT",
    "logger",
    "DEFAULT_AGENT_TIMEZONE",
    "SAFETY_READ_ONLY",
    "SAFETY_CONFIRMATION_REQUIRED",
    "SAFETY_ADMIN_ONLY",
    "SAFETY_LEVELS",
    "ADMIN_PERMISSION",
    "SCHEDULE_TIME_BLOCKS_SCHEMA",
    "SCHEDULE_LOOKUP_PROPERTIES",
    "NOTIFICATION_RULE_LOOKUP_PROPERTIES",
    "NOTIFICATION_CONDITION_SCHEMA",
    "NOTIFICATION_ACTION_SCHEMA",
    "NOTIFICATION_RULE_PAYLOAD_SCHEMA",
    "AUTOMATION_RULE_LOOKUP_PROPERTIES",
    "AUTOMATION_TRIGGER_SCHEMA",
    "AUTOMATION_CONDITION_SCHEMA",
    "AUTOMATION_ACTION_SCHEMA",
    "AUTOMATION_RULE_PAYLOAD_SCHEMA",
    "set_chat_tool_context",
    "get_chat_tool_context",
    "_chat_context_user",
    "_require_admin_user",
    "_schedule_answer_artifacts",
    "_bounded_int",
    "_person_map",
    "_person_match_key",
    "_person_record_matches",
    "_entity_match_key",
    "_entity_match_score",
    "SECRET_OR_INTERNAL_KEY_MARKERS",
    "LARGE_PAYLOAD_KEY_MARKERS",
    "_compact_observation",
    "_compact_value",
    "_strip_empty",
    "_payload_summary",
    "_resolve_cover_target",
    "_cover_target_match_score",
    "_cover_match_key",
    "_cover_entities_by_kind",
    "_parse_agent_datetime",
    "_uuid_from_value",
    "_optional_text",
    "_agent_timezone",
    "_agent_now",
    "_agent_datetime",
    "_agent_datetime_iso",
    "_agent_datetime_display",
    "_normalize",
    "_preferred_subject_label",
    "_compact_time_label",
)
