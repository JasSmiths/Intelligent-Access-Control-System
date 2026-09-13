from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.auth_secret import get_auth_secret
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import MessagingIdentity
from app.models.enums import UserRole
from app.modules.messaging.base import IncomingChatMessage, MessagingAuthorityChanged
from app.modules.messaging.discord_transport import (
    send_channel_message,
    send_interaction_followup,
)
from app.modules.messaging.discord_bot import (
    DiscordConfirmationView,
    IacsDiscordBot,
    discord,
    discord_library_available,
)
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError
from app.modules.notifications.discord_formatter import (
    DiscordEmbedPayload,
    format_confirmation_embed,
    format_discord_notification,
)
from app.services.event_bus import event_bus
from app.services.settings import get_runtime_config, get_runtime_config_for_session

logger = get_logger(__name__)


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


class DiscordNotificationDeliveryError(NotificationDeliveryError):
    """Safe action-level truth for a Discord fanout that cannot be retried."""

    def __init__(
        self,
        message: str,
        *,
        delivery: str = "unknown",
        destination_outcomes: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message, delivery=delivery)  # type: ignore[arg-type]
        self.destination_outcomes = list(destination_outcomes or [])
        self.partial_failure = bool(
            self.destination_outcomes
            and any(item.get("delivery") != "accepted" for item in self.destination_outcomes)
            and any(item.get("delivery") == "accepted" for item in self.destination_outcomes)
        )
        self.failure_count = sum(item.get("delivery") != "accepted" for item in self.destination_outcomes)


