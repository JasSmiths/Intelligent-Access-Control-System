"""Alfred tool catalog entries for this domain."""

from __future__ import annotations

from app.ai.tools import (
    AgentTool,
    NOTIFICATION_ACTION_SCHEMA,
    NOTIFICATION_CONDITION_SCHEMA,
    NOTIFICATION_RULE_LOOKUP_PROPERTIES,
    NOTIFICATION_RULE_PAYLOAD_SCHEMA,
)
from app.ai.tool_groups.notifications_handlers import (
    create_notification_workflow,
    delete_notification_workflow,
    get_notification_workflow,
    preview_notification_workflow,
    query_notification_catalog,
    query_notification_workflows,
    test_notification_workflow,
    update_notification_workflow,
)
from app.ai.tool_groups.metadata import apply_group_metadata


TOOL_CATEGORIES = {
    "query_notification_catalog": ("Notifications",),
    "query_notification_workflows": ("Notifications",),
    "get_notification_workflow": ("Notifications",),
    "create_notification_workflow": ("Notifications",),
    "update_notification_workflow": ("Notifications",),
    "delete_notification_workflow": ("Notifications",),
    "preview_notification_workflow": ("Notifications",),
    "test_notification_workflow": ("Notifications",),
}

CONFIRMATION_REQUIRED_TOOLS = {
    "create_notification_workflow",
    "delete_notification_workflow",
    "test_notification_workflow",
    "update_notification_workflow",
}

DEFAULT_LIMITS = {"query_notification_workflows": 20}


def build_tools() -> list[AgentTool]:
    return apply_group_metadata(
        [
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
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                            "summarize_payload": {"type": "boolean"},
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
        ],
        categories=TOOL_CATEGORIES,
        confirmation_required=CONFIRMATION_REQUIRED_TOOLS,
        default_limits=DEFAULT_LIMITS,
    )
