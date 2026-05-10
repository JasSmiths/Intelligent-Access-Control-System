"""Alfred tool catalog entries for this domain."""

from __future__ import annotations

from app.ai.tools import AgentTool
from app.ai.tool_groups.visitor_passes_handlers import (
    cancel_visitor_pass,
    create_visitor_pass,
    get_visitor_pass,
    query_visitor_passes,
    trigger_icloud_sync,
    update_visitor_pass,
)
from app.ai.tool_groups.metadata import apply_group_metadata


TOOL_CATEGORIES = {
    "query_visitor_passes": ("Visitor_Passes", "Access_Logs", "General"),
    "get_visitor_pass": ("Visitor_Passes", "Access_Logs"),
    "create_visitor_pass": ("Visitor_Passes",),
    "update_visitor_pass": ("Visitor_Passes",),
    "cancel_visitor_pass": ("Visitor_Passes",),
    "trigger_icloud_sync": ("Calendar_Integrations", "Visitor_Passes"),
}

CONFIRMATION_REQUIRED_TOOLS = {
    "cancel_visitor_pass",
    "create_visitor_pass",
    "trigger_icloud_sync",
    "update_visitor_pass",
}

DEFAULT_LIMITS = {"query_visitor_passes": 20}


def build_tools() -> list[AgentTool]:
    return apply_group_metadata(
        [
        AgentTool(
                    name="query_visitor_passes",
                    description=(
                        "List Visitor Pass records and their linked arrival/departure telemetry. "
                        "Use this for expected visitors and follow-ups such as what car a visitor arrived in."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "statuses": {
                                "type": "array",
                                "items": {"type": "string", "enum": ["active", "scheduled", "used", "expired", "cancelled"]},
                            },
                            "status": {"type": "string", "enum": ["active", "scheduled", "used", "expired", "cancelled"]},
                            "search": {"type": "string", "description": "Visitor name, plate, make, or colour text."},
                            "visitor_name": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                            "fuzzy_name": {
                                "type": "boolean",
                                "description": "When true, include close visitor-name matches for duplicate checks.",
                            },
                        },
                        "additionalProperties": False,
                    },
                    handler=query_visitor_passes,
                    example_inputs=(
                        {"visitor_name": "Chris", "limit": 5},
                        {"search": "blue van", "statuses": ["used"], "limit": 10},
                    ),
                    return_schema={
                        "answer_types": ["visitor_pass", "visitor_arrival", "visitor_departure", "visitor_duration"],
                        "records": "visitor_passes",
                    },
                ),
        AgentTool(
                    name="get_visitor_pass",
                    description="Get one Visitor Pass by ID or visitor name, including linked vehicle details and visit duration.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "pass_id": {"type": "string"},
                            "visitor_name": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                    handler=get_visitor_pass,
                    example_inputs=(
                        {"visitor_name": "Chris"},
                    ),
                    return_schema={
                        "answer_types": ["visitor_pass", "visitor_arrival", "visitor_departure", "visitor_duration"],
                        "records": "visitor_pass",
                    },
                ),
        AgentTool(
                    name="create_visitor_pass",
                    description=(
                        "Create a one-shot Visitor Pass for an expected unknown vehicle. "
                        "For one-time passes, do not call until visitor_name and expected_time are known. "
                        "For duration passes, valid_from and valid_until are required; visitor_phone is optional. "
                        "Use local site time silently and default to a +/- 30 minute window when window_minutes is omitted. "
                        "Do not resolve visitor_name as a Person record. Requires confirm=true."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "visitor_name": {"type": "string"},
                            "pass_type": {"type": "string", "enum": ["one-time", "duration"]},
                            "visitor_phone": {
                                "type": ["string", "null"],
                                "description": "Optional visitor WhatsApp/mobile number. Use full international format when known.",
                            },
                            "number_plate": {
                                "type": ["string", "null"],
                                "description": "Optional visitor vehicle registration when the host already knows it.",
                            },
                            "expected_time": {"type": "string", "description": "Expected local or ISO datetime for the visitor."},
                            "window_minutes": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 1440,
                                "description": "Minutes before and after expected_time. Defaults to 30.",
                            },
                            "valid_from": {"type": "string", "description": "Start datetime for duration Visitor Passes."},
                            "valid_until": {"type": "string", "description": "End datetime for duration Visitor Passes."},
                            "confirm": {"type": "boolean"},
                        },
                        "required": ["visitor_name", "confirm"],
                        "additionalProperties": False,
                    },
                    handler=create_visitor_pass,
                    example_inputs=(
                        {"visitor_name": "Chris", "expected_time": "today 14:00", "window_minutes": 30, "confirm": False},
                        {"visitor_name": "Boiler engineer", "valid_from": "today 09:00", "valid_until": "today 12:00", "pass_type": "duration", "confirm": False},
                    ),
                ),
        AgentTool(
                    name="update_visitor_pass",
                    description="Update a scheduled or active Visitor Pass. Requires confirm=true.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "pass_id": {"type": "string"},
                            "visitor_name": {"type": "string", "description": "Pass lookup name or replacement visitor name."},
                            "new_visitor_name": {"type": "string", "description": "Replacement visitor name when renaming a pass."},
                            "pass_type": {"type": "string", "enum": ["one-time", "duration"]},
                            "visitor_phone": {"type": "string"},
                            "expected_time": {"type": "string"},
                            "window_minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
                            "valid_from": {"type": "string"},
                            "valid_until": {"type": "string"},
                            "confirm": {"type": "boolean"},
                        },
                        "required": ["confirm"],
                        "additionalProperties": False,
                    },
                    handler=update_visitor_pass,
                ),
        AgentTool(
                    name="cancel_visitor_pass",
                    description="Cancel a scheduled or active Visitor Pass. Requires confirm=true.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "pass_id": {"type": "string"},
                            "visitor_name": {"type": "string"},
                            "reason": {"type": "string"},
                            "confirm": {"type": "boolean"},
                        },
                        "required": ["confirm"],
                        "additionalProperties": False,
                    },
                    handler=cancel_visitor_pass,
                ),
        AgentTool(
                    name="trigger_icloud_sync",
                    description=(
                        "Manually sync connected iCloud Calendars and generate Visitor Passes for events "
                        "whose notes or description contain Open Gate. Requires confirm=true."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "confirm": {"type": "boolean", "description": "Must be true before calendar sync runs."},
                        },
                        "required": ["confirm"],
                        "additionalProperties": False,
                    },
                    handler=trigger_icloud_sync,
                ),
        ],
        categories=TOOL_CATEGORIES,
        confirmation_required=CONFIRMATION_REQUIRED_TOOLS,
        default_limits=DEFAULT_LIMITS,
    )
