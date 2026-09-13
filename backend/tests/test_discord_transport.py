from __future__ import annotations

import asyncio
import io
import json
import sys
from importlib.metadata import version as distribution_version
from types import SimpleNamespace
from typing import Any

import pytest

import app.modules.messaging.discord_transport as discord_transport
from app.modules.messaging.discord_bot import (
    DiscordConfirmationView,
    IacsDiscordBot,
    discord,
    discord_library_available,
)
from app.modules.notifications.base import NotificationDeliveryError


def _expected_discord_user_agent() -> str:
    return (
        "DiscordBot (https://github.com/Rapptz/discord.py "
        f"{distribution_version('discord.py')}) "
        f"Python/{sys.version_info.major}.{sys.version_info.minor} "
        f"aiohttp/{discord_transport.aiohttp.__version__}"
    )


class _Response:
    def __init__(self, status: int, payload: Any = None) -> None:
        self.status = status
        self.payload = payload if payload is not None else {"id": "987654321012345678"}

    async def json(self, *, content_type=None):
        del content_type
        return self.payload


class _PostContext:
    def __init__(
        self,
        response: _Response | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.response = response
        self.error = error

    async def __aenter__(self):
        if self.error is not None:
            raise self.error
        return self.response

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback


class _ClientSession:
    def __init__(self, calls: list[dict[str, Any]], context: _PostContext, **kwargs: Any) -> None:
        self.calls = calls
        self.context = context
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback

    def post(self, url: str, **kwargs: Any) -> _PostContext:
        self.calls.append({"url": url, **kwargs})
        return self.context


def _install_http_boundary(monkeypatch, context: _PostContext) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def client_session(**kwargs: Any) -> _ClientSession:
        return _ClientSession(calls, context, **kwargs)

    monkeypatch.setattr(discord_transport.aiohttp, "ClientSession", client_session)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "context",
    [
        _PostContext(response=_Response(500)),
        _PostContext(error=discord_transport.aiohttp.ClientConnectionError("Synthetic reset")),
    ],
    ids=["server_error", "connection_reset"],
)
async def test_discord_channel_transport_attempts_one_post_for_unknown_outcomes(
    monkeypatch,
    context,
) -> None:
    calls = _install_http_boundary(monkeypatch, context)

    with pytest.raises(NotificationDeliveryError) as caught:
        await discord_transport.send_channel_message(
            "111111111111111111",
            "synthetic-bot-token",
            {"content": "Exact formatted body", "allowed_mentions": {"parse": []}},
        )

    assert caught.value.delivery == "unknown"
    assert len(calls) == 1
    assert calls[0]["allow_redirects"] is False
    assert calls[0]["headers"] == {
        "User-Agent": _expected_discord_user_agent(),
        "Authorization": "Bot synthetic-bot-token",
    }


@pytest.mark.asyncio
async def test_discord_channel_transport_marks_a_definitive_4xx_as_rejected(monkeypatch) -> None:
    calls = _install_http_boundary(monkeypatch, _PostContext(response=_Response(400)))

    with pytest.raises(NotificationDeliveryError) as caught:
        await discord_transport.send_channel_message(
            "111111111111111111",
            "synthetic-bot-token",
            {"content": "Exact formatted body", "allowed_mentions": {"parse": []}},
        )

    assert caught.value.delivery == "rejected"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_discord_channel_transport_does_not_follow_redirects(monkeypatch) -> None:
    calls = _install_http_boundary(monkeypatch, _PostContext(response=_Response(302)))

    with pytest.raises(NotificationDeliveryError) as caught:
        await discord_transport.send_channel_message(
            "111111111111111111",
            "synthetic-bot-token",
            {"content": "Exact formatted body", "allowed_mentions": {"parse": []}},
        )

    assert caught.value.delivery == "unknown"
    assert len(calls) == 1
    assert calls[0]["allow_redirects"] is False


