"""Presentation and input-binding checks; atomic claims run in isolated PostgreSQL."""

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from app.services.alfred.approvals import Approval, ApprovalDecision
from app.services.chat import ChatService, IntentRoute, PENDING_SECRET_MARKER
from app.services.chat_contracts import ChatTurnResult


def approval(*, status="pending", payload=None, result=None):
    return Approval("confirm-synthetic", uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 0,
                    status, payload or {}, result, datetime.now(tz=UTC) + timedelta(minutes=10))


@pytest.mark.asyncio
@pytest.mark.parametrize("session_id", [None, "", "not-a-session"])
async def test_malformed_confirmation_never_creates_a_session_or_claims(session_id, monkeypatch):
    service = ChatService()
    store = SimpleNamespace(decide=AsyncMock())
    monkeypatch.setattr(service, "_approvals", store)
    monkeypatch.setattr(service, "_ensure_session", AsyncMock(side_effect=AssertionError("No conversation creation")))
    result = await service.handle_tool_confirmation(session_id=session_id, confirmation_id="confirm-synthetic",
                                                   tool_name="open_gate", arguments={"confirm": True})
    assert "fresh confirmation" in result.text
    store.decide.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_button_reads_requester_bound_store_without_memory_writes(monkeypatch):
    service = ChatService()
    row = approval(payload={"tool_name": "open_gate", "preview_output": {"target": "Synthetic Gate"}})
    store = SimpleNamespace(pending=AsyncMock(return_value=row))
    monkeypatch.setattr(service, "_approvals", store)
    monkeypatch.setattr(service, "_save_memory", AsyncMock(side_effect=AssertionError("Approval must not write chat memory")))
    public = await service._pending_action_for_response(row.session_id, user_id=str(row.requester_user_id))
    store.pending.assert_awaited_once_with(row.session_id, str(row.requester_user_id))
    assert public["confirmation_id"] == row.id and public["target"] == "Synthetic Gate"
    assert "arguments" not in public


def test_retained_result_keeps_artifact_urls_and_protects_secret_arguments():
    service = ChatService()
    tool_result = {"name": "update_system_settings", "arguments": {"api_key": "synthetic-private-value"},
                   "output": {"updated": True, "file_url": "/api/v1/ai/chat/files/synthetic-artifact"}}
    turn = ChatTurnResult(str(uuid.uuid4()), "local", "Completed", [tool_result], [])
    protected = service._protect_pending_value(asdict(turn), key="turn", tool_name="update_system_settings")
    assert PENDING_SECRET_MARKER in protected["tool_results"][0]["arguments"]["api_key"]
    assert protected["tool_results"][0]["output"]["file_url"].endswith("/synthetic-artifact")
    row = approval(status="completed", result={"turn": protected})
    restored = service._approval_response(ApprovalDecision("completed", row), str(row.session_id))
    assert restored == turn


@pytest.mark.asyncio
async def test_interrupted_claim_is_recorded_unknown_without_retry(monkeypatch):
    service = ChatService()
    row = approval(status="claimed")
    execute = AsyncMock(side_effect=asyncio.CancelledError())
    finish = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "_execute_claimed_approval", execute)
    monkeypatch.setattr(service, "_approvals", SimpleNamespace(finish=finish))
    with pytest.raises(asyncio.CancelledError):
        await service._run_claimed_approval(row, client_context={})
    execute.assert_awaited_once()
    finish.assert_awaited_once_with(row, {"error_code": "approval_interrupted"}, unknown=True)


@pytest.mark.asyncio
async def test_preview_binds_existing_intent_and_never_uses_actor_payload_as_approval(monkeypatch):
    service = ChatService()
    operation_id, session_id, requester = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    expected_operation_id = operation_id
    captured = {}

    async def create(session, user_id, payload, *, operation_id):
        captured.update(payload)
        assert session == session_id and user_id == str(requester)
        assert operation_id == expected_operation_id
        return Approval("confirm-synthetic", operation_id, session, requester, 0, "pending",
                        payload, None, datetime.now(tz=UTC) + timedelta(minutes=10))

    monkeypatch.setattr(service, "_approvals", SimpleNamespace(create=create))
    public = await service._store_pending_agent_action(
        session_id,
        {"name": "open_gate", "arguments": {"target": "Synthetic Gate", "confirm": False},
         "output": {"requires_confirmation": True, "target": "Synthetic Gate", "intent_id": str(operation_id),
                    "idempotency_key": "existing-synthetic-intent"}},
        [{"name": "open_gate", "arguments": {"api_key": "synthetic-duplicate-secret"}, "output": {}}],
        IntentRoute(("Gate_Hardware",), 1, False, "test"), [service._tools["open_gate"]],
        provider_name="local", user_message="Synthetic preview", user_id=str(requester),
        actor_context={"user": {"role": "admin"}}, iteration=0,
    )
    assert public["confirmation_id"] == "confirm-synthetic"
    assert captured["idempotency_key"] == "existing-synthetic-intent"
    assert captured["arguments"]["confirm"] is False and "actor_context" not in captured
    assert PENDING_SECRET_MARKER in captured["tool_results"][0]["arguments"]["api_key"]
    assert "synthetic-duplicate-secret" not in str(captured)
    assert captured["tool_contract"] == service._approval_tool_contract("open_gate")


def test_inspection_contract_fixture_matches_typed_response_model():
    import json
    from pathlib import Path

    from app.api.v1.ai import ChatApprovalInspectionResponse

    canonical = Path(__file__).parent / "contracts/fixtures/alfred/approval_inspection.json"
    frontend_copy = Path(__file__).resolve().parents[2] / "frontend/src/api/fixtures/approvalInspection.generated.json"
    assert frontend_copy.read_text() == canonical.read_text(), "Regenerate the frontend approval fixture from the canonical backend contract"
    fixtures = json.loads(canonical.read_text())
    for name, payload in fixtures.items():
        assert payload["status"] == name
        assert ChatApprovalInspectionResponse(**payload).model_dump() == payload


@pytest.mark.parametrize("age_seconds,expected", [(599.999999, "in_progress"), (600, "unknown"), (601, "unknown")])
def test_claim_review_age_is_not_a_new_execution_permission(age_seconds, expected):
    from app.services.alfred.approvals import approval_status
    now = datetime.now(tz=UTC)
    row = SimpleNamespace(status="claimed", claimed_at=now-timedelta(seconds=age_seconds), updated_at=now)
    assert approval_status(row, now) == expected
    assert row.status == "claimed"
