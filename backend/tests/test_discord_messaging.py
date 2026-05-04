from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.modules.messaging.base import IncomingChatMessage
from app.modules.messaging.discord_bot import normalize_discord_message
from app.modules.notifications.base import NotificationContext
from app.modules.notifications.discord_formatter import format_discord_notification
from app.services.discord_messaging import (
    DISCORD_HELP_TEXT,
    DiscordIntegrationConfig,
    DiscordMessagingService,
    _discord_channel_id_from_identifier,
    discord_typing,
    incoming_from_interaction,
    notify_test_command_text,
)
from app.services.messaging_bridge import deterministic_session_id, naturalize_messaging_response


class FakeMessage:
    id = 123
    content = "<@999> open the gate for the delivery driver"
    clean_content = "@Alfred open the gate for the delivery driver"
    created_at = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)

    guild = SimpleNamespace(id=456, owner_id=111)
    channel = SimpleNamespace(id=789, parent_id=None)
    author = SimpleNamespace(
        id=111,
        name="jason",
        display_name="Jason",
        roles=[SimpleNamespace(id=222)],
        guild_permissions=SimpleNamespace(administrator=False),
    )
    mentions = [SimpleNamespace(id=999)]


def test_discord_message_normalization_removes_bot_mention() -> None:
    normalized = normalize_discord_message(FakeMessage(), SimpleNamespace(id=999))

    assert normalized.provider == "discord"
    assert normalized.provider_message_id == "123"
    assert normalized.provider_guild_id == "456"
    assert normalized.provider_channel_id == "789"
    assert normalized.author_provider_id == "111"
    assert normalized.author_display_name == "Jason"
    assert normalized.author_role_ids == ["222"]
    assert normalized.author_is_provider_admin is True
    assert normalized.mentioned_bot is True
    assert normalized.is_direct_message is False
    assert normalized.text == "open the gate for the delivery driver"
    assert normalized.raw_payload["author_id"] == "111"
    assert normalized.raw_payload["author_is_provider_admin"] is True


def test_discord_message_normalization_detects_server_admin_permission() -> None:
    message = FakeMessage()
    message.guild = SimpleNamespace(id=456, owner_id=999)
    message.author = SimpleNamespace(
        id=111,
        name="jason",
        display_name="Jason",
        roles=[],
        guild_permissions=SimpleNamespace(administrator=True),
    )

    normalized = normalize_discord_message(message, SimpleNamespace(id=999))

    assert normalized.author_is_provider_admin is True


def test_discord_interaction_normalization_detects_server_owner() -> None:
    interaction = SimpleNamespace(
        id=999,
        user=SimpleNamespace(
            id=111,
            name="jason",
            display_name="Jason",
            roles=[],
            guild_permissions=SimpleNamespace(administrator=False),
        ),
        channel=SimpleNamespace(id=123),
        guild=SimpleNamespace(id=456, owner_id=111),
    )

    incoming = incoming_from_interaction(interaction, "turn on maintenance mode")

    assert incoming.author_is_provider_admin is True
    assert incoming.raw_payload["author_is_provider_admin"] is True


def test_deterministic_session_id_scopes_guild_channels_and_dms() -> None:
    guild_message = IncomingChatMessage(
        provider="discord",
        provider_message_id="m1",
        provider_channel_id="channel-1",
        provider_guild_id="guild-1",
        author_provider_id="user-1",
        author_display_name="Jason",
        text="status",
        is_direct_message=False,
        mentioned_bot=True,
        raw_payload={},
        received_at=datetime.now(tz=UTC),
    )
    same_channel_different_user = IncomingChatMessage(
        **{**guild_message.__dict__, "provider_message_id": "m2", "author_provider_id": "user-2"}
    )
    dm_message = IncomingChatMessage(
        **{**guild_message.__dict__, "is_direct_message": True, "provider_guild_id": None}
    )

    assert deterministic_session_id(guild_message) == deterministic_session_id(same_channel_different_user)
    assert deterministic_session_id(guild_message) != deterministic_session_id(dm_message)


