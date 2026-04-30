from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any

from app.modules.notifications.base import NotificationContext

DISCORD_EMBED_DESCRIPTION_LIMIT = 4096
DISCORD_EMBED_TITLE_LIMIT = 256
DISCORD_MESSAGE_LIMIT = 2000

SEVERITY_COLORS = {
    "critical": 0xD92D20,
    "error": 0xD92D20,
    "warning": 0xDC6803,
    "info": 0x2563EB,
    "success": 0x039855,
    "debug": 0x667085,
}


@dataclass(frozen=True)
class DiscordEmbedPayload:
    title: str
    description: str
    color: int
    footer: str = ""
    fields: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class DiscordNotificationPayload:
    content: str
    embeds: list[DiscordEmbedPayload]


def format_discord_notification(
    title: str,
    body: str,
    context: NotificationContext,
) -> DiscordNotificationPayload:
    safe_title = _truncate(_plain_text(title or context.subject), DISCORD_EMBED_TITLE_LIMIT)
    safe_body = _plain_text(body or context.subject)
    chunks = _split_text(safe_body, DISCORD_EMBED_DESCRIPTION_LIMIT)
    color = SEVERITY_COLORS.get(str(context.severity).lower(), SEVERITY_COLORS["info"])
    embeds = [
        DiscordEmbedPayload(
            title=safe_title if index == 0 else f"{safe_title} ({index + 1})",
            description=chunk,
            color=color,
            footer=f"IACS notification - {context.event_type}",
        )
        for index, chunk in enumerate(chunks or [safe_title])
    ]
    return DiscordNotificationPayload(
        content=_truncate(safe_title, DISCORD_MESSAGE_LIMIT),
        embeds=embeds,
    )


def format_confirmation_embed(pending_action: dict[str, Any], requester: str) -> DiscordEmbedPayload:
    title = _truncate(str(pending_action.get("title") or "Confirm action?"), DISCORD_EMBED_TITLE_LIMIT)
    description = _plain_text(str(pending_action.get("description") or "This action needs confirmation."))
    expires_at = str(pending_action.get("expires_at") or "")
    lines = [
        description,
        "",
        f"Requested by {requester}.",
    ]
    if expires_at:
        lines.append(f"Expires at {expires_at}.")
    return DiscordEmbedPayload(
        title=title,
        description=_truncate("\n".join(lines), DISCORD_EMBED_DESCRIPTION_LIMIT),
        color=0xDC6803 if pending_action.get("risk_level") == "high" else 0x2563EB,
        footer="IACS Alfred confirmation",
    )


def _plain_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return _escape_mentions(text.strip())


def _escape_mentions(value: str) -> str:
    return (
        value.replace("@everyone", "@ everyone")
        .replace("@here", "@ here")
        .replace("<@", "< @")
        .replace("<#", "< #")
        .replace("<@&", "< @&")
    )


def _split_text(value: str, limit: int) -> list[str]:
    if len(value) <= limit:
        return [value] if value else []
    chunks: list[str] = []
    remaining = value
    while remaining:
        chunk = remaining[:limit]
        split_at = max(chunk.rfind("\n"), chunk.rfind(". "), chunk.rfind(" "))
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return chunks


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."
