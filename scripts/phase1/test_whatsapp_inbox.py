"""Concrete WhatsApp recovery under isolated PostgreSQL and inert transport."""
from test_recovery_boundaries import _bounded, isolated_resources as isolated_resources

from datetime import timedelta
from types import SimpleNamespace
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text, update

from app.db.session import AsyncSessionLocal
from app.models import ProcessedMessagingMessage, User, VisitorPass
from app.models.enums import UserRole, VisitorPassStatus, VisitorPassType
from app.modules.messaging.whatsapp import WhatsAppIntegrationConfig
from app.services.messaging import whatsapp_incoming, whatsapp_replies
from app.services.messaging.incoming_messages import IncomingMessageStore
from app.services.messaging.whatsapp_delivery import WhatsAppDeliveryService
from app.services.messaging.whatsapp_webhook import WhatsAppWebhookService
from app.services.visitor_passes import visitor_pass_whatsapp_history

pytestmark = pytest.mark.asyncio
PHONE = "15550000001"
CONFIG = WhatsAppIntegrationConfig(True, "inert", "synthetic-channel", "synthetic-business", "inert", "inert",
    "v25.0", "iacs_visitor_welcome", "en")


@pytest_asyncio.fixture(autouse=True)
async def empty_inbox(isolated_resources):
    # The broad recovery fixture clears domain rows. Inbox identity deliberately
    # has no User FK and therefore needs its own explicit per-test namespace.
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE processed_messaging_messages"))
        await session.commit()


async def admin():
    async with AsyncSessionLocal() as session:
        user = User(username="synthetic-whatsapp-admin", first_name="Synthetic", last_name="Admin",
            full_name="Synthetic Admin", password_hash="unused", role=UserRole.ADMIN, is_active=True,
            auth_session_version=0, mobile_phone_number=PHONE)
        session.add(user)
        await session.commit()
        return user


async def accept(*, provider_id="synthetic-1", body="Synthetic hello"):
    return uuid.UUID(await WhatsAppWebhookService(dispatcher=SimpleNamespace(wake=lambda: None))._accept_incoming_provider_message(
        {"id": provider_id, "from": PHONE, "type": "text", "text": {"body": body}}, contacts=[],
        phone_number_id=CONFIG.phone_number_id, config=CONFIG, signature_verified=True))


def configure(monkeypatch, transport, handler):
    async def load(**kwargs): return CONFIG
    monkeypatch.setattr(whatsapp_replies, "load_whatsapp_config", load)
    monkeypatch.setattr(whatsapp_incoming, "is_recovery_hold", lambda: False)
    class Router:
        def __init__(self, **kwargs): self.delivery, self.result = kwargs["delivery"], {"kind": "message"}
        async def _handle_incoming_message(self, message, **kwargs): await handler(self, message, **kwargs)
    monkeypatch.setattr(whatsapp_incoming, "WhatsAppRouter", Router)
    return whatsapp_incoming.WhatsAppIncomingDispatcher(delivery=WhatsAppDeliveryService(transport=transport))


async def test_acceptance_restart_and_one_reply_checkpoint(monkeypatch):
    user = await admin()
    identity = await accept()
    assert await accept(body="Replacement is inert") == identity
    calls = []
    class Transport:
        async def send(self, config, payload):
            row = await IncomingMessageStore().get(identity)
            assert row.reply_plan[0]["state"] == "attempting"
            assert config is CONFIG
            calls.append(payload)
            return {"messages": [{"id": "synthetic-result"}]}
    async def handle(router, message, **kwargs):
        assert kwargs["sender_state"].admin.id == user.id
        assert message["text"]["body"] == "Synthetic hello"
        router.result = {"session_id": str(uuid.UUID(int=24)), "confirmation_id": "confirm-" + "a" * 32}
        await router.delivery.send_text_message(PHONE, "Synthetic exact response", config=CONFIG)
    worker = configure(monkeypatch, Transport(), handle)
    assert await _bounded(worker.run_once(identity))
    assert not await worker.run_once(identity)
    row = await IncomingMessageStore().get(identity)
    assert row.state == "handled" and row.reply_plan[0]["state"] == "accepted" and len(calls) == 1
    view = await IncomingMessageStore().recovery_detail(identity, provider="whatsapp")
    assert view["result_ids"]["confirmation_id"] == "confirm-" + "a" * 32
    assert "Synthetic exact response" not in str(view) and PHONE not in str(view)


