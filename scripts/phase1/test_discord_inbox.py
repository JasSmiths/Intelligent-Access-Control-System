"""Concrete Discord recovery under isolated PostgreSQL and inert provider callbacks."""
from test_recovery_boundaries import _bounded, isolated_resources as isolated_resources

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.models import MessagingIdentity, User
from app.models.enums import UserRole
from app.modules.messaging.base import IncomingChatMessage, MessagingAuthorityBinding, MessagingAuthorityChanged
from app.modules.notifications.base import NotificationDeliveryError
from app.services.discord_messaging import DiscordIntegrationConfig
from app.services.messaging import discord_incoming
from app.services.messaging.discord_incoming import DiscordIncomingDispatcher, DiscordIncomingGateway
from app.services.messaging.incoming_messages import IncomingMessageStore


pytestmark = pytest.mark.asyncio

CONFIG = DiscordIntegrationConfig(
    bot_token="synthetic-discord-token",
    guild_allowlist={"synthetic-guild"},
    channel_allowlist={"synthetic-channel"},
    user_allowlist={"synthetic-user"},
    role_allowlist={"synthetic-role"},
    admin_role_ids={"synthetic-admin-role"},
    default_notification_channel_id="111111111111111111",
    allow_direct_messages=False,
    require_mention=True,
)


@pytest_asyncio.fixture(autouse=True)
async def empty_discord_inbox(isolated_resources):
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE processed_messaging_messages, messaging_identities"))
        await session.commit()


async def linked_admin() -> tuple[uuid.UUID, uuid.UUID, str]:
    suffix = uuid.uuid4().hex[:16]
    provider_user_id = f"synthetic-discord-{suffix}"
    async with AsyncSessionLocal() as session:
        user = User(
            username=f"synthetic-discord-{suffix}",
            first_name="Synthetic",
            last_name="Discord",
            full_name="Synthetic Discord",
            password_hash="unused",
            role=UserRole.ADMIN,
            is_active=True,
            auth_session_version=0,
        )
        session.add(user)
        await session.flush()
        identity = MessagingIdentity(
            provider="discord",
            provider_user_id=provider_user_id,
            provider_display_name="Synthetic Discord",
            user_id=user.id,
        )
        session.add(identity)
        await session.commit()
        return user.id, identity.id, provider_user_id


def incoming(provider_user_id: str, *, text: str = "Synthetic buffered Discord request") -> IncomingChatMessage:
    return IncomingChatMessage(
        provider="discord",
        provider_message_id=str(10**17 + uuid.uuid4().int % 10**17),
        provider_channel_id="synthetic-channel",
        provider_guild_id="synthetic-guild",
        author_provider_id=provider_user_id,
        author_display_name="Synthetic Discord",
        author_role_ids=["stale-provider-role"],
        author_is_provider_admin=True,
        text=text,
        is_direct_message=False,
        mentioned_bot=True,
        raw_payload={"provider_admin": True},
        received_at=datetime.now(tz=UTC),
    )


class InertDiscord:
    def __init__(self) -> None:
        self.allowed = True
        self.sender_current = True
        self.membership_unavailable = False
        self.channel: Any | None = SimpleNamespace(id="synthetic-reply")
        self.reply_error: BaseException | None = None
        self.sent: list[dict[str, object]] = []
        self.interaction_sent: list[dict[str, object]] = []

    def delivery_ready(self) -> bool:
        return True

    async def config_for_session(self, _session) -> DiscordIntegrationConfig:
        return CONFIG

    async def refresh_incoming_membership(
        self,
        message: IncomingChatMessage,
        *,
        config: DiscordIntegrationConfig,
    ) -> IncomingChatMessage:
        assert config is CONFIG
        if self.membership_unavailable:
            raise MessagingAuthorityChanged("discord_membership_unavailable")
        return replace(message, author_role_ids=["current-provider-role"])

    async def message_is_allowed(self, _message, *, slash_command=False, config=None):
        if config is not None:
            assert config is CONFIG
        return (True, "allowed") if self.allowed else (False, "admission_changed")

    async def _resolve_channel(self, _channel_id: str):
        return self.channel

    async def assert_current_sender(self, _config=None):
        if not self.sender_current:
            raise NotificationDeliveryError("Synthetic stale bot", delivery="not_sent")
        return CONFIG

    async def _send_chat_result(self, _channel, response_text, pending_action, requester):
        payload = {
            "content": response_text,
            "pending_action": pending_action,
            "requester": requester,
        }
        self.sent.append(payload)
        if self.reply_error is not None:
            raise self.reply_error
        return SimpleNamespace(id="synthetic-discord-reply")

    async def send_incoming_interaction_reply(self, _interaction, payload):
        self.interaction_sent.append(dict(payload))
        if self.reply_error is not None:
            raise self.reply_error
        return SimpleNamespace(id="synthetic-discord-interaction-reply")


