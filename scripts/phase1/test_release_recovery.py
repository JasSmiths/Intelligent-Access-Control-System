"""Release job PostgreSQL contracts; guarded synthetic namespace and disposable files only.

Diagnostic tests remain ordinary failures until executor/receipt ownership is fixed.
No update commands, Docker calls, package downloads, archives or live restores run.
"""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.db.session import AsyncSessionLocal
from app.models import DependencyUpdateBackup, DependencyUpdateJob, ExternalDependency, User
from app.models.enums import UserRole
from app.services import dependency_updates as owner
from app.services import release_artifacts as artifacts
from app.services.dependency_updates import DependencyUpdateError, DependencyUpdateService

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def release_resources(isolated_resources, monkeypatch, tmp_path):
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE dependency_update_jobs, dependency_update_backups, external_dependencies CASCADE"))
        await session.commit()
    monkeypatch.setattr(owner, "JOB_LOG_DIR", tmp_path / "job-logs")
    monkeypatch.setattr(owner, "_workspace_root", lambda: tmp_path / "repo")
    monkeypatch.setattr(owner, "emit_audit_log", lambda **kwargs: None)
    monkeypatch.setattr(DependencyUpdateService, "sync_enrollment", AsyncMock(return_value={}))
    # Any accidental command execution is a test failure, never a process/network call.
    monkeypatch.setattr(DependencyUpdateService, "_run_command", AsyncMock(side_effect=AssertionError("Unexpected release command")))
    yield


async def admin():
    async with AsyncSessionLocal() as session:
        user = User(username=f"synthetic-{uuid.uuid4().hex}", full_name="Synthetic Admin",
                    password_hash="inert-unused", role=UserRole.ADMIN, is_active=True)
        session.add(user)
        await session.commit()
        return user


async def enqueue(service, user):
    return await service._create_and_start_job(kind="apply", dependency=None,
        target_version="2", backup_id=None, user=user)


async def jobs():
    async with AsyncSessionLocal() as session:
        return list((await session.scalars(select(DependencyUpdateJob))).all())


async def cancel_services(*services):
    await asyncio.gather(*(service.stop() for service in services))


async def seed_job(user, *, state="queued", kind="apply", backup=None, result=None):
    async with AsyncSessionLocal() as session:
        job = DependencyUpdateJob(kind=kind, actor="Synthetic Admin", actor_user_id=user.id,
            status=state, phase=state, target_version="2", backup_id=backup.id if backup else None,
            result=result or {})
        session.add(job)
        await session.commit()
        return job


@pytest.mark.parametrize("running", [False, True])
async def test_concurrent_intake_has_one_queued_or_running_job_across_service_instances(monkeypatch, running):
    user = await admin()
    services = [DependencyUpdateService(), DependencyUpdateService()]
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold(*args, **kwargs):
        entered.set()
        await release.wait()

    for service in services:
        monkeypatch.setattr(service, "_run_apply_job" if running else "_run_job", hold)
    start = asyncio.Event()

    async def submit(service):
        await start.wait()
        return await enqueue(service, user)

    submissions = [asyncio.create_task(submit(service)) for service in services]
    try:
        start.set()
        results = await asyncio.wait_for(asyncio.gather(*submissions, return_exceptions=True), 8)
        await asyncio.wait_for(entered.wait(), 8)
        assert sum(isinstance(result, dict) for result in results) == 1
        failures = [result for result in results if isinstance(result, Exception)]
        assert len(failures) == 1 and isinstance(failures[0], DependencyUpdateError)
        rows = await jobs()
        assert len(rows) == 1 and rows[0].status == ("running" if running else "queued")
    finally:
        for task in submissions:
            if not task.done():
                task.cancel()
        await asyncio.gather(*submissions, return_exceptions=True)
        await cancel_services(*services)
        release.set()


async def test_restart_marks_orphan_jobs_for_review_without_losing_existing_receipts(monkeypatch):
    user = await admin()
    receipt = {"transaction_id": "synthetic-owned-transaction", "status": "source_prepared"}
    queued = await seed_job(user, result={"promotion": receipt})
    running = await seed_job(user, state="running", result={"restoration": receipt})
    completed = await seed_job(user, state="completed", result={"ok": True})
    service = DependencyUpdateService()
    execute = AsyncMock(side_effect=AssertionError("Recovery must not resume jobs"))
    monkeypatch.setattr(service, "_run_job", execute)
    await service._mark_interrupted_jobs()
    current = {row.id: row for row in await jobs()}
    for previous, key in [(queued, "promotion"), (running, "restoration")]:
        row = current[previous.id]
        assert row.status == "failed" and row.phase == "review_required" and row.ended_at is not None
        assert row.result[key] == receipt and row.result["automatically_resumed"] is False
    assert current[completed.id].status == "completed" and current[completed.id].result == {"ok": True}
    execute.assert_not_awaited()


