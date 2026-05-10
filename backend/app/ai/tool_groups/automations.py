"""Alfred tool catalog entries for this domain."""

from __future__ import annotations

from app.ai.tools import (
    AUTOMATION_ACTION_SCHEMA,
    AUTOMATION_CONDITION_SCHEMA,
    AUTOMATION_RULE_LOOKUP_PROPERTIES,
    AUTOMATION_TRIGGER_SCHEMA,
    AgentTool,
)
from app.ai.tool_groups.automations_handlers import (
    create_automation,
    delete_automation,
    disable_automation,
    edit_automation,
    enable_automation,
    get_automation,
    query_automation_catalog,
    query_automations,
)
from app.ai.tool_groups.metadata import apply_group_metadata


TOOL_CATEGORIES = {
    "query_automation_catalog": ("Automations", "Notifications", "Gate_Hardware", "Maintenance"),
    "query_automations": ("Automations",),
    "get_automation": ("Automations",),
    "create_automation": ("Automations",),
    "edit_automation": ("Automations",),
    "delete_automation": ("Automations",),
    "enable_automation": ("Automations",),
    "disable_automation": ("Automations",),
}

CONFIRMATION_REQUIRED_TOOLS = {
    "create_automation",
    "delete_automation",
    "disable_automation",
    "edit_automation",
    "enable_automation",
}

DEFAULT_LIMITS = {"query_automations": 20}


def build_tools() -> list[AgentTool]:
    return apply_group_metadata(
        [
        AgentTool(
                    name="query_automation_catalog",
                    description="Return automation building blocks: trigger, condition, action, variable, notification-rule, and garage-door catalogs.",
                    parameters={
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                    handler=query_automation_catalog,
                    example_inputs=({},),
                    return_schema={
                        "answer_types": ["automation_catalog"],
                        "result_keys": ["triggers", "conditions", "actions", "variables"],
                    },
                ),
        AgentTool(
                    name="query_automations",
                    description="List DB-backed automation rules, optionally filtered by trigger, active status, or search text.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "trigger_key": {"type": "string"},
                            "is_active": {"type": "boolean"},
                            "search": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                        },
                        "additionalProperties": False,
                    },
                    handler=query_automations,
                    example_inputs=(
                        {"trigger_key": "vehicle.outside_schedule", "is_active": True, "limit": 20},
                    ),
                    return_schema={
                        "answer_types": ["automation_list"],
                        "records": "automations",
                    },
                ),
        AgentTool(
                    name="get_automation",
                    description="Get one automation rule by ID or name, including normalized triggers, conditions, actions, and optional dry-run preview.",
                    parameters={
                        "type": "object",
                        "properties": {
                            **AUTOMATION_RULE_LOOKUP_PROPERTIES,
                            "include_dry_run": {"type": "boolean"},
                        },
                        "additionalProperties": False,
                    },
                    handler=get_automation,
                    example_inputs=(
                        {"automation_name": "Open gate for Steph", "include_dry_run": True},
                    ),
                    return_schema={
                        "answer_types": ["automation_detail"],
                        "records": "automation",
                    },
                ),
        AgentTool(
                    name="create_automation",
                    description=(
                        "Create a system automation rule. Requires confirm=true because saved active rules may later command gates, "
                        "garage doors, maintenance mode, or notification states. Resolve people, vehicles, and notification rules first."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "triggers": {"type": "array", "items": AUTOMATION_TRIGGER_SCHEMA},
                            "conditions": {"type": "array", "items": AUTOMATION_CONDITION_SCHEMA},
                            "actions": {"type": "array", "items": AUTOMATION_ACTION_SCHEMA},
                            "is_active": {"type": "boolean"},
                            "confirm": {"type": "boolean"},
                        },
                        "required": ["name", "triggers", "actions", "confirm"],
                        "additionalProperties": False,
                    },
                    handler=create_automation,
                ),
        AgentTool(
                    name="edit_automation",
                    description="Edit an existing automation rule. Requires confirm=true because future matching events may perform changed actions.",
                    parameters={
                        "type": "object",
                        "properties": {
                            **AUTOMATION_RULE_LOOKUP_PROPERTIES,
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "triggers": {"type": "array", "items": AUTOMATION_TRIGGER_SCHEMA},
                            "conditions": {"type": "array", "items": AUTOMATION_CONDITION_SCHEMA},
                            "actions": {"type": "array", "items": AUTOMATION_ACTION_SCHEMA},
                            "is_active": {"type": "boolean"},
                            "confirm": {"type": "boolean"},
                        },
                        "required": ["confirm"],
                        "additionalProperties": False,
                    },
                    handler=edit_automation,
                ),
        AgentTool(
                    name="delete_automation",
                    description="Delete an automation rule by ID or name. Requires confirm=true.",
                    parameters={
                        "type": "object",
                        "properties": {
                            **AUTOMATION_RULE_LOOKUP_PROPERTIES,
                            "confirm": {"type": "boolean"},
                        },
                        "required": ["confirm"],
                        "additionalProperties": False,
                    },
                    handler=delete_automation,
                ),
        AgentTool(
                    name="enable_automation",
                    description="Enable a saved automation rule. Requires confirm=true.",
                    parameters={
                        "type": "object",
                        "properties": {
                            **AUTOMATION_RULE_LOOKUP_PROPERTIES,
                            "confirm": {"type": "boolean"},
                        },
                        "required": ["confirm"],
                        "additionalProperties": False,
                    },
                    handler=enable_automation,
                ),
        AgentTool(
                    name="disable_automation",
                    description="Disable a saved automation rule. Requires confirm=true.",
                    parameters={
                        "type": "object",
                        "properties": {
                            **AUTOMATION_RULE_LOOKUP_PROPERTIES,
                            "confirm": {"type": "boolean"},
                        },
                        "required": ["confirm"],
                        "additionalProperties": False,
                    },
                    handler=disable_automation,
                ),
        ],
        categories=TOOL_CATEGORIES,
        confirmation_required=CONFIRMATION_REQUIRED_TOOLS,
        default_limits=DEFAULT_LIMITS,
    )
