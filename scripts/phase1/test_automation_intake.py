"""Atomic trigger intake and read-only recovery routes on disposable PostgreSQL.

The inherited guard requires a synthetic, loopback-only namespace before imports.
ASGI requests use real authentication and never start application workers.
"""
from test_recovery_boundaries import isolated_resources as isolated_resources

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text

from app.api.v1 import automations as api
from app.db.session import AsyncSessionLocal
from app.models import AutomationRule, AutomationRun, RevokedAuthToken, User
from app.models.enums import UserRole
from app.services import auth, automation_intake, automations
from app.services.automation_execution import AutomationRunStore

pytestmark = pytest.mark.asyncio


def notice_action():
    return {"id": "notice", "type": "integration.whatsapp.send_message", "config": {"target_mode": "all"}}


async def rule(*, trigger="visitor_pass.created", config=None, active=True):
    async with AsyncSessionLocal() as session:
        row = AutomationRule(name="Synthetic intake rule", is_active=active,
            triggers=[{"id": "source", "type": trigger, "config": config or {}}], trigger_keys=[trigger],
            conditions=[], actions=[notice_action()])
        session.add(row)
        await session.commit()
        return row.id


async def test_intake_and_origin_change_are_invisible_and_rollback_together():
    identity = await rule()
    origin = str(uuid.uuid4())
    async with AsyncSessionLocal() as owner:
        row = await owner.get(AutomationRule, identity)
        row.description = "Synthetic source mutation"
        runs = await automation_intake.reserve_trigger(owner, "visitor_pass.created",
            {"visitor_pass": {"id": origin, "visitor_name": "Captured synthetic visitor", "status": "pending"}},
            origin_kind="visitor_pass.created", origin_id=origin)
        assert len(runs) == 1
        async with AsyncSessionLocal() as reader:
            assert await reader.get(AutomationRun, runs[0]) is None
            assert (await reader.get(AutomationRule, identity)).description != "Synthetic source mutation"
        await owner.rollback()
    async with AsyncSessionLocal() as reader:
        assert await reader.get(AutomationRun, runs[0]) is None
        assert (await reader.get(AutomationRule, identity)).description != "Synthetic source mutation"


async def test_committed_intake_uses_captured_facts_and_same_origin_never_rebinds():
    identity, origin = await rule(), str(uuid.uuid4())
    payload = {"visitor_pass": {"id": origin, "visitor_name": "Captured synthetic visitor", "status": "pending"}}
    async with AsyncSessionLocal() as owner:
        first = await automation_intake.reserve_trigger(owner, "visitor_pass.created", payload,
            origin_kind="visitor_pass.created", origin_id=origin, trace_id="synthetic-intake")
        await owner.commit()
    payload["visitor_pass"]["visitor_name"] = "Changed later"
    async with AsyncSessionLocal() as owner:
        second = await automation_intake.reserve_trigger(owner, "visitor_pass.created", payload,
            origin_kind="visitor_pass.created", origin_id=origin)
        await owner.commit()
    assert first == second
    row = await AutomationRunStore().get(first[0])
    assert row.rule_id == identity and row.status == "queued" and row.claim_token is None
    assert row.context["dispatch"]["facts"]["visitor_name"] == "Captured synthetic visitor"
    assert row.trace_id == "synthetic-intake" and row.action_plan[0]["state"] == "pending"


async def test_intake_separates_rules_triggers_and_origin_transitions():
    first, second = await rule(), await rule()
    await rule(active=False)
    await rule(config={"visitor_pass_id": str(uuid.uuid4())})
    origin = str(uuid.uuid4())
    async with AsyncSessionLocal() as owner:
        initial = await automation_intake.reserve_trigger(owner, "visitor_pass.created", {},
            origin_kind="visitor_pass.created", origin_id=origin)
        another = await automation_intake.reserve_trigger(owner, "visitor_pass.created", {},
            origin_kind="visitor_pass.updated", origin_id=origin)
        await owner.commit()
    assert len(initial) == len(another) == 2 and set(initial).isdisjoint(another)
    async with AsyncSessionLocal() as reader:
        rows = (await reader.scalars(select(AutomationRun))).all()
        assert {row.rule_id for row in rows} == {first, second}
        assert len({row.occurrence_key for row in rows}) == 4


