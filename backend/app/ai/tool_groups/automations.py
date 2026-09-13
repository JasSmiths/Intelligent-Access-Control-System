"""Alfred tool catalog entries for this domain."""

from __future__ import annotations

from typing import Any

from app.ai.tool_groups import automations_handlers
from app.ai.tool_groups.metadata import apply_group_metadata
from app.ai.tools import AgentTool
from app.services.type_helpers import as_dict

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
                    handler=automations_handlers.query_automation_catalog,
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
                    handler=automations_handlers.query_automations,
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
                    handler=automations_handlers.get_automation,
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
                    handler=automations_handlers.create_automation,
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
                    handler=automations_handlers.edit_automation,
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
                    handler=automations_handlers.delete_automation,
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
                    handler=automations_handlers.enable_automation,
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
                    handler=automations_handlers.disable_automation,
                ),
        ],
        categories=TOOL_CATEGORIES,
        summary_handler=confirmation_summary,
        status_labels={},
        success_fields={'create_automation': ('created',), 'edit_automation': ('updated',), 'delete_automation': ('deleted',), 'enable_automation': ('updated',), 'disable_automation': ('updated',)},
        finish_after_confirmation=frozenset(),
        confirmation_required=CONFIRMATION_REQUIRED_TOOLS,
        default_limits=DEFAULT_LIMITS,
    )


def confirmation_summary(tool_name: str, output: dict[str, Any]) -> str:
    automation = as_dict(output.get("automation"))
    return f"{tool_name.replace('_', ' ').capitalize()}: {automation.get('name') or 'automation'}."
