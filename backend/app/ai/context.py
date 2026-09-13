"""Request-scoped Alfred actor context, independent of tools and services."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

CHAT_TOOL_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("chat_tool_context")


def set_chat_tool_context(
    value: dict[str, Any],
    *,
    token: Token[dict[str, Any]] | None = None,
) -> Token[dict[str, Any]] | None:
    if token is not None:
        CHAT_TOOL_CONTEXT.reset(token)
        return None
    return CHAT_TOOL_CONTEXT.set(value)


def get_chat_tool_context() -> dict[str, Any]:
    return CHAT_TOOL_CONTEXT.get({})
