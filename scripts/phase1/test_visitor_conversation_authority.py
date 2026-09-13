"""Neutral conversation policy with real, isolated PostgreSQL transactions."""
from test_recovery_boundaries import _bounded, isolated_resources as isolated_resources

import asyncio
from datetime import timedelta
import uuid

import pytest
from sqlalchemy import func, select

from app.db.session import AsyncSessionLocal
from app.models import AuditLog, NotificationRun, User, VisitorPass
from app.models.enums import UserRole, VisitorPassStatus, VisitorPassType
from app.services.mutation_context import MutationError
from app.services.visitor_conversations import (
    HomeAssistantTimeframeAction, VisitorConversationDenied, VisitorConversationService,
)
from app.services.workflows.visitor_conversations import visitor_timeframe_request_payload

pytestmark = pytest.mark.asyncio
PHONE = "15550000001"


async def seed(*, pending=False):
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        start, end = now - timedelta(minutes=30), now + timedelta(hours=2)
        visitor = VisitorPass(visitor_name="Synthetic Visitor", pass_type=VisitorPassType.DURATION,
            visitor_phone=PHONE, expected_time=start, valid_from=start, valid_until=end,
            window_minutes=30, status=VisitorPassStatus.ACTIVE)
        request = visitor_timeframe_request_payload("synthetic-request", "Synthetic request", None,
            (start, end), (start, end), (start, end + timedelta(hours=3)))
        if pending:
            visitor.source_metadata = {"whatsapp_timeframe_request": request}
        session.add(visitor)
        await session.commit()
        return visitor.id, start, end, request


async def stored(identity):
    async with AsyncSessionLocal() as session:
        return await session.get(VisitorPass, identity)


def scope(identity, request):
    return HomeAssistantTimeframeAction(str(identity), request["id"], "allow")


@pytest.mark.parametrize("label", [None, "Home Assistant Notification", "Admin", "Synthetic Operator"])
async def test_labels_never_grant_timeframe_authority(label):
    identity, _start, end, request = await seed(pending=True)
    with pytest.raises(VisitorConversationDenied, match="no longer valid") as caught:
        await VisitorConversationService().decide_timeframe_request(str(identity), request["id"], "allow", actor_label=label)
    assert caught.value.reason == "current_actor_required"
    row = await stored(identity)
    assert row.valid_until == end and row.source_metadata["whatsapp_timeframe_request"]["status"] == "pending"


@pytest.mark.parametrize("field", ["pass_id", "request_id", "decision"])
async def test_home_assistant_scope_is_exactly_bound(field):
    identity, _start, end, request = await seed(pending=True)
    values = dict(pass_id=str(identity), request_id=request["id"], decision="allow")
    values[field] = "deny" if field == "decision" else str(uuid.uuid4())
    with pytest.raises(VisitorConversationDenied):
        await VisitorConversationService().decide_timeframe_request(str(identity), request["id"], "allow",
            integration_action=HomeAssistantTimeframeAction(**values))
    assert (await stored(identity)).valid_until == end


async def test_authenticated_home_assistant_decision_consumes_exact_request_once():
    identity, _start, end, request = await seed(pending=True)
    service = VisitorConversationService()
    result = await service.decide_timeframe_request(str(identity), request["id"], "allow", integration_action=scope(identity, request))
    assert result.kind == "approved"
    row = await stored(identity)
    assert row.valid_until == end + timedelta(hours=3)
    assert row.source_metadata["whatsapp_timeframe_request"]["decided_by_user_id"] is None
    with pytest.raises(VisitorConversationDenied):
        await service.decide_timeframe_request(str(identity), request["id"], "allow", integration_action=scope(identity, request))
    async with AsyncSessionLocal() as session:
        audits = list((await session.scalars(select(AuditLog).where(AuditLog.action == "visitor_pass.timeframe_change_approved"))).all())
    assert len(audits) == 1 and audits[0].actor == "Home Assistant Notification"


