from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.api.dependencies import current_user
from app.api.v1 import dependency_updates, integrations, maintenance, settings, unifi_protect
from app.db.session import get_db_session
from app.models import ActionConfirmation, User
from app.models.enums import UserRole
from app.modules.gate.base import GateState
from app.services import action_confirmations
from app.services.gate_commands import GateCommandIntent, GateCommandOutcome


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


def accepted_gate_outcome(intent: GateCommandIntent) -> GateCommandOutcome:
    occurred_at = datetime(2026, 5, 3, 9, 15, tzinfo=UTC)
    return GateCommandOutcome(
        intent=intent,
        accepted=True,
        state=GateState.OPEN,
        detail=intent.reason,
        mechanically_confirmed=True,
        started_at=occurred_at,
        completed_at=occurred_at,
    )


class FakeSession:
    def __init__(self) -> None:
        self.rows: list[Any] = []
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
    assert row.outcome == "consumed"
    assert session.commits == 1

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
    app.include_router(dependency_updates.router, prefix="/api/v1/dependency-updates")
    app.include_router(integrations.router, prefix="/api/v1/integrations")
    app.include_router(maintenance.router, prefix="/api/v1/maintenance")
    app.include_router(settings.router, prefix="/api/v1/settings")
    app.include_router(unifi_protect.router, prefix="/api/v1/integrations/unifi-protect")

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

    class FailingCoordinator:
        async def execute_open(self, _intent):
            raise AssertionError("Gate must not open without server confirmation.")

    monkeypatch.setattr(integrations, "is_maintenance_mode_active", inactive_maintenance)
    monkeypatch.setattr(integrations, "get_gate_command_coordinator", lambda: FailingCoordinator())
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

    class FakeCoordinator:
        async def execute_open(self, intent):
            return accepted_gate_outcome(intent)

    monkeypatch.setattr(integrations, "is_maintenance_mode_active", inactive_maintenance)
    monkeypatch.setattr(integrations, "require_confirmed_action", consume)
    monkeypatch.setattr(integrations, "get_gate_command_coordinator", lambda: FakeCoordinator())
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


async def test_dependency_apply_requires_server_confirmation_for_admin(monkeypatch) -> None:
    class FailingDependencyService:
        async def start_apply_job(self, *_args, **_kwargs):
            raise AssertionError("Dependency apply must not start without server confirmation.")

    monkeypatch.setattr(dependency_updates, "get_dependency_update_service", lambda: FailingDependencyService())
    dependency_id = uuid.uuid4()
    transport = httpx.ASGITransport(app=app_for_user(user_with_role(UserRole.ADMIN)))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/api/v1/dependency-updates/packages/{dependency_id}/apply", json={})

    assert response.status_code == 428
    assert response.json()["detail"] == "Server-side confirmation is required for this action."


async def test_dependency_apply_consumes_confirmation(monkeypatch) -> None:
    consumed = {}
    dependency_id = uuid.uuid4()

    async def consume(_session, **kwargs) -> None:
        consumed.update(kwargs)

    class FakeDependencyService:
        async def start_apply_job(self, package_id, **kwargs):
            return {"id": "job-1", "dependency_id": str(package_id), "confirmed": kwargs["confirmed"]}

    monkeypatch.setattr(dependency_updates, "require_confirmed_action", consume)
    monkeypatch.setattr(dependency_updates, "get_dependency_update_service", lambda: FakeDependencyService())
    transport = httpx.ASGITransport(app=app_for_user(user_with_role(UserRole.ADMIN)))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/dependency-updates/packages/{dependency_id}/apply",
            json={"target_version": "1.2.3", "confirmation_token": "server-token"},
        )

    assert response.status_code == 200
    assert response.json()["confirmed"] is True
    assert consumed["action"] == "dependency_update.apply"
    assert consumed["payload"] == {"dependency_id": str(dependency_id), "target_version": "1.2.3"}
    assert consumed["confirmation_token"] == "server-token"


async def test_dependency_storage_validate_consumes_body_confirmation(monkeypatch) -> None:
    consumed = {}

    async def consume(_session, **kwargs) -> None:
        consumed.update(kwargs)

    class FakeDependencyService:
        async def validate_storage(self):
            return {"ok": True}

    monkeypatch.setattr(dependency_updates, "require_confirmed_action", consume)
    monkeypatch.setattr(dependency_updates, "get_dependency_update_service", lambda: FakeDependencyService())
    transport = httpx.ASGITransport(app=app_for_user(user_with_role(UserRole.ADMIN)))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/dependency-updates/storage/validate",
            json={"confirmation_token": "server-token"},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert consumed["action"] == "dependency_update.storage.validate"
    assert consumed["payload"] == {}
    assert consumed["confirmation_token"] == "server-token"


async def test_auth_secret_rotation_consumes_confirmation(monkeypatch) -> None:
    consumed = {}

    async def consume(_session, **kwargs) -> None:
        consumed.update(kwargs)

    async def rotate(**kwargs):
        return {"rotated": True, "confirmed": kwargs["confirmed"]}

    monkeypatch.setattr(settings, "require_confirmed_action", consume)
    monkeypatch.setattr(settings, "rotate_auth_secret", rotate)
    transport = httpx.ASGITransport(app=app_for_user(user_with_role(UserRole.ADMIN)))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/settings/security/auth-secret/rotate",
            json={"new_secret": "replacement-secret", "confirmation_token": "server-token"},
        )

    assert response.status_code == 200
    assert response.json()["confirmed"] is True
    assert consumed["action"] == "auth_secret.rotate"
    assert consumed["payload"] == {"new_secret_provided": True}
    assert consumed["confirmation_token"] == "server-token"


async def test_unifi_protect_apply_consumes_confirmation_not_client_boolean(monkeypatch) -> None:
    consumed = {}
    audit_rows: list[dict[str, Any]] = []

    async def consume(_session, **kwargs) -> None:
        consumed.update(kwargs)

    async def audit(_session, **kwargs) -> None:
        audit_rows.append(kwargs)

    class FakeProtectUpdateService:
        async def apply(self, **kwargs):
            return {"applied": True, "confirmed": kwargs["confirmed"], "target_version": kwargs["target_version"]}

    monkeypatch.setattr(unifi_protect, "require_unifi_confirmation", consume)
    monkeypatch.setattr(unifi_protect, "write_unifi_audit", audit)
    monkeypatch.setattr(unifi_protect, "get_unifi_protect_update_service", lambda: FakeProtectUpdateService())
    transport = httpx.ASGITransport(app=app_for_user(user_with_role(UserRole.ADMIN)))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/integrations/unifi-protect/update/apply",
            json={"target_version": "6.0.0", "confirmed": False, "confirmation_token": "server-token"},
        )

    assert response.status_code == 200
    assert response.json()["confirmed"] is True
    assert consumed["action"] == "unifi_protect.update.apply"
    assert consumed["payload"] == {"target_version": "6.0.0"}
    assert consumed["confirmation_token"] == "server-token"
    assert audit_rows[0]["action"] == "unifi_protect.update.apply"