class DiscordMessagingService:
    def __init__(self) -> None:
        self._client: Any | None = None
        self._task: asyncio.Task | None = None
        self._started = False
        self._last_error: str | None = None
        self._ready_at: datetime | None = None
        self._sender_token_binding: str | None = None

        # Lifecycle composition supplies the inbound gateway before startup.
        # The transport owns the connected bot only; it never imports or owns
        # durable intake, Alfred routing, or confirmation execution.
        self._gateway: Any | None = None

    def configure_gateway(self, gateway: Any) -> None:
        """Bind bot callbacks before this transport is started."""
        if self._started:
            raise RuntimeError("Discord gateway cannot change while the transport is running.")
        self._gateway = gateway

    async def start(self) -> None:
        if self._started:
            return
        if self._gateway is None:
            self._last_error = "Discord inbound gateway is not configured."
            logger.warning("discord_gateway_missing")
            return
        self._started = True
        config = await load_current_discord_config()
        if not config.bot_token:
            self._last_error = "Discord bot token is not configured."
            logger.warning("discord_bot_token_missing")
            return
        if not discord_library_available() or IacsDiscordBot is None:
            self._last_error = "discord.py is not installed. Rebuild the backend image after dependency enrollment."
            logger.warning("discord_library_missing")
            return
        self._client = IacsDiscordBot(self._gateway)
        self._sender_token_binding = discord_sender_binding(config.bot_token)
        self._task = asyncio.create_task(self._run_client(config.bot_token))
        logger.info("discord_bot_starting")

    async def stop(self) -> None:
        self._started = False
        self._sender_token_binding = None
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
                await asyncio.gather(task, return_exceptions=True)
            except Exception as exc:
                logger.warning("discord_bot_task_stop_failed", extra={"error": str(exc)})
        logger.info("discord_bot_stopped")

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def _run_client(self, token: str) -> None:
        client = self._client
        if client is None:
            return
        try:
            await client.start(token)
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

    @staticmethod
    async def config_for_session(session) -> DiscordIntegrationConfig:
        """Return an uncached transaction-scoped Discord configuration snapshot."""
        return discord_config_from_runtime(await get_runtime_config_for_session(session))

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

    def delivery_ready(self) -> bool:
        return bool(self._client and not self._client.is_closed() and getattr(self._client, "user", None))

    async def assert_current_sender(self, config: DiscordIntegrationConfig | None = None) -> DiscordIntegrationConfig:
        """Fence provider I/O against both the live bot token and fresh config."""
        current = await load_current_discord_config()
        if config is not None and discord_configuration_binding(config) != discord_configuration_binding(current):
            raise NotificationDeliveryError(
                "Discord configuration changed before sending; create a fresh notification request.",
                delivery="not_sent",
            )
        if (
            not current.configured
            or not self.delivery_ready()
            or self._sender_token_binding != discord_sender_binding(current.bot_token)
        ):
            raise NotificationDeliveryError(
                "Discord sender is unavailable or its configuration changed; reconnect before sending.",
                delivery="not_sent",
            )
        return current

    async def refresh_incoming_membership(
        self,
        message: IncomingChatMessage,
        *,
        config: DiscordIntegrationConfig,
    ) -> IncomingChatMessage:
        if message.is_direct_message:
            return message
        client = self._client
        get_guild = getattr(client, "get_guild", None)
        guild = (
            get_guild(int(message.provider_guild_id))
            if callable(get_guild) and str(message.provider_guild_id or "").isdecimal()
            else None
        )
        if guild is None:
            raise MessagingAuthorityChanged("discord_membership_unavailable")
        try:
            member = await guild.fetch_member(int(message.author_provider_id))
        except Exception as exc:
            raise MessagingAuthorityChanged("discord_membership_unavailable") from exc
        roles = [str(role.id) for role in getattr(member, "roles", [])]
        return replace(message, author_role_ids=roles)

    async def send_incoming_interaction_reply(self, interaction, payload):
        kwargs = {"content": payload["content"], "allowed_mentions": {"parse": []}}
        view = None
        if payload.get("pending_action"):
            kwargs["embeds"] = [
                self._embed_to_payload(
                    format_confirmation_embed(payload["pending_action"], payload["requester"])
                )
            ]
            view = self._confirmation_view(payload["pending_action"])
            if view is not None:
                kwargs["components"] = view.to_components()
        if payload["mode"] in {"confirmation", "help"}:
            kwargs["flags"] = 64
        await self.assert_current_sender()
        result = await send_interaction_followup(
            interaction.application_id,
            interaction.token,
            kwargs,
        )
        if view is not None:
            self._register_sent_view(view, result.id)
        return result

    async def message_is_allowed(
        self,
        message: IncomingChatMessage,
        *,
        slash_command: bool = False,
        config: DiscordIntegrationConfig | None = None,
    ) -> tuple[bool, str]:
        config = config or await load_current_discord_config()
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
        del role_ids, provider_admin
        return bool(await self._linked_admin_user_id(provider_user_id))

    async def _linked_admin_user_id(self, provider_user_id: str) -> str | None:
        async with AsyncSessionLocal() as session:
            identity = await session.scalar(
                select(MessagingIdentity)
                .options(selectinload(MessagingIdentity.user))
                .where(MessagingIdentity.provider == "discord")
                .where(MessagingIdentity.provider_user_id == provider_user_id)
            )
            user = identity.user if identity else None
            return str(user.id) if user and user.is_active and user.role == UserRole.ADMIN else None

    async def send_notification_action(
        self,
        action: dict[str, Any],
        context: NotificationContext,
        *,
        attachment_paths: list[str] | None = None,
        config: DiscordIntegrationConfig,
    ) -> dict[str, Any]:
        channels = _frozen_notification_channel_ids(action)
        if not channels:
            raise NotificationDeliveryError(
                "Stored Discord notification channels are unavailable; review is required.",
                delivery="not_sent",
            )
        if action.get("frozen_discord_configuration_binding") != discord_configuration_binding(config):
            raise NotificationDeliveryError(
                "Discord notification configuration changed; create a fresh notification request.",
                delivery="not_sent",
            )
        await self.assert_current_sender(config)
        return await self.send_notification_to_channels(
            channels,
            str(action.get("title") or context.subject),
            str(action.get("message") or ""),
            context,
            attachment_paths=attachment_paths,
            config=config,
        )

    async def prepare_notification_action(
        self,
        action: dict[str, Any],
        context: NotificationContext,
    ) -> dict[str, Any]:
        """Freeze numeric targets and the sender/configuration fingerprint before claims."""
        del context
        config = await load_current_discord_config()
        return {
            **action,
            "frozen_discord_channel_ids": _notification_channel_ids(action, config),
            "frozen_discord_configuration_binding": discord_configuration_binding(config),
        }

    async def authorize_notification_action_in_session(
        self,
        session,
        action: dict[str, Any],
        *,
        config: DiscordIntegrationConfig | None = None,
    ) -> str | None:
        """Validate immutable Discord plan data using the fresh dispatch transaction config."""
        channels = _frozen_notification_channel_ids(action)
        if not channels:
            return "discord_frozen_channels_unavailable"
        if any(not channel.isdecimal() for channel in channels):
            return "discord_frozen_channels_invalid"
        binding = action.get("frozen_discord_configuration_binding")
        if not isinstance(binding, str) or not binding:
            return "discord_configuration_binding_unavailable"
        config = config or await self.config_for_session(session)
        if not config.configured or binding != discord_configuration_binding(config):
            return "discord_configuration_changed"
        return None

    async def send_notification_to_channels(
        self,
        channel_ids: list[str],
        title: str,
        message: str,
        context: NotificationContext,
        *,
        attachment_paths: list[str] | None = None,
        config: DiscordIntegrationConfig,
    ) -> dict[str, Any]:
        if not channel_ids:
            raise NotificationDeliveryError("No Discord channel is configured or selected.", delivery="not_sent")
        payload = format_discord_notification(title, message, context)
        embeds = [self._embed_to_payload(embed) for embed in payload.embeds]
        outcomes: list[dict[str, str]] = []
        for index, channel_id in enumerate(channel_ids):
            try:
                await self.send_message(
                    channel_id,
                    payload.content,
                    embeds=embeds,
                    attachment_paths=attachment_paths,
                    config=config,
                )
                outcomes.append({"target": channel_id, "delivery": "accepted"})
            except NotificationDeliveryError as exc:
                delivery = exc.delivery
                outcomes.append({"target": channel_id, "delivery": delivery})
                if delivery == "unknown":
                    outcomes.extend(
                        {"target": remaining, "delivery": "not_sent"}
                        for remaining in channel_ids[index + 1 :]
                    )
                    raise DiscordNotificationDeliveryError(
                        "Discord delivery outcome is uncertain; review is required before any resend.",
                        delivery="unknown",
                        destination_outcomes=outcomes,
                    ) from exc
            except Exception as exc:
                outcomes.append({"target": channel_id, "delivery": "unknown"})
                outcomes.extend(
                    {"target": remaining, "delivery": "not_sent"}
                    for remaining in channel_ids[index + 1 :]
                )
                raise DiscordNotificationDeliveryError(
                    "Discord delivery outcome is uncertain; review is required before any resend.",
                    delivery="unknown",
                    destination_outcomes=outcomes,
                ) from exc
        accepted = sum(item["delivery"] == "accepted" for item in outcomes)
        failures = sum(item["delivery"] != "accepted" for item in outcomes)
        if not accepted:
            delivery = "rejected" if any(item["delivery"] == "rejected" for item in outcomes) else "not_sent"
            raise DiscordNotificationDeliveryError(
                "No Discord notification endpoints accepted the notification.",
                delivery=delivery,
                destination_outcomes=outcomes,
            )
        return {
            "destination_outcomes": outcomes,
            "partial_failure": bool(failures),
            "failure_count": failures,
        }

    async def send_message(
        self,
        provider_channel_id: str,
        text: str,
        *,
        embeds: list[Any] | None = None,
        view: Any | None = None,
        files: list[Any] | None = None,
        attachment_paths: list[str] | None = None,
        config: DiscordIntegrationConfig | None = None,
        _resolved_channel: Any | None = None,
    ) -> Any:
        channel = _resolved_channel or await self._resolve_channel(provider_channel_id)
        if channel is None:
            raise NotificationDeliveryError(
                f"Discord channel {provider_channel_id} is unavailable.",
                delivery="not_sent",
            )
        channel_id = str(getattr(channel, "id", "") or "")
        if not channel_id.isdecimal():
            raise NotificationDeliveryError("Discord reply channel is unavailable.", delivery="not_sent")
        embed_batches = _discord_embed_batches(
            [_discord_embed_to_payload(embed) for embed in embeds or []]
        )
        request_payloads: list[dict[str, Any]] = []
        for index, embed_batch in enumerate(embed_batches):
            request_payload: dict[str, Any] = {
                "content": (text or "")[:1900] if index == 0 else "",
                "embeds": embed_batch,
                "allowed_mentions": {"parse": []},
            }
            if index == 0 and view is not None:
                request_payload["components"] = view.to_components()
            request_payloads.append(request_payload)

        result = None
        accepted_any = False
        for index, request_payload in enumerate(request_payloads):
            try:
                current = await self.assert_current_sender(config)
                result = await send_channel_message(
                    channel_id,
                    current.bot_token,
                    request_payload,
                    files=files if index == 0 else None,
                    attachment_paths=attachment_paths if index == 0 else None,
                )
                accepted_any = True
                if index == 0 and view is not None:
                    self._register_sent_view(view, result.id)
            except NotificationDeliveryError as exc:
                if accepted_any:
                    raise NotificationDeliveryError(
                        "Discord message was partially delivered; review is required before any resend.",
                        delivery="unknown",
                    ) from exc
                raise
            except Exception as exc:
                if accepted_any:
                    raise NotificationDeliveryError(
                        "Discord message was partially delivered; review is required before any resend.",
                        delivery="unknown",
                    ) from exc
                raise NotificationDeliveryError(
                    f"Discord channel {provider_channel_id} delivery is uncertain.",
                    delivery="unknown",
                ) from exc
        return result

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
    ) -> Any:
        channel_id = str(getattr(channel, "id", "") or "")
        if not channel_id.isdecimal():
            raise NotificationDeliveryError(
                "Discord reply channel unavailable",
                delivery="not_sent",
            )
        embeds = None
        view = None
        if pending_action:
            embeds = [self._embed_to_payload(format_confirmation_embed(pending_action, requester))]
            view = self._confirmation_view(pending_action)
        return await self.send_message(
            channel_id,
            response_text or "Alfred completed the request.",
            embeds=embeds,
            view=view,
            _resolved_channel=channel,
        )

    def _confirmation_view(self, pending_action: dict[str, Any] | None) -> Any | None:
        gateway = self._gateway
        if not pending_action or DiscordConfirmationView is None or gateway is None:
            return None
        return DiscordConfirmationView(
            gateway,
            session_id=str(pending_action.get("session_id") or ""),
            confirmation_id=str(pending_action.get("confirmation_id") or ""),
            confirm_label=str(pending_action.get("confirm_label") or "Confirm"),
            cancel_label=str(pending_action.get("cancel_label") or "Cancel"),
            risk_level=str(pending_action.get("risk_level") or "medium"),
        )

    @staticmethod
    def _embed_to_payload(payload: DiscordEmbedPayload) -> dict[str, Any]:
        return _discord_embed_to_payload(payload)

    def _register_sent_view(self, view: Any, message_id: str) -> None:
        register = getattr(self._client, "register_sent_view", None)
        if not callable(register) or not register(view, message_id):
            # The message was accepted, but an interactive confirmation without a
            # matching gateway handler is unsafe and cannot be resent automatically.
            raise NotificationDeliveryError(
                "Discord confirmation delivery is uncertain; review is required.",
                delivery="unknown",
            )

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