@pytest.mark.parametrize("change", ["expired", "revoked", "request", "window", "handled"])
async def test_home_assistant_decision_rechecks_current_pass_and_pending_request(change):
    identity, _start, end, request = await seed(pending=True)
    async with AsyncSessionLocal() as session:
        row = await session.get(VisitorPass, identity)
        if change == "expired":
            row.valid_until = await session.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
        elif change == "revoked":
            row.status = VisitorPassStatus.CANCELLED
        elif change == "window":
            row.valid_until = end + timedelta(minutes=10)
        else:
            pending = dict(request)
            pending["id" if change == "request" else "status"] = "changed" if change == "request" else "denied"
            row.source_metadata = {"whatsapp_timeframe_request": pending}
        await session.commit()
        before = row.valid_until
    with pytest.raises(VisitorConversationDenied):
        await VisitorConversationService().decide_timeframe_request(str(identity), request["id"], "allow", integration_action=scope(identity, request))
    assert (await stored(identity)).valid_until == before


async def test_two_fresh_home_assistant_handlers_cannot_consume_the_same_request():
    identity, _start, end, request = await seed(pending=True)
    ready = asyncio.Event()
    async def attempt():
        await ready.wait()
        return await VisitorConversationService().decide_timeframe_request(str(identity), request["id"], "allow",
            integration_action=scope(identity, request))
    tasks = [asyncio.create_task(attempt()) for _ in range(2)]
    ready.set()
    outcomes = await _bounded(asyncio.gather(*tasks, return_exceptions=True))
    assert sum(isinstance(item, VisitorConversationDenied) for item in outcomes) == 1
    assert sum(getattr(item, "kind", None) == "approved" for item in outcomes) == 1
    assert (await stored(identity)).valid_until == end + timedelta(hours=3)


@pytest.mark.parametrize("change", ["inactive", "role", "auth_version"])
async def test_human_actor_is_freshly_revalidated(change):
    identity, _start, end, request = await seed(pending=True)
    async with AsyncSessionLocal() as session:
        user = User(username="synthetic-admin", full_name="Synthetic Admin", password_hash="unused",
            role=UserRole.ADMIN, is_active=True, auth_session_version=0)
        session.add(user)
        await session.commit()
    async with AsyncSessionLocal() as session:
        current = await session.get(User, user.id)
        if change == "inactive":
            current.is_active = False
        elif change == "role":
            current.role = UserRole.STANDARD
        else:
            current.auth_session_version = 1
        await session.commit()
    with pytest.raises(MutationError):
        await VisitorConversationService().decide_timeframe_request(str(identity), request["id"], "allow", actor_user=user)
    assert (await stored(identity)).valid_until == end


@pytest.mark.parametrize("history", [None, "freeform", "changed", "revoked"])
async def test_llm_direct_apply_does_not_grant_operator_authority(history):
    identity, start, end, _request = await seed()
    if history:
        async with AsyncSessionLocal() as session:
            row = await session.get(VisitorPass, identity)
            row.source_metadata = {"whatsapp_history": [{"direction": "outbound", "body": "Synthetic operator wording",
                "metadata": {"origin": "dashboard_custom", "proposal_status": history}}]}
            await session.commit()
    result = await VisitorConversationService().request_timeframe_change(identity, PHONE, "Synthetic consent",
        {"valid_from": start.isoformat(), "valid_until": (end + timedelta(hours=3)).isoformat(), "direct_apply": True})
    assert result.kind == "approval_required"
    row = await stored(identity)
    assert row.valid_until == end and row.source_metadata["whatsapp_timeframe_request"]["status"] == "pending"
    async with AsyncSessionLocal() as session:
        runs = list((await session.scalars(select(NotificationRun))).all())
    assert len(runs) == 1
    assert runs[0].id == uuid.uuid5(identity, f"timeframe-request:{result.request['id']}")
    assert runs[0].context["event_type"] == "visitor_pass_timeframe_change_requested"


async def test_bounded_visitor_window_requires_matching_confirmation_and_changes_once():
    identity, start, end, _request = await seed()
    service = VisitorConversationService()
    result = await service.request_timeframe_change(identity, PHONE, "Synthetic bounded request",
        {"valid_from": start.isoformat(), "valid_until": (end + timedelta(minutes=30)).isoformat(), "direct_apply": True})
    assert result.kind == "confirmation_required" and (await stored(identity)).valid_until == end
    confirmed = await service.confirm_timeframe_change(str(identity), PHONE, result.request["id"], "confirm")
    assert confirmed.kind == "window_updated" and (await stored(identity)).valid_until == end + timedelta(minutes=30)
    with pytest.raises(VisitorConversationDenied):
        await service.confirm_timeframe_change(str(identity), PHONE, result.request["id"], "confirm")