class InertInteractionResponse:
    def __init__(self, *, defer_error: BaseException | None = None) -> None:
        self.defer_error = defer_error
        self.defer_calls = 0
        self.messages: list[dict[str, object]] = []

    async def defer(self, **_kwargs) -> None:
        self.defer_calls += 1
        if self.defer_error is not None:
            raise self.defer_error

    async def send_message(self, message: str, **kwargs) -> None:
        self.messages.append({"message": message, **kwargs})


class InertInteraction:
    def __init__(self, provider_user_id: str, *, defer_error: BaseException | None = None) -> None:
        self.id = str(10**17 + uuid.uuid4().int % 10**17)
        self.token = "memory-only-interaction-token"
        self.guild = SimpleNamespace(id="synthetic-guild", owner_id=None)
        self.channel = SimpleNamespace(id="synthetic-channel")
        self.user = SimpleNamespace(
            id=provider_user_id,
            display_name="Synthetic Discord",
            roles=[SimpleNamespace(id="stale-provider-role")],
        )
        self.permissions = SimpleNamespace(administrator=True)
        self.response = InertInteractionResponse(defer_error=defer_error)


async def forbidden_message(*_args, **_kwargs):
    raise AssertionError("The Discord domain handler must not run")


async def forbidden_confirmation(**_kwargs):
    raise AssertionError("The Discord confirmation handler must not run")


def dispatcher(service, *, message_handler=forbidden_message, confirmation_handler=forbidden_confirmation):
    return DiscordIncomingDispatcher(
        service,
        message_handler=message_handler,
        confirmation_handler=confirmation_handler,
    )


async def test_ordinary_buffered_message_recovers_once_after_dispatcher_restart(monkeypatch):
    monkeypatch.setattr(discord_incoming, "is_recovery_hold", lambda: False)
    user_id, _identity_id, provider_user_id = await linked_admin()
    service = InertDiscord()
    accepted = dispatcher(service)
    identity = await accepted.accept(incoming(provider_user_id))
    calls: list[MessagingAuthorityBinding] = []

    async def handle_message(message, *, is_admin_hint, expected_authority):
        assert message.author_role_ids == ["current-provider-role"]
        assert is_admin_hint is True
        calls.append(expected_authority)
        return SimpleNamespace(
            session_id=str(uuid.uuid4()),
            response_text="Synthetic recovered response",
            pending_action=None,
        )

    restarted = dispatcher(service, message_handler=handle_message)
    assert await _bounded(restarted.run_once(identity))
    assert not await restarted.run_once(identity)

    row = await IncomingMessageStore().get(identity)
    assert row.state == "handled"
    assert row.reply_plan[0]["state"] == "accepted"
    assert calls == [MessagingAuthorityBinding(str(user_id), "admin", 0)]
    view = await IncomingMessageStore().recovery_detail(identity, provider="discord")
    assert "Synthetic recovered response" not in str(view)
    assert "synthetic-channel" not in str(view)


