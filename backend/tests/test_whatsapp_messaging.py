import asyncio
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace as _SimpleNamespace
from typing import Any, cast
import uuid

import pytest
from fastapi import BackgroundTasks, HTTPException
from starlette.requests import Request

from app.ai.providers import LlmResult
from app.api.v1 import visitor_passes as visitor_passes_api
from app.api.v1 import webhooks, whatsapp as whatsapp_api
from app.models import VisitorPass
from app.models.enums import UserRole, VisitorPassStatus, VisitorPassType
from app.modules.notifications.base import NotificationContext
from app.services.messaging import whatsapp_helpers
from app.services.messaging import whatsapp_webhook as whatsapp_webhook_module
from app.services.messaging import visitor_conversation as visitor_conversation_module
from app.services.messaging import whatsapp_delivery as whatsapp_delivery_module
from app.services.settings import DEFAULT_DYNAMIC_SETTINGS, SECRET_KEYS
from app.services.visitor_passes import visitor_pass_whatsapp_history
from app.modules.messaging.whatsapp import WhatsAppIntegrationConfig, normalize_graph_api_version
from app.services.event_bus import event_bus
from app.services.visitor_conversations import get_visitor_conversation_service
from app.services import visitor_conversations as conversation_state_module
from app.services.messaging import identities as identity_module
from app.services.messaging.whatsapp_delivery import get_whatsapp_delivery_service
from app.services.messaging.visitor_conversation import get_whatsapp_visitor_conversation_service
from app.services.messaging.whatsapp_webhook import get_whatsapp_webhook_service
from app.services.messaging.whatsapp_router import WhatsAppRouter
from app.services.messaging.whatsapp_replies import WhatsAppSender
from app.services.messaging.whatsapp_helpers import (
    feedback_rating_for_reaction,
    normalize_whatsapp_phone_number,
    parse_confirmation_button_id,
    parse_reaction_message,
    parse_visitor_pass_button_id,
    parse_visitor_pass_timeframe_button_id,
    parse_visitor_pass_timeframe_confirmation_button_id,
)

SimpleNamespace = cast(Any, _SimpleNamespace)


def make_whatsapp_router():
    visitor = get_whatsapp_visitor_conversation_service()
    return WhatsAppRouter(delivery=get_whatsapp_delivery_service(), visitor=visitor, identities=visitor._identities)


async def route_bound_message(service, message, **kwargs):
    """Supply the routing precondition; PG inbox tests verify its durable owner."""
    sender = normalize_whatsapp_phone_number(message.get("from") or whatsapp_helpers.contact_wa_id(kwargs["contacts"]))
    admin = await service._identities.admin_for_phone(sender)
    visitor, state = (None, None) if admin else await service._visitor._state.visitor_pass_for_phone(sender)
    await service._handle_incoming_message(message, **kwargs,
        sender_state=WhatsAppSender(kwargs["config"], admin=admin, visitor_pass=visitor, visitor_state=state))


def patch_whatsapp_boundary(monkeypatch, name, value):
    """Patch the same DB/config/provider boundaries now owned explicitly."""
    owners = {
        "load_whatsapp_config": (visitor_conversation_module, whatsapp_delivery_module, whatsapp_webhook_module),
        "AsyncSessionLocal": (conversation_state_module, identity_module),
        "get_visitor_pass_service": (visitor_conversation_module, conversation_state_module),
        "write_audit_log": (conversation_state_module, identity_module),
        "get_runtime_config": (visitor_conversation_module,),
        "get_llm_provider": (visitor_conversation_module,),
    }
    for owner in owners[name]:
        monkeypatch.setattr(owner, name, value)


@pytest.fixture
def no_pending_admin_feedback(monkeypatch):
    """Exercise real routing against an explicitly empty conversation store."""
    import app.services.chat as chat_module

    monkeypatch.setattr(chat_module, "chat_service", FakeWhatsAppFeedbackMemory({}))


def enabled_config(**overrides):
    data = {
        "enabled": True,
        "access_token": "token",
        "phone_number_id": "123456789",
        "business_account_id": "987654321",
        "webhook_verify_token": "verify-token",
        "app_secret": "",
        "graph_api_version": "v25.0",
        "visitor_pass_template_name": "iacs_visitor_welcome",
        "visitor_pass_template_language": "en",
    }
    data.update(overrides)
    return WhatsAppIntegrationConfig(**data)


async def async_enabled_config(**overrides):
    return enabled_config(**overrides)


def make_request(
    method: str,
    path: str,
    *,
    query: str = "",
    body: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
    client: tuple[str, int] | None = None,
) -> Request:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query.encode(),
        "headers": headers or [],
    }
    if client:
        scope["client"] = client
    return Request(scope, receive)


@pytest.fixture(autouse=True)
async def cleanup_whatsapp_test_runtime(monkeypatch):
    delivery = get_whatsapp_delivery_service()
    visitor = get_whatsapp_visitor_conversation_service()
    webhook = get_whatsapp_webhook_service()
    # Explicit composition lets each assertion patch its actual boundary.
    monkeypatch.setattr(delivery, "_conversations", visitor._state)
    monkeypatch.setattr(delivery, "_identities", visitor._identities)
    monkeypatch.setattr(webhook, "_state", visitor._state)
    monkeypatch.setattr(webhook, "_identities", visitor._identities)

    async def noop(*_args, **_kwargs):
        return None

    async def false(*_args, **_kwargs):
        return False

    monkeypatch.setattr(delivery, "mark_incoming_message_read", noop)
    monkeypatch.setattr(visitor, "_record_inbound_visitor_message", noop)
    monkeypatch.setattr(visitor._state, "record_outbound_visitor_message", noop)
    monkeypatch.setattr(visitor._state, "plate_is_known_vehicle", false)
    monkeypatch.setattr(visitor._state, "visitor_reply_is_muted", false)
    monkeypatch.setattr(visitor._state, "record_visitor_plate_change_attempt", false)
    yield
    from app.db.session import engine

    await engine.dispose()
    await asyncio.sleep(0)


def test_whatsapp_dynamic_settings_are_seeded_and_secret() -> None:
    for key in (
        "whatsapp_enabled",
        "whatsapp_phone_number_id",
        "whatsapp_business_account_id",
        "whatsapp_graph_api_version",
        "whatsapp_access_token",
        "whatsapp_webhook_verify_token",
        "whatsapp_app_secret",
        "whatsapp_visitor_pass_template_name",
        "whatsapp_visitor_pass_template_language",
    ):
        assert key in DEFAULT_DYNAMIC_SETTINGS

    assert DEFAULT_DYNAMIC_SETTINGS["whatsapp_graph_api_version"][1] == "v25.0"
    assert DEFAULT_DYNAMIC_SETTINGS["whatsapp_visitor_pass_template_name"][1] == "iacs_visitor_welcome"
    assert DEFAULT_DYNAMIC_SETTINGS["whatsapp_visitor_pass_template_language"][1] == "en"
    assert {"whatsapp_access_token", "whatsapp_webhook_verify_token", "whatsapp_app_secret"}.issubset(SECRET_KEYS)


def test_phone_and_graph_version_normalization() -> None:
    assert normalize_whatsapp_phone_number("+44 (7700) 900-123") == "447700900123"
    assert normalize_graph_api_version("25.0") == "v25.0"
    assert normalize_graph_api_version("") == "v25.0"


def test_whatsapp_reaction_feedback_rating() -> None:
    reaction = parse_reaction_message(
        {
            "id": "wamid.react",
            "from": "447700900123",
            "type": "reaction",
            "reaction": {"message_id": "wamid.bad", "emoji": "👎🏽"},
        }
    )
    assert reaction is not None
    assert reaction.message_id == "wamid.bad"
    assert feedback_rating_for_reaction(reaction) == "down"

    up_reaction = parse_reaction_message({"type": "reaction", "reaction": {"message_id": "wamid.good", "emoji": "👍"}})
    assert up_reaction is not None
    assert feedback_rating_for_reaction(up_reaction) == "up"
    assert parse_reaction_message({"type": "text", "text": {"body": "👎"}}) is None


def test_visitor_emoji_only_messages_are_preference_not_content() -> None:
    assert whatsapp_helpers.visitor_message_is_emoji_only("😂👍")
    assert not whatsapp_helpers.visitor_message_is_emoji_only("AB12 CDE 👍")
    assert whatsapp_helpers.visitor_message_contains_emoji("AB12 CDE 👍")


@pytest.mark.asyncio
async def test_webhook_verification_returns_challenge_on_token_match(monkeypatch) -> None:
    async def load_config():
        return await async_enabled_config(webhook_verify_token="match-me")

    monkeypatch.setattr(webhooks, "load_whatsapp_config", load_config)

    response = await webhooks.verify_whatsapp_webhook(
        make_request(
            "GET",
            "/api/v1/webhooks/whatsapp",
            query="hub.mode=subscribe&hub.verify_token=match-me&hub.challenge=abc123",
        )
    )

    assert response.status_code == 200
    assert response.body == b"abc123"


@pytest.mark.asyncio
async def test_webhook_verification_logs_authorized_attempt_with_ip(monkeypatch, caplog) -> None:
    async def load_config():
        return await async_enabled_config(webhook_verify_token="match-me")

    monkeypatch.setattr(webhooks, "load_whatsapp_config", load_config)
    caplog.set_level(logging.INFO, logger=webhooks.logger.name)

    response = await webhooks.verify_whatsapp_webhook(
        make_request(
            "GET",
            "/api/v1/webhooks/whatsapp",
            query="hub.mode=subscribe&hub.verify_token=match-me&hub.challenge=abc123",
            headers=[(b"x-forwarded-for", b"203.0.113.8, 10.0.0.12")],
            client=("172.18.0.2", 53210),
        )
    )

    record = next(item for item in caplog.records if item.message == "whatsapp_webhook_hit")
    assert response.status_code == 200
    assert record.source_ip == "203.0.113.8"
    assert record.direct_client_ip == "172.18.0.2"
    assert record.authorized is True
    assert record.authorization_status == "authorized"
    assert record.authorization_reason == "verify_token_match"


@pytest.mark.asyncio
async def test_webhook_verification_uses_constant_time_token_compare(monkeypatch) -> None:
    calls = []

    async def load_config():
        return await async_enabled_config(webhook_verify_token="match-me")

    def compare_digest(left, right):
        calls.append((left, right))
        return True

    monkeypatch.setattr(webhooks, "load_whatsapp_config", load_config)
    monkeypatch.setattr(webhooks.hmac, "compare_digest", compare_digest)

    response = await webhooks.verify_whatsapp_webhook(
        make_request(
            "GET",
            "/api/v1/webhooks/whatsapp",
            query="hub.mode=subscribe&hub.verify_token=match-me&hub.challenge=abc123",
        )
    )

    assert response.status_code == 200
    assert calls == [("match-me", "match-me")]


@pytest.mark.asyncio
async def test_webhook_verification_rejects_bad_token(monkeypatch) -> None:
    async def load_config():
        return await async_enabled_config(webhook_verify_token="match-me")

    monkeypatch.setattr(webhooks, "load_whatsapp_config", load_config)

    with pytest.raises(HTTPException) as exc:
        await webhooks.verify_whatsapp_webhook(
            make_request(
                "GET",
                "/api/v1/webhooks/whatsapp",
                query="hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=abc123",
            )
        )

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_webhook_post_rejects_disabled_whatsapp_before_body_parse(monkeypatch, caplog) -> None:
    async def load_config():
        return await async_enabled_config(enabled=False, app_secret="")

    monkeypatch.setattr(webhooks, "load_whatsapp_config", load_config)
    caplog.set_level(logging.WARNING, logger=webhooks.logger.name)

    with pytest.raises(HTTPException) as exc:
        await webhooks.receive_whatsapp_webhook(
            make_request(
                "POST",
                "/api/v1/webhooks/whatsapp",
                body=b"not-json",
                headers=[(b"x-real-ip", b"198.51.100.20")],
                client=("172.18.0.2", 53210),
            ),
            BackgroundTasks(),
        )

    record = next(item for item in caplog.records if item.message == "whatsapp_webhook_hit")
    assert exc.value.status_code == 403
    assert "not enabled" in str(exc.value.detail).lower()
    assert record.source_ip == "198.51.100.20"
    assert record.authorized is False
    assert record.authorization_status == "unauthorized"
    assert record.authorization_reason == "integration_disabled"


