"""Shared Alfred streaming event helpers."""

from __future__ import annotations

from typing import Awaitable, Callable

StatusCallback = Callable[[dict], Awaitable[None]]


async def emit_agent_state(
    callback: StatusCallback | None,
    state: str,
    label: str,
    *,
    detail: str | None = None,
    batch_id: str | None = None,
    agents_running: int | None = 1,
    active_tool_calls: int | None = 0,
    completed_tool_steps: int | None = None,
) -> None:
    if callback is None:
        return
    payload = {
        "event": "chat.agent_state",
        "state": state,
        "phase": state,
        "label": label,
    }
    if detail:
        payload["detail"] = detail
    if batch_id:
        payload["batch_id"] = batch_id
    if agents_running is not None:
        payload["agents_running"] = max(0, int(agents_running))
    if active_tool_calls is not None:
        payload["active_tool_calls"] = max(0, int(active_tool_calls))
    if completed_tool_steps is not None:
        payload["completed_tool_steps"] = max(0, int(completed_tool_steps))
    await callback(payload)
