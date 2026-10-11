from datetime import UTC, datetime
from types import SimpleNamespace
import uuid

import pytest
from starlette.requests import Request
from starlette.websockets import WebSocket

from app.models import User
from app.models.enums import UserRole
from app.services import auth


class EmptyScalarRows:
    def all(self):
        return []


class FakeSession:
    def __init__(self, user: User) -> None:
        self.user = user

    async def get(self, _model, user_id, *, populate_existing=False):
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


async def test_verified_token_retains_original_expiry_and_existing_user_adapter(monkeypatch) -> None:
    monkeypatch.setattr(auth, "get_auth_secret", lambda: "test-secret")
    user = make_user()
    expires = int(datetime.now(UTC).timestamp()) + 60
    token = auth._encode_jwt({"sub": str(user.id), "ver": 1, "exp": expires, "jti": "inert-expiry-value"})
    verified = await auth.authenticated_token(FakeSession(user), token)
    assert verified.user is user and verified.expires_at == expires
    assert await auth.authenticate_token(FakeSession(user), token) is user


@pytest.mark.parametrize("transport,headers,query", [
    ("http", "authorization", False),
    ("http", "cookie", False),
    ("websocket", "authorization", False),
    ("websocket", "cookie", False),
    ("websocket", None, True),
])
async def test_existing_request_and_websocket_adapters_use_verified_user_without_locks(
    monkeypatch, transport, headers, query,
) -> None:
    monkeypatch.setattr(auth, "get_auth_secret", lambda: "test-secret")
    async def runtime():
        return SimpleNamespace(auth_cookie_name="iacs_session")
    monkeypatch.setattr(auth, "get_runtime_config", runtime)
    user = make_user()
    token = auth._encode_jwt({"sub": str(user.id), "ver": 1,
        "exp": int(datetime.now(UTC).timestamp()) + 60, "jti": "adapter-test"})
    values = [] if headers is None else [(headers.encode(),
        (f"Bearer {token}" if headers == "authorization" else f"iacs_session={token}").encode())]
    scope = {"type": transport, "headers": values,
        "query_string": f"token={token}".encode() if query else b""}
    class AdapterSession(FakeSession):
        async def get(self, model, identity, *, populate_existing=False):
            assert populate_existing is True
            return await super().get(model, identity, populate_existing=populate_existing)
        async def scalar(self, statement):
            assert statement._for_update_arg is None
            assert statement.is_select
            return None
    session = AdapterSession(user)
    if transport == "http":
        assert await auth.extract_http_token(Request(scope)) == token
        assert await auth.authenticate_request(session, Request(scope)) is user
    else:
        async def unused():
            raise AssertionError("Auth must not receive a WebSocket message")
        assert await auth.authenticate_websocket(session, WebSocket(scope, unused, unused)) is user


@pytest.mark.parametrize("failure", ["signature", "expired", "missing_jti", "revoked", "inactive", "missing_user"])
async def test_verified_and_existing_token_adapters_share_rejection(monkeypatch, failure) -> None:
    monkeypatch.setattr(auth, "get_auth_secret", lambda: "test-secret")
    user = make_user()
    payload = {"sub": str(user.id), "ver": 1,
        "exp": int(datetime.now(UTC).timestamp()) + 60, "jti": "rejection-test"}
    if failure == "expired":
        payload["exp"] = 1
    if failure == "missing_jti":
        payload.pop("jti")
    token = auth._encode_jwt(payload)
    if failure == "signature":
        token = token[:-1] + ("A" if token[-1] != "A" else "B")
    if failure == "inactive":
        user.is_active = False
    class RejectionSession(FakeSession):
        async def get(self, *args, **kwargs):
            return None if failure == "missing_user" else await super().get(*args, **kwargs)
        async def scalar(self, _statement):
            return uuid.uuid4() if failure == "revoked" else None
    session = RejectionSession(user)
    assert await auth.authenticated_token(session, token) is None
    assert await auth.authenticate_token(session, token) is None
