"""Initial public setup admits exactly one first Admin in an isolated database."""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import func, select

from app.api.v1 import auth as endpoint
from app.db.session import AsyncSessionLocal
from app.models import User

pytestmark = pytest.mark.asyncio


async def test_concurrent_initial_setup_cannot_create_two_administrators(isolated_resources, monkeypatch):
    both_counted = asyncio.Event()
    calls = 0
    original = endpoint.count_users
    async def synchronized_preliminary_count(session):
        nonlocal calls
        count = await original(session)
        if count == 0:
            calls += 1
            if calls == 2:
                both_counted.set()
            await both_counted.wait()
        return count
    # The old route's unlocked count is forced to observe zero twice. The new
    # service's authoritative serialized count must not use this preliminary seam.
    monkeypatch.setattr(endpoint, "count_users", synchronized_preliminary_count)
    async def setup(index):
        async with AsyncSessionLocal() as session:
            try:
                return await endpoint.first_run_setup(endpoint.SetupRequest(username=f"synthetic-admin-{index}",
                    first_name="Synthetic", last_name="Admin", password="synthetic-password-only"), Response(), session)
            except HTTPException as error:
                return error
    results = await asyncio.wait_for(asyncio.gather(setup(1), setup(2)), 8)
    async with AsyncSessionLocal() as session:
        count = await session.scalar(select(func.count()).select_from(User))
    assert count == 1
    assert sum(isinstance(result, endpoint.UserResponse) for result in results) == 1
    assert [result.status_code for result in results if isinstance(result, HTTPException)] == [409]


async def test_required_setup_audit_failure_rolls_back_administrator(isolated_resources, monkeypatch):
    from app.services import telemetry
    async def failed_audit(*_, **__):
        raise RuntimeError("synthetic required audit failure")
    monkeypatch.setattr(telemetry, "write_audit_log", failed_audit)
    async with AsyncSessionLocal() as session:
        with pytest.raises(RuntimeError, match="required audit failure"):
            await endpoint.first_run_setup(endpoint.SetupRequest(username="synthetic-failed-admin",
                first_name="Synthetic", last_name="Admin", password="synthetic-password-only"), Response(), session)
        await session.rollback()
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 0
