import hashlib
import hmac
import json
from datetime import UTC, datetime
from types import SimpleNamespace
import uuid

import pytest
from fastapi import BackgroundTasks, HTTPException
from starlette.requests import Request

from app.ai.providers import LlmResult
from app.api.v1 import webhooks, whatsapp as whatsapp_api
from app.models import VisitorPass
from app.models.enums import UserRole, VisitorPassStatus, VisitorPassType
from app.modules.notifications.base import NotificationContext
from app.services.settings import DEFAULT_DYNAMIC_SETTINGS, SECRET_KEYS
from app.services import whatsapp_messaging
from app.services.whatsapp_messaging import (
    WhatsAppIntegrationConfig,
    get_whatsapp_messaging_service,
    normalize_graph_api_version,
    normalize_whatsapp_phone_number,
    parse_confirmation_button_id,
    parse_visitor_pass_button_id,
    parse_visitor_pass_timeframe_button_id,
    parse_visitor_pass_timeframe_confirmation_button_id,
)


def enabled_config(**overrides):
    data = {
        "enabled": True,
        "access_token": "token",
        "phone_number_id": "123456789",
        "business_account_id": "987654321",
        "webhook_verify_token": "verify-token",
        "app_secret": "",
        "graph_api_version": "v25.0",
        "visitor_pass_template_name": "visitor_pass_registration_request",
        "visitor_pass_template_language": "en_GB",
    }
    data.update(overrides)
    return WhatsAppIntegrationConfig(**data)


async def async_enabled_config(**overrides):
    return enabled_config(**overrides)


def make_request(method: str, path: str, *, query: str = "", body: bytes = b"", headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": query.encode(),
            "headers": headers or [],
        },
        receive,
    )


@pytest.fixture(autouse=True)
def disable_whatsapp_read_receipts(monkeypatch):
    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(get_whatsapp_messaging_service(), "mark_incoming_message_read", noop)


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
    assert DEFAULT_DYNAMIC_SETTINGS["whatsapp_visitor_pass_template_name"][1] == "visitor_pass_registration_request"
    assert DEFAULT_DYNAMIC_SETTINGS["whatsapp_visitor_pass_template_language"][1] == "en_GB"
    assert {"whatsapp_access_token", "whatsapp_webhook_verify_token", "whatsapp_app_secret"}.issubset(SECRET_KEYS)


def test_phone_and_graph_version_normalization() -> None:
    assert normalize_whatsapp_phone_number("+44 (7700) 900-123") == "447700900123"
    assert normalize_graph_api_version("25.0") == "v25.0"
    assert normalize_graph_api_version("") == "v25.0"


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
async def test_webhook_post_accepts_valid_signature(monkeypatch) -> None:
    handled = {}
    async def load_config():
        return await async_enabled_config(app_secret="secret")

    monkeypatch.setattr(webhooks, "load_whatsapp_config", load_config)

    async def handle(payload, *, signature_verified, unsigned_allowed):
        handled.update(
            {
                "payload": payload,
                "signature_verified": signature_verified,
                "unsigned_allowed": unsigned_allowed,
            }
        )

    service = get_whatsapp_messaging_service()
    monkeypatch.setattr(service, "handle_webhook_payload", handle)
    body = json.dumps({"entry": []}).encode()
    signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    background = BackgroundTasks()

    result = await webhooks.receive_whatsapp_webhook(
        make_request(
            "POST",
            "/api/v1/webhooks/whatsapp",
            body=body,
            headers=[(b"x-hub-signature-256", f"sha256={signature}".encode())],
        ),
        background,
    )
    await background()

    assert result == {"status": "accepted"}
    assert handled["signature_verified"] is True
    assert handled["unsigned_allowed"] is False


