from contextvars import Context
import asyncio
from types import SimpleNamespace
import uuid

import pytest

from app.ai.tool_groups._shared import _agent_timezone

from app.ai.context import (
    CHAT_TOOL_CONTEXT,
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


@pytest.mark.asyncio
async def test_concurrent_tool_contexts_do_not_share_actor_changes():
    outer = set_chat_tool_context({"user_role": "standard", "user_id": "parent"})
    try:
        async def actor(user_id, role):
            token = set_chat_tool_context({"user_id": user_id, "user_role": role})
            try:
                await asyncio.sleep(0)
                assert get_chat_tool_context() == {"user_id": user_id, "user_role": role}
            finally:
                set_chat_tool_context({}, token=token)
            assert get_chat_tool_context()["user_id"] == "parent"

        await asyncio.wait_for(asyncio.gather(actor("admin", "admin"), actor("reader", "standard")), 1)
        assert get_chat_tool_context() == {"user_role": "standard", "user_id": "parent"}
    finally:
        set_chat_tool_context({}, token=outer)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_chat_restores_outer_context_when_v3_fails_or_is_cancelled(monkeypatch, failure):
    from app.services import chat

    async def synthetic_result(*_args, **_kwargs):
        return uuid.uuid4()

    async def synthetic_runtime():
        return SimpleNamespace(llm_provider="synthetic")

    async def synthetic_context(**_kwargs):
        return {"user": {"role": "admin"}}

    async def ignore_event(*_args, **_kwargs):
        return None

    async def fail_v3(*_args, **_kwargs):
        assert get_chat_tool_context()["user_role"] == "admin"
        await asyncio.sleep(0)
        raise failure("synthetic failure")

    service = chat.ChatService()
    monkeypatch.setattr(service, "_ensure_session", synthetic_result)
    monkeypatch.setattr(service, "_append_message", synthetic_result)
    monkeypatch.setattr(service, "_load_memory", synthetic_result)
    monkeypatch.setattr(service, "_build_actor_context", synthetic_context)
    monkeypatch.setattr(service, "_model_for_provider", lambda *_args: "synthetic")
    monkeypatch.setattr(service, "_handle_message_v3", fail_v3)
    monkeypatch.setattr(chat, "get_runtime_config", synthetic_runtime)
    monkeypatch.setattr(chat, "get_llm_provider", lambda _name: SimpleNamespace(name="synthetic"))
    monkeypatch.setattr(chat.event_bus, "publish", ignore_event)
    outer = set_chat_tool_context({"user_role": "standard"})
    try:
        with pytest.raises(failure, match="synthetic failure"):
            await service.handle_message("synthetic request", user_id=str(uuid.uuid4()), user_role="admin")
        assert get_chat_tool_context() == {"user_role": "standard"}
    finally:
        set_chat_tool_context({}, token=outer)