@pytest.mark.asyncio
async def test_discord_allowlists_deny_empty_paths_and_require_mentions(monkeypatch) -> None:
    async def fake_config() -> DiscordIntegrationConfig:
        return DiscordIntegrationConfig(
            bot_token="token",
            guild_allowlist={"guild-1"},
            channel_allowlist={"channel-1"},
            user_allowlist={"user-1"},
            role_allowlist=set(),
            admin_role_ids=set(),
            default_notification_channel_id="channel-1",
            allow_direct_messages=False,
            require_mention=True,
        )

    monkeypatch.setattr("app.services.discord_messaging.load_discord_config", fake_config)
    service = DiscordMessagingService()
    base = IncomingChatMessage(
        provider="discord",
        provider_message_id="m1",
        provider_channel_id="channel-1",
        provider_guild_id="guild-1",
        author_provider_id="user-1",
        author_display_name="Jason",
        text="status",
        is_direct_message=False,
        mentioned_bot=True,
        raw_payload={},
        received_at=datetime.now(tz=UTC),
    )

    assert await service.message_is_allowed(base) == (True, "allowed")
    assert await service.message_is_allowed(IncomingChatMessage(**{**base.__dict__, "mentioned_bot": False})) == (
        False,
        "mention_required",
    )
    assert await service.message_is_allowed(IncomingChatMessage(**{**base.__dict__, "provider_channel_id": "other"})) == (
        False,
        "channel_not_allowlisted",
    )
    assert await service.message_is_allowed(IncomingChatMessage(**{**base.__dict__, "is_direct_message": True, "provider_guild_id": None})) == (
        False,
        "direct_messages_disabled",
    )


def test_discord_formatter_sanitizes_mentions_and_splits_long_content() -> None:
    payload = format_discord_notification(
        "Gate alert @everyone",
        "<p>@here vehicle <@123> arrived.</p>" + ("Long body. " * 500),
        NotificationContext(
            event_type="unauthorized_plate",
            subject="Gate alert",
            severity="critical",
            facts={},
        ),
    )

    assert payload.embeds[0].color == 0xD92D20
    assert "@ everyone" in payload.content
    assert "@here" not in payload.embeds[0].description
    assert "<@" not in payload.embeds[0].description
    assert len(payload.embeds) > 1
    assert all(len(embed.description) <= 4096 for embed in payload.embeds)


@pytest.mark.asyncio
async def test_discord_channel_resolution_accepts_names_when_visible_and_allowlisted(monkeypatch) -> None:
    async def fake_config() -> DiscordIntegrationConfig:
        return DiscordIntegrationConfig(
            bot_token="token",
            guild_allowlist={"guild-1"},
            channel_allowlist={"123"},
            user_allowlist=set(),
            role_allowlist=set(),
            admin_role_ids=set(),
            default_notification_channel_id="alerts",
            allow_direct_messages=False,
            require_mention=True,
        )

    monkeypatch.setattr("app.services.discord_messaging.load_discord_config", fake_config)
    service = DiscordMessagingService()
    alerts = SimpleNamespace(id=123, name="alerts")
    service._client = SimpleNamespace(  # noqa: SLF001
        guilds=[SimpleNamespace(id="guild-1", text_channels=[alerts, SimpleNamespace(id=456, name="general")])],
        get_channel=lambda _channel_id: None,
    )

    assert await service._resolve_channel("alerts") is alerts  # noqa: SLF001
    assert await service._resolve_channel("#alerts") is alerts  # noqa: SLF001


def test_discord_channel_identifier_extracts_mentions_and_urls() -> None:
    assert _discord_channel_id_from_identifier("<#123456789>") == "123456789"
    assert _discord_channel_id_from_identifier("https://discord.com/channels/1/234567890") == "234567890"
    assert _discord_channel_id_from_identifier("alerts") == "alerts"


def test_slash_followup_omits_none_view_for_plain_answers() -> None:
    service = DiscordMessagingService()
    result = SimpleNamespace(response_text="Alfred says hello.", pending_action=None)

    kwargs = service._slash_followup_kwargs(result, "Jason")  # noqa: SLF001

    assert kwargs["content"] == "Alfred says hello."
    assert "view" not in kwargs
    assert "embeds" not in kwargs


def test_notify_test_prompt_routes_through_notification_tool() -> None:
    text = notify_test_command_text("123")

    assert "test_notification_workflow" in text
    assert "confirm_send=false" in text
    assert "type 'discord'" in text
    assert "discord:123" in text


@pytest.mark.asyncio
async def test_provider_admin_counts_as_discord_admin_without_role_lookup(monkeypatch) -> None:
    service = DiscordMessagingService()

    async def fail_config():
        raise AssertionError("provider admin should short-circuit dynamic config lookup")

    monkeypatch.setattr("app.services.discord_messaging.load_discord_config", fail_config)

    assert await service.author_is_admin("user-1", [], provider_admin=True) is True


