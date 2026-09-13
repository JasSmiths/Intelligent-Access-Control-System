"""HTTP contracts use inert owners; real journal races have PostgreSQL tests."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.api.dependencies import current_user
from app.api.v1 import action_confirmations as confirmation_api, integrations
from app.db.session import get_db_session
from app.models import ActionConfirmation, User
from app.models.enums import UserRole
from app.modules.gate.base import CommandDelivery, GateState
from app.services import action_confirmations
from app.services.gate_commands import GateCommandOutcome


def actor(role=UserRole.ADMIN):
    return User(id=uuid.uuid4(), username="synthetic-admin", full_name="Synthetic Admin",
                role=role, is_active=True, auth_session_version=3, password_hash="inert")


def application(user):
    app = FastAPI()
    app.include_router(integrations.router, prefix="/api/v1/integrations")
    app.include_router(confirmation_api.router, prefix="/api/v1/action-confirmations")
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_db_session] = lambda: SimpleNamespace()
    return app


@pytest.mark.parametrize("target", [None, "entry", "side-gate"])
async def test_manual_request_keeps_scope_and_reuses_confirmation_identity(monkeypatch, target):
    user, identity = actor(), uuid.uuid4()
    plan = {"selected_device_key": target, "targets": [{"target_device_id": "synthetic"}]}
    devices = SimpleNamespace(preview_gate_open=AsyncMock(return_value=plan))
    approval = SimpleNamespace(id=identity, expires_at=datetime.now(tz=UTC) + timedelta(minutes=1))
    consume = AsyncMock(return_value=approval)
    sent = []

    async def send(intent):
        sent.append(intent)
        now = datetime.now(tz=UTC)
        return GateCommandOutcome(intent, False, GateState.UNKNOWN, "Response lost", now, now,
                                  command_id=str(uuid.uuid4()), delivery=CommandDelivery.UNKNOWN)

    audit = AsyncMock()
    monkeypatch.setattr(integrations, "get_access_device_service", lambda: devices)
    monkeypatch.setattr(integrations, "get_gate_command_coordinator", lambda: SimpleNamespace(execute_open=send))
    monkeypatch.setattr(integrations, "require_confirmed_action", consume)
    monkeypatch.setattr(integrations, "is_maintenance_mode_active", AsyncMock(return_value=False))
    monkeypatch.setattr(integrations, "write_audit_log", audit)
    body = {"reason": "Synthetic manual action", "confirmation_token": "synthetic-token"}
    if target is not None:
        body["target_device_key"] = target
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(user)), base_url="http://test") as client:
        response = await client.post("/api/v1/integrations/gate/open", json=body)
    assert response.status_code == 503
    assert response.json()["delivery"] == "unknown" and response.json()["requires_reconciliation"] is True
    assert len(sent) == 1
    assert sent[0].target_device_key == target and sent[0].target_plan == plan
    assert sent[0].intent_id == sent[0].idempotency_key == str(identity)
    assert sent[0].actor_user_id == str(user.id) and sent[0].auth_version == 3
    assert sent[0].require_admission is False
    assert consume.await_args.kwargs["expected_hardware_plan"] == plan
    assert audit.await_args.kwargs["outcome"] == "uncertain"


@pytest.mark.parametrize("kind", ["gate", "cover"])
@pytest.mark.parametrize("selector", ["row", "intent"])
@pytest.mark.parametrize("role,expected", [(UserRole.ADMIN, 200), (UserRole.STANDARD, 403)])
async def test_receipt_lookup_is_admin_read_only(monkeypatch, kind, selector, role, expected):
    identity = uuid.uuid4()
    read = AsyncMock(return_value={"delivery": "unknown", "requires_reconciliation": True})
    send = AsyncMock(side_effect=AssertionError("Receipt inspection must not dispatch"))
    monkeypatch.setattr(integrations, "get_gate_command_coordinator", lambda: SimpleNamespace(get_receipt=read, execute_open=send))
    monkeypatch.setattr(integrations, "get_access_device_service", lambda: SimpleNamespace(command_receipt=read, command_device=send))
    suffix = f"/{identity}" if selector == "row" else f"?intent_id={identity}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(actor(role))), base_url="http://test") as client:
        response = await client.get(f"/api/v1/integrations/{kind}/commands{suffix}")
    assert response.status_code == expected
    assert send.await_count == 0
    assert read.await_count == int(expected == 200)
    if expected == 200:
        assert response.json()["delivery"] == "unknown"
        if selector == "intent":
            assert read.await_args.kwargs == {"intent_id": str(identity)}


@pytest.mark.parametrize("saved", [None, {"targets": ["old"]}, {"targets": ["current"], "admission_device_key": "changed"}])
async def test_changed_or_legacy_hardware_confirmation_requires_fresh_preview(monkeypatch, saved):
    user = actor()
    row = ActionConfirmation(id=uuid.uuid4(), token_hash="synthetic", action="gate.open",
        actor_user_id=user.id, payload_hash=action_confirmations.confirmation_payload_hash({"reason": "test"}),
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=1), metadata_={"hardware_plan": saved})
    monkeypatch.setattr(action_confirmations, "find_action_confirmation", AsyncMock(return_value=row))
    monkeypatch.setattr(action_confirmations, "confirmation_token_hash", lambda _: "synthetic")
    monkeypatch.setattr(action_confirmations, "emit_audit_log", lambda **_: None)
    with pytest.raises(action_confirmations.ActionConfirmationError, match="fresh confirmation"):
        await action_confirmations.consume_action_confirmation(SimpleNamespace(commit=AsyncMock()), user=user,
            action="gate.open", payload={"reason": "test"}, confirmation_token="synthetic",
            expected_hardware_plan={"targets": ["current"]})
    assert row.outcome == "rejected"


async def test_client_cannot_supply_the_hardware_plan(monkeypatch):
    canonical = {"targets": ["server-resolved"]}
    create = AsyncMock(return_value={"confirmation_id": str(uuid.uuid4())})
    preview = AsyncMock(return_value=canonical)
    monkeypatch.setattr(confirmation_api, "get_access_device_service", lambda: SimpleNamespace(preview_gate_open=preview))
    monkeypatch.setattr(confirmation_api, "create_action_confirmation", create)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(actor())), base_url="http://test") as client:
        response = await client.post("/api/v1/action-confirmations", json={"action": "gate.open",
            "payload": {"target_device_key": "side-gate"}, "metadata": {"hardware_plan": {"targets": ["forged"]}}})
    assert response.status_code == 200
    assert create.await_args.kwargs["metadata"]["hardware_plan"] == canonical
    assert preview.await_args.kwargs == {"target_device_key": "side-gate"}
