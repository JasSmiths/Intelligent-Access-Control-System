"""Alfred tool catalog entries for this domain."""

from __future__ import annotations

from typing import Any

from app.ai.tool_groups import notifications_handlers
from app.ai.tool_groups.metadata import apply_group_metadata
from app.ai.tools import AgentTool
from app.services.type_helpers import as_dict

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
                    handler=notifications_handlers.query_notification_catalog,
                    example_inputs=({},),
                    return_schema={
                        "answer_types": ["notification_catalog"],
                        "result_keys": ["triggers", "variables", "channels", "endpoints"],
                    },
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
                    handler=notifications_handlers.query_notification_workflows,
                    example_inputs=(
                        {"trigger_event": "access.granted", "is_active": True, "limit": 20},
                        {"search": "gate malfunction", "include_preview": True},
                    ),
                    return_schema={
                        "answer_types": ["notification_workflow_list"],
                        "records": "workflows",
                    },
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
                    handler=notifications_handlers.get_notification_workflow,
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
                    handler=notifications_handlers.create_notification_workflow,
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
                    handler=notifications_handlers.update_notification_workflow,
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
                    handler=notifications_handlers.delete_notification_workflow,
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
                    handler=notifications_handlers.preview_notification_workflow,
                    example_inputs=(
                        {"rule_name": "Gate malfunction alerts"},
                    ),
                    return_schema={
                        "answer_types": ["notification_preview"],
                        "result_keys": ["previewed", "rendered_actions", "missing_variables"],
                    },
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
                    handler=notifications_handlers.test_notification_workflow,
                ),
        ],
        categories=TOOL_CATEGORIES,
        summary_handler=confirmation_summary,
        status_labels={'query_notification_catalog': 'Checking notification options...', 'query_notification_workflows': 'Checking notification workflows...', 'get_notification_workflow': 'Checking notification workflow...', 'create_notification_workflow': 'Preparing notification workflow...', 'update_notification_workflow': 'Preparing notification workflow update...', 'delete_notification_workflow': 'Preparing notification workflow deletion...', 'preview_notification_workflow': 'Previewing notification workflow...', 'test_notification_workflow': 'Preparing notification test...'},
        success_fields={'create_notification_workflow': ('created',), 'update_notification_workflow': ('updated',), 'delete_notification_workflow': ('deleted',), 'test_notification_workflow': ('sent',)},
        finish_after_confirmation=frozenset({'test_notification_workflow'}),
        confirmation_required=CONFIRMATION_REQUIRED_TOOLS,
        default_limits=DEFAULT_LIMITS,
    )


def confirmation_summary(tool_name: str, output: dict[str, Any]) -> str:
    if tool_name == 'create_notification_workflow':
        workflow = as_dict(output.get('workflow'))
        return f"Created notification workflow {workflow.get('name') or output.get('workflow_name') or ''}. Neatly filed.".strip()
    if tool_name == 'update_notification_workflow':
        workflow = as_dict(output.get('workflow'))
        return f"Updated notification workflow {workflow.get('name') or output.get('workflow_name') or ''}.".strip()
    if tool_name == 'delete_notification_workflow':
        workflow = as_dict(output.get('workflow'))
        return f"Deleted notification workflow {workflow.get('name') or output.get('workflow_name') or ''}.".strip()
    if tool_name == 'test_notification_workflow':
        if output.get('sent'):
            return 'Sent the notification workflow test. Tiny paper plane launched.'
        return str(output.get('detail') or 'I did not send the notification workflow test.')
    return str(output.get("detail") or "Action completed.")
