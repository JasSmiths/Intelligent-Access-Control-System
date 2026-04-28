import csv
import io
import re
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import AccessEvent, Anomaly, NotificationRule, Person, Presence, Schedule, User, Vehicle
from app.models.enums import AccessDecision, AccessDirection
from app.ai.providers import ImageAnalysisUnsupportedError, analyze_image_with_provider
from app.modules.home_assistant.client import HomeAssistantClient
from app.modules.home_assistant.covers import command_cover, cover_entity_state_payload
from app.modules.dvla.vehicle_enquiry import DvlaVehicleEnquiryError, display_vehicle_record, normalize_registration_number
from app.modules.unifi_protect.client import UnifiProtectError
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError
from app.services.chat_attachments import ChatAttachmentError, chat_attachment_store
from app.services.dvla import lookup_vehicle_registration, normalize_vehicle_enquiry_response
from app.services.event_bus import event_bus
from app.services.home_assistant import get_home_assistant_service
from app.services.leaderboard import get_leaderboard_service
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
    evaluate_vehicle_schedule,
    normalize_time_blocks,
    schedule_dependencies,
    schedule_allows_at,
)
from app.services.settings import get_runtime_config, update_settings
from app.services.unifi_protect import get_unifi_protect_service

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
CHAT_TOOL_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("chat_tool_context", default={})
logger = get_logger(__name__)
DEFAULT_AGENT_TIMEZONE = "Europe/London"

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
    "description": "Workflow action. type is mobile for Apprise, voice for Home Assistant TTS, or in_app for dashboard alerts.",
    "properties": {
        "id": {"type": "string"},
        "type": {"type": "string", "enum": ["mobile", "voice", "in_app"]},
        "target_mode": {"type": "string", "enum": ["all", "many", "selected"]},
        "target_ids": {"type": "array", "items": {"type": "string"}},
        "title_template": {"type": "string", "description": "Title template supporting @ variables such as @FirstName."},
        "message_template": {"type": "string", "description": "Message template supporting @ variables such as @VehicleName."},
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


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def as_llm_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


def build_agent_tools() -> dict[str, AgentTool]:
    tools = [
        AgentTool(
            name="query_presence",
            description="Return current presence state for everyone or a named person.",
            parameters={
                "type": "object",
                "properties": {"person": {"type": "string"}},
                "additionalProperties": False,
            },
            handler=query_presence,
        ),
        AgentTool(
            name="query_device_states",
            description="Return current states for configured gates, doors, and garage doors.",
            parameters={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Optional friendly device name, for example Top Gate, Back Door, or Main Garage Door.",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["all", "gate", "door", "garage_door"],
                        "description": "Optional device kind filter.",
                    },
                },
                "additionalProperties": False,
            },
            handler=query_device_states,
        ),
        AgentTool(
            name="open_device",
            description="Open a configured gate or garage door. This is a real-world side effect and requires confirm=true.",
            parameters={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Friendly device name, for example Top Gate or Main Garage Door.",
                    },
                    "entity_id": {"type": "string", "description": "Optional internal device identifier when already known."},
                    "kind": {
                        "type": "string",
                        "enum": ["all", "gate", "garage_door"],
                        "description": "Optional openable device kind filter.",
                    },
                    "reason": {"type": "string", "description": "Human-readable audit reason."},
                    "confirm": {
                        "type": "boolean",
                        "description": "Must be true before the device will be opened.",
                    },
                },
                "required": ["confirm"],
                "additionalProperties": False,
            },
            handler=open_device,
        ),
        AgentTool(
            name="query_access_events",
            description="Return recent access events, optionally filtered by person, group/category, plate, or day.",
            parameters={
                "type": "object",
                "properties": {
                    "person": {"type": "string"},
                    "group": {"type": "string"},
                    "registration_number": {"type": "string"},
                    "day": {"type": "string", "enum": ["today", "yesterday", "recent"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "additionalProperties": False,
            },
            handler=query_access_events,
        ),
        AgentTool(
            name="query_leaderboard",
            description="Return the Top Charts leaderboard for known VIP plates and denied unknown Mystery Guest plates.",
            parameters={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["all", "known", "unknown", "top_known"],
                        "description": "Which Top Charts section to return.",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "enrich_unknowns": {
                        "type": "boolean",
                        "description": "Whether to include live DVLA enrichment for unknown plates.",
                    },
                    "search": {
                        "type": "string",
                        "description": "Optional person, vehicle, or plate text to filter leaderboard rows.",
                    },
                    "person": {"type": "string", "description": "Optional known person name to filter VIP rows."},
                    "registration_number": {"type": "string", "description": "Optional plate to filter known or unknown rows."},
                },
                "additionalProperties": False,
            },
            handler=query_leaderboard,
        ),
        AgentTool(
            name="query_anomalies",
            description="Return recent anomaly records and unresolved alerts.",
            parameters={
                "type": "object",
                "properties": {
                    "severity": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "additionalProperties": False,
            },
            handler=query_anomalies,
        ),
        AgentTool(
            name="summarize_access_rhythm",
            description="Summarize arrivals, exits, denials, and anomalies for a recent period.",
            parameters={
                "type": "object",
                "properties": {"day": {"type": "string", "enum": ["today", "yesterday", "recent"]}},
                "additionalProperties": False,
            },
            handler=summarize_access_rhythm,
        ),
        AgentTool(
            name="calculate_visit_duration",
            description="Calculate how long a person or group stayed on site today or recently.",
            parameters={
                "type": "object",
                "properties": {
                    "person": {"type": "string"},
                    "group": {"type": "string"},
                    "day": {"type": "string", "enum": ["today", "yesterday", "recent"]},
                },
                "additionalProperties": False,
            },
            handler=calculate_visit_duration,
        ),
        AgentTool(
            name="trigger_anomaly_alert",
            description="Send a contextual anomaly alert notification.",
            parameters={
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                    "message": {"type": "string"},
                },
                "required": ["subject", "severity", "message"],
                "additionalProperties": False,
            },
            handler=trigger_anomaly_alert,
        ),
        AgentTool(
            name="get_system_users",
            description="Return non-sensitive system account roster for access-control context.",
            parameters={
                "type": "object",
                "properties": {"include_inactive": {"type": "boolean"}},
                "additionalProperties": False,
            },
            handler=get_system_users,
        ),
        AgentTool(
            name="lookup_dvla_vehicle",
            description="Look up UK vehicle details from the DVLA Vehicle Enquiry Service by registration number.",
            parameters={
                "type": "object",
                "properties": {
                    "registration_number": {
                        "type": "string",
                        "description": "Vehicle registration number without spaces or punctuation.",
                    },
                },
                "required": ["registration_number"],
                "additionalProperties": False,
            },
            handler=lookup_dvla_vehicle,
        ),
        AgentTool(
            name="analyze_camera_snapshot",
            description="Fetch a current UniFi Protect camera snapshot and ask the active vision-capable AI provider to analyze it.",
            parameters={
                "type": "object",
                "properties": {
                    "camera_id": {"type": "string"},
                    "camera_name": {"type": "string"},
                    "prompt": {
                        "type": "string",
                        "description": "What to inspect in the snapshot.",
                    },
                    "provider": {"type": "string"},
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
            handler=analyze_camera_snapshot,
        ),
        AgentTool(
            name="read_chat_attachment",
            description="Read text from a user-uploaded chat document, or analyze an uploaded image with the active vision-capable provider.",
            parameters={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "Chat attachment file ID returned by upload."},
                    "prompt": {
                        "type": "string",
                        "description": "Question or extraction goal for the attachment.",
                    },
                    "provider": {"type": "string"},
                },
                "required": ["file_id"],
                "additionalProperties": False,
            },
            handler=read_chat_attachment,
        ),
        AgentTool(
            name="export_presence_report_csv",
            description="Generate a CSV export of current presence and recent access events, then return a secure chat download link.",
            parameters={
                "type": "object",
                "properties": {
                    "day": {"type": "string", "enum": ["today", "yesterday", "recent"]},
                    "person": {"type": "string"},
                    "group": {"type": "string"},
                },
                "additionalProperties": False,
            },
            handler=export_presence_report_csv,
        ),
        AgentTool(
            name="generate_contractor_invoice_pdf",
            description="Generate a simple PDF contractor visit invoice from calculated visit duration and return a secure chat download link.",
            parameters={
                "type": "object",
                "properties": {
                    "contractor_name": {"type": "string"},
                    "day": {"type": "string", "enum": ["today", "yesterday", "recent"]},
                    "hourly_rate": {"type": "number", "minimum": 0},
                    "currency": {"type": "string"},
                },
                "additionalProperties": False,
            },
            handler=generate_contractor_invoice_pdf,
        ),
        AgentTool(
            name="get_camera_snapshot",
            description="Fetch a current UniFi Protect camera snapshot and return it as a secure chat image attachment.",
            parameters={
                "type": "object",
                "properties": {
                    "camera_id": {"type": "string"},
                    "camera_name": {"type": "string"},
                },
                "additionalProperties": False,
            },
            handler=get_camera_snapshot,
        ),
        AgentTool(
            name="query_notification_catalog",
            description="Return notification workflow building blocks: trigger events, variables, delivery integrations, endpoints, and mock preview context.",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=query_notification_catalog,
        ),
        AgentTool(
            name="query_notification_workflows",
            description="List DB-backed notification workflows, optionally filtered by trigger, active status, or search text.",
            parameters={
                "type": "object",
                "properties": {
                    "trigger_event": {"type": "string"},
                    "is_active": {"type": "boolean"},
                    "search": {"type": "string"},
                    "include_preview": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            handler=query_notification_workflows,
        ),
        AgentTool(
            name="get_notification_workflow",
            description="Get one notification workflow by ID or name, including normalized conditions/actions and rendered preview.",
            parameters={
                "type": "object",
                "properties": {
                    **NOTIFICATION_RULE_LOOKUP_PROPERTIES,
                    "include_preview": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            handler=get_notification_workflow,
        ),
        AgentTool(
            name="create_notification_workflow",
            description="Create a DB-backed notification workflow. Requires confirm=true because future matching events may send real notifications.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "trigger_event": {"type": "string"},
                    "conditions": {"type": "array", "items": NOTIFICATION_CONDITION_SCHEMA},
                    "actions": {"type": "array", "items": NOTIFICATION_ACTION_SCHEMA},
                    "is_active": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                },
                "required": ["name", "trigger_event", "actions", "confirm"],
                "additionalProperties": False,
            },
            handler=create_notification_workflow,
        ),
        AgentTool(
            name="update_notification_workflow",
            description="Edit an existing notification workflow. Requires confirm=true because future matching events may send changed notifications.",
            parameters={
                "type": "object",
                "properties": {
                    **NOTIFICATION_RULE_LOOKUP_PROPERTIES,
                    "name": {"type": "string"},
                    "trigger_event": {"type": "string"},
                    "conditions": {"type": "array", "items": NOTIFICATION_CONDITION_SCHEMA},
                    "actions": {"type": "array", "items": NOTIFICATION_ACTION_SCHEMA},
                    "is_active": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                },
                "required": ["confirm"],
                "additionalProperties": False,
            },
            handler=update_notification_workflow,
        ),
        AgentTool(
            name="delete_notification_workflow",
            description="Delete a notification workflow by ID or name. Requires confirm=true.",
            parameters={
                "type": "object",
                "properties": {
                    **NOTIFICATION_RULE_LOOKUP_PROPERTIES,
                    "confirm": {"type": "boolean"},
                },
                "required": ["confirm"],
                "additionalProperties": False,
            },
            handler=delete_notification_workflow,
        ),
        AgentTool(
            name="preview_notification_workflow",
            description="Render a saved or unsaved notification workflow with mock or supplied context without sending anything.",
            parameters={
                "type": "object",
                "properties": {
                    **NOTIFICATION_RULE_LOOKUP_PROPERTIES,
                    "rule": NOTIFICATION_RULE_PAYLOAD_SCHEMA,
                    "context": {
                        "type": "object",
                        "description": "Optional notification context payload/facts used for variable rendering.",
                        "additionalProperties": True,
                    },
                },
                "additionalProperties": False,
            },
            handler=preview_notification_workflow,
        ),
        AgentTool(
            name="test_notification_workflow",
            description="Verify a saved or unsaved workflow by sending its actions through configured providers. Requires confirm_send=true because this sends real test notifications.",
            parameters={
                "type": "object",
                "properties": {
                    **NOTIFICATION_RULE_LOOKUP_PROPERTIES,
                    "rule": NOTIFICATION_RULE_PAYLOAD_SCHEMA,
                    "context": {
                        "type": "object",
                        "description": "Optional notification context payload/facts used for the test.",
                        "additionalProperties": True,
                    },
                    "confirm_send": {"type": "boolean"},
                },
                "required": ["confirm_send"],
                "additionalProperties": False,
            },
            handler=test_notification_workflow,
        ),
        AgentTool(
            name="query_schedules",
            description="List reusable access schedules, optionally filtered by name/description and with dependency counts.",
            parameters={
                "type": "object",
                "properties": {
                    "search": {"type": "string"},
                    "include_dependencies": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            handler=query_schedules,
        ),
        AgentTool(
            name="get_schedule",
            description="Get one schedule by ID or name, including normalized time blocks and current People/Vehicle/Door dependencies.",
            parameters={
                "type": "object",
                "properties": SCHEDULE_LOOKUP_PROPERTIES,
                "additionalProperties": False,
            },
            handler=get_schedule,
        ),
        AgentTool(
            name="create_schedule",
            description="Create a reusable weekly access schedule. Days are 0=Monday through 6=Sunday. Use time_blocks when confident, or time_description for natural language such as Wednesdays and Fridays 6am to 7pm. Do not call this until the user has supplied at least one allowed day/time block or time description.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "time_description": {
                        "type": "string",
                        "description": "Natural-language allowed time, for example Wednesdays and Fridays 6am to 7pm.",
                    },
                    "time_blocks": SCHEDULE_TIME_BLOCKS_SCHEMA,
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            handler=create_schedule,
        ),
        AgentTool(
            name="update_schedule",
            description="Edit a schedule's name, description, or weekly time blocks. Unspecified fields are preserved.",
            parameters={
                "type": "object",
                "properties": {
                    **SCHEDULE_LOOKUP_PROPERTIES,
                    "name": {"type": "string"},
                    "description": {"type": "string", "description": "Use an empty string to clear the description."},
                    "time_description": {
                        "type": "string",
                        "description": "Natural-language replacement allowed time, for example weekdays 08:00-17:00.",
                    },
                    "time_blocks": SCHEDULE_TIME_BLOCKS_SCHEMA,
                },
                "additionalProperties": False,
            },
            handler=update_schedule,
        ),
        AgentTool(
            name="delete_schedule",
            description="Delete a schedule only when it has no People, Vehicle, Gate, or Garage Door dependencies. Requires confirm=true.",
            parameters={
                "type": "object",
                "properties": {
                    **SCHEDULE_LOOKUP_PROPERTIES,
                    "confirm": {"type": "boolean"},
                },
                "required": ["confirm"],
                "additionalProperties": False,
            },
            handler=delete_schedule,
        ),
        AgentTool(
            name="query_schedule_targets",
            description="List People, Vehicles, Gates, and Garage Doors with their current schedule assignment so an agent can choose valid targets.",
            parameters={
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "enum": ["all", "person", "vehicle", "gate", "garage_door", "door"],
                    },
                    "search": {"type": "string"},
                },
                "additionalProperties": False,
            },
            handler=query_schedule_targets,
        ),
        AgentTool(
            name="assign_schedule_to_entity",
            description="Assign or clear a schedule for a Person, Vehicle, Gate, or Garage Door. Vehicles with a cleared schedule inherit from owner.",
            parameters={
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "enum": ["person", "vehicle", "gate", "garage_door", "door"],
                    },
                    "entity_id": {"type": "string", "description": "Person/Vehicle UUID or Home Assistant cover entity ID."},
                    "entity_name": {"type": "string", "description": "Person display name or door label."},
                    "registration_number": {"type": "string", "description": "Vehicle registration number."},
                    "schedule_id": {"type": "string"},
                    "schedule_name": {"type": "string"},
                    "clear_schedule": {"type": "boolean"},
                },
                "required": ["entity_type"],
                "additionalProperties": False,
            },
            handler=assign_schedule_to_entity,
        ),
        AgentTool(
            name="verify_schedule_access",
            description="Verify whether a schedule or scheduled entity allows access at a specific date/time, honoring vehicle owner inheritance and default policy.",
            parameters={
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "enum": ["schedule", "person", "vehicle", "gate", "garage_door", "door"],
                    },
                    **SCHEDULE_LOOKUP_PROPERTIES,
                    "entity_id": {"type": "string", "description": "Person/Vehicle UUID or Home Assistant cover entity ID."},
                    "entity_name": {"type": "string", "description": "Person display name or door label."},
                    "registration_number": {"type": "string", "description": "Vehicle registration number."},
                    "at": {"type": "string", "description": "ISO datetime. If omitted, uses now."},
                },
                "required": ["entity_type"],
                "additionalProperties": False,
            },
            handler=verify_schedule_access,
        ),
    ]
    return {tool.name: tool for tool in tools}


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


