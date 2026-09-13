"""One-attempt Discord REST delivery.

The discord.py convenience send methods retry some failed requests. That is
unsafe for IACS deliveries: after a request might have reached Discord, a
second request can produce a second notification or confirmation. This small
adapter deliberately owns exactly one POST for each outbound message.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import quote

import aiohttp

from app.modules.notifications.base import NotificationDeliveryError

_DISCORD_API_BASE = "https://discord.com/api/v10"
_DISCORD_PY_URL = "https://github.com/Rapptz/discord.py"
_REQUEST_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class DiscordSentMessage:
    """The minimal provider receipt needed by durable reply tracking."""

    message_id: str
    channel_id: str | None = None

    @property
    def id(self) -> str:
        """Keep the receipt compatible with the message identity consumers expect."""
        return self.message_id


@dataclass
class _PreparedAttachment:
    stream: Any
    filename: str
    description: str | None
    close: Callable[[], None]

    def to_payload(self, index: int) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": index, "filename": self.filename}
        if self.description is not None:
            payload["description"] = self.description
        return payload


@dataclass
class _RequestBody:
    payload: dict[str, Any]
    form: aiohttp.FormData | None
    attachments: list[_PreparedAttachment]

    def close(self) -> None:
        for attachment in self.attachments:
            try:
                attachment.close()
            except Exception:
                # File cleanup must not change delivery certainty after the one POST.
                pass


async def send_channel_message(
    channel_id: str,
    bot_token: str,
    payload: dict[str, Any],
    *,
    files: Sequence[Any] | None = None,
    attachment_paths: Sequence[str] | None = None,
) -> DiscordSentMessage:
    """POST one bot-authenticated channel message without redirect following or retry."""
    channel = str(channel_id or "")
    if not channel.isdecimal() or not bot_token:
        raise NotificationDeliveryError(
            "Discord channel delivery is unavailable.",
            delivery="not_sent",
        )
    return await _post_message(
        f"{_DISCORD_API_BASE}/channels/{channel}/messages",
        payload,
        headers={"Authorization": f"Bot {bot_token}"},
        files=files,
        attachment_paths=attachment_paths,
    )


async def send_interaction_followup(
    application_id: int,
    token: str,
    payload: dict[str, Any],
) -> DiscordSentMessage:
    """POST one interaction follow-up and return the created message identity."""
    if type(application_id) is not int or application_id <= 0 or not token:
        raise NotificationDeliveryError("Discord interaction is unavailable.", delivery="not_sent")
    params = {"wait": "true"}
    if payload.get("components"):
        # Discord only accepts interactive webhook components when this is explicit.
        params["with_components"] = "true"
    return await _post_message(
        f"{_DISCORD_API_BASE}/webhooks/{application_id}/{quote(token, safe='')}",
        payload,
        params=params,
    )


async def _post_message(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    files: Sequence[Any] | None = None,
    attachment_paths: Sequence[str] | None = None,
) -> DiscordSentMessage:
    """Execute exactly one POST and classify only a definitive provider rejection."""
    request_headers = _discord_request_headers(headers)
    request_body = _prepare_request_body(payload, files=files, attachment_paths=attachment_paths)
    try:
        timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            # aiohttp does not retry POSTs. Redirect following is disabled because a
            # redirect response cannot prove whether Discord accepted the message.
            async with client.post(
                url,
                params=params,
                headers=request_headers,
                json=request_body.payload if request_body.form is None else None,
                data=request_body.form,
                allow_redirects=False,
            ) as response:
                if not 200 <= response.status < 300:
                    raise NotificationDeliveryError(
                        "Discord did not accept the message.",
                        delivery=_delivery_for_status(response.status),
                    )
                return _receipt_from_response(await response.json(content_type=None))
    except NotificationDeliveryError:
        raise
    except (aiohttp.ClientError, TimeoutError):
        # A connection failure, timeout, malformed success body, or reset can all
        # happen after Discord receives the request. Do not send again.
        raise NotificationDeliveryError(
            "Discord delivery is uncertain.",
            delivery="unknown",
        ) from None
    except Exception:
        # Any failure after entering the request path is similarly ambiguous.
        raise NotificationDeliveryError(
            "Discord delivery is uncertain.",
            delivery="unknown",
        ) from None
    finally:
        request_body.close()


def _discord_request_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """Supply Discord's required, truthful client identification on every route."""
    try:
        discord_py_version = distribution_version("discord.py")
    except PackageNotFoundError as exc:
        raise NotificationDeliveryError(
            "Discord transport is unavailable.",
            delivery="not_sent",
        ) from exc
    request_headers = {
        "User-Agent": (
            f"DiscordBot ({_DISCORD_PY_URL} {discord_py_version}) "
            f"Python/{sys.version_info.major}.{sys.version_info.minor} "
            f"aiohttp/{aiohttp.__version__}"
        )
    }
    if headers:
        request_headers.update(headers)
    return request_headers


