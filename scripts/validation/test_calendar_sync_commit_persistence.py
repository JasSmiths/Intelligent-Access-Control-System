"""Actual account effects, audit/checkpoint acceptance and uncertain commit seams."""
import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from test_calendar_sync_store_persistence import admit, allowed, setup_accounts, work
from test_calendar_sync_store_persistence import calendar_resources as calendar_resources
from test_calendar_workflow_authority_persistence import attempted
from test_recovery_boundaries import isolated_resources as isolated_resources

from app.db.session import AsyncSessionLocal, engine
from app.models import (
    AuditLog,
    AutomationRun,
    ICloudCalendarAccount,
    ICloudCalendarSyncRun,
    VisitorPass,
)
from app.services import calendar_sync_commit, calendar_workflow_authority
from app.services.calendar_sync_commit import CalendarAccountApply, CalendarAccountCommit
from app.services.calendar_sync_contracts import (
    CalendarAccountCheckpoint,
    CalendarOutcomeUnknown,
    CalendarOwnershipLost,
)
from app.services.calendar_sync_store import CalendarSyncStore
from app.services.calendar_workflow_authority import CalendarWorkflowAuthority
from app.services.visitor_passes import VisitorPassService

pytestmark = pytest.mark.asyncio


def visitor_stage(claim, *, name="Inert calendar atomic visitor"):
    async def stage(session, account, context):
        row = await VisitorPassService().create_pass(session,
            visitor_name=name, expected_time=datetime.now(UTC) + timedelta(hours=1),
            source="icloud_calendar", source_reference="inert-atomic:" + str(claim.account_id),
            actor="Inert Calendar Admin")
        return CalendarAccountApply(CalendarAccountCheckpoint(account_id=account.id,
            apple_id=account.apple_id, display_name=account.display_name or account.apple_id,
            attempt_token=claim.attempt_token, status="ok", events_scanned=1,
            events_matched=1, passes_created=1), (("visitor_pass.changed", {"id": str(row.id)}),))
    return stage


async def apply(commit, claim, *, authorize=allowed, stage=None, actor_id=None):
    return await commit.apply(claim, work=work(), authorize=authorize,
        stage=stage or visitor_stage(claim), actor="Inert Calendar Admin", actor_user_id=actor_id)


async def counts(run_id):
    async with AsyncSessionLocal() as session:
        return ((await session.get(ICloudCalendarSyncRun, run_id)).account_results,
            await session.scalar(select(func.count()).select_from(VisitorPass).where(VisitorPass.creation_source == "icloud_calendar")),
            await session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "icloud_calendar.sync_account")))


async def test_first_account_receipt_survives_next_account_failure_without_repeating_checkpoint():
    actor, ids = await setup_accounts()
    store = CalendarSyncStore()
    admitted = await admit(store, actor)
    first = await store.claim_account(admitted, ids[0], authorize=allowed, work=work())
    result = await apply(CalendarAccountCommit(store), first, actor_id=actor)
    assert result.checkpoint.passes_created == 1
    assert await store.claim_account(admitted, ids[0], authorize=allowed, work=work()) is None
    second = await store.claim_account(admitted, ids[1], authorize=allowed, work=work())

    async def fail_after_real_domain_stage(session, account, context):
        await visitor_stage(second)(session, account, context)
        raise RuntimeError("inert second-account failure")

    with pytest.raises(RuntimeError, match="second-account"):
        await apply(CalendarAccountCommit(store), second, stage=fail_after_real_domain_stage)
    checkpoints, passes, audits = await counts(admitted.run_id)
    assert len(checkpoints) == passes == audits == 1
    assert checkpoints[0]["account_id"] == str(first.account_id)
    assert engine.pool.checkedout() == 0


