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
    "query_dependency_updates": ("System_Operations",),
    "check_dependency_updates": ("System_Operations",),
    "analyze_dependency_update": ("System_Operations",),
    "apply_dependency_update": ("System_Operations",),
    "query_dependency_backups": ("System_Operations",),
    "restore_dependency_backup": ("System_Operations",),
    "query_dependency_update_job": ("System_Operations",),
    "configure_dependency_backup_storage": ("System_Operations",),
    "validate_dependency_backup_storage": ("System_Operations",),
}

CONFIRMATION_REQUIRED_TOOLS = {
    "analyze_dependency_update",
    "apply_dependency_update",
    "check_dependency_updates",
    "configure_dependency_backup_storage",
    "restore_dependency_backup",
    "rotate_auth_secret",
    "test_integration_connection",
    "update_system_settings",
    "validate_dependency_backup_storage",
}

REQUIRED_PERMISSIONS = admin_permissions(
    "analyze_dependency_update",
    "apply_dependency_update",
    "check_dependency_updates",
    "configure_dependency_backup_storage",
    "query_alfred_runtime_events",
    "query_auth_secret_status",
    "query_dependency_backups",
    "query_dependency_update_job",
    "query_system_settings",
    "restore_dependency_backup",
    "rotate_auth_secret",
    "test_integration_connection",
    "update_system_settings",
    "validate_dependency_backup_storage",
)

DEFAULT_LIMITS = {"query_alfred_runtime_events": 20}


def build_tools() -> list[AgentTool]:
    return apply_group_metadata(
        [
        AgentTool(
            name="query_integration_health",
            description=(
                "Return redacted health/configuration status for configured IACS integrations, "
                "access-event processing, and dependency backup storage."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "integration": {
                        "type": "string",
                        "description": (
                            "Optional integration name, for example access_events, home_assistant, "
                            "unifi_protect, discord, whatsapp, dvla, llm, dependency_updates, or all."
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
        AgentTool(
            name="query_dependency_updates",
            description="List tracked system dependencies and available update metadata.",
            parameters={
                "type": "object",
                "properties": {"update_only": {"type": "boolean"}},
                "additionalProperties": False,
            },
            handler=system_operations_handlers.query_dependency_updates,
        ),
        AgentTool(
            name="check_dependency_updates",
            description="Check dependency registries for updates. Requires confirm=true.",
            parameters={
                "type": "object",
                "properties": {
                    "direct_only": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                },
                "required": ["confirm"],
                "additionalProperties": False,
            },
            handler=system_operations_handlers.check_dependency_updates,
        ),
        AgentTool(
            name="analyze_dependency_update",
            description="Analyze a dependency update target using release data and configured LLM/heuristics. Requires confirm=true.",
            parameters={
                "type": "object",
                "properties": {
                    "dependency_id": {"type": "string"},
                    "target_version": {"type": "string"},
                    "provider": {"type": "string"},
                    "confirm": {"type": "boolean"},
                },
                "required": ["dependency_id", "confirm"],
                "additionalProperties": False,
            },
            handler=system_operations_handlers.analyze_dependency_update,
        ),
        AgentTool(
            name="apply_dependency_update",
            description="Start an existing dependency apply job. Requires confirm=true and never runs shell commands directly from Alfred.",
            parameters={
                "type": "object",
                "properties": {
                    "dependency_id": {"type": "string"},
                    "target_version": {"type": "string"},
                    "confirm": {"type": "boolean"},
                },
                "required": ["dependency_id", "confirm"],
                "additionalProperties": False,
            },
            handler=system_operations_handlers.apply_dependency_update,
        ),
        AgentTool(
            name="query_dependency_backups",
            description="List dependency update offline backups, optionally for one dependency.",
            parameters={
                "type": "object",
                "properties": {"dependency_id": {"type": "string"}},
                "additionalProperties": False,
            },
            handler=system_operations_handlers.query_dependency_backups,
        ),
        AgentTool(
            name="restore_dependency_backup",
            description="Start an existing dependency backup restore job. Requires confirm=true.",
            parameters={
                "type": "object",
                "properties": {
                    "backup_id": {"type": "string"},
                    "confirm": {"type": "boolean"},
                },
                "required": ["backup_id", "confirm"],
                "additionalProperties": False,
            },
            handler=system_operations_handlers.restore_dependency_backup,
        ),
        AgentTool(
            name="query_dependency_update_job",
            description="Return status for a dependency update apply/restore job.",
            parameters={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
                "additionalProperties": False,
            },
            handler=system_operations_handlers.query_dependency_update_job,
        ),
        AgentTool(
            name="configure_dependency_backup_storage",
            description="Configure local/NFS/Samba backup storage for dependency updates. Sensitive mount options are accepted but redacted. Requires confirm=true.",
            parameters={
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["local", "nfs", "samba"]},
                    "mount_source": {"type": "string"},
                    "mount_options": {"type": "string"},
                    "retention_days": {"type": "string"},
                    "min_free_bytes": {"type": "integer", "minimum": 0},
                    "confirm": {"type": "boolean"},
                },
                "required": ["mode", "confirm"],
                "additionalProperties": False,
            },
            handler=system_operations_handlers.configure_dependency_backup_storage,
        ),
        AgentTool(
            name="validate_dependency_backup_storage",
            description="Validate dependency update backup storage writability/free space. Requires confirm=true.",
            parameters={
                "type": "object",
                "properties": {"confirm": {"type": "boolean"}},
                "required": ["confirm"],
                "additionalProperties": False,
            },
            handler=system_operations_handlers.validate_dependency_backup_storage,
        ),
        ],
        categories=TOOL_CATEGORIES,
        status_labels={'query_integration_health': 'Checking integration health...', 'test_integration_connection': 'Preparing integration test...', 'query_system_settings': 'Reading redacted settings...', 'update_system_settings': 'Preparing settings update...', 'query_auth_secret_status': 'Checking auth-secret status...', 'rotate_auth_secret': 'Preparing auth-secret rotation...', 'query_dependency_updates': 'Checking dependency update state...', 'check_dependency_updates': 'Preparing dependency update check...', 'analyze_dependency_update': 'Preparing dependency analysis...', 'apply_dependency_update': 'Preparing dependency apply job...', 'query_dependency_backups': 'Checking dependency backups...', 'restore_dependency_backup': 'Preparing dependency restore job...', 'query_dependency_update_job': 'Checking dependency job...', 'configure_dependency_backup_storage': 'Preparing backup storage update...', 'validate_dependency_backup_storage': 'Preparing backup storage validation...'},
        success_fields={'update_system_settings': ('updated',)},
        finish_after_confirmation=frozenset(),
        confirmation_required=CONFIRMATION_REQUIRED_TOOLS,
        default_limits=DEFAULT_LIMITS,
        required_permissions=REQUIRED_PERMISSIONS,
    )