async def open_device(arguments: dict[str, Any]) -> dict[str, Any]:
    context = get_chat_tool_context()
    user_id = str(context.get("user_id") or "")
    session_id = str(context.get("session_id") or "")
    target_text = str(arguments.get("target") or arguments.get("entity_id") or "").strip()
    kind_filter = _normalize(arguments.get("kind") or "all")
    if kind_filter not in {"", "all", "gate", "garage_door"}:
        return {"opened": False, "error": "kind must be all, gate, or garage_door."}
    if not target_text:
        return {
            "opened": False,
            "requires_details": True,
            "detail": "Which gate or garage door should I open?",
        }

    target = await _resolve_openable_device(arguments, kind_filter=kind_filter or "all")
    if not target:
        return {
            "opened": False,
            "target": target_text,
            "error": f"I could not find a configured gate or garage door called {target_text}.",
        }

    if not bool(arguments.get("confirm")):
        device = _agent_device_payload(target)
        return {
            "opened": False,
            "requires_confirmation": True,
            "target": device["name"],
            "device": device,
            "detail": (
                "Opening gates and garage doors is a real-world action. "
                "Use the chat confirmation action before I open it."
            ),
        }

    config = await get_runtime_config()
    now = datetime.now(tz=UTC)
    async with AsyncSessionLocal() as session:
        schedule_evaluation = await evaluate_schedule_id(
            session,
            target["entity"].get("schedule_id"),
            now,
            timezone_name=config.site_timezone,
            default_policy=config.schedule_default_policy,
            source=str(target["kind"]),
        )
    if not schedule_evaluation.allowed:
        detail = schedule_evaluation.reason or "Device is outside its assigned schedule."
        payload = _agent_device_audit_payload(
            target,
            accepted=False,
            state="schedule_denied",
            detail=detail,
            user_id=user_id,
            session_id=session_id,
        )
        await event_bus.publish("agent.device_open_failed", payload)
        logger.warning("agent_device_open_schedule_denied", extra=payload)
        return {
            "opened": False,
            "accepted": False,
            "device": _agent_device_payload(target),
            "state": "schedule_denied",
            "detail": detail,
            "opened_by": "agent",
        }

    reason = str(arguments.get("reason") or "").strip()
    audit_reason = reason or f"Alfred agent requested opening {target['entity'].get('name') or target['entity']['entity_id']}"
    outcome = await command_cover(HomeAssistantClient(), target["entity"], "open", f"Alfred agent: {audit_reason}")
    audit_payload = _agent_device_audit_payload(
        target,
        accepted=outcome.accepted,
        state=outcome.state,
        detail=outcome.detail,
        user_id=user_id,
        session_id=session_id,
    )
    audit_payload["reason"] = audit_reason
    await event_bus.publish(
        "agent.device_open_requested" if outcome.accepted else "agent.device_open_failed",
        audit_payload,
    )
    await event_bus.publish(
        f"{target['kind']}.open_requested" if outcome.accepted else f"{target['kind']}.open_failed",
        {
            **audit_payload,
            "opened_by": "agent",
            "source": "alfred",
        },
    )
    if outcome.accepted:
        logger.info("agent_device_open_requested", extra=audit_payload)
    else:
        logger.error("agent_device_open_failed", extra=audit_payload)

    return {
        "opened": outcome.accepted,
        "accepted": outcome.accepted,
        "device": _agent_device_payload(target),
        "action": "open",
        "state": outcome.state,
        "detail": outcome.detail,
        "opened_by": "agent",
        "audit_event": "agent.device_open_requested" if outcome.accepted else "agent.device_open_failed",
    }


