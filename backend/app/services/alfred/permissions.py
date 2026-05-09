"""Role and catalog enforcement for Alfred tools."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.ai.tools import ADMIN_PERMISSION, AgentTool

VISITOR_CONCIERGE_TOOL_NAMES = {
    "get_pass_details",
    "update_visitor_plate",
    "request_visitor_timeframe_change",
}


def actor_role(actor_context: dict[str, Any] | None, fallback: str | None = None) -> str:
    user = actor_context.get("user") if isinstance(actor_context, dict) else {}
    role = str((user or {}).get("role") or fallback or "standard").strip().lower()
    return "admin" if role == "admin" else "standard"


def filter_tools_for_actor(tools: Iterable[AgentTool], actor_context: dict[str, Any] | None) -> list[AgentTool]:
    role = actor_role(actor_context)
    visible: list[AgentTool] = []
    for tool in tools:
        if role != "admin" and (
            tool.requires_confirmation
            or not tool.read_only
            or ADMIN_PERMISSION in tool.required_permissions
        ):
            continue
        visible.append(tool)
    return visible


def permitted_tool_names(tools: Iterable[AgentTool], actor_context: dict[str, Any] | None) -> set[str]:
    return {tool.name for tool in filter_tools_for_actor(tools, actor_context)}


def validate_tool_call(
    tool_name: str,
    *,
    selected_tool_names: set[str],
    tools_by_name: dict[str, AgentTool],
    actor_context: dict[str, Any] | None,
) -> str | None:
    if tool_name not in selected_tool_names:
        return "tool_not_selected"
    tool = tools_by_name.get(tool_name)
    if not tool:
        return "unknown_tool"
    if tool_name not in permitted_tool_names(tools_by_name.values(), actor_context):
        return "role_not_permitted"
    return None
