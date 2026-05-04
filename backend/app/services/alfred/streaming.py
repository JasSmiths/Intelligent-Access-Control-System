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
) -> None:
    if callback is None:
        return
    payload = {
        "event": "chat.agent_state",
        "state": state,
        "label": label,
    }
    if detail:
        payload["detail"] = detail
    if batch_id:
        payload["batch_id"] = batch_id
    await callback(payload)