@pytest.mark.asyncio
async def test_webhook_post_requires_signature_when_app_secret_configured(monkeypatch) -> None:
    async def load_config():
        return await async_enabled_config(app_secret="secret")

    monkeypatch.setattr(webhooks, "load_whatsapp_config", load_config)
    body = json.dumps({"entry": []}).encode()

    with pytest.raises(HTTPException) as exc:
        await webhooks.receive_whatsapp_webhook(
            make_request("POST", "/api/v1/webhooks/whatsapp", body=body),
            BackgroundTasks(),
        )

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_webhook_post_rejects_enabled_whatsapp_without_app_secret(monkeypatch) -> None:
    async def load_config():
        return await async_enabled_config(app_secret="")

    monkeypatch.setattr(webhooks, "load_whatsapp_config", load_config)
    body = json.dumps({"entry": []}).encode()

    with pytest.raises(HTTPException) as exc:
        await webhooks.receive_whatsapp_webhook(
            make_request("POST", "/api/v1/webhooks/whatsapp", body=body),
            BackgroundTasks(),
        )

    assert exc.value.status_code == 401
    assert "app secret" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_webhook_post_accepts_valid_signature(monkeypatch, caplog) -> None:
    handled = {}
    async def load_config():
        return await async_enabled_config(app_secret="secret")

    monkeypatch.setattr(webhooks, "load_whatsapp_config", load_config)
    caplog.set_level(logging.INFO, logger=webhooks.logger.name)

    async def handle(payload, *, signature_verified, unsigned_allowed, config=None):
        handled.update(
            {
                "payload": payload,
                "signature_verified": signature_verified,
                "unsigned_allowed": unsigned_allowed,
            }
        )

    service = get_whatsapp_webhook_service()
    monkeypatch.setattr(service, "handle_webhook_payload", handle)
    body = json.dumps({"entry": []}).encode()
    signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    background = BackgroundTasks()

    result = await webhooks.receive_whatsapp_webhook(
        make_request(
            "POST",
            "/api/v1/webhooks/whatsapp",
            body=body,
            headers=[
                (b"x-hub-signature-256", f"sha256={signature}".encode()),
                (b"cf-connecting-ip", b"192.0.2.44"),
            ],
            client=("172.18.0.2", 53210),
        ),
        background,
    )
    await background()

    record = next(item for item in caplog.records if item.message == "whatsapp_webhook_hit")
    assert result == {"status": "accepted"}
    assert handled["signature_verified"] is True
    assert handled["unsigned_allowed"] is False
    assert record.source_ip == "192.0.2.44"
    assert record.authorized is True
    assert record.authorization_status == "authorized"
    assert record.authorization_reason == "signature_valid"
    assert record.signature_verified is True


def test_whatsapp_signature_validation_fails_closed_without_secret() -> None:
    service = get_whatsapp_webhook_service()
    body = json.dumps({"entry": []}).encode()
    signature = hmac.new(b"", body, hashlib.sha256).hexdigest()

    assert service.validate_signature(body, f"sha256={signature}", "") is False


@pytest.mark.asyncio
async def test_unknown_sender_is_dropped_before_messaging_bridge(monkeypatch) -> None:
    service = make_whatsapp_router()
    calls = {"denied": 0, "sent": 0}

    async def no_admin(_sender):
        return None

    async def no_visitor(_sender):
        return None, "not_found"

    async def audit(sender, message, **_kwargs):
        calls["denied"] += 1
        assert sender == "447700900123"
        assert message["id"] == "wamid.1"

    async def send_text(*_args, **_kwargs):
        calls["sent"] += 1

    monkeypatch.setattr(service._identities, "admin_for_phone", no_admin)
    monkeypatch.setattr(service._visitor._state, "visitor_pass_for_phone", no_visitor)
    monkeypatch.setattr(service._identities, "audit_denied_sender", audit)
    monkeypatch.setattr(service._delivery, "send_text_message", send_text)

    await route_bound_message(service,
        {"id": "wamid.1", "from": "+44 7700 900123", "type": "text", "text": {"body": "status"}},
        contacts=[],
        phone_number_id="123456789",
        config=enabled_config(),
        signature_verified=False,
    )

    assert calls == {"denied": 1, "sent": 0}


@pytest.mark.asyncio
async def test_admin_sender_routes_text_to_messaging_bridge(monkeypatch, no_pending_admin_feedback) -> None:
    service = make_whatsapp_router()
    admin = SimpleNamespace(
        id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        username="jas",
        full_name="Jason",
        role=UserRole.ADMIN,
    )
    captured = {}
    sent = []

    async def admin_for_phone(sender):
        assert sender == "447700900123"
        return admin

    async def ensure_identity(*_args, **_kwargs):
        return None

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    class Bridge:
        async def handle_message(self, incoming, *, is_admin_hint=False):
            captured["incoming"] = incoming
            captured["is_admin_hint"] = is_admin_hint
            return SimpleNamespace(session_id=str(uuid.UUID(int=401)), response_text="Gate is closed.", pending_action=None)

    import app.services.messaging_bridge as messaging_bridge

    monkeypatch.setattr(service._identities, "admin_for_phone", admin_for_phone)
    monkeypatch.setattr(service._identities, "ensure_admin_identity", ensure_identity)
    monkeypatch.setattr(service._delivery, "send_text_message", send_text)
    monkeypatch.setattr(messaging_bridge, "messaging_bridge_service", Bridge())

    await route_bound_message(service,
        {"id": "wamid.2", "from": "447700900123", "type": "text", "text": {"body": "gate status"}},
        contacts=[{"wa_id": "447700900123", "profile": {"name": "Jason"}}],
        phone_number_id="123456789",
        config=enabled_config(),
        signature_verified=True,
    )

    assert captured["incoming"].provider == "whatsapp"
    assert captured["incoming"].author_provider_id == "447700900123"
    assert captured["is_admin_hint"] is True
    assert sent == [("447700900123", "Gate is closed.")]


def whatsapp_admin_user(name: str = "Mum"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        username=name.lower(),
        full_name=name,
        role=UserRole.ADMIN,
    )


class FakeWhatsAppFeedbackMemory:
    def __init__(self, memory: dict | None = None):
        self.memory = dict(memory or {})
        self.saved: list[Any] = []

    async def _ensure_session(self, session_id):
        self.session_id = session_id
        return uuid.UUID(session_id)

    async def _load_memory(self, _session_uuid):
        return dict(self.memory)

    async def _save_memory(self, _session_uuid, memory):
        self.memory = dict(memory)
        self.saved.append(dict(memory))


async def configure_admin_reaction_feedback_test(monkeypatch, *, memory: dict | None = None):
    service = make_whatsapp_router()
    admin = whatsapp_admin_user()
    sent = []
    fake_chat = FakeWhatsAppFeedbackMemory(memory)

    async def admin_for_phone(sender):
        assert sender == "447700900123"
        return admin

    async def ensure_identity(*_args, **_kwargs):
        return None

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    import app.services.chat as chat_module

    monkeypatch.setattr(service._identities, "admin_for_phone", admin_for_phone)
    monkeypatch.setattr(service._identities, "ensure_admin_identity", ensure_identity)
    monkeypatch.setattr(service._delivery, "send_text_message", send_text)
    monkeypatch.setattr(chat_module, "chat_service", fake_chat)
    return service, admin, sent, fake_chat


@pytest.mark.asyncio
async def test_admin_whatsapp_thumbs_down_reaction_asks_for_feedback_detail(monkeypatch) -> None:
    service, _admin, sent, fake_chat = await configure_admin_reaction_feedback_test(monkeypatch)

    await route_bound_message(service,
        {
            "id": "wamid.react",
            "from": "447700900123",
            "type": "reaction",
            "reaction": {"message_id": "wamid.bad-response", "emoji": "👎"},
        },
        contacts=[{"wa_id": "447700900123", "profile": {"name": "Mum"}}],
        phone_number_id="123456789",
        config=enabled_config(),
        signature_verified=True,
    )

    assert sent == [("447700900123", whatsapp_helpers.ADMIN_ALFRED_FEEDBACK_PROMPT)]
    state = fake_chat.memory[whatsapp_helpers.ADMIN_ALFRED_FEEDBACK_STATE_KEY]
    assert state["rating"] == "down"
    assert state["reacted_message_id"] == "wamid.bad-response"


@pytest.mark.asyncio
async def test_admin_whatsapp_feedback_followup_submits_reason_and_ideal(monkeypatch) -> None:
    memory = {
        whatsapp_helpers.ADMIN_ALFRED_FEEDBACK_STATE_KEY: {
            "rating": "down",
            "reacted_message_id": "wamid.bad-response",
        }
    }
    service, admin, sent, fake_chat = await configure_admin_reaction_feedback_test(monkeypatch, memory=memory)
    captured = {}

    class FeedbackService:
        async def submit_feedback_for_last_response(self, **kwargs):
            captured.update(kwargs)
            return {"corrected_answer": "Ask naturally and do not keep requesting optional details."}

    import app.services.alfred.feedback as feedback_module

    monkeypatch.setattr(feedback_module, "alfred_feedback_service", FeedbackService())

    await route_bound_message(service,
        {
            "id": "wamid.explain",
            "from": "447700900123",
            "type": "text",
            "text": {
                "body": "It kept asking for a plate after I said I don't know it. ideal: Accept that optional details can be unknown."
            },
        },
        contacts=[{"wa_id": "447700900123", "profile": {"name": "Mum"}}],
        phone_number_id="123456789",
        config=enabled_config(),
        signature_verified=True,
    )

    assert captured["rating"] == "down"
    assert captured["source_channel"] == "whatsapp"
    assert captured["actor_user_id"] == str(admin.id)
    assert captured["actor_role"] == UserRole.ADMIN.value
    assert captured["reason"] == "It kept asking for a plate after I said I don't know it."
    assert captured["ideal_answer"] == "Accept that optional details can be unknown."
    assert whatsapp_helpers.ADMIN_ALFRED_FEEDBACK_STATE_KEY not in fake_chat.memory
    assert sent == [
        (
            "447700900123",
            "Thanks, I logged that Alfred feedback.\n\nCorrected answer:\nAsk naturally and do not keep requesting optional details.",
        )
    ]


@pytest.mark.asyncio
async def test_admin_whatsapp_visitor_pass_text_uses_shared_alfred_bridge(monkeypatch, no_pending_admin_feedback) -> None:
    service = make_whatsapp_router()
    admin = whatsapp_admin_user()
    captured = {}
    sent = []
    confirmations = []

    async def admin_for_phone(sender):
        assert sender == "447700900123"
        return admin

    async def ensure_identity(*_args, **_kwargs):
        return None

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    async def send_confirmation(to, pending_action):
        confirmations.append((to, pending_action))

    class Bridge:
        async def handle_message(self, incoming, *, is_admin_hint=False):
            captured["incoming"] = incoming
            captured["is_admin_hint"] = is_admin_hint
            return SimpleNamespace(
                session_id=str(uuid.UUID(int=401)), response_text="Create a Visitor Pass for John Doe?",
                pending_action={"tool_name": "create_visitor_pass", "confirmation_id": "confirm-pass"},
            )

    import app.services.messaging_bridge as messaging_bridge

    monkeypatch.setattr(service._identities, "admin_for_phone", admin_for_phone)
    monkeypatch.setattr(service._identities, "ensure_admin_identity", ensure_identity)
    monkeypatch.setattr(service._delivery, "send_text_message", send_text)
    monkeypatch.setattr(service._delivery, "send_confirmation_message", send_confirmation)
    monkeypatch.setattr(messaging_bridge, "messaging_bridge_service", Bridge())

    assert not hasattr(service, "_handle_admin_visitor_pass_workflow")

    await route_bound_message(service,
        {
            "id": "wamid.pass",
            "from": "447700900123",
            "type": "text",
            "text": {"body": "John Doe is coming tomorrow at 9am until 5pm"},
        },
        contacts=[{"wa_id": "447700900123", "profile": {"name": "Mum"}}],
        phone_number_id="123456789",
        config=enabled_config(),
        signature_verified=True,
    )

    assert captured["incoming"].provider == "whatsapp"
    assert captured["incoming"].text == "John Doe is coming tomorrow at 9am until 5pm"
    assert captured["is_admin_hint"] is True
    assert sent == [("447700900123", "Create a Visitor Pass for John Doe?")]
    assert confirmations == [("447700900123", {"tool_name": "create_visitor_pass", "confirmation_id": "confirm-pass"})]


@pytest.mark.asyncio
async def test_incoming_admin_message_is_marked_read_with_typing_indicator(monkeypatch, no_pending_admin_feedback) -> None:
    service = make_whatsapp_router()
    admin = SimpleNamespace(
        id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        username="jas",
        full_name="Jason",
        role=UserRole.ADMIN,
    )
    acknowledgements = []

    async def admin_for_phone(_sender):
        return admin

    async def ensure_identity(*_args, **_kwargs):
        return None

    async def mark_read(message_id, **kwargs):
        acknowledgements.append((message_id, kwargs))

    async def send_text(*_args, **_kwargs):
        return None

    class Bridge:
        async def handle_message(self, _incoming, *, is_admin_hint=False):
            assert is_admin_hint is True
            return SimpleNamespace(session_id=str(uuid.UUID(int=401)), response_text="Gate is closed.", pending_action=None)

    import app.services.messaging_bridge as messaging_bridge

    monkeypatch.setattr(service._identities, "admin_for_phone", admin_for_phone)
    monkeypatch.setattr(service._identities, "ensure_admin_identity", ensure_identity)
    monkeypatch.setattr(service._delivery, "mark_incoming_message_read", mark_read)
    monkeypatch.setattr(service._delivery, "send_text_message", send_text)
    monkeypatch.setattr(messaging_bridge, "messaging_bridge_service", Bridge())

    await route_bound_message(service,
        {"id": "wamid.ack", "from": "447700900123", "type": "text", "text": {"body": "gate status"}},
        contacts=[{"wa_id": "447700900123", "profile": {"name": "Jason"}}],
        phone_number_id="123456789",
        config=enabled_config(),
        signature_verified=True,
    )

    assert acknowledgements == [("wamid.ack", {"config": enabled_config(), "show_typing": True})]