async def test_vanished_button_interaction_is_review_only_before_confirmation_handler(monkeypatch):
    monkeypatch.setattr(discord_incoming, "is_recovery_hold", lambda: False)
    _user_id, _identity_id, provider_user_id = await linked_admin()
    service = InertDiscord()
    interaction = SimpleNamespace(token="do-not-persist")
    accepted = dispatcher(service)
    identity = await accepted.accept(
        incoming(provider_user_id, text=""),
        mode="confirmation",
        interaction=interaction,
        confirmation={
            "session_id": str(uuid.uuid4()),
            "confirmation_id": "confirm-" + "a" * 32,
            "decision": "confirm",
        },
    )
    row = await IncomingMessageStore().get(identity)
    assert "do-not-persist" not in str(row.envelope)

    restarted = dispatcher(service)
    assert await _bounded(restarted.run_once(identity))
    assert not await restarted.run_once(identity)

    row = await IncomingMessageStore().get(identity)
    assert row.state == "review_required"
    assert row.review_reason == "interaction_reply_unavailable"
    assert not row.reply_plan and not service.sent and not service.interaction_sent
    view = await IncomingMessageStore().recovery_detail(identity, provider="discord")
    assert view["requires_review"] is True
    assert view["review_reason"] == "interaction_reply_unavailable"


async def test_live_slash_interaction_map_is_removed_after_single_reply(monkeypatch):
    monkeypatch.setattr(discord_incoming, "is_recovery_hold", lambda: False)
    _user_id, _identity_id, provider_user_id = await linked_admin()
    service = InertDiscord()
    worker = dispatcher(service)
    interaction = SimpleNamespace(token="memory-only")
    identity = await worker.accept(incoming(provider_user_id), mode="help", interaction=interaction)

    assert await _bounded(worker.run_once(identity, interaction=interaction))
    assert identity not in worker._interactions
    row = await IncomingMessageStore().get(identity)
    assert row.state == "handled"
    assert row.reply_plan[0]["state"] == "accepted"
    assert len(service.interaction_sent) == 1


async def test_uncertain_slash_ack_is_reviewed_before_domain_handler(monkeypatch):
    monkeypatch.setattr(discord_incoming, "is_recovery_hold", lambda: False)
    _user_id, _identity_id, provider_user_id = await linked_admin()
    service = InertDiscord()
    interaction = InertInteraction(provider_user_id, defer_error=TimeoutError("Synthetic acknowledgement lost"))
    calls: list[str] = []

    async def handle_message(*_args, **_kwargs):
        calls.append("domain-handler")
        raise AssertionError("An uncertain interaction acknowledgement cannot invoke a handler")

    gateway = DiscordIncomingGateway(
        service,
        message_handler=handle_message,
        confirmation_handler=forbidden_confirmation,
    )
    gateway.start()
    try:
        await _bounded(gateway.handle_slash_command(interaction, "ask", message="Synthetic slash request"))
    finally:
        await _bounded(gateway.stop())

    page = await IncomingMessageStore().recovery_page(provider="discord")
    assert len(page["items"]) == 1
    identity = uuid.UUID(page["items"][0]["id"])
    row = await IncomingMessageStore().get(identity)
    assert row.state == "review_required"
    assert row.review_reason == "interaction_reply_unavailable"
    assert not row.reply_plan and not calls and not service.interaction_sent
    assert interaction.response.defer_calls == 1


