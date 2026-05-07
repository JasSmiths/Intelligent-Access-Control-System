from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.modules.messaging.base import IncomingChatMessage, MessagingActor
from app.services.messaging_bridge import MessagingBridgeService


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["whatsapp", "discord"])
async def test_messaging_bridge_delegates_admin_chat_to_shared_alfred_service(monkeypatch, provider: str) -> None:
    service = MessagingBridgeService()
    captured: dict[str, object] = {}

    async def resolve_actor(message, *, is_admin_hint=False):
        return MessagingActor(
            provider=message.provider,
            provider_user_id=message.author_provider_id,
            display_name=message.author_display_name,
            user_id="user-1",
            user_role="admin",
            is_admin=True,
        )

    class SharedAlfred:
        async def handle_message(self, message, **kwargs):
            captured["message"] = message
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                session_id=kwargs["session_id"],
                text="Steph left at 07:42. Same Alfred, different doorway.",
                tool_results=[],
                pending_action=None,
            )

    incoming = IncomingChatMessage(
        provider=provider,
        provider_message_id=f"{provider}-message-1",
        provider_channel_id="channel-1",
        provider_guild_id="guild-1" if provider == "discord" else None,
        author_provider_id="provider-user-1",
        author_display_name="Jas",
        text="When did Steph leave this morning?",
        is_direct_message=provider == "whatsapp",
        mentioned_bot=True,
        raw_payload={},
        received_at=datetime.now(tz=UTC),
    )

    monkeypatch.setattr(service, "resolve_actor", resolve_actor)
    monkeypatch.setattr("app.services.messaging_bridge.chat_service", SharedAlfred())

    result = await service.handle_message(incoming, is_admin_hint=True)

    kwargs = captured["kwargs"]
    assert captured["message"] == "When did Steph leave this morning?"
    assert isinstance(kwargs, dict)
    assert kwargs["user_id"] == "user-1"
    assert kwargs["user_role"] == "admin"
    assert kwargs["client_context"]["source"] == "messaging"
    assert kwargs["client_context"]["messaging_provider"] == provider
    assert result.response_text == "Steph left at 07:42. Same Alfred, different doorway."
