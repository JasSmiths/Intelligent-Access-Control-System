"""Alfred tool catalog entries for system operations."""

from __future__ import annotations

from app.ai.tool_groups import system_operations_handlers
from app.ai.tool_groups.metadata import admin_permissions, apply_group_metadata
from app.ai.tools import AgentTool

TOOL_CATEGORIES = {
    "query_integration_health": ("System_Operations", "Users_Settings", "General"),
    "test_integration_connection": ("System_Operations", "Users_Settings"),
    "query_system_settings": ("System_Operations", "Users_Settings"),
    "update_system_settings": ("System_Operations", "Users_Settings"),
    "query_auth_secret_status": ("System_Operations", "Users_Settings"),
    "rotate_auth_secret": ("System_Operations", "Users_Settings"),
    "query_alfred_runtime_events": ("System_Operations", "Users_Settings"),
}

CONFIRMATION_REQUIRED_TOOLS = {
    "rotate_auth_secret",
    "test_integration_connection",
    "update_system_settings",
}

REQUIRED_PERMISSIONS = admin_permissions(
    "query_alfred_runtime_events",
    "query_auth_secret_status",
    "query_system_settings",
    "rotate_auth_secret",
    "test_integration_connection",
    "update_system_settings",
)

DEFAULT_LIMITS = {"query_alfred_runtime_events": 20}


def build_tools() -> list[AgentTool]:
    return apply_group_metadata(
        [
        AgentTool(
            name="query_integration_health",
            description=(
                "Return redacted health/configuration status for configured IACS integrations, "
                "access-event processing."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "integration": {
                        "type": "string",
                        "description": (
                            "Optional integration name, for example access_events, home_assistant, "
                            "unifi_protect, discord, whatsapp, dvla, llm, or all."
                        ),
                    }
                },
                "additionalProperties": False,
            },
            handler=system_operations_handlers.query_integration_health,
            example_inputs=(
                {"integration": "all"},
                {"integration": "llm"},
            ),
            return_schema={
                "answer_types": ["integration_health"],
                "result_keys": ["integrations", "health", "configured", "ok"],
            },
        ),
        AgentTool(
            name="test_integration_connection",
            description="Run a confirmed integration connection test. This may contact external providers and requires confirm=true.",
            parameters={
                "type": "object",
                "properties": {
                    "integration": {"type": "string"},
                    "confirm": {"type": "boolean"},
                },
                "required": ["integration", "confirm"],
                "additionalProperties": False,
            },
            handler=system_operations_handlers.test_integration_connection,
        ),
        AgentTool(
            name="query_system_settings",
            description="Return redacted dynamic system settings, optionally filtered by category.",
            parameters={
                "type": "object",
                "properties": {"category": {"type": "string"}},
                "additionalProperties": False,
            },
            handler=system_operations_handlers.query_system_settings,
        ),
        AgentTool(
            name="update_system_settings",
            description="Update dynamic system settings. Secrets are redacted and the mutation requires confirm=true.",
            parameters={
                "type": "object",
                "properties": {
                    "values": {"type": "object", "additionalProperties": True},
                    "confirm": {"type": "boolean"},
                },
                "required": ["values", "confirm"],
                "additionalProperties": False,
            },
            handler=system_operations_handlers.update_system_settings,
        ),
        AgentTool(
            name="query_auth_secret_status",
            description="Return auth-secret source/readiness status without revealing the secret value.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=system_operations_handlers.query_auth_secret_status,
            example_inputs=({},),
            return_schema={
                "answer_types": ["auth_secret_status"],
                "result_keys": ["source", "ready", "rotation_supported"],
            },
        ),
        AgentTool(
            name="query_alfred_runtime_events",
            description="Return recent redacted Alfred chat runtime failures, including WebSocket/SSE/HTTP crashes caught by the backend.",
            parameters={
                "type": "object",
                "properties": {
                    "hours": {"type": "integer", "minimum": 1, "maximum": 168},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "additionalProperties": False,
            },
            handler=system_operations_handlers.query_alfred_runtime_events,
            example_inputs=(
                {"hours": 24, "limit": 20},
            ),
            return_schema={
                "answer_types": ["alfred_runtime_events"],
                "records": "events",
            },
        ),
        AgentTool(
            name="rotate_auth_secret",
            description="Rotate the file-backed auth root secret with a generated value. Requires confirm=true and invalidates sessions/action links.",
            parameters={
                "type": "object",
                "properties": {"confirm": {"type": "boolean"}},
                "required": ["confirm"],
                "additionalProperties": False,
            },
            handler=system_operations_handlers.rotate_auth_secret_tool,
        ),
        ],
        categories=TOOL_CATEGORIES,
        status_labels={'query_integration_health': 'Checking integration health...', 'test_integration_connection': 'Preparing integration test...', 'query_system_settings': 'Reading redacted settings...', 'update_system_settings': 'Preparing settings update...', 'query_auth_secret_status': 'Checking auth-secret status...', 'rotate_auth_secret': 'Preparing auth-secret rotation...'},
        success_fields={'update_system_settings': ('updated',)},
        finish_after_confirmation=frozenset(),
        confirmation_required=CONFIRMATION_REQUIRED_TOOLS,
        default_limits=DEFAULT_LIMITS,
        required_permissions=REQUIRED_PERMISSIONS,
    )