async def test_gateway_stop_drains_callback_and_blocks_later_intake(monkeypatch):
    monkeypatch.setattr(discord_incoming, "is_recovery_hold", lambda: False)
    _user_id, _identity_id, provider_user_id = await linked_admin()
    service = InertDiscord()
    entered = asyncio.Event()

    async def blocked_handler(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    gateway = DiscordIncomingGateway(
        service,
        message_handler=blocked_handler,
        confirmation_handler=forbidden_confirmation,
    )
    gateway.start()
    first = incoming(provider_user_id)
    callback = asyncio.create_task(gateway.handle_provider_message(first, object()))
    await _bounded(entered.wait())
    page = await IncomingMessageStore().recovery_page(provider="discord")
    identity = uuid.UUID(page["items"][0]["id"])

    await _bounded(gateway.stop())
    with pytest.raises(asyncio.CancelledError):
        await callback

    row = await IncomingMessageStore().get(identity)
    assert row.state == "review_required"
    assert row.review_reason == "handler_interrupted"
    assert not row.reply_plan
    assert not await gateway.dispatcher.run_once(identity)

    await _bounded(gateway.handle_provider_message(incoming(provider_user_id), object()))
    late_interaction = InertInteraction(provider_user_id)
    await _bounded(gateway.handle_slash_command(late_interaction, "ask", message="Late synthetic request"))
    page_after = await IncomingMessageStore().recovery_page(provider="discord")
    assert [item["id"] for item in page_after["items"]] == [str(identity)]
    assert late_interaction.response.defer_calls == 0


@pytest.mark.parametrize(
    ("condition", "reason"),
    [
        ("missing_channel", "channel_unavailable"),
        ("membership_unavailable", "discord_membership_unavailable"),
        ("admission_changed", "discord_admission_changed"),
        ("stale_sender", "sender_binding_changed"),
    ],
)
async def test_current_inbound_authorization_or_channel_failure_blocks_handler(monkeypatch, condition, reason):
    monkeypatch.setattr(discord_incoming, "is_recovery_hold", lambda: False)
    _user_id, _identity_id, provider_user_id = await linked_admin()
    service = InertDiscord()
    accepted = dispatcher(service)
    identity = await accepted.accept(incoming(provider_user_id))
    if condition == "missing_channel":
        service.channel = None
    elif condition == "membership_unavailable":
        service.membership_unavailable = True
    elif condition == "stale_sender":
        service.sender_current = False
    else:
        service.allowed = False

    worker = dispatcher(service)
    assert await _bounded(worker.run_once(identity))
    row = await IncomingMessageStore().get(identity)
    assert row.state == "review_required"
    assert row.review_reason == reason
    assert not row.reply_plan and not service.sent


@pytest.mark.parametrize("change", ["inactive", "demoted", "auth_version", "unlinked"])
async def test_changed_linked_iacs_identity_cannot_run_or_reply(monkeypatch, change):
    monkeypatch.setattr(discord_incoming, "is_recovery_hold", lambda: False)
    user_id, identity_id, provider_user_id = await linked_admin()
    service = InertDiscord()
    accepted = dispatcher(service)
    identity = await accepted.accept(incoming(provider_user_id))
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        linked_identity = await session.get(MessagingIdentity, identity_id)
        if change == "inactive":
            user.is_active = False
        elif change == "demoted":
            user.role = UserRole.STANDARD
        elif change == "auth_version":
            user.auth_session_version += 1
        else:
            linked_identity.user_id = None
        await session.commit()

    worker = dispatcher(service)
    assert await _bounded(worker.run_once(identity))
    row = await IncomingMessageStore().get(identity)
    assert row.state == "review_required"
    assert row.review_reason == "sender_binding_changed"
    assert not row.reply_plan and not service.sent


async def test_uncertain_reply_after_handler_commit_is_reviewed_without_repeat(monkeypatch):
    monkeypatch.setattr(discord_incoming, "is_recovery_hold", lambda: False)
    user_id, _identity_id, provider_user_id = await linked_admin()
    service = InertDiscord()
    service.reply_error = TimeoutError("Synthetic response lost after provider acceptance")
    accepted = dispatcher(service)
    identity = await accepted.accept(incoming(provider_user_id))
    calls: list[str] = []

    async def handle_message(_message, *, is_admin_hint, expected_authority):
        assert is_admin_hint is True
        assert expected_authority == MessagingAuthorityBinding(str(user_id), "admin", 0)
        calls.append("domain-handler")
        async with AsyncSessionLocal() as session:
            user = await session.get(User, user_id)
            user.first_name = "Committed before reply uncertainty"
            await session.commit()
        return SimpleNamespace(
            session_id=str(uuid.uuid4()),
            response_text="Synthetic uncertain reply",
            pending_action=None,
        )

    worker = dispatcher(service, message_handler=handle_message)
    assert await _bounded(worker.run_once(identity))
    assert not await worker.run_once(identity)

    row = await IncomingMessageStore().get(identity)
    assert row.state == "review_required"
    assert row.reply_plan[0]["state"] == "unknown"
    assert calls == ["domain-handler"] and len(service.sent) == 1
    async with AsyncSessionLocal() as session:
        assert (await session.get(User, user_id)).first_name == "Committed before reply uncertainty"


async def test_cancelled_gateway_shutdown_drains_slow_review_checkpoint(monkeypatch):
    monkeypatch.setattr(discord_incoming, "is_recovery_hold", lambda: False)
    _user, _link, provider_user = await linked_admin()
    handler_entered, checkpoint_entered, checkpoint_release = (asyncio.Event() for _ in range(3))

    async def handler(*_args, **_kwargs):
        handler_entered.set()
        await asyncio.Event().wait()

    gateway = DiscordIncomingGateway(InertDiscord(), message_handler=handler,
                                     confirmation_handler=forbidden_confirmation)
    original_interrupt = gateway.dispatcher.store.interrupt

    async def slow_interrupt(*args, **kwargs):
        checkpoint_entered.set()
        await checkpoint_release.wait()
        return await original_interrupt(*args, **kwargs)

    monkeypatch.setattr(gateway.dispatcher.store, "interrupt", slow_interrupt)
    gateway.start()
    callback = asyncio.create_task(gateway.handle_provider_message(incoming(provider_user), object()))
    stop = None
    try:
        await _bounded(handler_entered.wait())
        stop = asyncio.create_task(gateway.stop())
        await _bounded(checkpoint_entered.wait())
        stop.cancel()
        callback.cancel()  # Second cancellation cannot detach the durable checkpoint.
        cancellation_observed = asyncio.Event()
        asyncio.get_running_loop().call_soon(cancellation_observed.set)
        await _bounded(cancellation_observed.wait())
        assert not stop.done() and not callback.done()
        assert gateway.dispatcher._task is not None
        # A lifecycle owner cannot advance to closing sinks while the durable
        # checkpoint is still deliberately blocked.
        checkpoint_release.set()
        with pytest.raises(asyncio.CancelledError):
            await _bounded(stop)
        await asyncio.gather(callback, return_exceptions=True)
        assert not gateway._active_callbacks and not gateway.dispatcher._active_runs
        assert gateway.dispatcher._task is None
        rows = (await IncomingMessageStore().recovery_page(provider="discord"))["items"]
        assert len(rows) == 1 and rows[0]["review_reason"] == "handler_interrupted"
    finally:
        checkpoint_release.set()
        callback.cancel()
        await asyncio.gather(callback, return_exceptions=True)
        if stop is not None:
            await asyncio.gather(stop, return_exceptions=True)
        await gateway.stop()


async def test_shutdown_cancels_preclaim_intake_before_waiting_for_intake_lock(monkeypatch):
    monkeypatch.setattr(discord_incoming, "is_recovery_hold", lambda: False)
    _user, _link, provider_user = await linked_admin()
    entered, cancelled = asyncio.Event(), asyncio.Event()
    gateway = DiscordIncomingGateway(InertDiscord(), message_handler=forbidden_message,
                                     confirmation_handler=forbidden_confirmation)

    async def blocked_accept(*_args, **_kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(gateway.dispatcher.store, "accept_in_session", blocked_accept)
    gateway.start()
    callback = asyncio.create_task(gateway.handle_provider_message(incoming(provider_user), object()))
    try:
        await _bounded(entered.wait())
        await _bounded(gateway.stop())
        assert cancelled.is_set() and callback.done()
        assert not gateway._active_callbacks and gateway.dispatcher._task is None
        assert not (await IncomingMessageStore().recovery_page(provider="discord"))["items"]
    finally:
        callback.cancel()
        await asyncio.gather(callback, return_exceptions=True)
        await gateway.stop()


async def test_closed_intake_blocks_buffered_and_prehandler_work(monkeypatch):
    monkeypatch.setattr(discord_incoming, "is_recovery_hold", lambda: False)
    _user, _link, provider_user = await linked_admin()
    worker = dispatcher(InertDiscord())
    first = await worker.accept(incoming(provider_user))
    second = await worker.accept(incoming(provider_user))
    entered, release = asyncio.Event(), asyncio.Event()
    current_input = worker._current_input
    async def paused_input(row):
        value = await current_input(row)
        entered.set()
        await release.wait()
        return value
    monkeypatch.setattr(worker, "_current_input", paused_input)
    task = asyncio.create_task(worker.run_once(first))
    try:
        await _bounded(entered.wait())
        await worker.close_intake()
        assert not await worker.run_once(second)
        release.set()
        assert await _bounded(task)
        assert (await worker.store.get(first)).review_reason == "handler_interrupted"
        assert (await worker.store.get(second)).state == "received"
        assert not worker.service.sent
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await worker.stop()
