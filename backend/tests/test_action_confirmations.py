from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.api.dependencies import current_user
from app.api.v1 import integrations, maintenance
from app.db.session import get_db_session
from app.models import ActionConfirmation, User
from app.models.enums import UserRole
from app.modules.gate.base import GateCommandResult, GateState
from app.services import action_confirmations


def user_with_role(role: UserRole) -> User:
    return User(
        id=uuid.uuid4(),
        username=f"{role.value}-{uuid.uuid4().hex[:8]}",
        first_name="Test",
        last_name="User",
        full_name="Test User",
        password_hash="not-used",
        role=role,
        is_active=True,
    )


class FakeSession:
    def __init__(self) -> None:
        self.rows = []
        self.commits = 0

    def add(self, row) -> None:
        if getattr(row, "id", None) is None:
            row.id = uuid.uuid4()
        self.rows.append(row)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, row) -> None:
        if getattr(row, "id", None) is None:
            row.id = uuid.uuid4()


def test_confirmation_payload_hash_ignores_tokens_and_empty_optional_values() -> None:
    left = action_confirmations.confirmation_payload_hash(
        {"reason": "Open", "confirmation_token": "secret", "entity_id": None}
    )
    right = action_confirmations.confirmation_payload_hash({"reason": "Open"})

    assert left == right


async def test_create_action_confirmation_returns_secret_token_without_storing_raw_value(monkeypatch) -> None:
    monkeypatch.setattr(action_confirmations, "get_auth_secret", lambda: "test-secret")
    monkeypatch.setattr(action_confirmations, "emit_audit_log", lambda **_kwargs: None)
    user = user_with_role(UserRole.ADMIN)
    session = FakeSession()

    result = await action_confirmations.create_action_confirmation(
        session,
        user=user,
        action="gate.open",
        payload={"reason": "Open gate"},
        target_entity="Gate",
        target_label="Top Gate",
        reason="Open gate",
    )

    row = session.rows[0]
    assert result["confirmation_token"]
    assert result["confirmation_token"] not in row.token_hash
    assert row.action == "gate.open"
    assert row.actor_user_id == user.id
    assert row.payload_hash == action_confirmations.confirmation_payload_hash({"reason": "Open gate"})
    assert session.commits == 1


async def test_consume_action_confirmation_rejects_replay_and_payload_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(action_confirmations, "get_auth_secret", lambda: "test-secret")
    monkeypatch.setattr(action_confirmations, "emit_audit_log", lambda **_kwargs: None)
    user = user_with_role(UserRole.ADMIN)
    token = "token-123"
    row = ActionConfirmation(
        id=uuid.uuid4(),
        token_hash=action_confirmations.confirmation_token_hash(token),
        action="gate.open",
        payload_hash=action_confirmations.confirmation_payload_hash({"reason": "Open gate"}),
        actor_user_id=user.id,
        target_entity="Gate",
        target_label="Top Gate",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=1),
    )

    async def find(_session, _token_hash):
        return row

    monkeypatch.setattr(action_confirmations, "find_action_confirmation", find)
    session = FakeSession()

    consumed = await action_confirmations.consume_action_confirmation(
        session,
        user=user,
        action="gate.open",
        payload={"reason": "Open gate"},
        confirmation_token=token,
    )

    assert consumed.outcome == "consumed"
    assert consumed.consumed_at is not None

    with pytest.raises(action_confirmations.ActionConfirmationError) as replay:
        await action_confirmations.consume_action_confirmation(
            session,
            user=user,
            action="gate.open",
            payload={"reason": "Open gate"},
            confirmation_token=token,
        )
    assert replay.value.status_code == 409

    fresh_row = ActionConfirmation(
        id=uuid.uuid4(),
        token_hash=action_confirmations.confirmation_token_hash(token),
        action="gate.open",
        payload_hash=action_confirmations.confirmation_payload_hash({"reason": "Open gate"}),
        actor_user_id=user.id,
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=1),
    )

    async def find_fresh(_session, _token_hash):
        return fresh_row

    monkeypatch.setattr(action_confirmations, "find_action_confirmation", find_fresh)
    with pytest.raises(action_confirmations.ActionConfirmationError) as mismatch:
        await action_confirmations.consume_action_confirmation(
            session,
            user=user,
            action="gate.open",
            payload={"reason": "Different"},
            confirmation_token=token,
        )
    assert mismatch.value.status_code == 403
    assert fresh_row.outcome == "rejected"