async def test_required_admin_notification_and_request_rollback_together(monkeypatch):
    identity, start, end, _request = await seed()
    async def fail_queue(*args, **kwargs):
        raise RuntimeError("Synthetic required queue failure")
    monkeypatch.setattr("app.services.visitor_conversations.NotificationRunStore.enqueue_in_session", fail_queue)
    with pytest.raises(RuntimeError, match="Synthetic required queue"):
        await VisitorConversationService().request_timeframe_change(identity, PHONE, "Synthetic request",
            {"valid_from": start.isoformat(), "valid_until": (end + timedelta(hours=3)).isoformat()})
    row = await stored(identity)
    assert row.valid_until == end and not row.source_metadata
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationRun)) == 0
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0


async def manual_seed():
    identity, _start, _end, _request = await seed()
    async with AsyncSessionLocal() as session:
        actor = User(username="synthetic-output-admin", first_name="Synthetic", last_name="Admin",
            full_name="Synthetic Admin", password_hash="unused", role=UserRole.ADMIN,
            is_active=True, auth_session_version=0)
        session.add(actor)
        await session.flush()
        visitor = await session.get(VisitorPass, identity)
        visitor.created_by_user_id, visitor.creation_source = actor.id, "ui"
        await session.commit()
        return actor, visitor


