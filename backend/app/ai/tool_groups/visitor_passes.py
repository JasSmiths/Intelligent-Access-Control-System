"""Alfred tool catalog entries for this domain."""

from __future__ import annotations

from app.ai.tools import (
    AgentTool,
    cancel_visitor_pass,
    create_visitor_pass,
    get_visitor_pass,
    query_visitor_passes,
    trigger_icloud_sync,
    update_visitor_pass,
)


def build_tools() -> list[AgentTool]:
    return [
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
                        },
                        "additionalProperties": False,
                    },
                    handler=query_visitor_passes,
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
                ),
        AgentTool(
                    name="create_visitor_pass",
                    description=(
                        "Create a one-shot Visitor Pass for an expected unknown vehicle. "
                        "For one-time passes, do not call until visitor_name and expected_time are known. "
                        "For duration passes, visitor_phone, valid_from, and valid_until are required. "
                        "Use local site time silently and default to a +/- 30 minute window when window_minutes is omitted. "
                        "Do not resolve visitor_name as a Person record. Requires confirm=true."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "visitor_name": {"type": "string"},
                            "pass_type": {"type": "string", "enum": ["one-time", "duration"]},
                            "visitor_phone": {"type": "string", "description": "Required for duration Visitor Passes. Use full international format when known."},
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
    ]
