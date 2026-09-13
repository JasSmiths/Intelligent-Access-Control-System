"""Messaging confirmation bridge contracts against disposable PostgreSQL.

The provider adapter is represented only by the already accepted confirmation
arguments.  The bridge, chat confirmation path, and durable Alfred approval
store remain real; the registered gate action, provider loop, optional memory
reflection, and realtime publication are inert.
"""
from test_recovery_boundaries import isolated_resources as isolated_resources
from test_recovery_boundaries import _approval_setup, _bounded, _rows

import asyncio
from dataclasses import replace
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.ai.context import get_chat_tool_context
from app.db.session import AsyncSessionLocal
from app.models import AlfredApproval, User
from app.models.enums import UserRole
from app.services import messaging_bridge
from app.services.messaging_bridge import MessagingBridgeService


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def messaging_confirmation_resources(isolated_resources):
    async def clear_rows():
        async with AsyncSessionLocal() as session:
            await session.execute(text(
                "TRUNCATE alfred_approvals, chat_messages, chat_sessions, "
                "people, users, audit_logs, notification_runs CASCADE"
            ))
            await session.commit()

    await clear_rows()
    yield
    await clear_rows()


async def bridge_setup(monkeypatch):
    service, _app, request, calls = await _approval_setup(monkeypatch)
    approval = (await _rows(AlfredApproval))[0]
    monkeypatch.setattr(messaging_bridge, "chat_service", service)
    bridge = MessagingBridgeService()
    confirmation = {
        "session_id": request["session_id"],
        "confirmation_id": request["confirmation_id"],
        "decision": "confirm",
        "user_id": str(approval.requester_user_id),
        "user_role": "admin",
        "provider": "discord",
    }
    return service, bridge, approval, confirmation, calls


async def test_bridge_duplicate_concurrent_confirmation_invokes_registered_action_once(monkeypatch):
    service, bridge, approval, confirmation, calls = await bridge_setup(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    contexts = []
    original = service._tools["open_gate"].handler

    async def held_action(arguments):
        contexts.append(dict(get_chat_tool_context()))
        entered.set()
        await _bounded(release.wait())
        return await original(arguments)

    service._tools["open_gate"] = replace(service._tools["open_gate"], handler=held_action)
    first_task = asyncio.create_task(bridge.handle_confirmation(**confirmation))
    second_task = None
    try:
        await _bounded(entered.wait())
        second_task = asyncio.create_task(bridge.handle_confirmation(**confirmation))
        second = await _bounded(second_task)
        assert "already claimed" in second.response_text.lower()
        assert calls == []
    finally:
        release.set()
        first = await _bounded(first_task)
        if second_task is not None and not second_task.done():
            second_task.cancel()
        if second_task is not None:
            await asyncio.gather(second_task, return_exceptions=True)

    assert first.session_id == confirmation["session_id"]
    assert calls == ["confirmed-inert-tool"]
    assert len(contexts) == 1
    row = (await _rows(AlfredApproval))[0]
    assert row.id == approval.id
    assert row.status == "completed"
    assert contexts[0]["intent_id"] == str(row.operation_id)
    assert contexts[0]["idempotency_key"] == str(row.operation_id)
    assert contexts[0]["approval"]["operation_id"] == str(row.operation_id)


async def test_bridge_sharing_session_does_not_allow_another_admin_to_confirm(monkeypatch):
    _service, bridge, approval, confirmation, calls = await bridge_setup(monkeypatch)
    async with AsyncSessionLocal() as session:
        other = User(
            username="synthetic-other-admin-" + uuid.uuid4().hex,
            first_name="Other",
            last_name="Admin",
            full_name="Other Admin",
            password_hash="inert-unused",
            role=UserRole.ADMIN,
            is_active=True,
        )
        session.add(other)
        await session.flush()
        other_id = other.id
        await session.commit()

    result = await bridge.handle_confirmation(
        **{**confirmation, "user_id": str(other_id), "user_role": "admin"}
    )

    assert "fresh confirmation" in result.response_text.lower()
    assert calls == []
    row = (await _rows(AlfredApproval))[0]
    assert row.id == approval.id
    assert row.status == "pending"
    assert row.requester_user_id != other_id
    assert row.operation_id == approval.operation_id


async def test_bridge_demoted_requester_is_denied_by_current_approval_authority(monkeypatch):
    _service, bridge, approval, confirmation, calls = await bridge_setup(monkeypatch)
    async with AsyncSessionLocal() as session:
        requester = await session.get(User, approval.requester_user_id)
        assert requester is not None
        requester.role = UserRole.STANDARD
        await session.commit()

    result = await bridge.handle_confirmation(**confirmation)

    assert "fresh confirmation" in result.response_text.lower()
    assert calls == []
    row = (await _rows(AlfredApproval))[0]
    assert row.id == approval.id
    assert row.status == "pending"
    assert row.operation_id == approval.operation_id


async def test_bridge_preserves_original_operation_identity_through_confirmation(monkeypatch):
    service, bridge, approval, confirmation, calls = await bridge_setup(monkeypatch)
    captured = []
    original = service._tools["open_gate"].handler

    async def capture_action(arguments):
        captured.append(dict(get_chat_tool_context()))
        return await original(arguments)

    service._tools["open_gate"] = replace(service._tools["open_gate"], handler=capture_action)
    result = await bridge.handle_confirmation(**confirmation)

    assert result.session_id == confirmation["session_id"]
    assert calls == ["confirmed-inert-tool"]
    assert len(captured) == 1
    row = (await _rows(AlfredApproval))[0]
    operation_id = str(approval.operation_id)
    assert row.id == approval.id
    assert row.operation_id == approval.operation_id
    assert row.status == "completed"
    assert captured[0]["intent_id"] == operation_id
    assert captured[0]["idempotency_key"] == operation_id
    assert captured[0]["approval"]["confirmation_id"] == approval.id
    assert captured[0]["approval"]["operation_id"] == operation_id
