"""Approval contracts against disposable PostgreSQL and inert tool sinks only.

Run through the isolated harness with IACS_RECOVERY_PROBES=synthetic-only.
The imported guard refuses collection before application imports elsewhere.
"""

from test_recovery_boundaries import _approval_setup, _bounded, _rows
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import httpx
import pytest
from sqlalchemy import text, update
from starlette.websockets import WebSocketDisconnect

from app.ai.context import get_chat_tool_context
from app.api.v1 import ai as ai_api
from app.db.session import AsyncSessionLocal
from app.models import AlfredApproval, ChatSession, User
from app.models.enums import UserRole
from app.services.alfred.approvals import AlfredApprovalStore
from app.services.chat import ChatService

pytestmark = pytest.mark.asyncio


async def _post(app, request):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid", trust_env=False) as client:
        return await client.post("/api/v1/ai/chat/confirm", json=request)


def _new_worker(monkeypatch, first):
    worker = ChatService()
    worker._tools = dict(first._tools)
    worker._approvals = AlfredApprovalStore()
    monkeypatch.setattr(worker, "_update_memory", AsyncMock())
    return worker


async def test_independent_workers_retain_one_operation_and_replay_result(monkeypatch):
    first, app, request, calls = await _approval_setup(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = first._tools["open_gate"].handler
    contexts = []

    async def held_sink(arguments):
        contexts.append(dict(get_chat_tool_context()))
        entered.set()
        await _bounded(release.wait())
        return await original(arguments)

    first._tools["open_gate"] = replace(first._tools["open_gate"], handler=held_sink)
    second = _new_worker(monkeypatch, first)
    approval = (await _rows(AlfredApproval))[0]
    task = asyncio.create_task(_post(app, request))
    try:
        await _bounded(entered.wait())
        repeated = await second.handle_tool_confirmation(**request, user_id=str(approval.requester_user_id), user_role="admin")
        assert "already claimed" in repeated.text
        assert not calls
    finally:
        release.set()
        response = await _bounded(task)
    assert response.status_code == 200
    replay = await second.handle_tool_confirmation(**request, user_id=str(approval.requester_user_id), user_role="admin")
    row = (await _rows(AlfredApproval))[0]
    assert calls == ["confirmed-inert-tool"]
    assert row.status == "completed" and row.result["tool_result"]["output"]["accepted"]
    assert contexts[0]["intent_id"] == contexts[0]["idempotency_key"] == str(row.operation_id)
    assert replay.assistant_message_id == response.json()["assistant_message_id"]
    assert replay.tool_results[0]["call_id"] == f"confirmed-{row.operation_id}"


async def test_disconnected_requester_does_not_cancel_or_erase_action_result(monkeypatch):
    service, app, request, calls = await _approval_setup(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._tools["open_gate"].handler

    async def held_sink(arguments):
        entered.set()
        await _bounded(release.wait())
        return await original(arguments)

    service._tools["open_gate"] = replace(service._tools["open_gate"], handler=held_sink)
    task = asyncio.create_task(_post(app, request))
    try:
        await _bounded(entered.wait())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service._approval_tasks
    finally:
        release.set()
        await _bounded(asyncio.gather(*list(service._approval_tasks)))
    row = (await _rows(AlfredApproval))[0]
    assert row.status == "completed" and row.result["turn"]["tool_results"][0]["output"]["accepted"]
    response = await _post(app, request)
    assert response.status_code == 200 and response.json()["tool_results"][0]["output"]["accepted"]
    assert calls == ["confirmed-inert-tool"] and not service._approval_tasks


@pytest.mark.parametrize("change", ["other_admin", "missing", "inactive", "demoted", "auth_version", "deleted"])
async def test_current_requester_authority_is_required_even_with_claimed_admin_payload(monkeypatch, change):
    service, app, request, calls = await _approval_setup(monkeypatch)
    row = (await _rows(AlfredApproval))[0]
    requester = row.requester_user_id
    async with AsyncSessionLocal() as session:
        user = await session.get(User, requester)
        if change == "other_admin":
            other = User(username="synthetic-other", full_name="Synthetic Other", password_hash="unused", role=UserRole.ADMIN, is_active=True)
            session.add(other)
            await session.flush()
            requester = other.id
        elif change == "missing":
            requester = None
        elif change == "inactive":
            user.is_active = False
        elif change == "demoted":
            user.role = UserRole.STANDARD
        elif change == "auth_version":
            user.auth_session_version += 1
        elif change == "deleted":
            await session.delete(user)
        await session.commit()
    result = await service.handle_tool_confirmation(**request, user_id=str(requester) if requester else None, user_role="admin")
    assert not calls and not result.tool_results
    assert "fresh confirmation" in result.text
    row = (await _rows(AlfredApproval))[0]
    assert row.status != "claimed"


async def test_expiry_uses_database_time_and_cannot_be_reset_by_replay(monkeypatch):
    service, app, request, calls = await _approval_setup(monkeypatch)
    async with AsyncSessionLocal() as session:
        await session.execute(update(AlfredApproval).values(expires_at=text("clock_timestamp()")))
        await session.commit()
    first, second = await _post(app, request), await _post(app, request)
    assert first.status_code == second.status_code == 200
    assert not calls and (await _rows(AlfredApproval))[0].status == "expired"


async def test_legacy_memory_confirmation_is_inert_without_overwriting_conversation_memory(monkeypatch):
    service, app, request, calls = await _approval_setup(monkeypatch)
    row = (await _rows(AlfredApproval))[0]
    legacy = {"id": "confirm-legacy", "tool_name": "open_gate", "arguments": {"confirm": True}}
    async with AsyncSessionLocal() as session:
        chat = await session.get(ChatSession, row.session_id)
        chat.context = {"last_subject": "Synthetic context", "pending_agent_action": legacy}
        await session.commit()
    response = await _post(app, {**request, "confirmation_id": "confirm-legacy"})
    assert response.status_code == 200 and "fresh confirmation" in response.json()["text"]
    assert not calls
    async with AsyncSessionLocal() as session:
        chat = await session.get(ChatSession, row.session_id)
        assert chat.context == {"last_subject": "Synthetic context", "pending_agent_action": legacy}
    assert await service._load_memory(row.session_id) == {"last_subject": "Synthetic context"}


async def test_confirm_cancel_race_has_one_terminal_decision(monkeypatch):
    service, app, request, calls = await _approval_setup(monkeypatch)
    row = (await _rows(AlfredApproval))[0]
    barrier = asyncio.Barrier(2)

    async def decide(confirm):
        await _bounded(barrier.wait())
        return await AlfredApprovalStore().decide(row.session_id, row.id, str(row.requester_user_id), confirm=confirm)

    choices = await _bounded(asyncio.gather(decide(True), decide(False)))
    stored = (await _rows(AlfredApproval))[0]
    assert stored.status in {"claimed", "cancelled"}
    assert sum(choice.status == "claimed" for choice in choices) == int(stored.status == "claimed")
    assert not calls


@pytest.mark.parametrize("state", ["claimed", "unknown"])
async def test_expired_claim_and_uncertain_outcome_never_reexecute(monkeypatch, state):
    service, app, request, calls = await _approval_setup(monkeypatch)
    row = (await _rows(AlfredApproval))[0]
    async with AsyncSessionLocal() as session:
        await session.execute(update(AlfredApproval).where(AlfredApproval.id == row.id).values(
            status=state, claimed_at=text("clock_timestamp() - interval '1 day'"), expires_at=text("clock_timestamp() - interval '1 day'")))
        await session.commit()
    for _ in range(2):
        response = await _post(app, request)
        assert response.status_code == 200
    assert not calls and (await _rows(AlfredApproval))[0].status == state


async def test_new_preview_supersedes_only_same_requester_pending_button(monkeypatch):
    service, app, request, calls = await _approval_setup(monkeypatch)
    original = (await _rows(AlfredApproval))[0]
    async with AsyncSessionLocal() as session:
        other = User(username="synthetic-other", full_name="Synthetic Other", password_hash="unused", role=UserRole.ADMIN, is_active=True)
        session.add(other)
        await session.commit()
        other_id = other.id
    store = AlfredApprovalStore()
    other = await store.create(original.session_id, str(other_id), dict(original.payload))
    replacement = await store.create(original.session_id, str(original.requester_user_id), dict(original.payload))
    rows = {row.id: row for row in await _rows(AlfredApproval)}
    assert rows[original.id].status == "cancelled"
    assert rows[other.id].status == rows[replacement.id].status == "pending"
    assert not calls


async def test_changed_tool_contract_is_rejected_before_handler(monkeypatch):
    service, app, request, calls = await _approval_setup(monkeypatch)
    service._tools["open_gate"] = replace(service._tools["open_gate"], parameters={"type": "object", "properties": {}})
    response = await _post(app, request)
    assert response.status_code == 200 and not calls
    assert response.json()["tool_results"][0]["output"]["error_code"] == "approval_contract_changed"


async def test_presentation_failure_cannot_erase_definitive_result(monkeypatch):
    service, app, request, calls = await _approval_setup(monkeypatch)
    monkeypatch.setattr(service, "_append_tool_message", AsyncMock(side_effect=RuntimeError("synthetic chat failure")))
    response = await _post(app, request)
    assert response.status_code == 500
    row = (await _rows(AlfredApproval))[0]
    assert row.status == "completed" and row.result["tool_result"]["output"]["accepted"]
    repeated = await _post(app, request)
    assert repeated.status_code == 200 and repeated.json()["tool_results"][0]["output"]["accepted"]
    assert calls == ["confirmed-inert-tool"]


@pytest.mark.parametrize("revoke_after_connect", [False, True])
async def test_real_websocket_confirmation_revalidates_actor_after_connection(monkeypatch, revoke_after_connect):
    service, app, request, calls = await _approval_setup(monkeypatch)
    approval = (await _rows(AlfredApproval))[0]

    async def authenticated_actor(session, websocket):
        return await session.get(User, approval.requester_user_id)

    class Socket:
        sent = []
        received = False

        async def accept(self):
            pass

        async def send_json(self, payload):
            self.sent.append(payload)

        async def receive_json(self):
            if self.received:
                raise WebSocketDisconnect()
            self.received = True
            if revoke_after_connect:
                async with AsyncSessionLocal() as session:
                    user = await session.get(User, approval.requester_user_id)
                    user.auth_session_version += 1
                    await session.commit()
            return {"session_id": request["session_id"], "tool_confirmation": {"confirmation_id": approval.id, "decision": "confirm"}}

    monkeypatch.setattr(ai_api, "authenticate_websocket", authenticated_actor)
    monkeypatch.setattr(ai_api, "get_runtime_config", AsyncMock(return_value=SimpleNamespace(llm_provider="local")))
    socket = Socket()
    await _bounded(ai_api.chat_websocket(socket))
    response = next(item["payload"] for item in socket.sent if item["type"] == "chat.response")
    assert len(calls) == int(not revoke_after_connect)
    assert bool(response["tool_results"]) is (not revoke_after_connect)


@pytest.mark.parametrize("stored_status, expired, expected", [
    ("pending", False, "pending"), ("pending", True, "expired"),
    ("claimed", True, "in_progress"), ("completed", False, "completed"),
    ("unknown", True, "unknown"), ("cancelled", False, "cancelled"),
])
async def test_inspection_get_never_claims_or_mutates_approval(monkeypatch, stored_status, expired, expected):
    service, app, request, calls = await _approval_setup(monkeypatch)
    row = (await _rows(AlfredApproval))[0]
    async with AsyncSessionLocal() as session:
        changes = {"status": stored_status}
        if expired:
            changes["expires_at"] = text("clock_timestamp() - interval '1 second'")
        if stored_status == "completed":
            changes["result"] = {"tool_result": {"name": "open_gate", "output": {"accepted": True, "target": "Synthetic Gate"}}}
        await session.execute(update(AlfredApproval).where(AlfredApproval.id == row.id).values(**changes))
        await session.commit()
    before = (await _rows(AlfredApproval))[0]
    monkeypatch.setattr(service._approvals, "decide", AsyncMock(side_effect=AssertionError("GET must never claim")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid", trust_env=False) as client:
        response = await client.get(f"/api/v1/ai/chat/approvals/{row.id}", params={"session_id": str(row.session_id)})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["status"] == expected
    assert bool(response.json()["pending_action"]) is (expected == "pending")
    assert bool(response.json()["result"]) is (expected == "completed")
    after = (await _rows(AlfredApproval))[0]
    assert (after.status, after.claimed_at, after.finished_at, after.updated_at, after.result) == (
        before.status, before.claimed_at, before.finished_at, before.updated_at, before.result)
    assert not calls


@pytest.mark.parametrize("mismatch", ["requester", "session", "auth_version", "role"])
async def test_inspection_get_does_not_disclose_other_or_revoked_requester_results(monkeypatch, mismatch):
    service, app, request, calls = await _approval_setup(monkeypatch)
    row = (await _rows(AlfredApproval))[0]
    requester_id = row.requester_user_id
    async with AsyncSessionLocal() as session:
        if mismatch == "requester":
            other = User(username="synthetic-inspector", full_name="Synthetic Inspector", password_hash="unused", role=UserRole.ADMIN, is_active=True)
            session.add(other)
            await session.flush()
            requester_id = other.id
        elif mismatch in {"auth_version", "role"}:
            user = await session.get(User, requester_id)
            if mismatch == "auth_version":
                user.auth_session_version += 1
            else:
                user.role = UserRole.STANDARD
        await session.commit()

    async def actor():
        async with AsyncSessionLocal() as session:
            return await session.get(User, requester_id)

    app.dependency_overrides[ai_api.require_current_user] = actor
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid", trust_env=False) as client:
        response = await client.get(f"/api/v1/ai/chat/approvals/{row.id}", params={
            "session_id": str(uuid.uuid4() if mismatch == "session" else row.session_id)})
    assert response.status_code == 200
    assert response.json() == {"status": "unavailable", "pending_action": None, "result": None}
    assert not calls and (await _rows(AlfredApproval))[0].status == "pending"


async def test_abandoned_claim_becomes_reviewable_without_reclaim_or_erasing_late_result(monkeypatch):
    from datetime import timedelta
    from sqlalchemy import func, select
    from app.services.alfred.approvals import CLAIM_REVIEW_AFTER

    _service, _app, request, calls = await _approval_setup(monkeypatch)
    store = AlfredApprovalStore()
    original = (await _rows(AlfredApproval))[0]
    claimed = await store.decide(original.session_id, original.id,
        str(original.requester_user_id), confirm=True)
    assert claimed.status == "claimed"
    async with AsyncSessionLocal() as session:
        row = await session.get(AlfredApproval, original.id)
        now = await session.scalar(select(func.clock_timestamp()))
        row.claimed_at = now - CLAIM_REVIEW_AFTER - timedelta(seconds=1)
        await session.commit()
    before = (await _rows(AlfredApproval))[0]
    response = await store.inspect(original.session_id, original.id, str(original.requester_user_id))
    assert response.status == "unknown"
    page = await store.list_for_requester(str(original.requester_user_id))
    assert page["items"][0]["status"] == "unknown"
    duplicate = await store.decide(original.session_id, original.id,
        str(original.requester_user_id), confirm=True)
    assert duplicate.status == "unknown" and calls == []
    retained = (await _rows(AlfredApproval))[0]
    assert retained.status == "claimed" and retained.claimed_at == before.claimed_at
    assert retained.operation_id == original.operation_id and retained.result is None
    assert await store.finish(claimed.approval, {"tool_result": {"name": "open_gate", "output": {"accepted": True}}})
    assert (await store.inspect(original.session_id, original.id, str(original.requester_user_id))).status == "completed"