@pytest.mark.asyncio
async def test_mark_read_payload_can_include_typing_indicator(monkeypatch) -> None:
    service = get_whatsapp_delivery_service()
    captured = {}

    async def post(config, payload):
        captured["config"] = config
        captured["payload"] = payload
        return {"success": True}

    monkeypatch.setattr(service._transport, "send", post)
    result = await type(service).mark_incoming_message_read(
        service,
        "wamid.in",
        config=enabled_config(),
        show_typing=True,
    )

    assert result == {"success": True}
    assert captured["config"].phone_number_id == "123456789"
    assert captured["payload"] == {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": "wamid.in",
        "typing_indicator": {"type": "text"},
    }


@pytest.mark.asyncio
async def test_status_webhook_tracks_visitor_message_received_and_read(monkeypatch) -> None:
    service = get_whatsapp_webhook_service()
    updates = []

    async def load_config(*_args, **_kwargs):
        return await async_enabled_config()

    async def update_delivery(phone_number, status, *, message_id=None):
        updates.append((phone_number, status, message_id))

    patch_whatsapp_boundary(monkeypatch, "load_whatsapp_config", load_config)
    monkeypatch.setattr(service._state, "update_visitor_delivery_status_for_phone", update_delivery)

    await service.handle_webhook_payload(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "statuses": [
                                    {"id": "wamid.out.1", "recipient_id": "+44 7700 900123", "status": "delivered"},
                                    {"id": "wamid.out.1", "recipient_id": "+44 7700 900123", "status": "read"},
                                ],
                            }
                        }
                    ]
                }
            ]
        },
        signature_verified=True,
        unsigned_allowed=False,
    )

    assert updates == [
        ("447700900123", "message_received", "wamid.out.1"),
        ("447700900123", "message_read", "wamid.out.1"),
    ]


@pytest.mark.asyncio
async def test_incoming_whatsapp_acceptance_commits_before_wakeup(monkeypatch) -> None:
    service = get_whatsapp_webhook_service()
    calls, identity = [], uuid.uuid4()

    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def commit(self): calls.append("committed")

    class Store:
        sessions = Session
        async def accept_in_session(self, session, **values):
            calls.append("accepted")
            assert values["provider"] == "whatsapp"
            assert values["author_provider_id"] == "447700900123"
            assert values["routing_context"] == {"kind": "denied", "business_account_id": "987654321"}
            assert values["envelope"]["message"]["id"] == "wamid.dedupe"
            return identity

    async def no_admin(_sender): return None
    async def no_visitor(_sender): return None, "not_found"
    monkeypatch.setattr(service, "_store", Store())
    monkeypatch.setattr(service._identities, "admin_for_phone", no_admin)
    monkeypatch.setattr(service._state, "visitor_pass_for_phone", no_visitor)
    monkeypatch.setattr(service._dispatcher, "wake", lambda: calls.append("wake"))
    first = await service._accept_incoming_provider_message(
        {"id": "wamid.dedupe", "type": "text", "text": {"body": "hello"}, "from": "+44 7700 900123"},
        contacts=[], phone_number_id="123456789", config=enabled_config(), signature_verified=True)
    assert first == str(identity)
    assert calls == ["accepted", "committed", "wake"]


@pytest.mark.asyncio
async def test_text_send_payload_uses_meta_cloud_api_shape(monkeypatch) -> None:
    service = get_whatsapp_delivery_service()
    captured = {}

    async def post(config, payload):
        captured["config"] = config
        captured["payload"] = payload
        return {"messages": [{"id": "wamid.out"}]}

    monkeypatch.setattr(service._transport, "send", post)
    result = await service.send_text_message("+44 7700 900123", "Hello", config=enabled_config())

    assert result["messages"][0]["id"] == "wamid.out"
    assert captured["config"].graph_api_version == "v25.0"
    assert captured["payload"] == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "447700900123",
        "type": "text",
        "text": {"preview_url": False, "body": "Hello"},
    }


@pytest.mark.asyncio
async def test_visitor_pass_custom_message_endpoint_uses_pass_scoped_service(monkeypatch) -> None:
    pass_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), username="jas", full_name="Jason Ash")
    captured = {}
    visitor_payload = {
        "id": str(pass_id),
        "visitor_name": "Ash",
        "pass_type": "duration",
        "visitor_phone": "447700900123",
        "expected_time": "2026-05-04T09:00:00+01:00",
        "window_minutes": 30,
        "window_start": "2026-05-04T09:00:00+01:00",
        "window_end": "2026-05-04T21:30:00+01:00",
        "valid_from": "2026-05-04T09:00:00+01:00",
        "valid_until": "2026-05-04T21:30:00+01:00",
        "status": "scheduled",
        "creation_source": "ui",
        "created_by_user_id": str(user.id),
        "created_by": "Jason Ash",
        "arrival_time": None,
        "departure_time": None,
        "number_plate": "Y90AGS",
        "vehicle_make": None,
        "vehicle_colour": None,
        "duration_on_site_seconds": None,
        "duration_human": None,
        "arrival_event_id": None,
        "departure_event_id": None,
        "telemetry_trace_id": None,
        "source_reference": None,
        "source_metadata": None,
        "whatsapp_status": "complete",
        "whatsapp_status_label": "Complete - Vehicle Registration: Y90AGS",
        "whatsapp_status_detail": None,
        "created_at": "2026-05-02T18:00:00+01:00",
        "updated_at": "2026-05-02T18:01:00+01:00",
    }
    message_payload = {
        "id": "history-1",
        "direction": "outbound",
        "kind": "text",
        "body": "Do you want me to move your visitor pass to tomorrow?",
        "actor_label": "IACS",
        "provider_message_id": "wamid.custom",
        "status": "sent",
        "created_at": "2026-05-02T18:01:00+01:00",
        "metadata": {"origin": "dashboard_custom", "sender_user_id": str(user.id)},
    }

    run_id, claim, stages = uuid.uuid4(), object(), []
    class Service:
        async def reserve_custom_message_in_session(self, session, pass_id_arg, message, *, actor_user, confirmation_token):
            captured.update(pass_id=pass_id_arg, message=message, actor_user=actor_user, token=confirmation_token)
            stages.append("reserved")
            return run_id, claim
    class Session:
        async def commit(self): stages.append("committed")
    async def dispatch(identity, row):
        assert identity == run_id and row is claim
        stages.append("dispatched")
        return SimpleNamespace(status="sent")
    async def result(pass_arg, identity):
        assert pass_arg == pass_id and identity == run_id
        return {"visitor_pass": visitor_payload, "message": message_payload}
    monkeypatch.setattr(visitor_passes_api, "get_whatsapp_visitor_conversation_service", lambda: Service())
    monkeypatch.setattr(visitor_passes_api, "get_notification_service", lambda: SimpleNamespace(dispatch_reserved=dispatch))
    monkeypatch.setattr(visitor_passes_api, "get_visitor_conversation_service", lambda: SimpleNamespace(get_notification_result=result))

    response = await visitor_passes_api.send_visitor_pass_whatsapp_message(
        pass_id,
        visitor_passes_api.VisitorPassWhatsAppSendRequest(
            message="  Do you want me to move your visitor pass to tomorrow?  ",
            confirmation_token="server-token",
        ),
        user=user, session=Session(),
    )

    assert captured == {"pass_id": pass_id, "message": "Do you want me to move your visitor pass to tomorrow?",
        "actor_user": user, "token": "server-token"}
    assert stages == ["reserved", "committed", "dispatched"]
    assert response.notification_run_id == str(run_id)
    assert response.message.body == "Do you want me to move your visitor pass to tomorrow?"
    assert response.message.metadata["origin"] == "dashboard_custom"
    assert response.visitor_pass.id == str(pass_id)


@pytest.mark.asyncio
async def test_visitor_pass_whatsapp_unblock_endpoint_uses_pass_scoped_service(monkeypatch) -> None:
    pass_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), username="jas", full_name="Jason Ash")
    captured = {}
    visitor_payload = {
        "id": str(pass_id),
        "visitor_name": "Ash",
        "pass_type": "duration",
        "visitor_phone": "447700900123",
        "expected_time": "2026-05-04T09:00:00+01:00",
        "window_minutes": 30,
        "window_start": "2026-05-04T09:00:00+01:00",
        "window_end": "2026-05-04T21:30:00+01:00",
        "valid_from": "2026-05-04T09:00:00+01:00",
        "valid_until": "2026-05-04T21:30:00+01:00",
        "status": "scheduled",
        "creation_source": "ui",
        "created_by_user_id": str(user.id),
        "created_by": "Jason Ash",
        "arrival_time": None,
        "departure_time": None,
        "number_plate": "Y90AGS",
        "vehicle_make": None,
        "vehicle_colour": None,
        "duration_on_site_seconds": None,
        "duration_human": None,
        "arrival_event_id": None,
        "departure_event_id": None,
        "telemetry_trace_id": None,
        "source_reference": None,
        "source_metadata": {},
        "whatsapp_status": "complete",
        "whatsapp_status_label": "Complete - Vehicle Registration: Y90AGS",
        "whatsapp_status_detail": "Visitor abuse cooldown was cleared by Jason Ash.",
        "created_at": "2026-05-02T18:00:00+01:00",
        "updated_at": "2026-05-02T18:01:00+01:00",
    }

    class Service:
        async def clear_visitor_abuse_mute(self, pass_id_arg, *, actor_user):
            captured["pass_id"] = pass_id_arg
            captured["actor_user"] = actor_user
            return visitor_payload

    monkeypatch.setattr(visitor_passes_api, "get_visitor_conversation_service", lambda: Service())

    async def fake_confirmation(_session, **kwargs) -> None:
        captured["confirmation"] = kwargs

    monkeypatch.setattr(visitor_passes_api, "require_confirmed_action", fake_confirmation)

    response = await visitor_passes_api.unblock_visitor_pass_whatsapp(
        pass_id,
        request=visitor_passes_api.VisitorPassConfirmationRequest(confirmation_token="server-token"),
        user=user,
    )

    assert captured == {
        "pass_id": pass_id,
        "actor_user": user,
        "confirmation": {
            "user": user,
            "action": "visitor_pass.whatsapp_unblock",
            "payload": {"pass_id": str(pass_id)},
            "confirmation_token": "server-token",
        },
    }
    assert response.id == str(pass_id)
    assert response.source_metadata == {}


@pytest.mark.asyncio
async def test_custom_message_reserves_exact_pass_and_literal_body_without_io(monkeypatch) -> None:
    from app.services import notifications as notification_module
    service = get_whatsapp_visitor_conversation_service()
    actor, visitor = SimpleNamespace(id=uuid.uuid4(), auth_session_version=4), SimpleNamespace(id=uuid.uuid4())
    origin, calls, session = {"recipient": "447700900123", "pass_id": str(visitor.id)}, [], object()
    async def prepare(session_arg, pass_id, **kwargs):
        assert session_arg is session and pass_id == visitor.id
        assert kwargs == {"actor_user_id": actor.id, "auth_version": 4, "kind": "custom"}
        return visitor, origin
    async def reserve(session_arg, **kwargs):
        assert session_arg is session
        calls.append(kwargs)
        return uuid.UUID(int=8), object()
    monkeypatch.setattr(service._state, "prepare_manual_notification_origin", prepare)
    monkeypatch.setattr(notification_module, "get_notification_service", lambda: SimpleNamespace(reserve_confirmed_request=reserve))
    async def forbidden(*args, **kwargs): raise AssertionError("Intake must not send")
    monkeypatch.setattr(service._transport, "send", forbidden)
    identity, _claim = await service.reserve_custom_message_in_session(session, visitor.id,
        "Do you want me to move your visitor pass to tomorrow?", actor_user=actor, confirmation_token="token")
    assert identity == uuid.UUID(int=8)
    assert calls[0]["visitor_origin"] is origin
    assert calls[0]["action"] == "visitor_pass.whatsapp_send"
    assert calls[0]["payload"] == {"pass_id": str(visitor.id), "message": "Do you want me to move your visitor pass to tomorrow?"}
    assert calls[0]["direct_action"] == {"type": "whatsapp", "delivery_mode": "literal", "target": "447700900123",
        "title": "", "message": "Do you want me to move your visitor pass to tomorrow?"}