async def history_setup(monkeypatch, *, count=3):
    forbidden = AsyncMock(side_effect=AssertionError("History must not execute or claim work"))
    monkeypatch.setattr(AutomationRunStore, "claim", forbidden)
    monkeypatch.setattr(automations, "get_automation_service", lambda: (_ for _ in ()).throw(AssertionError("History must not construct execution service")))
    monkeypatch.setattr(api, "get_automation_service", lambda: (_ for _ in ()).throw(AssertionError("History must not construct execution service")))
    monkeypatch.setattr(auth, "get_runtime_config", AsyncMock(return_value=SimpleNamespace(
        auth_access_token_minutes=30, auth_remember_days=1, auth_cookie_name="synthetic-auth")))
    async with AsyncSessionLocal() as session:
        user = User(username="synthetic-history-admin", full_name="Synthetic Admin", password_hash="not-a-real-hash",
            role=UserRole.ADMIN, is_active=True, auth_session_version=0)
        session.add(user)
        await session.flush()
        now = await session.scalar(select(func.clock_timestamp()))
        ids = [uuid.UUID(int=index + 1) for index in range(count)]
        for identity in ids:
            session.add(AutomationRun(id=identity, rule_id=None, trigger_key="time.every_x", status="queued",
                started_at=now, created_at=now, updated_at=now, recovery_version=1, queued_at=now,
                context={"version": 1, "dispatch": {"private": "synthetic"}, "rule_fingerprint": "private"},
                trigger_payload={}, condition_results=[], action_results=[], actor="Synthetic", source="synthetic", action_plan=[]))
        session.add(RevokedAuthToken(user_id=user.id, jti_hash="synthetic-expired-revocation", expires_at=now-timedelta(days=1)))
        await session.commit()
        token, _ = await auth.create_access_token(user)
        actor_id = user.id
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1/automations")
    return app, token, actor_id, ids


async def snapshot():
    async with AsyncSessionLocal() as session:
        return list((await session.execute(text("SELECT row_to_json(r)::text FROM automation_runs r ORDER BY id"))).scalars()), await session.scalar(select(func.count()).select_from(RevokedAuthToken))


async def test_history_list_detail_are_stable_read_only_and_redacted(monkeypatch):
    app, token, _, ids = await history_setup(monkeypatch)
    before = await snapshot()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid",
                                headers={"Authorization": f"Bearer {token}"}, trust_env=False) as client:
        first = await client.get("/api/v1/automations/runs?limit=2")
        assert first.status_code == 200
        page = first.json()
        assert [row["id"] for row in page["items"]] == [str(ids[2]), str(ids[1])]
        assert page["next_cursor"] == str(ids[1])
        second = await client.get(f"/api/v1/automations/runs?limit=2&before_id={page['next_cursor']}")
        assert second.status_code == 200 and [row["id"] for row in second.json()["items"]] == [str(ids[0])]
        assert second.json()["next_cursor"] is None
        detail = await client.get(f"/api/v1/automations/runs/{ids[2]}")
        assert detail.status_code == 200 and detail.json() == page["items"][0]
        assert detail.json()["context"] == {"version": 1}
    assert await snapshot() == before, "Recovery GET must not mutate commands, runs, or expired auth rows"


@pytest.mark.parametrize("change,expected", [("reader", 403), ("inactive", 401), ("version", 401), ("deleted", 401)])
async def test_history_rechecks_actual_current_actor(monkeypatch, change, expected):
    app, token, identity, ids = await history_setup(monkeypatch, count=1)
    async with AsyncSessionLocal() as session:
        user = await session.get(User, identity)
        if change == "reader":
            user.role = UserRole.STANDARD
        elif change == "inactive":
            user.is_active = False
        elif change == "version":
            user.auth_session_version += 1
        else:
            await session.delete(user)
        await session.commit()
    before = await snapshot()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid",
                                headers={"Authorization": f"Bearer {token}"}, trust_env=False) as client:
        for path in ("/api/v1/automations/runs", f"/api/v1/automations/runs/{ids[0]}"):
            assert (await client.get(path)).status_code == expected
    assert await snapshot() == before


async def test_history_missing_auth_cursor_id_and_limits(monkeypatch):
    app, token, _, _ = await history_setup(monkeypatch, count=1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid", trust_env=False) as client:
        assert (await client.get("/api/v1/automations/runs")).status_code == 401
        client.headers["Authorization"] = f"Bearer {token}"
        for query in ("limit=0", "limit=101", "before_id=not-a-uuid"):
            assert (await client.get("/api/v1/automations/runs?" + query)).status_code == 422
        assert (await client.get(f"/api/v1/automations/runs?before_id={uuid.uuid4()}")).status_code == 404
        assert (await client.get(f"/api/v1/automations/runs/{uuid.uuid4()}")).status_code == 404
