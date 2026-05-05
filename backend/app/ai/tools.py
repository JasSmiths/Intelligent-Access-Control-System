import asyncio
import csv
import io
import re
from contextvars import ContextVar, Token
from dataclasses import dataclass
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
    Group,
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
from app.modules.home_assistant.client import HomeAssistantClient
from app.modules.home_assistant.covers import command_cover, cover_entity_state_payload
from app.modules.dvla.vehicle_enquiry import DvlaVehicleEnquiryError, display_vehicle_record, normalize_registration_number
from app.modules.unifi_protect.client import UnifiProtectError
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError
from app.services.chat_attachments import ChatAttachmentError, chat_attachment_store
from app.services.auth_secret_management import AuthSecretRotationError, auth_secret_security_status, rotate_auth_secret
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
from app.services.settings import get_runtime_config, list_settings, update_settings
from app.services.unifi_protect import get_unifi_protect_service
from app.services.telemetry import TELEMETRY_CATEGORY_ALFRED, TELEMETRY_CATEGORY_ACCESS, TELEMETRY_CATEGORY_WEBHOOKS_API, telemetry, write_audit_log
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
GATE_OBSERVATION_PAYLOAD_KEY = "_iacs_gate_observation"
KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY = "_iacs_known_vehicle_plate_match"

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

NATURAL_SCHEDULE_DAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "weds": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}

NATURAL_SCHEDULE_DAY_PATTERN = (
    r"mon(?:day)?(?:'s|s)?|"
    r"tue(?:s|sday)?(?:'s|s)?|"
    r"wed(?:s|nesday)?(?:'s|s)?|"
    r"thu(?:r|rs|rsday)?(?:'s|s)?|"
    r"fri(?:day)?(?:'s|s)?|"
    r"sat(?:urday)?(?:'s|s)?|"
    r"sun(?:day)?(?:'s|s)?"
)

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


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    categories: tuple[str, ...] = ("General",)
    read_only: bool = True
    requires_confirmation: bool = False
    default_limit: int | None = None

    def as_llm_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


def build_agent_tools() -> dict[str, AgentTool]:
    from app.ai.tool_groups.registry import build_grouped_tools

    return _with_tool_metadata(build_grouped_tools())


