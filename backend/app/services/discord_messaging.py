from __future__ import annotations

import asyncio
import re
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import MessagingIdentity
from app.models.enums import UserRole
from app.modules.messaging.base import IncomingChatMessage
from app.modules.messaging.discord_bot import (
    DiscordConfirmationView,
    IacsDiscordBot,
    discord,
    discord_author_is_provider_admin,
    discord_library_available,
)
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError
from app.modules.notifications.discord_formatter import (
    DiscordEmbedPayload,
    format_confirmation_embed,
    format_discord_notification,
)
from app.services.event_bus import event_bus
from app.services.settings import get_runtime_config

logger = get_logger(__name__)


@asynccontextmanager
async def discord_typing(channel: Any):
    typing = getattr(channel, "typing", None)
    if not callable(typing):
        yield
        return
    try:
        manager = typing()
    except Exception as exc:
        logger.info("discord_typing_unavailable", extra={"error": str(exc)[:160]})
        yield
        return
    if not hasattr(manager, "__aenter__") or not hasattr(manager, "__aexit__"):
        yield
        return
    try:
        await manager.__aenter__()
    except Exception as exc:
        logger.info("discord_typing_unavailable", extra={"error": str(exc)[:160]})
        yield
        return
    try:
        yield
    except BaseException:
        suppress = await manager.__aexit__(*sys.exc_info())
        if not suppress:
            raise
    else:
        await manager.__aexit__(None, None, None)


@dataclass(frozen=True)
class DiscordIntegrationConfig:
    bot_token: str
    guild_allowlist: set[str]
    channel_allowlist: set[str]
    user_allowlist: set[str]
    role_allowlist: set[str]
    admin_role_ids: set[str]
    default_notification_channel_id: str
    allow_direct_messages: bool
    require_mention: bool

    @property
    def configured(self) -> bool:
        return bool(self.bot_token)


@dataclass(frozen=True)
class DiscordConfirmationResult:
    response_text: str


