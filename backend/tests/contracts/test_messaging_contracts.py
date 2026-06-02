from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.modules.messaging.base import MessagingBridgeResult
from app.services import messaging_bridge as messaging_bridge_module
from app.services.whatsapp_messaging import WhatsAppIntegrationConfig, WhatsAppMessagingService

from .helpers import assert_contract_subset, load_contract_fixture


def _whatsapp_config() -> WhatsAppIntegrationConfig:
    return WhatsAppIntegrationConfig(
        enabled=True,
        access_token="test-token",
        phone_number_id="phone-number-1",
        business_account_id="business-account-1",
        webhook_verify_token="verify-token",
        app_secret="",
        graph_api_version="v25.0",
        visitor_pass_template_name="visitor_pass",
        visitor_pass_template_language="en",
    )


@pytest.mark.asyncio
async def test_whatsapp_visitor_flow_contract_stays_inside_visitor_sandbox(monkeypatch) -> None:
    service = WhatsAppMessagingService()
    service._visitor_message_debounce_seconds = 0
    visitor_pass = SimpleNamespace(id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
    acknowledgements: list[dict[str, object]] = []
    visitor_calls: list[dict[str, object]] = []

    async def fake_admin_for_phone(sender: str):
        return None

    async def fake_visitor_pass_for_phone(sender: str):
        return visitor_pass, "active"

    async def fake_visitor_reply_is_muted(pass_id, sender: str):
        return False

    async def fake_mark_read(message_id, *, config, show_typing: bool = False):
        acknowledgements.append({"message_id": message_id, "show_typing": show_typing})

    async def fake_process_visitor_text(sender, pass_row, text, *, config, emoji_preferred, alfred_mentioned):
        visitor_calls.append(
            {
                "sender": sender,
                "pass_id": str(pass_row.id),
                "text": text,
                "emoji_preferred": emoji_preferred,
                "alfred_mentioned": alfred_mentioned,
            }
        )

    async def fake_record_inbound(*args, **kwargs):
        return None

    async def fail_admin_bridge(*args, **kwargs):
        raise AssertionError("Visitor messages must not route to Alfred.")

    monkeypatch.setattr(service, "_admin_for_phone", fake_admin_for_phone)
    monkeypatch.setattr(service, "_visitor_pass_for_phone", fake_visitor_pass_for_phone)
    monkeypatch.setattr(service, "_visitor_reply_is_muted", fake_visitor_reply_is_muted)
    monkeypatch.setattr(service, "mark_incoming_message_read", fake_mark_read)
    monkeypatch.setattr(service, "_record_inbound_visitor_message", fake_record_inbound)
    monkeypatch.setattr(service, "_process_visitor_text", fake_process_visitor_text)
    monkeypatch.setattr(messaging_bridge_module.messaging_bridge_service, "handle_message", fail_admin_bridge)

    await service._handle_incoming_message(
        {
            "id": "wamid.visitor-1",
            "from": "+44 7700 900123",
            "type": "text",
            "text": {"body": "AB12 CDE"},
            "timestamp": "1777813142",
        },
        contacts=[],
        phone_number_id="phone-number-1",
        config=_whatsapp_config(),
        signature_verified=True,
    )

    route_payload = {
        "route": "visitor_sandbox",
        "sender": visitor_calls[0]["sender"],
        "message_id": acknowledgements[0]["message_id"],
        "show_typing": acknowledgements[0]["show_typing"],
        "bridge_called": False,
    }
    assert visitor_calls[0]["text"] == "AB12 CDE"
    assert_contract_subset(route_payload, load_contract_fixture("messaging/visitor_plate_reply.json"))


@pytest.mark.asyncio
async def test_whatsapp_admin_flow_contract_routes_active_admin_to_alfred_v3(monkeypatch) -> None:
    service = WhatsAppMessagingService()
    admin = SimpleNamespace(
        id=uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        full_name="Jas",
        username="jas",
    )
    sent_messages: list[tuple[str, str]] = []
    captured: dict[str, object] = {}

    async def fake_admin_for_phone(sender: str):
        return admin

    async def fake_ensure_admin_identity(*args, **kwargs):
        return None

    async def fake_handle_feedback(*args, **kwargs):
        return False

    async def fake_send_text_message(phone: str, text: str, **kwargs):
        sent_messages.append((phone, text))

    async def fail_confirmation(*args, **kwargs):
        raise AssertionError("No confirmation should be sent for a read-only admin status query.")

    class FakeBridge:
        async def handle_message(self, incoming, *, is_admin_hint: bool = False):
            captured["incoming"] = incoming
            captured["is_admin_hint"] = is_admin_hint
            return MessagingBridgeResult(
                session_id="session-admin",
                response_text="Gate status is closed.",
                pending_action=None,
            )

    monkeypatch.setattr(service, "_admin_for_phone", fake_admin_for_phone)
    monkeypatch.setattr(service, "_ensure_admin_identity", fake_ensure_admin_identity)
    monkeypatch.setattr(service, "_handle_admin_feedback_followup", fake_handle_feedback)
    monkeypatch.setattr(service, "send_text_message", fake_send_text_message)
    monkeypatch.setattr(service, "send_confirmation_message", fail_confirmation)
    monkeypatch.setattr(messaging_bridge_module, "messaging_bridge_service", FakeBridge())

    await service._handle_incoming_message(
        {
            "id": "wamid.admin-1",
            "from": "+44 7700 900001",
            "type": "text",
            "text": {"body": "Gate status?"},
            "timestamp": "1777813142",
        },
        contacts=[{"wa_id": "447700900001", "profile": {"name": "Jas"}}],
        phone_number_id="phone-number-1",
        config=_whatsapp_config(),
        signature_verified=True,
    )

    incoming = captured["incoming"]
    bridge_payload = {
        "provider": incoming.provider,
        "response_text": sent_messages[0][1],
        "pending_action": False,
        "client_context": {
            "source": "messaging",
            "messaging_provider": incoming.provider,
            "provider_channel_id": incoming.provider_channel_id,
            "provider_guild_id": incoming.provider_guild_id,
            "is_direct_message": incoming.is_direct_message,
            "author_display_name": incoming.author_display_name,
        },
    }
    assert captured["is_admin_hint"] is True
    assert incoming.author_is_provider_admin is True
    assert sent_messages == [("447700900001", "Gate status is closed.")]
    assert_contract_subset(bridge_payload, load_contract_fixture("messaging/admin_alfred_message.json"))
