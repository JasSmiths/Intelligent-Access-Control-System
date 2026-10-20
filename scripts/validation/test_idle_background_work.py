"""Idle budgets and recovery through real owners in isolated PostgreSQL."""
from test_recovery_boundaries import isolated_resources as isolated_resources
from test_access_window_persistence import ready, value, commit_window, window_resources as window_resources

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError

from app.services.durable_work import WorkReadiness
from app.db.session import AsyncSessionLocal, engine
from app.models import AuditLog, AutomationRule, AutomationRun, LprCoordinatorState, LprIngestEvent
from app.services import access_events, automations
from app.services.access.pipeline_contracts import PipelineLeaseLost
from app.services.access.pipeline_store import AccessPipelineStore
from app.services.access.read_codec import read_from_ingest
from app.services.access.window_store import AccessWindowStore
from app.services.automation_execution import AutomationRunStore

pytestmark = pytest.mark.asyncio


@contextmanager
def statements():
    captured = []
    def record(connection, cursor, statement, parameters, context, many):
        captured.append((statement, parameters))
    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        yield captured
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)


async def terminal(status="success", *, version=1, rule=True):
    async with AsyncSessionLocal() as session:
        owner = AutomationRule(name="Synthetic idle accounting", is_active=False,
            triggers=[], trigger_keys=[], conditions=[], actions=[]) if rule else None
        if owner:
            session.add(owner)
            await session.flush()
        run = AutomationRun(rule_id=owner.id if owner else None, trigger_key="synthetic.idle",
            status="processing", started_at=datetime.now(UTC), recovery_version=version,
            action_plan=[], action_results=[], condition_results=[], context={}, trigger_payload={})
        session.add(run)
        AutomationRunStore._terminal(run, status, datetime.now(UTC))
        await session.commit()
        return run.id, owner.id if owner else None


@pytest.mark.parametrize("status", ["success", "failed", "skipped", "review_required"])
async def test_terminal_accounting_is_atomic_and_survives_restart_and_audit_purge(status):
    identity, rule_id = await terminal(status)
    async with AsyncSessionLocal() as session:
        assert (await session.get(AutomationRun, identity)).completion_audit_pending
    await automations.AutomationService().recover_completion_audits()
    async with AsyncSessionLocal() as session:
        run = await session.get(AutomationRun, identity)
        assert not run.completion_audit_pending
        assert (await session.get(AutomationRule, rule_id)).run_count == 1
        audit = await session.get(AuditLog, uuid.uuid5(identity, "completion-audit"))
        assert audit.action == "automation_rule." + status
        await session.delete(audit)  # Simulates an explicitly authorized retention purge.
        await session.commit()
    await automations.AutomationService().recover_completion_audits()
    await automations.AutomationService().account_completed_run(identity)
    async with AsyncSessionLocal() as session:
        assert (await session.get(AutomationRule, rule_id)).run_count == 1
        assert (await session.scalars(select(AuditLog))).all() == []


