from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
import warnings

from app.core.logging import get_logger
from app.modules.messaging.base import IncomingChatMessage

logger = get_logger(__name__)

try:  # pragma: no cover - exercised only when discord.py is installed.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="'audioop' is deprecated.*",
            category=DeprecationWarning,
            module="discord\\.player",
        )
        import discord
        from discord import app_commands
except Exception:  # pragma: no cover - import guard keeps the app bootable before image rebuild.
    discord = None  # type: ignore[assignment]
    app_commands = None  # type: ignore[assignment]


def discord_library_available() -> bool:
    return discord is not None and app_commands is not None


def discord_author_is_provider_admin(author: Any, guild: Any | None) -> bool:
    permissions = getattr(author, "guild_permissions", None)
    if bool(getattr(permissions, "administrator", False)):
        return True
    guild_owner_id = getattr(guild, "owner_id", None)
    author_id = getattr(author, "id", None)
    return guild_owner_id is not None and author_id is not None and str(guild_owner_id) == str(author_id)


def normalize_discord_message(message: Any, bot_user: Any | None = None) -> IncomingChatMessage:
    guild = getattr(message, "guild", None)
    channel = getattr(message, "channel", None)
    author = getattr(message, "author", None)
    author_is_provider_admin = discord_author_is_provider_admin(author, guild)
    author_roles = [
        str(getattr(role, "id", ""))
        for role in getattr(author, "roles", []) or []
        if str(getattr(role, "id", "")).strip()
    ]
    mentions = getattr(message, "mentions", []) or []
    mentioned_bot = bool(bot_user and any(getattr(mention, "id", None) == getattr(bot_user, "id", None) for mention in mentions))
    clean_content = str(getattr(message, "clean_content", None) or getattr(message, "content", "") or "").strip()
    if bot_user:
        mention_tokens = {
            f"<@{getattr(bot_user, 'id', '')}>",
            f"<@!{getattr(bot_user, 'id', '')}>",
        }
        content = str(getattr(message, "content", "") or "")
        for token in mention_tokens:
            content = content.replace(token, "").strip()
        if content:
            clean_content = content

    return IncomingChatMessage(
        provider="discord",
        provider_message_id=str(getattr(message, "id", "")),
        provider_channel_id=str(getattr(channel, "id", "")),
        provider_guild_id=str(getattr(guild, "id", "")) if guild else None,
        provider_thread_id=str(getattr(channel, "id", "")) if getattr(channel, "parent_id", None) else None,
        author_provider_id=str(getattr(author, "id", "")),
        author_display_name=str(getattr(author, "display_name", None) or getattr(author, "name", "") or "Discord user"),
        author_role_ids=author_roles,
        author_is_provider_admin=author_is_provider_admin,
        text=clean_content,
        is_direct_message=guild is None,
        mentioned_bot=mentioned_bot,
        raw_payload={
            "guild_id": str(getattr(guild, "id", "")) if guild else None,
            "channel_id": str(getattr(channel, "id", "")),
            "message_id": str(getattr(message, "id", "")),
            "author_id": str(getattr(author, "id", "")),
            "author_role_ids": author_roles,
            "author_is_provider_admin": author_is_provider_admin,
        },
        received_at=getattr(message, "created_at", None) or datetime.now(tz=UTC),
    )