async def test_diagnostic_recovered_job_cannot_be_resurrected_by_delayed_worker(monkeypatch):
    user = await admin()
    job = await seed_job(user)
    service = DependencyUpdateService()
    await service._mark_interrupted_jobs()
    apply = AsyncMock()
    monkeypatch.setattr(service, "_run_apply_job", apply)
    await service._run_job(job.id, kind="apply", user=user)
    apply.assert_not_awaited()
    row = (await jobs())[0]
    assert row.status == "failed" and row.phase == "review_required"


async def test_diagnostic_another_startup_cannot_retire_a_live_executor_and_admit_second_job(monkeypatch):
    user = await admin()
    active, newcomer = DependencyUpdateService(), DependencyUpdateService()
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold(*args, **kwargs):
        entered.set()
        await release.wait()

    monkeypatch.setattr(active, "_run_apply_job", hold)
    monkeypatch.setattr(newcomer, "_run_apply_job", hold)
    try:
        first = await enqueue(active, user)
        await asyncio.wait_for(entered.wait(), 8)
        try:
            await asyncio.wait_for(newcomer._mark_interrupted_jobs(), 8)
        except DependencyUpdateError:
            pass  # Explicit refusal is permitted while an executor owns work.
        rows = {str(row.id): row for row in await jobs()}
        assert rows[first["id"]].status == "running"
        with pytest.raises(DependencyUpdateError):
            await enqueue(newcomer, user)
    finally:
        await cancel_services(active, newcomer)
        release.set()


async def test_cancellation_persists_review_and_preserves_receipt_without_auto_restore(monkeypatch):
    user = await admin()
    receipt = {"transaction_id": "synthetic-retained", "status": "source_prepared"}
    job = await seed_job(user, result={"promotion": receipt})
    service = DependencyUpdateService()
    entered = asyncio.Event()

    async def paused(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_run_apply_job", paused)
    restore = AsyncMock(side_effect=AssertionError("Cancellation cannot restore files"))
    monkeypatch.setattr(service, "_restore_backup_manifests", restore)
    task = asyncio.create_task(service._run_job(job.id, kind="apply", user=user))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 8)
        row = (await jobs())[0]
        assert row.status == "failed" and row.phase == "review_required"
        assert row.result["promotion"] == receipt and row.result["automatically_resumed"] is False
        restore.assert_not_awaited()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@asynccontextmanager
async def fail_receipt_commit(job_id, key, *, persistent=False, terminal=False):
    """A deferred PostgreSQL constraint error aborts the actual metadata commit.

    nextval survives rollback, so transient mode fails exactly the first commit.
    All objects are uniquely named and explicitly dropped in this isolated DB.
    """
    suffix = uuid.uuid4().hex
    function, sequence, trigger = (f"iacs_p1_release_{part}_{suffix}" for part in ("fn", "seq", "trg"))
    condition = f"nextval('{sequence}') > 0" if terminal else ("TRUE" if persistent else f"nextval('{sequence}') = 1")
    receipt_condition = f"(NEW.result ? '{key}')"
    if terminal:
        receipt_condition = f"({receipt_condition} OR (SELECT is_called FROM {sequence}))"
    async with AsyncSessionLocal() as session:
        await session.execute(text(f"CREATE SEQUENCE {sequence}"))
        await session.execute(text(f"""CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN
            IF NEW.id = '{job_id}'::uuid AND {receipt_condition} AND {condition} THEN
              RAISE EXCEPTION 'synthetic release metadata commit failure' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
          END $$"""))
        await session.execute(text(f"""CREATE CONSTRAINT TRIGGER {trigger} AFTER UPDATE ON dependency_update_jobs
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {function}()"""))
        await session.commit()
    try:
        yield
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(text(f"DROP TRIGGER IF EXISTS {trigger} ON dependency_update_jobs"))
            await session.execute(text(f"DROP FUNCTION IF EXISTS {function}()"))
            await session.execute(text(f"DROP SEQUENCE IF EXISTS {sequence}"))
            await session.commit()


