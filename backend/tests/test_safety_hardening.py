from types import SimpleNamespace
import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.api.dependencies import current_user
from app.api.router import api_router
from app.db.session import get_db_session
from app.models import User
from app.models.enums import UserRole
from app.services.access_events import get_access_event_service


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


def app_for_user(user: User) -> FastAPI:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def override_current_user() -> User:
        return user

    async def override_db_session():
        yield SimpleNamespace()

    class FailingAccessEventService:
        async def enqueue_plate_read(self, *_args, **_kwargs):
            raise AssertionError("Simulation injection must not run in this test.")

    app.dependency_overrides[current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_access_event_service] = lambda: FailingAccessEventService()
    return app


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/v1/schedules", {"name": "Standard Schedule", "time_blocks": {}}),
        ("POST", "/api/v1/groups", {"name": "Standard Group", "category": "family"}),
        ("POST", "/api/v1/vehicles", {"registration_number": "STD123"}),
        ("POST", "/api/v1/people", {"first_name": "Std", "last_name": "User"}),
        ("POST", "/api/v1/visitor-passes", {"visitor_name": "Visitor"}),
        ("POST", "/api/v1/access-devices", {"key": "gate_1", "kind": "gate", "name": "Gate"}),
        ("POST", "/api/v1/integrations/esphome/devices", {"name": "Gate", "host": "127.0.0.1"}),
        ("POST", "/api/v1/simulation/arrival/STD123", {}),
        ("GET", "/api/v1/integrations/unifi-protect/backups", None),
    ],
)
async def test_standard_users_are_denied_access_policy_mutations(method: str, path: str, body: dict | None) -> None:
    transport = httpx.ASGITransport(app=app_for_user(user_with_role(UserRole.STANDARD)))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(method, path, json=body)

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/v1/schedules", {"name": "Confirmed Schedule", "time_blocks": {}}),
        ("POST", "/api/v1/groups", {"name": "Confirmed Group", "category": "family"}),
        ("POST", "/api/v1/visitor-passes", {"visitor_name": "Visitor"}),
        ("POST", "/api/v1/access-devices", {"key": "gate_1", "kind": "gate", "name": "Gate"}),
        ("POST", "/api/v1/integrations/esphome/devices/gate_1/test", {}),
        ("POST", "/api/v1/simulation/arrival/CONF123", {}),
        ("DELETE", "/api/v1/telemetry/purge", {"scope": "telemetry"}),
    ],
)
async def test_safety_critical_admin_routes_require_server_confirmation(method: str, path: str, body: dict | None) -> None:
    transport = httpx.ASGITransport(app=app_for_user(user_with_role(UserRole.ADMIN)))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(method, path, json=body)

    assert response.status_code == 428
    assert response.json()["detail"] == "Server-side confirmation is required for this action."
