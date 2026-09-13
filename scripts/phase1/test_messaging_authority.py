"""Queued chat retains its intake authority under real isolated PostgreSQL."""
from test_recovery_boundaries import isolated_resources as isolated_resources

from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models import MessagingIdentity, User
from app.models.enums import UserRole
from app.modules.messaging.base import (
    IncomingChatMessage, MessagingAuthorityBinding, MessagingAuthorityChanged,
)
from app.services import messaging_bridge

pytestmark = pytest.mark.asyncio


def incoming():
    return IncomingChatMessage(provider="discord", provider_message_id="synthetic-message",
        provider_channel_id="synthetic-channel", author_provider_id="synthetic-sender",
        author_display_name="Synthetic display", text="Synthetic request",
        is_direct_message=True, mentioned_bot=False, raw_payload={}, author_is_provider_admin=True)


async def linked_actor():
    async with AsyncSessionLocal() as session:
        user = User(username="synthetic-messaging-actor", first_name="Synthetic", last_name="Actor",
            full_name="Synthetic Actor", password_hash="unused", role=UserRole.ADMIN,
            is_active=True, auth_session_version=1)
        session.add(user)
        await session.flush()
        identity = MessagingIdentity(provider="discord", provider_user_id="synthetic-sender",
            provider_display_name="Prior display", user_id=user.id, metadata_={"retained": True})
        session.add(identity)
        await session.commit()
        return user.id, identity.id


@pytest.mark.parametrize("change", ["demotion", "inactive", "version", "unlinked", "new-link"])
async def test_changed_intake_authority_cannot_invoke_or_change_identity_metadata(monkeypatch, change):
    user_id, identity_id = await linked_actor()
    expected = MessagingAuthorityBinding(str(user_id), "admin", 1)
    async with AsyncSessionLocal() as session:
        user, identity = await session.get(User, user_id), await session.get(MessagingIdentity, identity_id)
        if change == "demotion":
            user.role = UserRole.STANDARD
        elif change == "inactive":
            user.is_active = False
        elif change == "version":
            user.auth_session_version += 1
        elif change == "unlinked":
            identity.user_id = None
        else:
            expected = MessagingAuthorityBinding(None, "standard", None)
        await session.commit()
    handler = AsyncMock(side_effect=AssertionError("Changed authority must not invoke Alfred"))
    monkeypatch.setattr(messaging_bridge, "chat_service", SimpleNamespace(handle_message=handler))
    with pytest.raises(MessagingAuthorityChanged, match="sender_binding_changed"):
        await messaging_bridge.MessagingBridgeService().handle_message(
            incoming(), is_admin_hint=True, expected_authority=expected,
        )
    handler.assert_not_awaited()
    async with AsyncSessionLocal() as session:
        identity = await session.get(MessagingIdentity, identity_id)
        assert identity.metadata_ == {"retained": True}
        assert identity.provider_display_name == "Prior display"


async def test_matching_binding_routes_once_and_keeps_current_actor(monkeypatch):
    user_id, _ = await linked_actor()
    handler = AsyncMock(return_value=SimpleNamespace(session_id=str(uuid.uuid4()), text="Synthetic answer",
        tool_results=[], pending_action=None))
    monkeypatch.setattr(messaging_bridge, "chat_service", SimpleNamespace(handle_message=handler))
    result = await messaging_bridge.MessagingBridgeService().handle_message(
        incoming(), expected_authority=MessagingAuthorityBinding(str(user_id), "admin", 1),
    )
    handler.assert_awaited_once()
    assert handler.call_args.kwargs["user_id"] == str(user_id)
    assert result.actor.is_admin and result.response_text == "Synthetic answer"


async def test_unlinked_provider_admin_remains_standard(monkeypatch):
    handler = AsyncMock(return_value=SimpleNamespace(session_id=str(uuid.uuid4()), text="Synthetic answer",
        tool_results=[], pending_action=None))
    monkeypatch.setattr(messaging_bridge, "chat_service", SimpleNamespace(handle_message=handler))
    result = await messaging_bridge.MessagingBridgeService().handle_message(
        incoming(), is_admin_hint=True, expected_authority=MessagingAuthorityBinding(None, "standard", None),
    )
    assert result.actor.user_id is None and not result.actor.is_admin
    assert handler.call_args.kwargs["user_role"] == "standard"
    async with AsyncSessionLocal() as session:
        identity = await session.scalar(select(MessagingIdentity).where(MessagingIdentity.provider_user_id == "synthetic-sender"))
        assert identity.metadata_["last_provider_admin"] is True
