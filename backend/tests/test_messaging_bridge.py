from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.modules.messaging.base import IncomingChatMessage, MessagingActor
from app.models import MessagingIdentity
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


@pytest.mark.asyncio
async def test_messaging_bridge_does_not_trust_unlinked_provider_admin_hint(monkeypatch) -> None:
    service = MessagingBridgeService()
    added: list[MessagingIdentity] = []

    class FakeSession:
        async def scalar(self, _query):
            return None

        def add(self, row):
            added.append(row)

        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return None

    incoming = IncomingChatMessage(
        provider="discord",
        provider_message_id="discord-message-1",
        provider_channel_id="channel-1",
        provider_guild_id="guild-1",
        author_provider_id="provider-admin-1",
        author_display_name="Discord Admin",
        author_role_ids=["admin-role"],
        author_is_provider_admin=True,
        text="open the gate",
        is_direct_message=False,
        mentioned_bot=True,
        raw_payload={},
        received_at=datetime.now(tz=UTC),
    )

    monkeypatch.setattr("app.services.messaging_bridge.AsyncSessionLocal", lambda: FakeSession())

    actor = await service.resolve_actor(incoming, is_admin_hint=True)

    assert actor.user_id is None
    assert actor.user_role == "standard"
    assert actor.is_admin is False
    assert added[0].metadata_["last_provider_admin"] is True
