"""Tool execution policy helpers for Alfred v3."""

from __future__ import annotations

from app.ai.providers import ToolCall
from app.ai.tools import AgentTool


def is_unconfirmed_action_preview(call: ToolCall, tool: AgentTool | None) -> bool:
    if not tool or tool.read_only:
        return False
    if not tool.requires_confirmation:
        return False
    args = call.arguments or {}
    if "confirm_send" in args:
        return args.get("confirm_send") is False
    if "confirmed" in args:
        return args.get("confirmed") is False
    return args.get("confirm") is False


def can_execute_parallel(calls: list[ToolCall], tools_by_name: dict[str, AgentTool]) -> bool:
    if len(calls) <= 1:
        return False
    for call in calls:
        tool = tools_by_name.get(call.name)
        if not tool:
            return False
        if not tool.read_only and not is_unconfirmed_action_preview(call, tool):
            return False
    return True

