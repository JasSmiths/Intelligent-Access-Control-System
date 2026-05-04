"""Registry that assembles Alfred tool groups without changing public tool names."""

from __future__ import annotations

from app.ai.tools import AgentTool
from app.ai.tool_groups import (
    access_diagnostics,
    automations,
    compliance_cameras_files,
    gate_maintenance,
    general,
    notifications,
    schedules,
    visitor_passes,
)

_TOOL_GROUP_BUILDERS = (
    general.build_tools,
    gate_maintenance.build_tools,
    visitor_passes.build_tools,
    access_diagnostics.build_tools,
    compliance_cameras_files.build_tools,
    notifications.build_tools,
    automations.build_tools,
    schedules.build_tools,
)


def build_grouped_tools() -> list[AgentTool]:
    tools: list[AgentTool] = []
    seen: set[str] = set()
    for build_tools in _TOOL_GROUP_BUILDERS:
        for tool in build_tools():
            if tool.name in seen:
                raise RuntimeError(f"Duplicate Alfred tool name: {tool.name}")
            seen.add(tool.name)
            tools.append(tool)
    return tools
