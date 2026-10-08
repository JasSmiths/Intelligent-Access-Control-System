"""Current Admin/session authority and real logout lock serialization."""
from test_recovery_boundaries import isolated_resources as isolated_resources
from test_calendar_sync_store_persistence import calendar_resources as calendar_resources
from test_calendar_sync_store_persistence import setup_accounts, work
from test_calendar_sync_commit_persistence import counts, visitor_stage

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal, engine
from app.models import AuditLog, User
from app.models.enums import UserRole
from app.services import auth
from app.services.calendar_request_authority import CalendarAuthorityError, CalendarRequestAuthority
from app.services.calendar_sync_commit import CalendarAccountCommit
from app.services.calendar_sync_contracts import CalendarOwnershipLost
from app.services.calendar_sync_store import CalendarSyncStore

pytestmark = pytest.mark.asyncio


async def setup(*, expires_at=None):
    actor, ids = await setup_accounts(1)
    token = auth._encode_jwt({"sub": str(actor), "ver": 0,
        "exp": expires_at or int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()), "jti": str(uuid.uuid4())})
    authority = CalendarRequestAuthority(actor, token)
    store = CalendarSyncStore()
    admission = await store.admit(uuid.uuid4(), origin="ui", actor_user_id=actor,
        execution=None, authorize=authority.authorize, work=work())
    claim = await store.claim_account(admission, ids[0], authorize=authority.authorize, work=work())
    return actor, token, authority, store, admission, claim


async def test_current_admin_verified_expiry_accepts_account_and_token_is_transient():
    actor, token, authority, store, admission, claim = await setup()
    assert token not in repr(authority)
    result = await CalendarAccountCommit(store).apply(claim, work=work(), authorize=authority.authorize,
        stage=visitor_stage(claim), actor="Inert Calendar Admin", actor_user_id=actor)
    assert result.checkpoint.status == "ok"
    async with AsyncSessionLocal() as session:
        row = await session.get(User, actor)
        assert await auth.authenticate_token(session, token) is row
        audit = (await session.scalars(select(AuditLog).where(AuditLog.action == "icloud_calendar.sync_account"))).one()
        assert token not in str(audit.metadata_)
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("drift", ["inactive", "role", "version", "logout"])
async def test_final_authority_refetches_identity_map_and_rejects_current_drift(drift):
    actor, token, authority, store, admission, claim = await setup()

    async def changed_during_real_domain_stage(session, account, context):
        result = await visitor_stage(claim)(session, account, context)
        async with AsyncSessionLocal() as administration:
            user = await administration.get(User, actor)
            if drift == "inactive":
                user.is_active = False
            elif drift == "role":
                user.role = UserRole.STANDARD
            elif drift == "version":
                user.auth_session_version += 1
            else:
                await auth.revoke_access_token(administration, token)
            await administration.commit()
        return result

    with pytest.raises(CalendarAuthorityError):
        await CalendarAccountCommit(store).apply(claim, work=work(), authorize=authority.authorize,
            stage=changed_during_real_domain_stage, actor="Inert Calendar Admin", actor_user_id=actor)
    assert await counts(admission.run_id) == ([], 0, 0)
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("logout_order", ["before", "after"])
async def test_actual_logout_fk_lock_serializes_final_actor_authority(logout_order):
    actor, token, authority, store, admission, claim = await setup()
    locked, release = asyncio.Event(), asyncio.Event()

    async def final_authority(session, final):
        expiry = await authority.authorize(session, final)
        if final:
            locked.set()
            await release.wait()
        return expiry

    if logout_order == "before":
        async with AsyncSessionLocal() as logout:
            # The canonical logout owner commits its own revocation.
            await auth.revoke_access_token(logout, token)
            with pytest.raises(CalendarAuthorityError):
                await CalendarAccountCommit(store).apply(claim, work=work(), authorize=authority.authorize,
                    stage=visitor_stage(claim), actor="Inert Calendar Admin", actor_user_id=actor)
        assert await counts(admission.run_id) == ([], 0, 0)
    else:
        task = asyncio.create_task(CalendarAccountCommit(store).apply(claim, work=work(), authorize=final_authority,
            stage=visitor_stage(claim), actor="Inert Calendar Admin", actor_user_id=actor))
        await asyncio.wait_for(locked.wait(), 1)
        started = asyncio.Event()

        async def logout_after_lock():
            async with AsyncSessionLocal() as logout:
                started.set()
                await auth.revoke_access_token(logout, token)

        logout_task = asyncio.create_task(logout_after_lock())
        await started.wait()
        await asyncio.sleep(0.05)
        assert not logout_task.done()
        release.set()
        assert (await task).checkpoint.status == "ok"
        await logout_task
        checkpoints, passes, audits = await counts(admission.run_id)
        assert len(checkpoints) == passes == audits == 1
    async with AsyncSessionLocal() as session:
        assert await auth.authenticate_token(session, token) is None
    assert engine.pool.checkedout() == 0


async def test_original_request_token_expiry_during_final_flush_prevents_acceptance(monkeypatch):
    actor, _, authority, store, admission, claim = await setup(expires_at=int(datetime.now(UTC).timestamp()) + 2)
    original_flush = AsyncSession.flush

    async def delayed_final_flush(session, *args, **kwargs):
        checkpoint_audit = any(isinstance(item, AuditLog) and item.action == "icloud_calendar.sync_account" for item in session.new)
        await original_flush(session, *args, **kwargs)
        if checkpoint_audit:
            await asyncio.sleep(2.1)

    monkeypatch.setattr(AsyncSession, "flush", delayed_final_flush)
    with pytest.raises(CalendarOwnershipLost, match="authority expired"):
        await CalendarAccountCommit(store).apply(claim, work=work(), authorize=authority.authorize,
            stage=visitor_stage(claim), actor="Inert Calendar Admin", actor_user_id=actor)
    monkeypatch.setattr(AsyncSession, "flush", original_flush)
    assert await counts(admission.run_id) == ([], 0, 0)
    assert engine.pool.checkedout() == 0