async def setup_release(tmp_path, user, *, kind="apply"):
    root = tmp_path / "repo"
    for name, content in {"backend/pyproject.toml": "synthetic manifest", "backend/uv.lock": "old lock",
                          "frontend/package.json": "{}", "frontend/package-lock.json": "{}"}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    staged = tmp_path / "candidate"
    (staged / "backend").mkdir(parents=True)
    (staged / "backend/uv.lock").write_text("new synthetic lock")
    async with AsyncSessionLocal() as session:
        revisions = list((await session.scalars(text("SELECT version_num FROM alembic_version"))).all())
    identity = artifacts.capture_source(root, tmp_path / "source-backup", schema_revisions=revisions, image_identity="synthetic-image")
    async with AsyncSessionLocal() as session:
        dependency = ExternalDependency(ecosystem="python", package_name="synthetic", normalized_name="synthetic",
            current_version="1", latest_version="2", manifest_path="backend/pyproject.toml", is_direct=True)
        session.add(dependency)
        await session.flush()
        backup = DependencyUpdateBackup(dependency_id=dependency.id, package_name="synthetic", ecosystem="python",
            version="1", reason="synthetic-test", archive_path=str(tmp_path / "inert-archive"), storage_root=str(tmp_path),
            checksum_sha256="0" * 64, metadata_={"release_identity": identity})
        session.add(backup)
        await session.flush()
        job = DependencyUpdateJob(dependency_id=dependency.id, kind=kind, actor="Synthetic Admin", actor_user_id=user.id,
            target_version="2", backup_id=backup.id, status="queued", phase="queued", result={})
        session.add(job)
        await session.commit()
    return root, staged, identity, dependency, backup, job


@pytest.mark.parametrize("persistent", [False, True, "terminal"])
async def test_promotion_commit_failure_preserves_file_truth_and_never_automatically_restores(monkeypatch, tmp_path, persistent):
    user = await admin()
    root, staged, identity, dependency, backup, job = await setup_release(tmp_path, user)
    service = DependencyUpdateService()
    monkeypatch.setattr(service, "create_backup", AsyncMock(return_value={"id": str(backup.id), "metadata": backup.metadata_}))
    restore = AsyncMock(side_effect=AssertionError("No automatic source restoration"))
    monkeypatch.setattr(service, "_restore_backup_manifests", restore)
    receipts = []

    async def promote(*args, **kwargs):
        receipt = await artifacts.prepare_and_publish(staged, root, ["backend/uv.lock"],
            expected_source=identity["files"], release_context=identity, checks=["synthetic inert bytes"])
        receipts.append(receipt)
        return receipt

    monkeypatch.setattr(service, "_run_update_commands", promote)
    async with fail_receipt_commit(job.id, "promotion", persistent=bool(persistent), terminal=persistent == "terminal"):
        await service._run_job(job.id, kind="apply", user=user)
    receipt = receipts[0]
    assert (root / "backend/uv.lock").read_text() == "new synthetic lock"
    marker = root / "data/backend/release-transactions" / receipt["transaction_id"] / "transaction.json"
    assert json.loads(marker.read_text())["state"] == "completed"
    assert receipt["files"]["backend/uv.lock"] == artifacts.sha256(root / "backend/uv.lock")
    assert (root / "data/backend/release-candidates" / receipt["candidate_id"] / "release.json").is_file()
    async with AsyncSessionLocal() as session:
        saved = await session.get(DependencyUpdateJob, job.id)
        saved_backup = await session.get(DependencyUpdateBackup, backup.id)
        if persistent == "terminal":
            assert saved.status == "running" and "promotion" not in saved.result
            event = service._job_streams[str(job.id)][-1]
            assert event["phase"] == "review_required"
            assert event["diagnosis"]["database_outcome"] == "unverified"
            assert event["diagnosis"]["receipt_persistence"]["transaction_id"] == receipt["transaction_id"]
        else:
            assert saved.status == "failed" and saved.result["diagnosis"]["review_required"]
            assert "synthetic release metadata commit failure" in saved.error
        if persistent == "terminal":
            assert "promotion" not in saved_backup.metadata_
        elif persistent:
            assert "promotion" not in saved.result
            assert saved.result["receipt_persistence"] == {
                "status": "journal_only", "kind": "promotion", "transaction_id": receipt["transaction_id"],
                "review_required": True,
            }
            assert saved.result["diagnosis"]["source_status"] == "unverified"
        else:
            assert saved.result["promotion"] == saved_backup.metadata_["promotion"] == receipt
            assert saved.result["rollback"]["attempted"] is False
            assert saved.result["diagnosis"]["source_status"] == "prepared"
    restore.assert_not_awaited()
    service._run_command.assert_not_awaited()