async def test_required_checkpoint_audit_failure_rolls_back_domain_audit_and_checkpoint_then_retries(monkeypatch):
    actor, ids = await setup_accounts(1)
    store = CalendarSyncStore()
    admitted = await admit(store, actor)
    claim = await store.claim_account(admitted, ids[0], authorize=allowed, work=work())
    original = calendar_sync_commit.write_audit_log

    async def broken_audit(session, **kwargs):
        await original(session, **kwargs)
        await session.flush()
        raise RuntimeError("inert mandatory checkpoint audit failure")

    monkeypatch.setattr(calendar_sync_commit, "write_audit_log", broken_audit)
    with pytest.raises(RuntimeError, match="mandatory checkpoint"):
        await apply(CalendarAccountCommit(store), claim)
    assert await counts(admitted.run_id) == ([], 0, 0)
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.target_entity == "VisitorPass")) == 0
    assert engine.pool.checkedout() == 0
    monkeypatch.setattr(calendar_sync_commit, "write_audit_log", original)
    result = await apply(CalendarAccountCommit(store), claim)
    assert result.checkpoint.status == "ok"
    checkpoints, passes, audits = await counts(admitted.run_id)
    assert len(checkpoints) == passes == audits == 1


@pytest.mark.parametrize("lost_ack", [False, True])
async def test_known_durable_receipt_survives_commit_ack_or_session_cleanup_failure(lost_ack, monkeypatch):
    actor, ids = await setup_accounts(1)
    store = CalendarSyncStore()
    admitted = await admit(store, actor)
    claim = await store.claim_account(admitted, ids[0], authorize=allowed, work=work())
    original_commit, original_close = AsyncSession.commit, AsyncSession.close
    seen = set()

    async def commit_then_lose_ack(session):
        await original_commit(session)
        raise OSError("inert commit acknowledgement lost")

    async def close_then_fail(session):
        await original_close(session)
        if id(session) not in seen:
            seen.add(id(session))
            raise OSError("inert close result lost")

    if lost_ack:
        monkeypatch.setattr(AsyncSession, "commit", commit_then_lose_ack)
    monkeypatch.setattr(AsyncSession, "close", close_then_fail)
    result = await apply(CalendarAccountCommit(store), claim)
    monkeypatch.setattr(AsyncSession, "commit", original_commit)
    monkeypatch.setattr(AsyncSession, "close", original_close)
    assert result.checkpoint.status == "ok"
    checkpoints, passes, audits = await counts(admitted.run_id)
    assert len(checkpoints) == passes == audits == 1
    assert engine.pool.checkedout() == 0


