from datetime import UTC, datetime
from types import SimpleNamespace
import uuid

from app.models import User
from app.models.enums import UserRole
from app.services import auth


class EmptyScalarRows:
    def all(self):
        return []


class FakeSession:
    def __init__(self, user: User) -> None:
        self.user = user

    async def get(self, _model, user_id):
        return self.user if user_id == self.user.id else None

    async def scalars(self, _statement):
        return EmptyScalarRows()

    async def scalar(self, _statement):
        return None


def make_user() -> User:
    return User(
        id=uuid.uuid4(),
        username="admin",
        first_name="Admin",
        last_name="User",
        full_name="Admin User",
        password_hash="not-used",
        role=UserRole.ADMIN,
        is_active=True,
        auth_session_version=1,
        created_at=datetime(2026, 6, 24, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 24, 12, 0, tzinfo=UTC),
    )


async def test_authenticate_token_rejects_stale_session_version(monkeypatch) -> None:
    monkeypatch.setattr(auth, "get_auth_secret", lambda: "test-secret")

    async def runtime_config():
        return SimpleNamespace(auth_access_token_minutes=15, auth_remember_days=30)

    monkeypatch.setattr(auth, "get_runtime_config", runtime_config)
    user = make_user()
    token, _expires = await auth.create_access_token(user)

    user.auth_session_version = 2

    assert await auth.authenticate_token(FakeSession(user), token) is None