def _prepare_request_body(
    payload: dict[str, Any],
    *,
    files: Sequence[Any] | None,
    attachment_paths: Sequence[str] | None,
) -> _RequestBody:
    attachments: list[_PreparedAttachment] = []
    try:
        for file in files or ():
            attachments.append(_prepare_file(file))
        for path in attachment_paths or ():
            attachments.append(_prepare_path(path))

        body = dict(payload)
        if attachments:
            existing_attachments = body.get("attachments")
            if existing_attachments is not None:
                if not isinstance(existing_attachments, list) or any(
                    not isinstance(item, dict) for item in existing_attachments
                ):
                    raise ValueError("Discord attachment metadata is invalid.")
                attachment_metadata = [dict(item) for item in existing_attachments]
            else:
                attachment_metadata = []
            attachment_metadata.extend(
                attachment.to_payload(index) for index, attachment in enumerate(attachments)
            )
            body["attachments"] = attachment_metadata

            form = aiohttp.FormData(quote_fields=False)
            form.add_field(
                "payload_json",
                json.dumps(body, separators=(",", ":"), ensure_ascii=False),
                content_type="application/json",
            )
            for index, attachment in enumerate(attachments):
                form.add_field(
                    f"files[{index}]",
                    attachment.stream,
                    filename=attachment.filename,
                    content_type="application/octet-stream",
                )
            return _RequestBody(payload=body, form=form, attachments=attachments)

        # Validate JSON before attempting the POST, so a malformed local payload is
        # a definite non-send rather than an ambiguous delivery.
        json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        return _RequestBody(payload=body, form=None, attachments=[])
    except Exception as exc:
        for attachment in attachments:
            try:
                attachment.close()
            except Exception:
                pass
        raise NotificationDeliveryError(
            "Discord message could not be prepared.",
            delivery="not_sent",
        ) from exc


def _prepare_path(value: str) -> _PreparedAttachment:
    path = Path(value)
    stream = path.open("rb")
    return _PreparedAttachment(
        stream=stream,
        filename=path.name or "attachment",
        description=None,
        close=stream.close,
    )


def _prepare_file(value: Any) -> _PreparedAttachment:
    stream = getattr(value, "fp", value)
    filename = str(
        getattr(value, "filename", getattr(stream, "name", "attachment")) or "attachment"
    )
    description = getattr(value, "description", None)
    reset = getattr(value, "reset", None)
    if callable(reset):
        reset(seek=True)
    elif hasattr(stream, "seek"):
        stream.seek(0)
    if not hasattr(stream, "read"):
        raise ValueError("Discord file is not readable.")
    close = getattr(value, "close", None)
    if not callable(close):
        close = lambda: None
    return _PreparedAttachment(
        stream=stream,
        filename=filename,
        description=str(description) if description is not None else None,
        close=close,
    )


def _receipt_from_response(payload: Any) -> DiscordSentMessage:
    if not isinstance(payload, dict):
        raise NotificationDeliveryError(
            "Discord delivery receipt is unavailable.",
            delivery="unknown",
        )
    message_id = str(payload.get("id") or "")
    if not message_id.isdecimal():
        raise NotificationDeliveryError(
            "Discord delivery receipt is unavailable.",
            delivery="unknown",
        )
    channel_id = str(payload.get("channel_id") or "")
    return DiscordSentMessage(
        message_id=message_id,
        channel_id=channel_id if channel_id.isdecimal() else None,
    )


def _delivery_for_status(status: int) -> str:
    # A 408 or 429 can be observed after the provider has begun handling the
    # request. All other 4xx responses are an explicit non-acceptance.
    if 400 <= status < 500 and status not in {408, 429}:
        return "rejected"
    return "unknown"