if discord is not None:

    class DiscordConfirmationView(discord.ui.View):  # type: ignore[name-defined]
        def __init__(
            self,
            service: Any,
            *,
            session_id: str,
            confirmation_id: str,
            confirm_label: str,
            cancel_label: str,
            risk_level: str,
        ) -> None:
            super().__init__(timeout=600)
            self.service = service
            self.session_id = session_id
            self.confirmation_id = confirmation_id

            confirm = discord.ui.Button(  # type: ignore[union-attr]
                label=confirm_label or "Confirm",
                style=discord.ButtonStyle.danger if risk_level == "high" else discord.ButtonStyle.success,
                custom_id=f"iacs:confirm:{session_id}:{confirmation_id}",
            )
            cancel = discord.ui.Button(  # type: ignore[union-attr]
                label=cancel_label or "Cancel",
                style=discord.ButtonStyle.secondary,
                custom_id=f"iacs:cancel:{session_id}:{confirmation_id}",
            )
            confirm.callback = self._confirm  # type: ignore[method-assign]
            cancel.callback = self._cancel  # type: ignore[method-assign]
            self.add_item(confirm)
            self.add_item(cancel)

        async def _confirm(self, interaction: discord.Interaction) -> None:  # type: ignore[name-defined]
            await self.service.handle_confirmation_interaction(
                interaction,
                session_id=self.session_id,
                confirmation_id=self.confirmation_id,
                decision="confirm",
            )

        async def _cancel(self, interaction: discord.Interaction) -> None:  # type: ignore[name-defined]
            await self.service.handle_confirmation_interaction(
                interaction,
                session_id=self.session_id,
                confirmation_id=self.confirmation_id,
                decision="cancel",
            )


    class IacsDiscordBot(discord.Client):  # type: ignore[name-defined]
        def __init__(self, service: Any) -> None:
            intents = discord.Intents.default()
            intents.guilds = True
            intents.messages = True
            intents.dm_messages = True
            intents.message_content = True
            super().__init__(intents=intents)
            self.service = service
            self.tree = app_commands.CommandTree(self)  # type: ignore[union-attr]

        async def setup_hook(self) -> None:
            self._register_slash_commands()
            try:
                await self.tree.sync()
            except Exception as exc:
                logger.warning("discord_slash_command_sync_failed", extra={"error": str(exc)})

        async def on_ready(self) -> None:
            await self.service.handle_bot_ready()

        async def on_message(self, message: discord.Message) -> None:  # type: ignore[name-defined]
            author = getattr(message, "author", None)
            if getattr(author, "bot", False):
                return
            normalized = normalize_discord_message(message, self.user)
            await self.service.handle_provider_message(normalized, message)

        def _register_slash_commands(self) -> None:
            group = app_commands.Group(name="alfred", description="IACS Alfred commands")  # type: ignore[union-attr]

            @group.command(name="status", description="Concise current IACS state.")
            async def status(interaction: discord.Interaction) -> None:  # type: ignore[name-defined]
                await self.service.handle_slash_command(interaction, "status")

            @group.command(name="last_event", description="Explain the most recent access event.")
            async def last_event(interaction: discord.Interaction) -> None:  # type: ignore[name-defined]
                await self.service.handle_slash_command(interaction, "last_event")

            @group.command(name="arrivals_today", description="Summarise today's known and unknown arrivals.")
            async def arrivals_today(interaction: discord.Interaction) -> None:  # type: ignore[name-defined]
                await self.service.handle_slash_command(interaction, "arrivals_today")

            @group.command(name="presence", description="Show current home occupancy.")
            async def presence(interaction: discord.Interaction) -> None:  # type: ignore[name-defined]
                await self.service.handle_slash_command(interaction, "presence")

            @group.command(name="help", description="List Alfred Discord commands.")
            async def help_command(interaction: discord.Interaction) -> None:  # type: ignore[name-defined]
                await self.service.handle_slash_command(interaction, "help")

            @group.command(name="ask", description="Ask Alfred a natural language question.")
            @app_commands.describe(message="Message to send to Alfred")  # type: ignore[union-attr]
            async def ask(interaction: discord.Interaction, message: str) -> None:  # type: ignore[name-defined]
                await self.service.handle_slash_command(interaction, "ask", message=message)

            @group.command(name="notify_test", description="Send a test Discord notification.")
            async def notify_test(interaction: discord.Interaction) -> None:  # type: ignore[name-defined]
                await self.service.handle_slash_command(interaction, "notify_test")

            self.tree.add_command(group)

else:
    DiscordConfirmationView = None
    IacsDiscordBot = None