async def load_discord_config() -> DiscordIntegrationConfig:
    return discord_config_from_runtime(await get_runtime_config())


async def load_current_discord_config() -> DiscordIntegrationConfig:
    """Read the live setting rows instead of the short-lived display cache."""
    async with AsyncSessionLocal() as session:
        return discord_config_from_runtime(await get_runtime_config_for_session(session))


def discord_config_from_runtime(runtime) -> DiscordIntegrationConfig:
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


def discord_sender_binding(token: str) -> str:
    return _discord_binding_hash(token)


def discord_configuration_binding(config: DiscordIntegrationConfig) -> str:
    values = {key: sorted(value) if isinstance(value, set) else value for key, value in config.__dict__.items()}
    return _discord_binding_hash(json.dumps(values, sort_keys=True, separators=(",", ":")))


def _discord_binding_hash(value: str) -> str:
    return hmac.new(
        get_auth_secret().encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _notification_channel_ids(action: dict[str, Any], config: DiscordIntegrationConfig) -> list[str]:
    """Resolve only the persisted notification target grammar into numeric snowflakes.

    Inbound guild/channel allowlists deliberately do not constrain notification
    destinations. A configured bot can send to an explicit channel it can resolve.
    """
    target_ids = [str(target) for target in action.get("target_ids", []) if str(target).startswith("discord:")]
    mode = str(action.get("target_mode") or "all")
    if mode == "all" or not target_ids or "discord:*" in target_ids:
        candidates = [config.default_notification_channel_id]
    else:
        candidates = [target.split(":", 1)[1] for target in target_ids]
    channels: list[str] = []
    for candidate in candidates:
        identifier = _discord_channel_id_from_identifier(str(candidate))
        if identifier.isdecimal():
            channels.append(identifier)
    return channels


def _frozen_notification_channel_ids(action: dict[str, Any]) -> list[str]:
    channels = action.get("frozen_discord_channel_ids")
    if not isinstance(channels, list) or not channels:
        return []
    values = [str(channel) for channel in channels]
    return values if all(channel.isdecimal() for channel in values) else []


_DISCORD_EMBEDS_PER_MESSAGE = 10
_DISCORD_EMBED_TOTAL_CHARACTERS = 6000


def _discord_embed_to_payload(embed: Any) -> dict[str, Any]:
    if isinstance(embed, DiscordEmbedPayload):
        payload: dict[str, Any] = {
            "title": embed.title,
            "description": embed.description,
            "color": embed.color,
        }
        if embed.footer:
            payload["footer"] = {"text": embed.footer}
        if embed.fields:
            payload["fields"] = [dict(field) for field in embed.fields]
        return payload
    if isinstance(embed, dict):
        return dict(embed)
    to_dict = getattr(embed, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, dict):
            return dict(payload)
    raise NotificationDeliveryError("Discord embed could not be prepared.", delivery="not_sent")


def _discord_embed_batches(embeds: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Bound batches by Discord's count and aggregate embed-character limits."""
    batches: list[list[dict[str, Any]]] = []
    batch: list[dict[str, Any]] = []
    batch_characters = 0
    for embed in embeds:
        characters = _discord_embed_character_count(embed)
        if characters > _DISCORD_EMBED_TOTAL_CHARACTERS:
            raise NotificationDeliveryError(
                "Discord embed exceeds the message character limit.",
                delivery="not_sent",
            )
        if batch and (
            len(batch) >= _DISCORD_EMBEDS_PER_MESSAGE
            or batch_characters + characters > _DISCORD_EMBED_TOTAL_CHARACTERS
        ):
            batches.append(batch)
            batch = []
            batch_characters = 0
        batch.append(embed)
        batch_characters += characters
    if batch:
        batches.append(batch)
    return batches or [[]]


def _discord_embed_character_count(embed: dict[str, Any]) -> int:
    total = _discord_text_length(embed.get("title")) + _discord_text_length(
        embed.get("description")
    )
    footer = embed.get("footer")
    if isinstance(footer, dict):
        total += _discord_text_length(footer.get("text"))
    author = embed.get("author")
    if isinstance(author, dict):
        total += _discord_text_length(author.get("name"))
    fields = embed.get("fields")
    if isinstance(fields, list):
        for field in fields:
            if isinstance(field, dict):
                total += _discord_text_length(field.get("name")) + _discord_text_length(
                    field.get("value")
                )
    return total


def _discord_text_length(value: Any) -> int:
    return len(value) if isinstance(value, str) else 0


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


discord_messaging_service = DiscordMessagingService()


def get_discord_messaging_service() -> DiscordMessagingService:
    return discord_messaging_service