class DiscordMessagingService:
    def __init__(self) -> None:
        self._client: Any | None = None
        self._task: asyncio.Task | None = None
        self._started = False
        self._last_error: str | None = None
        self._ready_at: datetime | None = None

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        config = await load_discord_config()
        if not config.bot_token:
            self._last_error = "Discord bot token is not configured."
            logger.warning("discord_bot_token_missing")
            return
        if not discord_library_available() or IacsDiscordBot is None:
            self._last_error = "discord.py is not installed. Rebuild the backend image after dependency enrollment."
            logger.warning("discord_library_missing")
            return
        self._client = IacsDiscordBot(self)
        self._task = asyncio.create_task(self._run_client(config.bot_token))
        logger.info("discord_bot_starting")

    async def stop(self) -> None:
        self._started = False
        client = self._client
        task = self._task
        self._client = None
        self._task = None
        if client is not None:
            try:
                await client.close()
            except Exception as exc:
                logger.warning("discord_bot_close_failed", extra={"error": str(exc)})
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
            except Exception:
                pass
        logger.info("discord_bot_stopped")

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def _run_client(self, token: str) -> None:
        try:
            await self._client.start(token)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("discord_bot_runtime_error", extra={"error": str(exc)})

    async def handle_bot_ready(self) -> None:
        self._ready_at = datetime.now(tz=UTC)
        self._last_error = None
        client = self._client
        logger.info(
            "discord_bot_ready",
            extra={
                "guild_count": len(getattr(client, "guilds", []) or []),
                "user_id": str(getattr(getattr(client, "user", None), "id", "") or ""),
            },
        )
        await event_bus.publish(
            "discord.status",
            {
                "connected": True,
                "guild_count": len(getattr(client, "guilds", []) or []),
            },
        )

    async def status(self) -> dict[str, Any]:
        config = await load_discord_config()
        client = self._client
        connected = bool(client and not client.is_closed() and getattr(client, "user", None))
        guilds = getattr(client, "guilds", []) if client else []
        return {
            "configured": config.configured,
            "connected": connected,
            "library_available": discord_library_available(),
            "guild_count": len(guilds or []),
            "channel_count": len(await self.available_channels()),
            "default_notification_channel_id": config.default_notification_channel_id,
            "allow_direct_messages": config.allow_direct_messages,
            "require_mention": config.require_mention,
            "last_error": self._last_error,
            "ready_at": self._ready_at.isoformat() if self._ready_at else None,
        }

    async def available_channels(self) -> list[dict[str, str]]:
        config = await load_discord_config()
        channels: list[dict[str, str]] = []
        client = self._client
        if client is not None and getattr(client, "guilds", None):
            for guild in client.guilds:
                guild_id = str(getattr(guild, "id", ""))
                if config.guild_allowlist and guild_id not in config.guild_allowlist:
                    continue
                for channel in getattr(guild, "text_channels", []) or []:
                    channel_id = str(getattr(channel, "id", ""))
                    if config.channel_allowlist and channel_id not in config.channel_allowlist:
                        continue
                    channels.append(
                        {
                            "id": channel_id,
                            "guild_id": guild_id,
                            "name": str(getattr(channel, "name", channel_id)),
                            "label": f"#{getattr(channel, 'name', channel_id)}",
                        }
                    )
        known_ids = list(config.channel_allowlist)
        if config.default_notification_channel_id:
            known_ids.insert(0, config.default_notification_channel_id)
        existing = {row["id"] for row in channels}
        for channel_id in known_ids:
            if channel_id and channel_id not in existing:
                channels.append({"id": channel_id, "guild_id": "", "name": channel_id, "label": f"Discord channel {channel_id}"})
                existing.add(channel_id)
        return channels

    async def test_connection(self, values: dict[str, Any]) -> None:
        token = str(values.get("discord_bot_token") or "").strip()
        if not token:
            token = (await get_runtime_config()).discord_bot_token
        if not token:
            raise ValueError("Discord bot token is required.")
        if not discord_library_available() or discord is None:
            raise ValueError("discord.py is not installed. Rebuild the backend image.")
        client = discord.Client(intents=discord.Intents.none())
        try:
            await client.login(token)
        finally:
            await client.close()

    async def handle_provider_message(self, message: IncomingChatMessage, provider_message: Any) -> None:
        allowed, reason = await self.message_is_allowed(message)
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
        is_admin = await self.author_is_admin(
            message.author_provider_id,
            message.author_role_ids,
            provider_admin=message.author_is_provider_admin,
        )
        from app.services.messaging_bridge import messaging_bridge_service

        channel = provider_message.channel
        async with discord_typing(channel):
            result = await messaging_bridge_service.handle_message(message, is_admin_hint=is_admin)
            await self._send_chat_result(channel, result.response_text, result.pending_action, message.author_display_name)

    async def handle_slash_command(self, interaction: Any, command: str, *, message: str | None = None) -> None:
        incoming = incoming_from_interaction(interaction, command_text(command, message))
        try:
            allowed, reason = await self.message_is_allowed(incoming, slash_command=True)
            if not allowed:
                await interaction.response.send_message(f"Discord access denied: {reason}.", ephemeral=True)
                return
            if command == "help":
                await interaction.response.send_message(DISCORD_HELP_TEXT, ephemeral=True)
                return
            is_admin = await self.author_is_admin(
                incoming.author_provider_id,
                incoming.author_role_ids,
                provider_admin=incoming.author_is_provider_admin,
            )
            if command == "notify_test":
                if not is_admin:
                    await interaction.response.send_message("Admin permission is required for `/alfred notify_test`.", ephemeral=True)
                    return
                await interaction.response.defer(thinking=True)
                target_channel_id = incoming.provider_channel_id or (await load_discord_config()).default_notification_channel_id
                incoming = incoming_from_interaction(interaction, notify_test_command_text(target_channel_id))
                from app.services.messaging_bridge import messaging_bridge_service

                result = await messaging_bridge_service.handle_message(incoming, is_admin_hint=True)
                await interaction.followup.send(**self._slash_followup_kwargs(result, incoming.author_display_name))
                return

            await interaction.response.defer(thinking=True)
            from app.services.messaging_bridge import messaging_bridge_service

            result = await messaging_bridge_service.handle_message(incoming, is_admin_hint=is_admin)
            await interaction.followup.send(**self._slash_followup_kwargs(result, incoming.author_display_name))
        except Exception as exc:
            logger.warning(
                "discord_slash_command_failed",
                extra={
                    "command": command,
                    "author_id": incoming.author_provider_id,
                    "channel_id": incoming.provider_channel_id,
                    "error": str(exc),
                },
            )
            await self._send_interaction_error(interaction)

    async def handle_confirmation_interaction(
        self,
        interaction: Any,
        *,
        session_id: str,
        confirmation_id: str,
        decision: str,
    ) -> None:
        incoming = incoming_from_interaction(interaction, "")
        allowed, reason = await self.message_is_allowed(incoming, slash_command=True)
        if not allowed:
            logger.info(
                "discord_hitl_denied",
                extra={"author_id": incoming.author_provider_id, "confirmation_id": confirmation_id, "reason": reason},
            )
            await interaction.response.send_message(f"Discord access denied: {reason}.", ephemeral=True)
            return
        role_ids = incoming.author_role_ids
        provider_user_id = incoming.author_provider_id
        if not await self.author_is_admin(provider_user_id, role_ids, provider_admin=incoming.author_is_provider_admin):
            logger.info("discord_hitl_denied", extra={"author_id": provider_user_id, "confirmation_id": confirmation_id})
            await interaction.response.send_message(
                "Admin permission is required to resolve this action. Use a linked IACS Admin account, a configured Discord admin role, or a Discord server admin.",
                ephemeral=True,
            )
            return
        logger.info(
            "discord_hitl_resolution",
            extra={"author_id": provider_user_id, "confirmation_id": confirmation_id, "decision": decision},
        )
        await interaction.response.defer(thinking=True, ephemeral=True)
        result = await messaging_bridge_service_handle_confirmation(
            session_id=session_id,
            confirmation_id=confirmation_id,
            decision=decision,
            user_id=await self._linked_user_id(provider_user_id),
            user_role="admin",
        )
        await interaction.followup.send(result.response_text[:1900] or "Action resolved.", ephemeral=True)

    async def message_is_allowed(self, message: IncomingChatMessage, *, slash_command: bool = False) -> tuple[bool, str]:
        config = await load_discord_config()
        if message.is_direct_message:
            if not config.allow_direct_messages:
                return False, "direct_messages_disabled"
            if message.author_provider_id not in config.user_allowlist:
                return False, "user_not_allowlisted"
            return True, "allowed"

        if not config.guild_allowlist or not message.provider_guild_id or message.provider_guild_id not in config.guild_allowlist:
            return False, "guild_not_allowlisted"
        if not config.channel_allowlist or message.provider_channel_id not in config.channel_allowlist:
            return False, "channel_not_allowlisted"
        if config.require_mention and not message.mentioned_bot and not slash_command:
            return False, "mention_required"
        if not self._author_allowlisted(message, config):
            return False, "author_not_allowlisted"
        return True, "allowed"

    async def author_is_admin(self, provider_user_id: str, role_ids: list[str], *, provider_admin: bool = False) -> bool:
        if provider_admin:
            return True
        config = await load_discord_config()
        if set(role_ids) & config.admin_role_ids:
            return True
        async with AsyncSessionLocal() as session:
            identity = await session.scalar(
                select(MessagingIdentity)
                .options(selectinload(MessagingIdentity.user))
                .where(MessagingIdentity.provider == "discord")
                .where(MessagingIdentity.provider_user_id == provider_user_id)
            )
            user = identity.user if identity else None
            return bool(user and user.is_active and user.role == UserRole.ADMIN)

    async def send_notification_action(
        self,
        action: dict[str, Any],
        context: NotificationContext,
        *,
        attachment_paths: list[str] | None = None,
    ) -> None:
        target_ids = [target for target in action.get("target_ids", []) if str(target).startswith("discord:")]
        if str(action.get("target_mode") or "all") == "all" or not target_ids:
            default_channel = (await load_discord_config()).default_notification_channel_id
            target_channel_ids = [default_channel] if default_channel else []
        else:
            target_channel_ids = [str(target).split(":", 1)[1] for target in target_ids]
        await self.send_notification_to_channels(
            target_channel_ids,
            str(action.get("title") or context.subject),
            str(action.get("message") or ""),
            context,
            attachment_paths=attachment_paths,
        )

    async def send_notification_to_channels(
        self,
        channel_ids: list[str],
        title: str,
        message: str,
        context: NotificationContext,
        *,
        attachment_paths: list[str] | None = None,
    ) -> None:
        if not channel_ids:
            raise NotificationDeliveryError("No Discord channel is configured or selected.")
        payload = format_discord_notification(title, message, context)
        embeds = [self._embed_from_payload(embed) for embed in payload.embeds]
        failures: list[str] = []
        delivered = False
        for channel_id in channel_ids:
            try:
                await self.send_message(
                    channel_id,
                    payload.content,
                    embeds=embeds,
                    attachment_paths=attachment_paths,
                )
                delivered = True
            except Exception as exc:
                failures.append(f"{channel_id}: {exc}")
        if failures:
            raise NotificationDeliveryError("; ".join(failures))
        if not delivered:
            raise NotificationDeliveryError("No Discord notification endpoints were delivered.")

    async def send_message(
        self,
        provider_channel_id: str,
        text: str,
        *,
        embeds: list[Any] | None = None,
        view: Any | None = None,
        files: list[Any] | None = None,
        attachment_paths: list[str] | None = None,
    ) -> None:
        if discord is None:
            raise NotificationDeliveryError("discord.py is not installed.")
        channel = await self._resolve_channel(provider_channel_id)
        if channel is None:
            raise NotificationDeliveryError(f"Discord channel {provider_channel_id} is unavailable.")
        embed_batches = _chunked(embeds or [], 10) or [[]]
        file_paths = attachment_paths or []
        for index, embed_batch in enumerate(embed_batches):
            initial_files = list(files or []) if index == 0 else []
            batch_files = _discord_files(file_paths) if index == 0 and file_paths else []
            content = (text or "")[:1900] if index == 0 else ""
            try:
                await channel.send(
                    content=content,
                    embeds=embed_batch,
                    view=view if index == 0 else None,
                    files=[*initial_files, *batch_files],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                if not embed_batch:
                    raise
                fallback_files = list(files or []) if index == 0 else []
                fallback_files.extend(_discord_files(file_paths) if index == 0 and file_paths else [])
                await channel.send(
                    content=content or _plain_fallback_from_embeds(embed_batch),
                    view=view if index == 0 else None,
                    files=fallback_files,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

    def _author_allowlisted(self, message: IncomingChatMessage, config: DiscordIntegrationConfig) -> bool:
        role_ids = set(message.author_role_ids)
        return (
            message.author_provider_id in config.user_allowlist
            or bool(role_ids & config.role_allowlist)
            or bool(role_ids & config.admin_role_ids)
        )

    async def _send_chat_result(
        self,
        channel: Any,
        response_text: str,
        pending_action: dict[str, Any] | None,
        requester: str,
    ) -> None:
        kwargs: dict[str, Any] = {
            "content": (response_text or "Alfred completed the request.")[:1900],
        }
        if discord is not None:
            kwargs["allowed_mentions"] = discord.AllowedMentions.none()
        if pending_action:
            kwargs["embeds"] = [self._embed_from_payload(format_confirmation_embed(pending_action, requester))]
            kwargs["view"] = self._confirmation_view(pending_action)
        await channel.send(**kwargs)

    def _slash_followup_kwargs(self, result: Any, requester: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "content": (result.response_text or "Alfred completed the request.")[:1900],
        }
        if discord is not None:
            kwargs["allowed_mentions"] = discord.AllowedMentions.none()
        if result.pending_action:
            kwargs["embeds"] = [self._embed_from_payload(format_confirmation_embed(result.pending_action, requester))]
            view = self._confirmation_view(result.pending_action)
            if view is not None:
                kwargs["view"] = view
        return kwargs

    async def _send_interaction_error(self, interaction: Any) -> None:
        message = "Alfred hit a Discord integration error while answering that command. Please try again."
        response = getattr(interaction, "response", None)
        try:
            if response is not None and not response.is_done():
                await response.send_message(message, ephemeral=True)
                return
        except Exception:
            logger.debug("discord_slash_error_response_failed")
        try:
            await interaction.followup.send(message, ephemeral=True)
        except Exception as exc:
            logger.warning("discord_slash_error_followup_failed", extra={"error": str(exc)})

    def _confirmation_view(self, pending_action: dict[str, Any] | None) -> Any | None:
        if not pending_action or DiscordConfirmationView is None:
            return None
        return DiscordConfirmationView(
            self,
            session_id=str(pending_action.get("session_id") or ""),
            confirmation_id=str(pending_action.get("confirmation_id") or ""),
            confirm_label=str(pending_action.get("confirm_label") or "Confirm"),
            cancel_label=str(pending_action.get("cancel_label") or "Cancel"),
            risk_level=str(pending_action.get("risk_level") or "medium"),
        )

    def _embed_from_payload(self, payload: DiscordEmbedPayload) -> Any:
        if discord is None:
            return payload
        embed = discord.Embed(title=payload.title, description=payload.description, color=payload.color)
        if payload.footer:
            embed.set_footer(text=payload.footer)
        for field in payload.fields:
            embed.add_field(
                name=str(field.get("name", ""))[:256],
                value=str(field.get("value", ""))[:1024],
                inline=bool(field.get("inline", False)),
            )
        return embed

    async def _resolve_channel(self, channel_id: str) -> Any | None:
        client = self._client
        raw_identifier = str(channel_id or "").strip()
        if client is None or not raw_identifier:
            return None
        channel_id = _discord_channel_id_from_identifier(raw_identifier)
        try:
            parsed = int(channel_id)
        except ValueError:
            return await self._resolve_channel_by_name(raw_identifier)
        channel = client.get_channel(parsed)
        if channel is not None:
            return channel
        try:
            return await client.fetch_channel(parsed)
        except Exception as exc:
            logger.warning("discord_channel_fetch_failed", extra={"channel_id": channel_id, "error": str(exc)})
            return None

    async def _resolve_channel_by_name(self, identifier: str) -> Any | None:
        client = self._client
        if client is None:
            return None
        wanted = _normalize_channel_name(identifier)
        if not wanted:
            return None
        config = await load_discord_config()
        matches: list[Any] = []
        for guild in getattr(client, "guilds", []) or []:
            guild_id = str(getattr(guild, "id", ""))
            if config.guild_allowlist and guild_id not in config.guild_allowlist:
                continue
            for channel in getattr(guild, "text_channels", []) or []:
                channel_name = str(getattr(channel, "name", "") or "")
                channel_id = str(getattr(channel, "id", "") or "")
                if config.channel_allowlist and channel_id not in config.channel_allowlist:
                    continue
                if _normalize_channel_name(channel_name) == wanted:
                    matches.append(channel)
        if len(matches) == 1:
            logger.info(
                "discord_channel_name_resolved",
                extra={"requested": identifier, "channel_id": str(getattr(matches[0], "id", ""))},
            )
            return matches[0]
        if len(matches) > 1:
            logger.warning("discord_channel_name_ambiguous", extra={"requested": identifier, "count": len(matches)})
        return None

    async def _linked_user_id(self, provider_user_id: str) -> str | None:
        async with AsyncSessionLocal() as session:
            identity = await session.scalar(
                select(MessagingIdentity)
                .where(MessagingIdentity.provider == "discord")
                .where(MessagingIdentity.provider_user_id == provider_user_id)
            )
            return str(identity.user_id) if identity and identity.user_id else None


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


async def messaging_bridge_service_handle_confirmation(
    *,
    session_id: str,
    confirmation_id: str,
    decision: str,
    user_id: str | None,
    user_role: str,
):
    from app.services.chat import chat_service
    from app.services.messaging_bridge import naturalize_messaging_response

    result = await chat_service.handle_tool_confirmation(
        session_id=session_id,
        confirmation_id=confirmation_id,
        decision=decision,
        user_id=user_id,
        user_role=user_role,
        client_context={"source": "discord_button"},
    )
    return DiscordConfirmationResult(
        response_text=naturalize_messaging_response(result.text, result.tool_results, "Discord confirmation")
    )


async def load_discord_config() -> DiscordIntegrationConfig:
    runtime = await get_runtime_config()
    return DiscordIntegrationConfig(
        bot_token=runtime.discord_bot_token,
        guild_allowlist=set(runtime.discord_guild_allowlist),
        channel_allowlist=set(runtime.discord_channel_allowlist),
        user_allowlist=set(runtime.discord_user_allowlist),
        role_allowlist=set(runtime.discord_role_allowlist),
        admin_role_ids=set(runtime.discord_admin_role_ids),
        default_notification_channel_id=runtime.discord_default_notification_channel_id,
        allow_direct_messages=runtime.discord_allow_direct_messages,
        require_mention=runtime.discord_require_mention,
    )


def _chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _discord_files(paths: list[str]) -> list[Any]:
    if discord is None:
        return []
    return [discord.File(path) for path in paths]


def _plain_fallback_from_embeds(embeds: list[Any]) -> str:
    parts: list[str] = []
    for embed in embeds:
        title = getattr(embed, "title", "")
        description = getattr(embed, "description", "")
        combined = "\n".join(str(part) for part in [title, description] if str(part).strip())
        if combined:
            parts.append(combined)
    return "\n\n".join(parts)[:1900] or "IACS notification"


def _discord_channel_id_from_identifier(identifier: str) -> str:
    value = identifier.strip()
    mention_match = re.fullmatch(r"<#(\d+)>", value)
    if mention_match:
        return mention_match.group(1)
    url_match = re.search(r"/channels/\d+/(\d+)(?:\D*$|$)", value)
    if url_match:
        return url_match.group(1)
    return value


def _normalize_channel_name(identifier: str) -> str:
    value = identifier.strip().lower()
    if value.startswith("#"):
        value = value[1:]
    value = re.sub(r"^discord channel\s+", "", value)
    value = re.sub(r"\s+", "-", value)
    return value


DISCORD_HELP_TEXT = (
    "**Alfred Discord commands**\n"
    "Warm, witty, and annoyingly serious about safety.\n"
    "`/alfred status` - concise site state\n"
    "`/alfred last_event` - most recent access event\n"
    "`/alfred arrivals_today` - today's arrivals\n"
    "`/alfred presence` - current occupancy\n"
    "`/alfred ask <message>` - ask Alfred naturally\n"
    "`/alfred notify_test` - Admin-only test notification, with confirmation"
)


discord_messaging_service = DiscordMessagingService()


def get_discord_messaging_service() -> DiscordMessagingService:
    return discord_messaging_service
