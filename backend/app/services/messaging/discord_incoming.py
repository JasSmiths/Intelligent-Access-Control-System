"""Discord accepted-input recovery with requester-bound, non-replayed replies."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
import uuid

from sqlalchemy import select

from app.core.logging import get_logger
from app.core.recovery_hold import is_recovery_hold
from app.models import MessagingIdentity, User
from app.modules.messaging.base import IncomingChatMessage, MessagingAuthorityBinding, MessagingAuthorityChanged
from app.modules.messaging.discord_bot import discord_author_is_provider_admin
from app.modules.notifications.base import NotificationDeliveryError
from app.services.messaging.incoming_messages import IncomingMessageClaimLost, IncomingMessageStore
from app.core.task_lifecycle import drain_owned

logger = get_logger(__name__)
POLL_SECONDS = 2
HANDLER_TIMEOUT_SECONDS = 240
MAX_INTERACTIONS = 128



_MODES = frozenset({"message", "slash", "confirmation", "help"})
_REVIEW_REASONS = frozenset({
    "channel_unavailable",
    "discord_admission_changed",
    "discord_membership_unavailable",
    "incoming_snapshot_unavailable",
    "interaction_reply_unavailable",
    "reply_receipt_lost",
    "sender_binding_changed",
})


async def current_authority(session, provider_user_id: str, *, lock: bool = False) -> MessagingAuthorityBinding:
    """Resolve only the current linked IACS user; provider-admin data is never authority."""
    query = select(MessagingIdentity).where(
        MessagingIdentity.provider == "discord",
        MessagingIdentity.provider_user_id == provider_user_id,
    )
    if lock:
        query = query.with_for_update()
    identity = await session.scalar(query.execution_options(populate_existing=True))
    user = (
        await session.get(User, identity.user_id, with_for_update=lock, populate_existing=True)
        if identity and identity.user_id
        else None
    )
    return MessagingAuthorityBinding(
        user_id=str(user.id) if user and user.is_active else None,
        user_role=user.role.value if user and user.is_active else "standard",
        auth_session_version=user.auth_session_version if user and user.is_active else None,
    )


def message_snapshot(message: IncomingChatMessage) -> dict[str, Any]:
    """Persist only replay-safe input; Discord interaction credentials never enter the envelope."""
    return {
        "type": "text",
        "text": {"body": message.text},
        "display_name": message.author_display_name,
        "guild_id": message.provider_guild_id,
        "thread_id": message.provider_thread_id,
        "is_direct_message": message.is_direct_message,
        "mentioned_bot": message.mentioned_bot,
        "role_ids": message.author_role_ids,
    }


def restored_message(row) -> IncomingChatMessage:
    envelope = row.envelope if isinstance(row.envelope, dict) else {}
    value = envelope.get("message")
    text = value.get("text") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("type") != "text"
        or not isinstance(text, dict)
        or not isinstance(text.get("body"), str)
        or not isinstance(value.get("role_ids", []), list)
    ):
        raise MessagingAuthorityChanged("incoming_snapshot_unavailable")
    return IncomingChatMessage(
        provider="discord",
        provider_message_id=row.provider_message_id,
        provider_channel_id=row.provider_channel_id or "",
        author_provider_id=row.author_provider_id or "",
        author_display_name=str(value.get("display_name") or "Discord user"),
        text=text["body"],
        is_direct_message=bool(value.get("is_direct_message")),
        mentioned_bot=bool(value.get("mentioned_bot")),
        raw_payload={},
        received_at=row.received_at,
        provider_guild_id=value.get("guild_id") if isinstance(value.get("guild_id"), str) else None,
        provider_thread_id=value.get("thread_id") if isinstance(value.get("thread_id"), str) else None,
        author_role_ids=[str(role) for role in value.get("role_ids", []) if str(role).strip()],
    )


def authority_snapshot(binding: Any) -> MessagingAuthorityBinding:
    if not isinstance(binding, dict):
        raise MessagingAuthorityChanged("sender_binding_changed")
    try:
        return MessagingAuthorityBinding(binding.get("user_id"), binding["kind"], binding.get("auth_version"))
    except (KeyError, TypeError, ValueError) as exc:
        raise MessagingAuthorityChanged("sender_binding_changed") from exc


def _mode(row) -> str:
    envelope = row.envelope if isinstance(row.envelope, dict) else {}
    mode = envelope.get("mode")
    if mode not in _MODES:
        raise MessagingAuthorityChanged("incoming_snapshot_unavailable")
    return mode


def _confirmation(row) -> dict[str, str]:
    envelope = row.envelope if isinstance(row.envelope, dict) else {}
    value = envelope.get("confirmation")
    if (
        not isinstance(value, dict)
        or set(value) != {"session_id", "confirmation_id", "decision"}
        or value.get("decision") not in {"confirm", "cancel"}
        or any(not isinstance(value.get(key), str) or not value[key].strip() for key in value)
    ):
        raise MessagingAuthorityChanged("incoming_snapshot_unavailable")
    return {key: value[key] for key in ("session_id", "confirmation_id", "decision")}


def _review_reason(error: BaseException) -> str:
    reason = str(error)
    return reason if reason in _REVIEW_REASONS else "sender_binding_changed"


MessageHandler = Callable[..., Awaitable[Any]]
ConfirmationHandler = Callable[..., Awaitable[Any]]


class DiscordIncomingStopped(RuntimeError):
    """Lifecycle has closed Discord intake before this durable operation began."""


def incoming_from_interaction(interaction: Any, text: str) -> IncomingChatMessage:
    guild = getattr(interaction, "guild", None)
    channel = getattr(interaction, "channel", None)
    user = getattr(interaction, "user", None)
    interaction_permissions = getattr(interaction, "permissions", None)
    author_is_provider_admin = discord_author_is_provider_admin(user, guild) or bool(
        getattr(interaction_permissions, "administrator", False)
    )
    role_ids = [
        str(getattr(role, "id", ""))
        for role in getattr(user, "roles", []) or []
        if str(getattr(role, "id", "")).strip()
    ]
    return IncomingChatMessage(
        provider="discord",
        provider_message_id=str(getattr(interaction, "id", "")),
        provider_channel_id=str(getattr(channel, "id", "")),
        provider_guild_id=str(getattr(guild, "id", "")) if guild else None,
        author_provider_id=str(getattr(user, "id", "")),
        author_display_name=str(getattr(user, "display_name", None) or getattr(user, "name", "") or "Discord user"),
        author_role_ids=role_ids,
        author_is_provider_admin=author_is_provider_admin,
        text=text,
        is_direct_message=guild is None,
        mentioned_bot=True,
        raw_payload={
            "interaction_id": str(getattr(interaction, "id", "")),
            "command": "alfred",
            "channel_id": str(getattr(channel, "id", "")),
            "guild_id": str(getattr(guild, "id", "")) if guild else None,
            "author_is_provider_admin": author_is_provider_admin,
        },
        received_at=datetime.now(tz=UTC),
    )


def command_text(command: str, message: str | None = None) -> str:
    if command == "ask":
        return str(message or "").strip()
    prompts = {
        "status": "Give a concise current IACS status: who is home, gate state, and active alerts.",
        "last_event": "Explain the most recent access event in one concise paragraph.",
        "arrivals_today": "Summarise today's known and unknown arrivals.",
        "presence": "Show current home occupancy.",
    }
    return prompts.get(command, command)


def notify_test_command_text(channel_id: str) -> str:
    return (
        "Prepare a Discord notification test for this channel. "
        "Use the test_notification_workflow tool with confirm_send=false and an unsaved notification rule. "
        "The rule should be named 'IACS Discord notification test', use trigger_event 'integration_test', "
        "and have one action with type 'discord', target_mode 'selected', "
        f"target_ids ['discord:{channel_id}'], title_template 'IACS Discord notification test', "
        "and message_template 'Discord notifications are configured and reachable.'"
    )


DISCORD_HELP_TEXT = (
    "**Alfred Discord commands**\n"
    "Warm, witty, and annoyingly serious about safety.\n"
    "`/alfred status` - concise site state\n"
    "`/alfred last_event` - Explain the most recent access event\n"
    "`/alfred arrivals_today` - Summarise today's known and unknown arrivals\n"
    "`/alfred presence` - Show current home occupancy\n"
    "`/alfred ask <message>` - ask Alfred naturally\n"
    "`/alfred notify_test` - Admin-only test notification, with confirmation"
)


class DiscordIncomingGateway:
    """Provider callbacks plus durable intake; bridge work is injected by lifecycle."""

    def __init__(
        self,
        service: Any,
        *,
        message_handler: MessageHandler,
        confirmation_handler: ConfirmationHandler,
        store: IncomingMessageStore | None = None,
    ) -> None:
        self.service = service
        self.dispatcher = DiscordIncomingDispatcher(
            service,
            message_handler=message_handler,
            confirmation_handler=confirmation_handler,
            store=store,
        )
        self._accepting = False
        self._lifecycle_lock = asyncio.Lock()
        self._active_callbacks: set[asyncio.Task[Any]] = set()

    def start(self) -> None:
        self._accepting = True
        self.dispatcher.start()

    async def stop(self) -> None:
        await drain_owned(self._stop())

    async def _stop(self) -> None:
        async with self._lifecycle_lock:
            self._accepting = False
            callbacks = tuple(self._active_callbacks)
        await self.dispatcher.close_intake()
        current = asyncio.current_task()
        pending = [task for task in callbacks if task is not current and not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await self.dispatcher.stop()

    async def _run_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        task = asyncio.current_task()
        if task is None:
            return
        async with self._lifecycle_lock:
            if not self._accepting:
                return
            self._active_callbacks.add(task)
        try:
            await callback()
        finally:
            async with self._lifecycle_lock:
                self._active_callbacks.discard(task)

    async def _intake_open(self) -> bool:
        async with self._lifecycle_lock:
            return self._accepting

    async def handle_bot_ready(self) -> None:
        async def callback() -> None:
            await self.service.handle_bot_ready()
            self.dispatcher.wake()

        await self._run_callback(callback)

    async def handle_provider_message(self, message: IncomingChatMessage, _provider_message: Any) -> None:
        async def callback() -> None:
            allowed, reason = await self.service.message_is_allowed(message)
            if not allowed:
                logger.info(
                    "discord_message_denied",
                    extra={
                        "reason": reason,
                        "guild_id": message.provider_guild_id,
                        "channel_id": message.provider_channel_id,
                        "author_id": message.author_provider_id,
                    },
                )
                return
            if not message.text:
                return
            try:
                identity = await self.dispatcher.accept(message, hold_for_direct=True)
            except DiscordIncomingStopped:
                return
            if not await self._intake_open():
                return
            await self.dispatcher.run_once(identity)

        await self._run_callback(callback)

    async def handle_slash_command(self, interaction: Any, command: str, *, message: str | None = None) -> None:
        async def callback() -> None:
            incoming = incoming_from_interaction(interaction, command_text(command, message))
            allowed, reason = await self.service.message_is_allowed(incoming, slash_command=True)
            if not allowed:
                await interaction.response.send_message(f"Discord access denied: {reason}.", ephemeral=True)
                return
            if command == "notify_test":
                if not await self.service.author_is_admin(incoming.author_provider_id, incoming.author_role_ids):
                    await interaction.response.send_message(
                        "Admin permission is required for `/alfred notify_test`.",
                        ephemeral=True,
                    )
                    return
                async with self.dispatcher.store.sessions() as session:
                    config = await self.service.config_for_session(session)
                incoming = replace(
                    incoming,
                    text=notify_test_command_text(incoming.provider_channel_id or config.default_notification_channel_id),
                )
            try:
                identity = await self.dispatcher.accept(
                    incoming,
                    mode="help" if command == "help" else "slash",
                    interaction=interaction,
                    hold_for_direct=True,
                )
            except DiscordIncomingStopped:
                return
            if not await self._intake_open():
                self.dispatcher.discard_interaction(identity)
                return
            try:
                await interaction.response.defer(thinking=command != "help", ephemeral=command == "help")
            except Exception:
                # A failed acknowledgement leaves the interaction's reply path
                # uncertain. Retain it for review, but do not invoke a handler.
                self.dispatcher.discard_interaction(identity)
                logger.warning("discord_interaction_ack_uncertain", extra={"incoming_id": str(identity)})
                await self.dispatcher.run_once(identity)
                return
            await self.dispatcher.run_once(identity, interaction=interaction)

        await self._run_callback(callback)

    async def handle_confirmation_interaction(
        self,
        interaction: Any,
        *,
        session_id: str,
        confirmation_id: str,
        decision: str,
    ) -> None:
        async def callback() -> None:
            incoming = incoming_from_interaction(interaction, "")
            allowed, reason = await self.service.message_is_allowed(incoming, slash_command=True)
            if not allowed:
                await interaction.response.send_message(f"Discord access denied: {reason}.", ephemeral=True)
                return
            if not await self.service._linked_admin_user_id(incoming.author_provider_id):
                await interaction.response.send_message(
                    "Admin permission is required to resolve this action. Link this Discord identity to an active IACS Admin account first.",
                    ephemeral=True,
                )
                return
            try:
                identity = await self.dispatcher.accept(
                    incoming,
                    mode="confirmation",
                    interaction=interaction,
                    confirmation={"session_id": session_id, "confirmation_id": confirmation_id, "decision": decision},
                    hold_for_direct=True,
                )
            except DiscordIncomingStopped:
                return
            if not await self._intake_open():
                self.dispatcher.discard_interaction(identity)
                return
            try:
                await interaction.response.defer(thinking=True, ephemeral=True)
            except Exception:
                self.dispatcher.discard_interaction(identity)
                logger.warning("discord_interaction_ack_uncertain", extra={"incoming_id": str(identity)})
                await self.dispatcher.run_once(identity)
                return
            await self.dispatcher.run_once(identity, interaction=interaction)

        await self._run_callback(callback)


class DiscordIncomingDispatcher:
    def __init__(
        self,
        service: Any,
        *,
        message_handler: MessageHandler,
        confirmation_handler: ConfirmationHandler,
        store: IncomingMessageStore | None = None,
    ):
        self.service, self.store = service, store or IncomingMessageStore()
        self.message_handler = message_handler
        self.confirmation_handler = confirmation_handler
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._intake_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        # Direct provider/slash callbacks invoke run_once too. Keep each owned
        # task here so shutdown waits for every claimed handler to checkpoint.
        self._active_runs: set[asyncio.Task[Any]] = set()
        self._accepting = True
        # This in-memory map is deliberately non-durable: interaction tokens
        # disappear on restart, so their accepted work becomes review-only.
        self._interactions: dict[uuid.UUID, Any] = {}
        # The polling worker must never race a live provider callback before it
        # has durably acknowledged its interaction. That callback claims it.
        self._pending_direct_ids: set[uuid.UUID] = set()

    def start(self) -> None:
        self._accepting = True
        if self._task is None:
            self._task = asyncio.create_task(self._worker(), name="discord-incoming")

    async def stop(self) -> None:
        await drain_owned(self._stop())

    async def close_intake(self) -> None:
        async with self._lifecycle_lock:
            self._accepting = False

    async def _stop(self) -> None:
        async with self._lifecycle_lock:
            self._accepting = False
            active = tuple(self._active_runs)
        task, self._task = self._task, None
        current = asyncio.current_task()
        pending = {candidate for candidate in (*active, task) if candidate is not None and candidate is not current and not candidate.done()}
        for candidate in pending:
            candidate.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        # A callback that was already persisting accepted input is not an active
        # run yet. Wait for that critical section before returning, then discard
        # non-durable interaction handles so restart cannot replay them.
        async with self._intake_lock:
            self._interactions.clear()
            self._pending_direct_ids.clear()

    def wake(self) -> None:
        if self._accepting:
            self._wake.set()

    def discard_interaction(self, identity: uuid.UUID) -> None:
        self._interactions.pop(identity, None)

    async def accept(
        self,
        message: IncomingChatMessage,
        *,
        mode: str = "message",
        confirmation: dict[str, str] | None = None,
        interaction: Any | None = None,
        hold_for_direct: bool = False,
    ) -> uuid.UUID:
        if message.provider != "discord" or mode not in _MODES:
            raise ValueError("Unsupported Discord intake")
        envelope: dict[str, Any] = {"message": message_snapshot(message), "mode": mode}
        if mode == "confirmation":
            if (
                not isinstance(confirmation, dict)
                or set(confirmation) != {"session_id", "confirmation_id", "decision"}
                or confirmation.get("decision") not in {"confirm", "cancel"}
                or any(not isinstance(confirmation.get(key), str) or not confirmation[key].strip() for key in confirmation)
            ):
                raise ValueError("An exact confirmation input is required")
            envelope["confirmation"] = dict(confirmation)
        async with self._intake_lock:
            async with self._lifecycle_lock:
                if not self._accepting:
                    raise DiscordIncomingStopped("Discord incoming intake is closed.")
            async with self.store.sessions() as session:
                actor = await current_authority(session, message.author_provider_id)
                identity = await self.store.accept_in_session(
                    session,
                    provider="discord",
                    provider_message_id=message.provider_message_id,
                    provider_channel_id=message.provider_channel_id,
                    author_provider_id=message.author_provider_id,
                    envelope=envelope,
                    routing_context={
                        "kind": actor.user_role,
                        "user_id": actor.user_id,
                        "auth_version": actor.auth_session_version,
                    },
                    received_at=message.received_at,
                )
                await session.commit()
            if interaction is not None:
                if identity not in self._interactions and len(self._interactions) >= MAX_INTERACTIONS:
                    oldest = next(iter(self._interactions))
                    self._interactions.pop(oldest)
                self._interactions[identity] = interaction
            if hold_for_direct:
                self._pending_direct_ids.add(identity)
        return identity

    async def _worker(self) -> None:
        while True:
            self._wake.clear()
            try:
                for _ in range(20):
                    if not await self.run_once():
                        break
            except Exception:
                logger.exception("discord_incoming_tick_failed")
            try:
                await asyncio.wait_for(self._wake.wait(), POLL_SECONDS)
            except TimeoutError:
                pass

    async def _current_input(self, row) -> IncomingChatMessage:
        incoming = restored_message(row)
        # Fetch membership outside a database transaction. The final admission
        # and linked-IACS-user checks below are a fresh transaction immediately
        # before any Alfred/domain work.
        async with self.store.sessions() as session:
            config = await self.service.config_for_session(session)
        incoming = await self.service.refresh_incoming_membership(incoming, config=config)
        async with self.store.sessions() as session:
            config = await self.service.config_for_session(session)
            allowed, _reason = await self.service.message_is_allowed(
                incoming,
                slash_command=_mode(row) != "message",
                config=config,
            )
            if not allowed:
                raise MessagingAuthorityChanged("discord_admission_changed")
            if await current_authority(session, incoming.author_provider_id, lock=True) != authority_snapshot(row.routing_context):
                raise MessagingAuthorityChanged("sender_binding_changed")
            await session.commit()
        return incoming

    async def run_once(self, identity: uuid.UUID | None = None, *, interaction: Any | None = None) -> bool:
        task = asyncio.current_task()
        if task is None:
            return False
        async with self._lifecycle_lock:
            if not self._accepting:
                return False
            self._active_runs.add(task)
        try:
            return await self._run_once(identity, interaction=interaction)
        finally:
            async with self._lifecycle_lock:
                self._active_runs.discard(task)

    async def _run_once(self, identity: uuid.UUID | None = None, *, interaction: Any | None = None) -> bool:
        # Do not execute a domain handler unless the connected bot can make the
        # checkpointed reply attempt. Buffered normal messages can wait; a lost
        # slash/button interaction is terminalized after it is claimed below.
        claim = None
        result_ids: dict[str, Any] = {}
        try:
            if is_recovery_hold() or not self.service.delivery_ready():
                return False
            async with self._intake_lock:
                if identity is None and self._pending_direct_ids:
                    return False
                claim = await self.store.claim(identity, provider="discord")
                if claim is None:
                    if identity is not None:
                        self.discard_interaction(identity)
                        self._pending_direct_ids.discard(identity)
                    return False
                mapped_interaction = self._interactions.pop(claim.batch_id, None)
                self._pending_direct_ids.discard(claim.batch_id)
            if interaction is None:
                interaction = mapped_interaction
            async with self.store.sessions() as session:
                rows, _ = await self.store.owned(session, claim)
                if len(rows) != 1 or rows[0].id != claim.batch_id:
                    raise MessagingAuthorityChanged("incoming_snapshot_unavailable")
                row = rows[0]
                mode = _mode(row)
                await session.commit()
            # There is no durable interaction token. Once it is gone, the
            # accepted command/button cannot safely run or be replayed publicly.
            if mode != "message" and interaction is None:
                await self.store.interrupt(claim, "interaction_reply_unavailable", result=result_ids)
                return True
            async with asyncio.timeout(HANDLER_TIMEOUT_SECONDS):
                incoming = await self._current_input(row)
                try:
                    await self.service.assert_current_sender()
                except NotificationDeliveryError:
                    raise MessagingAuthorityChanged("sender_binding_changed") from None
                if mode == "message" and await self.service._resolve_channel(incoming.provider_channel_id) is None:
                    await self.store.interrupt(claim, "channel_unavailable", result=result_ids)
                    return True
                if not self._accepting:
                    await self.store.interrupt(claim, "handler_interrupted", result=result_ids)
                    return True
                if mode == "confirmation":
                    approval = _confirmation(row)
                    authority = authority_snapshot(row.routing_context)
                    if authority.user_role != "admin" or not authority.user_id:
                        raise MessagingAuthorityChanged("sender_binding_changed")
                    result = await self.confirmation_handler(
                        **approval,
                        user_id=authority.user_id,
                        user_role=authority.user_role,
                    )
                    result_ids = {key: approval[key] for key in ("session_id", "confirmation_id")}
                    response_text, pending = result.response_text, None
                elif mode == "help":
                    response_text, pending = DISCORD_HELP_TEXT, None
                else:
                    authority = authority_snapshot(row.routing_context)
                    result = await self.message_handler(
                        incoming,
                        is_admin_hint=authority.user_role == "admin",
                        expected_authority=authority,
                    )
                    response_text, pending = result.response_text, result.pending_action
                    result_ids = {
                        "session_id": result.session_id,
                        "confirmation_id": (pending or {}).get("confirmation_id"),
                    }
                await self._reply(claim, row, response_text, pending, interaction=interaction)
                async with self.store.sessions() as session:
                    await self.store.finish_handling_in_session(session, claim, result_ids)
                    await session.commit()
        except (IncomingMessageClaimLost, MessagingAuthorityChanged) as exc:
            if claim is None:
                raise
            await self.store.interrupt(claim, _review_reason(exc), result=result_ids)
        except TimeoutError:
            if claim is None:
                raise
            await self.store.interrupt(claim, "handler_interrupted", result=result_ids)
        except asyncio.CancelledError:
            if claim is not None:
                await drain_owned(self.store.interrupt(claim, "handler_interrupted", result=result_ids))
            raise
        except Exception:
            if claim is None:
                raise
            await self.store.interrupt(claim, "handler_failed", result=result_ids)
            logger.exception("discord_incoming_handler_failed", extra={"incoming_id": str(claim.batch_id)})
        finally:
            if claim is None and identity is not None:
                self._pending_direct_ids.discard(identity)
        return True

    async def _reply(self, claim, row, response_text, pending, *, interaction: Any | None) -> None:
        incoming = await self._current_input(row)
        # Preflight before durable attempt; the provider method repeats this
        # check immediately before its actual send.
        await self.service.assert_current_sender()
        payload = {
            "content": (response_text or "Alfred completed the request.")[:1900],
            "pending_action": pending,
            "requester": incoming.author_display_name,
            "mode": _mode(row),
        }
        async with self.store.sessions() as session:
            await self.store.owned(session, claim)
            if await current_authority(session, incoming.author_provider_id, lock=True) != authority_snapshot(row.routing_context):
                raise MessagingAuthorityChanged("sender_binding_changed")
            await self.store.begin_reply_in_session(
                session,
                claim,
                index=0,
                recipient=incoming.provider_channel_id,
                payload=payload,
            )
            await session.commit()
        try:
            if interaction is not None:
                result = await self.service.send_incoming_interaction_reply(interaction, payload)
            else:
                channel = await self.service._resolve_channel(incoming.provider_channel_id)
                if channel is None:
                    raise NotificationDeliveryError("Discord reply channel unavailable", delivery="not_sent")
                result = await self.service._send_chat_result(
                    channel,
                    payload["content"],
                    pending,
                    incoming.author_display_name,
                )
        except NotificationDeliveryError as exc:
            # A provider can prove a pre-send failure. Preserve that narrower
            # fact, while the surrounding claimed handler still becomes review
            # only and is never replayed after its domain work may have run.
            await drain_owned(self.store.record_reply_outcome(claim, 0, delivery=exc.delivery))
            raise
        except BaseException:
            await drain_owned(self.store.record_reply_outcome(claim, 0, delivery="unknown"))
            raise
        if not await self.store.record_reply_outcome(
            claim,
            0,
            delivery="accepted",
            result={"provider_message_id": str(getattr(result, "id", "") or "")},
        ):
            raise IncomingMessageClaimLost("reply_receipt_lost")