@pytest.mark.asyncio
async def test_clear_visitor_abuse_mute_removes_cooldown_and_records_status(monkeypatch) -> None:
    service = get_visitor_conversation_service()
    actor = SimpleNamespace(id=uuid.uuid4(), username="jas", full_name="Jason Ash", auth_session_version=0)
    pass_id = uuid.uuid4()
    visitor_pass = VisitorPass(
        id=pass_id,
        visitor_name="Ash",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 4, 9, 0, tzinfo=UTC),
        valid_from=datetime(2026, 5, 4, 9, 0, tzinfo=UTC),
        valid_until=datetime(2026, 5, 4, 21, 30, tzinfo=UTC),
        status=VisitorPassStatus.SCHEDULED,
        creation_source="ui",
        source_metadata={
            "whatsapp_abuse_muted_until": "2026-05-04T10:00:00+00:00",
            "whatsapp_abuse_muted_reason": "plate_changes",
            "whatsapp_concierge_status": "complete",
        },
        number_plate="Y90AGS",
    )
    visitor_pass.created_at = datetime(2026, 5, 2, 17, 0, tzinfo=UTC)
    visitor_pass.updated_at = datetime(2026, 5, 2, 17, 0, tzinfo=UTC)
    captured: dict[str, Any] = {"published": []}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, key):
            assert model is VisitorPass
            assert key == pass_id
            return visitor_pass

        async def scalar(self, statement):
            assert statement._for_update_arg is not None
            return visitor_pass

        async def commit(self):
            captured["committed"] = True

        async def refresh(self, row):
            captured["refreshed"] = row.id

    async def audit(*_args, **kwargs):
        captured["audit"] = kwargs

    async def publish(event, payload):
        captured["published"].append((event, payload))

    patch_whatsapp_boundary(monkeypatch, "AsyncSessionLocal", lambda: Session())
    patch_whatsapp_boundary(monkeypatch, "write_audit_log", audit)
    monkeypatch.setattr(event_bus, "publish", publish)

    async def current_actor(session, user_id, *, auth_version, lock):
        assert user_id == actor.id and auth_version == 0 and lock is True
        return actor
    monkeypatch.setattr(conversation_state_module, "load_active_admin", current_actor)

    result = await service.clear_visitor_abuse_mute(pass_id, actor_user=actor)

    assert "whatsapp_abuse_muted_until" not in visitor_pass.source_metadata
    assert "whatsapp_abuse_muted_reason" not in visitor_pass.source_metadata
    history = visitor_pass_whatsapp_history(visitor_pass)
    assert history[0]["kind"] == "operator_action"
    assert "unblocked" in history[0]["body"]
    assert captured["audit"]["action"] == "visitor_pass.whatsapp_abuse_cooldown_cleared"
    assert captured["audit"]["metadata"]["muted_reason"] == "plate_changes"
    assert captured["published"][0][0] == "visitor_pass.updated"
    assert result["source_metadata"] == visitor_pass.source_metadata


@pytest.mark.asyncio
async def test_template_send_payload_uses_configured_sender_id(monkeypatch) -> None:
    service = get_whatsapp_delivery_service()
    captured = {}
    config = enabled_config(phone_number_id="configured-sender-id")

    async def post(config_arg, payload):
        captured["config"] = config_arg
        captured["payload"] = payload
        return {"messages": [{"id": "wamid.template"}]}

    monkeypatch.setattr(service._transport, "send", post)

    await service.send_template_message(
        "+44 7700 900123",
        template_name="iacs_visitor_welcome",
        language_code="en",
        body_parameters=["Sarah"],
        config=config,
    )

    assert captured["config"].phone_number_id == "configured-sender-id"
    assert captured["payload"]["type"] == "template"
    assert captured["payload"]["to"] == "447700900123"
    assert captured["payload"]["template"] == {
        "name": "iacs_visitor_welcome",
        "language": {"code": "en"},
        "components": [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": "Sarah"},
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_visitor_pass_outreach_reserves_approved_welcome_template_shape(monkeypatch) -> None:
    from app.services import notification_runs
    service, captured = get_whatsapp_delivery_service(), {}
    visitor = VisitorPass(id=uuid.uuid4(), visitor_name="Ash", pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123", status=VisitorPassStatus.SCHEDULED)
    actor = uuid.uuid4()
    class Session:
        async def flush(self): pass
    session = Session()
    async def prepare(session_arg, identity, **kwargs):
        assert session_arg is session and identity == visitor.id
        return visitor, {"version": 1, "kind": "outreach", "pass_id": str(visitor.id), "recipient": visitor.visitor_phone}
    async def enqueue(_self, session_arg, context, **kwargs):
        assert session_arg is session
        captured.update(context=context, **kwargs)
        return kwargs["run_id"]
    async def config(**kwargs): return enabled_config()
    monkeypatch.setattr(service._conversations, "prepare_manual_notification_origin", prepare)
    monkeypatch.setattr(whatsapp_delivery_module, "load_whatsapp_config", config)
    monkeypatch.setattr(whatsapp_delivery_module, "get_visitor_pass_service", lambda: SimpleNamespace(status_for=lambda *_: VisitorPassStatus.SCHEDULED))
    monkeypatch.setattr(notification_runs.NotificationRunStore, "enqueue_prepared_in_session", enqueue)
    result = await service.reserve_outreach_in_session(session, visitor, actor_user_id=actor, auth_version=0, source="ui")
    assert result == uuid.uuid5(visitor.id, "visitor-outreach")
    action = captured["plan"][0]["action"]
    assert action["template_name"] == "iacs_visitor_welcome" and action["language_code"] == "en"
    assert action["body_parameters"] == ["Ash"] and action["target"] == "447700900123"
    assert captured["plan"][0]["state"] == "pending"



@pytest.mark.asyncio
async def test_visitor_plate_confirmation_buttons_use_namespaced_payload(monkeypatch) -> None:
    service = get_whatsapp_visitor_conversation_service()
    captured = {}
    pass_id = uuid.uuid4()
    visitor_pass = VisitorPass(
        id=pass_id,
        visitor_name="Sarah",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 1, 10, 0),
        valid_from=datetime(2026, 5, 1, 10, 0),
        valid_until=datetime(2026, 5, 1, 18, 0),
        status=VisitorPassStatus.ACTIVE,
        creation_source="ui",
    )

    async def send_buttons(to, body, buttons, **_kwargs):
        captured["to"] = to
        captured["body"] = body
        captured["buttons"] = buttons

    monkeypatch.setattr(service._delivery, "send_interactive_buttons", send_buttons)

    await service.send_visitor_plate_confirmation(
        "447700900123",
        visitor_pass,
        "AB12CDE",
        "nonce123",
        vehicle_make="Tesla",
        vehicle_colour="Silver",
        emoji_preferred=True,
        alfred_mentioned=True,
        alfred_nod="Alfred just adjusted his imaginary pocket protector for Jason.",
    )

    assert captured["to"] == "447700900123"
    assert captured["body"].startswith("Thanks Sarah.")
    assert "I read your registration as" in captured["body"]
    assert "AB12 CDE" in captured["body"]
    assert "which is a Silver Tesla" in captured["body"]
    assert "DVLA" not in captured["body"]
    assert "Alfred just adjusted his imaginary pocket protector for Jason." in captured["body"]
    assert "Jason's access-control side quest gains +1 XP" not in captured["body"]
    assert "tap Change" in captured["body"]
    assert "tap Confirm" in captured["body"]
    assert "Very official, only slightly over-engineered." in captured["body"]
    assert captured["body"].endswith("👍")
    parsed_confirm = parse_visitor_pass_button_id(captured["buttons"][0]["id"])
    parsed_change = parse_visitor_pass_button_id(captured["buttons"][1]["id"])
    assert parsed_confirm is not None
    assert parsed_change is not None
    assert parsed_confirm.decision == "confirm"
    assert parsed_confirm.pass_id == str(pass_id)
    assert parsed_confirm.nonce == "nonce123"
    assert parsed_change.decision == "change"


@pytest.mark.asyncio
async def test_visitor_plate_confirmation_publishes_arranged_event(monkeypatch) -> None:
    service = get_whatsapp_visitor_conversation_service()
    pass_id = uuid.uuid4()
    visitor_pass = VisitorPass(
        id=pass_id,
        visitor_name="Sarah",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        valid_from=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        valid_until=datetime(2026, 5, 1, 18, 0, tzinfo=UTC),
        status=VisitorPassStatus.ACTIVE,
        creation_source="ui",
        source_metadata={
            "whatsapp_pending_plate": "AB12CDE",
            "whatsapp_pending_nonce": "nonce123",
            "whatsapp_pending_vehicle_make": "Tesla",
            "whatsapp_pending_vehicle_colour": "Silver",
            "whatsapp_concierge_status": "visitor_replied",
        },
    )
    visitor_pass.created_at = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)
    visitor_pass.updated_at = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)
    captured: dict[str, Any] = {"published": [], "sent": []}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, key):
            assert model is VisitorPass
            assert key == pass_id
            return visitor_pass

        async def scalar(self, statement):
            assert statement._for_update_arg is not None
            return visitor_pass

        async def commit(self):
            captured["committed"] = True

        async def flush(self): pass

        async def refresh(self, row, **kwargs):
            captured["refreshed"] = row.id

        async def rollback(self):
            captured["rolled_back"] = True

    class VisitorPassService:
        def status_for(self, row, now): return row.status

        async def refresh_statuses(self, **_kwargs):
            return []

        async def update_visitor_plate(self, _session, pass_arg, **kwargs):
            pass_arg.number_plate = "AB12CDE"
            pass_arg.vehicle_make = kwargs.get("vehicle_make")
            pass_arg.vehicle_colour = kwargs.get("vehicle_colour")
            return pass_arg

    async def publish(event, payload):
        captured["published"].append((event, payload))

    async def send_text(to, body, **_kwargs):
        captured["sent"].append((to, body))

    patch_whatsapp_boundary(monkeypatch, "AsyncSessionLocal", lambda: Session())
    patch_whatsapp_boundary(monkeypatch, "get_visitor_pass_service", lambda: VisitorPassService())
    monkeypatch.setattr(event_bus, "publish", publish)
    monkeypatch.setattr(service._delivery, "send_text_message", send_text)

    async def enqueue(store, session, context, *, run_id):
        captured.setdefault("reserved", []).append((context, run_id))
        return run_id
    monkeypatch.setattr(conversation_state_module.NotificationRunStore, "enqueue_in_session", enqueue)

    button = parse_visitor_pass_button_id(f"iacs:vp:confirm:{pass_id}:nonce123")
    assert button is not None
    await service._handle_visitor_button_reply(button, "447700900123", config=enabled_config())

    assert [event for event, _payload in captured["published"]] == ["visitor_pass.updated", "visitor_pass.arranged"]
    arranged_payload = captured["published"][1][1]["visitor_pass"]
    assert arranged_payload["number_plate"] == "AB12CDE"
    assert arranged_payload["vehicle_make"] == "Tesla"
    assert arranged_payload["vehicle_colour"] == "Silver"
    assert arranged_payload["source_metadata"]["whatsapp_concierge_status"] == "complete"
    assert captured["published"][1][1]["source"] == "whatsapp_visitor"
    assert captured["sent"][0][0] == "447700900123"


def test_visitor_plate_saved_message_is_warm_and_vehicle_aware() -> None:
    body = whatsapp_helpers.visitor_plate_saved_message(
        {
            "visitor_name": "Josh",
            "number_plate": "C25UNY",
            "vehicle_make": "Tesla",
            "vehicle_colour": "Black",
            "source_metadata": {},
        },
        emoji_preferred=True,
    )

    assert body.startswith("Thanks Josh. All set.")
    assert "C25 UNY, the Black Tesla" in body
    assert "We're looking forward to seeing you at Crest House." in body
    assert "virtual clipboard" not in body
    assert "Alfred" not in body
    assert body.endswith("👍")


def test_visitor_confirmation_does_not_name_alfred_without_visitor_mention() -> None:
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Josh",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 1, 10, 0),
        valid_from=datetime(2026, 5, 1, 10, 0),
        valid_until=datetime(2026, 5, 1, 18, 0),
        status=VisitorPassStatus.ACTIVE,
        creation_source="ui",
    )

    body = whatsapp_helpers.visitor_plate_confirmation_message(
        visitor_pass,
        "C25UNY",
        vehicle_make="Tesla",
        vehicle_colour="Black",
        alfred_mentioned=False,
        alfred_nod="Alfred says Jason has achieved peak driveway nerd.",
    )

    assert "C25 UNY" in body
    assert "Black Tesla" in body
    assert "Alfred" not in body


def test_visitor_freeform_reply_strips_unprompted_alfred_name() -> None:
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Josh",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 1, 10, 0),
        valid_from=datetime(2026, 5, 1, 10, 0),
        valid_until=datetime(2026, 5, 1, 18, 0),
        status=VisitorPassStatus.ACTIVE,
        creation_source="ui",
    )

    body = whatsapp_helpers.style_visitor_freeform_reply(
        "Alfred says you're all set. You're all set.",
        visitor_pass,
        "thanks",
        alfred_mentioned=False,
    )

    assert body == "You're all set."
    assert "Alfred" not in body


def test_visitor_registration_not_found_message_is_plain() -> None:
    body = whatsapp_helpers.visitor_registration_not_found_message("B00B1ES")

    assert "B00B1ES" in body
    assert "Please check the registration" in body
    assert "DVLA" not in body


