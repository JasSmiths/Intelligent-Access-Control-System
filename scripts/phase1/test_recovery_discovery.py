"""Real-auth, read-only durable identity discovery in disposable PostgreSQL."""
from test_recovery_boundaries import isolated_resources as isolated_resources

from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text

from app.api.v1 import ai, integrations
from app.db.session import AsyncSessionLocal
from app.models import AlfredApproval, ChatSession, RevokedAuthToken, User
from app.models.enums import UserRole
from app.services import auth
from app.services.alfred.approvals import AlfredApprovalStore

pytestmark = pytest.mark.asyncio


async def setup(monkeypatch):
    monkeypatch.setattr(auth, "get_runtime_config", AsyncMock(return_value=SimpleNamespace(
        auth_access_token_minutes=30, auth_remember_days=1, auth_cookie_name="synthetic-auth")))
    forbidden = AsyncMock(side_effect=AssertionError("Discovery must not claim an approval"))
    monkeypatch.setattr(AlfredApprovalStore, "decide", forbidden)
    async with AsyncSessionLocal() as session:
        owner = User(username="synthetic-discovery", full_name="Synthetic Owner", password_hash="unused",
            role=UserRole.ADMIN, is_active=True, auth_session_version=1)
        other = User(username="synthetic-other", full_name="Synthetic Other", password_hash="unused",
            role=UserRole.ADMIN, is_active=True, auth_session_version=1)
        chats = [ChatSession(title="Synthetic shared history") for _ in range(3)]
        session.add_all([owner, other, *chats])
        await session.flush()
        now = await session.scalar(select(func.clock_timestamp()))
        for index, state in enumerate(["completed", "claimed", "pending", "completed", "unknown"]):
            session.add(AlfredApproval(id=f"confirm-discovery-{index}", operation_id=uuid.UUID(int=index+1),
                session_id=chats[min(index, 2)].id, requester_user_id=other.id if index == 3 else owner.id,
                requester_auth_session_version=0 if index == 4 else 1,
                status=state, payload={"private": "synthetic input"}, result={"private": "synthetic result"},
                created_at=now, updated_at=now, expires_at=now-timedelta(minutes=1)))
        session.add(RevokedAuthToken(user_id=owner.id, jti_hash="synthetic-expired-discovery", expires_at=now-timedelta(days=1)))
        await session.commit()
        token, _ = await auth.create_access_token(owner)
    app = FastAPI()
    app.include_router(ai.router, prefix="/api/v1/ai")
    app.include_router(integrations.router, prefix="/api/v1/integrations")
    return app, token, owner.id, chats, forbidden


async def snapshot():
    async with AsyncSessionLocal() as session:
        return [(await session.execute(text(f"SELECT row_to_json(r)::text FROM {table} r ORDER BY id"))).scalars().all()
                for table in ("alfred_approvals", "revoked_auth_tokens", "audit_logs")]


def client(app, token):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid",
        headers={"Authorization": f"Bearer {token}"}, trust_env=False)


async def test_lost_browser_identity_is_discoverable_without_payload_leak_or_mutation(monkeypatch):
    app, token, _, chats, forbidden = await setup(monkeypatch)
    before = await snapshot()
    async with client(app, token) as browser:
        first = await browser.get("/api/v1/ai/chat/approvals?limit=2")
        assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
        page = first.json()
        assert [row["confirmation_id"] for row in page["items"]] == ["confirm-discovery-2", "confirm-discovery-1"]
        assert [row["status"] for row in page["items"]] == ["expired", "in_progress"]
        expected_keys = {"confirmation_id", "operation_id", "session_id", "status", "created_at", "expires_at"}
        assert all(set(row) == expected_keys for row in page["items"])
        assert page["next_cursor"] == "confirm-discovery-1"
        second = await browser.get("/api/v1/ai/chat/approvals", params={"limit": 2, "before_id": page["next_cursor"]})
        assert [row["confirmation_id"] for row in second.json()["items"]] == ["confirm-discovery-0"]
        assert second.json()["next_cursor"] is None
        filtered = await browser.get("/api/v1/ai/chat/approvals", params={"session_id": str(chats[0].id)})
        assert [row["confirmation_id"] for row in filtered.json()["items"]] == ["confirm-discovery-0"]
        # Other requester and prior auth generation cannot become pagination oracles.
        for cursor in ("confirm-discovery-3", "confirm-discovery-4", "absent"):
            assert (await browser.get("/api/v1/ai/chat/approvals", params={"before_id": cursor})).status_code == 400
    assert await snapshot() == before
    forbidden.assert_not_awaited()


@pytest.mark.parametrize("change,expected", [("role", 200), ("inactive", 401), ("version", 401), ("delete", 401)])
async def test_discovery_rechecks_current_actor_and_generation(monkeypatch, change, expected):
    app, token, identity, _, _ = await setup(monkeypatch)
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
        response = await browser.get("/api/v1/ai/chat/approvals")
        assert response.status_code == expected
        if expected == 200:
            assert response.json() == {"items": [], "next_cursor": None}
    assert await snapshot() == before


async def test_discovery_empty_gate_cover_and_invalid_queries_are_read_only(monkeypatch):
    app, token, _, _, _ = await setup(monkeypatch)
    before = await snapshot()
    async with client(app, token) as browser:
        for kind in ("gate", "cover"):
            response = await browser.get(f"/api/v1/integrations/{kind}/commands")
            assert response.status_code == 200 and response.json() == {"items": [], "next_cursor": None}
            assert response.headers["cache-control"] == "no-store"
        for query in ("limit=0", "limit=101"):
            assert (await browser.get("/api/v1/ai/chat/approvals?"+query)).status_code == 422
        assert (await browser.get("/api/v1/ai/chat/approvals?session_id=invalid")).status_code == 400
    assert await snapshot() == before


async def test_expired_auth_cleanup_is_an_auth_mutation_participant(monkeypatch):
    _, token, _, _, _ = await setup(monkeypatch)
    async with AsyncSessionLocal() as session:
        before = await session.scalar(select(func.count()).select_from(RevokedAuthToken))
        await auth.purge_expired_revoked_tokens(session)
        assert await session.scalar(select(func.count()).select_from(RevokedAuthToken)) == 0
        await session.rollback()
        assert await session.scalar(select(func.count()).select_from(RevokedAuthToken)) == before
        await auth.revoke_access_token(session, token)
        assert await auth.authenticate_token(session, token) is None
        assert await session.scalar(select(func.count()).select_from(RevokedAuthToken)) == 1


async def test_paired_approval_discovery_fixture_is_exact():
    root = Path(__file__).resolve().parents[2]
    original = root / "backend/tests/contracts/fixtures/alfred/approval_pages.json"
    generated = root / "frontend/src/api/fixtures/approvalPages.generated.json"
    assert original.read_bytes() == generated.read_bytes()
    pages = json.loads(original.read_text())
    assert {row["status"] for page in pages.values() for row in page["items"]} == {
        "pending", "in_progress", "completed", "unknown", "cancelled", "expired"}
