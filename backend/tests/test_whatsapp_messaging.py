import asyncio
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
from app.api.v1 import visitor_passes as visitor_passes_api
from app.api.v1 import webhooks, whatsapp as whatsapp_api
from app.models import VisitorPass
from app.models.enums import UserRole, VisitorPassStatus, VisitorPassType
from app.modules.notifications.base import NotificationContext
from app.services.settings import DEFAULT_DYNAMIC_SETTINGS, LEGACY_DEFAULT_REPLACEMENTS, SECRET_KEYS
from app.services import whatsapp_messaging
from app.services.visitor_passes import visitor_pass_whatsapp_history
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
        "visitor_pass_template_name": "iacs_visitor_welcome",
        "visitor_pass_template_language": "en",
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
async def cleanup_whatsapp_test_runtime(monkeypatch):
    service = get_whatsapp_messaging_service()
    previous_debounce = service._visitor_message_debounce_seconds

    async def noop(*_args, **_kwargs):
        return None

    async def not_privileged(*_args, **_kwargs):
        return False

    async def not_muted(*_args, **_kwargs):
        return False

    async def no_plate_change_attempt(*_args, **_kwargs):
        return False

    service._visitor_message_debounce_seconds = 0
    monkeypatch.setattr(service, "mark_incoming_message_read", noop)
    monkeypatch.setattr(service, "_record_inbound_visitor_message", noop)
    monkeypatch.setattr(service, "_visitor_plate_is_privileged", not_privileged)
    monkeypatch.setattr(service, "_visitor_reply_is_muted", not_muted)
    monkeypatch.setattr(service, "_record_visitor_plate_change_attempt", no_plate_change_attempt)
    yield
    service._visitor_message_debounce_seconds = previous_debounce
    tasks = list(service._visitor_message_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    service._visitor_message_tasks.clear()
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
    assert LEGACY_DEFAULT_REPLACEMENTS["whatsapp_visitor_pass_template_name"] == {
        "visitor_pass_registration_request": "iacs_visitor_welcome",
    }
    assert DEFAULT_DYNAMIC_SETTINGS["whatsapp_visitor_pass_template_language"][1] == "en"
    assert {"whatsapp_access_token", "whatsapp_webhook_verify_token", "whatsapp_app_secret"}.issubset(SECRET_KEYS)


def test_phone_and_graph_version_normalization() -> None:
    assert normalize_whatsapp_phone_number("+44 (7700) 900-123") == "447700900123"
    assert normalize_graph_api_version("25.0") == "v25.0"
    assert normalize_graph_api_version("") == "v25.0"


def test_visitor_emoji_only_messages_are_preference_not_content() -> None:
    assert whatsapp_messaging.visitor_message_is_emoji_only("😂👍")
    assert not whatsapp_messaging.visitor_message_is_emoji_only("AB12 CDE 👍")
    assert whatsapp_messaging.visitor_message_contains_emoji("AB12 CDE 👍")


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

    class Service:
        async def send_visitor_pass_custom_message(self, pass_id_arg, message, *, actor_user):
            captured["pass_id"] = pass_id_arg
            captured["message"] = message
            captured["actor_user"] = actor_user
            return {"visitor_pass": visitor_payload, "message": message_payload}

    monkeypatch.setattr(visitor_passes_api, "get_whatsapp_messaging_service", lambda: Service())

    response = await visitor_passes_api.send_visitor_pass_whatsapp_message(
        pass_id,
        visitor_passes_api.VisitorPassWhatsAppSendRequest(message="  Do you want me to move your visitor pass to tomorrow?  "),
        user=user,
    )

    assert captured == {
        "pass_id": pass_id,
        "message": "Do you want me to move your visitor pass to tomorrow?",
        "actor_user": user,
    }
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

    monkeypatch.setattr(visitor_passes_api, "get_whatsapp_messaging_service", lambda: Service())

    response = await visitor_passes_api.unblock_visitor_pass_whatsapp(pass_id, user=user)

    assert captured == {"pass_id": pass_id, "actor_user": user}
    assert response.id == str(pass_id)
    assert response.source_metadata == {}


@pytest.mark.asyncio
async def test_custom_message_send_records_history_on_exact_pass(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
    actor = SimpleNamespace(id=uuid.uuid4(), username="jas", full_name="Jason Ash")
    pass_id = uuid.uuid4()
    visitor_pass = VisitorPass(
        id=pass_id,
        visitor_name="Ash",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 4, 8, 0, tzinfo=UTC),
        valid_from=datetime(2026, 5, 4, 8, 0, tzinfo=UTC),
        valid_until=datetime(2026, 5, 4, 20, 30, tzinfo=UTC),
        status=VisitorPassStatus.SCHEDULED,
        creation_source="ui",
        source_metadata={},
    )
    visitor_pass.created_at = datetime(2026, 5, 2, 17, 0, tzinfo=UTC)
    visitor_pass.updated_at = datetime(2026, 5, 2, 17, 0, tzinfo=UTC)
    captured = {"published": []}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, key):
            assert model is VisitorPass
            assert key == pass_id
            return visitor_pass

        async def commit(self):
            captured["committed"] = True

        async def refresh(self, row):
            captured["refreshed"] = row.id

    class VisitorPassService:
        async def refresh_statuses(self, **_kwargs):
            return []

    async def post(config, payload):
        captured["config"] = config
        captured["payload"] = payload
        return {"messages": [{"id": "wamid.custom"}]}

    async def audit(*_args, **kwargs):
        captured["audit"] = kwargs

    async def publish(event, payload):
        captured["published"].append((event, payload))

    monkeypatch.setattr(whatsapp_messaging, "AsyncSessionLocal", lambda: Session())
    monkeypatch.setattr(whatsapp_messaging, "get_visitor_pass_service", lambda: VisitorPassService())
    monkeypatch.setattr(whatsapp_messaging, "write_audit_log", audit)
    monkeypatch.setattr(whatsapp_messaging.event_bus, "publish", publish)
    monkeypatch.setattr(service, "_post_message", post)

    result = await service.send_visitor_pass_custom_message(
        pass_id,
        "Do you want me to move your visitor pass to tomorrow?",
        actor_user=actor,
        config=enabled_config(),
    )

    assert captured["payload"]["to"] == "447700900123"
    assert captured["payload"]["text"]["body"] == "Do you want me to move your visitor pass to tomorrow?"
    history = visitor_pass_whatsapp_history(visitor_pass)
    assert len(history) == 1
    assert history[0]["body"] == "Do you want me to move your visitor pass to tomorrow?"
    assert history[0]["provider_message_id"] == "wamid.custom"
    assert history[0]["metadata"]["origin"] == "dashboard_custom"
    assert history[0]["metadata"]["sender_user_id"] == str(actor.id)
    assert captured["audit"]["action"] == "visitor_pass.whatsapp_custom_message_sent"
    assert captured["published"][0][0] == "visitor_pass.updated"
    assert result["message"]["id"] == history[0]["id"]