@pytest.mark.asyncio
async def test_visitor_alfred_name_nod_is_llm_generated(monkeypatch) -> None:
    service = get_whatsapp_visitor_conversation_service()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Josh",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 1, 10, 0),
        valid_from=datetime(2026, 5, 1, 10, 0),
        valid_until=datetime(2026, 5, 1, 18, 0),
        status=VisitorPassStatus.ACTIVE,
        creation_source="ui",
    )
    captured = {}

    async def runtime():
        return SimpleNamespace(llm_provider="openai", site_timezone="Europe/London")

    class Provider:
        async def complete(self, messages, **_kwargs):
            captured["messages"] = messages
            return LlmResult('{"nod":"Alfred says Jason has reached maximum access-control wizardry."}')

    patch_whatsapp_boundary(monkeypatch, "get_runtime_config", runtime)
    patch_whatsapp_boundary(monkeypatch, "get_llm_provider", lambda _provider_name: Provider())

    nod = await service._visitor_alfred_name_nod(visitor_pass, "Thanks Alfred")

    assert nod == "Alfred says Jason has reached maximum access-control wizardry."
    prompt = captured["messages"][0].content
    assert "Vary the wording using the supplied style_seed" in prompt
    assert "Do not reuse this phrase" in prompt
    user_payload = json.loads(captured["messages"][1].content)
    assert user_payload["visitor_message"] == "Thanks Alfred"
    assert user_payload["style_seed"]


@pytest.mark.asyncio
async def test_visitor_sender_routes_to_sandbox_not_messaging_bridge(monkeypatch) -> None:
    service = make_whatsapp_router()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Sarah",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 1, 10, 0),
        valid_from=datetime(2026, 5, 1, 10, 0),
        valid_until=datetime(2026, 5, 1, 18, 0),
        status=VisitorPassStatus.ACTIVE,
        creation_source="ui",
    )
    captured = {}

    async def no_admin(_sender):
        return None

    async def visitor_for_phone(sender):
        assert sender == "447700900123"
        return visitor_pass, "active"

    async def visitor_result(sender, pass_arg, text, **_kwargs):
        assert sender == "447700900123"
        assert pass_arg is visitor_pass
        assert text == "yeah it is ab12 cde"
        return {"action": "plate_detected", "registration_number": "AB12CDE"}

    async def lookup_vehicle(plate):
        assert plate == "AB12CDE"
        return whatsapp_helpers.VisitorVehicleLookup(make="Tesla", colour="Silver")

    async def store_pending(pass_id, sender, plate, nonce, **kwargs):
        captured["pending"] = (pass_id, sender, plate, nonce)
        captured["pending_details"] = kwargs

    async def send_confirmation(to, pass_arg, plate, nonce, **_kwargs):
        captured["confirmation"] = (to, pass_arg.id, plate, nonce)
        captured["confirmation_details"] = _kwargs

    monkeypatch.setattr(service._identities, "admin_for_phone", no_admin)
    monkeypatch.setattr(service._visitor._state, "visitor_pass_for_phone", visitor_for_phone)
    monkeypatch.setattr(service._visitor, "_visitor_concierge_result", visitor_result)
    monkeypatch.setattr(service._visitor, "_lookup_visitor_vehicle_details", lookup_vehicle)
    monkeypatch.setattr(service._visitor._state, "store_pending_visitor_plate", store_pending)
    monkeypatch.setattr(service._visitor, "send_visitor_plate_confirmation", send_confirmation)

    await route_bound_message(service,
        {"id": "wamid.visitor", "from": "+44 7700 900123", "type": "text", "text": {"body": "yeah it is ab12 cde"}},
        contacts=[],
        phone_number_id="123456789",
        config=enabled_config(),
        signature_verified=True,
    )

    assert captured["pending"][0] == visitor_pass.id
    assert captured["pending"][1] == "447700900123"
    assert captured["pending"][2] == "AB12CDE"
    assert captured["pending_details"]["vehicle_make"] == "Tesla"
    assert captured["pending_details"]["vehicle_colour"] == "Silver"
    assert captured["confirmation"][0] == "447700900123"
    assert captured["confirmation_details"]["vehicle_make"] == "Tesla"
    assert captured["confirmation_details"]["vehicle_colour"] == "Silver"


@pytest.mark.asyncio
async def test_muted_visitor_message_is_marked_read_without_typing(monkeypatch) -> None:
    service = make_whatsapp_router()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Sarah",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 1, 10, 0),
        valid_from=datetime(2026, 5, 1, 10, 0),
        valid_until=datetime(2026, 5, 1, 18, 0),
        status=VisitorPassStatus.ACTIVE,
        creation_source="ui",
        source_metadata={"whatsapp_abuse_muted_until": "2026-05-01T11:00:00+00:00"},
    )
    captured: dict[str, Any] = {"reads": []}

    async def no_admin(_sender):
        return None

    async def visitor_for_phone(_sender):
        return visitor_pass, "active"

    async def muted(*_args, **_kwargs):
        return True

    async def mark_read(message_id, *, config, show_typing):
        captured["reads"].append((message_id, show_typing, config.phone_number_id))

    async def record_inbound(pass_arg, message, *, sender):
        captured["recorded"] = (pass_arg.id, message["id"], sender)

    async def handle_visitor(*_args, **_kwargs):
        raise AssertionError("Muted visitors should not enter Concierge processing.")

    monkeypatch.setattr(service._identities, "admin_for_phone", no_admin)
    monkeypatch.setattr(service._visitor._state, "visitor_pass_for_phone", visitor_for_phone)
    monkeypatch.setattr(service._visitor._state, "visitor_reply_is_muted", muted)
    monkeypatch.setattr(service._delivery, "mark_incoming_message_read", mark_read)
    monkeypatch.setattr(service._visitor, "_record_inbound_visitor_message", record_inbound)
    monkeypatch.setattr(service._visitor, "_handle_visitor_message", handle_visitor)

    await route_bound_message(service,
        {"id": "wamid.muted", "from": "+44 7700 900123", "type": "text", "text": {"body": "hello?"}},
        contacts=[],
        phone_number_id="123456789",
        config=enabled_config(),
        signature_verified=True,
    )

    assert captured["reads"] == [("wamid.muted", False, "123456789")]
    assert captured["recorded"] == (visitor_pass.id, "wamid.muted", "447700900123")


@pytest.mark.asyncio
async def test_visitor_plate_is_rejected_when_vehicle_lookup_fails(monkeypatch) -> None:
    service = get_whatsapp_visitor_conversation_service()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Josh",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 1, 10, 0),
        valid_from=datetime(2026, 5, 1, 10, 0),
        valid_until=datetime(2026, 5, 1, 18, 0),
        status=VisitorPassStatus.ACTIVE,
        creation_source="ui",
    )
    captured = {}
    sent = []

    async def not_muted(*_args, **_kwargs):
        return False

    async def visitor_result(*_args, **_kwargs):
        return {"action": "plate_detected", "registration_number": "B00B1ES"}

    async def lookup_vehicle(plate):
        assert plate == "B00B1ES"
        return whatsapp_helpers.VisitorVehicleLookup(error="Vehicle not found")

    async def plate_change(*_args, **_kwargs):
        return False

    async def unverified(pass_id, sender, plate, error):
        captured["unverified"] = (pass_id, sender, plate, error)

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    async def store_pending(*_args, **_kwargs):
        raise AssertionError("Unverified registrations must not become pending plates.")

    async def send_confirmation(*_args, **_kwargs):
        raise AssertionError("Unverified registrations must not be confirmed.")

    monkeypatch.setattr(service._state, "visitor_reply_is_muted", not_muted)
    monkeypatch.setattr(service, "_visitor_concierge_result", visitor_result)
    monkeypatch.setattr(service, "_lookup_visitor_vehicle_details", lookup_vehicle)
    monkeypatch.setattr(service._state, "record_visitor_plate_change_attempt", plate_change)
    monkeypatch.setattr(service._state, "record_unverified_visitor_plate", unverified)
    monkeypatch.setattr(service._state, "store_pending_visitor_plate", store_pending)
    monkeypatch.setattr(service, "send_visitor_plate_confirmation", send_confirmation)
    monkeypatch.setattr(service._delivery, "send_text_message", send_text)

    await service._process_visitor_text(
        "447700900123",
        visitor_pass,
        "it's B00B1ES",
        config=enabled_config(),
    )

    assert captured["unverified"] == (visitor_pass.id, "447700900123", "B00B1ES", "Vehicle not found")
    assert sent == [("447700900123", "I couldn't find a vehicle for B00B1ES. Please check the registration and send it again.")]


@pytest.mark.asyncio
async def test_visitor_known_registration_is_rejected_with_llm_reply(monkeypatch) -> None:
    service = get_whatsapp_visitor_conversation_service()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Josh",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 1, 10, 0),
        valid_from=datetime(2026, 5, 1, 10, 0),
        valid_until=datetime(2026, 5, 1, 18, 0),
        status=VisitorPassStatus.ACTIVE,
        creation_source="ui",
    )
    captured = {}
    sent = []

    async def not_muted(*_args, **_kwargs):
        return False

    async def visitor_result(*_args, **_kwargs):
        return {"action": "plate_detected", "registration_number": "C25UNY"}

    async def privileged(plate):
        assert plate == "C25UNY"
        return True

    async def record_privileged(pass_id, sender, plate):
        captured["privileged"] = (pass_id, sender, plate)

    async def privileged_reply(pass_arg, plate, text):
        captured["reply_context"] = (pass_arg, plate, text)
        return "I can't use C25 UNY because it is already linked to privileged access. Please send the visitor vehicle registration instead."

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    async def lookup_vehicle(*_args, **_kwargs):
        raise AssertionError("Known privileged registrations must be rejected before vehicle lookup.")

    async def store_pending(*_args, **_kwargs):
        raise AssertionError("Known privileged registrations must not become pending plates.")

    monkeypatch.setattr(service._state, "visitor_reply_is_muted", not_muted)
    monkeypatch.setattr(service, "_visitor_concierge_result", visitor_result)
    monkeypatch.setattr(service._state, "plate_is_known_vehicle", privileged)
    monkeypatch.setattr(service._state, "record_privileged_visitor_plate", record_privileged)
    monkeypatch.setattr(service, "_visitor_privileged_plate_reply", privileged_reply)
    monkeypatch.setattr(service, "_lookup_visitor_vehicle_details", lookup_vehicle)
    monkeypatch.setattr(service._state, "store_pending_visitor_plate", store_pending)
    monkeypatch.setattr(service._delivery, "send_text_message", send_text)

    await service._process_visitor_text(
        "447700900123",
        visitor_pass,
        "Use C25 UNY",
        config=enabled_config(),
    )

    assert captured["privileged"] == (visitor_pass.id, "447700900123", "C25UNY")
    assert captured["reply_context"] == (visitor_pass.visitor_name, "C25UNY", "Use C25 UNY")
    assert sent == [
        (
            "447700900123",
            "I can't use C25 UNY because it is already linked to privileged access. Please send the visitor vehicle registration instead.",
        )
    ]


@pytest.mark.asyncio
async def test_privileged_registration_reply_is_llm_generated(monkeypatch) -> None:
    service = get_whatsapp_visitor_conversation_service()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Josh",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 1, 10, 0),
        valid_from=datetime(2026, 5, 1, 10, 0),
        valid_until=datetime(2026, 5, 1, 18, 0),
        status=VisitorPassStatus.ACTIVE,
        creation_source="ui",
    )
    captured = {}

    async def runtime():
        return SimpleNamespace(llm_provider="openai", site_timezone="Europe/London")

    class Provider:
        async def complete(self, messages, **_kwargs):
            captured["messages"] = messages
            return LlmResult(
                '{"message":"I can\'t use C25 UNY for this Visitor Pass because it is already linked to privileged access. Please send the visitor vehicle registration instead."}'
            )

    patch_whatsapp_boundary(monkeypatch, "get_runtime_config", runtime)
    patch_whatsapp_boundary(monkeypatch, "get_llm_provider", lambda _provider_name: Provider())

    body = await service._visitor_privileged_plate_reply(visitor_pass.visitor_name, "C25UNY", "Use C25 UNY")

    assert "privileged access" in body
    assert "visitor vehicle registration" in body
    prompt = captured["messages"][0].content
    assert "already linked to privileged access" in prompt
    assert "cannot be used for this Visitor Pass" in prompt


@pytest.mark.asyncio
async def test_repeated_plate_changes_trigger_llm_mute(monkeypatch) -> None:
    service = get_whatsapp_visitor_conversation_service()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Josh",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 1, 10, 0),
        valid_from=datetime(2026, 5, 1, 10, 0),
        valid_until=datetime(2026, 5, 1, 18, 0),
        number_plate="C25UNY",
        status=VisitorPassStatus.ACTIVE,
        creation_source="ui",
    )
    captured = {}

    async def not_muted(*_args, **_kwargs):
        return False

    async def visitor_result(*_args, **_kwargs):
        return {"action": "plate_detected", "registration_number": "AB12CDE"}

    async def lookup_vehicle(_plate):
        return whatsapp_helpers.VisitorVehicleLookup(found=True, make="Tesla", colour="Silver")

    async def plate_change(*_args, **_kwargs):
        return True

    async def abuse(sender, pass_arg, text, **kwargs):
        captured["abuse"] = (sender, pass_arg.id, text, kwargs)

    async def store_pending(*_args, **_kwargs):
        raise AssertionError("Abusive plate changes should not be stored.")

    async def send_confirmation(*_args, **_kwargs):
        raise AssertionError("Abusive plate changes should not be confirmed.")

    monkeypatch.setattr(service._state, "visitor_reply_is_muted", not_muted)
    monkeypatch.setattr(service, "_visitor_concierge_result", visitor_result)
    monkeypatch.setattr(service, "_lookup_visitor_vehicle_details", lookup_vehicle)
    monkeypatch.setattr(service._state, "record_visitor_plate_change_attempt", plate_change)
    monkeypatch.setattr(service, "_trigger_visitor_abuse_mute", abuse)
    monkeypatch.setattr(service._state, "store_pending_visitor_plate", store_pending)
    monkeypatch.setattr(service, "send_visitor_plate_confirmation", send_confirmation)

    await service._process_visitor_text(
        "447700900123",
        visitor_pass,
        "changed again AB12 CDE",
        config=enabled_config(),
    )

    assert captured["abuse"][0] == "447700900123"
    assert captured["abuse"][1] == visitor_pass.id
    assert captured["abuse"][3]["reason"] == "plate_changes"


