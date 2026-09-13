from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.api.v1 import discord as discord_api
from app.modules.messaging.base import IncomingChatMessage
from app.modules.messaging.discord_bot import normalize_discord_message
from app.modules.messaging.discord_transport import DiscordSentMessage
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError
from app.modules.notifications.discord_formatter import format_discord_notification
from app.services.messaging.discord_incoming import DISCORD_HELP_TEXT, incoming_from_interaction, notify_test_command_text
from app.services.discord_messaging import (
    DiscordIntegrationConfig,
    DiscordMessagingService,
    DiscordNotificationDeliveryError,
    _discord_channel_id_from_identifier,
    discord_config_from_runtime,
    discord_configuration_binding,
    discord_sender_binding,
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

    monkeypatch.setattr("app.services.discord_messaging.load_current_discord_config", fake_config)
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
    service._client = SimpleNamespace(
        guilds=[SimpleNamespace(id="guild-1", text_channels=[alerts, SimpleNamespace(id=456, name="general")])],
        get_channel=lambda _channel_id: None,
    )

    assert await service._resolve_channel("alerts") is alerts
    assert await service._resolve_channel("#alerts") is alerts


def test_discord_channel_identifier_extracts_mentions_and_urls() -> None:
    assert _discord_channel_id_from_identifier("<#123456789>") == "123456789"
    assert _discord_channel_id_from_identifier("https://discord.com/channels/1/234567890") == "234567890"
    assert _discord_channel_id_from_identifier("alerts") == "alerts"


def test_notify_test_prompt_routes_through_notification_tool() -> None:
    text = notify_test_command_text("123")

    assert "test_notification_workflow" in text
    assert "confirm_send=false" in text
    assert "type 'discord'" in text
    assert "discord:123" in text


@pytest.mark.asyncio
async def test_provider_admin_requires_linked_iacs_admin(monkeypatch) -> None:
    service = DiscordMessagingService()

    async def no_linked_admin(_provider_user_id):
        return None

    async def linked_admin(_provider_user_id):
        return "admin-1"

    monkeypatch.setattr(service, "_linked_admin_user_id", no_linked_admin)
    assert await service.author_is_admin("user-1", ["admin-role"], provider_admin=True) is False

    monkeypatch.setattr(service, "_linked_admin_user_id", linked_admin)
    assert await service.author_is_admin("user-1", [], provider_admin=False) is True


def _config(*, token: str = "synthetic-token", default_channel: str = "111111111111111111") -> DiscordIntegrationConfig:
    return DiscordIntegrationConfig(
        bot_token=token,
        guild_allowlist={"inbound-guild"},
        channel_allowlist={"inbound-channel"},
        user_allowlist={"inbound-user"},
        role_allowlist={"inbound-role"},
        admin_role_ids={"inbound-admin-role"},
        default_notification_channel_id=default_channel,
        allow_direct_messages=False,
        require_mention=True,
    )


async def test_membership_refresh_uses_fetched_roles_without_mutating_captured_message():
    service = DiscordMessagingService()
    message = normalize_discord_message(FakeMessage(), SimpleNamespace(id=999))
    fetched = []

    async def fetch_member(member_id):
        fetched.append(member_id)
        return SimpleNamespace(roles=[SimpleNamespace(id=333)])

    service._client = SimpleNamespace(
        get_guild=lambda guild_id: SimpleNamespace(fetch_member=fetch_member)
        if guild_id == 456 else None
    )
    refreshed = await service.refresh_incoming_membership(message, config=_config())
    assert fetched == [111]
    assert refreshed.author_role_ids == ["333"]
    assert message.author_role_ids == ["222"]
    assert refreshed.author_provider_id == message.author_provider_id
    assert refreshed.provider_message_id == message.provider_message_id


def test_discord_config_from_runtime_copies_only_runtime_values() -> None:
    runtime = SimpleNamespace(
        discord_bot_token="synthetic-token",
        discord_guild_allowlist=["guild-1"],
        discord_channel_allowlist=["channel-1"],
        discord_user_allowlist=["user-1"],
        discord_role_allowlist=["role-1"],
        discord_admin_role_ids=["role-admin"],
        discord_default_notification_channel_id="123456789012345678",
        discord_allow_direct_messages=True,
        discord_require_mention=False,
    )

    config = discord_config_from_runtime(runtime)
    runtime.discord_guild_allowlist.append("changed-after-read")

    assert config.guild_allowlist == {"guild-1"}
    assert config.default_notification_channel_id == "123456789012345678"
    assert config.allow_direct_messages is True
    assert config.require_mention is False


@pytest.mark.asyncio
async def test_discord_notification_action_freezes_explicit_outbound_channel_outside_inbound_allowlist(monkeypatch) -> None:
    config = _config()

    async def current_config():
        return config

    monkeypatch.setattr("app.services.discord_messaging.load_current_discord_config", current_config)
    service = DiscordMessagingService()
    action = {
        "type": "discord",
        "target_mode": "selected",
        "target_ids": ["discord:987654321012345678"],
        "title": "Synthetic title",
        "message": "Synthetic body",
    }

    prepared = await service.prepare_notification_action(
        action,
        NotificationContext("synthetic", "Synthetic title", "info", {}),
    )

    assert action.get("frozen_discord_channel_ids") is None
    assert prepared["frozen_discord_channel_ids"] == ["987654321012345678"]
    assert prepared["frozen_discord_configuration_binding"] == discord_configuration_binding(config)


@pytest.mark.asyncio
async def test_discord_notification_action_denies_changed_configuration_before_delivery(monkeypatch) -> None:
    prepared_config = _config(token="synthetic-token-a")
    current_config = _config(token="synthetic-token-b")

    async def load_prepared():
        return prepared_config

    service = DiscordMessagingService()
    monkeypatch.setattr("app.services.discord_messaging.load_current_discord_config", load_prepared)
    action = await service.prepare_notification_action(
        {"type": "discord", "target_mode": "all", "target_ids": []},
        NotificationContext("synthetic", "Synthetic title", "info", {}),
    )

    assert await service.authorize_notification_action_in_session(
        object(),
        action,
        config=current_config,
    ) == "discord_configuration_changed"


@pytest.mark.asyncio
async def test_discord_sender_refuses_a_bot_bound_to_a_previous_token(monkeypatch) -> None:
    current_config = _config(token="synthetic-token-a")
    service = DiscordMessagingService()
    service._client = SimpleNamespace(is_closed=lambda: False, user=object())
    service._sender_token_binding = discord_sender_binding("synthetic-token-b")

    async def load_current():
        return current_config

    monkeypatch.setattr("app.services.discord_messaging.load_current_discord_config", load_current)

    with pytest.raises(NotificationDeliveryError, match="reconnect") as caught:
        await service.assert_current_sender(current_config)

    assert caught.value.delivery == "not_sent"


@pytest.mark.asyncio
async def test_discord_notification_fanout_retains_order_and_known_partial_truth(monkeypatch) -> None:
    service = DiscordMessagingService()
    config = _config()
    sent: list[str] = []

    async def send(channel_id, *_args, **kwargs):
        sent.append(channel_id)
        assert kwargs["config"] is config
        if channel_id == "222222222222222222":
            raise NotificationDeliveryError("Synthetic forbidden channel", delivery="rejected")
        return SimpleNamespace(id="synthetic-message")

    monkeypatch.setattr(service, "send_message", send)
    receipt = await service.send_notification_to_channels(
        ["111111111111111111", "222222222222222222", "333333333333333333"],
        "Synthetic title",
        "Synthetic body",
        NotificationContext("synthetic", "Synthetic title", "info", {}),
        config=config,
    )

    assert sent == ["111111111111111111", "222222222222222222", "333333333333333333"]
    assert receipt == {
        "destination_outcomes": [
            {"target": "111111111111111111", "delivery": "accepted"},
            {"target": "222222222222222222", "delivery": "rejected"},
            {"target": "333333333333333333", "delivery": "accepted"},
        ],
        "partial_failure": True,
        "failure_count": 1,
    }


@pytest.mark.asyncio
async def test_discord_notification_unknown_outcome_stops_fanout_for_review(monkeypatch) -> None:
    service = DiscordMessagingService()
    config = _config()
    sent: list[str] = []

    async def send(channel_id, *_args, **_kwargs):
        sent.append(channel_id)
        if channel_id == "222222222222222222":
            raise NotificationDeliveryError("Synthetic response lost", delivery="unknown")
        return SimpleNamespace(id="synthetic-message")

    monkeypatch.setattr(service, "send_message", send)

    with pytest.raises(DiscordNotificationDeliveryError, match="review") as caught:
        await service.send_notification_to_channels(
            ["111111111111111111", "222222222222222222", "333333333333333333"],
            "Synthetic title",
            "Synthetic body",
            NotificationContext("synthetic", "Synthetic title", "info", {}),
            config=config,
        )

    assert sent == ["111111111111111111", "222222222222222222"]
    assert caught.value.destination_outcomes == [
        {"target": "111111111111111111", "delivery": "accepted"},
        {"target": "222222222222222222", "delivery": "unknown"},
        {"target": "333333333333333333", "delivery": "not_sent"},
    ]


@pytest.mark.asyncio
async def test_discord_send_never_retries_a_failed_embed_as_plaintext(monkeypatch) -> None:
    import app.services.discord_messaging as discord_messaging

    service = DiscordMessagingService()
    config = _config()
    attempts: list[dict[str, object]] = []

    async def resolve(_channel_id):
        return SimpleNamespace(id="111111111111111111")

    async def current_sender(_config=None):
        return config

    async def send_once(channel_id, token, payload, **kwargs):
        attempts.append({"channel_id": channel_id, "token": token, "payload": payload, **kwargs})
        raise NotificationDeliveryError("Synthetic transport lost", delivery="unknown")

    monkeypatch.setattr(service, "_resolve_channel", resolve)
    monkeypatch.setattr(service, "assert_current_sender", current_sender)
    monkeypatch.setattr(discord_messaging, "send_channel_message", send_once)

    with pytest.raises(NotificationDeliveryError) as caught:
        await service.send_message(
            "111111111111111111",
            "Exact formatted body",
            embeds=[{"title": "Synthetic title", "description": "Synthetic description"}],
            config=config,
        )

    assert caught.value.delivery == "unknown"
    assert len(attempts) == 1
    assert attempts[0]["payload"]["content"] == "Exact formatted body"
    assert attempts[0]["payload"]["embeds"]
    assert attempts[0]["payload"]["allowed_mentions"] == {"parse": []}


@pytest.mark.asyncio
async def test_discord_multi_batch_config_change_preserves_partial_unknown_truth(
    monkeypatch,
) -> None:
    import app.services.discord_messaging as discord_messaging

    service = DiscordMessagingService()
    config = _config()
    preflights = 0
    attempts: list[dict[str, object]] = []

    async def resolve(_channel_id):
        return SimpleNamespace(id="111111111111111111")

    async def current_sender(_config=None):
        nonlocal preflights
        preflights += 1
        if preflights == 2:
            raise NotificationDeliveryError("Discord configuration changed", delivery="not_sent")
        return config

    async def send_once(_channel_id, _token, payload, **_kwargs):
        attempts.append(payload)
        return DiscordSentMessage("987654321012345678")

    monkeypatch.setattr(service, "_resolve_channel", resolve)
    monkeypatch.setattr(service, "assert_current_sender", current_sender)
    monkeypatch.setattr(discord_messaging, "send_channel_message", send_once)

    with pytest.raises(NotificationDeliveryError) as caught:
        await service.send_message(
            "111111111111111111",
            "Exact formatted body",
            embeds=[
                {"title": "First", "description": "a" * 4000},
                {"title": "Second", "description": "b" * 4000},
            ],
            config=config,
        )

    assert caught.value.delivery == "unknown"
    assert preflights == 2
    assert len(attempts) == 1
    assert attempts[0]["content"] == "Exact formatted body"


@pytest.mark.asyncio
async def test_discord_embed_batches_respect_aggregate_character_limit(monkeypatch) -> None:
    import app.services.discord_messaging as discord_messaging

    service = DiscordMessagingService()
    config = _config()
    attempts: list[dict[str, object]] = []

    async def resolve(_channel_id):
        return SimpleNamespace(id="111111111111111111")

    async def current_sender(_config=None):
        return config

    async def send_once(_channel_id, _token, payload, **_kwargs):
        attempts.append(payload)
        return DiscordSentMessage(str(987654321012345678 + len(attempts)))

    monkeypatch.setattr(service, "_resolve_channel", resolve)
    monkeypatch.setattr(service, "assert_current_sender", current_sender)
    monkeypatch.setattr(discord_messaging, "send_channel_message", send_once)

    embeds = [
        {"title": "First", "description": "a" * 3500},
        {"title": "Second", "description": "b" * 3500},
        {"title": "Third", "description": "c" * 1000},
    ]
    await service.send_message(
        "111111111111111111",
        "Exact formatted body",
        embeds=embeds,
        config=config,
    )

    assert len(attempts) == 2
    assert attempts[0]["content"] == "Exact formatted body"
    assert attempts[1]["content"] == ""
    delivered_embeds = [embed for attempt in attempts for embed in attempt["embeds"]]
    assert delivered_embeds == embeds
    assert all(
        len(attempt["embeds"]) <= 10
        and sum(
            len(str(embed.get("title", ""))) + len(str(embed.get("description", "")))
            for embed in attempt["embeds"]
        ) <= 6000
        for attempt in attempts
    )


@pytest.mark.asyncio
async def test_discord_interaction_reply_registers_returned_confirmation_view(monkeypatch) -> None:
    import app.services.discord_messaging as discord_messaging

    service = DiscordMessagingService()
    config = _config()
    sent: dict[str, object] = {}
    registered: list[tuple[object, str]] = []
    view = SimpleNamespace(
        to_components=lambda: [{"type": 1, "components": [{"custom_id": "confirm"}]}]
    )

    async def current_sender(_config=None):
        return config

    async def send_once(application_id, token, payload):
        sent.update({"application_id": application_id, "token": token, "payload": payload})
        return DiscordSentMessage("987654321012345678")

    def register_sent_view(registered_view, message_id):
        registered.append((registered_view, message_id))
        return True

    monkeypatch.setattr(service, "assert_current_sender", current_sender)
    monkeypatch.setattr(service, "_confirmation_view", lambda _pending: view)
    monkeypatch.setattr(discord_messaging, "send_interaction_followup", send_once)
    service._client = SimpleNamespace(register_sent_view=register_sent_view)

    result = await service.send_incoming_interaction_reply(
        SimpleNamespace(application_id=123, token="synthetic-interaction-token"),
        {
            "content": "Confirmation is ready.",
            "pending_action": {
                "session_id": "session",
                "confirmation_id": "confirmation",
                "title": "Open gate?",
            },
            "requester": "Synthetic Admin",
            "mode": "confirmation",
        },
    )

    assert result.id == "987654321012345678"
    assert sent["application_id"] == 123
    assert sent["payload"]["allowed_mentions"] == {"parse": []}
    assert sent["payload"]["flags"] == 64
    assert sent["payload"]["components"] == view.to_components()
    assert registered == [(view, "987654321012345678")]


@pytest.mark.asyncio
async def test_discord_test_endpoint_reserves_one_normal_discord_rule(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def current_config():
        return _config(default_channel="111111111111111111")

    async def confirmed(session, **kwargs):
        captured["session"] = session
        captured.update(kwargs)
        return SimpleNamespace(status="sent")

    monkeypatch.setattr(discord_api, "load_current_discord_config", current_config)
    monkeypatch.setattr(discord_api, "send_confirmed_notification", confirmed)
    monkeypatch.setattr(
        discord_api,
        "get_discord_messaging_service",
        lambda: (_ for _ in ()).throw(AssertionError("The endpoint must not send directly.")),
    )

    response = await discord_api.send_discord_test(
        discord_api.DiscordTestRequest(
            channel_id="222222222222222222",
            message="Synthetic {{ subject }} template",
            confirmation_token="synthetic-confirmation",
        ),
        user=SimpleNamespace(),
        session=object(),
    )

    assert response == {"ok": True}
    assert captured["action"] == "discord.test_notification"
    assert captured["payload"] == {
        "channel_id": "222222222222222222",
        "message": "Synthetic {{ subject }} template",
    }
    rule, = captured["rules_override"]
    action, = rule["actions"]
    assert action == {
        "id": "discord-integration-test-action",
        "type": "discord",
        "target_mode": "selected",
        "target_ids": ["discord:222222222222222222"],
        "title_template": "IACS Discord integration test",
        "message_template": "Synthetic {{ subject }} template",
    }


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