@pytest.mark.parametrize("change", ["phone", "revoked", "role", "auth_version", "operation"])
async def test_manual_visitor_notification_authority_rechecks_mutable_facts(change):
    actor, visitor = await manual_seed()
    service, operation = VisitorConversationService(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        _, origin = await service.prepare_manual_notification_origin(session, visitor.id,
            actor_user_id=actor.id, auth_version=0, kind="custom")
        await session.commit()
    origin["operation_id"] = str(operation)
    run_id = uuid.uuid5(operation, "notification-delivery")
    async with AsyncSessionLocal() as session:
        assert await service.authorize_notification_in_session(session, origin, run_id) is None
        await session.rollback()
    async with AsyncSessionLocal() as session:
        current, user = await session.get(VisitorPass, visitor.id), await session.get(User, actor.id)
        if change == "phone": current.visitor_phone = "15550000002"
        elif change == "revoked": current.status = VisitorPassStatus.CANCELLED
        elif change == "role": user.role = UserRole.STANDARD
        elif change == "auth_version": user.auth_session_version = 1
        else: run_id = uuid.uuid4()
        await session.commit()
    async with AsyncSessionLocal() as session:
        assert await service.authorize_notification_in_session(session, origin, run_id) is not None


@pytest.mark.parametrize("unavailable", ["disabled", "phone", "expired"])
async def test_outreach_unavailable_is_retained_without_rolling_back_valid_pass(monkeypatch, unavailable):
    from app.modules.messaging.whatsapp import WhatsAppIntegrationConfig
    from app.services.messaging import whatsapp_delivery as adapter

    actor, visitor = await manual_seed()
    config = WhatsAppIntegrationConfig(False if unavailable == "disabled" else True,
        "inert", "synthetic-channel", "synthetic-business", "inert", "inert", "v25.0", "iacs_visitor_welcome", "en")
    async def load(**kwargs): return config
    monkeypatch.setattr(adapter, "load_whatsapp_config", load)
    async with AsyncSessionLocal() as session:
        row = await session.get(VisitorPass, visitor.id)
        if unavailable == "phone": row.visitor_phone = "letters without a number"
        elif unavailable == "expired": row.valid_until = await session.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
        identity = await adapter.WhatsAppDeliveryService().reserve_outreach_in_session(
            session, row, actor_user_id=actor.id, auth_version=0, source="ui")
        assert identity == uuid.uuid5(visitor.id, "visitor-outreach")
        await session.commit()
    async with AsyncSessionLocal() as session:
        assert await session.get(VisitorPass, visitor.id) is not None
        run = await session.get(NotificationRun, identity)
        assert run.delivery_plan[0]["state"] == "skipped"
        assert run.delivery_plan[0]["reason"] == f"whatsapp_outreach_{'not_configured' if unavailable == 'disabled' else 'recipient_invalid' if unavailable == 'phone' else 'pass_invalid'}"
        assert run.context["visitor_conversation_origin"]["authority"]["user_id"] == str(actor.id)


async def prepared_custom_output():
    from app.services.notification_runs import NotificationRunStore

    actor, visitor = await manual_seed()
    service, operation = VisitorConversationService(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        _, origin = await service.prepare_manual_notification_origin(session, visitor.id,
            actor_user_id=actor.id, auth_version=0, kind="custom")
        origin["operation_id"] = str(operation)
        run_id = uuid.uuid5(operation, "notification-delivery")
        await NotificationRunStore().enqueue_prepared_in_session(session,
            {"event_type": "visitor_custom_message", "subject": "Visitor message", "severity": "info", "facts": {},
                "visitor_conversation_origin": origin}, run_id=run_id,
            plan=[{"rule": {"id": "custom", "name": "Custom", "trigger_event": "visitor_custom_message"},
                "action": {"type": "whatsapp", "delivery_mode": "literal", "target": PHONE, "message": "Synthetic literal message"},
                "state": "pending"}])
        await session.commit()
        return visitor.id, run_id


@pytest.mark.parametrize("state", ["accepted", "unknown", "skipped"])
async def test_notification_output_is_exact_pass_atomic_and_once(state):
    from app.services.visitor_passes import visitor_pass_whatsapp_history

    identity, run_id = await prepared_custom_output()
    service = VisitorConversationService()
    # Preparing may lock/read, but its caller retains the commit boundary.
    async with AsyncSessionLocal() as session:
        run = await session.get(NotificationRun, run_id)
        apply = await service.prepare_notification_output(session, run, 0,
            {"state": state, **({"provider_message_id": "synthetic-provider-id"} if state == "accepted" else {})})
        assert not visitor_pass_whatsapp_history(await session.get(VisitorPass, identity))
        await apply()
        await session.rollback()
    assert not visitor_pass_whatsapp_history(await stored(identity))
    async with AsyncSessionLocal() as session:
        run = await session.get(NotificationRun, run_id)
        apply = await service.prepare_notification_output(session, run, 0,
            {"state": state, **({"provider_message_id": "synthetic-provider-id"} if state == "accepted" else {})})
        await apply()
        await session.commit()
    async with AsyncSessionLocal() as session:
        run = await session.get(NotificationRun, run_id)
        assert await service.prepare_notification_output(session, run, 0, {"state": state}) is None
    history = visitor_pass_whatsapp_history(await stored(identity))
    assert len(history) == 1 and history[0]["id"] == uuid.uuid5(run_id, "visitor-history:0").hex
    assert history[0]["body"] == "Synthetic literal message" and history[0]["status"] == state
    assert history[0]["metadata"]["origin"] == "dashboard_custom"
    assert history[0]["provider_message_id"] == ("synthetic-provider-id" if state == "accepted" else None)
    result = await service.get_notification_result(identity, run_id)
    assert result["message"] == history[0]


async def test_history_projects_expired_attempt_without_mutation_or_llm_claim():
    from app.services.messaging.whatsapp_helpers import visitor_pass_whatsapp_llm_context

    identity, run_id = await prepared_custom_output()
    async with AsyncSessionLocal() as session:
        run = await session.get(NotificationRun, run_id)
        run.status = "processing"
        run.lease_expires_at = await session.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
        run.delivery_plan = [{**run.delivery_plan[0], "state": "attempting"}]
        await session.commit()
        await session.refresh(run, attribute_names=["updated_at"])
        before_updated = run.updated_at
    visitor_before = await stored(identity)
    history = await VisitorConversationService().notification_history(identity)
    assert len(history) == 1 and history[0]["status"] == "unknown" and history[0]["direction"] == "status"
    async with AsyncSessionLocal() as session:
        run = await session.get(NotificationRun, run_id)
        assert run.status == "processing" and run.updated_at == before_updated
    visitor_after = await stored(identity)
    assert visitor_after.source_metadata == visitor_before.source_metadata
    assert visitor_pass_whatsapp_llm_context(visitor_after)["recent_messages"] == []
