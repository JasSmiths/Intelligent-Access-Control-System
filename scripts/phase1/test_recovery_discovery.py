"""Real-auth, read-only command identity discovery in disposable PostgreSQL."""
from test_recovery_boundaries import isolated_resources as isolated_resources

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text

from app.api.v1 import integrations
from app.db.session import AsyncSessionLocal
from app.models import RevokedAuthToken, User
from app.models.enums import UserRole
from app.services import auth

pytestmark = pytest.mark.asyncio


async def setup(monkeypatch):
    monkeypatch.setattr(auth, "get_runtime_config", AsyncMock(return_value=SimpleNamespace(
        auth_access_token_minutes=30, auth_remember_days=1, auth_cookie_name="synthetic-auth")))
    async with AsyncSessionLocal() as session:
        owner = User(username="synthetic-discovery", full_name="Synthetic Owner", password_hash="unused",
            role=UserRole.ADMIN, is_active=True, auth_session_version=1)
        session.add(owner)
        await session.flush()
        now = await session.scalar(select(func.clock_timestamp()))
        session.add(RevokedAuthToken(user_id=owner.id, jti_hash="synthetic-expired-discovery", expires_at=now-timedelta(days=1)))
        await session.commit()
        token, _ = await auth.create_access_token(owner)
    app = FastAPI()
    app.include_router(integrations.router, prefix="/api/v1/integrations")
    return app, token, owner.id


async def snapshot():
    async with AsyncSessionLocal() as session:
        return [(await session.execute(text(f"SELECT row_to_json(r)::text FROM {table} r ORDER BY id"))).scalars().all()
                for table in ("gate_command_records", "access_device_command_records", "revoked_auth_tokens", "audit_logs")]


def client(app, token):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid",
        headers={"Authorization": f"Bearer {token}"}, trust_env=False)


@pytest.mark.parametrize("change,expected", [("role", 403), ("inactive", 401), ("version", 401), ("delete", 401)])
async def test_command_discovery_rechecks_current_actor_and_generation(monkeypatch, change, expected):
    app, token, identity = await setup(monkeypatch)
    async with AsyncSessionLocal() as session:
        user = await session.get(User, identity)
        if change == "role":
            user.role = UserRole.STANDARD
        elif change == "inactive":
            user.is_active = False
        elif change == "version":
            user.auth_session_version += 1
        else:
            await session.delete(user)
        await session.commit()
    before = await snapshot()
    async with client(app, token) as browser:
        for kind in ("gate", "cover"):
            response = await browser.get(f"/api/v1/integrations/{kind}/commands")
            assert response.status_code == expected
    assert await snapshot() == before


async def test_discovery_empty_gate_cover_and_invalid_queries_are_read_only(monkeypatch):
    app, token, _ = await setup(monkeypatch)
    before = await snapshot()
    async with client(app, token) as browser:
        for kind in ("gate", "cover"):
            path = f"/api/v1/integrations/{kind}/commands"
            response = await browser.get(path)
            assert response.status_code == 200 and response.json() == {"items": [], "next_cursor": None}
            assert response.headers["cache-control"] == "no-store"
            for query in ("limit=0", "limit=101", "before_id=invalid"):
                assert (await browser.get(path+"?"+query)).status_code == 422
    assert await snapshot() == before


async def test_expired_auth_cleanup_is_an_auth_mutation_participant(monkeypatch):
    _, token, _ = await setup(monkeypatch)
    async with AsyncSessionLocal() as session:
        before = await session.scalar(select(func.count()).select_from(RevokedAuthToken))
        await auth.purge_expired_revoked_tokens(session)
        assert await session.scalar(select(func.count()).select_from(RevokedAuthToken)) == 0
        await session.rollback()
        assert await session.scalar(select(func.count()).select_from(RevokedAuthToken)) == before
        await auth.revoke_access_token(session, token)
        assert await auth.authenticate_token(session, token) is None
        assert await session.scalar(select(func.count()).select_from(RevokedAuthToken)) == 1