async def test_unresolved_commit_is_explicit_unknown_and_retains_durable_checkpoint_for_read(monkeypatch):
    actor, ids = await setup_accounts(1)
    store = CalendarSyncStore()
    admitted = await admit(store, actor)
    claim = await store.claim_account(admitted, ids[0], authorize=allowed, work=work())
    original_commit = AsyncSession.commit

    async def commit_then_lose_ack(session):
        await original_commit(session)
        raise OSError("inert lost acknowledgement")

    async def unavailable_resolution(self, claim):
        raise OSError("inert resolution database unavailable")

    monkeypatch.setattr(AsyncSession, "commit", commit_then_lose_ack)
    monkeypatch.setattr(CalendarAccountCommit, "_resolve_account", unavailable_resolution)
    with pytest.raises(CalendarOutcomeUnknown, match=str(admitted.run_id)):
        await apply(CalendarAccountCommit(store), claim)
    monkeypatch.setattr(AsyncSession, "commit", original_commit)
    checkpoints, passes, audits = await counts(admitted.run_id)
    assert len(checkpoints) == passes == audits == 1
    assert await store.claim_account(admitted, ids[0], authorize=allowed, work=work()) is None
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("drift", ["generation", "session", "removed"])
async def test_old_fetch_cannot_apply_after_account_generation_session_or_removal_changes(drift):
    actor, ids = await setup_accounts(1)
    store = CalendarSyncStore()
    admitted = await admit(store, actor)
    claim = await store.claim_account(admitted, ids[0], authorize=allowed, work=work())
    async with AsyncSessionLocal() as session:
        account = await session.get(ICloudCalendarAccount, ids[0])
        if drift == "generation":
            account.sync_generation += 1
        elif drift == "session":
            account.encrypted_session_bundle = "new-inert-session"
        else:
            account.is_active = False
        await session.commit()
    with pytest.raises(CalendarOwnershipLost):
        await apply(CalendarAccountCommit(store), claim)
    assert await counts(admitted.run_id) == ([], 0, 0)
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("barrier", ["policy", "flush"])
async def test_workflow_authority_expiry_during_policy_or_final_flush_rolls_back_account(barrier, monkeypatch):
    actor, ids = await setup_accounts(1)
    _, identity, runs, execution, action = await attempted(conditions=[{
        "id": "maintenance", "type": "maintenance_mode.disabled", "config": {}}])
    authority = CalendarWorkflowAuthority(execution, action, runs=runs)
    store = CalendarSyncStore()
    admitted = await store.admit(uuid.UUID(execution["operation_id"]), origin="workflow", actor_user_id=actor,
        execution=execution, authorize=authority.authorize, work=work())
    claim = await store.claim_account(admitted, ids[0], authorize=authority.authorize, work=work())
    async with AsyncSessionLocal() as session:
        run = await session.get(AutomationRun, identity)
        run.lease_expires_at = datetime.now(UTC) + timedelta(milliseconds=300)
        await session.commit()
    original_evaluate, original_flush = calendar_workflow_authority.evaluate_automation_condition, AsyncSession.flush
    final = False

    async def delayed_condition(session, condition, context):
        result = await original_evaluate(session, condition, context)
        if final:
            await session.execute(text("SELECT pg_sleep(0.4)"))
        return result

    async def delayed_final_flush(session, *args, **kwargs):
        checkpoint_audit = any(isinstance(item, AuditLog) and item.action == "icloud_calendar.sync_account" for item in session.new)
        await original_flush(session, *args, **kwargs)
        if checkpoint_audit:
            await asyncio.sleep(0.4)

    async def authorize(session, is_final):
        nonlocal final
        final = is_final
        return await authority.authorize(session, is_final)

    if barrier == "policy":
        monkeypatch.setattr(calendar_workflow_authority, "evaluate_automation_condition", delayed_condition)
    else:
        monkeypatch.setattr(AsyncSession, "flush", delayed_final_flush)
    with pytest.raises(CalendarOwnershipLost, match="authority expired"):
        await apply(CalendarAccountCommit(store), claim, authorize=authorize)
    monkeypatch.setattr(AsyncSession, "flush", original_flush)
    assert await counts(admitted.run_id) == ([], 0, 0)
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.target_entity == "VisitorPass")) == 0
    assert engine.pool.checkedout() == 0


async def test_run_lock_is_acquired_only_after_real_domain_stage_and_conflict_rolls_back():
    actor, ids = await setup_accounts(1)
    store = CalendarSyncStore()
    admitted = await admit(store, actor)
    claim = await store.claim_account(admitted, ids[0], authorize=allowed, work=work())
    domain_staged = asyncio.Event()

    async def staged(session, account, context):
        result = await visitor_stage(claim)(session, account, context)
        domain_staged.set()
        return result

    async def no_authority_fixture(session, final):
        return None

    async with AsyncSessionLocal() as blocker:
        await blocker.scalar(select(ICloudCalendarSyncRun)
            .where(ICloudCalendarSyncRun.id == admitted.run_id).with_for_update())
        task = asyncio.create_task(apply(CalendarAccountCommit(store), claim, stage=staged, authorize=no_authority_fixture))
        await asyncio.wait_for(domain_staged.wait(), 0.8)
        assert not task.done()
        with pytest.raises(DBAPIError):
            await task
        await blocker.rollback()
    assert await counts(admitted.run_id) == ([], 0, 0)
    assert engine.pool.checkedout() == 0
