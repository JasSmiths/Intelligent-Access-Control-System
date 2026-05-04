from datetime import UTC, datetime
import uuid

import httpx
from fastapi import FastAPI

from app.api.dependencies import current_user
from app.api.v1 import users as users_api
from app.db.session import get_db_session
from app.models import User
from app.models.enums import UserRole


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


class FakeScalarResult:
    def __init__(self, rows) -> None:
        self._rows = rows

    def all(self):
        return self._rows


class FakeUserSession:
    def __init__(self, rows) -> None:
        self.rows = rows

    async def scalars(self, _statement):
        return FakeScalarResult(self.rows)


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