async def test_lost_reply_response_is_reviewed_without_resend(monkeypatch):
    await admin()
    identity, calls = await accept(), []
    class Transport:
        async def send(self, config, payload):
            calls.append(payload)
            raise TimeoutError("Synthetic response lost after transmission")
    async def handle(router, message, **kwargs):
        router.result = {"session_id": str(uuid.UUID(int=44))}
        await router.delivery.send_text_message(PHONE, "Synthetic response", config=CONFIG)
    worker = configure(monkeypatch, Transport(), handle)
    assert await _bounded(worker.run_once(identity))
    row = await IncomingMessageStore().get(identity)
    assert row.state == "review_required" and row.reply_plan[0]["state"] == "unknown"
    assert row.result == {"session_id": str(uuid.UUID(int=44))}
    assert not await worker.run_once(identity) and len(calls) == 1


@pytest.mark.parametrize("change", ["inactive", "demoted", "auth_version", "phone"])
async def test_current_sender_change_prevents_handler_and_provider(monkeypatch, change):
    user = await admin()
    identity = await accept()
    async with AsyncSessionLocal() as session:
        row = await session.get(User, user.id)
        if change == "inactive": row.is_active = False
        elif change == "demoted": row.role = UserRole.STANDARD
        elif change == "auth_version": row.auth_session_version += 1
        else: row.mobile_phone_number = "15550000002"
        await session.commit()
    async def forbidden(*args, **kwargs): raise AssertionError("Changed sender cannot execute")
    worker = configure(monkeypatch, SimpleNamespace(send=forbidden), forbidden)
    assert await worker.run_once(identity)
    row = await IncomingMessageStore().get(identity)
    assert row.state == "review_required" and row.review_reason == "sender_binding_changed"
    assert not row.reply_plan


async def test_dispatcher_does_not_claim_another_provider(monkeypatch):
    await admin()
    async with AsyncSessionLocal() as session:
        other = await IncomingMessageStore().accept_in_session(session, provider="discord", provider_message_id="123456789",
            provider_channel_id="synthetic-discord", author_provider_id="synthetic-user",
            envelope={"message": {"type": "text", "text": {"body": "inert"}}},
            routing_context={"kind": "standard", "user_id": None}, received_at=None)
        await session.commit()
    identity = await accept()
    async def handle(router, message, **kwargs): router.result = {"kind": "handled-without-reply"}
    async def forbidden(*args, **kwargs): raise AssertionError("No reply requested")
    worker = configure(monkeypatch, SimpleNamespace(send=forbidden), handle)
    assert not await worker.run_once(other)
    assert await worker.run_once()
    assert (await IncomingMessageStore().get(other)).state == "received"
    assert (await IncomingMessageStore().get(identity)).state == "handled"


async def test_crash_after_handler_mutation_never_reinvokes(monkeypatch):
    user = await admin()
    identity, calls = await accept(), []
    async def handle(router, message, **kwargs):
        calls.append(1)
        async with AsyncSessionLocal() as session:
            row = await session.get(User, user.id)
            row.first_name = "Committed synthetic result"
            await session.commit()
        router.result = {"session_id": str(uuid.UUID(int=88))}
        raise RuntimeError("Synthetic checkpoint interruption")
    worker = configure(monkeypatch, SimpleNamespace(), handle)
    assert await worker.run_once(identity)
    assert not await worker.run_once(identity)
    assert calls == [1] and (await IncomingMessageStore().get(identity)).state == "review_required"
    async with AsyncSessionLocal() as session:
        assert (await session.get(User, user.id)).first_name == "Committed synthetic result"


async def test_visitor_debounced_batch_retains_individual_history(monkeypatch):
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        visitor = VisitorPass(visitor_name="Synthetic Visitor", pass_type=VisitorPassType.DURATION,
            visitor_phone=PHONE, expected_time=now, valid_from=now - timedelta(minutes=1),
            valid_until=now + timedelta(hours=1), status=VisitorPassStatus.ACTIVE, window_minutes=30)
        session.add(visitor)
        await session.commit()
        pass_id = visitor.id
    ids = [await accept(provider_id=f"synthetic-{i}", body=text) for i, text in enumerate(["Hello Alfred", "my registration", "AB12 CDE 😀"])]
    async with AsyncSessionLocal() as session:
        await session.execute(update(ProcessedMessagingMessage).values(available_at=func.clock_timestamp()))
        await session.commit()
    captured = []
    async def handle(router, message, **kwargs):
        captured.append((message["text"]["body"], kwargs["history_recorded"]))
        assert kwargs["sender_state"].visitor_pass.id == pass_id
    worker = configure(monkeypatch, SimpleNamespace(), handle)
    assert await _bounded(worker.run_once(ids[0]))
    assert captured == [("Hello Alfred\nmy registration\nAB12 CDE 😀", True)]
    async with AsyncSessionLocal() as session:
        history = visitor_pass_whatsapp_history(await session.get(VisitorPass, pass_id))
    assert [item["body"] for item in history] == ["Hello Alfred", "my registration", "AB12 CDE 😀"]
    assert all([(await IncomingMessageStore().get(identity)).state == "handled" for identity in ids])
