"""Compatibility wrappers for handlers that still live behind app.ai.tools."""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable

from app.ai import tools as tools_facade

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def facade_handler(name: str) -> ToolHandler:
    frame = inspect.currentframe()
    caller = frame.f_back if frame else None
    module_name = str(caller.f_globals.get("__name__") or __name__) if caller else __name__

    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        target = getattr(tools_facade, name)
        return await target(arguments)

    handler.__name__ = name
    handler.__qualname__ = name
    handler.__module__ = module_name
    return handler