def _with_tool_metadata(tools: list[AgentTool]) -> dict[str, AgentTool]:
    categories: dict[str, tuple[str, ...]] = {
        "resolve_human_entity": ("General",),
        "query_presence": ("Access_Logs", "General"),
        "query_access_events": ("Access_Logs", "Access_Diagnostics", "General"),
        "diagnose_access_event": ("Access_Diagnostics",),
        "investigate_access_incident": ("Access_Diagnostics", "Access_Logs", "Gate_Hardware", "Notifications"),
        "query_unifi_protect_events": ("Access_Diagnostics", "Cameras"),
        "backfill_access_event_from_protect": ("Access_Diagnostics",),
        "test_unifi_alarm_webhook": ("Access_Diagnostics", "Cameras"),
        "query_lpr_timing": ("Access_Diagnostics", "Access_Logs"),
        "query_vehicle_detection_history": ("Access_Logs", "Access_Diagnostics", "Compliance_DVLA"),
        "get_telemetry_trace": ("Access_Diagnostics", "Users_Settings"),
        "query_anomalies": ("Access_Logs", "Access_Diagnostics", "General"),
        "summarize_access_rhythm": ("Access_Logs", "General"),
        "calculate_visit_duration": ("Access_Logs",),
        "trigger_anomaly_alert": ("Access_Logs", "Notifications"),
        "query_device_states": ("Gate_Hardware", "General"),
        "open_device": ("Gate_Hardware",),
        "command_device": ("Gate_Hardware",),
        "open_gate": ("Gate_Hardware",),
        "get_active_malfunctions": ("Gate_Hardware", "Access_Diagnostics"),
        "get_malfunction_history": ("Gate_Hardware", "Access_Diagnostics"),
        "trigger_manual_malfunction_override": ("Gate_Hardware",),
        "get_maintenance_status": ("Maintenance", "Gate_Hardware", "Access_Diagnostics"),
        "enable_maintenance_mode": ("Maintenance",),
        "disable_maintenance_mode": ("Maintenance",),
        "toggle_maintenance_mode": ("Maintenance",),
        "lookup_dvla_vehicle": ("Compliance_DVLA",),
        "query_leaderboard": ("Access_Logs", "Compliance_DVLA"),
        "analyze_camera_snapshot": ("Cameras", "Access_Diagnostics"),
        "get_camera_snapshot": ("Cameras",),
        "read_chat_attachment": ("Reports_Files", "General"),
        "export_presence_report_csv": ("Reports_Files", "Access_Logs"),
        "generate_contractor_invoice_pdf": ("Reports_Files", "Access_Logs"),
        "query_notification_catalog": ("Notifications",),
        "query_notification_workflows": ("Notifications",),
        "get_notification_workflow": ("Notifications",),
        "create_notification_workflow": ("Notifications",),
        "update_notification_workflow": ("Notifications",),
        "delete_notification_workflow": ("Notifications",),
        "preview_notification_workflow": ("Notifications",),
        "test_notification_workflow": ("Notifications",),
        "query_automation_catalog": ("Automations", "Notifications", "Gate_Hardware", "Maintenance"),
        "query_automations": ("Automations",),
        "get_automation": ("Automations",),
        "create_automation": ("Automations",),
        "edit_automation": ("Automations",),
        "delete_automation": ("Automations",),
        "enable_automation": ("Automations",),
        "disable_automation": ("Automations",),
        "query_schedules": ("Schedules", "Access_Diagnostics"),
        "get_schedule": ("Schedules", "Access_Diagnostics"),
        "create_schedule": ("Schedules",),
        "update_schedule": ("Schedules",),
        "delete_schedule": ("Schedules",),
        "query_schedule_targets": ("Schedules",),
        "assign_schedule_to_entity": ("Schedules",),
        "override_schedule": ("Schedules",),
        "verify_schedule_access": ("Schedules", "Access_Diagnostics"),
        "get_system_users": ("Users_Settings",),
        "query_integration_health": ("System_Operations", "Users_Settings", "General"),
        "test_integration_connection": ("System_Operations", "Users_Settings"),
        "query_system_settings": ("System_Operations", "Users_Settings"),
        "update_system_settings": ("System_Operations", "Users_Settings"),
        "query_auth_secret_status": ("System_Operations", "Users_Settings"),
        "rotate_auth_secret": ("System_Operations", "Users_Settings"),
        "query_alfred_runtime_events": ("System_Operations", "Users_Settings"),
        "query_dependency_updates": ("System_Operations",),
        "check_dependency_updates": ("System_Operations",),
        "analyze_dependency_update": ("System_Operations",),
        "apply_dependency_update": ("System_Operations",),
        "query_dependency_backups": ("System_Operations",),
        "restore_dependency_backup": ("System_Operations",),
        "query_dependency_update_job": ("System_Operations",),
        "configure_dependency_backup_storage": ("System_Operations",),
        "validate_dependency_backup_storage": ("System_Operations",),
        "query_visitor_passes": ("Visitor_Passes", "Access_Logs", "General"),
        "get_visitor_pass": ("Visitor_Passes", "Access_Logs"),
        "create_visitor_pass": ("Visitor_Passes",),
        "update_visitor_pass": ("Visitor_Passes",),
        "cancel_visitor_pass": ("Visitor_Passes",),
        "trigger_icloud_sync": ("Calendar_Integrations", "Visitor_Passes"),
    }
    state_changing = {
        "assign_schedule_to_entity",
        "cancel_visitor_pass",
        "create_notification_workflow",
        "create_automation",
        "create_schedule",
        "create_visitor_pass",
        "trigger_icloud_sync",
        "delete_notification_workflow",
        "delete_automation",
        "delete_schedule",
        "disable_automation",
        "disable_maintenance_mode",
        "edit_automation",
        "enable_automation",
        "enable_maintenance_mode",
        "command_device",
        "backfill_access_event_from_protect",
        "open_gate",
        "open_device",
        "override_schedule",
        "test_integration_connection",
        "update_system_settings",
        "rotate_auth_secret",
        "check_dependency_updates",
        "analyze_dependency_update",
        "apply_dependency_update",
        "restore_dependency_backup",
        "configure_dependency_backup_storage",
        "validate_dependency_backup_storage",
        "investigate_access_incident",
        "trigger_anomaly_alert",
        "trigger_manual_malfunction_override",
        "test_unifi_alarm_webhook",
        "test_notification_workflow",
        "toggle_maintenance_mode",
        "update_notification_workflow",
        "update_schedule",
        "update_visitor_pass",
    }
    defaults = {
        "query_access_events": 10,
        "query_anomalies": 10,
        "query_leaderboard": 10,
        "query_lpr_timing": 25,
        "query_unifi_protect_events": 25,
        "query_notification_workflows": 20,
        "query_automations": 20,
        "query_schedule_targets": 25,
        "query_visitor_passes": 20,
        "query_alfred_runtime_events": 20,
        "get_telemetry_trace": 20,
    }
    return {
        tool.name: AgentTool(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            handler=tool.handler,
            categories=categories.get(tool.name, tool.categories),
            read_only=tool.name not in state_changing,
            requires_confirmation=tool.name in state_changing,
            default_limit=defaults.get(tool.name, tool.default_limit),
        )
        for tool in tools
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


def _admin_required_result(action: str) -> dict[str, Any]:
    return {"changed": False, "error": f"Admin access is required for {action}."}


async def _require_admin_user(action: str) -> User | dict[str, Any]:
    context = get_chat_tool_context()
    if str(context.get("user_role") or "").lower() != "admin":
        return _admin_required_result(action)
    user = await _chat_context_user()
    if not user:
        return {"changed": False, "error": f"Authenticated Admin context is required for {action}."}
    return user


async def query_integration_health(arguments: dict[str, Any]) -> dict[str, Any]:
    runtime = await get_runtime_config()
    requested = str(arguments.get("integration") or "all").strip().lower()
    health = {
        "home_assistant": await get_home_assistant_service().status(refresh=False),
        "unifi_protect": await get_unifi_protect_service().status(refresh=False),
        "discord": await get_discord_messaging_service().status(),
        "whatsapp": await get_whatsapp_messaging_service().status(),
        "dvla": {"configured": bool(runtime.dvla_api_key), "endpoint": runtime.dvla_vehicle_enquiry_url},
        "llm": {
            "provider": runtime.llm_provider,
            "openai_configured": bool(runtime.openai_api_key),
            "gemini_configured": bool(runtime.gemini_api_key),
            "anthropic_configured": bool(runtime.anthropic_api_key),
            "ollama_configured": bool(runtime.ollama_base_url),
        },
        "dependency_updates": {
            "backup_storage": await get_dependency_update_service().storage_status(),
        },
    }
    if requested and requested != "all":
        return {"integration": requested, "health": health.get(requested, {"error": "Unknown integration."})}
    return {"integrations": health}


async def test_integration_connection(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("integration connection tests")
    if isinstance(admin, dict):
        return admin
    integration = str(arguments.get("integration") or "").strip().lower()
    if not integration:
        return {"tested": False, "error": "integration is required."}
    if not bool(arguments.get("confirm")):
        return {
            "tested": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": f"{integration} connection",
            "detail": "Testing integrations can contact external providers. Confirm before Alfred runs the test.",
        }
    try:
        if integration == "home_assistant":
            result = await get_home_assistant_service().status(refresh=True)
        elif integration == "unifi_protect":
            result = await get_unifi_protect_service().status(refresh=True)
        elif integration == "discord":
            await get_discord_messaging_service().test_connection({})
            result = {"ok": True}
        elif integration == "whatsapp":
            await get_whatsapp_messaging_service().test_connection({})
            result = {"ok": True}
        elif integration == "apprise":
            runtime = await get_runtime_config()
            result = {"configured": bool(runtime.apprise_urls)}
        elif integration == "dvla":
            runtime = await get_runtime_config()
            result = {"configured": bool(runtime.dvla_api_key), "endpoint": runtime.dvla_vehicle_enquiry_url}
        else:
            return {"tested": False, "integration": integration, "error": "Unknown integration."}
    except Exception as exc:
        return {"tested": True, "integration": integration, "ok": False, "error": str(exc)[:500]}
    return {"tested": True, "integration": integration, "ok": not bool(result.get("error")), "result": result}


async def query_system_settings(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("system setting reads")
    if isinstance(admin, dict):
        return admin
    category = str(arguments.get("category") or "").strip() or None
    rows = await list_settings(category=category)
    return {"settings": rows, "category": category, "redacted": True}


async def update_system_settings(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("system setting updates")
    if isinstance(admin, dict):
        return admin
    values = arguments.get("values")
    if not isinstance(values, dict) or not values:
        return {"updated": False, "error": "values must be a non-empty object."}
    if not bool(arguments.get("confirm")):
        return {
            "updated": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "System Settings",
            "detail": f"Update {len(values)} setting(s)? Secrets stay redacted, but this can change live IACS behavior.",
            "setting_keys": sorted(str(key) for key in values.keys()),
        }
    rows = await update_settings(values)
    changed_keys = sorted(str(key) for key in values.keys())
    return {
        "updated": True,
        "changed_keys": changed_keys,
        "settings": [row for row in rows if row.get("key") in changed_keys],
        "redacted": True,
    }


async def query_auth_secret_status(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("auth-secret status")
    if isinstance(admin, dict):
        return admin
    return await auth_secret_security_status()


async def rotate_auth_secret_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("auth-secret rotation")
    if isinstance(admin, dict):
        return admin
    if arguments.get("new_secret"):
        return {"rotated": False, "error": "Alfred only supports generated auth-secret rotation."}
    if not bool(arguments.get("confirm")):
        return {
            "rotated": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Auth Secret",
            "detail": (
                "Rotate the auth root secret? This invalidates existing login sessions and pending action links, "
                "then re-encrypts dynamic secrets."
            ),
        }
    try:
        return await rotate_auth_secret(user=admin, confirmed=True, new_secret=None)
    except AuthSecretRotationError as exc:
        return {"rotated": False, "error": str(exc)}


async def query_alfred_runtime_events(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("Alfred runtime diagnostics")
    if isinstance(admin, dict):
        return admin
    await telemetry.flush()
    config = await get_runtime_config()
    limit = _bounded_int(arguments.get("limit"), default=20, minimum=1, maximum=100)
    hours = _bounded_int(arguments.get("hours"), default=24, minimum=1, maximum=168)
    since = datetime.now(tz=UTC) - timedelta(hours=hours)
    actions = {
        "alfred.chat.http_error",
        "alfred.chat.http_confirm_error",
        "alfred.chat.sse_error",
        "alfred.chat.websocket_error",
        "alfred.chat.websocket_receive_error",
    }
    async with AsyncSessionLocal() as session:
        rows = (
            await session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.category == TELEMETRY_CATEGORY_ALFRED,
                    AuditLog.action.in_(actions),
                    AuditLog.timestamp >= since,
                )
                .order_by(AuditLog.timestamp.desc())
                .limit(limit)
            )
        ).all()
    events = [
        {
            "id": str(row.id),
            "timestamp": row.timestamp.isoformat() if row.timestamp else None,
            "action": row.action,
            "channel": (row.metadata_ or {}).get("channel"),
            "error_type": (row.metadata_ or {}).get("error_type"),
            "message_preview": (row.metadata_ or {}).get("message_preview"),
            "session_id": (row.metadata_ or {}).get("session_id") or row.target_id,
            "provider": (row.metadata_ or {}).get("provider"),
            "outcome": row.outcome,
            "level": row.level,
        }
        for row in rows
    ]
    return {
        "events": events,
        "count": len(events),
        "has_recent_failures": bool(events),
        "window_hours": hours,
        "timezone": config.site_timezone,
        "redacted": True,
    }


async def query_dependency_updates(arguments: dict[str, Any]) -> dict[str, Any]:
    update_only = bool(arguments.get("update_only"))
    packages = await get_dependency_update_service().list_packages(update_only=update_only)
    return {"packages": packages, "count": len(packages), "update_only": update_only}


async def check_dependency_updates(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("dependency update checks")
    if isinstance(admin, dict):
        return admin
    if not bool(arguments.get("confirm")):
        return {
            "checked": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Dependency Updates",
            "detail": "Check configured dependencies against package registries?",
        }
    try:
        return await get_dependency_update_service().check_all_packages(
            direct_only=bool(arguments.get("direct_only")),
            user=admin,
            source="alfred",
        )
    except DependencyUpdateError as exc:
        return {"checked": False, "error": str(exc)}


async def analyze_dependency_update(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("dependency update analysis")
    if isinstance(admin, dict):
        return admin
    dependency_id = _uuid_from_value(arguments.get("dependency_id"))
    if not dependency_id:
        return {"analyzed": False, "error": "dependency_id is required."}
    if not bool(arguments.get("confirm")):
        return {
            "analyzed": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Dependency Analysis",
            "detail": "Analyze this dependency update using release metadata and the configured LLM provider?",
        }
    try:
        return await get_dependency_update_service().analyze_package(
            dependency_id,
            target_version=str(arguments.get("target_version") or "") or None,
            provider=str(arguments.get("provider") or "") or None,
            user=admin,
        )
    except DependencyUpdateError as exc:
        return {"analyzed": False, "error": str(exc)}


async def apply_dependency_update(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("dependency update apply jobs")
    if isinstance(admin, dict):
        return admin
    dependency_id = _uuid_from_value(arguments.get("dependency_id"))
    if not dependency_id:
        return {"started": False, "error": "dependency_id is required."}
    if not bool(arguments.get("confirm")):
        return {
            "started": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Dependency Apply Job",
            "detail": "Apply this dependency update? Alfred will use the existing backup and job pipeline, not shell commands.",
        }
    try:
        return await get_dependency_update_service().start_apply_job(
            dependency_id,
            target_version=str(arguments.get("target_version") or "") or None,
            confirmed=True,
            user=admin,
        )
    except DependencyUpdateError as exc:
        return {"started": False, "error": str(exc)}


async def query_dependency_backups(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("dependency backup reads")
    if isinstance(admin, dict):
        return admin
    dependency_id = _uuid_from_value(arguments.get("dependency_id"))
    backups = await get_dependency_update_service().list_backups(dependency_id)
    return {"backups": backups, "count": len(backups)}


async def restore_dependency_backup(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("dependency backup restore jobs")
    if isinstance(admin, dict):
        return admin
    backup_id = _uuid_from_value(arguments.get("backup_id"))
    if not backup_id:
        return {"started": False, "error": "backup_id is required."}
    if not bool(arguments.get("confirm")):
        return {
            "started": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Dependency Restore Job",
            "detail": "Restore this dependency backup? This uses the existing restore job pipeline.",
        }
    try:
        return await get_dependency_update_service().start_restore_job(backup_id, confirmed=True, user=admin)
    except DependencyUpdateError as exc:
        return {"started": False, "error": str(exc)}


async def query_dependency_update_job(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("dependency job reads")
    if isinstance(admin, dict):
        return admin
    job_id = _uuid_from_value(arguments.get("job_id"))
    if not job_id:
        return {"found": False, "error": "job_id is required."}
    try:
        return await get_dependency_update_service().job_status(job_id)
    except DependencyUpdateError as exc:
        return {"found": False, "error": str(exc)}


async def configure_dependency_backup_storage(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("dependency backup storage configuration")
    if isinstance(admin, dict):
        return admin
    payload = {
        "mode": arguments.get("mode"),
        "mount_source": arguments.get("mount_source"),
        "retention_days": arguments.get("retention_days"),
        "min_free_bytes": arguments.get("min_free_bytes"),
    }
    if "mount_options" in arguments:
        payload["mount_options"] = arguments.get("mount_options")
    if not bool(arguments.get("confirm")):
        return {
            "configured": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Dependency Backup Storage",
            "detail": "Update dependency backup storage? Sensitive mount options stay redacted.",
            "mount_options_configured": bool(arguments.get("mount_options")),
        }
    try:
        return await get_dependency_update_service().save_storage_config(payload, user=admin)
    except DependencyUpdateError as exc:
        return {"configured": False, "error": str(exc)}


async def validate_dependency_backup_storage(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("dependency backup storage validation")
    if isinstance(admin, dict):
        return admin
    if not bool(arguments.get("confirm")):
        return {
            "validated": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Dependency Backup Storage",
            "detail": "Validate backup storage writability and free space?",
        }
    return await get_dependency_update_service().validate_storage()


async def resolve_human_entity(arguments: dict[str, Any]) -> dict[str, Any]:
    query_text = str(arguments.get("query") or "").strip()
    if not query_text:
        return {"status": "not_found", "query": query_text, "matches": [], "error": "query is required."}

    requested_types = arguments.get("entity_types")
    if isinstance(requested_types, list) and requested_types:
        entity_types = {
            str(item).strip().lower()
            for item in requested_types
            if str(item).strip().lower() in {"person", "vehicle", "group", "device", "visitor_pass"}
        }
    else:
        entity_types = {"person", "vehicle", "group", "device", "visitor_pass"}
    include_inactive = bool(arguments.get("include_inactive"))
    query_key = _entity_match_key(query_text)
    matches: list[dict[str, Any]] = []

    async with AsyncSessionLocal() as session:
        if "person" in entity_types:
            people = (
                await session.scalars(
                    select(Person)
                    .options(selectinload(Person.group), selectinload(Person.vehicles))
                    .order_by(Person.display_name)
                )
            ).all()
            for person in people:
                if not include_inactive and not person.is_active:
                    continue
                haystack = " ".join(
                    str(value or "")
                    for value in [
                        person.display_name,
                        person.first_name,
                        person.last_name,
                        person.notes,
                        person.group.name if person.group else "",
                        " ".join(vehicle.registration_number for vehicle in person.vehicles),
                        " ".join(str(vehicle.make or "") for vehicle in person.vehicles),
                        " ".join(str(vehicle.model or "") for vehicle in person.vehicles),
                        " ".join(str(vehicle.color or "") for vehicle in person.vehicles),
                    ]
                )
                score = _entity_match_score(query_key, haystack, exact_value=person.display_name)
                if score:
                    matches.append(
                        _compact_observation(
                            {
                                "type": "person",
                                "score": score,
                                "id": str(person.id),
                                "display_name": person.display_name,
                                "group": person.group.name if person.group else None,
                                "is_active": person.is_active,
                                "vehicle_ids": [str(vehicle.id) for vehicle in person.vehicles],
                                "registration_numbers": [vehicle.registration_number for vehicle in person.vehicles],
                            }
                        )
                    )

        if "vehicle" in entity_types:
            vehicles = (
                await session.scalars(
                    select(Vehicle)
                    .options(selectinload(Vehicle.owner), selectinload(Vehicle.schedule))
                    .order_by(Vehicle.registration_number)
                )
            ).all()
            plate_query = normalize_registration_number(query_text)
            for vehicle in vehicles:
                if not include_inactive and not vehicle.is_active:
                    continue
                haystack = " ".join(
                    str(value or "")
                    for value in [
                        vehicle.registration_number,
                        vehicle.make,
                        vehicle.model,
                        vehicle.color,
                        vehicle.description,
                        vehicle.owner.display_name if vehicle.owner else "",
                    ]
                )
                score = _entity_match_score(query_key, haystack, exact_value=vehicle.registration_number)
                if plate_query and plate_query == vehicle.registration_number:
                    score = max(score, 100)
                elif plate_query and plate_query in vehicle.registration_number:
                    score = max(score, 90)
                if score:
                    matches.append(
                        _compact_observation(
                            {
                                "type": "vehicle",
                                "score": score,
                                "id": str(vehicle.id),
                                "registration_number": vehicle.registration_number,
                                "make": vehicle.make,
                                "model": vehicle.model,
                                "color": vehicle.color,
                                "owner_id": str(vehicle.person_id) if vehicle.person_id else None,
                                "owner": vehicle.owner.display_name if vehicle.owner else None,
                                "schedule_id": str(vehicle.schedule_id) if vehicle.schedule_id else None,
                                "schedule_name": vehicle.schedule.name if vehicle.schedule else None,
                                "is_active": vehicle.is_active,
                            }
                        )
                    )

        if "group" in entity_types:
            groups = (await session.scalars(select(Group).order_by(Group.name))).all()
            for group in groups:
                haystack = " ".join(str(value or "") for value in [group.name, group.category.value, group.subtype, group.description])
                score = _entity_match_score(query_key, haystack, exact_value=group.name)
                if score:
                    matches.append(
                        _compact_observation(
                            {
                                "type": "group",
                                "score": score,
                                "id": str(group.id),
                                "name": group.name,
                                "category": group.category.value,
                                "subtype": group.subtype,
                            }
                        )
                    )

        if "visitor_pass" in entity_types:
            config = await get_runtime_config()
            service = get_visitor_pass_service()
            changed = await service.refresh_statuses(session=session, publish=False)
            if changed:
                await session.commit()
            visitor_passes = await service.list_passes(session, statuses=None, search=query_text, limit=10)
            for visitor_pass in visitor_passes:
                haystack = " ".join(
                    str(value or "")
                    for value in [
                        visitor_pass.visitor_name,
                        visitor_pass.number_plate,
                        visitor_pass.vehicle_make,
                        visitor_pass.vehicle_colour,
                        visitor_pass.status.value,
                    ]
                )
                score = _entity_match_score(query_key, haystack, exact_value=visitor_pass.visitor_name)
                if score:
                    payload = _visitor_pass_agent_payload(visitor_pass, config.site_timezone)
                    payload.update(
                        {
                            "type": "visitor_pass",
                            "score": score,
                            "display_name": visitor_pass.visitor_name,
                            "visitor_pass_id": str(visitor_pass.id),
                        }
                    )
                    matches.append(_compact_observation(payload))

    if "device" in entity_types:
        config = await get_runtime_config()
        device_rows = [
            ("gate", entity)
            for entity in list(getattr(config, "home_assistant_gate_entities", None) or [])
            if isinstance(entity, dict)
        ] + [
            ("garage_door", entity)
            for entity in list(getattr(config, "home_assistant_garage_door_entities", None) or [])
            if isinstance(entity, dict)
        ]
        for kind, entity in device_rows:
            if not include_inactive and entity.get("enabled") is False:
                continue
            name = str(entity.get("name") or entity.get("entity_id") or "")
            haystack = f"{name} {entity.get('entity_id') or ''} {kind.replace('_', ' ')}"
            score = _entity_match_score(query_key, haystack, exact_value=name)
            if score:
                matches.append(
                    _compact_observation(
                        {
                            "type": "device",
                            "score": score,
                            "kind": kind,
                            "entity_id": str(entity.get("entity_id") or ""),
                            "name": name,
                            "enabled": bool(entity.get("enabled", True)),
                            "schedule_id": entity.get("schedule_id"),
                        }
                    )
                )

    matches = sorted(
        matches,
        key=lambda item: (
            -int(item.get("score") or 0),
            str(item.get("type") or ""),
            str(item.get("display_name") or item.get("name") or item.get("registration_number") or ""),
        ),
    )
    if not matches:
        return {"status": "not_found", "query": query_text, "entity_types": sorted(entity_types), "matches": []}

    top_score = int(matches[0].get("score") or 0)
    top_matches = [match for match in matches if int(match.get("score") or 0) >= top_score - 5]
    status = "unique" if len(top_matches) == 1 and top_score >= 70 else "ambiguous"
    return {
        "status": status,
        "query": query_text,
        "entity_types": sorted(entity_types),
        "match": matches[0] if status == "unique" else None,
        "matches": matches[:10],
    }


async def query_presence(arguments: dict[str, Any]) -> dict[str, Any]:
    person_filter = _normalize(arguments.get("person"))
    config = await get_runtime_config()
    async with AsyncSessionLocal() as session:
        query = select(Presence).options(selectinload(Presence.person)).order_by(Presence.updated_at.desc())
        rows = (await session.scalars(query)).all()

    records = [
        {
            "person": row.person.display_name,
            "state": row.state.value,
            "last_changed_at": _agent_datetime_iso(row.last_changed_at, config.site_timezone) if row.last_changed_at else None,
            "last_changed_at_display": _agent_datetime_display(row.last_changed_at, config.site_timezone) if row.last_changed_at else None,
        }
        for row in rows
        if not person_filter or person_filter in row.person.display_name.lower()
    ]
    return {"presence": records, "timezone": config.site_timezone}


async def query_device_states(arguments: dict[str, Any]) -> dict[str, Any]:
    target = _normalize(arguments.get("target"))
    kind_filter = _normalize(arguments.get("kind") or "all")
    if kind_filter not in {"", "all", "gate", "door", "garage_door"}:
        return {"configured": False, "devices": [], "count": 0, "error": "kind must be all, gate, door, or garage_door."}

    status = await get_home_assistant_service().status()
    devices: list[dict[str, Any]] = []
    for entity in status.get("gate_entities") or []:
        devices.append(_device_state_record(entity, "gate"))
    for entity in status.get("garage_door_entities") or []:
        devices.append(_device_state_record(entity, "garage_door"))

    legacy_doors = [
        {
            "kind": "door",
            "name": "Front Door",
            "entity_id": "binary_sensor.front_door",
            "state": status.get("front_door_state"),
            "enabled": True,
        },
        {
            "kind": "door",
            "name": "Back Door",
            "entity_id": "binary_sensor.back_door",
            "state": status.get("back_door_state"),
            "enabled": True,
        },
    ]
    devices.extend(row for row in legacy_doors if row.get("state") is not None)

    filtered = [
        device
        for device in devices
        if kind_filter in {"", "all", device["kind"]}
        and (not target or _device_matches_target(device, target))
    ]
    return {
        "configured": bool(status.get("configured")),
        "devices": filtered,
        "count": len(filtered),
        "target": arguments.get("target") or None,
        "kind": kind_filter or "all",
    }


async def get_maintenance_status(arguments: dict[str, Any]) -> dict[str, Any]:
    status = await get_maintenance_mode_status()
    return {"maintenance_mode": status, **status}


async def get_active_malfunctions(arguments: dict[str, Any]) -> dict[str, Any]:
    include_timeline = bool(arguments.get("include_timeline"))
    items = await get_gate_malfunction_service().active(include_timeline=include_timeline)
    return {"count": len(items), "malfunctions": items}


async def get_malfunction_history(arguments: dict[str, Any]) -> dict[str, Any]:
    status = str(arguments.get("status") or "").strip().lower() or None
    limit = max(1, min(int(arguments.get("limit") or 25), 100))
    include_timeline = bool(arguments.get("include_timeline"))
    items = await get_gate_malfunction_service().history(
        status=status,
        limit=limit,
        include_timeline=include_timeline,
    )
    return {"count": len(items), "malfunctions": items}


async def trigger_manual_malfunction_override(arguments: dict[str, Any]) -> dict[str, Any]:
    context = get_chat_tool_context()
    if str(context.get("user_role") or "").lower() != "admin":
        return {
            "changed": False,
            "error": "Admin access is required for gate malfunction overrides.",
        }
    malfunction_id = str(arguments.get("malfunction_id") or "").strip()
    action = str(arguments.get("action") or "").strip()
    reason = str(arguments.get("reason") or "Manual Alfred gate malfunction override").strip()
    confirm = bool(arguments.get("confirm"))
    if not malfunction_id:
        return {"changed": False, "error": "malfunction_id is required."}
    if action not in {"recheck_live_state", "run_attempt_now", "mark_resolved", "mark_fubar"}:
        return {"changed": False, "error": "action must be recheck_live_state, run_attempt_now, mark_resolved, or mark_fubar."}
    return await get_gate_malfunction_service().override(
        malfunction_id,
        action=action,
        reason=reason,
        actor="Alfred",
        confirm=confirm,
    )


async def enable_maintenance_mode(arguments: dict[str, Any]) -> dict[str, Any]:
    if not bool(arguments.get("confirm")):
        return {
            "enabled": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Maintenance Mode",
            "detail": "Maintenance Mode disables automated access actions until it is turned off.",
        }
    context = get_chat_tool_context()
    status = await set_maintenance_mode(
        True,
        actor="Alfred",
        actor_user_id=str(context.get("user_id") or "") or None,
        source="Alfred",
        reason=str(arguments.get("reason") or "Enabled by Alfred").strip(),
        sync_ha=True,
    )
    return {"enabled": bool(status.get("is_active")), "maintenance_mode": status, **status}


async def disable_maintenance_mode(arguments: dict[str, Any]) -> dict[str, Any]:
    if not bool(arguments.get("confirm")):
        return {
            "disabled": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Maintenance Mode",
            "detail": "Disabling Maintenance Mode resumes automated access actions.",
        }
    context = get_chat_tool_context()
    status = await set_maintenance_mode(
        False,
        actor="Alfred",
        actor_user_id=str(context.get("user_id") or "") or None,
        source="Alfred",
        reason="Disabled by Alfred",
        sync_ha=True,
    )
    return {"disabled": not bool(status.get("is_active")), "maintenance_mode": status, **status}


async def open_device(arguments: dict[str, Any]) -> dict[str, Any]:
    context = get_chat_tool_context()
    user_id = str(context.get("user_id") or "")
    session_id = str(context.get("session_id") or "")
    target_text = str(arguments.get("target") or arguments.get("entity_id") or "").strip()
    action = _normalize(arguments.get("action") or "open")
    if action not in {"open", "close"}:
        return {"accepted": False, "error": "action must be open or close."}
    kind_filter = _normalize(arguments.get("kind") or "all")
    if kind_filter not in {"", "all", "gate", "garage_door"}:
        return {"accepted": False, "error": "kind must be all, gate, or garage_door."}
    if action == "close" and kind_filter == "gate":
        return {
            "closed": False,
            "accepted": False,
            "action": action,
            "error": "Alfred can close configured garage doors, not gates.",
        }
    if not target_text:
        return {
            "accepted": False,
            "action": action,
            "requires_details": True,
            "detail": f"Which gate or garage door should I {action}?",
        }

    target = await _resolve_openable_device(arguments, kind_filter=kind_filter or "all")
    if not target:
        return {
            "accepted": False,
            "action": action,
            "target": target_text,
            "error": f"I could not find a configured gate or garage door called {target_text}.",
        }
    if action == "close" and target["kind"] != "garage_door":
        return {
            "closed": False,
            "accepted": False,
            "action": action,
            "device": _agent_device_payload(target),
            "detail": "Alfred can close configured garage doors. Gate close commands are not enabled.",
        }

    if not bool(arguments.get("confirm")):
        device = _agent_device_payload(target)
        return {
            "opened": False,
            "closed": False,
            "accepted": False,
            "action": action,
            "requires_confirmation": True,
            "target": device["name"],
            "device": device,
            "confirmation_field": "confirm",
            "detail": (
                f"{'Opening gates and garage doors' if action == 'open' else 'Closing garage doors'} "
                "is a real-world action. Use the chat confirmation action before I continue."
            ),
        }

    if action == "open" and await is_maintenance_mode_active():
        return {
            "opened": False,
            "accepted": False,
            "action": action,
            "device": _agent_device_payload(target),
            "state": "maintenance_mode",
            "detail": "Maintenance Mode is active. Automated actions are disabled.",
            "opened_by": "agent",
        }

    config = await get_runtime_config()
    now = datetime.now(tz=UTC)
    if action == "open":
        async with AsyncSessionLocal() as session:
            schedule_evaluation = await evaluate_schedule_id(
                session,
                target["entity"].get("schedule_id"),
                now,
                timezone_name=config.site_timezone,
                default_policy=config.schedule_default_policy,
                source=str(target["kind"]),
            )
    else:
        schedule_evaluation = None
    if schedule_evaluation and not schedule_evaluation.allowed:
        detail = schedule_evaluation.reason or "Device is outside its assigned schedule."
        payload = _agent_device_audit_payload(
            target,
            action=action,
            accepted=False,
            state="schedule_denied",
            detail=detail,
            user_id=user_id,
            session_id=session_id,
        )
        await event_bus.publish("agent.device_open_failed", payload)
        logger.warning("agent_device_open_schedule_denied", extra=_log_extra(payload))
        return {
            "opened": False,
            "accepted": False,
            "device": _agent_device_payload(target),
            "action": action,
            "state": "schedule_denied",
            "detail": detail,
            "opened_by": "agent",
        }

    reason = str(arguments.get("reason") or "").strip()
    action_label = "opening" if action == "open" else "closing"
    audit_reason = reason or f"Alfred agent requested {action_label} {target['entity'].get('name') or target['entity']['entity_id']}"
    outcome = await command_cover(HomeAssistantClient(), target["entity"], action, f"Alfred agent: {audit_reason}")
    audit_payload = _agent_device_audit_payload(
        target,
        action=action,
        accepted=outcome.accepted,
        state=outcome.state,
        detail=outcome.detail,
        user_id=user_id,
        session_id=session_id,
    )
    audit_payload["reason"] = audit_reason
    agent_event = f"agent.device_{action}_requested" if outcome.accepted else f"agent.device_{action}_failed"
    device_event = f"{target['kind']}.{action}_requested" if outcome.accepted else f"{target['kind']}.{action}_failed"
    await event_bus.publish(
        agent_event,
        audit_payload,
    )
    await event_bus.publish(
        device_event,
        {
            **audit_payload,
            "source": "alfred",
        },
    )
    if outcome.accepted:
        logger.info(f"agent_device_{action}_requested", extra=_log_extra(audit_payload))
    else:
        logger.error(f"agent_device_{action}_failed", extra=_log_extra(audit_payload))

    return {
        "opened": outcome.accepted if action == "open" else False,
        "closed": outcome.accepted if action == "close" else False,
        "accepted": outcome.accepted,
        "device": _agent_device_payload(target),
        "action": action,
        "state": outcome.state,
        "detail": outcome.detail,
        f"{'opened' if action == 'open' else 'closed'}_by": "agent",
        "audit_event": agent_event,
    }


async def open_gate(arguments: dict[str, Any]) -> dict[str, Any]:
    target = str(arguments.get("target") or "").strip()
    if not target:
        config = await get_runtime_config()
        gates = [entity for entity in _cover_entities_by_kind(config).get("gate", []) if entity.get("enabled", True)]
        if len(gates) == 1:
            target = str(gates[0].get("name") or gates[0].get("entity_id") or "")
    if not target:
        return {
            "opened": False,
            "requires_details": True,
            "detail": "Which gate should I open?",
        }
    output = await open_device(
        {
            "target": target,
            "kind": "gate",
            "reason": arguments.get("reason"),
            "confirm": bool(arguments.get("confirm")),
        }
    )
    output.setdefault("target", target)
    output["tool_alias"] = "open_gate"
    return output


async def toggle_maintenance_mode(arguments: dict[str, Any]) -> dict[str, Any]:
    state = _normalize(arguments.get("state"))
    enable = state in {"enabled", "enable", "on", "true", "yes", "active"}
    disable = state in {"disabled", "disable", "off", "false", "no", "inactive"}
    if not enable and not disable:
        return {
            "changed": False,
            "error": "state must be enabled or disabled.",
        }
    if not bool(arguments.get("confirm")):
        return {
            "changed": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Maintenance Mode",
            "state": "enabled" if enable else "disabled",
            "detail": (
                "Enable Maintenance Mode and stop automated access actions?"
                if enable
                else "Disable Maintenance Mode and resume automated access actions?"
            ),
        }
    if enable:
        result = await enable_maintenance_mode(
            {"reason": arguments.get("reason") or "Enabled by Alfred", "confirm": True}
        )
        return {"changed": bool(result.get("enabled")), "state": "enabled", **result}
    result = await disable_maintenance_mode({"confirm": True})
    return {"changed": bool(result.get("disabled")), "state": "disabled", **result}


async def override_schedule(arguments: dict[str, Any]) -> dict[str, Any]:
    person_id = _uuid_from_value(arguments.get("person_id"))
    if not person_id:
        return {
            "created": False,
            "requires_details": True,
            "detail": "A person_id from actor context or resolve_human_entity is required.",
        }
    config = await get_runtime_config()
    try:
        starts_at = _parse_agent_datetime(arguments.get("time"), config.site_timezone)
    except (TypeError, ValueError) as exc:
        return {"created": False, "error": f"Invalid override time: {exc}"}
    duration_minutes = _bounded_int(arguments.get("duration_minutes"), default=60, minimum=1, maximum=1440)
    ends_at = starts_at + timedelta(minutes=duration_minutes)
    reason = str(arguments.get("reason") or "Temporary access override from Alfred").strip()

    async with AsyncSessionLocal() as session:
        person = await _load_person_with_schedule(session, person_id)
        if not person:
            return {"created": False, "error": "Person not found."}
        if not bool(arguments.get("confirm")):
            return {
                "created": False,
                "requires_confirmation": True,
                "confirmation_field": "confirm",
                "target": person.display_name,
                "person_id": str(person.id),
                "starts_at": _agent_datetime_iso(starts_at, config.site_timezone),
                "starts_at_display": _agent_datetime_display(starts_at, config.site_timezone),
                "ends_at": _agent_datetime_iso(ends_at, config.site_timezone),
                "ends_at_display": _agent_datetime_display(ends_at, config.site_timezone),
                "duration_minutes": duration_minutes,
                "detail": f"Create a temporary access override for {person.display_name}?",
            }

        context = get_chat_tool_context()
        override = ScheduleOverride(
            person_id=person.id,
            starts_at=starts_at,
            ends_at=ends_at,
            reason=reason,
            created_by_user_id=_uuid_from_value(context.get("user_id")),
            source="alfred",
            is_active=True,
        )
        session.add(override)
        await session.commit()
        await session.refresh(override)

    await event_bus.publish(
        "schedule.override_created",
        {
            "override_id": str(override.id),
            "person_id": str(person.id),
            "person": person.display_name,
            "starts_at": _agent_datetime_iso(starts_at, config.site_timezone),
            "ends_at": _agent_datetime_iso(ends_at, config.site_timezone),
            "source": "alfred",
        },
    )
    return {
        "created": True,
        "override_id": str(override.id),
        "person_id": str(person.id),
        "person": person.display_name,
        "starts_at": _agent_datetime_iso(starts_at, config.site_timezone),
        "starts_at_display": _agent_datetime_display(starts_at, config.site_timezone),
        "ends_at": _agent_datetime_iso(ends_at, config.site_timezone),
        "ends_at_display": _agent_datetime_display(ends_at, config.site_timezone),
        "duration_minutes": duration_minutes,
        "reason": reason,
    }


async def query_visitor_passes(arguments: dict[str, Any]) -> dict[str, Any]:
    config = await get_runtime_config()
    limit = _bounded_int(arguments.get("limit"), default=20, minimum=1, maximum=100)
    statuses = _visitor_pass_statuses_from_arguments(arguments)
    search = str(arguments.get("search") or arguments.get("visitor_name") or "").strip() or None
    fuzzy_name = bool(arguments.get("fuzzy_name"))
    service = get_visitor_pass_service()
    async with AsyncSessionLocal() as session:
        changed = await service.refresh_statuses(session=session, publish=False)
        if changed:
            await session.commit()
        passes = await service.list_passes(session, statuses=statuses, search=search, limit=limit)
        if fuzzy_name and search:
            broad_limit = max(limit, 100)
            candidates = await service.list_passes(session, statuses=statuses, search=None, limit=broad_limit)
            merged: list[VisitorPass] = []
            seen: set[str] = set()
            for pass_ in [*passes, *_fuzzy_visitor_pass_name_matches(search, candidates)]:
                key = str(pass_.id)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(pass_)
                if len(merged) >= limit:
                    break
            passes = merged
        records = [_visitor_pass_agent_payload(pass_, config.site_timezone) for pass_ in passes]
    return {
        "visitor_passes": records,
        "count": len(records),
        "timezone": config.site_timezone,
        "filters": {
            "statuses": [status.value for status in statuses] if statuses else None,
            "search": search,
            "fuzzy_name": fuzzy_name or None,
        },
    }


def _fuzzy_visitor_pass_name_matches(search: str, candidates: list[VisitorPass]) -> list[VisitorPass]:
    needle = _normalize_name_for_similarity(search)
    if not needle:
        return []
    scored: list[tuple[float, VisitorPass]] = []
    for pass_ in candidates:
        candidate = _normalize_name_for_similarity(pass_.visitor_name)
        if not candidate:
            continue
        score = SequenceMatcher(None, needle, candidate).ratio()
        if needle in candidate or candidate in needle:
            score = max(score, 0.9)
        if score >= 0.72:
            scored.append((score, pass_))
    scored.sort(key=lambda item: (item[0], item[1].expected_time), reverse=True)
    return [pass_ for _score, pass_ in scored]


def _normalize_name_for_similarity(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


async def get_visitor_pass(arguments: dict[str, Any]) -> dict[str, Any]:
    config = await get_runtime_config()
    async with AsyncSessionLocal() as session:
        resolved = await _resolve_visitor_pass_for_agent(session, arguments)
        if isinstance(resolved, dict):
            return resolved
        return {
            "found": True,
            "visitor_pass": _visitor_pass_agent_payload(resolved, config.site_timezone),
            "timezone": config.site_timezone,
        }


async def create_visitor_pass(arguments: dict[str, Any]) -> dict[str, Any]:
    visitor_name = str(arguments.get("visitor_name") or "").strip()
    expected_value = arguments.get("expected_time")
    pass_type = _visitor_pass_type_from_arguments(arguments.get("pass_type"))
    visitor_phone = str(arguments.get("visitor_phone") or "").strip()
    number_plate = normalize_registration_number(arguments.get("number_plate")) or None
    valid_from_value = arguments.get("valid_from")
    valid_until_value = arguments.get("valid_until")
    missing = []
    if not visitor_name:
        missing.append("visitor_name")
    if pass_type == VisitorPassType.DURATION:
        if not valid_from_value:
            missing.append("valid_from")
        if not valid_until_value:
            missing.append("valid_until")
    elif not expected_value:
        missing.append("expected_time")
    if missing:
        return {
            "created": False,
            "requires_details": True,
            "missing": missing,
            "detail": (
                "I need the visitor name and start/end times before I can prepare a duration Visitor Pass."
                if pass_type == VisitorPassType.DURATION
                else "I need the visitor name and expected time before I can prepare a Visitor Pass."
            ),
        }

    config = await get_runtime_config()
    try:
        if pass_type == VisitorPassType.DURATION:
            valid_from = _parse_agent_datetime(valid_from_value, config.site_timezone)
            valid_until = _parse_agent_datetime(valid_until_value, config.site_timezone)
            expected_time = _parse_agent_datetime(expected_value, config.site_timezone) if expected_value else valid_from
        else:
            valid_from = None
            valid_until = None
            expected_time = _parse_agent_datetime(expected_value, config.site_timezone)
    except (TypeError, ValueError) as exc:
        return {"created": False, "error": f"Invalid visitor pass time: {exc}"}
    window_minutes = _bounded_int(
        arguments.get("window_minutes"),
        default=DEFAULT_WINDOW_MINUTES,
        minimum=1,
        maximum=1440,
    )
    ends_at = valid_until if pass_type == VisitorPassType.DURATION else expected_time + timedelta(minutes=window_minutes)
    if ends_at < _agent_now(config.site_timezone):
        return {
            "created": False,
            "error": "That Visitor Pass window has already elapsed.",
            "expected_time": _agent_datetime_iso(expected_time, config.site_timezone),
        }

    starts_at = valid_from if pass_type == VisitorPassType.DURATION else expected_time - timedelta(minutes=window_minutes)
    if not bool(arguments.get("confirm")):
        return {
            "created": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": visitor_name,
            "visitor_name": visitor_name,
            "pass_type": pass_type.value,
            "visitor_phone": visitor_phone or None,
            "number_plate": number_plate,
            "expected_time": _agent_datetime_iso(expected_time, config.site_timezone),
            "expected_time_display": _agent_datetime_display(expected_time, config.site_timezone),
            "window_minutes": window_minutes,
            "window_start": _agent_datetime_iso(starts_at, config.site_timezone),
            "window_end": _agent_datetime_iso(ends_at, config.site_timezone),
            "detail": (
                f"Create a duration Visitor Pass for {visitor_name} from "
                f"{_agent_datetime_display(starts_at, config.site_timezone)} to "
                f"{_agent_datetime_display(ends_at, config.site_timezone)} and message them on WhatsApp?"
                if pass_type == VisitorPassType.DURATION
                else f"Create a Visitor Pass for {visitor_name} at "
                f"{_agent_datetime_display(expected_time, config.site_timezone)} "
                f"with a +/- {window_minutes} minute window?"
            ),
        }

    service = get_visitor_pass_service()
    context = get_chat_tool_context()
    async with AsyncSessionLocal() as session:
        try:
            visitor_pass = await service.create_pass(
                session,
                visitor_name=visitor_name,
                expected_time=expected_time,
                window_minutes=window_minutes,
                pass_type=pass_type,
                visitor_phone=visitor_phone or None,
                number_plate=number_plate,
                valid_from=valid_from,
                valid_until=valid_until,
                source="alfred",
                created_by_user_id=_uuid_from_value(context.get("user_id")),
                actor="Alfred_AI",
            )
            await session.commit()
            await session.refresh(visitor_pass)
        except VisitorPassError as exc:
            await session.rollback()
            return {"created": False, "error": str(exc)}
        payload = _visitor_pass_agent_payload(visitor_pass, config.site_timezone)

    await event_bus.publish("visitor_pass.created", {"visitor_pass": payload, "source": "alfred"})
    if pass_type == VisitorPassType.DURATION and payload.get("visitor_phone"):
        try:
            await get_whatsapp_messaging_service().send_visitor_pass_outreach(visitor_pass)
        except Exception as exc:
            logger.warning(
                "alfred_visitor_pass_whatsapp_outreach_failed",
                extra={"visitor_pass_id": payload["id"], "error": str(exc)[:240]},
            )
    return {
        "created": True,
        "visitor_pass": payload,
        "visitor_pass_id": payload["id"],
        "visitor_name": payload["visitor_name"],
        "expected_time_display": payload["expected_time_display"],
    }


async def update_visitor_pass(arguments: dict[str, Any]) -> dict[str, Any]:
    config = await get_runtime_config()
    expected_time: datetime | None = None
    if arguments.get("expected_time"):
        try:
            expected_time = _parse_agent_datetime(arguments.get("expected_time"), config.site_timezone)
        except (TypeError, ValueError) as exc:
            return {"updated": False, "error": f"Invalid visitor expected time: {exc}"}
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    if arguments.get("valid_from"):
        try:
            valid_from = _parse_agent_datetime(arguments.get("valid_from"), config.site_timezone)
        except (TypeError, ValueError) as exc:
            return {"updated": False, "error": f"Invalid Visitor Pass start time: {exc}"}
    if arguments.get("valid_until"):
        try:
            valid_until = _parse_agent_datetime(arguments.get("valid_until"), config.site_timezone)
        except (TypeError, ValueError) as exc:
            return {"updated": False, "error": f"Invalid Visitor Pass end time: {exc}"}
    window_minutes = (
        _bounded_int(arguments.get("window_minutes"), default=DEFAULT_WINDOW_MINUTES, minimum=1, maximum=1440)
        if arguments.get("window_minutes") is not None
        else None
    )
    pass_type = _visitor_pass_type_from_arguments(arguments.get("pass_type")) if arguments.get("pass_type") else None
    visitor_phone = str(arguments.get("visitor_phone") or "").strip() or None
    replacement_name = str(arguments.get("new_visitor_name") or arguments.get("replacement_visitor_name") or "").strip() or None
    if arguments.get("pass_id") and arguments.get("visitor_name"):
        replacement_name = str(arguments.get("visitor_name") or "").strip()
    if not any([replacement_name, expected_time, window_minutes is not None, pass_type, visitor_phone, valid_from, valid_until]):
        return {
            "updated": False,
            "requires_details": True,
            "detail": "Tell me which Visitor Pass field to change: name, type, phone, expected time, or time window.",
        }

    service = get_visitor_pass_service()
    context = get_chat_tool_context()
    async with AsyncSessionLocal() as session:
        resolved = await _resolve_visitor_pass_for_agent(session, arguments, editable_only=True)
        if isinstance(resolved, dict):
            return resolved
        visitor_pass = resolved
        if not bool(arguments.get("confirm")):
            return {
                "updated": False,
                "requires_confirmation": True,
                "confirmation_field": "confirm",
                "target": visitor_pass.visitor_name,
                "visitor_pass_id": str(visitor_pass.id),
                "visitor_name": replacement_name or visitor_pass.visitor_name,
                "pass_type": (pass_type or visitor_pass.pass_type).value,
                "visitor_phone": visitor_phone or visitor_pass.visitor_phone,
                "expected_time": (
                    _agent_datetime_iso(expected_time, config.site_timezone)
                    if expected_time
                    else _agent_datetime_iso(visitor_pass.expected_time, config.site_timezone)
                ),
                "expected_time_display": (
                    _agent_datetime_display(expected_time, config.site_timezone)
                    if expected_time
                    else _agent_datetime_display(visitor_pass.expected_time, config.site_timezone)
                ),
                "window_minutes": window_minutes or visitor_pass.window_minutes,
                "detail": f"Update the Visitor Pass for {visitor_pass.visitor_name}?",
            }
        try:
            await service.update_pass(
                session,
                visitor_pass,
                visitor_name=replacement_name,
                expected_time=expected_time,
                window_minutes=window_minutes,
                pass_type=pass_type,
                visitor_phone=visitor_phone,
                valid_from=valid_from,
                valid_until=valid_until,
                actor="Alfred_AI",
                actor_user_id=_uuid_from_value(context.get("user_id")),
            )
            await session.commit()
            await session.refresh(visitor_pass)
        except VisitorPassError as exc:
            await session.rollback()
            return {"updated": False, "error": str(exc)}
        payload = _visitor_pass_agent_payload(visitor_pass, config.site_timezone)

    await event_bus.publish("visitor_pass.updated", {"visitor_pass": payload, "source": "alfred"})
    return {"updated": True, "visitor_pass": payload, "visitor_pass_id": payload["id"]}


async def cancel_visitor_pass(arguments: dict[str, Any]) -> dict[str, Any]:
    config = await get_runtime_config()
    service = get_visitor_pass_service()
    context = get_chat_tool_context()
    async with AsyncSessionLocal() as session:
        resolved = await _resolve_visitor_pass_for_agent(session, arguments, editable_only=True)
        if isinstance(resolved, dict):
            return resolved
        visitor_pass = resolved
        if not bool(arguments.get("confirm")):
            return {
                "cancelled": False,
                "requires_confirmation": True,
                "confirmation_field": "confirm",
                "target": visitor_pass.visitor_name,
                "visitor_pass_id": str(visitor_pass.id),
                "visitor_name": visitor_pass.visitor_name,
                "expected_time_display": _agent_datetime_display(visitor_pass.expected_time, config.site_timezone),
                "detail": f"Cancel the Visitor Pass for {visitor_pass.visitor_name}?",
            }
        try:
            await service.cancel_pass(
                session,
                visitor_pass,
                actor="Alfred_AI",
                actor_user_id=_uuid_from_value(context.get("user_id")),
                reason=str(arguments.get("reason") or "Cancelled by Alfred").strip(),
            )
            await session.commit()
            await session.refresh(visitor_pass)
        except VisitorPassError as exc:
            await session.rollback()
            return {"cancelled": False, "error": str(exc)}
        payload = _visitor_pass_agent_payload(visitor_pass, config.site_timezone)

    await event_bus.publish("visitor_pass.cancelled", {"visitor_pass": payload, "source": "alfred"})
    return {"cancelled": True, "visitor_pass": payload, "visitor_pass_id": payload["id"]}


async def trigger_icloud_sync(arguments: dict[str, Any]) -> dict[str, Any]:
    if not bool(arguments.get("confirm")):
        return {
            "synced": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "iCloud Calendar",
            "detail": "Sync connected iCloud Calendars and create or update Visitor Passes for Open Gate events?",
        }
    context = get_chat_tool_context()
    service = get_icloud_calendar_service()
    try:
        result = await service.sync_all(
            trigger_source="alfred",
            triggered_by_user_id=_uuid_from_value(context.get("user_id")),
            actor="Alfred_AI",
        )
    except ICloudCalendarError as exc:
        return {"synced": False, "error": str(exc)}
    return {
        "synced": True,
        "sync": result,
        "account_count": result.get("account_count", 0),
        "events_scanned": result.get("events_scanned", 0),
        "events_matched": result.get("events_matched", 0),
        "passes_created": result.get("passes_created", 0),
        "passes_updated": result.get("passes_updated", 0),
        "passes_cancelled": result.get("passes_cancelled", 0),
        "passes_skipped": result.get("passes_skipped", 0),
        "account_results": result.get("account_results", []),
    }


async def query_access_events(arguments: dict[str, Any]) -> dict[str, Any]:
    limit = _bounded_int(arguments.get("limit"), default=10, minimum=1, maximum=100)
    summarize_payload = arguments.get("summarize_payload")
    summarize_payload = True if summarize_payload is None else bool(summarize_payload)
    config = await get_runtime_config()
    start, end = _period_bounds(arguments.get("day") or "recent", config.site_timezone)

    async with AsyncSessionLocal() as session:
        query = (
            select(AccessEvent)
            .options(
                selectinload(AccessEvent.vehicle).selectinload(Vehicle.owner),
                selectinload(AccessEvent.anomalies),
            )
            .where(AccessEvent.occurred_at >= start, AccessEvent.occurred_at <= end)
            .order_by(AccessEvent.occurred_at.desc())
            .limit(limit)
        )
        person_id_filter = _uuid_from_value(arguments.get("person_id"))
        if person_id_filter:
            query = query.where(AccessEvent.person_id == person_id_filter)
        vehicle_id_filter = _uuid_from_value(arguments.get("vehicle_id"))
        if vehicle_id_filter:
            query = query.where(AccessEvent.vehicle_id == vehicle_id_filter)
        events = (await session.scalars(query)).all()

        person_filter = _normalize(arguments.get("person"))
        group_filter = _normalize(arguments.get("group"))
        person_map = await _person_map(session)

    records = []
    for event in events:
        person = person_map.get(str(event.person_id)) if event.person_id else None
        if person_filter and (not person or not _person_record_matches(person, person_filter)):
            continue
        if group_filter and (not person or group_filter not in person.get("group", "").lower()):
            continue
        plate_filter = _normalize(arguments.get("registration_number"))
        if plate_filter and plate_filter not in event.registration_number.lower():
            continue
        raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
        schedule_payload = raw_payload.get("schedule") if isinstance(raw_payload.get("schedule"), dict) else None
        records.append(
            _compact_observation(
                {
                    "id": str(event.id),
                    "person_id": str(event.person_id) if event.person_id else None,
                    "vehicle_id": str(event.vehicle_id) if event.vehicle_id else None,
                    "person": person["display_name"] if person else None,
                    "group": person.get("group") if person else None,
                    "vehicle": _vehicle_agent_payload(event.vehicle),
                    "registration_number": event.registration_number,
                    "direction": event.direction.value,
                    "decision": event.decision.value,
                    "confidence": event.confidence,
                    "source": event.source,
                    "occurred_at": _agent_datetime_iso(event.occurred_at, config.site_timezone),
                    "occurred_at_display": _agent_datetime_display(event.occurred_at, config.site_timezone),
                    "timing_classification": event.timing_classification.value,
                    "anomaly_count": len(event.anomalies),
                    "schedule_summary": _payload_summary(schedule_payload) if summarize_payload else schedule_payload,
                    "gate_observation": _gate_observation_from_event(event),
                    "payload_summary": _payload_summary(raw_payload) if summarize_payload else None,
                    "raw_payload": raw_payload if not summarize_payload else None,
                }
            )
        )

    return {"events": records, "count": len(records), "timezone": config.site_timezone}


async def diagnose_access_event(arguments: dict[str, Any]) -> dict[str, Any]:
    config = await get_runtime_config()
    span_limit = _bounded_int(arguments.get("span_limit"), default=20, minimum=1, maximum=50)
    include_trace_payloads = bool(arguments.get("include_trace_payloads"))
    summarize_payload = arguments.get("summarize_payload")
    summarize_payload = True if summarize_payload is None else bool(summarize_payload)
    await telemetry.flush()
    async with AsyncSessionLocal() as session:
        person_map = await _person_map(session)
        event = await _resolve_access_event_for_diagnostics(session, arguments, person_map, config.site_timezone)
        if not event:
            return {
                "found": False,
                "error": "No matching access event was found.",
                "timezone": config.site_timezone,
            }

        person = person_map.get(str(event.person_id)) if event.person_id else None
        trace, spans = await _telemetry_for_access_event(session, event)
        history = await _registration_history_summary(
            session,
            event.registration_number,
            timezone_name=config.site_timezone,
            period="all",
            limit=8,
        )
        notifications = await _notification_diagnostics_for_event(
            session,
            event,
            person,
            trace.trace_id if trace else _trace_id_from_access_event(event),
            spans,
            config.site_timezone,
        )

    lpr_timing = await _lpr_timing_near_event(event, config.site_timezone)
    recognition = _recognition_diagnostics(event, trace, spans)
    gate = _gate_diagnostics(event, spans, config.site_timezone)
    maintenance = await get_maintenance_mode_status()

    return _compact_observation({
        "found": True,
        "timezone": config.site_timezone,
        "event": _access_event_diagnostic_payload(
            event,
            person,
            config.site_timezone,
            summarize_payload=summarize_payload,
        ),
        "recognition": recognition,
        "gate": gate,
        "maintenance_mode": maintenance,
        "notifications": notifications,
        "history": history,
        "lpr_timing_observations": lpr_timing,
        "trace": _trace_diagnostic_payload(
            trace,
            spans,
            config.site_timezone,
            span_limit=span_limit,
            include_payloads=include_trace_payloads,
            summarize_payload=summarize_payload,
        ),
        "answer_hints": _diagnostic_answer_hints(recognition, gate, notifications),
    })


async def investigate_access_incident(arguments: dict[str, Any]) -> dict[str, Any]:
    config = await get_runtime_config()
    incident_type = _normalize(arguments.get("incident_type") or "auto") or "auto"
    start, end, expected_at = _incident_window(arguments, config.site_timezone)
    direction_filter = _normalize(arguments.get("direction"))
    if direction_filter not in {"entry", "exit", "denied"}:
        direction_filter = ""

    async with AsyncSessionLocal() as session:
        subject = await _resolve_incident_subject(session, arguments)
        plates = _incident_candidate_plates(subject, arguments)
        iacs_events = await _incident_iacs_events(
            session,
            subject=subject,
            plates=plates,
            start=start,
            end=end,
            direction=direction_filter,
        )
        traces = await _incident_telemetry_traces(
            session,
            start=start,
            end=end,
            plates=plates,
            timezone_name=config.site_timezone,
        )
        audit_logs = await _incident_audit_logs(session, start=start, end=end)
        gate_observations = await _incident_gate_observations(session, start=start, end=end, timezone_name=config.site_timezone)
        anomalies = await _incident_anomalies(session, start=start, end=end, plates=plates, timezone_name=config.site_timezone)
        schedules = await _incident_schedule_diagnostics(
            session,
            subject=subject,
            checked_at=(expected_at or start + ((end - start) / 2)).astimezone(UTC),
            timezone_name=config.site_timezone,
            default_policy=config.schedule_default_policy,
        )
        notification_summary = await _incident_notification_summary(session, incident_type=incident_type)

    diagnostic = None
    if iacs_events:
        diagnostic = await diagnose_access_event(
            {
                "access_event_id": iacs_events[0]["id"],
                "span_limit": 20,
                "summarize_payload": True,
            }
        )

    protect = await _query_protect_events_for_incident(
        start=start,
        end=end,
        timezone_name=config.site_timezone,
        plates=plates,
        camera_id=str(arguments.get("camera_id") or "").strip() or None,
        camera_name=str(arguments.get("camera_name") or "").strip() or None,
        smart_detect_type=str(arguments.get("smart_detect_type") or "").strip() or None,
        include_tracks=True,
    )
    root = _incident_root_cause(
        found_iacs=bool(iacs_events),
        protect=protect,
        traces=traces,
        incident_type=incident_type,
    )
    backfill_args = _backfill_args_from_incident(
        subject=subject,
        protect=protect,
        arguments=arguments,
        root_cause=str(root.get("root_cause") or ""),
    )

    if bool(arguments.get("confirm")) and backfill_args:
        confirmed_args = {**backfill_args, "confirm": True}
        return await backfill_access_event_from_protect(confirmed_args)

    timeline = _incident_timeline(
        iacs_events=iacs_events,
        protect=protect,
        traces=traces,
        gate_observations=gate_observations,
        audit_logs=audit_logs,
        anomalies=anomalies,
        timezone_name=config.site_timezone,
    )
    result = {
        "found_iacs_event": bool(iacs_events),
        "found_protect_event": bool(protect.get("matched_event") or protect.get("events")),
        "root_cause": root.get("root_cause"),
        "confidence": root.get("confidence"),
        "timeline": timeline,
        "subject": subject.get("summary"),
        "window": {
            "start": _agent_datetime_iso(start, config.site_timezone),
            "end": _agent_datetime_iso(end, config.site_timezone),
            "expected_time": _agent_datetime_iso(expected_at, config.site_timezone) if expected_at else None,
            "timezone": config.site_timezone,
        },
        "iacs": {
            "events": iacs_events,
            "event_count": len(iacs_events),
            "telemetry_traces": traces,
            "gate_observations": gate_observations,
            "anomalies": anomalies,
            "audit_logs": audit_logs,
            "schedules": schedules,
            "notifications": notification_summary,
            "diagnostic": diagnostic,
        },
        "protect": protect,
        "iacs_vs_protect": _iacs_vs_protect_summary(bool(iacs_events), protect, traces),
        "recommended_action": _incident_recommended_action(root, bool(backfill_args), protect),
        "requires_confirmation": bool(backfill_args),
        "confirmation_field": "confirm" if backfill_args else None,
        "backfill_arguments": backfill_args,
    }
    if backfill_args:
        result["target"] = subject.get("summary", {}).get("label") or backfill_args.get("registration_number") or "missing access event"
        result["detail"] = (
            "I found durable UniFi Protect LPR evidence without a matching IACS access event. "
            "Confirm to backfill the access event and update presence only; no gate, garage, or normal arrival notifications will be fired."
        )
    return _compact_observation(result)


async def query_unifi_protect_events(arguments: dict[str, Any]) -> dict[str, Any]:
    config = await get_runtime_config()
    limit = _bounded_int(arguments.get("limit"), default=25, minimum=1, maximum=100)
    if arguments.get("start") or arguments.get("end"):
        start = _parse_incident_datetime(arguments.get("start"), config.site_timezone, str(arguments.get("day") or "today"))
        end = _parse_incident_datetime(arguments.get("end"), config.site_timezone, str(arguments.get("day") or "today"))
        if not start or not end:
            return {"available": False, "error": "start and end must be ISO datetimes or local times."}
        start_utc = start.astimezone(UTC)
        end_utc = end.astimezone(UTC)
    else:
        start_utc, end_utc, _ = _incident_window(arguments, config.site_timezone)

    plates = []
    registration_number = normalize_registration_number(str(arguments.get("registration_number") or ""))
    if registration_number:
        plates.append(registration_number)
    result = await _query_protect_events_for_incident(
        start=start_utc,
        end=end_utc,
        timezone_name=config.site_timezone,
        plates=plates,
        camera_id=str(arguments.get("camera_id") or "").strip() or None,
        camera_name=str(arguments.get("camera_name") or "").strip() or None,
        smart_detect_type=str(arguments.get("smart_detect_type") or "").strip() or None,
        include_tracks=bool(arguments.get("include_tracks") or registration_number),
        limit=limit,
    )
    return _compact_observation(result)


async def backfill_access_event_from_protect(arguments: dict[str, Any]) -> dict[str, Any]:
    context = get_chat_tool_context()
    if str(context.get("user_role") or "").lower() != "admin":
        return {"backfilled": False, "error": "Admin access is required to backfill access events."}

    config = await get_runtime_config()
    async with AsyncSessionLocal() as session:
        candidate = await _backfill_candidate(session, arguments, config.site_timezone)
        if isinstance(candidate, dict) and candidate.get("error"):
            return {"backfilled": False, **candidate}
        if not bool(arguments.get("confirm")):
            return {
                "backfilled": False,
                "requires_confirmation": True,
                "confirmation_field": "confirm",
                "target": candidate.get("label") or candidate.get("registration_number") or "access event",
                "detail": (
                    f"Backfill {candidate.get('direction')} {candidate.get('decision')} event for "
                    f"{candidate.get('label') or candidate.get('registration_number')} at "
                    f"{_agent_datetime_display(candidate['captured_at'], config.site_timezone)}? "
                    "This updates IACS event history and presence only."
                ),
                "candidate": _backfill_candidate_payload(candidate, config.site_timezone),
            }

        duplicate = await session.scalar(
            select(AccessEvent)
            .where(
                AccessEvent.registration_number == candidate["registration_number"],
                AccessEvent.occurred_at >= candidate["captured_at"] - timedelta(seconds=60),
                AccessEvent.occurred_at <= candidate["captured_at"] + timedelta(seconds=60),
            )
            .order_by(AccessEvent.occurred_at.desc())
            .limit(1)
        )
        if duplicate:
            return {
                "backfilled": False,
                "already_exists": True,
                "access_event_id": str(duplicate.id),
                "detail": "A matching IACS access event already exists within 60 seconds, so I did not create a duplicate.",
            }

        trace = telemetry.start_trace(
            "Alfred Access Event Backfill",
            category=TELEMETRY_CATEGORY_ALFRED,
            actor="Alfred_AI",
            source=candidate.get("source"),
            registration_number=candidate["registration_number"],
            context={
                "protect_event_id": candidate.get("protect_event_id"),
                "reason": candidate.get("reason"),
                "direction": candidate["direction"],
                "decision": candidate["decision"],
            },
        )
        trace.record_span(
            "Protect evidence selected",
            started_at=datetime.now(tz=UTC),
            category=TELEMETRY_CATEGORY_ALFRED,
            attributes={"source": candidate.get("source"), "protect_event_id": candidate.get("protect_event_id")},
            output_payload=_backfill_candidate_payload(candidate, config.site_timezone),
        )
        event = AccessEvent(
            vehicle_id=candidate.get("vehicle_id"),
            person_id=candidate.get("person_id"),
            registration_number=candidate["registration_number"],
            direction=AccessDirection(candidate["direction"]),
            decision=AccessDecision(candidate["decision"]),
            confidence=candidate["confidence"],
            source=candidate.get("source") or "alfred_backfill",
            occurred_at=candidate["captured_at"],
            timing_classification=TimingClassification.UNKNOWN,
            raw_payload={
                "backfill": {
                    "source": candidate.get("source"),
                    "reason": candidate.get("reason"),
                    "created_by": "Alfred_AI",
                    "created_by_user_id": str(context.get("user_id") or "") or None,
                },
                "protect_evidence": {
                    "event_id": candidate.get("protect_event_id"),
                    "camera_id": candidate.get("camera_id"),
                    "camera_name": candidate.get("camera_name"),
                    "captured_at": candidate["captured_at"].isoformat(),
                    "confidence": candidate["confidence"],
                    "track_candidate": candidate.get("track_candidate"),
                },
                "direction_resolution": {
                    "source": "alfred_backfill",
                    "gate_observation": candidate.get("gate_observation"),
                },
                "telemetry": {"trace_id": trace.trace_id},
            },
        )
        session.add(event)
        await session.flush()

        presence_updated = False
        if event.decision == AccessDecision.GRANTED and event.person_id and event.direction in {AccessDirection.ENTRY, AccessDirection.EXIT}:
            presence = await session.get(Presence, event.person_id)
            if not presence:
                presence = Presence(person_id=event.person_id)
                session.add(presence)
            presence.state = PresenceState.PRESENT if event.direction == AccessDirection.ENTRY else PresenceState.EXITED
            presence.last_event_id = event.id
            presence.last_changed_at = event.occurred_at
            presence_updated = True

        await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_ALFRED,
            action="access_event.backfilled",
            actor="Alfred_AI",
            actor_user_id=context.get("user_id"),
            target_entity="AccessEvent",
            target_id=event.id,
            target_label=event.registration_number,
            metadata={
                "protect_event_id": candidate.get("protect_event_id"),
                "camera_name": candidate.get("camera_name"),
                "direction": event.direction.value,
                "decision": event.decision.value,
                "presence_updated": presence_updated,
                "reason": candidate.get("reason"),
            },
            trace_id=trace.trace_id,
        )
        await session.commit()
        await session.refresh(event)

    trace.finish(
        status="ok",
        summary=f"Backfilled {event.direction.value} for plate {event.registration_number}",
        access_event_id=event.id,
        context={"event_id": str(event.id), "presence_updated": presence_updated},
    )
    await telemetry.flush()
    await event_bus.publish(
        "access_event.finalized",
        {
            "event_id": str(event.id),
            "access_event_id": str(event.id),
            "person_id": str(event.person_id) if event.person_id else None,
            "vehicle_id": str(event.vehicle_id) if event.vehicle_id else None,
            "registration_number": event.registration_number,
            "direction": event.direction.value,
            "decision": event.decision.value,
            "confidence": event.confidence,
            "source": event.source,
            "occurred_at": event.occurred_at.isoformat(),
            "event_type": "access_event.finalized",
            "timing_classification": event.timing_classification.value,
            "anomaly_count": 0,
            "backfilled": True,
        },
    )
    return {
        "backfilled": True,
        "access_event_id": str(event.id),
        "registration_number": event.registration_number,
        "direction": event.direction.value,
        "decision": event.decision.value,
        "occurred_at": _agent_datetime_iso(event.occurred_at, config.site_timezone),
        "occurred_at_display": _agent_datetime_display(event.occurred_at, config.site_timezone),
        "presence_updated": presence_updated,
        "telemetry_trace_id": trace.trace_id,
    }


async def test_unifi_alarm_webhook(arguments: dict[str, Any]) -> dict[str, Any]:
    trigger_id = str(arguments.get("trigger_id") or "").strip()
    if not trigger_id:
        return {"sent": False, "error": "trigger_id is required."}
    if not bool(arguments.get("confirm")):
        return {
            "sent": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": trigger_id,
            "detail": "Send a UniFi Protect Alarm Manager webhook test for this trigger?",
        }

    before = datetime.now(tz=UTC)
    try:
        sent = await get_unifi_protect_service().send_alarm_webhook_test(trigger_id)
    except UnifiProtectError as exc:
        return {"sent": False, "error": str(exc)}

    await asyncio.sleep(1)
    await telemetry.flush()
    async with AsyncSessionLocal() as session:
        traces = (
            await session.scalars(
                select(TelemetryTrace)
                .where(
                    TelemetryTrace.category == TELEMETRY_CATEGORY_WEBHOOKS_API,
                    TelemetryTrace.started_at >= before,
                )
                .order_by(TelemetryTrace.started_at.desc())
                .limit(5)
            )
        ).all()
    return {
        "sent": True,
        "trigger_id": trigger_id,
        "protect_result": _payload_summary(sent),
        "verified_iacs_webhook_trace": bool(traces),
        "recent_webhook_traces": [_incident_trace_payload(trace, DEFAULT_AGENT_TIMEZONE) for trace in traces],
    }


async def query_lpr_timing(arguments: dict[str, Any]) -> dict[str, Any]:
    limit = _bounded_int(arguments.get("limit"), default=50, minimum=1, maximum=200)
    config = await get_runtime_config()
    plate_filter = normalize_registration_number(str(arguments.get("registration_number") or ""))
    source_filter = _normalize(arguments.get("source"))
    include_possible_fields = bool(arguments.get("include_possible_fields"))
    include_payload_path = bool(arguments.get("include_payload_path"))
    raw_observations = await get_lpr_timing_recorder().recent(limit=max(limit, 200))

    observations: list[dict[str, Any]] = []
    for observation in raw_observations:
        if not include_possible_fields and observation.get("candidate_kind") == "possible_lpr_field":
            continue
        registration_number = normalize_registration_number(
            str(observation.get("registration_number") or observation.get("raw_value") or "")
        )
        if plate_filter and plate_filter not in registration_number:
            continue
        source_text = f"{observation.get('source') or ''} {observation.get('source_detail') or ''}".lower()
        if source_filter and source_filter not in source_text:
            continue
        observations.append(
            _serialize_lpr_timing_observation(
                observation,
                config.site_timezone,
                include_payload_path=include_payload_path,
            )
        )
        if len(observations) >= limit:
            break

    slowest = sorted(
        [row for row in observations if row.get("captured_to_received_ms") is not None],
        key=lambda row: float(row["captured_to_received_ms"]),
        reverse=True,
    )[:5]
    return {
        "observations": observations,
        "count": len(observations),
        "timezone": config.site_timezone,
        "filters": {
            "registration_number": plate_filter or None,
            "source": source_filter or None,
            "include_possible_fields": include_possible_fields,
        },
        "slowest_observations": slowest,
        "latest_observation": observations[0] if observations else None,
    }


async def query_vehicle_detection_history(arguments: dict[str, Any]) -> dict[str, Any]:
    config = await get_runtime_config()
    period = str(arguments.get("period") or "all")
    if period not in {"all", "today", "yesterday", "recent"}:
        period = "all"
    limit = _bounded_int(arguments.get("limit"), default=10, minimum=1, maximum=50)
    latest_unknown = bool(arguments.get("latest_unknown"))
    registration_number = normalize_registration_number(str(arguments.get("registration_number") or ""))

    async with AsyncSessionLocal() as session:
        if not registration_number:
            query = select(AccessEvent).order_by(AccessEvent.occurred_at.desc())
            if latest_unknown:
                query = query.where(AccessEvent.vehicle_id.is_(None))
            latest = await session.scalar(query.limit(1))
            if not latest:
                return {
                    "found": False,
                    "error": "No access events were found.",
                    "timezone": config.site_timezone,
                }
            registration_number = latest.registration_number
            latest_unknown = latest.vehicle_id is None

        history = await _registration_history_summary(
            session,
            registration_number,
            timezone_name=config.site_timezone,
            period=period,
            limit=limit,
        )

    return {
        "found": bool(history.get("total_count")),
        "registration_number": registration_number,
        "resolved_from_latest_unknown": latest_unknown and not arguments.get("registration_number"),
        "period": period,
        "timezone": config.site_timezone,
        **history,
    }


async def get_telemetry_trace(arguments: dict[str, Any]) -> dict[str, Any]:
    limit = _bounded_int(arguments.get("limit"), default=20, minimum=1, maximum=100)
    summarize_payload = arguments.get("summarize_payload")
    summarize_payload = True if summarize_payload is None else bool(summarize_payload)
    config = await get_runtime_config()
    trace_id = str(arguments.get("trace_id") or "").strip()
    access_event_id = _uuid_from_value(arguments.get("access_event_id"))
    await telemetry.flush()
    async with AsyncSessionLocal() as session:
        trace: TelemetryTrace | None = None
        if trace_id:
            trace = await session.get(TelemetryTrace, trace_id)
        elif access_event_id:
            trace = await session.scalar(
                select(TelemetryTrace)
                .where(TelemetryTrace.access_event_id == access_event_id)
                .order_by(TelemetryTrace.started_at.desc())
                .limit(1)
            )
        if not trace:
            return {
                "found": False,
                "error": "Telemetry trace not found.",
                "trace_id": trace_id or None,
                "access_event_id": str(access_event_id) if access_event_id else None,
            }
        spans = (
            await session.scalars(
                select(TelemetrySpan)
                .where(TelemetrySpan.trace_id == trace.trace_id)
                .order_by(TelemetrySpan.step_order, TelemetrySpan.started_at)
                .limit(limit)
            )
        ).all()
    return {
        "found": True,
        "timezone": config.site_timezone,
        "trace": _trace_diagnostic_payload(
            trace,
            list(spans),
            config.site_timezone,
            span_limit=limit,
            include_payloads=not summarize_payload,
            summarize_payload=summarize_payload,
        ),
    }


async def query_leaderboard(arguments: dict[str, Any]) -> dict[str, Any]:
    scope = _normalize(arguments.get("scope") or "all")
    if scope not in {"", "all", "known", "unknown", "top_known"}:
        return {"error": "scope must be all, known, unknown, or top_known."}
    scope = scope or "all"

    limit = _bounded_int(arguments.get("limit"), default=10, minimum=1, maximum=100)
    enrich_unknowns = arguments.get("enrich_unknowns")
    if enrich_unknowns is None:
        enrich_unknowns = scope in {"all", "unknown"}

    leaderboard = await get_leaderboard_service().get_leaderboard(
        limit=limit,
        enrich_unknowns=bool(enrich_unknowns),
    )

    search = _leaderboard_search_text(arguments)
    known = [
        row
        for row in list(leaderboard.get("known") or [])
        if not search or _leaderboard_known_matches(row, search)
    ]
    unknown = [
        row
        for row in list(leaderboard.get("unknown") or [])
        if not search or _leaderboard_unknown_matches(row, search)
    ]
    top_known = leaderboard.get("top_known")
    if search and isinstance(top_known, dict) and not _leaderboard_known_matches(top_known, search):
        top_known = known[0] if known else None

    response: dict[str, Any] = {
        "scope": scope,
        "generated_at": leaderboard.get("generated_at"),
        "top_known": top_known,
        "known_count": len(known),
        "unknown_count": len(unknown),
        "search": search or None,
        "enriched_unknowns": bool(enrich_unknowns),
    }
    if scope in {"all", "known"}:
        response["known"] = known
    if scope in {"all", "unknown"}:
        response["unknown"] = unknown
    if scope == "top_known":
        response["known"] = [top_known] if top_known else []
    return response


async def query_anomalies(arguments: dict[str, Any]) -> dict[str, Any]:
    limit = _bounded_int(arguments.get("limit"), default=10, minimum=1, maximum=100)
    severity = _normalize(arguments.get("severity"))
    config = await get_runtime_config()
    async with AsyncSessionLocal() as session:
        query = (
            select(Anomaly)
            .where(Anomaly.resolved_at.is_(None))
            .order_by(Anomaly.created_at.desc())
            .limit(limit)
        )
        anomalies = (await session.scalars(query)).all()

    records = [
        {
            "type": anomaly.anomaly_type.value,
            "severity": anomaly.severity.value,
            "message": anomaly.message,
            "status": "open",
            "created_at": _agent_datetime_iso(anomaly.created_at, config.site_timezone),
            "created_at_display": _agent_datetime_display(anomaly.created_at, config.site_timezone),
        }
        for anomaly in anomalies
        if not severity or severity == anomaly.severity.value
    ]
    return {"anomalies": records, "count": len(records), "timezone": config.site_timezone}


async def summarize_access_rhythm(arguments: dict[str, Any]) -> dict[str, Any]:
    result = await query_access_events({"day": arguments.get("day") or "today", "limit": 100})
    events = result["events"]
    return {
        "period": arguments.get("day") or "today",
        "total_events": len(events),
        "entries": sum(1 for event in events if event["direction"] == "entry"),
        "exits": sum(1 for event in events if event["direction"] == "exit"),
        "denials": sum(1 for event in events if event["decision"] == "denied"),
        "anomaly_events": sum(1 for event in events if event["anomaly_count"] > 0),
        "events": events[:10],
    }


async def calculate_visit_duration(arguments: dict[str, Any]) -> dict[str, Any]:
    result = await query_access_events(
        {
            "person": arguments.get("person"),
            "person_id": arguments.get("person_id"),
            "group": arguments.get("group"),
            "day": arguments.get("day") or "today",
            "limit": 100,
        }
    )
    timezone_name = str(result.get("timezone") or DEFAULT_AGENT_TIMEZONE)
    events = sorted(
        result["events"],
        key=lambda item: datetime.fromisoformat(str(item["occurred_at"])).astimezone(UTC),
    )
    open_entry: datetime | None = None
    total = timedelta()
    intervals: list[dict[str, str | None]] = []

    for event in events:
        occurred = datetime.fromisoformat(event["occurred_at"])
        if event["decision"] != AccessDecision.GRANTED.value:
            continue
        if event["direction"] == AccessDirection.ENTRY.value:
            open_entry = occurred
        elif event["direction"] == AccessDirection.EXIT.value and open_entry:
            total += occurred - open_entry
            intervals.append(
                {
                    "entry": _agent_datetime_iso(open_entry, timezone_name),
                    "entry_display": _agent_datetime_display(open_entry, timezone_name),
                    "exit": _agent_datetime_iso(occurred, timezone_name),
                    "exit_display": _agent_datetime_display(occurred, timezone_name),
                }
            )
            open_entry = None

    if open_entry:
        now = _agent_now(timezone_name)
        total += now - open_entry
        intervals.append(
            {
                "entry": _agent_datetime_iso(open_entry, timezone_name),
                "entry_display": _agent_datetime_display(open_entry, timezone_name),
                "exit": "still_present",
                "exit_display": None,
            }
        )

    return {
        "duration_seconds": int(total.total_seconds()),
        "duration_human": _human_duration(total),
        "intervals": intervals,
        "matched_events": len(events),
        "timezone": timezone_name,
    }


async def trigger_anomaly_alert(arguments: dict[str, Any]) -> dict[str, Any]:
    if not bool(arguments.get("confirm")):
        subject = str(arguments.get("subject") or "anomaly alert").strip()
        return {
            "sent": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": subject,
            "detail": "Send this anomaly alert notification?",
        }
    try:
        notification = await get_notification_service().notify(
            NotificationContext(
                event_type="agent_anomaly_alert",
                subject=str(arguments["subject"]),
                severity=str(arguments["severity"]),
                facts={"message": str(arguments["message"])},
            ),
            raise_on_failure=True,
        )
    except NotificationDeliveryError as exc:
        return {"sent": False, "error": str(exc)}
    await event_bus.publish(
        "ai.issue_detected",
        {
            "subject": str(arguments["subject"]),
            "severity": str(arguments["severity"]),
            "message": str(arguments["message"]),
            "issue": str(arguments["message"]),
            "source": "alfred",
        },
    )
    return {"sent": True, "title": notification.title, "body": notification.body}


async def get_system_users(arguments: dict[str, Any]) -> dict[str, Any]:
    include_inactive = bool(arguments.get("include_inactive"))
    async with AsyncSessionLocal() as session:
        query = select(User).order_by(User.first_name, User.last_name)
        users = (await session.scalars(query)).all()

    records = [
        {
            "full_name": user.full_name,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "username": user.username,
            "role": user.role.value,
            "is_active": user.is_active,
        }
        for user in users
        if include_inactive or user.is_active
    ]
    return {"users": records, "count": len(records)}


async def lookup_dvla_vehicle(arguments: dict[str, Any]) -> dict[str, Any]:
    registration_number = normalize_registration_number(str(arguments.get("registration_number") or ""))
    if not registration_number:
        return {"error": "registration_number is required."}
    try:
        vehicle = await lookup_vehicle_registration(registration_number)
    except DvlaVehicleEnquiryError as exc:
        return {
            "registration_number": registration_number,
            "error": str(exc),
        }
    return {
        "registration_number": registration_number,
        "vehicle": vehicle,
        "display_vehicle": display_vehicle_record(vehicle, registration_number),
        "normalized_vehicle": normalize_vehicle_enquiry_response(vehicle, registration_number).as_payload(),
    }


async def analyze_camera_snapshot(arguments: dict[str, Any]) -> dict[str, Any]:
    camera_identifier = str(arguments.get("camera_id") or arguments.get("camera_name") or "").strip()
    if not camera_identifier:
        return {"error": "camera_id or camera_name is required."}
    prompt = str(arguments.get("prompt") or "Describe what is visible in this camera snapshot.").strip()
    runtime = await get_runtime_config()
    provider = str(arguments.get("provider") or runtime.llm_provider)

    try:
        media = await get_unifi_protect_service().snapshot(
            camera_identifier,
            width=runtime.unifi_protect_snapshot_width,
            height=runtime.unifi_protect_snapshot_height,
        )
        result = await analyze_image_with_provider(
            provider,
            prompt=prompt,
            image_bytes=media.content,
            mime_type=media.content_type,
        )
    except (UnifiProtectError, ImageAnalysisUnsupportedError, Exception) as exc:
        return {"camera": camera_identifier, "provider": provider, "error": str(exc)}

    return {
        "camera": camera_identifier,
        "provider": provider,
        "analysis": result.text,
        "snapshot_retained": False,
    }


async def read_chat_attachment(arguments: dict[str, Any]) -> dict[str, Any]:
    context = get_chat_tool_context()
    user_id = str(context.get("user_id") or "")
    if not user_id:
        return {"error": "Attachment access requires an authenticated chat user."}

    file_id = str(arguments.get("file_id") or "").strip()
    if not file_id:
        return {"error": "file_id is required."}

    prompt = str(arguments.get("prompt") or "Summarize this attachment.").strip()
    runtime = await get_runtime_config()
    provider = str(arguments.get("provider") or runtime.llm_provider)

    try:
        attachment = chat_attachment_store.get(file_id)
        chat_attachment_store.require_access(attachment, user_id)
    except ChatAttachmentError as exc:
        return {"file_id": file_id, "error": str(exc)}

    if attachment.kind == "image":
        try:
            _, image_bytes = chat_attachment_store.read_bytes(file_id, owner_user_id=user_id)
            result = await analyze_image_with_provider(
                provider,
                prompt=prompt,
                image_bytes=image_bytes,
                mime_type=attachment.content_type,
            )
        except (ChatAttachmentError, ImageAnalysisUnsupportedError, Exception) as exc:
            return {
                "file_id": file_id,
                "filename": attachment.filename,
                "kind": attachment.kind,
                "provider": provider,
                "error": str(exc),
            }
        return {
            "file_id": file_id,
            "filename": attachment.filename,
            "kind": attachment.kind,
            "provider": provider,
            "analysis": result.text,
        }

    try:
        _, text = chat_attachment_store.read_text(file_id, owner_user_id=user_id)
    except ChatAttachmentError as exc:
        return {
            "file_id": file_id,
            "filename": attachment.filename,
            "kind": attachment.kind,
            "error": str(exc),
        }

    return {
        "file_id": file_id,
        "filename": attachment.filename,
        "kind": attachment.kind,
        "content_type": attachment.content_type,
        "text": text,
        "characters": len(text),
    }


async def export_presence_report_csv(arguments: dict[str, Any]) -> dict[str, Any]:
    context = get_chat_tool_context()
    user_id = str(context.get("user_id") or "")
    session_id = str(context.get("session_id") or "")
    if not user_id:
        return {"generated": False, "error": "File generation requires an authenticated chat user."}

    runtime = await get_runtime_config()
    day = str(arguments.get("day") or "today")
    presence = await query_presence({"person": arguments.get("person")})
    events = await query_access_events(
        {
            "person": arguments.get("person"),
            "group": arguments.get("group"),
            "day": day,
            "limit": 100,
        }
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["section", "person", "state", "registration_number", "direction", "decision", "occurred_at", "notes"])
    for row in presence.get("presence", []):
        writer.writerow(["presence", row.get("person"), row.get("state"), "", "", "", row.get("last_changed_at"), ""])
    for event in events.get("events", []):
        writer.writerow(
            [
                "access_event",
                event.get("person") or "",
                "",
                event.get("registration_number") or "",
                event.get("direction") or "",
                event.get("decision") or "",
                event.get("occurred_at") or "",
                f"{event.get('anomaly_count', 0)} anomalies",
            ]
        )

    filename = f"presence-report-{day}-{_agent_now(runtime.site_timezone).strftime('%Y%m%d-%H%M%S')}.csv"
    try:
        attachment = chat_attachment_store.save_generated(
            filename=filename,
            content=output.getvalue().encode("utf-8"),
            content_type="text/csv",
            owner_user_id=user_id,
            source="generated",
            session_id=session_id or None,
        )
    except ChatAttachmentError as exc:
        return {"generated": False, "error": str(exc)}

    return {
        "generated": True,
        "period": day,
        "rows": len(presence.get("presence", [])) + len(events.get("events", [])),
        "attachment": attachment.to_public_dict(),
    }


async def generate_contractor_invoice_pdf(arguments: dict[str, Any]) -> dict[str, Any]:
    context = get_chat_tool_context()
    user_id = str(context.get("user_id") or "")
    session_id = str(context.get("session_id") or "")
    if not user_id:
        return {"generated": False, "error": "File generation requires an authenticated chat user."}

    runtime = await get_runtime_config()
    contractor_name = str(arguments.get("contractor_name") or "Contractor").strip()
    day = str(arguments.get("day") or "today")
    hourly_rate = float(arguments.get("hourly_rate") or 0)
    currency = str(arguments.get("currency") or "GBP").strip().upper()
    duration = await calculate_visit_duration({"group": "contractor", "day": day})
    hours = round(int(duration.get("duration_seconds") or 0) / 3600, 2)
    amount = round(hours * hourly_rate, 2)
    issued_at = _agent_now(runtime.site_timezone)

    lines = [
        "Intelligent Access Control System",
        "Contractor Visit Invoice",
        "",
        f"Contractor: {contractor_name}",
        f"Period: {day}",
        f"Issued: {_agent_datetime_display(issued_at, runtime.site_timezone)}",
        f"Matched events: {duration.get('matched_events', 0)}",
        f"Visit duration: {duration.get('duration_human', '0m')} ({hours:.2f} hours)",
        f"Hourly rate: {currency} {hourly_rate:.2f}",
        f"Total: {currency} {amount:.2f}",
        "",
        "Intervals:",
    ]
    intervals = duration.get("intervals") or []
    if intervals:
        lines.extend(f"- {item.get('entry')} to {item.get('exit')}" for item in intervals[:12])
    else:
        lines.append("- No matched contractor intervals found.")

    filename = f"contractor-invoice-{_filename_slug(contractor_name)}-{issued_at.strftime('%Y%m%d-%H%M%S')}.pdf"
    try:
        attachment = chat_attachment_store.save_generated(
            filename=filename,
            content=_simple_pdf(lines),
            content_type="application/pdf",
            owner_user_id=user_id,
            source="generated",
            session_id=session_id or None,
        )
    except ChatAttachmentError as exc:
        return {"generated": False, "error": str(exc)}

    return {
        "generated": True,
        "contractor_name": contractor_name,
        "period": day,
        "duration_human": duration.get("duration_human"),
        "total": amount,
        "currency": currency,
        "attachment": attachment.to_public_dict(),
    }


async def get_camera_snapshot(arguments: dict[str, Any]) -> dict[str, Any]:
    context = get_chat_tool_context()
    user_id = str(context.get("user_id") or "")
    session_id = str(context.get("session_id") or "")
    if not user_id:
        return {"fetched": False, "error": "Camera media requires an authenticated chat user."}

    camera_identifier = str(arguments.get("camera_id") or arguments.get("camera_name") or "").strip()
    if not camera_identifier:
        return {"fetched": False, "error": "camera_id or camera_name is required."}
    runtime = await get_runtime_config()
    try:
        media = await get_unifi_protect_service().snapshot(
            camera_identifier,
            width=runtime.unifi_protect_snapshot_width,
            height=runtime.unifi_protect_snapshot_height,
        )
        attachment = chat_attachment_store.save_generated(
            filename=f"camera-snapshot-{_filename_slug(camera_identifier)}-{_agent_now(runtime.site_timezone).strftime('%Y%m%d-%H%M%S')}.jpg",
            content=media.content,
            content_type=media.content_type,
            owner_user_id=user_id,
            source="system_media",
            session_id=session_id or None,
        )
    except (UnifiProtectError, ChatAttachmentError, Exception) as exc:
        return {"fetched": False, "camera": camera_identifier, "error": str(exc)}

    return {
        "fetched": True,
        "camera": camera_identifier,
        "attachment": attachment.to_public_dict(),
    }


async def query_notification_catalog(_arguments: dict[str, Any]) -> dict[str, Any]:
    catalog = await get_notification_service().catalog()
    return {
        **catalog,
        "workflow_shape": {
            "conditions": ["schedule", "presence"],
            "actions": ["mobile", "voice", "in_app"],
            "variables": "Use @Variable tokens in title_template and message_template.",
        },
    }


async def query_notification_workflows(arguments: dict[str, Any]) -> dict[str, Any]:
    trigger_filter = str(arguments.get("trigger_event") or "").strip()
    active_filter = arguments.get("is_active")
    search = _normalize(arguments.get("search"))
    include_preview = bool(arguments.get("include_preview"))
    limit = _bounded_int(arguments.get("limit"), default=20, minimum=1, maximum=100)
    summarize_payload = arguments.get("summarize_payload")
    summarize_payload = True if summarize_payload is None else bool(summarize_payload)

    async with AsyncSessionLocal() as session:
        query = select(NotificationRule)
        if trigger_filter:
            query = query.where(NotificationRule.trigger_event == trigger_filter)
        if isinstance(active_filter, bool):
            query = query.where(NotificationRule.is_active.is_(active_filter))
        if search:
            pattern = f"%{search}%"
            query = query.where(
                or_(
                    func.lower(NotificationRule.name).like(pattern),
                    func.lower(NotificationRule.trigger_event).like(pattern),
                )
            )
        rules = (
            await session.scalars(
                query.order_by(NotificationRule.created_at.desc(), NotificationRule.name).limit(limit)
            )
        ).all()

    workflows: list[dict[str, Any]] = []
    for rule in rules:
        workflow = _serialize_notification_rule_for_agent(rule)
        if summarize_payload:
            workflow = _compact_notification_workflow(workflow)
        if include_preview:
            workflow["preview"] = await get_notification_service().preview_rule(workflow)
        workflows.append(workflow)
    return {"workflows": workflows, "count": len(workflows)}


async def get_notification_workflow(arguments: dict[str, Any]) -> dict[str, Any]:
    include_preview = bool(arguments.get("include_preview", True))
    async with AsyncSessionLocal() as session:
        rule = await _resolve_notification_rule(session, arguments)
        if not rule:
            return {"found": False, "error": "Notification workflow not found."}
        workflow = _serialize_notification_rule_for_agent(rule)

    result: dict[str, Any] = {"found": True, "workflow": workflow}
    if include_preview:
        result["preview"] = await get_notification_service().preview_rule(workflow)
    return result


async def create_notification_workflow(arguments: dict[str, Any]) -> dict[str, Any]:
    if not bool(arguments.get("confirm")):
        name = str(arguments.get("name") or "notification workflow").strip()
        return {
            "created": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "workflow_name": name,
            "detail": "Create this notification workflow? Future matching events may send real notifications.",
        }

    normalized = normalize_rule_payload(arguments)
    name = normalized["name"]
    trigger_event = normalized["trigger_event"]
    actions = normalized["actions"]
    if not name:
        return {"created": False, "error": "Workflow name is required."}
    if not trigger_event:
        return {"created": False, "error": "trigger_event is required."}
    if not actions:
        return {"created": False, "error": "At least one notification action is required."}

    async with AsyncSessionLocal() as session:
        rule = NotificationRule(
            name=name,
            trigger_event=trigger_event,
            conditions=normalized["conditions"],
            actions=actions,
            is_active=normalized["is_active"],
        )
        session.add(rule)
        try:
            await session.commit()
            await session.refresh(rule)
        except IntegrityError:
            await session.rollback()
            return {"created": False, "error": "Notification workflow could not be created."}
        workflow = _serialize_notification_rule_for_agent(rule)

    return {
        "created": True,
        "workflow": workflow,
        "preview": await get_notification_service().preview_rule(workflow),
    }


async def update_notification_workflow(arguments: dict[str, Any]) -> dict[str, Any]:
    if not bool(arguments.get("confirm")):
        name = str(arguments.get("rule_name") or arguments.get("name") or "notification workflow").strip()
        return {
            "updated": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "workflow_name": name,
            "detail": "Update this notification workflow? Future matching events may use the changed delivery rules.",
        }

    async with AsyncSessionLocal() as session:
        rule = await _resolve_notification_rule(session, arguments)
        if not rule:
            return {"updated": False, "error": "Notification workflow not found."}

        if "name" in arguments:
            name = str(arguments.get("name") or "").strip()
            if not name:
                return {"updated": False, "error": "Workflow name cannot be empty."}
            rule.name = name
        if "trigger_event" in arguments:
            trigger_event = str(arguments.get("trigger_event") or "").strip()
            if not trigger_event:
                return {"updated": False, "error": "trigger_event cannot be empty."}
            normalized_trigger_payload = normalize_rule_payload(
                {
                    "trigger_event": trigger_event,
                    "actions": rule.actions,
                }
            )
            rule.trigger_event = normalized_trigger_payload["trigger_event"]
            if "actions" not in arguments:
                rule.actions = normalized_trigger_payload["actions"]
        if "conditions" in arguments:
            rule.conditions = normalize_conditions(arguments.get("conditions"))
        if "actions" in arguments:
            actions = normalize_rule_payload(
                {
                    "trigger_event": arguments.get("trigger_event", rule.trigger_event),
                    "actions": arguments.get("actions"),
                }
            )["actions"]
            if not actions:
                return {"updated": False, "error": "At least one notification action is required."}
            rule.actions = actions
        if "is_active" in arguments:
            rule.is_active = bool(arguments.get("is_active"))

        try:
            await session.commit()
            await session.refresh(rule)
        except IntegrityError:
            await session.rollback()
            return {"updated": False, "error": "Notification workflow could not be updated."}
        workflow = _serialize_notification_rule_for_agent(rule)

    return {
        "updated": True,
        "workflow": workflow,
        "preview": await get_notification_service().preview_rule(workflow),
    }


async def delete_notification_workflow(arguments: dict[str, Any]) -> dict[str, Any]:
    if not bool(arguments.get("confirm")):
        name = str(arguments.get("rule_name") or arguments.get("rule_id") or "notification workflow").strip()
        return {
            "deleted": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "workflow_name": name,
            "detail": "Delete this notification workflow?",
        }

    async with AsyncSessionLocal() as session:
        rule = await _resolve_notification_rule(session, arguments)
        if not rule:
            return {"deleted": False, "error": "Notification workflow not found."}
        workflow = _serialize_notification_rule_for_agent(rule)
        await session.delete(rule)
        await session.commit()
    return {"deleted": True, "workflow": workflow}


async def preview_notification_workflow(arguments: dict[str, Any]) -> dict[str, Any]:
    rule = await _notification_rule_payload_for_agent(arguments)
    if not rule:
        return {"previewed": False, "error": "Supply rule payload, rule_id, or rule_name."}
    context = _notification_context_for_agent(arguments.get("context"), rule["trigger_event"])
    return {
        "previewed": True,
        "preview": await get_notification_service().preview_rule(rule, context),
    }


async def test_notification_workflow(arguments: dict[str, Any]) -> dict[str, Any]:
    if not bool(arguments.get("confirm_send")):
        name = str(arguments.get("rule_name") or arguments.get("name") or "notification workflow").strip()
        return {
            "sent": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm_send",
            "workflow_name": name,
            "detail": "Send a real test notification for this workflow?",
        }

    rule = await _notification_rule_payload_for_agent(arguments)
    if not rule:
        return {"sent": False, "error": "Supply rule payload, rule_id, or rule_name."}
    if not rule["trigger_event"]:
        return {"sent": False, "error": "A trigger_event is required before sending a test."}
    if not rule["actions"]:
        return {"sent": False, "error": "At least one notification action is required before sending a test."}

    context = _notification_context_for_agent(arguments.get("context"), rule["trigger_event"])
    try:
        notification = await get_notification_service().process_context(
            context,
            raise_on_failure=True,
            rules_override=[rule],
        )
    except NotificationDeliveryError as exc:
        return {
            "sent": False,
            "error": str(exc),
            "preview": await get_notification_service().preview_rule(rule, context),
        }

    return {
        "sent": True,
        "title": notification.title,
        "body": notification.body,
        "preview": await get_notification_service().preview_rule(rule, context),
    }


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


async def query_schedules(arguments: dict[str, Any]) -> dict[str, Any]:
    search = _normalize(arguments.get("search"))
    include_dependencies = bool(arguments.get("include_dependencies"))
    async with AsyncSessionLocal() as session:
        schedules = (await session.scalars(select(Schedule).order_by(Schedule.name))).all()
        records: list[dict[str, Any]] = []
        for schedule in schedules:
            serialized = _serialize_schedule_for_agent(schedule)
            if search and search not in f"{schedule.name} {schedule.description or ''}".lower():
                continue
            if include_dependencies:
                dependencies = await schedule_dependencies(session, schedule.id)
                serialized["dependency_counts"] = {
                    key: len(rows)
                    for key, rows in dependencies.items()
                }
            records.append(serialized)
    return {"schedules": records, "count": len(records)}


async def get_schedule(arguments: dict[str, Any]) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        schedule = await _resolve_schedule(session, arguments)
        if not schedule:
            return {"error": "Schedule not found."}
        dependencies = await schedule_dependencies(session, schedule.id)
        return {
            "schedule": _serialize_schedule_for_agent(schedule),
            "dependencies": dependencies,
        }


async def create_schedule(arguments: dict[str, Any]) -> dict[str, Any]:
    name = str(arguments.get("name") or "").strip()
    if not name:
        return {"created": False, "error": "Schedule name is required."}
    try:
        time_blocks = _time_blocks_from_agent_arguments(arguments)
    except (TypeError, ValueError) as exc:
        return {"created": False, "error": str(exc)}
    if not _schedule_has_allowed_time(time_blocks):
        return {
            "created": False,
            "requires_details": True,
            "detail": "I need at least one allowed day and time before I create a schedule.",
        }
    if not bool(arguments.get("confirm")):
        return {
            "created": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": name,
            "detail": f"Create schedule {name}?",
        }

    async with AsyncSessionLocal() as session:
        schedule = Schedule(
            name=name,
            description=_optional_text(arguments.get("description")),
            time_blocks=time_blocks,
        )
        session.add(schedule)
        try:
            await session.commit()
            await session.refresh(schedule)
        except IntegrityError:
            await session.rollback()
            return {
                "created": False,
                "error": "Schedule already exists.",
                "error_code": "schedule_exists",
                "schedule_name": name,
            }
        return {"created": True, "schedule": _serialize_schedule_for_agent(schedule)}


async def update_schedule(arguments: dict[str, Any]) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        schedule = await _resolve_schedule(session, arguments)
        if not schedule:
            return {"updated": False, "error": "Schedule not found."}

        if "time_blocks" in arguments or _natural_schedule_text_from_arguments(arguments):
            try:
                schedule.time_blocks = _time_blocks_from_agent_arguments(arguments)
            except (TypeError, ValueError) as exc:
                return {"updated": False, "error": str(exc)}
        if "name" in arguments:
            name = str(arguments.get("name") or "").strip()
            if not name:
                return {"updated": False, "error": "Schedule name cannot be empty."}
            schedule.name = name
        if "description" in arguments:
            schedule.description = _optional_text(arguments.get("description"))

        try:
            await session.commit()
            await session.refresh(schedule)
        except IntegrityError:
            await session.rollback()
            return {"updated": False, "error": "Schedule already exists."}
        return {"updated": True, "schedule": _serialize_schedule_for_agent(schedule)}


async def delete_schedule(arguments: dict[str, Any]) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        schedule = await _resolve_schedule(session, arguments)
        if not schedule:
            return {"deleted": False, "error": "Schedule not found."}
        dependencies = await schedule_dependencies(session, schedule.id)
        if any(dependencies.values()):
            return {
                "deleted": False,
                "error": "Schedule is currently assigned and cannot be deleted.",
                "schedule_name": schedule.name,
                "dependencies": dependencies,
            }
        serialized = _serialize_schedule_for_agent(schedule)
        if not bool(arguments.get("confirm")):
            return {
                "deleted": False,
                "requires_confirmation": True,
                "confirmation_field": "confirm",
                "schedule_name": schedule.name,
                "schedule": serialized,
                "detail": f"Delete the {schedule.name} schedule? This cannot be undone.",
            }
        await session.delete(schedule)
        await session.commit()
        return {"deleted": True, "schedule": serialized}


async def query_schedule_targets(arguments: dict[str, Any]) -> dict[str, Any]:
    entity_type = _normalize(arguments.get("entity_type") or "all")
    search = _normalize(arguments.get("search"))
    limit = _bounded_int(arguments.get("limit"), default=25, minimum=1, maximum=100)
    include_people = entity_type in {"", "all", "person"}
    include_vehicles = entity_type in {"", "all", "vehicle"}
    include_doors = entity_type in {"", "all", "gate", "garage_door", "door"}

    async with AsyncSessionLocal() as session:
        people: list[dict[str, Any]] = []
        vehicles: list[dict[str, Any]] = []
        if include_people:
            person_rows = (
                await session.scalars(
                    select(Person)
                    .options(selectinload(Person.schedule), selectinload(Person.group))
                    .order_by(Person.display_name)
                )
            ).all()
            people = [
                _serialize_person_schedule_target(person)
                for person in person_rows
                if not search or search in f"{person.display_name} {person.group.name if person.group else ''}".lower()
            ][:limit]
        if include_vehicles:
            vehicle_rows = (
                await session.scalars(
                    select(Vehicle)
                    .options(
                        selectinload(Vehicle.schedule),
                        selectinload(Vehicle.owner).selectinload(Person.schedule),
                    )
                    .order_by(Vehicle.registration_number)
                )
            ).all()
            vehicles = [
                _serialize_vehicle_schedule_target(vehicle)
                for vehicle in vehicle_rows
                if not search or search in f"{vehicle.registration_number} {vehicle.owner.display_name if vehicle.owner else ''}".lower()
            ][:limit]

    doors = (await _schedule_door_targets(entity_type=entity_type, search=search))[:limit] if include_doors else []
    return {
        "people": people,
        "vehicles": vehicles,
        "doors": doors,
        "counts": {
            "people": len(people),
            "vehicles": len(vehicles),
            "doors": len(doors),
        },
    }


async def assign_schedule_to_entity(arguments: dict[str, Any]) -> dict[str, Any]:
    entity_type = _normalize(arguments.get("entity_type"))
    clear_schedule = bool(arguments.get("clear_schedule"))

    async with AsyncSessionLocal() as session:
        schedule = None if clear_schedule else await _resolve_schedule(session, arguments)
        if not clear_schedule and not schedule:
            return {"assigned": False, "error": "Schedule not found. Supply schedule_id or schedule_name, or set clear_schedule=true."}

        if entity_type == "person":
            person = await _resolve_person(session, arguments)
            if not person:
                return {"assigned": False, "error": "Person not found."}
            person.schedule_id = schedule.id if schedule else None
            await session.commit()
            refreshed = await _load_person_with_schedule(session, person.id)
            return {
                "assigned": True,
                "entity_type": "person",
                "person": _serialize_person_schedule_target(refreshed or person),
            }

        if entity_type == "vehicle":
            vehicle = await _resolve_vehicle(session, arguments)
            if not vehicle:
                return {"assigned": False, "error": "Vehicle not found."}
            vehicle.schedule_id = schedule.id if schedule else None
            await session.commit()
            refreshed = await _load_vehicle_with_schedule(session, vehicle.id)
            return {
                "assigned": True,
                "entity_type": "vehicle",
                "vehicle": _serialize_vehicle_schedule_target(refreshed or vehicle),
                "inheritance": "inherits owner schedule when schedule_id is null",
            }

    if entity_type in {"gate", "garage_door", "door"}:
        return await _assign_schedule_to_cover(arguments, schedule_id=str(schedule.id) if schedule else None)

    return {"assigned": False, "error": "entity_type must be person, vehicle, gate, garage_door, or door."}


async def verify_schedule_access(arguments: dict[str, Any]) -> dict[str, Any]:
    entity_type = _normalize(arguments.get("entity_type") or "schedule")
    config = await get_runtime_config()
    try:
        occurred_at = _parse_agent_datetime(arguments.get("at"), config.site_timezone)
    except (TypeError, ValueError) as exc:
        return {"verified": False, "error": f"Invalid at datetime: {exc}"}

    async with AsyncSessionLocal() as session:
        if entity_type == "schedule":
            schedule = await _resolve_schedule(session, arguments)
            if not schedule:
                return {"verified": False, "error": "Schedule not found."}
            allowed = schedule_allows_at(schedule, occurred_at, config.site_timezone)
            return {
                "verified": True,
                "allowed": allowed,
                "source": "schedule",
                "checked_at": _agent_datetime_iso(occurred_at, config.site_timezone),
                "checked_at_display": _agent_datetime_display(occurred_at, config.site_timezone),
                "timezone": config.site_timezone,
                "schedule": _serialize_schedule_for_agent(schedule),
                "reason": f"{schedule.name} allows this time." if allowed else f"{schedule.name} does not allow this time.",
            }

        if entity_type == "person":
            person = await _resolve_person(session, arguments)
            if not person:
                return {"verified": False, "error": "Person not found."}
            evaluation = await evaluate_person_schedule(
                session,
                person,
                occurred_at,
                timezone_name=config.site_timezone,
                default_policy=config.schedule_default_policy,
            )
            return {
                "verified": True,
                "entity_type": "person",
                "person": person.display_name,
                "allowed": evaluation.allowed,
                "source": evaluation.source,
                "schedule_id": str(evaluation.schedule_id) if evaluation.schedule_id else None,
                "schedule_name": evaluation.schedule_name,
                "override_id": str(evaluation.override_id) if evaluation.override_id else None,
                "override_ends_at": _agent_datetime_iso(evaluation.override_ends_at, config.site_timezone) if evaluation.override_ends_at else None,
                "checked_at": _agent_datetime_iso(occurred_at, config.site_timezone),
                "checked_at_display": _agent_datetime_display(occurred_at, config.site_timezone),
                "timezone": config.site_timezone,
                "reason": evaluation.reason,
            }

        if entity_type == "vehicle":
            vehicle = await _resolve_vehicle(session, arguments)
            if not vehicle:
                return {"verified": False, "error": "Vehicle not found."}
            evaluation = await evaluate_vehicle_schedule(
                session,
                vehicle,
                occurred_at,
                timezone_name=config.site_timezone,
                default_policy=config.schedule_default_policy,
            )
            return {
                "verified": True,
                "entity_type": "vehicle",
                "registration_number": vehicle.registration_number,
                "owner": vehicle.owner.display_name if vehicle.owner else None,
                "allowed": evaluation.allowed,
                "source": evaluation.source,
                "schedule_id": str(evaluation.schedule_id) if evaluation.schedule_id else None,
                "schedule_name": evaluation.schedule_name,
                "override_id": str(evaluation.override_id) if evaluation.override_id else None,
                "override_ends_at": _agent_datetime_iso(evaluation.override_ends_at, config.site_timezone) if evaluation.override_ends_at else None,
                "checked_at": _agent_datetime_iso(occurred_at, config.site_timezone),
                "checked_at_display": _agent_datetime_display(occurred_at, config.site_timezone),
                "timezone": config.site_timezone,
                "reason": evaluation.reason,
            }

    if entity_type in {"gate", "garage_door", "door"}:
        door = await _resolve_cover_target(arguments, entity_type=entity_type)
        if not door:
            return {"verified": False, "error": "Door/gate target not found."}
        async with AsyncSessionLocal() as session:
            evaluation = await evaluate_schedule_id(
                session,
                door["entity"].get("schedule_id"),
                occurred_at,
                timezone_name=config.site_timezone,
                default_policy=config.schedule_default_policy,
                source=str(door["kind"]),
            )
        return {
            "verified": True,
            "entity_type": door["kind"],
            "entity_id": door["entity"]["entity_id"],
            "name": door["entity"]["name"],
            "allowed": evaluation.allowed,
            "source": evaluation.source,
            "schedule_id": str(evaluation.schedule_id) if evaluation.schedule_id else None,
            "schedule_name": evaluation.schedule_name,
            "checked_at": _agent_datetime_iso(occurred_at, config.site_timezone),
            "checked_at_display": _agent_datetime_display(occurred_at, config.site_timezone),
            "timezone": config.site_timezone,
            "reason": evaluation.reason,
        }

    return {"verified": False, "error": "entity_type must be schedule, person, vehicle, gate, garage_door, or door."}


async def _resolve_notification_rule(session, arguments: dict[str, Any]) -> NotificationRule | None:
    rule_id = _uuid_from_value(
        arguments.get("rule_id")
        or arguments.get("notification_rule_id")
        or arguments.get("id")
    )
    if rule_id:
        return await session.get(NotificationRule, rule_id)

    rule_name = _normalize(
        arguments.get("rule_name")
        or arguments.get("notification_rule_name")
        or arguments.get("name")
    )
    if not rule_name:
        return None
    rules = (await session.scalars(select(NotificationRule).order_by(NotificationRule.name))).all()
    exact = [rule for rule in rules if rule.name.lower() == rule_name]
    if exact:
        return exact[0]
    partial = [rule for rule in rules if rule_name in f"{rule.name} {rule.trigger_event}".lower()]
    return partial[0] if len(partial) == 1 else None


async def _notification_rule_payload_for_agent(arguments: dict[str, Any]) -> dict[str, Any] | None:
    raw_rule = arguments.get("rule")
    if isinstance(raw_rule, dict):
        return normalize_rule_payload(raw_rule)
    async with AsyncSessionLocal() as session:
        rule = await _resolve_notification_rule(session, arguments)
        if not rule:
            return None
        return _serialize_notification_rule_for_agent(rule)


def _serialize_notification_rule_for_agent(rule: NotificationRule) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "trigger_event": rule.trigger_event,
        "conditions": normalize_conditions(rule.conditions),
        "actions": normalize_actions(rule.actions),
        "is_active": rule.is_active,
        "last_fired_at": _agent_datetime_iso(rule.last_fired_at) if rule.last_fired_at else None,
        "last_fired_at_display": _agent_datetime_display(rule.last_fired_at) if rule.last_fired_at else None,
        "created_at": _agent_datetime_iso(rule.created_at) if rule.created_at else None,
        "created_at_display": _agent_datetime_display(rule.created_at) if rule.created_at else None,
        "updated_at": _agent_datetime_iso(rule.updated_at) if rule.updated_at else None,
        "updated_at_display": _agent_datetime_display(rule.updated_at) if rule.updated_at else None,
    }


def _notification_context_for_agent(value: Any, trigger_event: str) -> NotificationContext:
    if isinstance(value, dict):
        payload = dict(value)
        payload.setdefault("event_type", trigger_event or payload.get("trigger_event") or "integration_test")
        if not isinstance(payload.get("facts"), dict):
            reserved = {"event_type", "trigger_event", "subject", "severity"}
            payload["facts"] = {
                str(key): item
                for key, item in payload.items()
                if key not in reserved
            }
        return notification_context_from_payload(payload)
    return sample_notification_context(trigger_event or "integration_test")


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


async def _chat_context_user() -> User | None:
    context = get_chat_tool_context()
    user_id = _uuid_from_value(context.get("user_id"))
    if not user_id:
        return None
    async with AsyncSessionLocal() as session:
        return await session.get(User, user_id)


def _access_event_load_options() -> tuple[Any, ...]:
    return (
        selectinload(AccessEvent.vehicle).selectinload(Vehicle.owner).selectinload(Person.group),
        selectinload(AccessEvent.anomalies),
    )


async def _resolve_access_event_for_diagnostics(
    session,
    arguments: dict[str, Any],
    person_map: dict[str, dict[str, str]],
    timezone_name: str,
) -> AccessEvent | None:
    event_id = _uuid_from_value(arguments.get("access_event_id") or arguments.get("event_id"))
    query_options = _access_event_load_options()
    if event_id:
        return await session.scalar(
            select(AccessEvent)
            .options(*query_options)
            .where(AccessEvent.id == event_id)
        )

    start, end = _period_bounds(str(arguments.get("day") or "recent"), timezone_name)
    query = (
        select(AccessEvent)
        .options(*query_options)
        .where(AccessEvent.occurred_at >= start, AccessEvent.occurred_at <= end)
        .order_by(AccessEvent.occurred_at.desc())
        .limit(250)
    )
    registration_number = normalize_registration_number(str(arguments.get("registration_number") or ""))
    if registration_number:
        query = query.where(AccessEvent.registration_number == registration_number)
    person_id = _uuid_from_value(arguments.get("person_id"))
    if person_id:
        query = query.where(AccessEvent.person_id == person_id)
    vehicle_id = _uuid_from_value(arguments.get("vehicle_id"))
    if vehicle_id:
        query = query.where(AccessEvent.vehicle_id == vehicle_id)
    if bool(arguments.get("unknown_only")):
        query = query.where(AccessEvent.vehicle_id.is_(None))

    events = (await session.scalars(query)).all()
    person_filter = _normalize(arguments.get("person"))
    group_filter = _normalize(arguments.get("group"))
    decision_filter = _normalize(arguments.get("decision"))
    direction_filter = _normalize(arguments.get("direction"))
    for event in events:
        person = person_map.get(str(event.person_id)) if event.person_id else None
        if person_filter and (not person or not _person_record_matches(person, person_filter)):
            continue
        if group_filter and (not person or group_filter not in person.get("group", "").lower()):
            continue
        if decision_filter and event.decision.value != decision_filter:
            continue
        if direction_filter and event.direction.value != direction_filter:
            continue
        return event
    return None


async def _telemetry_for_access_event(session, event: AccessEvent) -> tuple[TelemetryTrace | None, list[TelemetrySpan]]:
    trace_id = _trace_id_from_access_event(event)
    trace: TelemetryTrace | None = None
    if trace_id:
        trace = await session.get(TelemetryTrace, trace_id)
    if not trace:
        trace = await session.scalar(
            select(TelemetryTrace)
            .where(TelemetryTrace.access_event_id == event.id)
            .order_by(TelemetryTrace.started_at.desc())
            .limit(1)
        )
    if not trace:
        return None, []
    spans = (
        await session.scalars(
            select(TelemetrySpan)
            .where(TelemetrySpan.trace_id == trace.trace_id)
            .order_by(TelemetrySpan.step_order, TelemetrySpan.started_at)
        )
    ).all()
    return trace, list(spans)


def _trace_id_from_access_event(event: AccessEvent) -> str | None:
    raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    telemetry_payload = raw_payload.get("telemetry") if isinstance(raw_payload.get("telemetry"), dict) else {}
    trace_id = str(telemetry_payload.get("trace_id") or "").strip()
    return trace_id or None


def _access_event_diagnostic_payload(
    event: AccessEvent,
    person: dict[str, str] | None,
    timezone_name: str,
    *,
    summarize_payload: bool = True,
) -> dict[str, Any]:
    raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    schedule = raw_payload.get("schedule") if isinstance(raw_payload.get("schedule"), dict) else {}
    direction_resolution = (
        raw_payload.get("direction_resolution")
        if isinstance(raw_payload.get("direction_resolution"), dict)
        else {}
    )
    debounce = raw_payload.get("debounce") if isinstance(raw_payload.get("debounce"), dict) else {}
    return {
        "id": str(event.id),
        "registration_number": event.registration_number,
        "person": person.get("display_name") if person else None,
        "group": person.get("group") if person else None,
        "vehicle": _vehicle_agent_payload(event.vehicle),
        "direction": event.direction.value,
        "decision": event.decision.value,
        "confidence": event.confidence,
        "source": event.source,
        "occurred_at": _agent_datetime_iso(event.occurred_at, timezone_name),
        "occurred_at_display": _agent_datetime_display(event.occurred_at, timezone_name),
        "timing_classification": event.timing_classification.value,
        "schedule": _payload_summary(schedule) if summarize_payload else schedule,
        "direction_resolution": _payload_summary(direction_resolution) if summarize_payload else direction_resolution,
        "gate_observation": _gate_observation_from_event(event),
        "debounce": {
            "candidate_count": debounce.get("candidate_count"),
            "candidates": _compact_value(debounce.get("candidates") or [], max_list_items=6)
            if summarize_payload
            else debounce.get("candidates") or [],
        },
        "anomalies": [
            {
                "id": str(anomaly.id),
                "type": anomaly.anomaly_type.value,
                "severity": anomaly.severity.value,
                "message": anomaly.message,
                "resolved": bool(anomaly.resolved_at),
            }
            for anomaly in event.anomalies
        ],
        "telemetry_trace_id": _trace_id_from_access_event(event),
        "payload_summary": _payload_summary(raw_payload) if summarize_payload else None,
        "raw_payload": raw_payload if not summarize_payload else None,
    }


def _vehicle_agent_payload(vehicle: Vehicle | None) -> dict[str, Any] | None:
    if not vehicle:
        return None
    owner = vehicle.owner
    return {
        "id": str(vehicle.id),
        "registration_number": vehicle.registration_number,
        "make": vehicle.make,
        "model": vehicle.model,
        "color": vehicle.color,
        "description": vehicle.description,
        "is_active": vehicle.is_active,
        "owner": owner.display_name if owner else None,
        "owner_id": str(owner.id) if owner else None,
    }


def _trace_diagnostic_payload(
    trace: TelemetryTrace | None,
    spans: list[TelemetrySpan],
    timezone_name: str,
    *,
    span_limit: int = 20,
    include_payloads: bool = False,
    summarize_payload: bool = True,
) -> dict[str, Any] | None:
    if not trace:
        return None
    limited_spans = spans[:span_limit]
    return {
        "trace_id": trace.trace_id,
        "name": trace.name,
        "category": trace.category,
        "status": trace.status,
        "level": trace.level,
        "started_at": _agent_datetime_iso(trace.started_at, timezone_name),
        "ended_at": _agent_datetime_iso(trace.ended_at, timezone_name) if trace.ended_at else None,
        "duration_ms": trace.duration_ms,
        "summary": trace.summary,
        "error": trace.error,
        "context": _payload_summary(trace.context or {}) if summarize_payload else trace.context or {},
        "span_count": len(spans),
        "spans": [
            _span_diagnostic_payload(
                span,
                timezone_name,
                include_payloads=include_payloads,
                summarize_payload=summarize_payload,
            )
            for span in limited_spans
        ],
    }


def _span_diagnostic_payload(
    span: TelemetrySpan,
    timezone_name: str,
    *,
    include_payloads: bool = False,
    summarize_payload: bool = True,
) -> dict[str, Any]:
    payload = {
        "span_id": span.span_id,
        "name": span.name,
        "category": span.category,
        "step_order": span.step_order,
        "started_at": _agent_datetime_iso(span.started_at, timezone_name),
        "duration_ms": span.duration_ms,
        "status": span.status,
        "attributes": _payload_summary(span.attributes or {}) if summarize_payload else span.attributes or {},
        "error": span.error,
    }
    if include_payloads:
        payload["input_payload"] = span.input_payload or {}
        payload["output_payload"] = span.output_payload or {}
    else:
        payload["input_payload_summary"] = _payload_summary(span.input_payload or {})
        payload["output_payload_summary"] = _payload_summary(span.output_payload or {})
    return _compact_observation(payload)


def _incident_window(arguments: dict[str, Any], timezone_name: str) -> tuple[datetime, datetime, datetime | None]:
    day = str(arguments.get("day") or "today")
    expected_at = _parse_incident_datetime(
        arguments.get("expected_time") or arguments.get("captured_at") or arguments.get("at"),
        timezone_name,
        day,
    )
    window_minutes = _bounded_int(arguments.get("window_minutes"), default=20, minimum=1, maximum=720)
    if expected_at:
        start = (expected_at - timedelta(minutes=window_minutes)).astimezone(UTC)
        end = (expected_at + timedelta(minutes=window_minutes)).astimezone(UTC)
        return start, end, expected_at.astimezone(UTC)
    start, end = _period_bounds(day, timezone_name)
    return start, end, None


def _parse_incident_datetime(value: Any, timezone_name: str, day: str = "today") -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _parse_agent_datetime(text, timezone_name).astimezone(UTC)
    except ValueError:
        pass

    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap]m)?\b", text.lower())
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridian = match.group(3)
    if meridian == "pm" and hour < 12:
        hour += 12
    if meridian == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    start, _end = _period_bounds(day, timezone_name)
    local_day = start.astimezone(_agent_timezone(timezone_name))
    return local_day.replace(hour=hour, minute=minute, second=0, microsecond=0).astimezone(UTC)


async def _resolve_incident_subject(session, arguments: dict[str, Any]) -> dict[str, Any]:
    person: Person | None = None
    vehicle: Vehicle | None = None
    vehicle_id = _uuid_from_value(arguments.get("vehicle_id"))
    if vehicle_id:
        vehicle = await session.scalar(
            select(Vehicle)
            .options(
                selectinload(Vehicle.owner).selectinload(Person.group),
                selectinload(Vehicle.owner).selectinload(Person.schedule),
                selectinload(Vehicle.schedule),
            )
            .where(Vehicle.id == vehicle_id)
        )
        person = vehicle.owner if vehicle else None

    person_id = _uuid_from_value(arguments.get("person_id"))
    if not person and person_id:
        person = await session.scalar(
            select(Person)
            .options(
                selectinload(Person.group),
                selectinload(Person.schedule),
                selectinload(Person.vehicles).selectinload(Vehicle.schedule),
            )
            .where(Person.id == person_id)
        )

    person_text = _normalize(arguments.get("person") or arguments.get("person_name") or arguments.get("name"))
    if not person and person_text:
        people = (
            await session.scalars(
                select(Person)
                .options(
                    selectinload(Person.group),
                    selectinload(Person.schedule),
                    selectinload(Person.vehicles).selectinload(Vehicle.schedule),
                )
                .order_by(Person.display_name)
            )
        ).all()
        matches = [
            item
            for item in people
            if _person_record_matches(
                {"display_name": item.display_name, "group": item.group.name if item.group else ""},
                person_text,
            )
        ]
        if len(matches) == 1:
            person = matches[0]

    registration_number = normalize_registration_number(str(arguments.get("registration_number") or ""))
    if registration_number and not vehicle:
        vehicle = await session.scalar(
            select(Vehicle)
            .options(
                selectinload(Vehicle.owner).selectinload(Person.group),
                selectinload(Vehicle.owner).selectinload(Person.schedule),
                selectinload(Vehicle.schedule),
            )
            .where(Vehicle.registration_number == registration_number)
        )
        if vehicle and not person:
            person = vehicle.owner

    vehicles = [vehicle] if vehicle else list(person.vehicles or []) if person else []
    vehicle_payloads = [_vehicle_agent_payload(item) for item in vehicles]
    plates = [
        normalize_registration_number(str(item.registration_number or ""))
        for item in vehicles
        if str(item.registration_number or "").strip()
    ]
    if registration_number and registration_number not in plates:
        plates.append(registration_number)
    label = (
        person.display_name
        if person
        else vehicle.registration_number
        if vehicle
        else registration_number
        if registration_number
        else "Unresolved subject"
    )
    return {
        "person": person,
        "vehicle": vehicle,
        "vehicles": vehicles,
        "summary": _compact_observation(
            {
                "label": label,
                "person_id": str(person.id) if person else None,
                "person": person.display_name if person else None,
                "group": person.group.name if person and person.group else None,
                "vehicle_id": str(vehicle.id) if vehicle else None,
                "vehicles": vehicle_payloads,
                "plates": plates,
                "resolved": bool(person or vehicle or registration_number),
            }
        ),
    }


def _incident_candidate_plates(subject: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    plates = list((subject.get("summary") or {}).get("plates") or [])
    registration_number = normalize_registration_number(str(arguments.get("registration_number") or ""))
    if registration_number and registration_number not in plates:
        plates.append(registration_number)
    return [plate for plate in plates if plate]


async def _incident_iacs_events(
    session,
    *,
    subject: dict[str, Any],
    plates: list[str],
    start: datetime,
    end: datetime,
    direction: str,
) -> list[dict[str, Any]]:
    summary = subject.get("summary") if isinstance(subject.get("summary"), dict) else {}
    query = (
        select(AccessEvent)
        .options(*_access_event_load_options())
        .where(AccessEvent.occurred_at >= start, AccessEvent.occurred_at <= end)
        .order_by(AccessEvent.occurred_at.desc())
        .limit(50)
    )
    person_id = _uuid_from_value(summary.get("person_id"))
    vehicle_id = _uuid_from_value(summary.get("vehicle_id"))
    if vehicle_id:
        query = query.where(AccessEvent.vehicle_id == vehicle_id)
    elif person_id:
        query = query.where(AccessEvent.person_id == person_id)
    elif plates:
        query = query.where(AccessEvent.registration_number.in_(plates))
    if direction:
        query = query.where(AccessEvent.direction == AccessDirection(direction))
    events = (await session.scalars(query)).all()
    return [_incident_access_event_payload(event) for event in events]


def _incident_access_event_payload(event: AccessEvent) -> dict[str, Any]:
    person = event.vehicle.owner if event.vehicle and event.vehicle.owner else None
    return _compact_observation(
        {
            "id": str(event.id),
            "registration_number": event.registration_number,
            "person_id": str(event.person_id) if event.person_id else None,
            "vehicle_id": str(event.vehicle_id) if event.vehicle_id else None,
            "person": person.display_name if person else None,
            "direction": event.direction.value,
            "decision": event.decision.value,
            "source": event.source,
            "confidence": event.confidence,
            "occurred_at": event.occurred_at.isoformat(),
            "timing_classification": event.timing_classification.value,
            "telemetry_trace_id": _trace_id_from_access_event(event),
            "gate_observation": _gate_observation_from_event(event),
        }
    )


async def _incident_telemetry_traces(
    session,
    *,
    start: datetime,
    end: datetime,
    plates: list[str],
    timezone_name: str,
) -> list[dict[str, Any]]:
    traces = (
        await session.scalars(
            select(TelemetryTrace)
            .where(
                TelemetryTrace.started_at >= start,
                TelemetryTrace.started_at <= end,
                TelemetryTrace.category.in_(
                    [TELEMETRY_CATEGORY_WEBHOOKS_API, "lpr_telemetry", TELEMETRY_CATEGORY_ACCESS, TELEMETRY_CATEGORY_ALFRED]
                ),
            )
            .order_by(TelemetryTrace.started_at.desc())
            .limit(60)
        )
    ).all()
    records = []
    for trace in traces:
        if plates and trace.registration_number and trace.registration_number not in plates:
            continue
        records.append(_incident_trace_payload(trace, timezone_name))
    return records


def _incident_trace_payload(trace: TelemetryTrace, timezone_name: str) -> dict[str, Any]:
    context = trace.context if isinstance(trace.context, dict) else {}
    return _compact_observation(
        {
            "trace_id": trace.trace_id,
            "name": trace.name,
            "category": trace.category,
            "status": trace.status,
            "level": trace.level,
            "started_at": _agent_datetime_iso(trace.started_at, timezone_name),
            "started_at_display": _agent_datetime_display(trace.started_at, timezone_name),
            "summary": trace.summary,
            "registration_number": trace.registration_number,
            "path": context.get("path"),
            "status_code": context.get("status_code"),
            "user_agent": context.get("user_agent"),
            "request_id": context.get("request_id"),
        }
    )


async def _incident_audit_logs(session, *, start: datetime, end: datetime) -> list[dict[str, Any]]:
    logs = (
        await session.scalars(
            select(AuditLog)
            .where(AuditLog.timestamp >= start, AuditLog.timestamp <= end)
            .order_by(AuditLog.timestamp.desc())
            .limit(30)
        )
    ).all()
    return [
        _compact_observation(
            {
                "id": str(row.id),
                "timestamp": _agent_datetime_iso(row.timestamp, DEFAULT_AGENT_TIMEZONE),
                "action": row.action,
                "category": row.category,
                "actor": row.actor,
                "target_entity": row.target_entity,
                "target_label": row.target_label,
                "outcome": row.outcome,
                "trace_id": row.trace_id,
            }
        )
        for row in logs
    ]


async def _incident_gate_observations(session, *, start: datetime, end: datetime, timezone_name: str) -> list[dict[str, Any]]:
    observations = (
        await session.scalars(
            select(GateStateObservation)
            .where(GateStateObservation.observed_at >= start, GateStateObservation.observed_at <= end)
            .order_by(GateStateObservation.observed_at)
            .limit(40)
        )
    ).all()
    return [_gate_observation_payload(observation, timezone_name) for observation in observations]


def _gate_observation_payload(observation: GateStateObservation, timezone_name: str) -> dict[str, Any]:
    return _compact_observation(
        {
            "id": str(observation.id),
            "gate_entity_id": observation.gate_entity_id,
            "gate_name": observation.gate_name,
            "state": observation.state,
            "raw_state": observation.raw_state,
            "previous_state": observation.previous_state,
            "observed_at": _agent_datetime_iso(observation.observed_at, timezone_name),
            "observed_at_display": _agent_datetime_display(observation.observed_at, timezone_name),
            "source": observation.source,
        }
    )


async def _incident_anomalies(
    session,
    *,
    start: datetime,
    end: datetime,
    plates: list[str],
    timezone_name: str,
) -> list[dict[str, Any]]:
    anomalies = (
        await session.scalars(
            select(Anomaly)
            .options(selectinload(Anomaly.event))
            .where(Anomaly.created_at >= start, Anomaly.created_at <= end)
            .order_by(Anomaly.created_at.desc())
            .limit(30)
        )
    ).all()
    records = []
    for anomaly in anomalies:
        event = anomaly.event
        if plates and event and event.registration_number not in plates:
            continue
        records.append(
            _compact_observation(
                {
                    "id": str(anomaly.id),
                    "type": anomaly.anomaly_type.value,
                    "severity": anomaly.severity.value,
                    "message": anomaly.message,
                    "created_at": _agent_datetime_iso(anomaly.created_at, timezone_name),
                    "event_id": str(anomaly.event_id) if anomaly.event_id else None,
                    "registration_number": event.registration_number if event else None,
                    "resolved": bool(anomaly.resolved_at),
                }
            )
        )
    return records


async def _incident_schedule_diagnostics(
    session,
    *,
    subject: dict[str, Any],
    checked_at: datetime,
    timezone_name: str,
    default_policy: str,
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    vehicles = [item for item in subject.get("vehicles") or [] if isinstance(item, Vehicle)]
    person = subject.get("person") if isinstance(subject.get("person"), Person) else None
    for vehicle in vehicles:
        evaluation = await evaluate_vehicle_schedule(
            session,
            vehicle,
            checked_at,
            timezone_name=timezone_name,
            default_policy=default_policy,
        )
        diagnostics.append(_schedule_evaluation_payload(evaluation, f"vehicle {vehicle.registration_number}", timezone_name))
    if person and not vehicles:
        evaluation = await evaluate_person_schedule(
            session,
            person,
            checked_at,
            timezone_name=timezone_name,
            default_policy=default_policy,
        )
        diagnostics.append(_schedule_evaluation_payload(evaluation, person.display_name, timezone_name))
    return diagnostics


def _schedule_evaluation_payload(evaluation: Any, label: str, timezone_name: str) -> dict[str, Any]:
    return _compact_observation(
        {
            "target": label,
            "allowed": bool(evaluation.allowed),
            "source": evaluation.source,
            "schedule_id": str(evaluation.schedule_id) if evaluation.schedule_id else None,
            "schedule_name": evaluation.schedule_name,
            "reason": evaluation.reason,
            "override_id": str(evaluation.override_id) if evaluation.override_id else None,
            "override_ends_at": _agent_datetime_iso(evaluation.override_ends_at, timezone_name) if evaluation.override_ends_at else None,
        }
    )


async def _incident_notification_summary(session, *, incident_type: str) -> dict[str, Any]:
    rules = (
        await session.scalars(
            select(NotificationRule)
            .where(NotificationRule.is_active.is_(True))
            .order_by(NotificationRule.trigger_event, NotificationRule.name)
            .limit(100)
        )
    ).all()
    relevant_triggers = {
        "authorized_entry",
        "unauthorized_plate",
        "outside_schedule",
        "duplicate_entry",
        "duplicate_exit",
        "gate_open_failure",
        "garage_door_open_failure",
    }
    relevant = rules if incident_type == "notification_failure" else [rule for rule in rules if rule.trigger_event in relevant_triggers]
    return _compact_observation(
        {
            "active_rule_count": len(rules),
            "relevant_rule_count": len(relevant),
            "relevant_rules": [
                {
                    "id": str(rule.id),
                    "name": rule.name,
                    "trigger_event": rule.trigger_event,
                    "action_count": len(rule.actions or []),
                    "condition_count": len(rule.conditions or []),
                    "last_fired_at": rule.last_fired_at.isoformat() if rule.last_fired_at else None,
                }
                for rule in relevant[:12]
            ],
        }
    )


async def _query_protect_events_for_incident(
    *,
    start: datetime,
    end: datetime,
    timezone_name: str,
    plates: list[str],
    camera_id: str | None = None,
    camera_name: str | None = None,
    smart_detect_type: str | None = None,
    include_tracks: bool = True,
    limit: int = 50,
) -> dict[str, Any]:
    try:
        events = await get_unifi_protect_service().list_events(
            camera_id=camera_id,
            limit=limit,
            since=start,
            until=end,
        )
    except UnifiProtectError as exc:
        return {"available": False, "error": str(exc), "events": [], "count": 0}

    records: list[dict[str, Any]] = []
    matched_event: dict[str, Any] | None = None
    track_errors: list[dict[str, str]] = []
    for event in events:
        if not _protect_event_matches(event, camera_name=camera_name, smart_detect_type=smart_detect_type):
            continue
        record = _protect_event_payload(event, timezone_name)
        if include_tracks and event.get("id") and (plates or len(records) < 25):
            try:
                track = await get_unifi_protect_service().event_lpr_track(str(event["id"]))
                observations = [
                    _serialize_lpr_timing_observation(observation, timezone_name, include_payload_path=True)
                    for observation in track.get("observations", [])
                    if isinstance(observation, dict)
                ]
                record["track_observations"] = observations
                record["track_observation_count"] = len(observations)
                candidate = _best_track_candidate(observations, plates)
                if candidate:
                    record["matched_candidate"] = candidate
                    matched_event = matched_event or record
            except UnifiProtectError as exc:
                track_errors.append({"event_id": str(event.get("id") or ""), "error": str(exc)})
        if not matched_event and not plates and _event_looks_like_lpr(record):
            matched_event = record
        records.append(record)

    return _compact_observation(
        {
            "available": True,
            "events": records,
            "count": len(records),
            "matched_event": matched_event,
            "matched": bool(matched_event),
            "track_errors": track_errors,
            "window": {
                "start": _agent_datetime_iso(start, timezone_name),
                "end": _agent_datetime_iso(end, timezone_name),
            },
            "filters": {
                "camera_id": camera_id,
                "camera_name": camera_name,
                "smart_detect_type": smart_detect_type,
                "plates": plates,
            },
        }
    )


def _protect_event_matches(event: dict[str, Any], *, camera_name: str | None, smart_detect_type: str | None) -> bool:
    if camera_name:
        haystack = f"{event.get('camera_name') or ''} {event.get('camera_id') or ''}".lower()
        if camera_name.lower() not in haystack:
            return False
    if smart_detect_type:
        requested = re.sub(r"[^a-z0-9]+", "", smart_detect_type.lower())
        event_types = [
            re.sub(r"[^a-z0-9]+", "", str(item or "").lower())
            for item in event.get("smart_detect_types", [])
        ]
        if requested not in event_types:
            return False
    return True


def _protect_event_payload(event: dict[str, Any], timezone_name: str) -> dict[str, Any]:
    started = _datetime_from_agent_value(event.get("start"))
    ended = _datetime_from_agent_value(event.get("end"))
    return _compact_observation(
        {
            "id": event.get("id"),
            "type": event.get("type"),
            "camera_id": event.get("camera_id"),
            "camera_name": event.get("camera_name"),
            "start": _agent_datetime_iso(started, timezone_name) if started else event.get("start"),
            "start_display": _agent_datetime_display(started, timezone_name) if started else None,
            "end": _agent_datetime_iso(ended, timezone_name) if ended else event.get("end"),
            "score": event.get("score"),
            "smart_detect_types": event.get("smart_detect_types"),
            "metadata": _payload_summary(event.get("metadata")),
        }
    )


def _best_track_candidate(observations: list[dict[str, Any]], plates: list[str]) -> dict[str, Any] | None:
    best: tuple[float, dict[str, Any]] | None = None
    for observation in observations:
        candidate_plate = normalize_registration_number(str(observation.get("registration_number") or observation.get("raw_value") or ""))
        if not candidate_plate:
            continue
        score = max((_plate_match_score(candidate_plate, plate) for plate in plates), default=0.0) if plates else 1.0
        if score < 0.78:
            continue
        candidate = {
            "registration_number": candidate_plate,
            "raw_value": observation.get("raw_value"),
            "captured_at": observation.get("captured_at"),
            "confidence": observation.get("confidence"),
            "confidence_scale": observation.get("confidence_scale"),
            "score": round(score, 3),
            "source_detail": observation.get("source_detail"),
            "payload_path": observation.get("payload_path"),
        }
        if best is None or score > best[0]:
            best = (score, candidate)
    return best[1] if best else None


def _plate_match_score(candidate: str, expected: str) -> float:
    candidate = normalize_registration_number(candidate)
    expected = normalize_registration_number(expected)
    if not candidate or not expected:
        return 0.0
    if candidate == expected:
        return 1.0
    if candidate in expected or expected in candidate:
        return 0.9
    return SequenceMatcher(None, candidate, expected).ratio()


def _event_looks_like_lpr(event: dict[str, Any]) -> bool:
    types = " ".join(str(item or "").lower() for item in event.get("smart_detect_types", []))
    camera = f"{event.get('camera_name') or ''} {event.get('camera_id') or ''}".lower()
    return "license" in types or "plate" in types or "lpr" in camera or "license" in camera


def _incident_root_cause(
    *,
    found_iacs: bool,
    protect: dict[str, Any],
    traces: list[dict[str, Any]],
    incident_type: str,
) -> dict[str, str]:
    if found_iacs:
        if incident_type == "notification_failure":
            return {"root_cause": "iacs_event_found_check_notification_diagnostics", "confidence": "high"}
        if incident_type in {"gate_failure", "garage_failure"}:
            return {"root_cause": "iacs_event_found_check_hardware_diagnostics", "confidence": "high"}
        if incident_type == "schedule_denial":
            return {"root_cause": "iacs_event_found_check_schedule_diagnostics", "confidence": "high"}
        return {"root_cause": "iacs_event_found", "confidence": "high"}
    if not protect.get("available"):
        return {"root_cause": "protect_unavailable_partial_diagnosis", "confidence": "low"}
    if protect.get("matched_event"):
        webhook_traces = [trace for trace in traces if _trace_is_lpr_webhook(trace)]
        if not webhook_traces:
            return {"root_cause": "protect_lpr_detected_but_iacs_webhook_missing", "confidence": "high"}
        if any(int(trace.get("status_code") or 0) >= 400 for trace in webhook_traces):
            return {"root_cause": "iacs_webhook_received_error", "confidence": "high"}
        return {"root_cause": "iacs_webhook_seen_but_access_event_missing", "confidence": "medium"}
    if protect.get("events"):
        return {"root_cause": "protect_event_found_without_matching_lpr_candidate", "confidence": "medium"}
    return {"root_cause": "no_iacs_or_protect_event_found", "confidence": "low"}


def _trace_is_lpr_webhook(trace: dict[str, Any]) -> bool:
    path = str(trace.get("path") or "")
    name = str(trace.get("name") or "")
    return "/webhooks/ubiquiti/lpr" in path or "Webhook" in name


def _backfill_args_from_incident(
    *,
    subject: dict[str, Any],
    protect: dict[str, Any],
    arguments: dict[str, Any],
    root_cause: str,
) -> dict[str, Any] | None:
    if root_cause not in {
        "protect_lpr_detected_but_iacs_webhook_missing",
        "iacs_webhook_received_error",
        "iacs_webhook_seen_but_access_event_missing",
    }:
        return None
    event = protect.get("matched_event") if isinstance(protect.get("matched_event"), dict) else None
    if not event:
        return None
    candidate = event.get("matched_candidate") if isinstance(event.get("matched_candidate"), dict) else {}
    summary = subject.get("summary") if isinstance(subject.get("summary"), dict) else {}
    registration_number = normalize_registration_number(
        str(candidate.get("registration_number") or (summary.get("plates") or [None])[0] or arguments.get("registration_number") or "")
    )
    captured_at = candidate.get("captured_at") or event.get("start") or arguments.get("expected_time")
    if not registration_number or not captured_at:
        return None
    direction = _normalize(arguments.get("direction"))
    if direction not in {"entry", "exit", "denied"}:
        direction = "entry"
    decision = "denied" if direction == "denied" or not (summary.get("person_id") or summary.get("vehicle_id")) else "granted"
    return _compact_observation(
        {
            "protect_event_id": event.get("id"),
            "person_id": summary.get("person_id"),
            "vehicle_id": summary.get("vehicle_id"),
            "registration_number": registration_number,
            "captured_at": captured_at,
            "direction": direction,
            "decision": decision,
            "confidence": candidate.get("confidence"),
            "reason": f"Alfred incident remediation: {root_cause}",
        }
    )


def _iacs_vs_protect_summary(found_iacs: bool, protect: dict[str, Any], traces: list[dict[str, Any]]) -> dict[str, Any]:
    webhook_traces = [trace for trace in traces if _trace_is_lpr_webhook(trace)]
    return _compact_observation(
        {
            "iacs_access_event": "found" if found_iacs else "missing",
            "protect_event": "found" if protect.get("matched_event") else "not_found",
            "iacs_webhook_trace_count": len(webhook_traces),
            "comparison": (
                "Protect saw a matching LPR candidate but IACS has no access event."
                if protect.get("matched_event") and not found_iacs
                else "IACS has a matching access event."
                if found_iacs
                else "Neither IACS nor Protect produced matching durable evidence in the requested window."
            ),
        }
    )


def _incident_recommended_action(root: dict[str, Any], backfill_available: bool, protect: dict[str, Any]) -> dict[str, Any]:
    root_cause = str(root.get("root_cause") or "")
    if backfill_available:
        return {"type": "confirmed_backfill_available", "summary": "Confirm the prepared backfill to repair IACS history and presence."}
    if root_cause == "protect_lpr_detected_but_iacs_webhook_missing":
        return {
            "type": "external_alarm_manager_fix",
            "summary": "Fix UniFi Protect Alarm Manager delivery, then send a test.",
            "steps": [
                "Open UniFi Protect Alarm Manager and check the LPR alarm action webhook URL.",
                "Use the current IACS endpoint: /api/v1/webhooks/ubiquiti/lpr.",
                "Do not use retired /api/webhooks, /api/chat, or other non-versioned paths.",
                "Send a Protect Alarm Manager test and verify IACS Webhooks & API telemetry shows HTTP 202.",
            ],
        }
    if root_cause == "protect_unavailable_partial_diagnosis":
        return {"type": "restore_protect_diagnostics", "summary": str(protect.get("error") or "UniFi Protect was unavailable.")}
    if root_cause == "no_iacs_or_protect_event_found":
        return {"type": "camera_or_timing_check", "summary": "No durable IACS or Protect evidence was found; widen the time window or check camera/LPR zones."}
    return {"type": "diagnostic_follow_up", "summary": "Use the linked event diagnostics or external repair steps above."}


def _incident_timeline(
    *,
    iacs_events: list[dict[str, Any]],
    protect: dict[str, Any],
    traces: list[dict[str, Any]],
    gate_observations: list[dict[str, Any]],
    audit_logs: list[dict[str, Any]],
    anomalies: list[dict[str, Any]],
    timezone_name: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for event in iacs_events:
        items.append({"time": event.get("occurred_at"), "source": "iacs_access_event", "summary": f"{event.get('direction')} {event.get('decision')} {event.get('registration_number')}"})
    for event in protect.get("events") or []:
        items.append({"time": event.get("start"), "source": "unifi_protect", "summary": f"{event.get('camera_name')} {event.get('smart_detect_types')}"})
    for trace in traces:
        items.append({"time": trace.get("started_at"), "source": f"telemetry:{trace.get('category')}", "summary": trace.get("summary") or trace.get("name")})
    for observation in gate_observations:
        items.append({"time": observation.get("observed_at"), "source": "home_assistant_gate", "summary": f"{observation.get('gate_name') or observation.get('gate_entity_id')} {observation.get('state')}"})
    for anomaly in anomalies:
        items.append({"time": anomaly.get("created_at"), "source": "anomaly", "summary": anomaly.get("message")})
    for row in audit_logs:
        items.append({"time": row.get("timestamp"), "source": f"audit:{row.get('category')}", "summary": row.get("action")})

    def sort_key(item: dict[str, Any]) -> datetime:
        parsed = _datetime_from_agent_value(item.get("time"))
        return parsed or datetime.max.replace(tzinfo=UTC)

    return [
        _compact_observation(
            {
                **item,
                "time_display": _agent_datetime_display(sort_key(item), timezone_name) if sort_key(item).year < 9999 else None,
            }
        )
        for item in sorted(items, key=sort_key)[:80]
    ]


async def _backfill_candidate(session, arguments: dict[str, Any], timezone_name: str) -> dict[str, Any]:
    subject = await _resolve_incident_subject(session, arguments)
    summary = subject.get("summary") if isinstance(subject.get("summary"), dict) else {}
    plates = _incident_candidate_plates(subject, arguments)
    protect_event_id = str(arguments.get("protect_event_id") or "").strip()
    track_candidate: dict[str, Any] | None = None
    protect_event: dict[str, Any] | None = None
    if protect_event_id:
        try:
            track = await get_unifi_protect_service().event_lpr_track(protect_event_id)
            protect_event = _protect_event_payload(track.get("event") or {"id": protect_event_id}, timezone_name)
            observations = [
                _serialize_lpr_timing_observation(observation, timezone_name, include_payload_path=True)
                for observation in track.get("observations", [])
                if isinstance(observation, dict)
            ]
            track_candidate = _best_track_candidate(observations, plates) or (observations[0] if observations else None)
        except UnifiProtectError as exc:
            return {"error": str(exc)}

    registration_number = normalize_registration_number(
        str(arguments.get("registration_number") or (track_candidate or {}).get("registration_number") or (plates[0] if plates else ""))
    )
    if not registration_number:
        return {"error": "registration_number is required for an access event backfill."}

    captured_at = (
        _parse_incident_datetime(arguments.get("captured_at") or arguments.get("expected_time"), timezone_name, str(arguments.get("day") or "today"))
        or _datetime_from_agent_value((track_candidate or {}).get("captured_at"))
        or _datetime_from_agent_value((protect_event or {}).get("start"))
    )
    if not captured_at:
        return {"error": "captured_at or durable Protect track time is required for an access event backfill."}
    captured_at = captured_at.astimezone(UTC)
    gate_observation = await _nearest_gate_observation(session, captured_at, timezone_name)
    direction = _normalize(arguments.get("direction"))
    if direction not in {"entry", "exit", "denied"}:
        direction = _direction_from_gate_observation(gate_observation) or "entry"
    decision = _normalize(arguments.get("decision"))
    if decision not in {"granted", "denied"}:
        decision = "denied" if direction == "denied" or not (summary.get("person_id") or summary.get("vehicle_id")) else "granted"
    if decision == "granted" and not (summary.get("person_id") or summary.get("vehicle_id")):
        return {"error": "A granted backfill requires a resolved person or vehicle."}
    return {
        "label": summary.get("label") or registration_number,
        "person_id": _uuid_from_value(summary.get("person_id")),
        "vehicle_id": _uuid_from_value(summary.get("vehicle_id")),
        "registration_number": registration_number,
        "captured_at": captured_at,
        "direction": direction,
        "decision": decision,
        "confidence": _confidence_ratio(arguments.get("confidence") or (track_candidate or {}).get("confidence") or 0.99),
        "source": "unifi_protect_backfill" if protect_event_id else "alfred_backfill",
        "protect_event_id": protect_event_id or None,
        "camera_id": (protect_event or {}).get("camera_id") or (track_candidate or {}).get("camera_id"),
        "camera_name": (protect_event or {}).get("camera_name") or (track_candidate or {}).get("camera_name"),
        "track_candidate": track_candidate,
        "gate_observation": gate_observation,
        "reason": str(arguments.get("reason") or "Backfilled by Alfred from incident investigation").strip(),
    }


async def _nearest_gate_observation(session, captured_at: datetime, timezone_name: str) -> dict[str, Any] | None:
    observations = (
        await session.scalars(
            select(GateStateObservation)
            .where(
                GateStateObservation.observed_at >= captured_at - timedelta(minutes=5),
                GateStateObservation.observed_at <= captured_at + timedelta(minutes=5),
            )
            .order_by(GateStateObservation.observed_at)
            .limit(30)
        )
    ).all()
    if not observations:
        return None
    nearest = min(
        observations,
        key=lambda observation: abs((observation.observed_at.astimezone(UTC) - captured_at).total_seconds()),
    )
    return _gate_observation_payload(nearest, timezone_name)


def _direction_from_gate_observation(observation: dict[str, Any] | None) -> str | None:
    if not observation:
        return None
    state = str(observation.get("state") or "").lower()
    if state == "closed":
        return "entry"
    if state in {"open", "opening", "closing"}:
        return "exit"
    return None


def _confidence_ratio(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.99
    if confidence > 1:
        confidence = confidence / 100
    return max(0.0, min(1.0, confidence))


def _backfill_candidate_payload(candidate: dict[str, Any], timezone_name: str) -> dict[str, Any]:
    return _compact_observation(
        {
            "label": candidate.get("label"),
            "person_id": str(candidate.get("person_id")) if candidate.get("person_id") else None,
            "vehicle_id": str(candidate.get("vehicle_id")) if candidate.get("vehicle_id") else None,
            "registration_number": candidate.get("registration_number"),
            "captured_at": _agent_datetime_iso(candidate["captured_at"], timezone_name),
            "captured_at_display": _agent_datetime_display(candidate["captured_at"], timezone_name),
            "direction": candidate.get("direction"),
            "decision": candidate.get("decision"),
            "confidence": candidate.get("confidence"),
            "source": candidate.get("source"),
            "protect_event_id": candidate.get("protect_event_id"),
            "camera_name": candidate.get("camera_name"),
            "gate_observation": candidate.get("gate_observation"),
            "reason": candidate.get("reason"),
        }
    )


def _recognition_diagnostics(
    event: AccessEvent,
    trace: TelemetryTrace | None,
    spans: list[TelemetrySpan],
) -> dict[str, Any]:
    raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    debounce_payload = raw_payload.get("debounce") if isinstance(raw_payload.get("debounce"), dict) else {}
    candidates = debounce_payload.get("candidates") if isinstance(debounce_payload.get("candidates"), list) else []
    debounce_span = _find_span(spans, "Debounce & Confidence Aggregation")
    slowest_spans = sorted(
        [span for span in spans if span.duration_ms is not None],
        key=lambda span: float(span.duration_ms or 0),
        reverse=True,
    )[:5]
    total_ms = trace.duration_ms if trace else None
    debounce_ms = debounce_span.duration_ms if debounce_span else None
    processing_after_debounce_ms = (
        max(0.0, float(total_ms) - float(debounce_ms))
        if total_ms is not None and debounce_ms is not None
        else None
    )
    exact_known_match = any(
        isinstance(candidate, dict)
        and isinstance(candidate.get("known_vehicle_plate_match"), dict)
        and candidate["known_vehicle_plate_match"].get("exact") is True
        for candidate in candidates
    )
    likely_reason = "Telemetry for this access event was not found."
    if trace:
        slowest = slowest_spans[0] if slowest_spans else None
        if debounce_span and slowest and slowest.span_id == debounce_span.span_id and float(debounce_ms or 0) >= 500:
            likely_reason = "Most of the time was spent waiting in the LPR debounce/confidence window."
            if exact_known_match:
                likely_reason += " An exact known-plate match was present, so the burst should have short-circuited once that read arrived."
            elif len(candidates) > 1:
                likely_reason += " Multiple candidate reads were grouped before the final plate was selected."
            else:
                likely_reason += " Only one candidate was present, so this usually means the quiet-period timer had not expired yet."
        elif slowest:
            likely_reason = f"The slowest recorded step was {slowest.name}."
        else:
            likely_reason = "The trace did not contain any timed spans."

    return {
        "total_pipeline_ms": total_ms,
        "debounce_or_recognition_ms": debounce_ms,
        "processing_after_debounce_ms": processing_after_debounce_ms,
        "candidate_count": debounce_payload.get("candidate_count") or len(candidates),
        "selected_registration_number": event.registration_number,
        "exact_known_plate_match_seen": exact_known_match,
        "slowest_steps": [
            {
                "name": span.name,
                "duration_ms": span.duration_ms,
                "status": span.status,
                "error": span.error,
            }
            for span in slowest_spans
        ],
        "likely_delay_reason": likely_reason,
    }


def _gate_diagnostics(event: AccessEvent, spans: list[TelemetrySpan], timezone_name: str) -> dict[str, Any]:
    gate_observation = _gate_observation_from_event(event)
    gate_span = _find_span(spans, "Home Assistant Gate Open Command Sent")
    garage_spans = [span for span in spans if "Garage Door Command" in span.name]
    automatic_open_considered = (
        event.decision == AccessDecision.GRANTED and event.direction == AccessDirection.ENTRY
    )

    if not automatic_open_considered:
        reason = (
            "The gate was not opened because this event was not a granted entry."
            if event.decision != AccessDecision.GRANTED
            else "The gate was not opened because this event was classified as an exit/departure."
        )
    elif str(gate_observation.get("state") or "unknown") != "closed":
        reason = (
            "Automatic gate and garage-door commands are skipped unless the top gate "
            "was closed at plate-read time."
        )
    elif gate_span is None:
        reason = "The event qualified for an automatic gate open, but no gate command span was recorded."
    elif gate_span.status == "ok" and (gate_span.output_payload or {}).get("accepted") is not False:
        reason = "The automatic gate open command was accepted."
    else:
        output = gate_span.output_payload or {}
        reason = str(gate_span.error or output.get("detail") or "The gate open command failed.")

    return {
        "automatic_open_considered": automatic_open_considered,
        "gate_observation": gate_observation,
        "gate_command": _span_diagnostic_payload(gate_span, timezone_name) if gate_span else None,
        "garage_commands": [
            _span_diagnostic_payload(span, timezone_name)
            for span in garage_spans
        ],
        "outcome_reason": reason,
    }


async def _notification_diagnostics_for_event(
    session,
    event: AccessEvent,
    person: dict[str, str] | None,
    trace_id: str | None,
    spans: list[TelemetrySpan],
    timezone_name: str,
) -> dict[str, Any]:
    triggers = _expected_notification_triggers(event, spans)
    if not triggers:
        return {
            "expected_triggers": [],
            "trigger_diagnostics": [],
            "delivery_records": [],
            "summary": "No notification trigger was expected for this event.",
        }

    rules = (
        await session.scalars(
            select(NotificationRule)
            .where(NotificationRule.trigger_event.in_([trigger["event_type"] for trigger in triggers]))
            .order_by(NotificationRule.trigger_event, NotificationRule.created_at)
        )
    ).all()
    notification_service = get_notification_service()
    delivery_records = _notification_delivery_records(spans, timezone_name)
    trigger_rows: list[dict[str, Any]] = []
    for trigger in triggers:
        trigger_rules = [rule for rule in rules if rule.trigger_event == trigger["event_type"]]
        active_rules = [rule for rule in trigger_rules if rule.is_active]
        context = _notification_context_for_access_event(event, person, trigger, trace_id, timezone_name)
        rule_rows: list[dict[str, Any]] = []
        for rule in trigger_rules:
            conditions_matched = None
            condition_error = None
            if rule.is_active:
                try:
                    conditions_matched = await notification_service.conditions_match(rule, context)
                except Exception as exc:
                    conditions_matched = False
                    condition_error = str(exc)
            rule_rows.append(
                {
                    "id": str(rule.id),
                    "name": rule.name,
                    "is_active": rule.is_active,
                    "conditions": rule.conditions or [],
                    "conditions_matched": conditions_matched,
                    "condition_error": condition_error,
                    "action_count": len(rule.actions or []),
                }
            )

        matching_delivery_records = [
            record for record in delivery_records if record.get("event_type") == trigger["event_type"]
        ]
        if matching_delivery_records:
            conclusion = "A persisted notification delivery record exists for this trigger."
        elif not active_rules:
            conclusion = "No active notification workflow currently matches this trigger."
        elif not any(row.get("conditions_matched") for row in rule_rows if row.get("is_active")):
            conclusion = "Active workflows exist, but their conditions do not currently match this event context."
        else:
            conclusion = (
                "An active workflow appears eligible, but no persisted delivery span was found. "
                "Older events may predate notification delivery telemetry, or delivery may have only appeared in realtime logs."
            )
        trigger_rows.append(
            {
                **trigger,
                "workflow_count": len(trigger_rules),
                "active_workflow_count": len(active_rules),
                "workflows": rule_rows,
                "delivery_records": matching_delivery_records,
                "conclusion": conclusion,
            }
        )

    return {
        "expected_triggers": triggers,
        "trigger_diagnostics": trigger_rows,
        "delivery_records": delivery_records,
        "summary": "; ".join(row["conclusion"] for row in trigger_rows),
    }


def _expected_notification_triggers(event: AccessEvent, spans: list[TelemetrySpan]) -> list[dict[str, Any]]:
    triggers: list[dict[str, Any]] = []
    for anomaly in event.anomalies:
        triggers.append(
            {
                "event_type": anomaly.anomaly_type.value,
                "severity": anomaly.severity.value,
                "subject": event.registration_number,
                "reason": anomaly.message,
            }
        )

    gate_span = _find_span(spans, "Home Assistant Gate Open Command Sent")
    if gate_span and gate_span.status == "error":
        triggers.append(
            {
                "event_type": "gate_open_failed",
                "severity": "critical",
                "subject": event.registration_number,
                "reason": str(gate_span.error or (gate_span.output_payload or {}).get("detail") or "Gate command failed."),
            }
        )
    if (
        event.decision == AccessDecision.GRANTED
        and event.direction == AccessDirection.ENTRY
        and gate_span
        and gate_span.status == "ok"
        and (gate_span.output_payload or {}).get("accepted") is not False
    ):
        triggers.append(
            {
                "event_type": "authorized_entry",
                "severity": "info",
                "subject": event.registration_number,
                "reason": "Granted entry and automatic gate open was accepted.",
            }
        )
    return triggers


def _notification_context_for_access_event(
    event: AccessEvent,
    person: dict[str, str] | None,
    trigger: dict[str, Any],
    trace_id: str | None,
    timezone_name: str,
) -> NotificationContext:
    facts = {
        "message": str(trigger.get("reason") or ""),
        "display_name": person.get("display_name") if person else "",
        "group_name": person.get("group") if person else "",
        "registration_number": event.registration_number,
        "vehicle_registration_number": event.registration_number,
        "direction": event.direction.value,
        "decision": event.decision.value,
        "source": event.source,
        "timing_classification": event.timing_classification.value,
        "occurred_at": _agent_datetime_iso(event.occurred_at, timezone_name),
        "access_event_id": str(event.id),
        "telemetry_trace_id": trace_id or "",
    }
    return NotificationContext(
        event_type=str(trigger["event_type"]),
        subject=str(trigger.get("subject") or event.registration_number),
        severity=str(trigger.get("severity") or "info"),
        facts={key: "" if value is None else str(value) for key, value in facts.items()},
    )


def _notification_delivery_records(spans: list[TelemetrySpan], timezone_name: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for span in spans:
        if not span.name.startswith("Notification "):
            continue
        output = span.output_payload or {}
        records.append(
            {
                "name": span.name,
                "status": span.status,
                "event_type": output.get("event_type"),
                "rule_id": output.get("rule_id"),
                "rule_name": output.get("rule_name"),
                "channel": output.get("channel"),
                "reason": output.get("reason"),
                "delivered": output.get("delivered"),
                "error": span.error or output.get("error"),
                "occurred_at": _agent_datetime_iso(span.started_at, timezone_name),
            }
        )
    return records


async def _registration_history_summary(
    session,
    registration_number: str,
    *,
    timezone_name: str,
    period: str,
    limit: int,
) -> dict[str, Any]:
    normalized = normalize_registration_number(registration_number)
    conditions: list[Any] = [AccessEvent.registration_number == normalized]
    if period != "all":
        start, end = _period_bounds(period, timezone_name)
        conditions.extend([AccessEvent.occurred_at >= start, AccessEvent.occurred_at <= end])

    total_count = int(
        await session.scalar(select(func.count()).select_from(AccessEvent).where(*conditions))
        or 0
    )
    granted_count = int(
        await session.scalar(
            select(func.count())
            .select_from(AccessEvent)
            .where(*conditions, AccessEvent.decision == AccessDecision.GRANTED)
        )
        or 0
    )
    denied_count = int(
        await session.scalar(
            select(func.count())
            .select_from(AccessEvent)
            .where(*conditions, AccessEvent.decision == AccessDecision.DENIED)
        )
        or 0
    )
    bounds = (
        await session.execute(
            select(func.min(AccessEvent.occurred_at), func.max(AccessEvent.occurred_at))
            .where(*conditions)
        )
    ).one()
    recent_events = (
        await session.scalars(
            select(AccessEvent)
            .options(selectinload(AccessEvent.anomalies))
            .where(*conditions)
            .order_by(AccessEvent.occurred_at.desc())
            .limit(limit)
        )
    ).all()
    return {
        "registration_number": normalized,
        "total_count": total_count,
        "granted_count": granted_count,
        "denied_count": denied_count,
        "first_seen_at": _agent_datetime_iso(bounds[0], timezone_name) if bounds[0] else None,
        "first_seen_at_display": _agent_datetime_display(bounds[0], timezone_name) if bounds[0] else None,
        "last_seen_at": _agent_datetime_iso(bounds[1], timezone_name) if bounds[1] else None,
        "last_seen_at_display": _agent_datetime_display(bounds[1], timezone_name) if bounds[1] else None,
        "recent_events": [
            {
                "id": str(row.id),
                "direction": row.direction.value,
                "decision": row.decision.value,
                "occurred_at": _agent_datetime_iso(row.occurred_at, timezone_name),
                "occurred_at_display": _agent_datetime_display(row.occurred_at, timezone_name),
                "anomaly_count": len(row.anomalies),
            }
            for row in recent_events
        ],
    }


async def _lpr_timing_near_event(event: AccessEvent, timezone_name: str) -> dict[str, Any]:
    observations = await get_lpr_timing_recorder().recent(limit=300)
    event_at = event.occurred_at if event.occurred_at.tzinfo else event.occurred_at.replace(tzinfo=UTC)
    event_at = event_at.astimezone(UTC)
    registration_number = normalize_registration_number(event.registration_number)
    nearby: list[dict[str, Any]] = []
    for observation in observations:
        observed_plate = normalize_registration_number(
            str(observation.get("registration_number") or observation.get("raw_value") or "")
        )
        if observed_plate != registration_number:
            continue
        received_at = _datetime_from_agent_value(observation.get("received_at"))
        captured_at = _datetime_from_agent_value(observation.get("captured_at"))
        comparison_at = captured_at or received_at
        if comparison_at and abs((comparison_at - event_at).total_seconds()) > 120:
            continue
        serialized = _serialize_lpr_timing_observation(observation, timezone_name)
        serialized["ms_from_access_event_time"] = (
            round((received_at - event_at).total_seconds() * 1000, 1) if received_at else None
        )
        nearby.append(serialized)
    return {
        "observations": nearby[:20],
        "count": len(nearby),
        "note": "This feed is in-memory and only covers recent observations since the backend started.",
    }


def _serialize_lpr_timing_observation(
    observation: dict[str, Any],
    timezone_name: str,
    *,
    include_payload_path: bool = False,
) -> dict[str, Any]:
    received_at = _datetime_from_agent_value(observation.get("received_at"))
    captured_at = _datetime_from_agent_value(observation.get("captured_at"))
    delay_ms = (
        round((received_at - captured_at).total_seconds() * 1000, 1)
        if received_at and captured_at
        else None
    )
    payload = {
        "id": observation.get("id"),
        "source": observation.get("source"),
        "source_detail": observation.get("source_detail"),
        "registration_number": observation.get("registration_number"),
        "raw_value": observation.get("raw_value"),
        "candidate_kind": observation.get("candidate_kind"),
        "received_at": _agent_datetime_iso(received_at, timezone_name) if received_at else observation.get("received_at"),
        "captured_at": _agent_datetime_iso(captured_at, timezone_name) if captured_at else observation.get("captured_at"),
        "captured_to_received_ms": delay_ms,
        "event_id": observation.get("event_id"),
        "camera_id": observation.get("camera_id"),
        "camera_name": observation.get("camera_name"),
        "confidence": observation.get("confidence"),
        "confidence_scale": observation.get("confidence_scale"),
        "protect_action": observation.get("protect_action"),
        "protect_model": observation.get("protect_model"),
    }
    if include_payload_path:
        payload["payload_path"] = observation.get("payload_path")
    return _compact_observation(payload)


def _gate_observation_from_event(event: AccessEvent) -> dict[str, Any]:
    raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    direction_resolution = (
        raw_payload.get("direction_resolution")
        if isinstance(raw_payload.get("direction_resolution"), dict)
        else {}
    )
    gate_observation = direction_resolution.get("gate_observation")
    if isinstance(gate_observation, dict):
        return gate_observation
    best_payload = raw_payload.get("best") if isinstance(raw_payload.get("best"), dict) else {}
    value = best_payload.get(GATE_OBSERVATION_PAYLOAD_KEY)
    return value if isinstance(value, dict) else {}


def _find_span(spans: list[TelemetrySpan], name: str) -> TelemetrySpan | None:
    return next((span for span in spans if span.name == name), None)


def _diagnostic_answer_hints(
    recognition: dict[str, Any],
    gate: dict[str, Any],
    notifications: dict[str, Any],
) -> list[str]:
    hints = [str(recognition.get("likely_delay_reason") or "").strip()]
    gate_reason = str(gate.get("outcome_reason") or "").strip()
    if gate_reason:
        hints.append(gate_reason)
    notification_summary = str(notifications.get("summary") or "").strip()
    if notification_summary:
        hints.append(notification_summary)
    return [hint for hint in hints if hint]


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _datetime_from_agent_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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


def _leaderboard_search_text(arguments: dict[str, Any]) -> str:
    registration = str(arguments.get("registration_number") or "").strip()
    if registration:
        return normalize_registration_number(registration).lower()
    return _person_match_key(
        " ".join(
            str(arguments.get(key) or "").strip()
            for key in ("search", "person")
            if str(arguments.get(key) or "").strip()
        )
    )


def _leaderboard_known_matches(row: dict[str, Any], requested: str) -> bool:
    if not requested:
        return True
    person = row.get("person") if isinstance(row.get("person"), dict) else {}
    vehicle = row.get("vehicle") if isinstance(row.get("vehicle"), dict) else {}
    haystack = _person_match_key(
        " ".join(
            str(value or "")
            for value in [
                row.get("registration_number"),
                row.get("first_name"),
                row.get("display_name"),
                row.get("vehicle_name"),
                person.get("first_name"),
                person.get("last_name"),
                person.get("display_name"),
                vehicle.get("registration_number"),
                vehicle.get("make"),
                vehicle.get("model"),
                vehicle.get("color"),
                vehicle.get("description"),
                vehicle.get("display_name"),
            ]
        )
    )
    return requested in haystack


def _leaderboard_unknown_matches(row: dict[str, Any], requested: str) -> bool:
    if not requested:
        return True
    dvla = row.get("dvla") if isinstance(row.get("dvla"), dict) else {}
    display_vehicle = dvla.get("display_vehicle") if isinstance(dvla.get("display_vehicle"), dict) else {}
    haystack = _person_match_key(
        " ".join(
            str(value or "")
            for value in [
                row.get("registration_number"),
                dvla.get("label"),
                display_vehicle.get("make"),
                display_vehicle.get("model"),
                display_vehicle.get("colour"),
                display_vehicle.get("color"),
            ]
        )
    )
    return requested in haystack


def _person_match_key(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


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
        compacted = [
            _compact_value(item, key=key, depth=depth + 1, max_depth=max_depth, max_list_items=max_list_items)
            for item in value[:max_list_items]
        ]
        if len(value) > max_list_items:
            compacted.append({"omitted_items": len(value) - max_list_items})
        return compacted
    if isinstance(value, dict):
        if depth >= max_depth:
            return {"type": "object", "key_count": len(value), "keys": list(map(str, value.keys()))[:20]}
        items = list(value.items())
        compacted = {
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
            compacted["omitted_keys"] = len(items) - max_dict_keys
        return compacted
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


def _compact_notification_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    actions = workflow.get("actions") if isinstance(workflow.get("actions"), list) else []
    conditions = workflow.get("conditions") if isinstance(workflow.get("conditions"), list) else []
    return _compact_observation(
        {
            "id": workflow.get("id"),
            "name": workflow.get("name"),
            "trigger_event": workflow.get("trigger_event"),
            "is_active": workflow.get("is_active"),
            "condition_count": len(conditions),
            "action_count": len(actions),
            "channels": [
                action.get("channel") or action.get("type")
                for action in actions
                if isinstance(action, dict)
            ],
            "last_fired_at": workflow.get("last_fired_at"),
            "title_template": workflow.get("title_template"),
            "message_template": workflow.get("message_template"),
        }
    )


async def _resolve_schedule(session, arguments: dict[str, Any]) -> Schedule | None:
    schedule_id = _uuid_from_value(arguments.get("schedule_id"))
    if schedule_id:
        return await session.get(Schedule, schedule_id)

    schedule_name = _normalize(arguments.get("schedule_name") or arguments.get("name"))
    if not schedule_name:
        return None
    schedules = (await session.scalars(select(Schedule).order_by(Schedule.name))).all()
    exact = [schedule for schedule in schedules if schedule.name.lower() == schedule_name]
    if exact:
        return exact[0]
    partial = [schedule for schedule in schedules if schedule_name in schedule.name.lower()]
    return partial[0] if len(partial) == 1 else None


async def _resolve_person(session, arguments: dict[str, Any]) -> Person | None:
    person_id = _uuid_from_value(arguments.get("entity_id") or arguments.get("person_id"))
    if person_id:
        return await session.scalar(
            select(Person)
            .options(selectinload(Person.schedule), selectinload(Person.group))
            .where(Person.id == person_id)
        )

    person_name = _normalize(
        arguments.get("entity_name")
        or arguments.get("person")
        or arguments.get("person_name")
        or arguments.get("name")
    )
    if not person_name:
        return None
    people = (
        await session.scalars(
            select(Person)
            .options(selectinload(Person.schedule), selectinload(Person.group))
            .order_by(Person.display_name)
        )
    ).all()
    exact = [person for person in people if person.display_name.lower() == person_name]
    if exact:
        return exact[0]
    partial = [
        person
        for person in people
        if _person_record_matches(
            {"display_name": person.display_name, "group": person.group.name if person.group else ""},
            person_name,
        )
    ]
    return partial[0] if len(partial) == 1 else None


async def _resolve_vehicle(session, arguments: dict[str, Any]) -> Vehicle | None:
    vehicle_id = _uuid_from_value(arguments.get("entity_id") or arguments.get("vehicle_id"))
    query = select(Vehicle).options(
        selectinload(Vehicle.schedule),
        selectinload(Vehicle.owner).selectinload(Person.schedule),
    )
    if vehicle_id:
        return await session.scalar(query.where(Vehicle.id == vehicle_id))

    registration_number = str(arguments.get("registration_number") or "").strip()
    if registration_number:
        normalized = normalize_registration_number(registration_number)
        return await session.scalar(query.where(Vehicle.registration_number == normalized))
    return None


async def _load_vehicle_with_schedule(session, vehicle_id: UUID) -> Vehicle | None:
    return await session.scalar(
        select(Vehicle)
        .options(
            selectinload(Vehicle.schedule),
            selectinload(Vehicle.owner).selectinload(Person.schedule),
        )
        .where(Vehicle.id == vehicle_id)
    )


async def _load_person_with_schedule(session, person_id: UUID) -> Person | None:
    return await session.scalar(
        select(Person)
        .options(selectinload(Person.schedule), selectinload(Person.group))
        .where(Person.id == person_id)
    )


def _serialize_schedule_for_agent(schedule: Schedule) -> dict[str, Any]:
    time_blocks = normalize_time_blocks(schedule.time_blocks)
    return {
        "id": str(schedule.id),
        "name": schedule.name,
        "description": schedule.description,
        "time_blocks": time_blocks,
        "summary": _schedule_summary(time_blocks),
        "created_at": _agent_datetime_iso(schedule.created_at),
        "created_at_display": _agent_datetime_display(schedule.created_at),
        "updated_at": _agent_datetime_iso(schedule.updated_at),
        "updated_at_display": _agent_datetime_display(schedule.updated_at),
    }


def _serialize_person_schedule_target(person: Person) -> dict[str, Any]:
    return {
        "id": str(person.id),
        "name": person.display_name,
        "group": person.group.name if person.group else None,
        "schedule_id": str(person.schedule_id) if person.schedule_id else None,
        "schedule_name": person.schedule.name if person.schedule else None,
        "is_active": person.is_active,
    }


def _serialize_vehicle_schedule_target(vehicle: Vehicle) -> dict[str, Any]:
    owner_schedule_id = vehicle.owner.schedule_id if vehicle.owner else None
    owner_schedule_name = vehicle.owner.schedule.name if vehicle.owner and vehicle.owner.schedule else None
    return {
        "id": str(vehicle.id),
        "registration_number": vehicle.registration_number,
        "owner": vehicle.owner.display_name if vehicle.owner else None,
        "schedule_id": str(vehicle.schedule_id) if vehicle.schedule_id else None,
        "schedule_name": vehicle.schedule.name if vehicle.schedule else None,
        "inherits_from_owner": vehicle.schedule_id is None and owner_schedule_id is not None,
        "owner_schedule_id": str(owner_schedule_id) if owner_schedule_id else None,
        "owner_schedule_name": owner_schedule_name,
        "is_active": vehicle.is_active,
    }


async def _schedule_door_targets(*, entity_type: str, search: str) -> list[dict[str, Any]]:
    config = await get_runtime_config()
    schedule_names = await _schedule_name_map()
    targets: list[dict[str, Any]] = []
    for kind, entities in _cover_entities_by_kind(config).items():
        if entity_type not in {"", "all", "door", kind}:
            continue
        for entity in entities:
            label = f"{entity.get('entity_id')} {entity.get('name')}".lower()
            if search and search not in label:
                continue
            payload = cover_entity_state_payload(entity)
            schedule_id = payload.get("schedule_id")
            targets.append(
                {
                    **payload,
                    "kind": kind,
                    "schedule_name": schedule_names.get(str(schedule_id)) if schedule_id else None,
                }
            )
    return targets


async def _schedule_name_map() -> dict[str, str]:
    async with AsyncSessionLocal() as session:
        schedules = (await session.scalars(select(Schedule))).all()
    return {str(schedule.id): schedule.name for schedule in schedules}


async def _assign_schedule_to_cover(arguments: dict[str, Any], *, schedule_id: str | None) -> dict[str, Any]:
    entity_type = _normalize(arguments.get("entity_type"))
    target = await _resolve_cover_target(arguments, entity_type=entity_type)
    if not target:
        return {"assigned": False, "error": "Door/gate target not found."}

    config = await get_runtime_config()
    setting_key = str(target["setting_key"])
    existing_entities = (
        list(config.home_assistant_gate_entities)
        if setting_key == "home_assistant_gate_entities"
        else list(config.home_assistant_garage_door_entities)
    )
    updated_entities: list[dict[str, Any]] = []
    for entity in existing_entities:
        updated = dict(entity)
        if str(updated.get("entity_id")) == str(target["entity"]["entity_id"]):
            updated["schedule_id"] = schedule_id
        updated_entities.append(updated)

    await update_settings({setting_key: updated_entities})
    refreshed = await _resolve_cover_target(arguments, entity_type=entity_type)
    return {
        "assigned": True,
        "entity_type": refreshed["kind"] if refreshed else target["kind"],
        "door": refreshed["entity"] if refreshed else {**target["entity"], "schedule_id": schedule_id},
    }


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


def _schedule_summary(time_blocks: dict[str, list[dict[str, str]]]) -> str:
    selected_slots = 0
    active_days = 0
    for intervals in time_blocks.values():
        day_slots = 0
        for interval in intervals:
            start = _parse_schedule_minute(interval["start"])
            end = _parse_schedule_minute(interval["end"])
            day_slots += max(0, (end - start) // 30)
        if day_slots:
            active_days += 1
            selected_slots += day_slots
    if not selected_slots:
        return "No allowed time"
    if selected_slots == 48 * 7:
        return "24/7"
    hours = selected_slots / 2
    display_hours = int(hours) if hours.is_integer() else round(hours, 1)
    return f"{display_hours}h across {active_days} day{'s' if active_days != 1 else ''}"


def _schedule_has_allowed_time(time_blocks: dict[str, list[dict[str, str]]]) -> bool:
    return any(bool(intervals) for intervals in time_blocks.values())


def _time_blocks_from_agent_arguments(arguments: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    raw_time_blocks = arguments.get("time_blocks")
    natural_text = _natural_schedule_text_from_arguments(arguments)
    if natural_text:
        parsed = _parse_natural_schedule_time_blocks(natural_text)
        if parsed:
            return normalize_time_blocks(parsed)
    return normalize_time_blocks(raw_time_blocks)


def _natural_schedule_text_from_arguments(arguments: dict[str, Any]) -> str:
    return " ".join(
        str(arguments.get(key) or "").strip()
        for key in ("time_description", "description")
        if str(arguments.get(key) or "").strip()
    ).strip()


def _parse_natural_schedule_time_blocks(text: str) -> dict[str, list[dict[str, str]]] | None:
    lower = text.lower()
    if any(token in lower for token in ["24/7", "24-7", "24 hours", "all day every day"]):
        return {str(day): [{"start": "00:00", "end": "24:00"}] for day in range(7)}

    days = _natural_schedule_days(lower)
    time_range = _natural_schedule_time_range(lower)
    if not days or not time_range:
        return None

    start, end = time_range
    blocks = {str(day): [] for day in range(7)}
    for day in days:
        blocks[str(day)].append({"start": start, "end": end})
    return blocks


def _natural_schedule_days(lower: str) -> list[int]:
    if any(phrase in lower for phrase in ["weekday", "week day", "workday", "work day"]):
        return list(range(5))
    if any(phrase in lower for phrase in ["weekend", "saturday and sunday", "sat and sun"]):
        return [5, 6]
    if any(phrase in lower for phrase in ["every day", "daily", "all week", "each day", "mon-sun", "monday to sunday"]):
        return list(range(7))

    range_match = re.search(
        rf"\b({NATURAL_SCHEDULE_DAY_PATTERN})\b"
        r"\s*(?:-|to|through|until|thru)\s*"
        rf"\b({NATURAL_SCHEDULE_DAY_PATTERN})\b",
        lower,
    )
    if range_match:
        start = _natural_schedule_day_index(range_match.group(1))
        end = _natural_schedule_day_index(range_match.group(2))
        if start is not None and end is not None:
            if start <= end:
                return list(range(start, end + 1))
            return list(range(start, 7)) + list(range(0, end + 1))

    days: list[int] = []
    for token in re.findall(rf"\b({NATURAL_SCHEDULE_DAY_PATTERN})\b", lower):
        day = _natural_schedule_day_index(token)
        if day is not None and day not in days:
            days.append(day)
    return days


def _natural_schedule_day_index(value: str) -> int | None:
    normalized = re.sub(r"(?:'s|s)$", "", value.lower())
    if normalized == "wed":
        normalized = "wednesday"
    return NATURAL_SCHEDULE_DAY_ALIASES.get(normalized, NATURAL_SCHEDULE_DAY_ALIASES.get(normalized[:3]))


def _natural_schedule_time_range(lower: str) -> tuple[str, str] | None:
    match = re.search(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:-|to|until|through|thru)\s*"
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
        lower,
    )
    if not match:
        return None

    start = _natural_schedule_minute(match.group(1), match.group(2), match.group(3))
    end = _natural_schedule_minute(match.group(4), match.group(5), match.group(6))
    if start is None or end is None:
        return None
    if end <= start and not match.group(3) and not match.group(6) and int(match.group(4)) <= 12:
        end += 12 * 60
    if start < 0 or end > 24 * 60 or end <= start:
        return None
    if start % 30 or end % 30:
        return None
    return _format_natural_schedule_minute(start), _format_natural_schedule_minute(end)


def _natural_schedule_minute(hour_text: str, minute_text: str | None, meridiem: str | None) -> int | None:
    hour = int(hour_text)
    minute = int(minute_text or "0")
    if minute not in {0, 30}:
        return None
    if meridiem:
        if hour < 1 or hour > 12:
            return None
        if meridiem == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
    if hour < 0 or hour > 24:
        return None
    return hour * 60 + minute


def _format_natural_schedule_minute(minute: int) -> str:
    if minute == 24 * 60:
        return "24:00"
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _parse_schedule_minute(value: str) -> int:
    if value in {"24:00", "23:59"}:
        return 24 * 60
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _visitor_pass_statuses_from_arguments(arguments: dict[str, Any]) -> list[VisitorPassStatus] | None:
    raw = arguments.get("statuses")
    if raw is None and arguments.get("status"):
        raw = [arguments.get("status")]
    if isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",")]
    if not isinstance(raw, list):
        return None
    statuses: list[VisitorPassStatus] = []
    for item in raw:
        normalized = str(item or "").strip().lower()
        try:
            status = VisitorPassStatus(normalized)
        except ValueError:
            continue
        if status not in statuses:
            statuses.append(status)
    return statuses or None


def _visitor_pass_type_from_arguments(value: Any) -> VisitorPassType:
    normalized = str(value or VisitorPassType.ONE_TIME.value).strip().lower().replace("_", "-")
    try:
        return VisitorPassType(normalized)
    except ValueError:
        return VisitorPassType.ONE_TIME


def _visitor_pass_agent_payload(visitor_pass: VisitorPass, timezone_name: str) -> dict[str, Any]:
    payload = serialize_visitor_pass(visitor_pass, timezone_name=timezone_name)
    vehicle_summary_parts = [
        payload.get("vehicle_colour"),
        payload.get("vehicle_make"),
    ]
    vehicle_summary = " ".join(str(part) for part in vehicle_summary_parts if part)
    if payload.get("number_plate"):
        vehicle_summary = f"{vehicle_summary} - {payload['number_plate']}".strip(" -")
    payload["expected_time_display"] = _agent_datetime_display(visitor_pass.expected_time, timezone_name)
    if payload.get("valid_from") and payload.get("valid_until"):
        payload["window_summary"] = f"{payload['window_start']} to {payload['window_end']}"
    else:
        payload["window_summary"] = f"+/- {visitor_pass.window_minutes} minutes"
    payload["vehicle_summary"] = vehicle_summary or None
    if payload.get("duration_human"):
        payload["visit_summary"] = f"On site for {payload['duration_human']}"
    elif payload.get("arrival_time") and not payload.get("departure_time"):
        payload["visit_summary"] = "Arrived, departure not recorded yet"
    else:
        payload["visit_summary"] = None
    return _compact_observation(payload)


async def _resolve_visitor_pass_for_agent(
    session,
    arguments: dict[str, Any],
    *,
    editable_only: bool = False,
) -> VisitorPass | dict[str, Any]:
    service = get_visitor_pass_service()
    await service.refresh_statuses(session=session, publish=False)
    pass_id = _uuid_from_value(arguments.get("pass_id") or arguments.get("visitor_pass_id"))
    if pass_id:
        visitor_pass = await service.get_pass(session, pass_id)
        if not visitor_pass:
            return {"found": False, "error": "Visitor Pass not found."}
        if editable_only and visitor_pass.status not in {VisitorPassStatus.ACTIVE, VisitorPassStatus.SCHEDULED}:
            return {
                "found": True,
                "changed": False,
                "error": f"{visitor_pass.status.value.title()} visitor passes cannot be changed.",
                "visitor_pass": _visitor_pass_agent_payload(visitor_pass, DEFAULT_AGENT_TIMEZONE),
            }
        return visitor_pass

    visitor_name = str(arguments.get("visitor_name") or arguments.get("search") or "").strip()
    if not visitor_name:
        return {
            "found": False,
            "requires_details": True,
            "detail": "Which visitor pass should I use?",
        }
    statuses = [VisitorPassStatus.ACTIVE, VisitorPassStatus.SCHEDULED] if editable_only else None
    matches = await service.list_passes(session, statuses=statuses, search=visitor_name, limit=10)
    exact = [pass_ for pass_ in matches if pass_.visitor_name.casefold() == visitor_name.casefold()]
    candidates = exact or matches
    if not candidates:
        return {"found": False, "error": f"I could not find a Visitor Pass for {visitor_name}."}
    if len(candidates) > 1:
        return {
            "found": True,
            "ambiguous": True,
            "error": f"I found more than one Visitor Pass matching {visitor_name}.",
            "matches": [_visitor_pass_agent_payload(pass_, DEFAULT_AGENT_TIMEZONE) for pass_ in candidates[:5]],
        }
    return candidates[0]


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


def _device_state_record(entity: dict[str, Any], kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": str(entity.get("name") or entity.get("entity_id") or ""),
        "entity_id": str(entity.get("entity_id") or ""),
        "state": entity.get("state") or "unknown",
        "enabled": bool(entity.get("enabled", True)),
        "schedule_id": entity.get("schedule_id"),
    }


def _device_matches_target(device: dict[str, Any], target: str) -> bool:
    aliases = {
        "main gate": "top gate",
        "top gate": "main gate",
        "mums garage": "mums garage door",
        "mum garage": "mums garage door",
    }
    haystack = f"{device.get('name', '')} {device.get('entity_id', '')} {device.get('kind', '')}".lower()
    candidates = {target, aliases.get(target, "")}
    return any(candidate and candidate in haystack for candidate in candidates)


async def _resolve_openable_device(
    arguments: dict[str, Any],
    *,
    kind_filter: str,
) -> dict[str, Any] | None:
    target_text = str(arguments.get("target") or arguments.get("entity_id") or "").strip()
    resolve_args = {
        **arguments,
        "entity_id": arguments.get("entity_id") or target_text,
        "entity_name": arguments.get("entity_name") or arguments.get("target") or arguments.get("name") or target_text,
        "target": target_text,
    }
    if kind_filter in {"gate", "garage_door"}:
        return await _resolve_cover_target(resolve_args, entity_type=kind_filter)

    preferred_order = ["garage_door", "gate"] if "garage" in target_text.lower() else ["gate", "garage_door"]
    for entity_type in preferred_order:
        target = await _resolve_cover_target(resolve_args, entity_type=entity_type)
        if target:
            return target
    return None


def _agent_device_payload(target: dict[str, Any]) -> dict[str, Any]:
    entity = target["entity"]
    return {
        "kind": target["kind"],
        "entity_id": str(entity["entity_id"]),
        "name": str(entity.get("name") or entity["entity_id"]),
    }


def _agent_device_audit_payload(
    target: dict[str, Any],
    *,
    action: str,
    accepted: bool,
    state: str,
    detail: str | None,
    user_id: str,
    session_id: str,
) -> dict[str, Any]:
    device = _agent_device_payload(target)
    payload = {
        "source": "alfred",
        "requested_by": "agent",
        "user_id": user_id or None,
        "session_id": session_id or None,
        "kind": device["kind"],
        "entity_id": device["entity_id"],
        "name": device["name"],
        "action": action,
        "accepted": accepted,
        "state": state,
        "detail": detail,
    }
    if action == "open":
        payload["opened_by"] = "agent"
    elif action == "close":
        payload["closed_by"] = "agent"
    return payload


def _log_extra(payload: dict[str, Any]) -> dict[str, Any]:
    extra = dict(payload)
    if "name" in extra:
        extra["device_name"] = extra.pop("name")
    return extra


def _filename_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "file"


def _simple_pdf(lines: list[str]) -> bytes:
    escaped_lines = [_pdf_escape(line) for line in lines[:46]]
    content_lines = [
        "BT",
        "/F1 16 Tf",
        "72 760 Td",
        "20 TL",
    ]
    for index, line in enumerate(escaped_lines):
        if index:
            content_lines.append("T*")
        content_lines.append(f"({line}) Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = io.BytesIO()
    output.write(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(output.tell())
        output.write(f"{index} 0 obj\n".encode("ascii"))
        output.write(obj)
        output.write(b"\nendobj\n")
    xref_offset = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.write(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return output.getvalue()


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


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


def _period_bounds(day: str, timezone_name: str = DEFAULT_AGENT_TIMEZONE) -> tuple[datetime, datetime]:
    now = _agent_now(timezone_name)
    if day == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.astimezone(UTC), now.astimezone(UTC)
    if day == "yesterday":
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return (today - timedelta(days=1)).astimezone(UTC), today.astimezone(UTC)
    return (now - timedelta(days=14)).astimezone(UTC), now.astimezone(UTC)


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def _human_duration(duration: timedelta) -> str:
    seconds = int(duration.total_seconds())
    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"