@pytest.mark.asyncio
async def test_repeated_post_complete_replies_trigger_llm_mute(monkeypatch) -> None:
    service = get_whatsapp_visitor_conversation_service()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Josh",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 1, 10, 0),
        valid_from=datetime(2026, 5, 1, 10, 0),
        valid_until=datetime(2026, 5, 1, 18, 0),
        number_plate="C25UNY",
        status=VisitorPassStatus.ACTIVE,
        creation_source="ui",
    )
    captured = {}

    async def not_muted(*_args, **_kwargs):
        return False

    async def visitor_result(*_args, **_kwargs):
        return {"action": "reply", "message": "You're all set."}

    async def chatter(*_args, **_kwargs):
        return True

    async def abuse(sender, pass_arg, text, **kwargs):
        captured["abuse"] = (sender, pass_arg.id, text, kwargs)

    async def send_text(*_args, **_kwargs):
        raise AssertionError("Abuse mute response should be sent through the abuse path.")

    monkeypatch.setattr(service._state, "visitor_reply_is_muted", not_muted)
    monkeypatch.setattr(service, "_visitor_concierge_result", visitor_result)
    monkeypatch.setattr(service._state, "record_visitor_post_complete_reply", chatter)
    monkeypatch.setattr(service, "_trigger_visitor_abuse_mute", abuse)
    monkeypatch.setattr(service._delivery, "send_text_message", send_text)

    await service._process_visitor_text(
        "447700900123",
        visitor_pass,
        "and another thing",
        config=enabled_config(),
    )

    assert captured["abuse"][0] == "447700900123"
    assert captured["abuse"][1] == visitor_pass.id
    assert captured["abuse"][3]["reason"] == "post_complete_replies"


@pytest.mark.asyncio
async def test_abuse_stop_reply_is_llm_generated_and_mentions_pause(monkeypatch) -> None:
    service = get_whatsapp_visitor_conversation_service()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Josh",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 1, 10, 0),
        valid_from=datetime(2026, 5, 1, 10, 0),
        valid_until=datetime(2026, 5, 1, 18, 0),
        number_plate="C25UNY",
        status=VisitorPassStatus.ACTIVE,
        creation_source="ui",
    )
    captured = {}

    async def runtime():
        return SimpleNamespace(llm_provider="openai", site_timezone="Europe/London")

    class Provider:
        async def complete(self, messages, **_kwargs):
            captured["messages"] = messages
            return LlmResult('{"message":"All sorted, so I am pausing replies for 30 minutes before this chat earns a timesheet."}')

    patch_whatsapp_boundary(monkeypatch, "get_runtime_config", runtime)
    patch_whatsapp_boundary(monkeypatch, "get_llm_provider", lambda _provider_name: Provider())

    body = await service._visitor_abuse_stop_reply(visitor_pass, "hello again", reason="post_complete_replies")

    assert "30 minutes" in body
    assert "Alfred" not in body
    prompt = captured["messages"][0].content
    assert "funny but firm" in prompt
    assert "pause for 30 minutes" in prompt


@pytest.mark.asyncio
async def test_terminal_visitor_pass_reply_is_sent_once(monkeypatch) -> None:
    service = get_whatsapp_visitor_conversation_service()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Josh",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 1, 10, 0),
        valid_from=datetime(2026, 5, 1, 10, 0),
        valid_until=datetime(2026, 5, 1, 18, 0),
        number_plate="C25UNY",
        status=VisitorPassStatus.CANCELLED,
        creation_source="ui",
        source_metadata={},
    )
    visitor_pass.created_at = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)
    visitor_pass.updated_at = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)
    sent = []
    published = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, key):
            assert model is VisitorPass
            assert key == visitor_pass.id
            return visitor_pass

        async def scalar(self, statement):
            assert statement._for_update_arg is not None
            return visitor_pass

        async def commit(self):
            return None

        async def refresh(self, _row):
            return None

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    async def publish(event, payload):
        published.append((event, payload))

    patch_whatsapp_boundary(monkeypatch, "AsyncSessionLocal", lambda: Session())
    monkeypatch.setattr(event_bus, "publish", publish)
    monkeypatch.setattr(service._delivery, "send_text_message", send_text)

    first = await service._send_terminal_visitor_pass_reply_once(visitor_pass, "447700900123", config=enabled_config())
    second = await service._send_terminal_visitor_pass_reply_once(visitor_pass, "447700900123", config=enabled_config())

    assert first is True
    assert second is False
    assert len(sent) == 1
    assert "cancelled" in sent[0][1]
    assert visitor_pass.source_metadata["whatsapp_terminal_notice_reserved_at"]
    assert published[0][0] == "visitor_pass.updated"


@pytest.mark.asyncio
async def test_claimed_visitor_text_preserves_emoji_preference(monkeypatch) -> None:
    service = get_whatsapp_visitor_conversation_service()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Josh",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 1, 10, 0),
        valid_from=datetime(2026, 5, 1, 10, 0),
        valid_until=datetime(2026, 5, 1, 18, 0),
        status=VisitorPassStatus.ACTIVE,
        creation_source="ui",
    )
    captured = {}

    async def visitor_result(sender, pass_arg, text, **_kwargs):
        captured["text"] = text
        captured["alfred_mentioned"] = _kwargs.get("alfred_mentioned")
        assert sender == "447700900123"
        assert pass_arg is visitor_pass
        return {"action": "plate_detected", "registration_number": "AB12CDE"}

    async def lookup_vehicle(plate):
        assert plate == "AB12CDE"
        return whatsapp_helpers.VisitorVehicleLookup(make="Tesla", colour="Silver")

    async def store_pending(*_args, **_kwargs):
        return None

    async def send_confirmation(to, pass_arg, plate, nonce, **kwargs):
        captured["confirmation"] = (to, pass_arg.id, plate)
        captured["confirmation_kwargs"] = kwargs

    async def alfred_nod(pass_arg, text):
        assert pass_arg is visitor_pass
        assert "Alfred" in text
        return "Alfred says Jason has achieved peak driveway nerd."

    monkeypatch.setattr(service, "_visitor_concierge_result", visitor_result)
    monkeypatch.setattr(service, "_lookup_visitor_vehicle_details", lookup_vehicle)
    monkeypatch.setattr(service._state, "store_pending_visitor_plate", store_pending)
    monkeypatch.setattr(service, "_visitor_alfred_name_nod", alfred_nod)
    monkeypatch.setattr(service, "send_visitor_plate_confirmation", send_confirmation)

    await service._process_visitor_text(
        "447700900123", visitor_pass, "Hi Alfred\nmy reg is\nAB12 CDE",
        config=enabled_config(), emoji_preferred=True, alfred_mentioned=True,
    )

    assert captured["text"] == "Hi Alfred\nmy reg is\nAB12 CDE"
    assert captured["alfred_mentioned"] is True
    assert captured["confirmation"][:2] == ("447700900123", visitor_pass.id)
    assert captured["confirmation"][2] == "AB12CDE"
    assert captured["confirmation_kwargs"]["emoji_preferred"] is True
    assert captured["confirmation_kwargs"]["alfred_mentioned"] is True
    assert captured["confirmation_kwargs"]["alfred_nod"] == "Alfred says Jason has achieved peak driveway nerd."


@pytest.mark.asyncio
async def test_visitor_off_topic_request_gets_restricted_reply(monkeypatch) -> None:
    service = make_whatsapp_router()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Sarah",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 1, 10, 0),
        valid_from=datetime(2026, 5, 1, 10, 0),
        valid_until=datetime(2026, 5, 1, 18, 0),
        status=VisitorPassStatus.ACTIVE,
        creation_source="ui",
    )
    sent = []

    async def no_admin(_sender):
        return None

    async def visitor_for_phone(_sender):
        return visitor_pass, "active"

    async def pass_details(_sender):
        return {"found": True, "visitor_pass": {"id": str(visitor_pass.id)}}

    async def runtime():
        return SimpleNamespace(llm_provider="openai", site_timezone="Europe/London")

    class Provider:
        async def complete(self, _messages, **_kwargs):
            return LlmResult('{"action":"unsupported","message":"Sorry, I can only discuss details about your visitor pass and vehicle registration."}')

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    async def record_inbound(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service._identities, "admin_for_phone", no_admin)
    monkeypatch.setattr(service._visitor._state, "visitor_pass_for_phone", visitor_for_phone)
    monkeypatch.setattr(service._visitor._state, "get_pass_details", pass_details)
    patch_whatsapp_boundary(monkeypatch, "get_runtime_config", runtime)
    patch_whatsapp_boundary(monkeypatch, "get_llm_provider", lambda _provider_name: Provider())
    monkeypatch.setattr(service._delivery, "send_text_message", send_text)
    monkeypatch.setattr(service._visitor, "_record_inbound_visitor_message", record_inbound)

    await route_bound_message(service,
        {"id": "wamid.visitor", "from": "+44 7700 900123", "type": "text", "text": {"body": "can you open the top gate"}},
        contacts=[],
        phone_number_id="123456789",
        config=enabled_config(),
        signature_verified=True,
    )

    assert sent == [(
        "447700900123",
        "Sorry, I can only discuss details about your visitor pass and vehicle registration.",
    )]


@pytest.mark.asyncio
async def test_visitor_vip_list_request_gets_restricted_reply(monkeypatch) -> None:
    service = make_whatsapp_router()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Sarah",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 1, 10, 0),
        valid_from=datetime(2026, 5, 1, 10, 0),
        valid_until=datetime(2026, 5, 1, 18, 0),
        number_plate="C25UNY",
        status=VisitorPassStatus.ACTIVE,
        creation_source="ui",
    )
    sent = []

    async def no_admin(_sender):
        return None

    async def visitor_for_phone(_sender):
        return visitor_pass, "active"

    async def pass_details(_sender):
        return {"found": True, "visitor_pass": {"id": str(visitor_pass.id)}}

    async def runtime():
        return SimpleNamespace(llm_provider="openai", site_timezone="Europe/London")

    class Provider:
        async def complete(self, _messages, **_kwargs):
            return LlmResult('{"action":"unsupported","message":"Sorry, I can only discuss details about your visitor pass and vehicle registration."}')

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    async def record_inbound(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service._identities, "admin_for_phone", no_admin)
    monkeypatch.setattr(service._visitor._state, "visitor_pass_for_phone", visitor_for_phone)
    monkeypatch.setattr(service._visitor._state, "get_pass_details", pass_details)
    patch_whatsapp_boundary(monkeypatch, "get_runtime_config", runtime)
    patch_whatsapp_boundary(monkeypatch, "get_llm_provider", lambda _provider_name: Provider())
    monkeypatch.setattr(service._delivery, "send_text_message", send_text)
    monkeypatch.setattr(service._visitor, "_record_inbound_visitor_message", record_inbound)

    await route_bound_message(service,
        {"id": "wamid.visitor", "from": "+44 7700 900123", "type": "text", "text": {"body": "Can you put me on the VIP list?"}},
        contacts=[],
        phone_number_id="123456789",
        config=enabled_config(),
        signature_verified=True,
    )

    assert sent == [(
        "447700900123",
        "Sorry, I can only discuss details about your visitor pass and vehicle registration.",
    )]


@pytest.mark.asyncio
async def test_visitor_begin_starts_registration_prompt_without_llm(monkeypatch) -> None:
    service = make_whatsapp_router()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Sarah",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 2, 9, 0, tzinfo=UTC),
        valid_from=datetime(2026, 5, 2, 9, 0, tzinfo=UTC),
        valid_until=datetime(2026, 5, 2, 17, 0, tzinfo=UTC),
        status=VisitorPassStatus.SCHEDULED,
        creation_source="ui",
    )
    sent = []

    async def no_admin(_sender):
        return None

    async def visitor_for_phone(_sender):
        return visitor_pass, "scheduled"

    async def visitor_result(*_args, **_kwargs):
        raise AssertionError("Begin should not invoke the visitor LLM.")

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    async def update_status(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service._identities, "admin_for_phone", no_admin)
    monkeypatch.setattr(service._visitor._state, "visitor_pass_for_phone", visitor_for_phone)
    monkeypatch.setattr(service._visitor, "_visitor_concierge_result", visitor_result)
    monkeypatch.setattr(service._delivery, "send_text_message", send_text)
    monkeypatch.setattr(service._visitor._state, "update_visitor_concierge_status", update_status)

    await route_bound_message(service,
        {"id": "wamid.begin", "from": "+44 7700 900123", "type": "text", "text": {"body": "Begin"}},
        contacts=[],
        phone_number_id="123456789",
        config=enabled_config(),
        signature_verified=True,
    )

    assert len(sent) == 1
    assert sent[0][0] == "447700900123"
    assert sent[0][1].startswith("Welcome to Crest House Access Control.")
    assert "access between 02 May 2026, 10:00 and 02 May 2026, 18:00" in sent[0][1]
    assert sent[0][1].endswith("vehicle registration, which will be read upon arrival to open the gate.")