async def test_failed_audit_transaction_remains_pending_then_concurrent_retry_counts_once(monkeypatch):
    identity, rule_id = await terminal()
    original = automations.write_audit_log
    async def fail_after_insert(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("synthetic audit transaction failure")
    monkeypatch.setattr(automations, "write_audit_log", fail_after_insert)
    with pytest.raises(RuntimeError, match="synthetic audit"):
        await automations.AutomationService().account_completed_run(identity)
    async with AsyncSessionLocal() as session:
        assert (await session.get(AutomationRun, identity)).completion_audit_pending
        assert (await session.get(AutomationRule, rule_id)).run_count == 0
        assert (await session.scalars(select(AuditLog))).all() == []
    monkeypatch.setattr(automations, "write_audit_log", original)
    await asyncio.wait_for(asyncio.gather(*(
        automations.AutomationService().account_completed_run(identity) for _ in range(4))), 10)
    async with AsyncSessionLocal() as session:
        assert not (await session.get(AutomationRun, identity)).completion_audit_pending
        assert (await session.get(AutomationRule, rule_id)).run_count == 1
        assert len((await session.scalars(select(AuditLog))).all()) == 1


async def test_existing_deterministic_receipt_clears_checkpoint_without_recounting():
    identity, rule_id = await terminal()
    async with AsyncSessionLocal() as session:
        (await session.get(AutomationRule, rule_id)).run_count = 7
        session.add(AuditLog(id=uuid.uuid5(identity, "completion-audit"), category="automation_engine",
            action="automation_rule.success", actor="Synthetic", metadata_={"run_id":str(identity)}))
        await session.commit()
    await automations.AutomationService().recover_completion_audits()
    async with AsyncSessionLocal() as session:
        assert not (await session.get(AutomationRun, identity)).completion_audit_pending
        assert (await session.get(AutomationRule, rule_id)).run_count == 7
        assert len((await session.scalars(select(AuditLog))).all()) == 1


async def test_deleted_rule_can_account_but_legacy_runs_are_never_recovered():
    identity, rule_id = await terminal()
    legacy, _ = await terminal(version=None)
    async with AsyncSessionLocal() as session:
        await session.delete(await session.get(AutomationRule, rule_id))
        await session.commit()
    await automations.AutomationService().recover_completion_audits()
    async with AsyncSessionLocal() as session:
        audit = await session.get(AuditLog, uuid.uuid5(identity, "completion-audit"))
        assert audit.target_label == "Deleted automation rule"
        assert not (await session.get(AutomationRun, legacy)).completion_audit_pending
        assert len((await session.scalars(select(AuditLog))).all()) == 1


@pytest.mark.parametrize("version,status", [(None,"success"),(0,"success"),(1,"queued"),(1,"processing"),(1,"running")])
async def test_pending_constraint_rejects_nonterminal_or_legacy_rows(version, status):
    identity, _ = await terminal()
    async with AsyncSessionLocal() as session:
        row = await session.get(AutomationRun, identity)
        row.recovery_version, row.status = version, status
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_empty_workers_have_one_select_and_no_locks_writes_or_configuration_reads(monkeypatch):
    service = access_events.AccessEventService()
    claim = await service._pipeline.acquire_coordinator()
    monkeypatch.setattr(access_events, "get_runtime_config", AsyncMock(side_effect=AssertionError("idle config read")))
    service._last_probe_generation = service._wake.generation
    passes = 0
    async def safety_check(generation, pending, *, fallback_seconds):
        nonlocal passes
        assert not pending.ready
        passes += 1
        if passes == 8:
            service._stop_event.set()
    monkeypatch.setattr(service._wake, "wait", safety_check)
    with statements() as queries:
        await service._wait_until_ready(owned=True)
    assert passes == len(queries) == 8
    for sql, _ in queries:
        assert sql.lstrip().upper().startswith(("SELECT", "WITH"))
        assert not any(word in sql.upper() for word in ("INSERT ", "UPDATE ", "DELETE ", "PG_NOTIFY"))
        assert "FOR UPDATE" not in sql.upper()
        assert "normalized_payload" not in sql
    with statements() as queries:
        await automations.AutomationService().recover_completion_audits()
    assert len(queries) == 1 and "audit_logs" not in queries[0][0]
    assert "completion_audit_pending IS true" in queries[0][0]


@pytest.mark.parametrize("checkpoint", ["flush_clock", "flush_upper_ordinal", "flush_completed_ordinal"])
async def test_retained_flush_checkpoint_enters_existing_recovery_path(monkeypatch, checkpoint):
    service = access_events.AccessEventService()
    claim = await service._pipeline.acquire_coordinator()
    async with AsyncSessionLocal() as session:
        state = await session.get(LprCoordinatorState, 1)
        setattr(state, checkpoint, datetime.now(UTC) if checkpoint == "flush_clock" else 0)
        await session.commit()
    assert await service._pipeline.has_pending_work(claim)
    configure_inert_worker(monkeypatch)
    await service._process_boundary(claim)
    assert not await service._pipeline.has_pending_work(claim)


def configure_inert_worker(monkeypatch):
    monkeypatch.setattr(access_events, "get_runtime_config", AsyncMock(return_value=SimpleNamespace(
        lpr_debounce_quiet_seconds=0, lpr_debounce_max_seconds=0)))
    monkeypatch.setattr(access_events, "is_maintenance_mode_active", AsyncMock(return_value=False))


@pytest.mark.parametrize("arrival", ["wake", "fallback", "during_probe"])
async def test_actual_worker_observes_committed_arrivals_and_drains_shutdown(monkeypatch, arrival):
    configure_inert_worker(monkeypatch)
    service = access_events.AccessEventService()
    idle, handled = asyncio.Event(), asyncio.Event()
    original = service._pipeline.has_pending_work
    inserted = False
    async def probe(claim):
        nonlocal inserted
        pending = await original(claim)
        if not pending:
            if arrival == "during_probe" and not inserted:
                await ready(value())
                inserted = True
            idle.set()
        return pending
    async def handle(claim):
        async with AsyncSessionLocal() as session:
            await service._pipeline.suppress_read_in_session(session, claim, reason="synthetic-inert")
            await session.commit()
        handled.set()
        return None
    monkeypatch.setattr(service._pipeline, "has_pending_work", probe)
    monkeypatch.setattr(service, "_handle_queued_read", handle)
    if arrival == "wake":
        # Disable the timer only for this proof: the local wake alone must suffice.
        async def wait_for_local_hint():
            await service._wake.wait(service._wait_generation, WorkReadiness(), fallback_seconds=float("inf"))
        monkeypatch.setattr(service, "_wait_for_wake", wait_for_local_hint)
    task = asyncio.create_task(service._process_queue())
    try:
        await asyncio.wait_for(idle.wait(), 5)
        if arrival != "during_probe":
            await ready(value())
        if arrival == "wake":
            service._wake.set()
        await asyncio.wait_for(handled.wait(), 5)
    finally:
        service._stop_event.set()
        service._wake.set()
        await asyncio.wait_for(task, 5)
    async with AsyncSessionLocal() as session:
        state = await session.get(LprCoordinatorState, 1)
        assert state.lease_token is None and state.handled_ready_ordinal > 0


async def test_probe_rejects_expired_lease_and_old_owner_after_takeover():
    pipeline = AccessPipelineStore()
    old = await pipeline.acquire_coordinator()
    async with AsyncSessionLocal() as session:
        await session.execute(text("UPDATE lpr_coordinator_state SET lease_until=clock_timestamp()-interval '1 second'"))
        await session.commit()
    with pytest.raises(PipelineLeaseLost):
        await pipeline.has_pending_work(old)
    current = await pipeline.acquire_coordinator()
    assert current.epoch > old.epoch
    with pytest.raises(PipelineLeaseLost):
        await pipeline.has_pending_work(old)
    assert not await pipeline.has_pending_work(current)


async def test_cancellation_during_probe_drains_operation_and_releases_lease(monkeypatch):
    service = access_events.AccessEventService()
    entered, drained = asyncio.Event(), asyncio.Event()
    async def probe(claim):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            drained.set()
    monkeypatch.setattr(service._pipeline, "has_pending_work", probe)
    task = asyncio.create_task(service._process_queue())
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert drained.is_set()
    async with AsyncSessionLocal() as session:
        assert (await session.get(LprCoordinatorState, 1)).lease_token is None


async def test_ready_terminal_prefix_advances_before_returning_to_idle(monkeypatch):
    configure_inert_worker(monkeypatch)
    service = access_events.AccessEventService()
    claim = await service._pipeline.acquire_coordinator()
    for _ in range(55):
        await ready(value())
    async with AsyncSessionLocal() as session:
        await session.execute(text("UPDATE lpr_ingest_events SET status='skipped'"))
        upper = await session.scalar(text("SELECT max(ready_ordinal) FROM lpr_ingest_events"))
        await session.commit()
    assert await service._pipeline.has_pending_work(claim)
    await service._process_boundary(claim)
    async with AsyncSessionLocal() as session:
        assert (await session.get(LprCoordinatorState, 1)).handled_ready_ordinal == upper
    assert not await service._pipeline.has_pending_work(claim)


async def test_open_debounce_window_and_preparing_boundary_are_not_hidden(monkeypatch):
    configure_inert_worker(monkeypatch)
    service = access_events.AccessEventService()
    pipeline, windows = service._pipeline, AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    await ready(value())
    await pipeline.claim_next_read(coordinator)
    assert await pipeline.has_pending_work(coordinator)
    boundary = await pipeline.boundary(coordinator)
    assert boundary.phase == "preparing"
    claim = await pipeline.recover_preparing_read(coordinator)
    async with AsyncSessionLocal() as session:
        read = read_from_ingest(await session.get(LprIngestEvent, claim.ingest_id))
        await windows.append_in_session(session, claim, read, threshold=1)
        await pipeline.begin_flush_in_session(session, claim)
        await session.commit()
    assert await pipeline.next_flush_window(coordinator, quiet_seconds=1e9, max_seconds=1e9) is None
    assert await pipeline.has_pending_work(coordinator)  # Open window alone must wake flushing.
    finalized = []
    async def finalize(claim):
        await commit_window(windows, claim)
        finalized.append(claim.window_id)
    monkeypatch.setattr(service, "_finalize_window", finalize)
    assert await service._process_boundary(coordinator)
    assert len(finalized) == 1
    assert not await pipeline.has_pending_work(coordinator)