@pytest.mark.asyncio
async def test_discord_interaction_transport_returns_message_identity_and_serializes_components(
    monkeypatch,
) -> None:
    calls = _install_http_boundary(
        monkeypatch,
        _PostContext(
            response=_Response(
                200,
                {"id": "987654321012345678", "channel_id": "111111111111111111"},
            )
        ),
    )
    payload = {
        "content": "Confirmation is ready.",
        "allowed_mentions": {"parse": []},
        "flags": 64,
        "components": [{"type": 1, "components": [{"type": 2, "custom_id": "iacs:confirm"}]}],
    }

    receipt = await discord_transport.send_interaction_followup(
        123,
        "synthetic-interaction-token",
        payload,
    )

    assert receipt.id == "987654321012345678"
    assert receipt.channel_id == "111111111111111111"
    assert len(calls) == 1
    assert calls[0]["params"] == {"wait": "true", "with_components": "true"}
    assert calls[0]["headers"] == {
        "User-Agent": _expected_discord_user_agent(),
    }
    assert calls[0]["json"] == payload
    assert calls[0]["allow_redirects"] is False


@pytest.mark.asyncio
async def test_discord_channel_transport_preserves_files_and_attachment_paths(
    monkeypatch,
    tmp_path,
) -> None:
    calls = _install_http_boundary(monkeypatch, _PostContext(response=_Response(200)))
    attachment = tmp_path / "capture.jpg"
    attachment.write_bytes(b"synthetic-image")

    class SyntheticFile:
        def __init__(self) -> None:
            self.fp = io.BytesIO(b"synthetic-file")
            self.filename = "sensor.json"
            self.description = "Synthetic sensor output"
            self.closed = False

        def reset(self, *, seek=True) -> None:
            if seek:
                self.fp.seek(0)

        def close(self) -> None:
            self.closed = True

    synthetic_file = SyntheticFile()
    await discord_transport.send_channel_message(
        "111111111111111111",
        "synthetic-bot-token",
        {"content": "Exact formatted body", "allowed_mentions": {"parse": []}},
        files=[synthetic_file],
        attachment_paths=[str(attachment)],
    )

    assert len(calls) == 1
    form = calls[0]["data"]
    field_names = [headers.get("name") for headers, _headers, _value in form._fields]
    assert field_names == ["payload_json", "files[0]", "files[1]"]
    payload_json = next(
        value for headers, _headers, value in form._fields if headers.get("name") == "payload_json"
    )
    assert json.loads(payload_json)["attachments"] == [
        {"id": 0, "filename": "sensor.json", "description": "Synthetic sensor output"},
        {"id": 1, "filename": "capture.jpg"},
    ]
    assert synthetic_file.closed is True


@pytest.mark.asyncio
@pytest.mark.skipif(
    not discord_library_available(),
    reason="discord.py is required for connection-state dispatch",
)
async def test_timed_confirmation_view_registers_in_connection_state_and_dispatches_to_gateway(
) -> None:
    assert DiscordConfirmationView is not None
    assert IacsDiscordBot is not None
    assert discord is not None

    class Gateway:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str]] = []

        async def handle_confirmation_interaction(
            self,
            _interaction,
            *,
            session_id,
            confirmation_id,
            decision,
        ) -> None:
            self.calls.append((session_id, confirmation_id, decision))

    gateway = Gateway()
    bot = IacsDiscordBot(gateway)
    view = DiscordConfirmationView(
        gateway,
        session_id="session-1",
        confirmation_id="confirmation-1",
        confirm_label="Confirm",
        cancel_label="Cancel",
        risk_level="high",
    )
    message_id = 987654321012345678

    try:
        assert view.timeout == 600
        component_rows = view.to_components()
        custom_id = component_rows[0]["components"][0]["custom_id"]
        assert custom_id == "iacs:confirm:session-1:confirmation-1"
        assert bot.register_sent_view(view, message_id) is True
        assert bot._connection._view_store.is_message_tracked(message_id) is True

        interaction = SimpleNamespace(
            data={"custom_id": custom_id},
            message=SimpleNamespace(id=message_id),
        )
        bot._connection._view_store.dispatch_view(
            discord.ComponentType.button.value,
            custom_id,
            interaction,
        )
        for _ in range(10):
            if gateway.calls:
                break
            await asyncio.sleep(0)

        assert gateway.calls == [("session-1", "confirmation-1", "confirm")]
    finally:
        view.stop()
        await bot.close()