def app_for_user(user: User) -> FastAPI:
    app = FastAPI()
    app.include_router(integrations.router, prefix="/api/v1/integrations")
    app.include_router(maintenance.router, prefix="/api/v1/maintenance")

    async def override_current_user() -> User:
        return user

    async def override_db_session():
        yield SimpleNamespace()

    app.dependency_overrides[current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_db_session
    return app


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/integrations/gate/open", {"reason": "test"}),
        (
            "/api/v1/integrations/cover/command",
            {"entity_id": "cover.main_garage", "action": "open", "reason": "test"},
        ),
        ("/api/v1/integrations/announcements/say", {"message": "hello"}),
        ("/api/v1/maintenance/enable", {"reason": "test"}),
        ("/api/v1/maintenance/disable", {"reason": "test"}),
    ],
)
async def test_real_world_action_routes_deny_standard_users(path: str, body: dict) -> None:
    transport = httpx.ASGITransport(app=app_for_user(user_with_role(UserRole.STANDARD)))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(path, json=body)

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required"


async def test_gate_open_requires_server_confirmation_for_admin(monkeypatch) -> None:
    async def inactive_maintenance() -> bool:
        return False

    class FailingGate:
        async def open_gate(self, _reason: str):
            raise AssertionError("Gate must not open without server confirmation.")

    monkeypatch.setattr(integrations, "is_maintenance_mode_active", inactive_maintenance)
    monkeypatch.setattr(integrations, "HomeAssistantGateController", lambda: FailingGate())
    transport = httpx.ASGITransport(app=app_for_user(user_with_role(UserRole.ADMIN)))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/integrations/gate/open", json={"reason": "test"})

    assert response.status_code == 428
    assert response.json()["detail"] == "Server-side confirmation is required for this action."


async def test_gate_open_succeeds_with_admin_and_consumed_confirmation(monkeypatch) -> None:
    consumed = {}

    async def inactive_maintenance() -> bool:
        return False

    async def consume(_session, **kwargs) -> None:
        consumed.update(kwargs)

    class FakeGate:
        async def open_gate(self, reason: str):
            return GateCommandResult(True, GateState.OPEN, reason)

    monkeypatch.setattr(integrations, "is_maintenance_mode_active", inactive_maintenance)
    monkeypatch.setattr(integrations, "consume_action_confirmation", consume)
    monkeypatch.setattr(integrations, "HomeAssistantGateController", lambda: FakeGate())
    monkeypatch.setattr(integrations, "emit_audit_log", lambda **_kwargs: None)
    transport = httpx.ASGITransport(app=app_for_user(user_with_role(UserRole.ADMIN)))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/integrations/gate/open",
            json={"reason": "test", "confirmation_token": "server-token"},
        )

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert consumed["action"] == "gate.open"
    assert consumed["payload"] == {"reason": "test"}
    assert consumed["confirmation_token"] == "server-token"


async def test_maintenance_toggle_succeeds_with_admin_and_consumed_confirmation(monkeypatch) -> None:
    consumed = {}

    async def consume(_session, **kwargs) -> None:
        consumed.update(kwargs)

    async def set_mode(active: bool, **_kwargs):
        return {"is_active": active}

    monkeypatch.setattr(maintenance, "consume_action_confirmation", consume)
    monkeypatch.setattr(maintenance, "set_mode", set_mode)
    transport = httpx.ASGITransport(app=app_for_user(user_with_role(UserRole.ADMIN)))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/maintenance/enable",
            json={"reason": "test", "confirmation_token": "server-token"},
        )

    assert response.status_code == 200
    assert response.json()["is_active"] is True
    assert consumed["action"] == "maintenance_mode.enable"
    assert consumed["payload"] == {"reason": "test"}
    assert consumed["confirmation_token"] == "server-token"