@pytest.mark.asyncio
async def test_unknown_sender_is_dropped_before_messaging_bridge(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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

    monkeypatch.setattr(service, "_admin_for_phone", no_admin)
    monkeypatch.setattr(service, "_visitor_pass_for_phone", no_visitor)
    monkeypatch.setattr(service, "_audit_denied_sender", audit)
    monkeypatch.setattr(service, "send_text_message", send_text)

    await service._handle_incoming_message(
        {"id": "wamid.1", "from": "+44 7700 900123", "type": "text", "text": {"body": "status"}},
        contacts=[],
        phone_number_id="123456789",
        config=enabled_config(),
        signature_verified=False,
    )

    assert calls == {"denied": 1, "sent": 0}


@pytest.mark.asyncio
async def test_admin_sender_routes_text_to_messaging_bridge(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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
            return SimpleNamespace(response_text="Gate is closed.", pending_action=None)

    import app.services.messaging_bridge as messaging_bridge

    monkeypatch.setattr(service, "_admin_for_phone", admin_for_phone)
    monkeypatch.setattr(service, "_ensure_admin_identity", ensure_identity)
    monkeypatch.setattr(service, "send_text_message", send_text)
    monkeypatch.setattr(messaging_bridge, "messaging_bridge_service", Bridge())

    await service._handle_incoming_message(
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


@pytest.mark.asyncio
async def test_incoming_admin_message_is_marked_read_with_typing_indicator(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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
            return SimpleNamespace(response_text="Gate is closed.", pending_action=None)

    import app.services.messaging_bridge as messaging_bridge

    monkeypatch.setattr(service, "_admin_for_phone", admin_for_phone)
    monkeypatch.setattr(service, "_ensure_admin_identity", ensure_identity)
    monkeypatch.setattr(service, "mark_incoming_message_read", mark_read)
    monkeypatch.setattr(service, "send_text_message", send_text)
    monkeypatch.setattr(messaging_bridge, "messaging_bridge_service", Bridge())

    await service._handle_incoming_message(
        {"id": "wamid.ack", "from": "447700900123", "type": "text", "text": {"body": "gate status"}},
        contacts=[{"wa_id": "447700900123", "profile": {"name": "Jason"}}],
        phone_number_id="123456789",
        config=enabled_config(),
        signature_verified=True,
    )

    assert acknowledgements == [("wamid.ack", {"config": enabled_config(), "show_typing": True})]


@pytest.mark.asyncio
async def test_mark_read_payload_can_include_typing_indicator(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
    captured = {}

    async def post(config, payload):
        captured["config"] = config
        captured["payload"] = payload
        return {"success": True}

    monkeypatch.setattr(service, "_post_message", post)
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
    service = get_whatsapp_messaging_service()
    updates = []

    async def load_config(*_args, **_kwargs):
        return await async_enabled_config()

    async def update_delivery(phone_number, status, *, message_id=None):
        updates.append((phone_number, status, message_id))

    monkeypatch.setattr(whatsapp_messaging, "load_whatsapp_config", load_config)
    monkeypatch.setattr(service, "_update_visitor_delivery_status_for_phone", update_delivery)

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
async def test_text_send_payload_uses_meta_cloud_api_shape(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
    captured = {}

    async def post(config, payload):
        captured["config"] = config
        captured["payload"] = payload
        return {"messages": [{"id": "wamid.out"}]}

    monkeypatch.setattr(service, "_post_message", post)
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
async def test_template_send_payload_uses_configured_sender_id(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
    captured = {}
    config = enabled_config(phone_number_id="configured-sender-id")

    async def post(config_arg, payload):
        captured["config"] = config_arg
        captured["payload"] = payload
        return {"messages": [{"id": "wamid.template"}]}

    monkeypatch.setattr(service, "_post_message", post)

    await service.send_template_message(
        "+44 7700 900123",
        template_name="visitor_pass_registration_request",
        language_code="en_GB",
        body_parameters=["Sarah", "01 May 2026, 10:00 to 01 May 2026, 18:00"],
        config=config,
    )

    assert captured["config"].phone_number_id == "configured-sender-id"
    assert captured["payload"]["type"] == "template"
    assert captured["payload"]["to"] == "447700900123"
    assert captured["payload"]["template"] == {
        "name": "visitor_pass_registration_request",
        "language": {"code": "en_GB"},
        "components": [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": "Sarah"},
                    {"type": "text", "text": "01 May 2026, 10:00 to 01 May 2026, 18:00"},
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_visitor_plate_confirmation_buttons_use_namespaced_payload(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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

    monkeypatch.setattr(service, "send_interactive_buttons", send_buttons)

    await service.send_visitor_plate_confirmation("447700900123", visitor_pass, "AB12CDE", "nonce123")

    assert captured["to"] == "447700900123"
    assert "AB12 CDE" in captured["body"]
    parsed_confirm = parse_visitor_pass_button_id(captured["buttons"][0]["id"])
    parsed_change = parse_visitor_pass_button_id(captured["buttons"][1]["id"])
    assert parsed_confirm.decision == "confirm"
    assert parsed_confirm.pass_id == str(pass_id)
    assert parsed_confirm.nonce == "nonce123"
    assert parsed_change.decision == "change"


@pytest.mark.asyncio
async def test_visitor_sender_routes_to_sandbox_not_messaging_bridge(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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

    async def visitor_result(sender, pass_arg, text):
        assert sender == "447700900123"
        assert pass_arg is visitor_pass
        assert text == "yeah it is ab12 cde"
        return {"action": "plate_detected", "registration_number": "AB12CDE"}

    async def store_pending(pass_id, sender, plate, nonce):
        captured["pending"] = (pass_id, sender, plate, nonce)

    async def send_confirmation(to, pass_arg, plate, nonce, **_kwargs):
        captured["confirmation"] = (to, pass_arg.id, plate, nonce)

    monkeypatch.setattr(service, "_admin_for_phone", no_admin)
    monkeypatch.setattr(service, "_visitor_pass_for_phone", visitor_for_phone)
    monkeypatch.setattr(service, "_visitor_concierge_result", visitor_result)
    monkeypatch.setattr(service, "_store_pending_visitor_plate", store_pending)
    monkeypatch.setattr(service, "send_visitor_plate_confirmation", send_confirmation)

    await service._handle_incoming_message(
        {"id": "wamid.visitor", "from": "+44 7700 900123", "type": "text", "text": {"body": "yeah it is ab12 cde"}},
        contacts=[],
        phone_number_id="123456789",
        config=enabled_config(),
        signature_verified=True,
    )

    assert captured["pending"][0] == visitor_pass.id
    assert captured["pending"][1] == "447700900123"
    assert captured["pending"][2] == "AB12CDE"
    assert captured["confirmation"][0] == "447700900123"


@pytest.mark.asyncio
async def test_visitor_off_topic_request_gets_restricted_reply(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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
        return SimpleNamespace(llm_provider="local", site_timezone="Europe/London")

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    monkeypatch.setattr(service, "_admin_for_phone", no_admin)
    monkeypatch.setattr(service, "_visitor_pass_for_phone", visitor_for_phone)
    monkeypatch.setattr(service, "get_pass_details", pass_details)
    monkeypatch.setattr(whatsapp_messaging, "get_runtime_config", runtime)
    monkeypatch.setattr(service, "send_text_message", send_text)

    await service._handle_incoming_message(
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
async def test_visitor_begin_starts_registration_prompt_without_llm(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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

    monkeypatch.setattr(service, "_admin_for_phone", no_admin)
    monkeypatch.setattr(service, "_visitor_pass_for_phone", visitor_for_phone)
    monkeypatch.setattr(service, "_visitor_concierge_result", visitor_result)
    monkeypatch.setattr(service, "send_text_message", send_text)
    monkeypatch.setattr(service, "_update_visitor_concierge_status", update_status)

    await service._handle_incoming_message(
        {"id": "wamid.begin", "from": "+44 7700 900123", "type": "text", "text": {"body": "Begin"}},
        contacts=[],
        phone_number_id="123456789",
        config=enabled_config(),
        signature_verified=True,
    )

    assert len(sent) == 1
    assert sent[0][0] == "447700900123"
    assert sent[0][1].startswith("Hello Sarah. Your visitor pass is ready for")
    assert sent[0][1].endswith("Please reply with your vehicle registration.")


def test_visitor_timeframe_button_payload_round_trips() -> None:
    parsed = parse_visitor_pass_timeframe_button_id("iacs:vp_time:allow:pass-1:req-1")

    assert parsed.decision == "allow"
    assert parsed.pass_id == "pass-1"
    assert parsed.request_id == "req-1"


def test_visitor_timeframe_confirmation_button_payload_round_trips() -> None:
    parsed = parse_visitor_pass_timeframe_confirmation_button_id("iacs:vp_time_user:confirm:pass-1:req-1")

    assert parsed.decision == "confirm"
    assert parsed.pass_id == "pass-1"
    assert parsed.request_id == "req-1"


@pytest.mark.asyncio
async def test_visitor_timeframe_change_uses_llm_for_exact_range(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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

    monkeypatch.setattr(whatsapp_messaging, "get_runtime_config", runtime)
    monkeypatch.setattr(service, "get_pass_details", pass_details)
    monkeypatch.setattr(whatsapp_messaging, "get_llm_provider", lambda _provider_name: Provider())

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
async def test_visitor_timeframe_change_is_not_keyword_parsed_without_llm(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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

    monkeypatch.setattr(whatsapp_messaging, "get_runtime_config", runtime)
    monkeypatch.setattr(service, "get_pass_details", pass_details)

    result = await service._visitor_concierge_result(
        "447700900123",
        visitor_pass,
        "Can you change my time from 07:00 to 07:30?",
    )

    assert result == {
        "action": "reply",
        "message": "Sorry, I can't safely process time changes right now. Please contact your host.",
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

    original_start, original_end = whatsapp_messaging.visitor_timeframe_original_window(
        metadata,
        current_start,
        current_end,
    )

    assert original_start == datetime(2026, 5, 2, 8, 0, tzinfo=UTC)
    assert original_end == datetime(2026, 5, 2, 8, 30, tzinfo=UTC)
    assert whatsapp_messaging.timeframe_change_within_auto_limit(
        original_start,
        original_end,
        datetime(2026, 5, 2, 7, 0, tzinfo=UTC),
        datetime(2026, 5, 2, 7, 30, tzinfo=UTC),
    )
    assert not whatsapp_messaging.timeframe_change_within_auto_limit(
        original_start,
        original_end,
        datetime(2026, 5, 2, 6, 30, tzinfo=UTC),
        datetime(2026, 5, 2, 7, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_whatsapp_test_endpoint_rejects_disabled_integration(monkeypatch) -> None:
    async def load_config(values):
        assert values == {"whatsapp_enabled": False}
        return await async_enabled_config(enabled=False)

    monkeypatch.setattr(whatsapp_api, "load_whatsapp_config", load_config)

    with pytest.raises(HTTPException) as exc:
        await whatsapp_api.send_whatsapp_test(
            whatsapp_api.WhatsAppTestRequest(values={"whatsapp_enabled": False}),
            SimpleNamespace(id=uuid.uuid4(), mobile_phone_number="+44 7700 900123"),
        )

    assert exc.value.status_code == 400
    assert "Enable WhatsApp" in exc.value.detail


@pytest.mark.asyncio
async def test_whatsapp_test_endpoint_uses_modal_values(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
    captured = {}

    async def load_config(values):
        captured["values"] = values
        return await async_enabled_config(phone_number_id=str(values["whatsapp_phone_number_id"]))

    async def send_text(to, body, *, config=None):
        captured["to"] = to
        captured["body"] = body
        captured["config"] = config

    monkeypatch.setattr(whatsapp_api, "load_whatsapp_config", load_config)
    monkeypatch.setattr(whatsapp_api, "emit_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(service, "send_text_message", send_text)

    result = await whatsapp_api.send_whatsapp_test(
        whatsapp_api.WhatsAppTestRequest(
            message="Test",
            values={
                "whatsapp_enabled": True,
                "whatsapp_phone_number_id": "phone-id-from-form",
            },
        ),
        SimpleNamespace(id=uuid.uuid4(), mobile_phone_number="+44 7700 900123"),
    )

    assert result == {"ok": True}
    assert captured["values"]["whatsapp_enabled"] is True
    assert captured["to"] == "+44 7700 900123"
    assert captured["body"] == "Test"
    assert captured["config"].phone_number_id == "phone-id-from-form"


@pytest.mark.asyncio
async def test_interactive_confirmation_buttons_bind_session_and_confirmation(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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
    assert parsed.session_id == "session-1"
    assert parsed.confirmation_id == "confirm-1"
    assert parsed.decision == "confirm"


@pytest.mark.asyncio
async def test_notification_action_delivers_to_dynamic_whatsapp_target(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
    sent = []
    async def load_config(*_args, **_kwargs):
        return await async_enabled_config()

    monkeypatch.setattr(whatsapp_messaging, "load_whatsapp_config", load_config)

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    monkeypatch.setattr(service, "send_text_message", send_text)

    await service.send_notification_action(
        {
            "target_mode": "selected",
            "target_ids": ["whatsapp:number:@AdminPhone"],
            "title": "Gate alert",
            "message": "Gate is open.",
        },
        NotificationContext("gate_malfunction_initial", "Gate alert", "warning", {}),
        variables={"AdminPhone": "+44 7700 900123"},
    )

    assert sent == [("447700900123", "Gate alert\n\nGate is open.")]


@pytest.mark.asyncio
async def test_timeframe_notification_uses_whatsapp_interactive_buttons(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
    sent = []

    async def load_config(*_args, **_kwargs):
        return await async_enabled_config()

    async def send_buttons(to, body, buttons, **_kwargs):
        sent.append((to, body, buttons))

    monkeypatch.setattr(whatsapp_messaging, "load_whatsapp_config", load_config)
    monkeypatch.setattr(service, "send_interactive_buttons", send_buttons)

    await service.send_notification_action(
        {
            "target_mode": "selected",
            "target_ids": ["whatsapp:number:@AdminPhone"],
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
        variables={"AdminPhone": "+44 7700 900123"},
    )

    assert sent[0][0] == "447700900123"
    assert sent[0][1] == "Timeframe request\n\nSarah wants to stay later."
    allow = parse_visitor_pass_timeframe_button_id(sent[0][2][0]["id"])
    deny = parse_visitor_pass_timeframe_button_id(sent[0][2][1]["id"])
    assert allow.decision == "allow"
    assert allow.pass_id == "pass-1"
    assert allow.request_id == "request-1"
    assert deny.decision == "deny"


@pytest.mark.asyncio
async def test_automation_whatsapp_action_executes_dynamic_target(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
    sent = []
    async def load_config(*_args, **_kwargs):
        return await async_enabled_config()

    monkeypatch.setattr(whatsapp_messaging, "load_whatsapp_config", load_config)

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    monkeypatch.setattr(service, "send_text_message", send_text)
    context = SimpleNamespace(subject="Gate alert", variables={"AdminPhone": "+44 7700 900123", "Subject": "Gate alert"})

    result = await service.execute_automation_action(
        SimpleNamespace(),
        {
            "id": "action-1",
            "type": "integration.whatsapp.send_message",
            "config": {
                "target_mode": "dynamic",
                "phone_number_template": "@AdminPhone",
                "message_template": "@Subject",
            },
        },
        context,
        rule=SimpleNamespace(name="Gate rule"),
    )

    assert result["status"] == "success"
    assert result["delivered_count"] == 1
    assert sent == [("447700900123", "Gate alert")]
