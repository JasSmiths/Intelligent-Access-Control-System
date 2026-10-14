from datetime import UTC, datetime
import uuid

import httpx
from fastapi import FastAPI

from app.api.dependencies import current_user
from app.api.v1 import users as users_api
from app.db.session import get_db_session
from app.models import User
from app.models.enums import UserRole
from app.services.auth import serialize_user


def make_user(role: UserRole, *, username: str | None = None) -> User:
    now = datetime(2026, 5, 4, 17, 0, tzinfo=UTC)
    name = username or f"{role.value}-{uuid.uuid4().hex[:8]}"
    return User(
        id=uuid.uuid4(),
        username=name,
        first_name=name.title(),
        last_name="User",
        full_name=f"{name.title()} User",
        email=f"{name}@example.com",
        mobile_phone_number="+447700900123",
        password_hash="not-used",
        role=role,
        is_active=True,
        last_login_at=now,
        preferences={"sidebarCollapsed": False},
        created_at=now,
        updated_at=now,
    )


def test_serialize_user_can_omit_profile_photo() -> None:
    user = make_user(UserRole.ADMIN, username="admin")
    user.profile_photo_data_url = "data:image/png;base64,avatar"

    assert serialize_user(user)["profile_photo_data_url"] is None
    assert serialize_user(user)["profile_photo_url"] == f"/api/v1/users/{user.id}/photo?v={int(user.updated_at.timestamp())}"
    assert serialize_user(user, include_photo=True)["profile_photo_data_url"] == "data:image/png;base64,avatar"


class FakeScalarResult:
    def __init__(self, rows) -> None:
        self._rows = rows

    def all(self):
        return self._rows


class FakeUserSession:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.committed = False

    async def scalars(self, _statement):
        return FakeScalarResult(self.rows)

    async def get(self, _model, row_id):
        for row in self.rows:
            if row.id == row_id:
                return row
        return None

    async def commit(self):
        self.committed = True


def app_for_user(user: User, session: FakeUserSession) -> FastAPI:
    app = FastAPI()
    app.include_router(users_api.router, prefix="/api/v1/users")

    async def override_current_user() -> User:
        return user

    async def override_db_session():
        yield session

    app.dependency_overrides[current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_db_session
    return app


async def test_user_roster_denies_standard_users() -> None:
    app = app_for_user(make_user(UserRole.STANDARD), FakeUserSession([make_user(UserRole.ADMIN)]))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/users")

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required"


async def test_user_roster_allows_admin_users() -> None:
    admin = make_user(UserRole.ADMIN, username="admin")
    listed = make_user(UserRole.STANDARD, username="standard")
    app = app_for_user(admin, FakeUserSession([admin, listed]))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/users")

    assert response.status_code == 200
    payload = response.json()
    assert [user["username"] for user in payload] == ["admin", "standard"]
    assert payload[1]["email"] == "standard@example.com"
    assert payload[1]["mobile_phone_number"] == "+447700900123"


async def test_reset_password_adapter_passes_exact_confirmation_and_prepared_hash(monkeypatch) -> None:
    admin = make_user(UserRole.ADMIN, username="admin")
    target = make_user(UserRole.STANDARD, username="target")
    session = FakeUserSession([target])
    forwarded = {}
    async def owner(_session, proof, target_id, **kwargs):
        assert _session is session and proof.id == admin.id and target_id == target.id
        forwarded.update(kwargs)
    async def fake_hash(password):
        assert password == "temporary-password"
        return "synthetic-hash"
    monkeypatch.setattr(users_api.user_accounts, "reset_account_password", owner)
    monkeypatch.setattr(users_api, "hash_password_async", fake_hash)
    monkeypatch.setattr(users_api, "generate_temporary_password", lambda: "temporary-password")
    app = app_for_user(admin, session)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Authorization": "Bearer synthetic-unused-private-token"}) as client:
        response = await client.post(f"/api/v1/users/{target.id}/reset-password",
            json={"generate_password": True, "confirmation_token": "server-token"})
    assert response.status_code == 200 and response.json()["temporary_password"] == "temporary-password"
    assert forwarded == {"payload": {"user_id": str(target.id), "generate_password": True},
        "token": "server-token", "password_hash": "synthetic-hash", "generated": True}
