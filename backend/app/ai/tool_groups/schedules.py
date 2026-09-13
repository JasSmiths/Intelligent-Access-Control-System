"""Alfred tool catalog entries for this domain."""

from __future__ import annotations

from typing import Any

from app.ai.tool_groups import schedules_handlers
from app.ai.tool_groups.metadata import apply_group_metadata
from app.ai.tools import AgentTool
from app.services.type_helpers import as_dict

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

TOOL_CATEGORIES = {
    "override_schedule": ("Schedules",),
    "query_schedules": ("Schedules", "Access_Diagnostics"),
    "get_schedule": ("Schedules", "Access_Diagnostics"),
    "create_schedule": ("Schedules",),
    "update_schedule": ("Schedules",),
    "delete_schedule": ("Schedules",),
    "query_schedule_targets": ("Schedules",),
    "assign_schedule_to_entity": ("Schedules",),
    "verify_schedule_access": ("Schedules", "Access_Diagnostics"),
}

CONFIRMATION_REQUIRED_TOOLS = {
    "assign_schedule_to_entity",
    "create_schedule",
    "delete_schedule",
    "override_schedule",
    "update_schedule",
}

DEFAULT_LIMITS = {"query_schedule_targets": 25}


def build_tools() -> list[AgentTool]:
    return apply_group_metadata(
        [
        AgentTool(
                    name="override_schedule",
                    description=(
                        "Create a temporary one-off access allowance for a person. "
                        "Default duration is 60 minutes and the action requires confirm=true."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "person_id": {"type": "string", "description": "Exact person UUID from actor context or resolve_human_entity."},
                            "time": {"type": "string", "description": "Override start datetime or empty for now."},
                            "duration_minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
                            "reason": {"type": "string"},
                            "confirm": {"type": "boolean", "description": "Must be true before the override is created."},
                        },
                        "required": ["person_id", "confirm"],
                        "additionalProperties": False,
                    },
                    handler=schedules_handlers.override_schedule,
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
                    handler=schedules_handlers.query_schedules,
                    example_inputs=(
                        {"search": "weekday", "include_dependencies": True},
                    ),
                    return_schema={
                        "answer_types": ["schedule_list"],
                        "records": "schedules",
                    },
                ),
        AgentTool(
                    name="get_schedule",
                    description="Get one schedule by ID or name, including normalized time blocks and current People/Vehicle/Door dependencies.",
                    parameters={
                        "type": "object",
                        "properties": SCHEDULE_LOOKUP_PROPERTIES,
                        "additionalProperties": False,
                    },
                    handler=schedules_handlers.get_schedule,
                    example_inputs=(
                        {"schedule_name": "Weekdays"},
                    ),
                    return_schema={
                        "answer_types": ["schedule_detail"],
                        "records": "schedule",
                    },
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
                            "confirm": {
                                "type": "boolean",
                                "description": "Must be true before the schedule will be created.",
                            },
                        },
                        "required": ["name", "confirm"],
                        "additionalProperties": False,
                    },
                    handler=schedules_handlers.create_schedule,
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
                            "confirm": {"type": "boolean"},
                        },
                        "required": ["confirm"],
                        "additionalProperties": False,
                    },
                    handler=schedules_handlers.update_schedule,
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
                    handler=schedules_handlers.delete_schedule,
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
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                        },
                        "additionalProperties": False,
                    },
                    handler=schedules_handlers.query_schedule_targets,
                    example_inputs=(
                        {"entity_type": "vehicle", "search": "Tesla", "limit": 25},
                    ),
                    return_schema={
                        "answer_types": ["schedule_assignments"],
                        "records": "targets",
                    },
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
                            "confirm": {"type": "boolean"},
                        },
                        "required": ["entity_type", "confirm"],
                        "additionalProperties": False,
                    },
                    handler=schedules_handlers.assign_schedule_to_entity,
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
                    handler=schedules_handlers.verify_schedule_access,
                    example_inputs=(
                        {"entity_type": "person", "entity_name": "Steph", "at": "today 18:30"},
                        {"entity_type": "vehicle", "registration_number": "PE70DHX"},
                    ),
                    return_schema={
                        "answer_types": ["schedule_access"],
                        "handles": ["schedule_allowed_now", "schedule_allowed_at_time", "vehicle_owner_inheritance"],
                        "result_keys": ["verified", "allowed", "policy", "schedule"],
                    },
                ),
        ],
        categories=TOOL_CATEGORIES,
        button_handler=confirmation_button,
        summary_handler=confirmation_summary,
        status_labels={'override_schedule': 'Preparing schedule override...', 'query_schedules': 'Checking schedules...', 'get_schedule': 'Checking schedule details...', 'create_schedule': 'Creating schedule...', 'update_schedule': 'Updating schedule...', 'delete_schedule': 'Deleting schedule...', 'query_schedule_targets': 'Checking schedule assignments...', 'assign_schedule_to_entity': 'Assigning schedule...', 'verify_schedule_access': 'Verifying schedule access...'},
        success_fields={'override_schedule': ('created',), 'create_schedule': ('created',), 'update_schedule': ('updated',), 'delete_schedule': ('deleted',), 'assign_schedule_to_entity': ('assigned',)},
        finish_after_confirmation=frozenset({'update_schedule', 'create_schedule', 'delete_schedule'}),
        confirmation_required=CONFIRMATION_REQUIRED_TOOLS,
        default_limits=DEFAULT_LIMITS,
    )


def confirmation_summary(tool_name: str, output: dict[str, Any]) -> str:
    if tool_name == 'override_schedule':
        if output.get('created'):
            return f"Created the temporary access override for {output.get('person') or 'that person'} until {output.get('ends_at_display') or output.get('ends_at')}."
        return str(output.get('detail') or 'I did not create the schedule override.')
    if tool_name == 'create_schedule':
        schedule = as_dict(output.get('schedule'))
        name = schedule.get('name') or output.get('schedule_name') or 'the schedule'
        summary = schedule.get('summary')
        return f"Created {name}{(f' with {summary}' if summary else '')}." if output.get('created') else str(output.get('detail') or f'I did not create {name}.')
    if tool_name == 'update_schedule':
        schedule = as_dict(output.get('schedule'))
        name = schedule.get('name') or output.get('schedule_name') or 'the schedule'
        summary = schedule.get('summary')
        return f"Updated {name}{(f' to {summary}' if summary else '')}."
    if tool_name == 'delete_schedule':
        schedule = as_dict(output.get('schedule'))
        name = schedule.get('name') or output.get('schedule_name') or 'the schedule'
        return f'Deleted {name}.' if output.get('deleted') else str(output.get('detail') or f'I did not delete {name}.')
    return str(output.get("detail") or "Action completed.")


def confirmation_button(tool_name: str, output: dict[str, Any]) -> str:
    if tool_name == 'override_schedule':
        return 'Create override'
    if tool_name == 'create_schedule':
        return 'Create schedule'
    return "Confirm"
