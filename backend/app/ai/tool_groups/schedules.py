"""Alfred tool catalog entries for this domain."""

from __future__ import annotations

from app.ai.tools import (
    AgentTool,
    SCHEDULE_LOOKUP_PROPERTIES,
    SCHEDULE_TIME_BLOCKS_SCHEMA,
    assign_schedule_to_entity,
    create_schedule,
    delete_schedule,
    get_schedule,
    override_schedule,
    query_schedule_targets,
    query_schedules,
    update_schedule,
    verify_schedule_access,
)


def build_tools() -> list[AgentTool]:
    return [
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
                    handler=override_schedule,
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
                            "confirm": {
                                "type": "boolean",
                                "description": "Must be true before the schedule will be created.",
                            },
                        },
                        "required": ["name", "confirm"],
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
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
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