@pytest.mark.asyncio
async def test_provider_message_shows_typing_while_alfred_works(monkeypatch) -> None:
    service = DiscordMessagingService()
    events: list[str] = []

    class FakeTyping:
        async def __aenter__(self):
            events.append("typing_enter")

        async def __aexit__(self, exc_type, exc, tb):
            events.append("typing_exit")

    class FakeChannel:
        def typing(self):
            return FakeTyping()

        async def send(self, **kwargs):
            events.append("send")

    message = IncomingChatMessage(
        provider="discord",
        provider_message_id="m1",
        provider_channel_id="channel-1",
        provider_guild_id="guild-1",
        author_provider_id="user-1",
        author_display_name="Jason",
        text="status",
        is_direct_message=False,
        mentioned_bot=True,
        raw_payload={},
        received_at=datetime.now(tz=UTC),
    )

    async def allowed(_incoming, *, slash_command=False):
        return True, "allowed"

    async def is_admin(_provider_user_id, _role_ids, **_kwargs):
        return False

    class FakeBridge:
        async def handle_message(self, incoming, *, is_admin_hint=False):
            events.append("bridge")
            return SimpleNamespace(response_text="Alfred says hello.", pending_action=None)

    monkeypatch.setattr(service, "message_is_allowed", allowed)
    monkeypatch.setattr(service, "author_is_admin", is_admin)
    monkeypatch.setattr("app.services.messaging_bridge.messaging_bridge_service", FakeBridge())

    await service.handle_provider_message(message, SimpleNamespace(channel=FakeChannel()))

    assert events == ["typing_enter", "bridge", "send", "typing_exit"]


@pytest.mark.asyncio
async def test_typing_context_falls_back_when_channel_has_no_typing() -> None:
    events: list[str] = []

    async with discord_typing(SimpleNamespace()):
        events.append("inside")

    assert events == ["inside"]


@pytest.mark.asyncio
async def test_discord_notify_test_routes_through_messaging_bridge(monkeypatch) -> None:
    service = DiscordMessagingService()
    captured: dict[str, object] = {}

    class FakeResponse:
        async def defer(self, **kwargs):
            captured["defer"] = kwargs

        async def send_message(self, *args, **kwargs):
            captured["send_message"] = (args, kwargs)

    class FakeFollowup:
        async def send(self, **kwargs):
            captured["followup"] = kwargs

    interaction = SimpleNamespace(
        id=999,
        user=SimpleNamespace(id=111, name="jason", display_name="Jason", roles=[SimpleNamespace(id=222)]),
        channel=SimpleNamespace(id=123),
        guild=SimpleNamespace(id=456),
        response=FakeResponse(),
        followup=FakeFollowup(),
    )

    async def allowed(_incoming, *, slash_command=False):
        return True, "allowed"

    async def is_admin(_provider_user_id, _role_ids, **_kwargs):
        return True

    async def forbidden_direct_send(*_args, **_kwargs):
        raise AssertionError("notify_test should route through Alfred, not direct Discord sending")

    class FakeBridge:
        async def handle_message(self, incoming, *, is_admin_hint=False):
            captured["incoming_text"] = incoming.text
            captured["is_admin_hint"] = is_admin_hint
            return SimpleNamespace(response_text="Prepared test notification.", pending_action=None)

    monkeypatch.setattr(service, "message_is_allowed", allowed)
    monkeypatch.setattr(service, "author_is_admin", is_admin)
    monkeypatch.setattr(service, "send_notification_to_channels", forbidden_direct_send)
    monkeypatch.setattr("app.services.messaging_bridge.messaging_bridge_service", FakeBridge())

    await service.handle_slash_command(interaction, "notify_test")

    assert captured["is_admin_hint"] is True
    assert "test_notification_workflow" in str(captured["incoming_text"])
    assert "discord:123" in str(captured["incoming_text"])
    followup = captured["followup"]
    assert isinstance(followup, dict)
    assert followup["content"] == "Prepared test notification."


def test_messaging_bridge_naturalizes_raw_status_json() -> None:
    tool_results = [
        {
            "name": "get_maintenance_status",
            "output": {"maintenance_mode": {"is_active": False}},
        },
        {"name": "get_active_malfunctions", "output": {"count": 0, "malfunctions": []}},
        {"name": "query_anomalies", "output": {"count": 0, "anomalies": []}},
    ]
    raw = (
        '[{"maintenance_mode":{"is_active":false}},'
        '{"count":0,"malfunctions":[]},'
        '{"count":0,"anomalies":[]}]'
    )

    response = naturalize_messaging_response(raw, tool_results, "current IACS status")

    assert response == (
        "Maintenance Mode is off. Machinery may proceed with dignity. "
        "No active alerts. Lovely lack of drama."
    )
    assert "{" not in response
    assert "[" not in response


def test_messaging_bridge_keeps_active_alerts_plain() -> None:
    response = naturalize_messaging_response(
        "",
        [
            {
                "name": "query_anomalies",
                "output": {"count": 1, "anomalies": [{"severity": "critical", "message": "Gate forced"}]},
            }
        ],
        "current IACS status",
    )

    assert response == "1 active alert: critical Gate forced."
    assert "Lovely lack of drama" not in response


def test_messaging_bridge_leaves_natural_text_unchanged() -> None:
    response = naturalize_messaging_response("Jason is currently home.", [], "presence")

    assert response == "Jason is currently home."


def test_discord_help_text_uses_alfred_persona() -> None:
    assert "Warm, witty" in DISCORD_HELP_TEXT
    assert "serious about safety" in DISCORD_HELP_TEXT