async def query_access_events(arguments: dict[str, Any]) -> dict[str, Any]:
    limit = int(arguments.get("limit") or 25)
    config = await get_runtime_config()
    start, end = _period_bounds(arguments.get("day") or "recent", config.site_timezone)

    async with AsyncSessionLocal() as session:
        query = (
            select(AccessEvent)
            .options(selectinload(AccessEvent.vehicle), selectinload(AccessEvent.anomalies))
            .where(AccessEvent.occurred_at >= start, AccessEvent.occurred_at <= end)
            .order_by(AccessEvent.occurred_at.desc())
            .limit(limit)
        )
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
        records.append(
            {
                "person": person["display_name"] if person else None,
                "group": person.get("group") if person else None,
                "registration_number": event.registration_number,
                "direction": event.direction.value,
                "decision": event.decision.value,
                "confidence": event.confidence,
                "occurred_at": _agent_datetime_iso(event.occurred_at, config.site_timezone),
                "occurred_at_display": _agent_datetime_display(event.occurred_at, config.site_timezone),
                "anomaly_count": len(event.anomalies),
            }
        )

    return {"events": records, "count": len(records), "timezone": config.site_timezone}


async def query_leaderboard(arguments: dict[str, Any]) -> dict[str, Any]:
    scope = _normalize(arguments.get("scope") or "all")
    if scope not in {"", "all", "known", "unknown", "top_known"}:
        return {"error": "scope must be all, known, unknown, or top_known."}
    scope = scope or "all"

    try:
        limit = int(arguments.get("limit") or 25)
    except (TypeError, ValueError):
        limit = 25
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
    limit = int(arguments.get("limit") or 25)
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

    async with AsyncSessionLocal() as session:
        rules = (
            await session.scalars(
                select(NotificationRule).order_by(NotificationRule.created_at.desc(), NotificationRule.name)
            )
        ).all()

    workflows: list[dict[str, Any]] = []
    for rule in rules:
        if trigger_filter and rule.trigger_event != trigger_filter:
            continue
        if isinstance(active_filter, bool) and rule.is_active is not active_filter:
            continue
        haystack = f"{rule.name} {rule.trigger_event}".lower()
        if search and search not in haystack:
            continue
        workflow = _serialize_notification_rule_for_agent(rule)
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

    name = str(arguments.get("name") or "").strip()
    trigger_event = str(arguments.get("trigger_event") or "").strip()
    actions = normalize_actions(arguments.get("actions"))
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
            conditions=normalize_conditions(arguments.get("conditions")),
            actions=actions,
            is_active=arguments.get("is_active", True) is not False,
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
            rule.trigger_event = trigger_event
        if "conditions" in arguments:
            rule.conditions = normalize_conditions(arguments.get("conditions"))
        if "actions" in arguments:
            actions = normalize_actions(arguments.get("actions"))
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
            ]
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
            ]

    doors = await _schedule_door_targets(entity_type=entity_type, search=search) if include_doors else []
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
            evaluation = await evaluate_schedule_id(
                session,
                person.schedule_id,
                occurred_at,
                timezone_name=config.site_timezone,
                default_policy=config.schedule_default_policy,
                source="person",
            )
            return {
                "verified": True,
                "entity_type": "person",
                "person": person.display_name,
                "allowed": evaluation.allowed,
                "source": evaluation.source,
                "schedule_id": str(evaluation.schedule_id) if evaluation.schedule_id else None,
                "schedule_name": evaluation.schedule_name,
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
    accepted: bool,
    state: str,
    detail: str | None,
    user_id: str,
    session_id: str,
) -> dict[str, Any]:
    device = _agent_device_payload(target)
    return {
        "source": "alfred",
        "opened_by": "agent",
        "user_id": user_id or None,
        "session_id": session_id or None,
        "kind": device["kind"],
        "entity_id": device["entity_id"],
        "name": device["name"],
        "action": "open",
        "accepted": accepted,
        "state": state,
        "detail": detail,
    }


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
    timezone = _agent_timezone(timezone_name)
    label = getattr(timezone, "key", DEFAULT_AGENT_TIMEZONE)
    return f"{_agent_datetime(value, label).strftime('%d %b %Y, %H:%M')} {label}"


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