@pytest.mark.asyncio
async def test_visitor_begin_template_button_starts_registration_prompt(monkeypatch) -> None:
    service = make_whatsapp_router()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Sarah",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 2, 9, 0, tzinfo=UTC),
        valid_from=datetime(2026, 5, 2, 9, 0, tzinfo=UTC),
        valid_until=datetime(2026, 5, 2, 17, 0, tzinfo=UTC),
        status=VisitorPassStatus.SCHEDULED,
        creation_source="ui",
    )
    sent = []

    async def no_admin(_sender):
        return None

    async def visitor_for_phone(_sender):
        return visitor_pass, "scheduled"

    async def visitor_result(*_args, **_kwargs):
        raise AssertionError("Begin button should not invoke the visitor LLM.")

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    async def update_status(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service._identities, "admin_for_phone", no_admin)
    monkeypatch.setattr(service._visitor._state, "visitor_pass_for_phone", visitor_for_phone)
    monkeypatch.setattr(service._visitor, "_visitor_concierge_result", visitor_result)
    monkeypatch.setattr(service._delivery, "send_text_message", send_text)
    monkeypatch.setattr(service._visitor._state, "update_visitor_concierge_status", update_status)

    await route_bound_message(service,
        {
            "id": "wamid.begin",
            "from": "+44 7700 900123",
            "type": "button",
            "button": {"text": "Begin", "payload": "Begin"},
        },
        contacts=[],
        phone_number_id="123456789",
        config=enabled_config(),
        signature_verified=True,
    )

    assert len(sent) == 1
    assert sent[0][0] == "447700900123"
    assert sent[0][1].startswith("Welcome to Crest House Access Control.")
    assert "access between 02 May 2026, 10:00 and 02 May 2026, 18:00" in sent[0][1]
    assert sent[0][1].endswith("vehicle registration, which will be read upon arrival to open the gate.")


def test_visitor_timeframe_button_payload_round_trips() -> None:
    parsed = parse_visitor_pass_timeframe_button_id("iacs:vp_time:allow:pass-1:req-1")
    assert parsed is not None

    assert parsed.decision == "allow"
    assert parsed.pass_id == "pass-1"
    assert parsed.request_id == "req-1"


def test_visitor_timeframe_confirmation_button_payload_round_trips() -> None:
    parsed = parse_visitor_pass_timeframe_confirmation_button_id("iacs:vp_time_user:confirm:pass-1:req-1")
    assert parsed is not None

    assert parsed.decision == "confirm"
    assert parsed.pass_id == "pass-1"
    assert parsed.request_id == "req-1"


@pytest.mark.asyncio
async def test_visitor_timeframe_change_uses_llm_for_exact_range(monkeypatch) -> None:
    service = get_whatsapp_visitor_conversation_service()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Sarah",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 2, 7, 0, tzinfo=UTC),
        valid_from=datetime(2026, 5, 2, 7, 0, tzinfo=UTC),
        valid_until=datetime(2026, 5, 2, 7, 30, tzinfo=UTC),
        status=VisitorPassStatus.SCHEDULED,
        creation_source="ui",
    )
    captured = {}

    async def runtime():
        return SimpleNamespace(llm_provider="openai", site_timezone="Europe/London")

    async def pass_details(_sender):
        return {"found": True, "visitor_pass": {"id": str(visitor_pass.id)}}

    class Provider:
        async def complete(self, messages, **_kwargs):
            captured["messages"] = messages
            return LlmResult(
                '{"action":"timeframe_change","valid_from":"2026-05-02T07:00:00",'
                '"valid_until":"2026-05-02T07:30:00","summary":"Visitor requested 07:00 to 07:30."}'
            )

    patch_whatsapp_boundary(monkeypatch, "get_runtime_config", runtime)
    monkeypatch.setattr(service._state, "get_pass_details", pass_details)
    patch_whatsapp_boundary(monkeypatch, "get_llm_provider", lambda _provider_name: Provider())

    result = await service._visitor_concierge_result(
        "447700900123",
        visitor_pass,
        "Can you change my time from 07:00 to 07:30?",
    )

    assert result["action"] == "timeframe_change"
    assert result["valid_from"] == "2026-05-02T06:00:00+00:00"
    assert result["valid_until"] == "2026-05-02T06:30:00+00:00"
    prompt_payload = json.loads(captured["messages"][1].content)
    assert prompt_payload["site_timezone"] == "Europe/London"
    assert prompt_payload["current_window"] == {
        "site_timezone": "Europe/London",
        "valid_from": "2026-05-02T08:00:00+01:00",
        "valid_until": "2026-05-02T08:30:00+01:00",
        "date": "2026-05-02",
    }


@pytest.mark.asyncio
async def test_visitor_concierge_prompt_includes_latest_dashboard_custom_message(monkeypatch) -> None:
    service = get_whatsapp_visitor_conversation_service()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Ash",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 4, 8, 0, tzinfo=UTC),
        valid_from=datetime(2026, 5, 4, 8, 0, tzinfo=UTC),
        valid_until=datetime(2026, 5, 4, 20, 30, tzinfo=UTC),
        status=VisitorPassStatus.SCHEDULED,
        creation_source="ui",
        source_metadata={
            "whatsapp_chat_history": [
                {
                    "id": "custom-1",
                    "direction": "outbound",
                    "kind": "text",
                    "body": "Do you want me to move your visitor pass to tomorrow?",
                    "actor_label": "IACS",
                    "provider_message_id": "wamid.custom",
                    "created_at": "2026-05-02T18:01:00+01:00",
                    "metadata": {"origin": "dashboard_custom", "sender_label": "Jason Ash"},
                },
                {
                    "id": "visitor-1",
                    "direction": "inbound",
                    "kind": "text",
                    "body": "Yes",
                    "actor_label": "Ash",
                    "created_at": "2026-05-02T18:02:00+01:00",
                    "metadata": {},
                },
            ]
        },
    )
    captured = {}

    async def runtime():
        return SimpleNamespace(llm_provider="openai", site_timezone="Europe/London")

    async def pass_details(_sender):
        return {"found": True, "visitor_pass": {"id": str(visitor_pass.id)}}

    class Provider:
        async def complete(self, messages, **_kwargs):
            captured["messages"] = messages
            return LlmResult('{"action":"reply","message":"All set."}')

    patch_whatsapp_boundary(monkeypatch, "get_runtime_config", runtime)
    monkeypatch.setattr(service._state, "get_pass_details", pass_details)
    patch_whatsapp_boundary(monkeypatch, "get_llm_provider", lambda _provider_name: Provider())

    result = await service._visitor_concierge_result("447700900123", visitor_pass, "Yes")

    assert result == {"action": "reply", "message": "All set."}
    prompt_payload = json.loads(captured["messages"][1].content)
    custom_message = prompt_payload["conversation_context"]["latest_dashboard_custom_message"]
    assert custom_message["body"] == "Do you want me to move your visitor pass to tomorrow?"
    assert custom_message["origin"] == "dashboard_custom"
    assert prompt_payload["conversation_context"]["recent_messages"][-1]["body"] == "Yes"


@pytest.mark.asyncio
async def test_visitor_timeframe_change_is_not_keyword_parsed_without_llm(monkeypatch) -> None:
    service = get_whatsapp_visitor_conversation_service()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Sarah",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 2, 7, 0, tzinfo=UTC),
        valid_from=datetime(2026, 5, 2, 7, 0, tzinfo=UTC),
        valid_until=datetime(2026, 5, 2, 7, 30, tzinfo=UTC),
        status=VisitorPassStatus.SCHEDULED,
        creation_source="ui",
    )

    async def runtime():
        return SimpleNamespace(llm_provider="local", site_timezone="Europe/London")

    async def pass_details(_sender):
        return {"found": True, "visitor_pass": {"id": str(visitor_pass.id)}}

    patch_whatsapp_boundary(monkeypatch, "get_runtime_config", runtime)
    monkeypatch.setattr(service._state, "get_pass_details", pass_details)

    result = await service._visitor_concierge_result(
        "447700900123",
        visitor_pass,
        "Can you change my time from 07:00 to 07:30?",
    )

    assert result == {
        "action": "reply",
        "message": "Sorry, I can't safely process visitor chat right now. Please contact your host.",
    }


@pytest.mark.asyncio
async def test_visitor_thanks_after_confirmed_plate_gets_warm_reply_not_reconfirmation(monkeypatch) -> None:
    service = get_whatsapp_visitor_conversation_service()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Josh",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 11, 8, 0, tzinfo=UTC),
        valid_from=datetime(2026, 5, 11, 8, 0, tzinfo=UTC),
        valid_until=datetime(2026, 5, 11, 16, 30, tzinfo=UTC),
        number_plate="C25UNY",
        status=VisitorPassStatus.SCHEDULED,
        creation_source="ui",
    )

    async def runtime():
        return SimpleNamespace(llm_provider="openai", site_timezone="Europe/London")

    async def pass_details(_sender):
        return {"found": True, "visitor_pass": {"id": str(visitor_pass.id), "number_plate": "C25UNY"}}

    class Provider:
        async def complete(self, _messages, **_kwargs):
            return LlmResult('{"action":"plate_detected","registration_number":"C25UNY"}')

    patch_whatsapp_boundary(monkeypatch, "get_runtime_config", runtime)
    monkeypatch.setattr(service._state, "get_pass_details", pass_details)
    patch_whatsapp_boundary(monkeypatch, "get_llm_provider", lambda _provider_name: Provider())

    result = await service._visitor_concierge_result(
        "447700900123",
        visitor_pass,
        "Alfred you LEGEND",
    )

    assert result == {"action": "reply", "message": "Haha, thanks Josh! You're all set."}


@pytest.mark.asyncio
async def test_visitor_random_text_after_confirmed_plate_is_not_treated_as_new_plate_with_llm(monkeypatch) -> None:
    service = get_whatsapp_visitor_conversation_service()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Josh",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 11, 8, 0, tzinfo=UTC),
        valid_from=datetime(2026, 5, 11, 8, 0, tzinfo=UTC),
        valid_until=datetime(2026, 5, 11, 16, 30, tzinfo=UTC),
        number_plate="C25UNY",
        status=VisitorPassStatus.SCHEDULED,
        creation_source="ui",
    )

    async def runtime():
        return SimpleNamespace(llm_provider="openai", site_timezone="Europe/London")

    async def pass_details(_sender):
        return {"found": True, "visitor_pass": {"id": str(visitor_pass.id), "number_plate": "C25UNY"}}

    class Provider:
        async def complete(self, _messages, **_kwargs):
            return LlmResult('{"action":"plate_detected","registration_number":"AB12CDE"}')

    patch_whatsapp_boundary(monkeypatch, "get_runtime_config", runtime)
    monkeypatch.setattr(service._state, "get_pass_details", pass_details)
    patch_whatsapp_boundary(monkeypatch, "get_llm_provider", lambda _provider_name: Provider())

    result = await service._visitor_concierge_result(
        "447700900123",
        visitor_pass,
        "Random reference AB12 CDE, lol",
    )

    assert result == {"action": "reply", "message": "Haha, thanks Josh! You're all set."}


@pytest.mark.asyncio
async def test_visitor_random_text_after_confirmed_plate_fails_closed_without_llm(monkeypatch) -> None:
    service = get_whatsapp_visitor_conversation_service()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Josh",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 11, 8, 0, tzinfo=UTC),
        valid_from=datetime(2026, 5, 11, 8, 0, tzinfo=UTC),
        valid_until=datetime(2026, 5, 11, 16, 30, tzinfo=UTC),
        number_plate="C25UNY",
        status=VisitorPassStatus.SCHEDULED,
        creation_source="ui",
    )

    async def runtime():
        return SimpleNamespace(llm_provider="local", site_timezone="Europe/London")

    async def pass_details(_sender):
        return {"found": True, "visitor_pass": {"id": str(visitor_pass.id), "number_plate": "C25UNY"}}

    patch_whatsapp_boundary(monkeypatch, "get_runtime_config", runtime)
    monkeypatch.setattr(service._state, "get_pass_details", pass_details)

    result = await service._visitor_concierge_result(
        "447700900123",
        visitor_pass,
        "Random reference AB12 CDE, lol",
    )

    assert result == {
        "action": "reply",
        "message": "Sorry, I can't safely process visitor chat right now. Please contact your host.",
    }


