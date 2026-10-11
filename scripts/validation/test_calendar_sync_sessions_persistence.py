"""Actual short-session checkout and absolute provider-start eligibility."""
from test_recovery_boundaries import isolated_resources as isolated_resources
from test_calendar_sync_store_persistence import calendar_resources as calendar_resources
from test_calendar_sync_store_persistence import admit, allowed, setup_accounts, work

import asyncio
from datetime import UTC, datetime, timedelta
from time import monotonic

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_secret
from app.db.session import AsyncSessionLocal, engine
from app.models import ICloudCalendarAccount, SystemSetting
from app.modules.icloud_calendar.contracts import CalendarWork, ICloudCalendarTimeout
from app.services.auth_root import reload_auth_root_for_transaction
from app.services.calendar_sync_contracts import CalendarOwnershipLost
from app.services.calendar_sync_sessions import CalendarSyncSessions
from app.services.calendar_sync_store import CalendarSyncStore

pytestmark = pytest.mark.asyncio


async def setup():
    actor, ids = await setup_accounts(1)
    async with AsyncSessionLocal() as session:
        await reload_auth_root_for_transaction(session)
        row = await session.get(ICloudCalendarAccount, ids[0])
        row.encrypted_session_bundle = encrypt_secret('{"inert":"session"}')
        await session.commit()
    store = CalendarSyncStore()
    admission = await admit(store, actor)
    claim = await store.claim_account(admission, ids[0], authorize=allowed, work=work())
    return store, admission, claim


async def test_detached_decrypted_bundle_owns_no_connection_before_provider_dispatch():
    store, _, claim = await setup()
    original = work()
    detached = await CalendarSyncSessions(store).read_session(claim, authorize=allowed, work=original)
    assert detached.bundle == {"inert": "session"}
    assert "inert" not in repr(detached)
    assert engine.pool.checkedout() == 0
    detached.before_dispatch(original)


@pytest.mark.parametrize("expiring", ["work", "account_lease"])
async def test_slow_actual_close_cannot_reset_attempt_or_db_sampled_lease_before_provider(expiring, monkeypatch):
    store, _, claim = await setup()
    original = CalendarWork(monotonic() + 120)
    if expiring == "account_lease":
        async with AsyncSessionLocal() as session:
            row = await session.get(ICloudCalendarAccount, claim.account_id)
            row.sync_lease_until = datetime.now(UTC) + timedelta(milliseconds=150)
            await session.commit()
    entered, release = asyncio.Event(), asyncio.Event()
    actual_close = AsyncSession.close
    async def blocked_close(session):
        entered.set()
        await release.wait()
        return await actual_close(session)
    monkeypatch.setattr(AsyncSession, "close", blocked_close)
    provider_calls = []
    async def read_then_dispatch():
        detached = await CalendarSyncSessions(store).read_session(claim, authorize=allowed, work=original)
        detached.before_dispatch(original)
        provider_calls.append(detached.bundle)
    task = asyncio.create_task(read_then_dispatch())
    await asyncio.wait_for(entered.wait(), 2)
    assert engine.pool.checkedout() == 1 and not task.done()
    if expiring == "work":
        original.deadline = monotonic() - 1
    else:
        await asyncio.sleep(0.2)
    release.set()
    with pytest.raises(ICloudCalendarTimeout if expiring == "work" else CalendarOwnershipLost):
        await task
    assert provider_calls == [] and engine.pool.checkedout() == 0


@pytest.mark.parametrize("changed", ["ciphertext", "policy_reverted"])
async def test_provider_session_read_rejects_stale_account_or_semantic_revision(changed):
    store, admission, claim = await setup()
    async with AsyncSessionLocal() as session:
        if changed == "ciphertext":
            row = await session.get(ICloudCalendarAccount, claim.account_id)
            row.encrypted_session_bundle = encrypt_secret('{"inert":"replacement"}')
        else:
            session.add(SystemSetting(key="site_timezone", category="general",
                value={"value": admission.context.site_timezone}, is_secret=False,
                description="Same semantic value after an explicit policy replacement"))
        await session.commit()
    with pytest.raises(CalendarOwnershipLost):
        await CalendarSyncSessions(store).read_session(claim, authorize=allowed, work=work())
    assert engine.pool.checkedout() == 0