@pytest.mark.asyncio
async def test_clear_visitor_abuse_mute_removes_cooldown_and_records_status(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
    actor = SimpleNamespace(id=uuid.uuid4(), username="jas", full_name="Jason Ash")
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
    captured = {"published": []}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, key):
            assert model is VisitorPass
            assert key == pass_id
            return visitor_pass

        async def commit(self):
            captured["committed"] = True

        async def refresh(self, row):
            captured["refreshed"] = row.id

    async def audit(*_args, **kwargs):
        captured["audit"] = kwargs

    async def publish(event, payload):
        captured["published"].append((event, payload))

    monkeypatch.setattr(whatsapp_messaging, "AsyncSessionLocal", lambda: Session())
    monkeypatch.setattr(whatsapp_messaging, "write_audit_log", audit)
    monkeypatch.setattr(whatsapp_messaging.event_bus, "publish", publish)

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
async def test_visitor_pass_outreach_uses_approved_welcome_template_shape(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
    captured = {}
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Ash",
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        expected_time=datetime(2026, 5, 3, 9, 0, tzinfo=UTC),
        valid_from=datetime(2026, 5, 3, 9, 0, tzinfo=UTC),
        valid_until=datetime(2026, 5, 3, 17, 0, tzinfo=UTC),
        status=VisitorPassStatus.SCHEDULED,
        creation_source="ui",
    )

    async def post(config_arg, payload):
        captured["config"] = config_arg
        captured["payload"] = payload
        return {"messages": [{"id": "wamid.template"}]}

    async def update_status(*_args, **_kwargs):
        captured["status_updated"] = True

    async def record_outbound(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_post_message", post)
    monkeypatch.setattr(service, "_update_visitor_concierge_status", update_status)
    monkeypatch.setattr(service, "_record_outbound_visitor_message", record_outbound)

    await service.send_visitor_pass_outreach(visitor_pass, config=enabled_config())

    assert captured["payload"]["template"]["name"] == "iacs_visitor_welcome"
    assert captured["payload"]["template"]["language"] == {"code": "en"}
    assert captured["payload"]["template"]["components"] == [
        {
            "type": "body",
            "parameters": [{"type": "text", "text": "Ash"}],
        }
    ]
    assert captured["status_updated"] is True


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
    assert parsed_confirm.decision == "confirm"
    assert parsed_confirm.pass_id == str(pass_id)
    assert parsed_confirm.nonce == "nonce123"
    assert parsed_change.decision == "change"


def test_visitor_plate_saved_message_is_warm_and_vehicle_aware() -> None:
    body = whatsapp_messaging.visitor_plate_saved_message(
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

    body = whatsapp_messaging.visitor_plate_confirmation_message(
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

    body = whatsapp_messaging.style_visitor_freeform_reply(
        "Alfred says you're all set. You're all set.",
        visitor_pass,
        "thanks",
        alfred_mentioned=False,
    )

    assert body == "You're all set."
    assert "Alfred" not in body


def test_visitor_registration_not_found_message_is_plain() -> None:
    body = whatsapp_messaging.visitor_registration_not_found_message("B00B1ES")

    assert "B00B1ES" in body
    assert "Please check the registration" in body
    assert "DVLA" not in body


@pytest.mark.asyncio
async def test_visitor_alfred_name_nod_is_llm_generated(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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

    monkeypatch.setattr(whatsapp_messaging, "get_runtime_config", runtime)
    monkeypatch.setattr(whatsapp_messaging, "get_llm_provider", lambda _provider_name: Provider())

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

    async def visitor_result(sender, pass_arg, text, **_kwargs):
        assert sender == "447700900123"
        assert pass_arg is visitor_pass
        assert text == "yeah it is ab12 cde"
        return {"action": "plate_detected", "registration_number": "AB12CDE"}

    async def lookup_vehicle(plate):
        assert plate == "AB12CDE"
        return whatsapp_messaging.VisitorVehicleLookup(make="Tesla", colour="Silver")

    async def store_pending(pass_id, sender, plate, nonce, **kwargs):
        captured["pending"] = (pass_id, sender, plate, nonce)
        captured["pending_details"] = kwargs

    async def send_confirmation(to, pass_arg, plate, nonce, **_kwargs):
        captured["confirmation"] = (to, pass_arg.id, plate, nonce)
        captured["confirmation_details"] = _kwargs

    monkeypatch.setattr(service, "_admin_for_phone", no_admin)
    monkeypatch.setattr(service, "_visitor_pass_for_phone", visitor_for_phone)
    monkeypatch.setattr(service, "_visitor_concierge_result", visitor_result)
    monkeypatch.setattr(service, "_lookup_visitor_vehicle_details", lookup_vehicle)
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
    assert captured["pending_details"]["vehicle_make"] == "Tesla"
    assert captured["pending_details"]["vehicle_colour"] == "Silver"
    assert captured["confirmation"][0] == "447700900123"
    assert captured["confirmation_details"]["vehicle_make"] == "Tesla"
    assert captured["confirmation_details"]["vehicle_colour"] == "Silver"


@pytest.mark.asyncio
async def test_muted_visitor_message_is_marked_read_without_typing(monkeypatch) -> None:
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
        source_metadata={"whatsapp_abuse_muted_until": "2026-05-01T11:00:00+00:00"},
    )
    captured = {"reads": []}

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

    monkeypatch.setattr(service, "_admin_for_phone", no_admin)
    monkeypatch.setattr(service, "_visitor_pass_for_phone", visitor_for_phone)
    monkeypatch.setattr(service, "_visitor_reply_is_muted", muted)
    monkeypatch.setattr(service, "mark_incoming_message_read", mark_read)
    monkeypatch.setattr(service, "_record_inbound_visitor_message", record_inbound)
    monkeypatch.setattr(service, "_handle_visitor_message", handle_visitor)

    await service._handle_incoming_message(
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
    service = get_whatsapp_messaging_service()
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
        return whatsapp_messaging.VisitorVehicleLookup(error="Vehicle not found")

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

    monkeypatch.setattr(service, "_visitor_reply_is_muted", not_muted)
    monkeypatch.setattr(service, "_visitor_concierge_result", visitor_result)
    monkeypatch.setattr(service, "_lookup_visitor_vehicle_details", lookup_vehicle)
    monkeypatch.setattr(service, "_record_visitor_plate_change_attempt", plate_change)
    monkeypatch.setattr(service, "_record_unverified_visitor_plate", unverified)
    monkeypatch.setattr(service, "_store_pending_visitor_plate", store_pending)
    monkeypatch.setattr(service, "send_visitor_plate_confirmation", send_confirmation)
    monkeypatch.setattr(service, "send_text_message", send_text)

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
    service = get_whatsapp_messaging_service()
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
        captured["reply_context"] = (pass_arg.id, plate, text)
        return "I can't use C25 UNY because it is already linked to privileged access. Please send the visitor vehicle registration instead."

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    async def lookup_vehicle(*_args, **_kwargs):
        raise AssertionError("Known privileged registrations must be rejected before vehicle lookup.")

    async def store_pending(*_args, **_kwargs):
        raise AssertionError("Known privileged registrations must not become pending plates.")

    monkeypatch.setattr(service, "_visitor_reply_is_muted", not_muted)
    monkeypatch.setattr(service, "_visitor_concierge_result", visitor_result)
    monkeypatch.setattr(service, "_visitor_plate_is_privileged", privileged)
    monkeypatch.setattr(service, "_record_privileged_visitor_plate", record_privileged)
    monkeypatch.setattr(service, "_visitor_privileged_plate_reply", privileged_reply)
    monkeypatch.setattr(service, "_lookup_visitor_vehicle_details", lookup_vehicle)
    monkeypatch.setattr(service, "_store_pending_visitor_plate", store_pending)
    monkeypatch.setattr(service, "send_text_message", send_text)

    await service._process_visitor_text(
        "447700900123",
        visitor_pass,
        "Use C25 UNY",
        config=enabled_config(),
    )

    assert captured["privileged"] == (visitor_pass.id, "447700900123", "C25UNY")
    assert captured["reply_context"] == (visitor_pass.id, "C25UNY", "Use C25 UNY")
    assert sent == [
        (
            "447700900123",
            "I can't use C25 UNY because it is already linked to privileged access. Please send the visitor vehicle registration instead.",
        )
    ]


@pytest.mark.asyncio
async def test_privileged_registration_reply_is_llm_generated(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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

    monkeypatch.setattr(whatsapp_messaging, "get_runtime_config", runtime)
    monkeypatch.setattr(whatsapp_messaging, "get_llm_provider", lambda _provider_name: Provider())

    body = await service._visitor_privileged_plate_reply(visitor_pass, "C25UNY", "Use C25 UNY")

    assert "privileged access" in body
    assert "visitor vehicle registration" in body
    prompt = captured["messages"][0].content
    assert "already linked to privileged access" in prompt
    assert "cannot be used for this Visitor Pass" in prompt


@pytest.mark.asyncio
async def test_repeated_plate_changes_trigger_llm_mute(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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
        return whatsapp_messaging.VisitorVehicleLookup(found=True, make="Tesla", colour="Silver")

    async def plate_change(*_args, **_kwargs):
        return True

    async def abuse(sender, pass_arg, text, **kwargs):
        captured["abuse"] = (sender, pass_arg.id, text, kwargs)

    async def store_pending(*_args, **_kwargs):
        raise AssertionError("Abusive plate changes should not be stored.")

    async def send_confirmation(*_args, **_kwargs):
        raise AssertionError("Abusive plate changes should not be confirmed.")

    monkeypatch.setattr(service, "_visitor_reply_is_muted", not_muted)
    monkeypatch.setattr(service, "_visitor_concierge_result", visitor_result)
    monkeypatch.setattr(service, "_lookup_visitor_vehicle_details", lookup_vehicle)
    monkeypatch.setattr(service, "_record_visitor_plate_change_attempt", plate_change)
    monkeypatch.setattr(service, "_trigger_visitor_abuse_mute", abuse)
    monkeypatch.setattr(service, "_store_pending_visitor_plate", store_pending)
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
    service = get_whatsapp_messaging_service()
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

    monkeypatch.setattr(service, "_visitor_reply_is_muted", not_muted)
    monkeypatch.setattr(service, "_visitor_concierge_result", visitor_result)
    monkeypatch.setattr(service, "_record_visitor_post_complete_reply", chatter)
    monkeypatch.setattr(service, "_trigger_visitor_abuse_mute", abuse)
    monkeypatch.setattr(service, "send_text_message", send_text)

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
    service = get_whatsapp_messaging_service()
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

    monkeypatch.setattr(whatsapp_messaging, "get_runtime_config", runtime)
    monkeypatch.setattr(whatsapp_messaging, "get_llm_provider", lambda _provider_name: Provider())

    body = await service._visitor_abuse_stop_reply(visitor_pass, "hello again", reason="post_complete_replies")

    assert "30 minutes" in body
    assert "Alfred" not in body
    prompt = captured["messages"][0].content
    assert "funny but firm" in prompt
    assert "pause for 30 minutes" in prompt


@pytest.mark.asyncio
async def test_terminal_visitor_pass_reply_is_sent_once(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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

        async def commit(self):
            return None

        async def refresh(self, _row):
            return None

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    async def publish(event, payload):
        published.append((event, payload))

    monkeypatch.setattr(whatsapp_messaging, "AsyncSessionLocal", lambda: Session())
    monkeypatch.setattr(whatsapp_messaging.event_bus, "publish", publish)
    monkeypatch.setattr(service, "send_text_message", send_text)

    first = await service._send_terminal_visitor_pass_reply_once(visitor_pass, "447700900123", config=enabled_config())
    second = await service._send_terminal_visitor_pass_reply_once(visitor_pass, "447700900123", config=enabled_config())

    assert first is True
    assert second is False
    assert len(sent) == 1
    assert "cancelled" in sent[0][1]
    assert visitor_pass.source_metadata["whatsapp_terminal_notice_sent_at"]
    assert published[0][0] == "visitor_pass.updated"


@pytest.mark.asyncio
async def test_buffered_visitor_messages_are_processed_as_one_reply_with_emoji_preference(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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

    async def consume(pass_id, sender, token):
        assert pass_id == visitor_pass.id
        assert sender == "447700900123"
        assert token == "token-1"
        return {
            "visitor_pass": visitor_pass,
            "text": "Hi Alfred\nmy reg is\nAB12 CDE",
            "emoji_preferred": True,
        }

    async def visitor_result(sender, pass_arg, text, **_kwargs):
        captured["text"] = text
        captured["alfred_mentioned"] = _kwargs.get("alfred_mentioned")
        assert sender == "447700900123"
        assert pass_arg is visitor_pass
        return {"action": "plate_detected", "registration_number": "AB12CDE"}

    async def lookup_vehicle(plate):
        assert plate == "AB12CDE"
        return whatsapp_messaging.VisitorVehicleLookup(make="Tesla", colour="Silver")

    async def store_pending(*_args, **_kwargs):
        return None

    async def send_confirmation(to, pass_arg, plate, nonce, **kwargs):
        captured["confirmation"] = (to, pass_arg.id, plate)
        captured["confirmation_kwargs"] = kwargs

    async def alfred_nod(pass_arg, text):
        assert pass_arg is visitor_pass
        assert "Alfred" in text
        return "Alfred says Jason has achieved peak driveway nerd."

    monkeypatch.setattr(service, "_consume_visitor_text_buffer", consume)
    monkeypatch.setattr(service, "_visitor_concierge_result", visitor_result)
    monkeypatch.setattr(service, "_lookup_visitor_vehicle_details", lookup_vehicle)
    monkeypatch.setattr(service, "_store_pending_visitor_plate", store_pending)
    monkeypatch.setattr(service, "_visitor_alfred_name_nod", alfred_nod)
    monkeypatch.setattr(service, "send_visitor_plate_confirmation", send_confirmation)

    await service._process_buffered_visitor_text(
        visitor_pass.id,
        "447700900123",
        "token-1",
        config=enabled_config(),
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

    async def record_inbound(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_admin_for_phone", no_admin)
    monkeypatch.setattr(service, "_visitor_pass_for_phone", visitor_for_phone)
    monkeypatch.setattr(service, "get_pass_details", pass_details)
    monkeypatch.setattr(whatsapp_messaging, "get_runtime_config", runtime)
    monkeypatch.setattr(service, "send_text_message", send_text)
    monkeypatch.setattr(service, "_record_inbound_visitor_message", record_inbound)

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
async def test_visitor_vip_list_request_gets_restricted_reply(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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
        return SimpleNamespace(llm_provider="local", site_timezone="Europe/London")

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    async def record_inbound(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_admin_for_phone", no_admin)
    monkeypatch.setattr(service, "_visitor_pass_for_phone", visitor_for_phone)
    monkeypatch.setattr(service, "get_pass_details", pass_details)
    monkeypatch.setattr(whatsapp_messaging, "get_runtime_config", runtime)
    monkeypatch.setattr(service, "send_text_message", send_text)
    monkeypatch.setattr(service, "_record_inbound_visitor_message", record_inbound)

    await service._handle_incoming_message(
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
    assert sent[0][1].startswith("Welcome to Crest House Access Control.")
    assert "access between 02 May 2026, 10:00 and 02 May 2026, 18:00" in sent[0][1]
    assert sent[0][1].endswith("vehicle registration, which will be read upon arrival to open the gate.")


@pytest.mark.asyncio
async def test_visitor_begin_template_button_starts_registration_prompt(monkeypatch) -> None:
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
        raise AssertionError("Begin button should not invoke the visitor LLM.")

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
async def test_visitor_concierge_prompt_includes_latest_dashboard_custom_message(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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

    monkeypatch.setattr(whatsapp_messaging, "get_runtime_config", runtime)
    monkeypatch.setattr(service, "get_pass_details", pass_details)
    monkeypatch.setattr(whatsapp_messaging, "get_llm_provider", lambda _provider_name: Provider())

    result = await service._visitor_concierge_result("447700900123", visitor_pass, "Yes")

    assert result == {"action": "reply", "message": "All set."}
    prompt_payload = json.loads(captured["messages"][1].content)
    custom_message = prompt_payload["conversation_context"]["latest_dashboard_custom_message"]
    assert custom_message["body"] == "Do you want me to move your visitor pass to tomorrow?"
    assert custom_message["origin"] == "dashboard_custom"
    assert prompt_payload["conversation_context"]["recent_messages"][-1]["body"] == "Yes"


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


@pytest.mark.asyncio
async def test_visitor_thanks_after_confirmed_plate_gets_warm_reply_not_reconfirmation(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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

    monkeypatch.setattr(whatsapp_messaging, "get_runtime_config", runtime)
    monkeypatch.setattr(service, "get_pass_details", pass_details)
    monkeypatch.setattr(whatsapp_messaging, "get_llm_provider", lambda _provider_name: Provider())

    result = await service._visitor_concierge_result(
        "447700900123",
        visitor_pass,
        "Alfred you LEGEND",
    )

    assert result == {"action": "reply", "message": "Haha, thanks Josh! You're all set."}


@pytest.mark.asyncio
async def test_visitor_random_text_after_confirmed_plate_is_not_treated_as_new_plate_with_llm(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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

    monkeypatch.setattr(whatsapp_messaging, "get_runtime_config", runtime)
    monkeypatch.setattr(service, "get_pass_details", pass_details)
    monkeypatch.setattr(whatsapp_messaging, "get_llm_provider", lambda _provider_name: Provider())

    result = await service._visitor_concierge_result(
        "447700900123",
        visitor_pass,
        "Random reference AB12 CDE, lol",
    )

    assert result == {"action": "reply", "message": "Haha, thanks Josh! You're all set."}


@pytest.mark.asyncio
async def test_visitor_random_text_after_confirmed_plate_is_not_locally_parsed_as_new_plate(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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

    monkeypatch.setattr(whatsapp_messaging, "get_runtime_config", runtime)
    monkeypatch.setattr(service, "get_pass_details", pass_details)

    result = await service._visitor_concierge_result(
        "447700900123",
        visitor_pass,
        "Random reference AB12 CDE, lol",
    )

    assert result == {"action": "reply", "message": "Haha, thanks Josh! You're all set."}


@pytest.mark.asyncio
async def test_visitor_confirmed_pass_can_still_send_new_plate_without_llm(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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

    monkeypatch.setattr(whatsapp_messaging, "get_runtime_config", runtime)
    monkeypatch.setattr(service, "get_pass_details", pass_details)

    result = await service._visitor_concierge_result(
        "447700900123",
        visitor_pass,
        "Actually I brought AB12 CDE today",
    )

    assert result == {"action": "plate_detected", "registration_number": "AB12CDE"}


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
async def test_pending_timeframe_approval_blocks_new_time_request(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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

        async def commit(self):
            return None

    class VisitorPassService:
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

    monkeypatch.setattr(whatsapp_messaging, "AsyncSessionLocal", lambda: Session())
    monkeypatch.setattr(whatsapp_messaging, "get_visitor_pass_service", lambda: VisitorPassService())
    monkeypatch.setattr(service, "_visitor_pending_timeframe_reply", pending_reply)
    monkeypatch.setattr(service, "send_text_message", send_text)

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
async def test_dashboard_custom_timeframe_consent_applies_directly(monkeypatch) -> None:
    service = get_whatsapp_messaging_service()
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
            "whatsapp_chat_history": [
                {
                    "id": "custom-1",
                    "direction": "outbound",
                    "kind": "text",
                    "body": "Do you want me to move your visitor pass to tomorrow?",
                    "actor_label": "IACS",
                    "created_at": "2026-05-02T18:01:00+01:00",
                    "metadata": {"origin": "dashboard_custom", "sender_label": "Jason Ash"},
                }
            ]
        },
    )
    visitor_pass.created_at = datetime(2026, 5, 2, 17, 0, tzinfo=UTC)
    visitor_pass.updated_at = datetime(2026, 5, 2, 17, 0, tzinfo=UTC)
    sent = []
    audits = []
    published = []
    requested_from = datetime(2026, 5, 5, 8, 0, tzinfo=UTC)
    requested_until = datetime(2026, 5, 5, 20, 30, tzinfo=UTC)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, key):
            assert model is VisitorPass
            assert key == pass_id
            return visitor_pass

        async def commit(self):
            return None

        async def refresh(self, _row):
            return None

    class VisitorPassService:
        async def refresh_statuses(self, **_kwargs):
            return []

        def window_start(self, pass_):
            return pass_.valid_from

        def window_end(self, pass_):
            return pass_.valid_until

        async def update_pass(self, _session, pass_, **kwargs):
            pass_.valid_from = kwargs["valid_from"]
            pass_.valid_until = kwargs["valid_until"]
            pass_.expected_time = kwargs["valid_from"]
            pass_.source_metadata = kwargs["source_metadata"]
            return pass_

    async def send_text(to, body, **_kwargs):
        sent.append((to, body))

    async def audit(*_args, **kwargs):
        audits.append(kwargs)

    async def publish(event, payload):
        published.append((event, payload))

    monkeypatch.setattr(whatsapp_messaging, "AsyncSessionLocal", lambda: Session())
    monkeypatch.setattr(whatsapp_messaging, "get_visitor_pass_service", lambda: VisitorPassService())
    monkeypatch.setattr(whatsapp_messaging, "write_audit_log", audit)
    monkeypatch.setattr(whatsapp_messaging.event_bus, "publish", publish)
    monkeypatch.setattr(service, "send_text_message", send_text)

    await service._handle_visitor_timeframe_change(
        "447700900123",
        visitor_pass,
        "Yes",
        {
            "valid_from": requested_from.isoformat(),
            "valid_until": requested_until.isoformat(),
            "summary": "Visitor agreed to dashboard proposal.",
            "direct_apply": True,
            "source": "dashboard_custom_proposal",
        },
        config=enabled_config(),
    )

    assert visitor_pass.valid_from == requested_from
    assert visitor_pass.valid_until == requested_until
    assert visitor_pass.source_metadata["whatsapp_timeframe_last_change"]["status"] == "dashboard_custom_confirmed"
    assert visitor_pass.source_metadata["whatsapp_timeframe_last_change"]["operator_message"] == (
        "Do you want me to move your visitor pass to tomorrow?"
    )
    assert audits[0]["action"] == "visitor_pass.dashboard_custom_timeframe_applied"
    assert published[0][0] == "visitor_pass.updated"
    assert sent[0][0] == "447700900123"
    assert "now valid" in sent[0][1]


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
