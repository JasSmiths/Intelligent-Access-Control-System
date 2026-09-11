from contextvars import Context

import pytest

from app.ai.tool_groups._shared import (
    CHAT_TOOL_CONTEXT,
    _agent_timezone,
    get_chat_tool_context,
    set_chat_tool_context,
)


def test_unset_tool_context_has_no_shared_mutable_default():
    context = Context()
    with pytest.raises(LookupError):
        context.run(CHAT_TOOL_CONTEXT.get)
    context.run(get_chat_tool_context)["user_role"] = "admin"
    assert context.run(get_chat_tool_context) == {}
    assert Context().run(get_chat_tool_context) == {}


def test_tool_context_reset_restores_outer_actor_and_isolates_other_contexts():
    def exercise():
        outer = set_chat_tool_context({"user_role": "standard"})
        try:
            inner = set_chat_tool_context({"user_role": "admin"})
            assert get_chat_tool_context()["user_role"] == "admin"
            set_chat_tool_context({}, token=inner)
            assert get_chat_tool_context()["user_role"] == "standard"
            assert Context().run(get_chat_tool_context) == {}
        finally:
            set_chat_tool_context({}, token=outer)
        assert get_chat_tool_context() == {}

    Context().run(exercise)


@pytest.mark.parametrize("zone", ["Not/AZone", "/etc/passwd", "../invalid"])
def test_invalid_timezone_uses_configured_default(zone):
    assert _agent_timezone(zone).key == "Europe/London"