@pytest.mark.asyncio
async def test_visitor_confirmed_pass_cannot_send_new_plate_without_llm(monkeypatch) -> None:
    service = get_whatsapp_visitor_conversation_service()
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Josh",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 11, 8, 0, tzinfo=UTC),
        valid_from=datetime(2026, 5, 11, 8, 0, tzinfo=UTC),
        valid_until=datetime(2026, 5, 11, 16, 30, tzinfo=UTC),
        number_plate="C25UNY",
        status=VisitorPassStatus.SCHEDULED,
        creation_source="ui",
    )

    async def runtime():
        return SimpleNamespace(llm_provider="local", site_timezone="Europe/London")

    async def pass_details(_sender):
        return {"found": True, "visitor_pass": {"id": str(visitor_pass.id), "number_plate": "C25UNY"}}

    patch_whatsapp_boundary(monkeypatch, "get_runtime_config", runtime)
    monkeypatch.setattr(service._state, "get_pass_details", pass_details)

    result = await service._visitor_concierge_result(
        "447700900123",
        visitor_pass,
        "Actually I brought AB12 CDE today",
    )

    assert result == {
        "action": "reply",
        "message": "Sorry, I can't safely process visitor chat right now. Please contact your host.",
    }


def test_visitor_timeframe_auto_limit_uses_original_window_for_cumulative_changes() -> None:
    metadata = {
        "whatsapp_timeframe_confirmation": {
            "status": "confirmed",
            "current_valid_from": "2026-05-02T08:00:00+00:00",
            "current_valid_until": "2026-05-02T08:30:00+00:00",
            "requested_valid_from": "2026-05-02T07:30:00+00:00",
            "requested_valid_until": "2026-05-02T08:00:00+00:00",
        }
    }
    current_start = datetime(2026, 5, 2, 7, 30, tzinfo=UTC)
    current_end = datetime(2026, 5, 2, 8, 0, tzinfo=UTC)

    original_start, original_end = whatsapp_helpers.visitor_timeframe_original_window(
        metadata,
        current_start,
        current_end,
    )

    assert original_start == datetime(2026, 5, 2, 8, 0, tzinfo=UTC)
    assert original_end == datetime(2026, 5, 2, 8, 30, tzinfo=UTC)
    assert whatsapp_helpers.timeframe_change_within_auto_limit(
        original_start,
        original_end,
        datetime(2026, 5, 2, 7, 0, tzinfo=UTC),
        datetime(2026, 5, 2, 7, 30, tzinfo=UTC),
    )
    assert not whatsapp_helpers.timeframe_change_within_auto_limit(
        original_start,
        original_end,
        datetime(2026, 5, 2, 6, 30, tzinfo=UTC),
        datetime(2026, 5, 2, 7, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_pending_timeframe_approval_blocks_new_time_request(monkeypatch) -> None:
    service = get_whatsapp_visitor_conversation_service()
    pass_id = uuid.uuid4()
    visitor_pass = VisitorPass(
        id=pass_id,
        visitor_name="Ash",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 4, 8, 0, tzinfo=UTC),
        valid_from=datetime(2026, 5, 4, 8, 0, tzinfo=UTC),
        valid_until=datetime(2026, 5, 4, 20, 30, tzinfo=UTC),
        number_plate="Y90AGS",
        status=VisitorPassStatus.SCHEDULED,
        creation_source="ui",
        source_metadata={
            "whatsapp_timeframe_request": {
                "id": "request-1",
                "status": "pending",
                "requested_valid_from": "2026-05-04T09:00:00+00:00",
                "requested_valid_until": "2026-05-04T21:30:00+00:00",
            }
        },
    )
    visitor_pass.created_at = visitor_pass.updated_at = datetime(2026, 5, 2, tzinfo=UTC)
    sent = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, key):
            assert model is VisitorPass
            assert key == pass_id
            return visitor_pass

        async def scalar(self, statement):
            assert statement._for_update_arg is not None
            return visitor_pass

        async def commit(self):
            return None

    class VisitorPassService:
        def status_for(self, row, now): return row.status

        async def refresh_statuses(self, **_kwargs):
            return []

        def window_start(self, pass_):
            return pass_.valid_from

        def window_end(self, pass_):
            return pass_.valid_until

        async def update_pass(self, *_args, **_kwargs):
            raise AssertionError("Pending approval should block new time updates.")

    async def pending_reply(pass_arg, text):
        assert pass_arg is visitor_pass
        assert "later" in text
        return "There is already a time change waiting for approval, so I can't take another one just yet."

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    patch_whatsapp_boundary(monkeypatch, "AsyncSessionLocal", lambda: Session())
    patch_whatsapp_boundary(monkeypatch, "get_visitor_pass_service", lambda: VisitorPassService())
    monkeypatch.setattr(service, "_visitor_pending_timeframe_reply", pending_reply)
    monkeypatch.setattr(service._delivery, "send_text_message", send_text)

    await service._handle_visitor_timeframe_change(
        "447700900123",
        visitor_pass,
        "Can I make it later?",
        {
            "valid_from": "2026-05-04T10:00:00+00:00",
            "valid_until": "2026-05-04T22:00:00+00:00",
            "summary": "Visitor asked for another change.",
        },
        config=enabled_config(),
    )

    assert sent == [
        (
            "447700900123",
            "There is already a time change waiting for approval, so I can't take another one just yet.",
        )
    ]


@pytest.mark.asyncio
async def test_llm_direct_apply_uses_canonical_consent_policy(monkeypatch) -> None:
    from app.services.visitor_conversations import VisitorConversationOutcome

    service = get_whatsapp_visitor_conversation_service()
    visitor_pass = VisitorPass(id=uuid.uuid4(), visitor_name="Synthetic Visitor")
    proposed = {"valid_from": "2026-05-05T08:00:00+00:00", "valid_until": "2026-05-05T20:30:00+00:00",
                "direct_apply": True, "source": "dashboard_custom_proposal"}
    calls = []
    async def request_timeframe(pass_id, sender, text, value):
        assert (pass_id, sender, text, value) == (visitor_pass.id, "447700900123", "Yes", proposed)
        calls.append("canonical_policy")
        return VisitorConversationOutcome("approval_required", {"id": str(pass_id)})
    async def send_text(to, body, **kwargs): calls.append((to, body))
    monkeypatch.setattr(service._state, "request_timeframe_change", request_timeframe)
    monkeypatch.setattr(service._delivery, "send_text_message", send_text)
    await service._handle_visitor_timeframe_change("447700900123", visitor_pass, "Yes", proposed, config=enabled_config())
    assert calls == ["canonical_policy", ("447700900123", whatsapp_helpers.VISITOR_TIMEFRAME_APPROVAL_REPLY)]
    # Real PostgreSQL tests in test_visitor_conversation_authority prove the
    # untrusted flag cannot alter the stored pass without consent/authority.



@pytest.mark.asyncio
async def test_whatsapp_test_endpoint_rejects_disabled_integration(monkeypatch) -> None:
    async def load_config(values):
        assert values == {"whatsapp_enabled": False}
        return await async_enabled_config(enabled=False)

    monkeypatch.setattr(whatsapp_api, "load_whatsapp_config", load_config)

    async def consume_confirmation(*_args, **_kwargs):
        return SimpleNamespace()


    with pytest.raises(HTTPException) as exc:
        await whatsapp_api.send_whatsapp_test(
            whatsapp_api.WhatsAppTestRequest(values={"whatsapp_enabled": False}, confirmation_token="confirmed"),
            SimpleNamespace(id=uuid.uuid4(), mobile_phone_number="+44 7700 900123"),
        )

    assert exc.value.status_code == 400
    assert "Enable WhatsApp" in exc.value.detail


@pytest.mark.asyncio
async def test_whatsapp_test_endpoint_binds_typed_ephemeral_modal_config(monkeypatch) -> None:
    captured = {}
    async def load_config(values):
        captured["values"] = values
        return enabled_config(phone_number_id=str(values["whatsapp_phone_number_id"]))
    async def send_confirmed(session, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(run_id="synthetic-run")
    monkeypatch.setattr(whatsapp_api, "load_whatsapp_config", load_config)
    monkeypatch.setattr(whatsapp_api, "send_confirmed_notification", send_confirmed)
    result = await whatsapp_api.send_whatsapp_test(
        whatsapp_api.WhatsAppTestRequest(message="Test", values={"whatsapp_enabled": True,
            "whatsapp_phone_number_id": "phone-id-from-form"}, confirmation_token="confirmed"),
        SimpleNamespace(id=uuid.uuid4(), mobile_phone_number="+44 7700 900123"))
    assert result == {"ok": True, "notification_run_id": "synthetic-run"}
    assert captured["direct_action"] == {"type": "whatsapp", "delivery_mode": "literal", "target": "+44 7700 900123", "title": "", "message": "Test"}
    assert captured["ephemeral_config"].phone_number_id == "phone-id-from-form"
    assert captured["payload"]["values"] == captured["values"]



@pytest.mark.asyncio
async def test_interactive_confirmation_buttons_bind_session_and_confirmation(monkeypatch) -> None:
    service = get_whatsapp_delivery_service()
    captured = {}

    async def send_buttons(to, body, buttons, **_kwargs):
        captured["to"] = to
        captured["body"] = body
        captured["buttons"] = buttons

    monkeypatch.setattr(service, "send_interactive_buttons", send_buttons)

    await service.send_confirmation_message(
        "447700900123",
        {
            "session_id": "session-1",
            "confirmation_id": "confirm-1",
            "title": "Open gate?",
            "description": "This needs confirmation.",
        },
    )

    assert captured["to"] == "447700900123"
    parsed = parse_confirmation_button_id(captured["buttons"][0]["id"])
    assert parsed is not None
    assert parsed.session_id == "session-1"
    assert parsed.confirmation_id == "confirm-1"
    assert parsed.decision == "confirm"


@pytest.mark.asyncio
async def test_notification_action_delivers_to_dynamic_whatsapp_target(monkeypatch) -> None:
    service = get_whatsapp_delivery_service()
    sent = []
    async def load_config(*_args, **_kwargs):
        return await async_enabled_config()

    patch_whatsapp_boundary(monkeypatch, "load_whatsapp_config", load_config)

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    monkeypatch.setattr(service, "send_text_message", send_text)

    await service.send_notification_action(
        {
            "target_mode": "selected",
            "target_ids": ["whatsapp:number:@AdminPhone"],
            "frozen_whatsapp_recipients": [{"kind": "number", "phone": "447700900123"}],
            "title": "Gate alert",
            "message": "Gate is open.",
        },
        NotificationContext("gate_malfunction", "Gate alert", "warning", {"malfunction_stage": "initial"}),
        variables={"AdminPhone": "+44 7700 900123"}, config=enabled_config(),
    )

    assert sent == [("447700900123", "Gate alert\n\nGate is open.")]


@pytest.mark.asyncio
async def test_timeframe_notification_uses_whatsapp_interactive_buttons(monkeypatch) -> None:
    service = get_whatsapp_delivery_service()
    sent = []

    async def load_config(*_args, **_kwargs):
        return await async_enabled_config()

    async def send_buttons(to, body, buttons, **_kwargs):
        sent.append((to, body, buttons))

    patch_whatsapp_boundary(monkeypatch, "load_whatsapp_config", load_config)
    monkeypatch.setattr(service, "send_interactive_buttons", send_buttons)

    await service.send_notification_action(
        {
            "target_mode": "selected",
            "target_ids": ["whatsapp:number:@AdminPhone"],
            "frozen_whatsapp_recipients": [{"kind": "number", "phone": "447700900123"}],
            "title": "Timeframe request",
            "message": "Sarah wants to stay later.",
        },
        NotificationContext(
            "visitor_pass_timeframe_change_requested",
            "Timeframe request",
            "warning",
            {
                "visitor_pass_id": "pass-1",
                "visitor_pass_timeframe_request_id": "request-1",
            },
        ),
        variables={"AdminPhone": "+44 7700 900123"}, config=enabled_config(),
    )

    assert sent[0][0] == "447700900123"
    assert sent[0][1] == "Timeframe request\n\nSarah wants to stay later."
    allow = parse_visitor_pass_timeframe_button_id(sent[0][2][0]["id"])
    deny = parse_visitor_pass_timeframe_button_id(sent[0][2][1]["id"])
    assert allow is not None
    assert deny is not None
    assert allow.decision == "allow"
    assert allow.pass_id == "pass-1"
    assert allow.request_id == "request-1"
    assert deny.decision == "deny"


@pytest.mark.asyncio
async def test_whatsapp_delivery_preserves_dynamic_recipient_and_rendered_body(monkeypatch) -> None:
    service = get_whatsapp_delivery_service()
    sent = []
    async def load_config(*_args, **_kwargs):
        return await async_enabled_config()

    patch_whatsapp_boundary(monkeypatch, "load_whatsapp_config", load_config)

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    monkeypatch.setattr(service, "send_text_message", send_text)
    context = SimpleNamespace(subject="Gate alert", variables={"AdminPhone": "+44 7700 900123", "Subject": "Gate alert"})

    await service.send_notification_action(
        {"target_mode": "selected", "target_ids": ["whatsapp:number:@AdminPhone"],
         "frozen_whatsapp_recipients": [{"kind": "number", "phone": "447700900123"}],
         "title": context.subject, "message": ""},
        NotificationContext("automation.whatsapp", context.subject, "info", {}),
        variables=context.variables, config=enabled_config(),
    )

    assert sent == [("447700900123", "Gate alert")]
