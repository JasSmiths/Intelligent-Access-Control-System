"""Registry that assembles Alfred tool groups without changing public tool names."""

from __future__ import annotations

from typing import Any

from app.ai.tools import (
    ADMIN_PERMISSION,
    SAFETY_ADMIN_ONLY,
    SAFETY_CONFIRMATION_REQUIRED,
    SAFETY_LEVELS,
    AgentTool,
)
from app.ai.tool_groups import (
    access_diagnostics,
    automations,
    compliance_cameras_files,
    gate_maintenance,
    general,
    notifications,
    schedules,
    system_operations,
    visitor_passes,
)


class ToolRegistryError(RuntimeError):
    """Raised when Alfred tool metadata is unsafe or malformed."""


_TOOL_GROUP_BUILDERS = (
    general.build_tools,
    gate_maintenance.build_tools,
    visitor_passes.build_tools,
    access_diagnostics.build_tools,
    compliance_cameras_files.build_tools,
    notifications.build_tools,
    automations.build_tools,
    schedules.build_tools,
    system_operations.build_tools,
)


def build_grouped_tools() -> list[AgentTool]:
    tools: list[AgentTool] = []
    seen: set[str] = set()
    for build_tools in _TOOL_GROUP_BUILDERS:
        for tool in build_tools():
            if tool.name in seen:
                raise ToolRegistryError(f"Duplicate Alfred tool name: {tool.name}")
            seen.add(tool.name)
            _validate_tool(tool)
            tools.append(tool)
    return tools


def build_grouped_tool_map() -> dict[str, AgentTool]:
    return {tool.name: tool for tool in build_grouped_tools()}


def _validate_tool(tool: AgentTool) -> None:
    if not tool.name or not tool.name.strip():
        raise ToolRegistryError("Alfred tool name is required.")
    if not tool.description.strip():
        raise ToolRegistryError(f"{tool.name}: description is required.")
    if not tool.categories:
        raise ToolRegistryError(f"{tool.name}: at least one category is required.")
    if tool.safety_level not in SAFETY_LEVELS:
        raise ToolRegistryError(f"{tool.name}: unsupported safety_level {tool.safety_level!r}.")
    if tool.safety_level == SAFETY_CONFIRMATION_REQUIRED:
        if tool.read_only or not tool.requires_confirmation:
            raise ToolRegistryError(f"{tool.name}: confirmation tools must be non-read-only.")
        _validate_confirmation_schema(tool)
    if tool.requires_confirmation and tool.safety_level != SAFETY_CONFIRMATION_REQUIRED:
        raise ToolRegistryError(f"{tool.name}: requires_confirmation must use confirmation safety level.")
    if not tool.read_only and not tool.requires_confirmation:
        raise ToolRegistryError(f"{tool.name}: non-read-only tools must require confirmation.")
    if tool.safety_level == SAFETY_ADMIN_ONLY and ADMIN_PERMISSION not in tool.required_permissions:
        raise ToolRegistryError(f"{tool.name}: admin-only tools must require admin permission.")
    if not isinstance(tool.required_permissions, tuple):
        raise ToolRegistryError(f"{tool.name}: required_permissions must be a tuple.")
    if tool.rate_limit is not None and not isinstance(tool.rate_limit, dict):
        raise ToolRegistryError(f"{tool.name}: rate_limit must be an object when supplied.")
    if not isinstance(tool.example_inputs, tuple) or any(
        not isinstance(example, dict) for example in tool.example_inputs
    ):
        raise ToolRegistryError(f"{tool.name}: example_inputs must be a tuple of objects.")
    _validate_json_schema(tool.parameters, f"{tool.name}.parameters", require_object=True)
    if tool.return_schema is not None:
        _validate_json_schema(tool.return_schema, f"{tool.name}.return_schema", require_object=False)


def _validate_confirmation_schema(tool: AgentTool) -> None:
    properties = tool.parameters.get("properties")
    if not isinstance(properties, dict):
        raise ToolRegistryError(f"{tool.name}: confirmation tool parameters must define properties.")
    if not any(field in properties for field in ("confirm", "confirm_send", "confirmed")):
        raise ToolRegistryError(f"{tool.name}: confirmation tool must expose a confirmation field.")


def _validate_json_schema(schema: Any, label: str, *, require_object: bool) -> None:
    if not isinstance(schema, dict):
        raise ToolRegistryError(f"{label}: schema must be an object.")
    schema_type = schema.get("type")
    if require_object and schema_type != "object":
        raise ToolRegistryError(f"{label}: schema type must be object.")
    supported_types = {"object", "array", "string", "number", "integer", "boolean", "null"}
    if isinstance(schema_type, str) and schema_type not in supported_types:
        raise ToolRegistryError(f"{label}: unsupported schema type {schema_type!r}.")
    if isinstance(schema_type, list) and any(item not in supported_types for item in schema_type):
        raise ToolRegistryError(f"{label}: unsupported schema type list.")
    properties = schema.get("properties")
    if properties is not None and not isinstance(properties, dict):
        raise ToolRegistryError(f"{label}: properties must be an object.")
    required = schema.get("required")
    if required is not None and (
        not isinstance(required, list) or any(not isinstance(item, str) for item in required)
    ):
        raise ToolRegistryError(f"{label}: required must be a list of strings.")