@pytest.mark.parametrize("persistent", [False, True, "terminal"])
async def test_diagnostic_restoration_receipt_survives_metadata_commit_failure(monkeypatch, tmp_path, persistent):
    user = await admin()
    root, staged, identity, dependency, backup, job = await setup_release(tmp_path, user, kind="restore")
    service = DependencyUpdateService()
    monkeypatch.setattr(service, "_validate_backup_archive", AsyncMock(return_value={"manifest_count": 4, "artifact_count": 0}))
    receipts = []

    async def restore(*args, **kwargs):
        # Exercise only reversible file ownership; no archive, schema or runtime restoration.
        receipt = artifacts.publish_manifest_set(staged, root, ["backend/uv.lock"],
            expected={"backend/uv.lock": identity["files"]["backend/uv.lock"]}, expected_source=identity["files"])
        receipts.append(receipt)
        return receipt

    monkeypatch.setattr(service, "_restore_backup_manifests", restore)
    async with fail_receipt_commit(job.id, "restoration", persistent=bool(persistent), terminal=persistent == "terminal"):
        await service._run_job(job.id, kind="restore", user=user)
    assert len(receipts) == 1 and (root / "backend/uv.lock").read_text() == "new synthetic lock"
    async with AsyncSessionLocal() as session:
        saved = await session.get(DependencyUpdateJob, job.id)
        saved_backup = await session.get(DependencyUpdateBackup, backup.id)
        if persistent == "terminal":
            assert saved.status == "running" and "restoration" not in saved.result
            event = service._job_streams[str(job.id)][-1]
            assert event["phase"] == "review_required"
            assert event["diagnosis"]["database_outcome"] == "unverified"
            assert event["diagnosis"]["receipt_persistence"]["transaction_id"] == receipts[0]["transaction_id"]
        else:
            assert saved.status == "failed"
            assert "synthetic release metadata commit failure" in saved.error
        if persistent == "terminal":
            assert "restoration" not in saved_backup.metadata_
        elif persistent:
            assert "restoration" not in saved.result and "restoration" not in saved_backup.metadata_
            assert saved.result["receipt_persistence"] == {
                "status": "journal_only", "kind": "restoration", "transaction_id": receipts[0]["transaction_id"],
                "review_required": True,
            }
            assert saved.result["diagnosis"]["source_status"] == "unverified"
        else:
            assert saved.result["restoration"] == saved_backup.metadata_["restoration"] == receipts[0]
            assert saved.result["diagnosis"]["source_status"] == "prepared"
    marker = root / "data/backend/release-transactions" / receipts[0]["transaction_id"] / "transaction.json"
    assert json.loads(marker.read_text())["state"] == "completed"
    service._run_command.assert_not_awaited()


async def test_startup_cannot_retire_queued_job_before_its_task_starts(monkeypatch):
    user = await admin()
    service, newcomer = DependencyUpdateService(), DependencyUpdateService()
    entered, release = asyncio.Event(), asyncio.Event()

    async def pending(*args, **kwargs):
        entered.set()
        await release.wait()

    monkeypatch.setattr(service, "_run_job", pending)
    try:
        accepted = await enqueue(service, user)
        await asyncio.wait_for(entered.wait(), 8)
        await asyncio.wait_for(newcomer._mark_interrupted_jobs(), 8)
        row = (await jobs())[0]
        assert str(row.id) == accepted["id"] and row.status == "queued"
        with pytest.raises(DependencyUpdateError):
            await enqueue(newcomer, user)
    finally:
        await cancel_services(service, newcomer)
        release.set()
    executor = await newcomer._acquire_executor()
    assert executor is not None, "Cancelled queued task leaked its session advisory lock"
    await newcomer._release_executor(executor)


@pytest.mark.parametrize("loss", ["job_retired", "lock_removed", "connection_lost"])
async def test_final_current_ownership_refuses_mutation_after_executor_loss(monkeypatch, loss):
    user = await admin()
    job = await seed_job(user)
    service = DependencyUpdateService()
    entered, release = asyncio.Event(), asyncio.Event()
    mutation = AsyncMock(side_effect=AssertionError("Lost executor must not mutate source"))

    async def prepared(job_id, *args, **kwargs):
        entered.set()
        await release.wait()
        await service._assert_executor(job_id)
        await mutation()

    monkeypatch.setattr(service, "_run_apply_job", prepared)
    task = asyncio.create_task(service._run_job(job.id, kind="apply", user=user))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        executor = service._executors[job.id]
        if loss == "job_retired":
            async with AsyncSessionLocal() as session:
                row = await session.get(DependencyUpdateJob, job.id)
                row.status, row.phase = "failed", "review_required"
                row.result = {"review_required": True, "external_retirement": True}
                await session.commit()
        elif loss == "lock_removed":
            assert await executor.connection.scalar(text("SELECT pg_advisory_unlock(:namespace, :key)"),
                {"namespace": owner._EXECUTOR_NAMESPACE, "key": owner._EXECUTOR_KEY})
            await executor.connection.commit()
        else:
            await executor.connection.invalidate()
        release.set()
        await asyncio.wait_for(task, 8)
        mutation.assert_not_awaited()
        row = (await jobs())[0]
        assert row.status == "failed"
        if loss == "job_retired":
            assert row.result == {"review_required": True, "external_retirement": True}
        else:
            assert "ownership was lost" in row.error
        assert not service._executors
        replacement = await service._acquire_executor()
        assert replacement is not None
        await service._release_executor(replacement)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.stop()
