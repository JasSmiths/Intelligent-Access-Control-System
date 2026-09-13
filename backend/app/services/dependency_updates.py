import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ai.providers import ChatMessageInput, ProviderNotConfiguredError, complete_with_provider_options, get_llm_provider
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal, engine
from app.models import (
    DependencyUpdateAnalysis,
    DependencyUpdateBackup,
    DependencyUpdateJob,
    ExternalDependency,
    SystemSetting,
    User,
)
from app.services.event_bus import event_bus
from app.services.dependency_process import DependencyProcessTimeout, run_dependency_process
from app.services.release_artifacts import (
    BUILD_INPUTS, ReleaseArtifactError, assert_manifest_restore_compatible,
    capture_source, owned_release_io, prepare_and_publish, publish_manifest_set_async, safe_file, sha256, source_files, validate_source,
)
from app.services.settings import get_runtime_config, update_settings
from app.services.telemetry import (
    TELEMETRY_CATEGORY_DEPENDENCY_UPDATES,
    actor_from_user,
    emit_audit_log,
    sanitize_payload,
    telemetry,
    utc_now,
)

logger = get_logger(__name__)

BACKUP_CONTAINER_ROOT = Path("/app/update-backups")
GENERATED_COMPOSE = "docker-compose.update-backups.generated.yml"
JOB_LOG_DIR = settings.log_dir / "dependency-updates"
JOB_STREAM_LIMIT = 500
_MOUNT_OPTIONS_UNSET = object()
# Two signed int32 keys identify this owner in pg_locks (objsubid=2).
_EXECUTOR_NAMESPACE = 0x49414353
_EXECUTOR_KEY = 0x44455053
_INTAKE_LOCK = "SELECT pg_advisory_xact_lock(hashtext('iacs:dependency-update-intake'))"


@dataclass
class _DependencyExecutor:
    connection: AsyncConnection
    backend_pid: int
    lost: bool = False
    task: asyncio.Task | None = None



class DependencyUpdateError(RuntimeError):
    """Raised when the dependency update engine cannot complete an operation."""


class DependencyCommandError(DependencyUpdateError):
    """Raised when an update command fails and the captured output is needed for recovery."""

    def __init__(self, command: list[str], returncode: int, output: str):
        self.command = command
        self.returncode = returncode
        self.output = output
        super().__init__(f"Command failed with exit code {returncode}: {' '.join(command)}")


@dataclass(frozen=True)
class NpmRecoveryPlan:
    strategy: str
    summary: str
    specs: list[str]
    regenerate_lockfile: bool = False


@dataclass(frozen=True)
class ManifestDependency:
    ecosystem: str
    package_name: str
    current_version: str | None
    dependant_area: str
    manifest_path: str
    manifest_section: str
    requirement_spec: str | None
    is_direct: bool
    metadata: dict[str, Any]

    @property
    def normalized_name(self) -> str:
        return normalize_package_name(self.package_name)


class DependencyUpdateService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._job_tasks: dict[str, asyncio.Task[None]] = {}
        self._job_streams: dict[str, list[dict[str, Any]]] = {}
        self._job_waiters: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._periodic_scan_task: asyncio.Task[None] | None = None
        self._executors: dict[uuid.UUID, _DependencyExecutor] = {}
        self._unpersisted_receipts: dict[uuid.UUID, tuple[str, dict]] = {}

    async def start(self) -> None:
        await self._mark_interrupted_jobs()
        await self.sync_enrollment(reason="boot")
        await self.validate_storage(mark_active=True)
        if self._periodic_scan_task is None or self._periodic_scan_task.done():
            self._periodic_scan_task = asyncio.create_task(self._periodic_update_scan(), name="dependency-update-periodic-scan")

    async def stop(self) -> None:
        if self._periodic_scan_task and not self._periodic_scan_task.done():
            self._periodic_scan_task.cancel()
            await asyncio.gather(self._periodic_scan_task, return_exceptions=True)
        self._periodic_scan_task = None
        for task in list(self._job_tasks.values()):
            if not task.done():
                task.cancel()
        if self._job_tasks:
            await asyncio.gather(*self._job_tasks.values(), return_exceptions=True)
        self._job_tasks.clear()
        # A task cancelled before its first instruction cannot enter its finally.
        for job_id, executor in list(self._executors.items()):
            await self._release_executor(executor)
            self._executors.pop(job_id, None)

    async def _acquire_executor(self) -> _DependencyExecutor | None:
        """A dedicated session owns the executor from queue intake through cleanup."""
        connection = await engine.connect()
        try:
            acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(:namespace, :key)"),
                {"namespace": _EXECUTOR_NAMESPACE, "key": _EXECUTOR_KEY})
            if not acquired:
                await connection.close()
                return None
            pid = await connection.scalar(text("SELECT pg_backend_pid()"))
            await connection.commit()  # Session lock survives; no idle transaction.
            return _DependencyExecutor(connection, int(pid))
        except BaseException:
            # Acquisition may have reached PostgreSQL before cancellation.
            # Never return an uncertain session lock to the connection pool.
            await connection.invalidate()
            await connection.close()
            raise

    async def _release_executor(self, executor: _DependencyExecutor) -> None:
        async def release() -> None:
            connection = executor.connection
            try:
                async with asyncio.timeout(5):
                    if executor.lost or connection.invalidated:
                        await connection.invalidate()
                    else:
                        await connection.rollback()
                        unlocked = await connection.scalar(text("SELECT pg_advisory_unlock(:namespace, :key)"),
                            {"namespace": _EXECUTOR_NAMESPACE, "key": _EXECUTOR_KEY})
                        if not unlocked:
                            await connection.invalidate()
                        else:
                            await connection.commit()
            except BaseException:
                await connection.invalidate()
            finally:
                executor.lost = True
                await connection.close()

        cleanup = asyncio.create_task(release())
        cancellation = None
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError as exc:
                cancellation = exc
        cleanup.result()
        if cancellation is not None:
            raise cancellation

    async def _assert_executor(self, job_id: uuid.UUID, *, require_running: bool = True) -> None:
        """Do not reconnect/reacquire after loss, or promote a retired job."""
        executor = self._executors.get(job_id)
        if executor is None or executor.lost or executor.connection.closed or executor.connection.invalidated:
            if executor is not None:
                executor.lost = True
            raise DependencyUpdateError("Dependency executor ownership was lost; source mutation is refused.")
        try:
            connection = executor.connection
            valid = await connection.scalar(text("""
                SELECT pg_backend_pid() = :pid AND EXISTS (
                    SELECT 1 FROM pg_locks WHERE locktype = 'advisory'
                    AND pid = pg_backend_pid() AND granted
                    AND classid = :namespace AND objid = :key AND objsubid = 2)
                AND (:require_running = FALSE OR EXISTS (
                    SELECT 1 FROM dependency_update_jobs WHERE id = :job_id AND status = 'running'))
            """), {"pid": executor.backend_pid, "namespace": _EXECUTOR_NAMESPACE,
                   "key": _EXECUTOR_KEY, "job_id": job_id, "require_running": require_running})
            await connection.commit()
            if not valid:
                raise DependencyUpdateError("Dependency executor or running job ownership was lost; source mutation is refused.")
        except BaseException:
            executor.lost = True
            raise

    async def _mark_interrupted_jobs(self) -> None:
        """Only an unowned generation can be retired; restart never resumes work."""
        executor = await self._acquire_executor()
        if executor is None:
            return
        try:
            async with AsyncSessionLocal(bind=executor.connection) as session:
                await session.execute(text(_INTAKE_LOCK))
                rows = (await session.scalars(select(DependencyUpdateJob).where(
                    DependencyUpdateJob.status.in_(["queued", "running"])).with_for_update())).all()
                for row in rows:
                    row.status, row.phase, row.ended_at = "failed", "review_required", utc_now()
                    row.error = "Update worker stopped. Inspect retained candidate and manifest transaction evidence before recovery."
                    row.result = {**(row.result or {}), "review_required": True, "automatically_resumed": False}
                await session.commit()
        finally:
            await self._release_executor(executor)

    async def _record_interrupted_job(self, job_id: uuid.UUID) -> None:
        async with AsyncSessionLocal() as session:
            row = await session.get(DependencyUpdateJob, job_id, with_for_update=True)
            if row is not None and row.status in {"queued", "running"}:
                row.status, row.phase, row.ended_at = "failed", "review_required", utc_now()
                row.error = "Update cancelled. Its retained transaction records determine the source outcome; no automatic rollback was attempted."
                row.result = {**(row.result or {}), "review_required": True, "automatically_resumed": False}
                await session.commit()

    async def sync_enrollment(self, *, reason: str = "manual", user: User | None = None) -> dict[str, Any]:
        trace = telemetry.start_trace(
            "Dependency enrollment sync",
            category=TELEMETRY_CATEGORY_DEPENDENCY_UPDATES,
            actor=actor_from_user(user),
            source=reason,
            context={"reason": reason},
        )
        span = trace.start_span("Parse dependency manifests")
        try:
            async with self._lock:
                discovered = await owned_release_io(self._discover_dependencies)
                span.finish(output_payload={"dependencies": len(discovered)})
                changed = await self._persist_discovered_dependencies(discovered)
            await event_bus.publish(
                "dependency_updates.enrollment.synced",
                {"reason": reason, "discovered": len(discovered), **changed},
            )
            emit_audit_log(
                category=TELEMETRY_CATEGORY_DEPENDENCY_UPDATES,
                action="dependency_updates.enrollment.sync",
                actor=actor_from_user(user),
                actor_user_id=getattr(user, "id", None),
                target_entity="ExternalDependency",
                target_id=reason,
                target_label="Dependency enrollment",
                metadata={"reason": reason, "discovered": len(discovered), **changed},
            )
            trace.finish(status="ok", summary=f"Synced {len(discovered)} external dependencies")
            return {"ok": True, "discovered": len(discovered), **changed}
        except Exception as exc:
            span.finish(status="error", error=exc)
            trace.finish(status="error", level="error", summary="Dependency enrollment failed", error=exc)
            logger.exception("dependency_enrollment_failed", extra={"reason": reason})
            raise

    async def list_packages(self, *, update_only: bool = False) -> list[dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            query = select(ExternalDependency).where(ExternalDependency.is_enabled.is_(True)).order_by(
                ExternalDependency.update_available.desc(),
                ExternalDependency.ecosystem,
                ExternalDependency.normalized_name,
            )
            if update_only:
                query = query.where(ExternalDependency.update_available.is_(True))
            rows = (await session.scalars(query)).all()
            analyses: dict[uuid.UUID, DependencyUpdateAnalysis] = {}
            analysis_ids = [row.latest_analysis_id for row in rows if row.latest_analysis_id]
            if analysis_ids:
                analysis_rows = (await session.scalars(
                    select(DependencyUpdateAnalysis).where(DependencyUpdateAnalysis.id.in_(analysis_ids))
                )).all()
                analyses = {row.id: row for row in analysis_rows}
        return [serialize_dependency(row, analyses.get(row.latest_analysis_id)) for row in rows]

    async def check_package(self, dependency_id: uuid.UUID, *, user: User | None = None) -> dict[str, Any]:
        dependency = await self._get_dependency(dependency_id)
        latest, metadata = await self._latest_version(dependency)
        update_available = bool(latest and dependency.current_version and _is_newer(latest, dependency.current_version))
        async with AsyncSessionLocal() as session:
            row = await session.get(ExternalDependency, dependency_id)
            if not row:
                raise DependencyUpdateError("Dependency not found.")
            row.latest_version = latest
            row.update_available = update_available
            row.last_checked_at = utc_now()
            row.metadata_ = {**(row.metadata_ or {}), "latest": metadata}
            await session.commit()
            await session.refresh(row)
        await event_bus.publish(
            "dependency_updates.package.checked",
            {
                "dependency_id": str(dependency_id),
                "package_name": dependency.package_name,
                "latest_version": latest,
                "update_available": update_available,
            },
        )
        emit_audit_log(
            category=TELEMETRY_CATEGORY_DEPENDENCY_UPDATES,
            action="dependency_updates.package.check",
            actor=actor_from_user(user),
            actor_user_id=getattr(user, "id", None),
            target_entity="ExternalDependency",
            target_id=str(dependency_id),
            target_label=dependency.package_name,
            metadata={"latest_version": latest, "update_available": update_available},
        )
        return serialize_dependency(row, None)

    async def check_all_packages(self, *, direct_only: bool = False, user: User | None = None, source: str = "manual") -> dict[str, Any]:
        trace = telemetry.start_trace(
            "Dependency update check",
            category=TELEMETRY_CATEGORY_DEPENDENCY_UPDATES,
            actor=actor_from_user(user),
            source=source,
            context={"direct_only": direct_only},
        )
        async with AsyncSessionLocal() as session:
            query = select(ExternalDependency).where(ExternalDependency.is_enabled.is_(True)).order_by(
                ExternalDependency.is_direct.desc(),
                ExternalDependency.ecosystem,
                ExternalDependency.normalized_name,
            )
            if direct_only:
                query = query.where(ExternalDependency.is_direct.is_(True))
            dependencies = (await session.scalars(query)).all()
            ids = [dependency.id for dependency in dependencies]

        checked: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        semaphore = asyncio.Semaphore(8)

        async def check_one(dependency_id: uuid.UUID) -> None:
            async with semaphore:
                try:
                    checked.append(await self.check_package(dependency_id, user=user))
                except Exception as exc:
                    errors.append({"dependency_id": str(dependency_id), "error": str(exc)})

        await asyncio.gather(*(check_one(dependency_id) for dependency_id in ids))
        updates = [dependency for dependency in checked if dependency.get("update_available")]
        summary = {
            "ok": not errors,
            "checked": len(checked),
            "failed": len(errors),
            "updates": len(updates),
            "direct_only": direct_only,
            "errors": errors[:25],
            "packages": checked,
        }
        emit_audit_log(
            category=TELEMETRY_CATEGORY_DEPENDENCY_UPDATES,
            action="dependency_updates.package.check_all",
            actor=actor_from_user(user),
            actor_user_id=getattr(user, "id", None),
            target_entity="ExternalDependency",
            target_id="all",
            target_label="Dependency update check",
            outcome="success" if not errors else "warning",
            level="info" if not errors else "warning",
            metadata={key: value for key, value in summary.items() if key != "packages"},
        )
        await event_bus.publish(
            "dependency_updates.package.check_all.completed",
            {key: value for key, value in summary.items() if key != "packages"},
        )
        trace.finish(status="ok" if not errors else "warning", summary=f"Checked {len(checked)} dependencies; {len(updates)} updates available")
        return summary

    async def _periodic_update_scan(self) -> None:
        await asyncio.sleep(15)
        while True:
            try:
                await self.check_all_packages(user=None, source="periodic")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("dependency_periodic_update_scan_failed")
            await asyncio.sleep(24 * 60 * 60)

    async def analyze_package(
        self,
        dependency_id: uuid.UUID,
        *,
        target_version: str | None = None,
        provider: str | None = None,
        user: User | None = None,
    ) -> dict[str, Any]:
        dependency = await self._get_dependency(dependency_id)
        latest = target_version or dependency.latest_version
        if not latest:
            checked = await self.check_package(dependency_id, user=user)
            latest = checked.get("latest_version")
        if not latest:
            raise DependencyUpdateError("No target version is available for this dependency.")

        trace = telemetry.start_trace(
            "Dependency update analysis",
            category=TELEMETRY_CATEGORY_DEPENDENCY_UPDATES,
            actor=actor_from_user(user),
            source=dependency.package_name,
            context={"dependency_id": str(dependency.id), "target_version": latest},
        )
        changelog_span = trace.start_span("Fetch changelog")
        try:
            changelog = await self._release_notes(dependency, latest)
            changelog_span.finish(output_payload={"source": changelog.get("source")})
            usage_span = trace.start_span("Scan local code usage")
            usage = await owned_release_io(self._scan_usage, dependency.package_name)
            usage_span.finish(output_payload={"references": usage.get("reference_count")})
            analysis = await self._llm_or_heuristic_analysis(
                dependency=dependency,
                target_version=latest,
                changelog=changelog,
                usage=usage,
                provider=provider,
            )
            async with AsyncSessionLocal() as session:
                row = DependencyUpdateAnalysis(
                    dependency_id=dependency.id,
                    target_version=latest,
                    provider=analysis["provider"],
                    model=analysis.get("model"),
                    verdict=analysis["verdict"],
                    summary_markdown=analysis["summary_markdown"],
                    changelog_source=changelog.get("source"),
                    changelog_markdown=changelog.get("body"),
                    usage_summary=usage,
                    breaking_changes=analysis.get("breaking_changes") or [],
                    verification_steps=analysis.get("verification_steps") or [],
                    suggested_diff=analysis.get("suggested_diff") or "",
                    raw_result=analysis.get("raw_result") or {},
                )
                session.add(row)
                await session.flush()
                dependency_row = await session.get(ExternalDependency, dependency.id)
                if dependency_row:
                    dependency_row.latest_analysis_id = row.id
                    dependency_row.risk_status = analysis["verdict"]
                await session.commit()
                await session.refresh(row)
            emit_audit_log(
                category=TELEMETRY_CATEGORY_DEPENDENCY_UPDATES,
                action="dependency_updates.package.analyze",
                actor=actor_from_user(user),
                actor_user_id=getattr(user, "id", None),
                target_entity="ExternalDependency",
                target_id=str(dependency.id),
                target_label=dependency.package_name,
                metadata={"target_version": latest, "verdict": analysis["verdict"], "provider": analysis["provider"]},
            )
            await event_bus.publish(
                "dependency_updates.analysis.created",
                {
                    "dependency_id": str(dependency.id),
                    "analysis_id": str(row.id),
                    "package_name": dependency.package_name,
                    "verdict": row.verdict,
                },
            )
            trace.finish(status="ok", summary=f"{dependency.package_name} analysis: {analysis['verdict']}")
            return serialize_analysis(row)
        except Exception as exc:
            trace.finish(status="error", level="error", summary="Dependency analysis failed", error=exc)
            raise

    async def create_backup(
        self,
        dependency_id: uuid.UUID | None,
        *,
        reason: str,
        user: User | None = None,
        log: "JobLogger | None" = None,
    ) -> dict[str, Any]:
        dependency = await self._get_dependency(dependency_id) if dependency_id else None
        storage = await self.storage_status()
        if not storage["ok"]:
            raise DependencyUpdateError(str(storage["detail"]))
        root = Path(storage["backup_root"])
        package_name = dependency.package_name if dependency else "system"
        ecosystem = dependency.ecosystem if dependency else "system"
        version = dependency.current_version if dependency else None
        backup_id = uuid.uuid4()
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", package_name).strip("-") or "system"
        archive_dir = root / ecosystem / safe_name
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"{datetime.now(tz=UTC).strftime('%Y%m%d%H%M%S')}-{backup_id.hex[:8]}.tar.zst"

        if log:
            await log.info(f"Creating offline backup archive {archive_path}")
        with tempfile.TemporaryDirectory(prefix="iacs-dependency-backup-") as tmp:
            staging = Path(tmp)
            async with AsyncSessionLocal() as identity_session:
                revisions = list((await identity_session.scalars(text("SELECT version_num FROM alembic_version"))).all())
            release_identity = await owned_release_io(capture_source, _workspace_root(), staging / "source",
                schema_revisions=revisions, image_identity=os.getenv("IACS_IMAGE_IDENTITY"))
            (staging / "release.json").write_text(json.dumps(release_identity, indent=2, sort_keys=True))
            manifest_snapshot = await owned_release_io(self._write_manifest_snapshot, staging, source_root=staging / "source")
            config_snapshot = await self._write_config_snapshot(staging, dependency)
            await self._cache_current_artifact(staging, dependency, log=log)
            (staging / "backup.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "release_source_sha256": release_identity["source_sha256"],
                        "backup_id": str(backup_id),
                        "created_at": datetime.now(tz=UTC).isoformat(),
                        "reason": reason,
                        "dependency": serialize_dependency(dependency, None) if dependency else None,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            await _create_zstd_archive(staging, archive_path)

        checksum = await owned_release_io(_sha256_file, archive_path)
        size_bytes = archive_path.stat().st_size
        async with AsyncSessionLocal() as session:
            row = DependencyUpdateBackup(
                id=backup_id,
                dependency_id=dependency.id if dependency else None,
                package_name=package_name,
                ecosystem=ecosystem,
                version=version,
                reason=reason,
                archive_path=str(archive_path),
                storage_root=str(root),
                checksum_sha256=checksum,
                size_bytes=size_bytes,
                manifest_snapshot=manifest_snapshot,
                config_snapshot=config_snapshot,
                metadata_={"archive_format": "tar.zst", "release_identity": release_identity},
                created_by_user_id=getattr(user, "id", None),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
        emit_audit_log(
            category=TELEMETRY_CATEGORY_DEPENDENCY_UPDATES,
            action="dependency_updates.backup.create",
            actor=actor_from_user(user),
            actor_user_id=getattr(user, "id", None),
            target_entity="DependencyUpdateBackup",
            target_id=str(backup_id),
            target_label=package_name,
            metadata={"reason": reason, "size_bytes": size_bytes, "storage_root": str(root)},
        )
        await event_bus.publish(
            "dependency_updates.backup.created",
            {"backup_id": str(backup_id), "package_name": package_name, "size_bytes": size_bytes},
        )
        return serialize_backup(row)

    async def list_backups(self, dependency_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            query = select(DependencyUpdateBackup).order_by(DependencyUpdateBackup.created_at.desc())
            if dependency_id:
                query = query.where(DependencyUpdateBackup.dependency_id == dependency_id)
            rows = (await session.scalars(query.limit(100))).all()
        return [serialize_backup(row) for row in rows]

    async def validate_backup_archive(self, backup_id: uuid.UUID) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            backup = await session.get(DependencyUpdateBackup, backup_id)
            if not backup:
                raise DependencyUpdateError("Backup not found.")
            session.expunge(backup)
        return await self._validate_backup_archive(backup)

    async def start_apply_job(
        self,
        dependency_id: uuid.UUID,
        *,
        target_version: str | None,
        confirmed: bool,
        user: User,
    ) -> dict[str, Any]:
        if not confirmed:
            raise DependencyUpdateError("Update confirmation is required.")
        dependency = await self._get_dependency(dependency_id)
        target = target_version or dependency.latest_version
        if not target:
            raise DependencyUpdateError("No target version is available.")
        if dependency.current_version and not _is_newer(target, dependency.current_version):
            raise DependencyUpdateError("No newer version is available for this dependency.")
        if dependency.ecosystem in {"python", "npm"} and not dependency.is_direct:
            raise DependencyUpdateError("Transitive dependencies must be updated through their direct manifest dependency.")
        analysis = await self._latest_analysis_for_target(dependency.id, target)
        if not analysis:
            raise DependencyUpdateError("Run dependency analysis for this target version before applying the update.")
        if str(analysis.verdict or "").lower() == "breaking":
            raise DependencyUpdateError(
                "This update is marked Breaking. Resolve the proposed migration first, then re-run analysis before applying."
            )
        return await self._create_and_start_job(
            kind="apply",
            dependency=dependency,
            target_version=target,
            backup_id=None,
            user=user,
        )

    async def start_restore_job(self, backup_id: uuid.UUID, *, confirmed: bool, user: User) -> dict[str, Any]:
        if not confirmed:
            raise DependencyUpdateError("Restore confirmation is required.")
        async with AsyncSessionLocal() as session:
            backup = await session.get(DependencyUpdateBackup, backup_id)
            if not backup:
                raise DependencyUpdateError("Backup not found.")
            dependency = await session.get(ExternalDependency, backup.dependency_id) if backup.dependency_id else None
        return await self._create_and_start_job(
            kind="restore",
            dependency=dependency,
            target_version=backup.version,
            backup_id=backup_id,
            user=user,
        )

    async def job_status(self, job_id: uuid.UUID) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            job = await session.get(DependencyUpdateJob, job_id)
            if not job:
                raise DependencyUpdateError("Update job not found.")
        return serialize_job(job)

    async def subscribe_job(self, job_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for event in self._job_streams.get(job_id, []):
            queue.put_nowait(event)
        self._job_waiters.setdefault(job_id, set()).add(queue)
        return queue

    def unsubscribe_job(self, job_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        waiters = self._job_waiters.get(job_id)
        if waiters:
            waiters.discard(queue)

    async def storage_status(self) -> dict[str, Any]:
        runtime = await get_runtime_config()
        root = _backup_root()
        mount_options_configured = bool(runtime.dependency_update_backup_mount_options.strip())
        status = {
            "mode": runtime.dependency_update_backup_storage_mode,
            "mount_source": runtime.dependency_update_backup_mount_source,
            "mount_options": "",
            "mount_options_configured": mount_options_configured,
            "mount_options_redacted": mount_options_configured,
            "config_status": runtime.dependency_update_backup_config_status,
            "backup_root": str(root),
            "exists": root.exists(),
            "writable": False,
            "free_bytes": 0,
            "min_free_bytes": runtime.dependency_update_backup_min_free_bytes,
            "retention_days": runtime.dependency_update_backup_retention_days,
            "ok": False,
            "detail": "",
        }
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".iacs-write-probe"
            probe.write_text(datetime.now(tz=UTC).isoformat())
            probe.unlink(missing_ok=True)
            usage = shutil.disk_usage(root)
            status.update(
                {
                    "writable": True,
                    "free_bytes": usage.free,
                    "ok": usage.free >= runtime.dependency_update_backup_min_free_bytes,
                    "detail": "Backup storage is writable."
                    if usage.free >= runtime.dependency_update_backup_min_free_bytes
                    else "Backup storage is below the configured free-space threshold.",
                }
            )
        except Exception as exc:
            status["detail"] = f"Backup storage is not writable: {exc}"
        return status

    async def validate_storage(self, *, mark_active: bool = False) -> dict[str, Any]:
        status = await self.storage_status()
        next_status = "active" if status["ok"] else "error"
        if mark_active:
            await update_settings({"dependency_update_backup_config_status": next_status})
        emit_audit_log(
            category=TELEMETRY_CATEGORY_DEPENDENCY_UPDATES,
            action="dependency_updates.storage.validate",
            actor="System",
            target_entity="DependencyUpdateStorage",
            target_id=status["mode"],
            target_label="Update backup storage",
            outcome="success" if status["ok"] else "failed",
            level="info" if status["ok"] else "error",
            metadata=status,
        )
        await event_bus.publish("dependency_updates.storage.validated", status)
        return status

    async def save_storage_config(self, payload: dict[str, Any], *, user: User) -> dict[str, Any]:
        runtime = await get_runtime_config()
        mode = str(payload.get("mode") or "local").strip().lower()
        if mode not in {"local", "nfs", "samba"}:
            raise DependencyUpdateError("Storage mode must be local, nfs, or samba.")
        source = str(payload.get("mount_source") or "").strip()
        raw_options = payload.get("mount_options", _MOUNT_OPTIONS_UNSET)
        options_changed = raw_options is not _MOUNT_OPTIONS_UNSET and raw_options is not None
        options = (
            str(raw_options or "").strip()
            if options_changed
            else runtime.dependency_update_backup_mount_options
        )
        retention = str(payload.get("retention_days") or "").strip()
        min_free = int(payload.get("min_free_bytes") or 1073741824)
        if mode == "local":
            source = ""
            options = ""
            options_changed = True
        if mode in {"nfs", "samba"} and not source:
            raise DependencyUpdateError("NAS storage requires a mount source.")
        await owned_release_io(self._write_generated_compose_override, mode, source, options)
        updates = {
            "dependency_update_backup_storage_mode": mode,
            "dependency_update_backup_mount_source": source,
            "dependency_update_backup_retention_days": retention,
            "dependency_update_backup_min_free_bytes": min_free,
            "dependency_update_backup_config_status": "pending_reboot",
        }
        if options_changed:
            updates["dependency_update_backup_mount_options"] = options
        await update_settings(
            updates, user=user, source="dependency_update_storage"
        )
        result = await self.storage_status()
        result["config_status"] = "pending_reboot"
        result["mount_options_configured"] = bool(options)
        result["mount_options_redacted"] = bool(options)
        emit_audit_log(
            category=TELEMETRY_CATEGORY_DEPENDENCY_UPDATES,
            action="dependency_updates.storage.configure",
            actor=actor_from_user(user),
            actor_user_id=user.id,
            target_entity="DependencyUpdateStorage",
            target_id=mode,
            target_label="Update backup storage",
            metadata={
                "mode": mode,
                "mount_source": source,
                "mount_options_configured": bool(options),
                "mount_options_changed": options_changed,
                "mount_options_cleared": options_changed and not bool(options),
                "pending_reboot": True,
            },
        )
        await event_bus.publish("dependency_updates.storage.configured", result)
        return result

    async def _create_and_start_job(
        self,
        *,
        kind: str,
        dependency: ExternalDependency | None,
        target_version: str | None,
        backup_id: uuid.UUID | None,
        user: User,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4()
        log_path = JOB_LOG_DIR / f"{job_id}.log"
        JOB_LOG_DIR.mkdir(parents=True, exist_ok=True)
        executor = await self._acquire_executor()
        if executor is None:
            raise DependencyUpdateError("Another dependency update or restore is in progress.")
        self._executors[job_id] = executor
        try:
            async with AsyncSessionLocal(bind=executor.connection) as session:
                await session.execute(text(_INTAKE_LOCK))
                active_job = await session.scalar(select(DependencyUpdateJob.id).where(
                    DependencyUpdateJob.status.in_(["queued", "running"])).limit(1))
                if active_job:
                    raise DependencyUpdateError("Another dependency update or restore is in progress.")
                job = DependencyUpdateJob(
                    id=job_id,
                    dependency_id=dependency.id if dependency else None,
                    kind=kind,
                    status="queued",
                    phase="queued",
                    actor=actor_from_user(user),
                    actor_user_id=user.id,
                    target_version=target_version,
                    backup_id=backup_id,
                    stdout_log_path=str(log_path),
                )
                session.add(job)
                await session.commit()
                await session.refresh(job)
            self._append_job_event(str(job_id), {"type": "queued", "phase": "queued", "message": f"{kind} job queued"})
            task = asyncio.create_task(self._run_job(job_id, kind=kind, user=user), name=f"dependency-update-job:{job_id}")
            executor.task = task
            self._job_tasks[str(job_id)] = task
            task.add_done_callback(lambda done: self._job_tasks.pop(str(job_id), None))
            return serialize_job(job)
        finally:
            if str(job_id) not in self._job_tasks:
                await self._release_executor(executor)
                self._executors.pop(job_id, None)

    async def _run_job(self, job_id: uuid.UUID, *, kind: str, user: User) -> None:
        trace = telemetry.start_trace(
            f"Dependency {kind} job",
            category=TELEMETRY_CATEGORY_DEPENDENCY_UPDATES,
            actor=actor_from_user(user),
            context={"job_id": str(job_id), "kind": kind},
        )
        logger_ = JobLogger(self, str(job_id))
        dependency: ExternalDependency | None = None
        target_version: str | None = None
        claimed = False
        executor = self._executors.get(job_id)
        if executor is not None and executor.task is not None and executor.task is not asyncio.current_task():
            trace.finish(status="ok", summary="Dependency job already belongs to another task")
            return
        if executor is None:
            executor = await self._acquire_executor()
            if executor is None:
                trace.finish(status="ok", summary="Another executor owns dependency work")
                return
            self._executors[job_id] = executor
        executor.task = asyncio.current_task()
        try:
            await self._assert_executor(job_id, require_running=False)
            async with AsyncSessionLocal(bind=executor.connection) as session:
                await session.execute(text(_INTAKE_LOCK))
                job = await session.get(DependencyUpdateJob, job_id, with_for_update=True)
                if not job or job.status != "queued":
                    trace.finish(status="ok", summary="Dependency job is no longer queued; no execution")
                    return
                if job.kind != kind:
                    raise DependencyUpdateError("Queued dependency job kind does not match its executor.")
                job.status = "running"
                job.phase = "starting"
                job.started_at = utc_now()
                job.trace_id = trace.trace_id
                dependency = await session.get(ExternalDependency, job.dependency_id) if job.dependency_id else None
                backup = await session.get(DependencyUpdateBackup, job.backup_id) if job.backup_id else None
                target_version = job.target_version
                await session.commit()
                claimed = True

            await self._assert_executor(job_id)
            await logger_.info(f"Starting {kind} job {job_id}")
            if kind == "apply":
                await self._run_apply_job(job_id, dependency, user, logger_, target_version=job.target_version)
            elif kind == "restore":
                if not backup:
                    raise DependencyUpdateError("Restore job has no backup.")
                await self._run_restore_job(job_id, backup, user, logger_)
            else:
                raise DependencyUpdateError(f"Unsupported job kind: {kind}")

            await self._assert_executor(job_id)
            async with AsyncSessionLocal() as session:
                job = await session.get(DependencyUpdateJob, job_id, with_for_update=True)
                if not job or job.status != "running":
                    raise DependencyUpdateError("Dependency job ownership changed before completion.")
                job.status = "completed"
                job.phase = "completed"
                job.ended_at = utc_now()
                job.result = {**(job.result or {}), "ok": True, "source_status": "prepared",
                              "deployment": "not_performed", "runtime_health": "not_checked"}
                await session.commit()
            self._append_job_event(str(job_id), {"type": "completed", "phase": "completed", "message": "Job completed"})
            trace.finish(status="ok", summary=f"Dependency {kind} job completed")
        except asyncio.CancelledError as cancellation:
            checkpoint = asyncio.create_task(self._record_interrupted_job(job_id))
            try:
                await asyncio.shield(checkpoint)
            except Exception:
                logger.exception("dependency_interruption_checkpoint_failed")
            finally:
                while not checkpoint.done():
                    try:
                        await asyncio.shield(checkpoint)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        logger.exception("dependency_interruption_checkpoint_failed")
                        break
                trace.finish(status="error", level="warning", summary="Dependency job interrupted; review required")
            raise cancellation
        except Exception as exc:
            await logger_.error(str(exc))
            diagnosis = _diagnose_dependency_failure(kind, dependency, target_version, exc)
            checkpoint_persisted = False
            try:
                async with AsyncSessionLocal() as session:
                    job = await session.get(DependencyUpdateJob, job_id)
                    if job and (job.status == "running" or (not claimed and job.status == "queued")):
                        result = dict(job.result or {})
                        pending_receipt = self._unpersisted_receipts.get(job_id)
                        if pending_receipt is not None:
                            result["receipt_persistence"] = {
                                "status": "journal_only", "kind": pending_receipt[0],
                                "transaction_id": pending_receipt[1].get("transaction_id"),
                                "review_required": True,
                            }
                        diagnosis = _diagnose_dependency_failure(kind, dependency, target_version, exc, job_result=result)
                        result.update({"ok": False, "diagnosis": diagnosis})
                        job.status = "failed"
                        job.phase = "failed"
                        job.ended_at = utc_now()
                        job.error = str(exc)
                        job.result = result
                        await session.commit()
                        checkpoint_persisted = True
            except Exception:
                # The filesystem transaction/candidate is the last-resort receipt
                # when even failure accounting cannot reach the database.
                await logger_.error("Job outcome could not be persisted. Inspect retained source transaction and candidate journals; no automatic recovery was attempted.")
            if not checkpoint_persisted:
                diagnosis = {**diagnosis, "database_outcome": "unverified", "review_required": True}
                pending_receipt = self._unpersisted_receipts.get(job_id)
                if pending_receipt is not None:
                    await logger_.error(f"Journal-only {pending_receipt[0]} receipt: transaction {pending_receipt[1].get('transaction_id')}; database checkpoint unavailable.")
                    diagnosis["receipt_persistence"] = {"status": "journal_only", "kind": pending_receipt[0],
                        "transaction_id": pending_receipt[1].get("transaction_id")}
            self._append_job_event(str(job_id), {"type": "failed", "phase": "failed" if checkpoint_persisted else "review_required",
                "message": str(exc), "diagnosis": diagnosis})
            trace.finish(status="error", level="error", summary=f"Dependency {kind} job failed", error=exc)
        finally:
            try:
                await self._release_executor(executor)
            finally:
                self._executors.pop(job_id, None)
                self._unpersisted_receipts.pop(job_id, None)

    async def _run_apply_job(
        self,
        job_id: uuid.UUID,
        dependency: ExternalDependency | None,
        user: User,
        log: "JobLogger",
        *,
        target_version: str | None,
    ) -> None:
        if not dependency:
            raise DependencyUpdateError("Apply job has no dependency.")
        await self._set_job_phase(job_id, "backup")
        backup = await self.create_backup(
            dependency.id,
            reason=f"before_{dependency.package_name}_update",
            user=user,
            log=log,
        )
        async with AsyncSessionLocal() as session:
            job = await session.get(DependencyUpdateJob, job_id)
            if job:
                job.backup_id = uuid.UUID(backup["id"])
                await session.commit()

        promotion = None
        try:
            await self._set_job_phase(job_id, "apply")
            await log.info(f"Applying {dependency.package_name} {dependency.current_version or 'unknown'} -> {target_version or dependency.latest_version or 'target'}")
            promotion = await self._run_update_commands(dependency, target_version, log,
                release_context=(backup.get("metadata") or {}).get("release_identity", {}), job_id=job_id)
            self._unpersisted_receipts[job_id] = ("promotion", promotion)
            await log.info(f"Source promotion receipt: transaction {promotion.get('transaction_id')}. Deployment was not performed.")
            async with AsyncSessionLocal() as session:
                saved_backup = await session.get(DependencyUpdateBackup, uuid.UUID(backup["id"]), with_for_update=True)
                saved_job = await session.get(DependencyUpdateJob, job_id, with_for_update=True)
                saved_backup.metadata_ = {**(saved_backup.metadata_ or {}), "promotion": promotion}
                saved_job.result = {**(saved_job.result or {}), "promotion": promotion}
                await session.commit()
            self._unpersisted_receipts.pop(job_id, None)
            await self.sync_enrollment(reason="apply_job", user=user)
            await self._set_job_phase(job_id, "verify")
            await log.info("Checking Python syntax only. Source is prepared; image build, runtime health and deployment remain separate release checks.")
            await self._run_command(["python", "-m", "compileall", "-q", "backend/app"], cwd=_workspace_root(), log=log, timeout=180)
        except Exception:
            # The file-set owner restores its own preimages on ordinary write
            # failure. A later job/accounting error is not permission to overwrite
            # intervening source changes or reset a durable executor generation.
            await log.error("Update did not complete. Retain its candidate/transaction evidence for review; no broad automatic restore was attempted.")
            async with AsyncSessionLocal() as session:
                if promotion is not None:
                    saved_backup = await session.get(DependencyUpdateBackup, uuid.UUID(backup["id"]), with_for_update=True)
                    if saved_backup:
                        saved_backup.metadata_ = {**(saved_backup.metadata_ or {}), "promotion": promotion}
                saved_job = await session.get(DependencyUpdateJob, job_id, with_for_update=True)
                if saved_job:
                    saved_job.result = {**(saved_job.result or {}),
                        **({"promotion": promotion} if promotion is not None else {}), "rollback": {
                        "attempted": False, "review_required": True, "backup_id": backup["id"]}}
                    await session.commit()
            self._unpersisted_receipts.pop(job_id, None)
            raise
        emit_audit_log(
            category=TELEMETRY_CATEGORY_DEPENDENCY_UPDATES,
            action="dependency_updates.package.apply",
            actor=actor_from_user(user),
            actor_user_id=user.id,
            target_entity="ExternalDependency",
            target_id=str(dependency.id),
            target_label=dependency.package_name,
            metadata={"backup_id": backup["id"]},
        )

    async def _run_restore_job(
        self,
        job_id: uuid.UUID,
        backup: DependencyUpdateBackup,
        user: User,
        log: "JobLogger",
    ) -> None:
        await self._set_job_phase(job_id, "validate_backup")
        validation = await self._validate_backup_archive(backup)
        await log.info(
            "Validated offline backup archive with "
            f"{validation['manifest_count']} manifest files and "
            f"{validation['artifact_count']} cached package artifacts."
        )
        archive_path = Path(backup.archive_path)
        await log.info(f"Restoring backup {backup.id} from {archive_path}")
        await self._set_job_phase(job_id, "restore_files")
        restoration = None
        try:
            restoration = await self._restore_backup_manifests(archive_path, job_id=job_id,
                promotion=(backup.metadata_ or {}).get("promotion"))
            self._unpersisted_receipts[job_id] = ("restoration", restoration)
            await log.info(f"Source restoration receipt: transaction {restoration.get('transaction_id')}. Runtime and database were not restored.")
            await self._persist_restoration(job_id, backup.id, user.id, restoration)
            self._unpersisted_receipts.pop(job_id, None)
            await self.sync_enrollment(reason="restore_job", user=user)
            await self._set_job_phase(job_id, "verify")
            await self._run_command(["python", "-m", "compileall", "-q", "backend/app"], cwd=_workspace_root(), log=log, timeout=180)
        except Exception:
            if restoration is not None:
                # Receipt retry only: never repeat the filesystem restoration.
                await self._persist_restoration(job_id, backup.id, user.id, restoration)
                self._unpersisted_receipts.pop(job_id, None)
            raise
        emit_audit_log(
            category=TELEMETRY_CATEGORY_DEPENDENCY_UPDATES,
            action="dependency_updates.backup.restore",
            actor=actor_from_user(user),
            actor_user_id=user.id,
            target_entity="DependencyUpdateBackup",
            target_id=str(backup.id),
            target_label=backup.package_name,
            metadata={"archive_path": backup.archive_path},
        )

    async def _persist_restoration(self, job_id: uuid.UUID, backup_id: uuid.UUID,
                                   user_id: uuid.UUID, restoration: dict) -> None:
        async with AsyncSessionLocal() as session:
            row = await session.get(DependencyUpdateBackup, backup_id, with_for_update=True)
            job = await session.get(DependencyUpdateJob, job_id, with_for_update=True)
            if row is None or job is None:
                raise DependencyUpdateError("Restoration receipt metadata is missing; inspect the retained filesystem journal.")
            row.restored_at = utc_now()
            row.restored_by_user_id = user_id
            row.metadata_ = {**(row.metadata_ or {}), "restoration": restoration}
            job.result = {**(job.result or {}), "restoration": restoration}
            await session.commit()

    async def _restore_backup_manifests(self, archive_path: Path, *, job_id: uuid.UUID, promotion: dict | None = None) -> dict:
        if not promotion or not isinstance(promotion.get("files"), dict) or not promotion["files"]:
            raise DependencyUpdateError("Backup has no owned promoted transaction; a reviewed recovery procedure is required.")
        with tempfile.TemporaryDirectory(prefix="iacs-dependency-restore-") as tmp:
            staging = Path(tmp)
            await _extract_archive(archive_path, staging)
            identity_path = staging / "release.json"
            if not identity_path.exists():
                raise DependencyUpdateError("Legacy backup lacks release identity; manual recovery review is required.")
            identity = json.loads(identity_path.read_text())
            await owned_release_io(validate_source, staging / "source", identity)
            async with AsyncSessionLocal() as session:
                revisions = list((await session.scalars(text("SELECT version_num FROM alembic_version"))).all())
            snapshot_root = staging / "manifests"
            paths = sorted(promotion["files"])
            before = promotion.get("before")
            if not isinstance(before, dict) or set(before) != set(paths):
                raise DependencyUpdateError("Backup lacks the transaction's exact preimage ownership.")
            absent_paths = set()
            for name in paths:
                expected_before = identity["files"].get(name)
                candidate = safe_file(snapshot_root, name)
                if before[name] != expected_before:
                    raise DependencyUpdateError("Transaction preimage differs from the backup's release identity.")
                if expected_before is None:
                    if candidate.exists():
                        raise DependencyUpdateError("An absent backup preimage cannot contain replacement bytes.")
                    absent_paths.add(name)
                elif not candidate.is_file() or sha256(candidate) != expected_before:
                    raise DependencyUpdateError("Restored bytes do not match the backup's release identity.")
            await owned_release_io(assert_manifest_restore_compatible, _workspace_root(), identity,
                schema_revisions=revisions, image_identity=os.getenv("IACS_IMAGE_IDENTITY"), restored_paths=paths)
            # Only the recorded transaction afterimages may be changed. Its
            # explicit absent preimage also authorizes removing a file it added.
            expected_source = dict(identity["files"])
            expected_source.update(promotion["files"])
            await self._assert_executor(job_id)
            return await publish_manifest_set_async(snapshot_root, _workspace_root(), paths,
                expected=promotion["files"], absent_paths=absent_paths, expected_source=expected_source)

    async def _validate_backup_archive(self, backup: DependencyUpdateBackup) -> dict[str, Any]:
        archive_path = Path(backup.archive_path)
        if not archive_path.exists():
            raise DependencyUpdateError("Backup archive is missing from configured storage.")
        checksum = await owned_release_io(_sha256_file, archive_path)
        if checksum != backup.checksum_sha256:
            raise DependencyUpdateError("Backup checksum validation failed.")
        with tempfile.TemporaryDirectory(prefix="iacs-dependency-validate-") as tmp:
            staging = Path(tmp)
            await _extract_archive(archive_path, staging)
            try:
                return await owned_release_io(self._validate_backup_snapshot, staging, backup, checksum)
            except ReleaseArtifactError as exc:
                raise DependencyUpdateError(str(exc)) from exc

    def _validate_backup_snapshot(
        self,
        staging: Path,
        backup: DependencyUpdateBackup,
        checksum: str,
    ) -> dict[str, Any]:
        backup_file = staging / "backup.json"
        if not backup_file.exists():
            raise DependencyUpdateError("Backup archive did not include backup.json.")
        try:
            backup_payload = json.loads(backup_file.read_text())
        except json.JSONDecodeError as exc:
            raise DependencyUpdateError("Backup archive included invalid backup.json.") from exc
        if not isinstance(backup_payload, dict):
            raise DependencyUpdateError("Backup archive included invalid backup.json.")
        payload_backup_id = backup_payload.get("backup_id")
        if payload_backup_id and str(payload_backup_id) != str(backup.id):
            raise DependencyUpdateError("Backup archive metadata does not match the requested backup.")

        settings_file = staging / "settings.json"
        if not settings_file.exists():
            raise DependencyUpdateError("Backup archive did not include the settings snapshot.")
        try:
            settings_payload = json.loads(settings_file.read_text())
        except json.JSONDecodeError as exc:
            raise DependencyUpdateError("Backup archive included invalid settings snapshot JSON.") from exc
        if not isinstance(settings_payload, list):
            raise DependencyUpdateError("Backup archive settings snapshot has an invalid shape.")

        snapshot_root = staging / "manifests"
        if not snapshot_root.exists():
            raise DependencyUpdateError("Backup archive did not include manifest snapshots.")
        manifest_files = [path for path in snapshot_root.rglob("*") if path.is_file()]
        if not manifest_files:
            raise DependencyUpdateError("Backup archive manifest snapshot is empty.")
        expected_files = (backup.manifest_snapshot or {}).get("files")
        if isinstance(expected_files, list) and expected_files:
            for item in expected_files:
                if not isinstance(item, dict):
                    continue
                relative = str(item.get("path") or "")
                expected_sha = str(item.get("sha256") or "")
                if not relative:
                    continue
                candidate = snapshot_root / relative
                if not candidate.exists():
                    raise DependencyUpdateError(f"Backup archive is missing manifest snapshot: {relative}")
                if expected_sha and _sha256_file(candidate) != expected_sha:
                    raise DependencyUpdateError(f"Backup manifest snapshot checksum failed: {relative}")

        artifact_root = staging / "artifacts"
        artifact_files = [path for path in artifact_root.rglob("*") if path.is_file()] if artifact_root.exists() else []
        package_artifacts = [
            path
            for path in artifact_files
            if path.name not in {"artifact-cache.log", "artifact-cache-error.txt", "artifact-unavailable.txt"}
        ]
        if backup.version and backup.ecosystem in {"python", "npm"}:
            if not artifact_root.exists():
                raise DependencyUpdateError("Backup archive did not include offline package artifacts.")
            if not package_artifacts:
                raise DependencyUpdateError("Backup archive did not include a usable offline package artifact.")

        release_path = staging / "release.json"
        release_identity = json.loads(release_path.read_text()) if release_path.exists() else None
        if release_identity is not None:
            validate_source(staging / "source", release_identity)
        return {
            "release_identity": release_identity,
            "compatibility_review_required": release_identity is None or not release_identity.get("image_identity"),
            "backup_id": str(backup.id),
            "archive_path": str(backup.archive_path),
            "checksum_sha256": checksum,
            "manifest_count": len(manifest_files),
            "settings_count": len(settings_payload),
            "artifact_count": len(package_artifacts),
            "artifact_metadata_count": len(artifact_files) - len(package_artifacts),
        }

    async def _run_update_commands(self, dependency: ExternalDependency, target_version: str | None, log: "JobLogger", *,
                                   job_id: uuid.UUID, release_context: dict | None = None) -> dict:
        root = _workspace_root()
        target = target_version or dependency.latest_version
        baseline_source = {name: sha256(root / name) for name in source_files(root)}
        if not target:
            raise DependencyUpdateError("No latest version is recorded for this dependency.")
        if dependency.ecosystem == "python" and dependency.is_direct and dependency.manifest_path.endswith("pyproject.toml"):
            with tempfile.TemporaryDirectory(prefix="iacs-python-update-") as tmp:
                staged_root = Path(tmp)
                paths = ["backend/pyproject.toml", "backend/uv.lock"]
                expected = {name: sha256(root / name) if (root / name).exists() else None for name in paths}
                if any(value is None for value in expected.values()):
                    raise DependencyUpdateError("Python candidate requires the existing manifest and lockfile.")
                for name in paths:
                    await owned_release_io(_copy_manifest_to_staging_root, root, staged_root, name)
                await log.info("Resolving the requested Python dependency in an isolated locked candidate.")
                await owned_release_io(_update_python_requirement, dependency, target, staged_root)
                await self._run_command(["uv", "lock", "--upgrade-package", f"{dependency.package_name}=={target}"],
                                        cwd=staged_root / "backend", log=log, timeout=600)
                await self._run_command(["uv", "lock", "--check"], cwd=staged_root / "backend", log=log, timeout=180)
                await self._run_command(["uv", "sync", "--locked", "--all-extras", "--no-install-project"],
                                        cwd=staged_root / "backend", log=log, timeout=900)
                await self._assert_executor(job_id)
                promotion = await prepare_and_publish(staged_root, root, paths, expected_source=baseline_source,
                    release_context=release_context or {}, before_publish=lambda: self._assert_executor(job_id), checks=["uv lock --check", "uv sync --locked --all-extras --no-install-project"])
            return promotion
        if dependency.ecosystem == "npm" and dependency.is_direct:
            frontend = root / "frontend"
            if not frontend.exists():
                raise DependencyUpdateError("Frontend workspace is not mounted in the updater environment.")
            return await self._apply_npm_update_transactionally(frontend, dependency, target, log,
                release_context=release_context, expected_source=baseline_source, job_id=job_id)
        if dependency.ecosystem == "docker_image":
            with tempfile.TemporaryDirectory(prefix="iacs-docker-update-") as tmp:
                staged_root = Path(tmp)
                await owned_release_io(_copy_manifest_to_staging_root, root, staged_root, str(dependency.manifest_path or ""))
                if (root / "docker-compose.yml").exists() and dependency.manifest_path != "docker-compose.yml":
                    await owned_release_io(_copy_manifest_to_staging_root, root, staged_root, "docker-compose.yml")
                await log.info("Updating Docker image tag in an isolated manifest workspace.")
                await owned_release_io(_update_docker_image_tag, dependency, target, staged_root)
                if dependency.manifest_path == "docker-compose.yml" and shutil.which("docker"):
                    await self._run_command(["docker", "compose", "-f", "docker-compose.yml", "config"], cwd=staged_root, log=log, timeout=180)
                elif dependency.manifest_path == "docker-compose.yml":
                    await log.warning("Docker CLI is not available in this container; skipping compose syntax verification.")
                await self._assert_executor(job_id)
                promotion = await prepare_and_publish(staged_root, root, [str(dependency.manifest_path)],
                    expected_source=baseline_source, release_context=release_context or {},
                    before_publish=lambda: self._assert_executor(job_id), checks=["compose syntax" if dependency.manifest_path == "docker-compose.yml" and shutil.which("docker")
                            else "image reference edit only; compose/build not checked"])
            return promotion
        raise DependencyUpdateError("Only direct Python/npm dependencies can be applied by this build.")

    async def _run_command(
        self,
        command: list[str],
        *,
        cwd: Path,
        log: "JobLogger",
        timeout: int,
    ) -> str:
        await log.info("$ " + " ".join(command))
        if not shutil.which(command[0]):
            raise DependencyUpdateError(f"Required command is not available in this container: {command[0]}")
        try:
            completed = await run_dependency_process(command, cwd, timeout)
        except DependencyProcessTimeout as exc:
            raise DependencyUpdateError("Dependency command timed out; its owned process tree was stopped.") from exc
        stdout = completed.stdout
        output = stdout.decode(errors="replace") if stdout else ""
        for line in output.splitlines():
            await log.stdout(line)
        if completed.returncode != 0:
            raise DependencyCommandError(command, completed.returncode or 1, output)
        return output

    async def _apply_npm_update_transactionally(
        self,
        frontend: Path,
        dependency: ExternalDependency,
        target: str,
        log: "JobLogger", *, job_id: uuid.UUID, release_context: dict | None = None, expected_source: dict | None = None,
    ) -> dict:
        root = frontend.parent
        baseline_source = expected_source or {name: sha256(root / name) for name in source_files(root)}
        with tempfile.TemporaryDirectory(prefix="iacs-frontend-build-") as tmp:
            staged_frontend = Path(tmp) / "frontend"
            await owned_release_io(_copy_frontend_build_context, frontend, staged_frontend)
            staged_before = {name: sha256(Path(tmp) / name) for name in source_files(Path(tmp))}
            if any(baseline_source.get(name) != value for name, value in staged_before.items()):
                raise ReleaseArtifactError("Frontend source changed while preparing its build context.")
            await log.info("Prepared isolated frontend update workspace. Real manifests will not be changed until verification passes.")
            install_command = ["npm", "install", f"{dependency.package_name}@{target}", "--package-lock-only"]
            try:
                await self._run_command(install_command, cwd=staged_frontend, log=log, timeout=600)
            except DependencyCommandError as exc:
                recovered = await self._attempt_npm_recovery(staged_frontend, dependency, target, exc, log)
                if not recovered:
                    raise
            await self._verify_npm_peer_tree(staged_frontend, dependency, target, log)
            await self._normalize_npm_lockfile_for_ci(staged_frontend, log)
            await log.info("Installing frontend dependencies in the isolated verification workspace.")
            await self._run_command(["npm", "ci", "--include=optional", "--no-audit"], cwd=staged_frontend, log=log, timeout=900)
            await log.info("Running frontend build verification in the isolated workspace.")
            try:
                await self._run_command(["npm", "run", "build"], cwd=staged_frontend, log=log, timeout=600)
            except DependencyCommandError as exc:
                recovered = await self._attempt_frontend_build_recovery(staged_frontend, dependency, target, exc, log)
                if not recovered:
                    raise
                await self._run_command(["npm", "run", "build"], cwd=staged_frontend, log=log, timeout=600)
            paths = ["frontend/" + name for name in ["package.json", "package-lock.json", "tsconfig.json", "src/vite-env.d.ts"]
                     if (staged_frontend / name).exists()]
            staged_after = {name: sha256(Path(tmp) / name) for name in source_files(Path(tmp))}
            if ({name: value for name, value in staged_before.items() if name not in paths}
                    != {name: value for name, value in staged_after.items() if name not in paths}):
                raise ReleaseArtifactError("Frontend build changed source outside its approved build inputs; review the candidate.")
            await self._assert_executor(job_id)
            promotion = await prepare_and_publish(Path(tmp), root, paths, expected_source=baseline_source,
                release_context=release_context or {}, before_publish=lambda: self._assert_executor(job_id), checks=["npm ci --include=optional --no-audit", "npm run build"])
            return promotion

    async def _attempt_frontend_build_recovery(
        self,
        staged_frontend: Path,
        dependency: ExternalDependency,
        target: str,
        failure: DependencyCommandError,
        log: "JobLogger",
    ) -> bool:
        if _repair_tsconfig_for_typescript_6(staged_frontend, failure.output):
            await log.warning("TypeScript reported a deprecated moduleResolution setting. IACS migrated tsconfig.json to the Vite-compatible Bundler resolver in the isolated workspace.")
            return True
        if _repair_vite_type_declarations(staged_frontend, failure.output):
            await log.warning("TypeScript reported missing Vite side-effect import declarations. IACS added the standard vite-env.d.ts declaration in the isolated workspace.")
            return True
        plan = await self._llm_recovery_plan(
            ecosystem="npm",
            dependency=dependency,
            target_version=target,
            failed_command=failure.command,
            command_output=failure.output,
        )
        if plan:
            await log.info(f"Build recovery advisor: {plan.get('summary')}")
        return False

    async def _verify_npm_peer_tree(
        self,
        staged_frontend: Path,
        dependency: ExternalDependency,
        target: str,
        log: "JobLogger",
    ) -> None:
        await log.info("Checking npm peer dependency graph before installing verification dependencies.")
        try:
            await self._run_command(["npm", "install", "--package-lock-only", "--dry-run"], cwd=staged_frontend, log=log, timeout=300)
        except DependencyCommandError as exc:
            await log.warning("The npm dependency graph still has peer conflicts. IACS will attempt one more isolated repair pass.")
            recovered = await self._attempt_npm_recovery(staged_frontend, dependency, target, exc, log)
            if not recovered:
                raise
            await self._run_command(["npm", "install", "--package-lock-only", "--dry-run"], cwd=staged_frontend, log=log, timeout=300)

    async def _attempt_npm_recovery(
        self,
        staged_frontend: Path,
        dependency: ExternalDependency,
        target: str,
        failure: DependencyCommandError,
        log: "JobLogger",
    ) -> bool:
        await log.warning("Initial npm update command failed. Diagnosing recoverable npm failure patterns.")
        seen: set[tuple[str, tuple[str, ...]]] = set()
        current_failure = failure
        for _ in range(3):
            plan = await _npm_recovery_plan(staged_frontend, dependency, target, current_failure.output)
            if not plan:
                break
            key = (plan.strategy, tuple(plan.specs))
            if key in seen:
                break
            seen.add(key)
            await log.warning(plan.summary)
            await log.info("Recovery retry: " + ", ".join(plan.specs))
            if plan.regenerate_lockfile:
                await owned_release_io(_apply_npm_recovery_specs, staged_frontend, plan.specs)
                lockfile = staged_frontend / "package-lock.json"
                if lockfile.exists():
                    lockfile.unlink()
                await log.info("Regenerating npm lockfile from the repaired package.json peer group.")
                command = ["npm", "install", "--package-lock-only", "--no-audit"]
            else:
                command = ["npm", "install", *plan.specs, "--package-lock-only"]
            try:
                await self._run_command(command, cwd=staged_frontend, log=log, timeout=700)
                await self._run_command(["npm", "install", "--package-lock-only", "--dry-run"], cwd=staged_frontend, log=log, timeout=300)
                return True
            except DependencyCommandError as next_failure:
                current_failure = next_failure

        llm_plan = await self._llm_recovery_plan(
            ecosystem="npm",
            dependency=dependency,
            target_version=target,
            failed_command=current_failure.command,
            command_output=current_failure.output,
        )
        if llm_plan:
            await log.info(f"Recovery advisor: {llm_plan.get('summary')}")
            await log.warning("The suggested recovery is not in the approved automatic strategy set, so IACS is leaving the live workspace unchanged.")
        return False

    async def _normalize_npm_lockfile_for_ci(self, staged_frontend: Path, log: "JobLogger") -> None:
        await log.info("Normalizing npm lockfile before clean install verification.")
        await self._run_command(
            ["npm", "install", "--package-lock-only", "--no-audit"],
            cwd=staged_frontend,
            log=log,
            timeout=300,
        )

    async def _llm_recovery_plan(
        self,
        *,
        ecosystem: str,
        dependency: ExternalDependency,
        target_version: str,
        failed_command: list[str],
        command_output: str,
    ) -> dict[str, Any] | None:
        runtime = await get_runtime_config()
        provider_name = runtime.llm_provider
        if provider_name == "local":
            return None
        prompt = (
            "A dependency update command failed inside an isolated IACS staging workspace. "
            "Diagnose the likely root cause and propose a recovery plan. Do not suggest unsafe commands, "
            "do not ignore failing tests, and do not use --force unless there is no safer path. "
            "Return strict JSON with keys: root_cause, confidence, safe_to_retry, approved_strategy, summary, commands. "
            "approved_strategy must be one of: npm_peer_group, npm_clean_install, pip_constraint_backtrack, docker_pull_retry, none.\n\n"
            f"Ecosystem: {ecosystem}\n"
            f"Dependency: {dependency.package_name}\n"
            f"Current version: {dependency.current_version}\n"
            f"Target version: {target_version}\n"
            f"Manifest: {dependency.manifest_path}\n"
            f"Failed command: {' '.join(failed_command)}\n"
            f"Output:\n{_truncate(command_output, 16000)}"
        )
        try:
            llm = get_llm_provider(provider_name)
            result = await complete_with_provider_options(
                llm,
                [
                    ChatMessageInput(
                        role="system",
                        content="You are a dependency update recovery advisor. Prefer safe, reversible, minimal changes.",
                    ),
                    ChatMessageInput(role="user", content=prompt),
                ],
                max_output_tokens=900,
                request_purpose="dependency_updates.recovery_plan",
            )
            return _parse_recovery_json(result.text)
        except Exception as exc:
            logger.warning("dependency_update_recovery_plan_failed", extra={"provider": provider_name, "error": str(exc)})
            return None

    async def _set_job_phase(self, job_id: uuid.UUID, phase: str) -> None:
        async with AsyncSessionLocal() as session:
            job = await session.get(DependencyUpdateJob, job_id)
            if job:
                job.phase = phase
                await session.commit()
        self._append_job_event(str(job_id), {"type": "phase", "phase": phase, "message": phase})

    def _append_job_event(self, job_id: str, event: dict[str, Any]) -> None:
        payload = {"job_id": job_id, "created_at": datetime.now(tz=UTC).isoformat(), **event}
        stream = self._job_streams.setdefault(job_id, [])
        stream.append(payload)
        if len(stream) > JOB_STREAM_LIMIT:
            del stream[: len(stream) - JOB_STREAM_LIMIT]
        for queue in list(self._job_waiters.get(job_id, set())):
            queue.put_nowait(payload)

    def _discover_dependencies(self) -> list[ManifestDependency]:
        root = _workspace_root()
        direct_names: set[tuple[str, str]] = set()
        rows: list[ManifestDependency] = []
        rows.extend(self._parse_backend_pyproject(root, direct_names))
        rows.extend(self._parse_frontend_package_json(root, direct_names))
        rows.extend(self._parse_frontend_lockfile(root, direct_names))
        rows.extend(self._parse_installed_python_distributions(direct_names))
        rows.extend(self._parse_container_images(root, direct_names))
        deduped: dict[tuple[str, str, str, str], ManifestDependency] = {}
        for row in rows:
            key = (row.ecosystem, row.normalized_name, row.manifest_path, row.manifest_section)
            deduped[key] = row
        return list(deduped.values())

    def _parse_backend_pyproject(self, root: Path, direct_names: set[tuple[str, str]]) -> list[ManifestDependency]:
        path = root / "backend" / "pyproject.toml"
        if not path.exists():
            path = Path.cwd() / "backend" / "pyproject.toml"
        if not path.exists():
            path = Path.cwd() / "pyproject.toml"
        if not path.exists():
            return []
        data = tomllib.loads(path.read_text())
        rows: list[ManifestDependency] = []
        for section, dependencies in (
            ("project.dependencies", data.get("project", {}).get("dependencies", [])),
            ("project.optional-dependencies.dev", data.get("project", {}).get("optional-dependencies", {}).get("dev", [])),
        ):
            for spec in dependencies or []:
                try:
                    requirement = Requirement(str(spec))
                except InvalidRequirement:
                    continue
                name = requirement.name
                direct_names.add(("python", normalize_package_name(name)))
                rows.append(
                    ManifestDependency(
                        ecosystem="python",
                        package_name=name,
                        current_version=_pinned_or_installed_python_version(name, str(requirement.specifier)),
                        dependant_area=_dependant_area(name),
                        manifest_path=_relative_path(path),
                        manifest_section=section,
                        requirement_spec=str(spec),
                        is_direct=True,
                        metadata={"specifier": str(requirement.specifier), "extras": sorted(requirement.extras)},
                    )
                )
        return rows

    def _parse_frontend_package_json(self, root: Path, direct_names: set[tuple[str, str]]) -> list[ManifestDependency]:
        path = root / "frontend" / "package.json"
        if not path.exists():
            return []
        data = json.loads(path.read_text())
        rows: list[ManifestDependency] = []
        for section in ("dependencies", "devDependencies"):
            for name, spec in (data.get(section) or {}).items():
                direct_names.add(("npm", normalize_package_name(name)))
                rows.append(
                    ManifestDependency(
                        ecosystem="npm",
                        package_name=name,
                        current_version=_npm_lock_version(root, name) or str(spec).lstrip("^~>=< "),
                        dependant_area=_dependant_area(name),
                        manifest_path=_relative_path(path),
                        manifest_section=section,
                        requirement_spec=str(spec),
                        is_direct=True,
                        metadata={"specifier": spec},
                    )
                )
        return rows

    def _parse_frontend_lockfile(self, root: Path, direct_names: set[tuple[str, str]]) -> list[ManifestDependency]:
        path = root / "frontend" / "package-lock.json"
        if not path.exists():
            return []
        data = json.loads(path.read_text())
        rows: list[ManifestDependency] = []
        for package_path, payload in (data.get("packages") or {}).items():
            if not package_path.startswith("node_modules/") or not isinstance(payload, dict):
                continue
            name = package_path.replace("node_modules/", "", 1)
            if ("npm", normalize_package_name(name)) in direct_names:
                continue
            rows.append(
                ManifestDependency(
                    ecosystem="npm",
                    package_name=name,
                    current_version=str(payload.get("version") or ""),
                    dependant_area="Frontend Runtime",
                    manifest_path=_relative_path(path),
                    manifest_section="transitive",
                    requirement_spec=None,
                    is_direct=False,
                    metadata={"resolved": payload.get("resolved"), "license": payload.get("license")},
                )
            )
        return rows

    def _parse_installed_python_distributions(self, direct_names: set[tuple[str, str]]) -> list[ManifestDependency]:
        rows: list[ManifestDependency] = []
        try:
            from importlib import metadata
        except ImportError:
            return rows
        for dist in metadata.distributions():
            name = dist.metadata.get("Name") or dist.metadata.get("Summary")
            if not name:
                continue
            normalized = normalize_package_name(name)
            if ("python", normalized) in direct_names:
                continue
            rows.append(
                ManifestDependency(
                    ecosystem="python",
                    package_name=name,
                    current_version=dist.version,
                    dependant_area="Backend Runtime",
                    manifest_path="runtime:python",
                    manifest_section="installed_distribution",
                    requirement_spec=None,
                    is_direct=False,
                    metadata={"summary": dist.metadata.get("Summary")},
                )
            )
        return rows

    def _parse_container_images(self, root: Path, direct_names: set[tuple[str, str]]) -> list[ManifestDependency]:
        rows: list[ManifestDependency] = []
        for path in [root / "backend" / "Dockerfile", root / "frontend" / "Dockerfile", root / "docker-compose.yml"]:
            if not path.exists():
                continue
            text = path.read_text()
            for image in _docker_images_from_text(text, path.name):
                normalized = normalize_package_name(image["name"])
                if ("docker_image", normalized) in direct_names:
                    continue
                direct_names.add(("docker_image", normalized))
                rows.append(
                    ManifestDependency(
                        ecosystem="docker_image",
                        package_name=image["name"],
                        current_version=image["tag"],
                        dependant_area=_dependant_area(image["name"]),
                        manifest_path=_relative_path(path),
                        manifest_section=image["source"],
                        requirement_spec=image["raw"],
                        is_direct=True,
                        metadata=image,
                    )
                )
        return rows

    async def _persist_discovered_dependencies(self, discovered: list[ManifestDependency]) -> dict[str, int]:
        async with AsyncSessionLocal() as session:
            existing_rows = (await session.scalars(select(ExternalDependency))).all()
            existing = {
                (row.ecosystem, row.normalized_name, row.manifest_path or "", row.manifest_section or ""): row
                for row in existing_rows
            }
            seen: set[tuple[str, str, str, str]] = set()
            created = 0
            updated = 0
            for item in discovered:
                key = (item.ecosystem, item.normalized_name, item.manifest_path, item.manifest_section)
                seen.add(key)
                row = existing.get(key)
                if row:
                    row.package_name = item.package_name
                    row.current_version = item.current_version
                    row.dependant_area = item.dependant_area
                    row.requirement_spec = item.requirement_spec
                    row.is_direct = item.is_direct
                    row.is_enabled = True
                    row.metadata_ = item.metadata
                    if row.latest_version and item.current_version:
                        row.update_available = _is_newer(row.latest_version, item.current_version)
                    updated += 1
                else:
                    session.add(
                        ExternalDependency(
                            ecosystem=item.ecosystem,
                            package_name=item.package_name,
                            normalized_name=item.normalized_name,
                            current_version=item.current_version,
                            dependant_area=item.dependant_area,
                            manifest_path=item.manifest_path,
                            manifest_section=item.manifest_section,
                            requirement_spec=item.requirement_spec,
                            is_direct=item.is_direct,
                            is_enabled=True,
                            metadata_=item.metadata,
                        )
                    )
                    created += 1
            disabled = 0
            for key, row in existing.items():
                if key not in seen and row.is_enabled:
                    row.is_enabled = False
                    disabled += 1
            await session.commit()
        return {"created": created, "updated": updated, "disabled": disabled}

    async def _latest_version(self, dependency: ExternalDependency) -> tuple[str | None, dict[str, Any]]:
        if dependency.ecosystem == "python":
            url = f"https://pypi.org/pypi/{quote(dependency.package_name)}/json"
            async with httpx.AsyncClient(timeout=12, follow_redirects=True, trust_env=False) as client:
                response = await client.get(url)
            if response.status_code >= 400:
                return dependency.current_version, {"source": url, "error": f"HTTP {response.status_code}"}
            payload = response.json()
            return str(payload.get("info", {}).get("version") or dependency.current_version or ""), {
                "source": url,
                "summary": payload.get("info", {}).get("summary"),
                "project_urls": payload.get("info", {}).get("project_urls") or {},
            }
        if dependency.ecosystem == "npm":
            url = f"https://registry.npmjs.org/{quote(dependency.package_name, safe='@/')}"
            async with httpx.AsyncClient(timeout=12, follow_redirects=True, trust_env=False) as client:
                response = await client.get(url)
            if response.status_code >= 400:
                return dependency.current_version, {"source": url, "error": f"HTTP {response.status_code}"}
            payload = response.json()
            latest = payload.get("dist-tags", {}).get("latest")
            return str(latest or dependency.current_version or ""), {
                "source": url,
                "description": payload.get("description"),
                "homepage": payload.get("homepage"),
            }
        return dependency.current_version, {"source": dependency.manifest_path, "detail": "Docker image latest lookup is not automatic."}

    async def _release_notes(self, dependency: ExternalDependency, target_version: str) -> dict[str, Any]:
        if dependency.ecosystem == "python":
            url = f"https://pypi.org/pypi/{quote(dependency.package_name)}/{quote(target_version)}/json"
            async with httpx.AsyncClient(timeout=12, follow_redirects=True, trust_env=False) as client:
                response = await client.get(url)
            if response.status_code < 400:
                payload = response.json()
                info = payload.get("info", {})
                return {
                    "source": url,
                    "title": f"{dependency.package_name} {target_version}",
                    "body": _truncate(str(info.get("description") or info.get("summary") or ""), 20000),
                }
        if dependency.ecosystem == "npm":
            url = f"https://registry.npmjs.org/{quote(dependency.package_name, safe='@/')}"
            async with httpx.AsyncClient(timeout=12, follow_redirects=True, trust_env=False) as client:
                response = await client.get(url)
            if response.status_code < 400:
                payload = response.json()
                version_payload = (payload.get("versions") or {}).get(target_version, {})
                return {
                    "source": url,
                    "title": f"{dependency.package_name} {target_version}",
                    "body": _truncate(
                        "\n".join(
                            str(part or "")
                            for part in [
                                payload.get("description"),
                                version_payload.get("description"),
                                payload.get("readme"),
                            ]
                        ),
                        20000,
                    ),
                }
        return {
            "source": dependency.manifest_path,
            "title": f"{dependency.package_name} {target_version}",
            "body": "No release notes could be fetched automatically.",
        }

    def _scan_usage(self, package_name: str) -> dict[str, Any]:
        root = _workspace_root()
        patterns = {package_name, package_name.replace("-", "_")}
        references: list[dict[str, Any]] = []
        for pattern in patterns:
            try:
                result = subprocess.run(
                    ["rg", "-n", "--glob", "!frontend/node_modules/**", "--glob", "!data/**", "--glob", "!logs/**", pattern, str(root)],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            for line in result.stdout.splitlines()[:80]:
                path, line_no, text = _split_rg_line(line)
                if path:
                    references.append({"path": path, "line": line_no, "text": text[:300]})
        return {
            "package": package_name,
            "reference_count": len(references),
            "references": references[:60],
            "scan_root": str(root),
        }

    async def _llm_or_heuristic_analysis(
        self,
        *,
        dependency: ExternalDependency,
        target_version: str,
        changelog: dict[str, Any],
        usage: dict[str, Any],
        provider: str | None,
    ) -> dict[str, Any]:
        runtime = await get_runtime_config()
        provider_name = provider or runtime.llm_provider
        prompt = (
            "Analyze this external dependency update for IACS.\n"
            "Return strict JSON only with keys: verdict, summary_markdown, breaking_changes, "
            "verification_steps, suggested_diff.\n"
            "verdict must be one of safe, warning, breaking. Be conservative. Tie risks to release notes "
            "or concrete local usage. Do not expose secrets. Keep verification_steps concise; prefix steps "
            "with [automated] when IACS can run them during the update job, or [operator] when a human "
            "should manually check behavior after the job.\n\n"
            f"Dependency: {dependency.package_name}\n"
            f"Ecosystem: {dependency.ecosystem}\n"
            f"Dependant area: {dependency.dependant_area}\n"
            f"Current version: {dependency.current_version}\n"
            f"Target version: {target_version}\n"
            f"Manifest: {dependency.manifest_path} {dependency.manifest_section}\n\n"
            f"Local usage scan:\n{json.dumps(usage, default=str)[:12000]}\n\n"
            f"Release notes source: {changelog.get('source')}\n"
            f"Release notes:\n{str(changelog.get('body') or '')[:20000]}"
        )
        if provider_name != "local":
            try:
                llm = get_llm_provider(provider_name)
                result = await complete_with_provider_options(
                    llm,
                    [
                        ChatMessageInput(
                            role="system",
                            content="You are a careful dependency update reviewer for a private access-control system.",
                        ),
                        ChatMessageInput(role="user", content=prompt),
                    ],
                    max_output_tokens=1200,
                    request_purpose="dependency_updates.analysis",
                )
                parsed = _parse_analysis_json(result.text)
                parsed["provider"] = provider_name
                parsed["model"] = _provider_model(runtime, provider_name)
                parsed["raw_result"] = {"text": result.text}
                return parsed
            except (ProviderNotConfiguredError, Exception) as exc:
                logger.warning("dependency_update_llm_analysis_fallback", extra={"provider": provider_name, "error": str(exc)})
        fallback = _heuristic_analysis(dependency, target_version, changelog, usage)
        fallback["provider"] = "local"
        fallback["model"] = "heuristic"
        fallback["raw_result"] = {"fallback_reason": "local heuristic analysis"}
        return fallback

    def _write_manifest_snapshot(self, staging: Path, *, source_root: Path | None = None) -> dict[str, Any]:
        root = source_root or _workspace_root()
        manifest_root = staging / "manifests"
        manifest_root.mkdir(parents=True, exist_ok=True)
        copied: list[dict[str, Any]] = []
        for relative in (*BUILD_INPUTS, "frontend/src/vite-env.d.ts"):
            source = root / relative
            if not source.exists():
                continue
            target = manifest_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append({"path": relative, "sha256": _sha256_file(source), "size_bytes": source.stat().st_size})
        return {"files": copied}

    async def _write_config_snapshot(self, staging: Path, dependency: ExternalDependency | None) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            rows = (await session.scalars(select(SystemSetting).order_by(SystemSetting.key))).all()
        payload = [
            {
                "key": row.key,
                "category": row.category,
                "value": row.value,
                "is_secret": row.is_secret,
                "description": row.description,
            }
            for row in rows
            if _setting_relevant_to_dependency(row.key, dependency)
        ]
        (staging / "settings.json").write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return {"settings_count": len(payload), "scope": dependency.dependant_area if dependency else "all"}

    async def _cache_current_artifact(
        self,
        staging: Path,
        dependency: ExternalDependency | None,
        *,
        log: "JobLogger | None",
    ) -> None:
        if not dependency or not dependency.current_version:
            return
        artifact_dir = staging / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        if dependency.ecosystem == "python" and shutil.which("python"):
            command = [sys.executable, "-m", "pip", "download", "--no-deps", "--dest", str(artifact_dir), f"{dependency.package_name}=={dependency.current_version}"]
        elif dependency.ecosystem == "npm" and shutil.which("npm"):
            command = ["npm", "pack", f"{dependency.package_name}@{dependency.current_version}", "--pack-destination", str(artifact_dir)]
        else:
            (artifact_dir / "artifact-unavailable.txt").write_text(
                f"No local artifact cache command available for {dependency.ecosystem}:{dependency.package_name}."
            )
            return
        try:
            if log:
                await log.info("Caching current package artifact: " + " ".join(command))
            completed = await run_dependency_process(command, _workspace_root(), 180)
            (artifact_dir / "artifact-cache.log").write_bytes(completed.stdout)
            if completed.returncode:
                raise DependencyUpdateError("Package artifact caching failed; the source backup is not an offline package installation proof.")
        except Exception as exc:
            (artifact_dir / "artifact-cache-error.txt").write_text(str(exc))

    def _write_generated_compose_override(self, mode: str, source: str, options: str) -> None:
        root = _workspace_root()
        path = root / GENERATED_COMPOSE
        if mode == "local":
            body = (
                "# Generated by IACS Update & Rollback settings.\n"
                "# Backup storage uses the repository bind mount at ./data/backend/dependency-update-backups.\n"
                "x-iacs-update-backups:\n"
                "  mode: local\n"
                "  host_path: ./data/backend/dependency-update-backups\n"
            )
        else:
            host_path = source or "./data/backend/dependency-update-backups"
            body = (
                "# Generated by IACS Update & Rollback settings.\n"
                "# Docker named volumes are intentionally not used by this project.\n"
                "# Mount the remote share on the host, then bind that mounted path into the containers.\n"
                "x-iacs-update-backups:\n"
                f"  mode: {mode}\n"
                f"  host_path: {json.dumps(host_path)}\n"
                f"  mount_options: {json.dumps(options)}\n"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(body)
            os.replace(temp_path, path)
            path.chmod(0o600)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    async def _get_dependency(self, dependency_id: uuid.UUID | None) -> ExternalDependency:
        if not dependency_id:
            raise DependencyUpdateError("Dependency ID is required.")
        async with AsyncSessionLocal() as session:
            dependency = await session.get(ExternalDependency, dependency_id)
            if not dependency:
                raise DependencyUpdateError("Dependency not found.")
            session.expunge(dependency)
            return dependency

    async def _latest_analysis_for_target(self, dependency_id: uuid.UUID, target_version: str) -> DependencyUpdateAnalysis | None:
        async with AsyncSessionLocal() as session:
            analysis = (
                await session.scalars(
                    select(DependencyUpdateAnalysis)
                    .where(
                        DependencyUpdateAnalysis.dependency_id == dependency_id,
                        DependencyUpdateAnalysis.target_version == target_version,
                    )
                    .order_by(DependencyUpdateAnalysis.created_at.desc())
                    .limit(1)
                )
            ).first()
            if analysis:
                session.expunge(analysis)
            return analysis


class JobLogger:
    def __init__(self, service: DependencyUpdateService, job_id: str) -> None:
        self._service = service
        self._job_id = job_id
        self._path = JOB_LOG_DIR / f"{job_id}.log"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    async def info(self, message: str) -> None:
        await self._write("info", message)

    async def warning(self, message: str) -> None:
        await self._write("warning", message)

    async def stdout(self, message: str) -> None:
        await self._write("stdout", message)

    async def error(self, message: str) -> None:
        await self._write("error", message)

    async def _write(self, kind: str, message: str) -> None:
        clean = str(sanitize_payload(message))
        line = f"{datetime.now(tz=UTC).isoformat()} {kind.upper()} {clean}\n"
        await owned_release_io(_append_text, self._path, line)
        self._service._append_job_event(
            self._job_id,
            {"type": kind, "phase": kind, "message": clean},
        )


def serialize_dependency(row: ExternalDependency | None, analysis: DependencyUpdateAnalysis | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "id": str(row.id),
        "ecosystem": row.ecosystem,
        "package_name": row.package_name,
        "normalized_name": row.normalized_name,
        "current_version": row.current_version,
        "latest_version": row.latest_version,
        "dependant_area": row.dependant_area,
        "manifest_path": row.manifest_path,
        "manifest_section": row.manifest_section,
        "requirement_spec": row.requirement_spec,
        "is_direct": row.is_direct,
        "is_enabled": row.is_enabled,
        "update_available": row.update_available,
        "risk_status": row.risk_status,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "metadata": row.metadata_ or {},
        "latest_analysis": serialize_analysis(analysis) if analysis else None,
    }


def serialize_analysis(row: DependencyUpdateAnalysis) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "dependency_id": str(row.dependency_id),
        "target_version": row.target_version,
        "provider": row.provider,
        "model": row.model,
        "verdict": row.verdict,
        "summary_markdown": row.summary_markdown,
        "changelog_source": row.changelog_source,
        "changelog_markdown": row.changelog_markdown,
        "usage_summary": row.usage_summary or {},
        "breaking_changes": row.breaking_changes,
        "verification_steps": row.verification_steps,
        "suggested_diff": row.suggested_diff,
        "created_at": row.created_at.isoformat(),
    }


def serialize_backup(row: DependencyUpdateBackup) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "dependency_id": str(row.dependency_id) if row.dependency_id else None,
        "package_name": row.package_name,
        "ecosystem": row.ecosystem,
        "version": row.version,
        "reason": row.reason,
        "archive_path": row.archive_path,
        "storage_root": row.storage_root,
        "checksum_sha256": row.checksum_sha256,
        "size_bytes": row.size_bytes,
        "created_at": row.created_at.isoformat(),
        "restored_at": row.restored_at.isoformat() if row.restored_at else None,
        "metadata": row.metadata_ or {},
    }


def serialize_job(row: DependencyUpdateJob) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "dependency_id": str(row.dependency_id) if row.dependency_id else None,
        "kind": row.kind,
        "status": row.status,
        "phase": row.phase,
        "actor": row.actor,
        "target_version": row.target_version,
        "backup_id": str(row.backup_id) if row.backup_id else None,
        "stdout_log_path": row.stdout_log_path,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "result": row.result or {},
        "error": row.error,
        "trace_id": row.trace_id,
    }


def _diagnose_dependency_failure(
    kind: str,
    dependency: ExternalDependency | None,
    target_version: str | None,
    exc: Exception,
    *, job_result: dict | None = None,
) -> dict[str, Any]:
    output = exc.output if isinstance(exc, DependencyCommandError) else ""
    command = " ".join(exc.command) if isinstance(exc, DependencyCommandError) else ""
    package_name = dependency.package_name if dependency else "unknown package"
    ecosystem = dependency.ecosystem if dependency else "unknown"
    affected = _npm_direct_names_from_peer_output(output) if ecosystem == "npm" else []
    if package_name not in affected and package_name != "unknown package":
        affected.insert(0, package_name)

    diagnosis = {
        "category": "unknown",
        "title": "Update job failed",
        "summary": str(exc),
        "safe_state": "Source outcome is unverified; inspect the retained transaction evidence. Deployment was not performed.",
        "retry_recommendation": "Review the diagnosis and retry after the listed issue has been resolved.",
        "actions": [
            "Open the Updates & Rollbacks logs for the full command output.",
            "Retry the update after resolving the reported blocker.",
        ],
        "affected_packages": affected[:8],
        "command": command,
        "ecosystem": ecosystem,
        "package_name": package_name,
        "target_version": target_version,
        "kind": kind,
    }

    lower_output = output.lower()
    lower_error = str(exc).lower()
    if "npm ci" in lower_output and "can only install packages when your package.json and package-lock.json" in lower_output:
        diagnosis.update(
            {
                "category": "npm_lockfile_sync",
                "title": "npm lockfile sync failed",
                "summary": (
                    "npm rejected the staged package-lock because it was missing entries required by package.json. "
                    "This can happen after peer dependency repair unless the lockfile is normalized before clean install."
                ),
                "retry_recommendation": (
                    "Retry the update. IACS now normalizes the staged npm lockfile before running npm ci."
                ),
                "actions": [
                    "Retry the update so IACS can rebuild the staged lockfile and rerun npm ci.",
                    "If it repeats, inspect the listed missing packages in the Updates & Rollbacks log.",
                ],
            }
        )
    elif "eresolve" in lower_output or "peer dependency" in lower_output:
        diagnosis.update(
            {
                "category": "npm_peer_conflict",
                "title": "npm peer dependency conflict",
                "summary": (
                    "npm could not build a compatible dependency graph. This is usually caused by a peer package "
                    "that must be updated together with the selected package."
                ),
                "retry_recommendation": (
                    "Retry the update. IACS now attempts a bounded peer-group repair in an isolated workspace, "
                    "then verifies the graph strictly before anything is promoted."
                ),
                "actions": [
                    "Retry the update so IACS can update the matching direct peer group together.",
                    "If it still fails, inspect the listed affected packages and choose the parent framework update first.",
                    "Avoid using force installs; IACS will only promote a strict, build-verified dependency graph.",
                ],
            }
        )
    elif "@rollup/rollup-" in lower_output and "cannot find module" in lower_output:
        diagnosis.update(
            {
                "category": "npm_optional_native_dependency",
                "title": "Native optional dependency missing in verification workspace",
                "summary": (
                    "The frontend build could not load Rollup's platform-specific optional package. "
                    "This happens when node_modules was created for a different platform or optional dependencies were skipped."
                ),
                "retry_recommendation": (
                    "Retry the update. IACS now verifies from a clean Linux npm install with optional dependencies enabled."
                ),
                "actions": [
                    "Retry the update to let IACS rebuild the isolated npm workspace cleanly.",
                    "If it repeats, run a frontend dependency reinstall and check npm optional dependency handling.",
                ],
            }
        )
    elif "ts5107" in lower_output or "moduleresolution=node10" in lower_output:
        diagnosis.update(
            {
                "category": "typescript_config_migration",
                "title": "TypeScript config migration required",
                "summary": (
                    "TypeScript rejected the current tsconfig module resolution setting. "
                    "For this Vite frontend, moduleResolution should be migrated from Node/node10 to Bundler."
                ),
                "retry_recommendation": (
                    "Retry the update. IACS now applies this tsconfig migration in the isolated workspace and only promotes it after the frontend build passes."
                ),
                "actions": [
                    "Retry the TypeScript update so IACS can migrate tsconfig.json and rerun the build.",
                    "If the build reports additional TypeScript errors, review those compiler errors as code migration work.",
                ],
            }
        )
    elif "ts2882" in lower_output and "side-effect import" in lower_output:
        diagnosis.update(
            {
                "category": "typescript_vite_declaration",
                "title": "Vite type declaration required",
                "summary": (
                    "TypeScript could not resolve the frontend CSS side-effect import. "
                    "The Vite client declaration file is required for this newer compiler."
                ),
                "retry_recommendation": (
                    "Retry the update. IACS now adds the standard src/vite-env.d.ts declaration in the isolated workspace and reruns the build."
                ),
                "actions": [
                    "Retry the TypeScript update so IACS can add the Vite type declaration.",
                    "If new compiler errors appear, treat them as the next migration item and IACS will keep the live workspace rolled back until verified.",
                ],
            }
        )
    elif "no matching distribution found" in lower_output or "could not find a version" in lower_output:
        diagnosis.update(
            {
                "category": "pip_no_candidate",
                "title": "Python package version is not installable",
                "summary": "pip could not find an installable artifact for the target version in this runtime environment.",
                "retry_recommendation": "Check whether the version supports Python 3.12 and this container platform before retrying.",
                "actions": [
                    "Inspect the package release metadata for Python and platform support.",
                    "Try the next lower available version if the latest release does not publish a compatible artifact.",
                ],
            }
        )
    elif "timed out" in lower_error:
        diagnosis.update(
            {
                "category": "command_timeout",
                "title": "Package manager command timed out",
                "summary": "The package manager did not finish within the guarded execution window.",
                "retry_recommendation": "Retry after checking network/package registry availability.",
                "actions": [
                    "Validate network access from the updater container.",
                    "Retry once the registry is reachable and responsive.",
                ],
            }
        )

    result = job_result or {}
    receipt = result.get("restoration") or result.get("promotion")
    prepared = isinstance(receipt, dict) and receipt.get("status") == "source_prepared"
    diagnosis.update(
        source_status="prepared" if prepared else "unverified",
        review_required=True,
        safe_state=("Source files were prepared, but a later job step failed. Deployment was not performed."
                    if prepared else diagnosis["safe_state"]),
        retry_recommendation="Review the retained transaction and current source before starting another update or restore.",
        actions=["Inspect the job logs and retained candidate/transaction evidence.",
                 "Compare the current source with the transaction afterimages; preserve any intervening edits.",
                 "Use a separately reviewed recovery or release plan; this failed job does not authorize an automatic retry."],
    )
    diagnosis["technical_detail"] = _truncate(output or str(exc), 2400)
    return sanitize_payload(diagnosis)


def normalize_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower().strip()


def _workspace_root() -> Path:
    configured = Path(os.environ.get("IACS_WORKSPACE_DIR", "/workspace"))
    if configured.exists():
        return configured
    cwd = Path.cwd()
    if (cwd / "docker-compose.yml").exists():
        return cwd
    for parent in [cwd, *cwd.parents]:
        if (parent / "docker-compose.yml").exists():
            return parent
    return cwd


def _cache_root() -> Path:
    path = settings.data_dir / "dependency-update-cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _backup_root() -> Path:
    if BACKUP_CONTAINER_ROOT.exists() or Path("/app").exists():
        return BACKUP_CONTAINER_ROOT
    return settings.data_dir / "dependency-update-backups"


def _relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(_workspace_root()))
    except ValueError:
        return str(path)


def _dependant_area(name: str) -> str:
    normalized = normalize_package_name(name)
    mappings = {
        "uiprotect": "UniFi Protect",
        "pyicloud": "iCloud Calendar",
        "discord-py": "Discord Messaging",
        "discord.py": "Discord Messaging",
        "apprise": "Notifications",
        "home-assistant": "Home Assistant",
        "fastapi": "Backend API",
        "sqlalchemy": "Database",
        "asyncpg": "Database",
        "redis": "Realtime Infrastructure",
        "react": "Frontend Runtime",
        "react-dom": "Frontend Runtime",
        "vite": "Frontend Build",
        "typescript": "Frontend Build",
        "lucide-react": "Frontend UI",
        "monaco": "Telemetry Diff Viewer",
        "postgres": "Database",
        "nginx": "Frontend Runtime",
    }
    for needle, area in mappings.items():
        if needle in normalized:
            return area
    if normalized.startswith("@monaco") or normalized.startswith("@tanstack"):
        return "Frontend Runtime"
    return "System Core"


def _pinned_or_installed_python_version(name: str, specifier: str) -> str | None:
    for spec in specifier.split(","):
        spec = spec.strip()
        if spec.startswith("=="):
            return spec[2:]
    try:
        from importlib import metadata

        return metadata.version(name)
    except Exception:
        return None


def _npm_lock_version(root: Path, package_name: str) -> str | None:
    path = root / "frontend" / "package-lock.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    package = (data.get("packages") or {}).get(f"node_modules/{package_name}")
    if isinstance(package, dict):
        return str(package.get("version") or "") or None
    return None


def _npm_peer_update_group(frontend: Path, dependency: ExternalDependency, target_version: str, command_output: str) -> list[str]:
    output = command_output.lower()
    if "eresolve" not in output and "peer" not in output:
        return []
    if "/" not in dependency.package_name or not dependency.package_name.startswith("@"):
        return []
    package_json = frontend / "package.json"
    if not package_json.exists():
        return []
    try:
        data = json.loads(package_json.read_text())
    except json.JSONDecodeError:
        return []

    scope = dependency.package_name.split("/", 1)[0]
    names: list[str] = []
    for section in ("dependencies", "devDependencies"):
        dependencies = data.get(section) or {}
        if not isinstance(dependencies, dict):
            continue
        for name in dependencies:
            if name == dependency.package_name or name.startswith(f"{scope}/"):
                if name not in names:
                    names.append(name)
    if dependency.package_name not in names:
        names.insert(0, dependency.package_name)
    if len(names) < 2:
        return []
    return [f"{name}@{target_version}" for name in names]


async def _npm_recovery_plan(frontend: Path, dependency: ExternalDependency, target_version: str, command_output: str) -> NpmRecoveryPlan | None:
    output = command_output.lower()
    if "eresolve" not in output and "peer" not in output:
        return None
    direct = _npm_direct_dependency_specs(frontend)
    if not direct:
        return None

    specs: dict[str, str] = {}
    regenerate_lockfile = False
    if dependency.package_name in direct:
        specs[dependency.package_name] = target_version

    scoped_group = _npm_peer_update_group(frontend, dependency, target_version, command_output)
    if scoped_group:
        regenerate_lockfile = True
        for spec in scoped_group:
            name, version = _split_npm_spec(spec)
            specs[name] = version

    if dependency.package_name in {"react", "react-dom"}:
        regenerate_lockfile = True
        for name in ("react", "react-dom"):
            if name in direct:
                specs[name] = target_version

    for name in _npm_direct_names_from_peer_output(command_output):
        if name not in direct:
            continue
        if name == dependency.package_name:
            specs[name] = target_version
            continue
        if name in {"react", "react-dom"} and dependency.package_name in {"react", "react-dom"}:
            specs[name] = target_version
            continue
        if name.startswith("@types/") and not dependency.package_name.startswith("@types/"):
            continue
        latest = await _npm_latest_version(name, frontend)
        if latest:
            specs[name] = latest

    if len(specs) <= 1:
        return None
    formatted = [f"{name}@{version}" for name, version in specs.items()]
    summary = (
        "npm reported a peer dependency conflict. IACS will retry in the isolated workspace by "
        "updating the smallest matching direct peer group it can identify."
    )
    return NpmRecoveryPlan(
        strategy="npm_peer_group",
        summary=summary,
        specs=formatted,
        regenerate_lockfile=regenerate_lockfile,
    )


def _npm_direct_dependency_specs(frontend: Path) -> dict[str, str]:
    package_json = frontend / "package.json"
    if not package_json.exists():
        return {}
    try:
        data = json.loads(package_json.read_text())
    except json.JSONDecodeError:
        return {}
    direct: dict[str, str] = {}
    for section in ("dependencies", "devDependencies"):
        dependencies = data.get(section) or {}
        if not isinstance(dependencies, dict):
            continue
        for name, spec in dependencies.items():
            direct[str(name)] = str(spec)
    return direct


def _npm_direct_names_from_peer_output(command_output: str) -> list[str]:
    names: list[str] = []
    pattern = r"(?:Found:|from|peer)\s+((?:@[\w.-]+/)?[\w.-]+)@"
    for match in re.finditer(pattern, command_output):
        name = match.group(1)
        if name not in names:
            names.append(name)
    return names


async def _npm_latest_version(package_name: str, cwd: Path) -> str | None:
    if not re.match(r"^(?:@[\w.-]+/)?[\w.-]+$", package_name):
        return None
    try:
        result = await run_dependency_process(["npm", "view", package_name, "version", "--json"], cwd, 45)
    except (DependencyProcessTimeout, OSError):
        return None
    if result.returncode != 0:
        return None
    raw = result.stdout.decode(errors="replace").strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = raw.strip('"')
    return payload if isinstance(payload, str) and payload else None


def _split_npm_spec(spec: str) -> tuple[str, str]:
    if spec.startswith("@"):
        name, version = spec.rsplit("@", 1)
        return name, version
    name, version = spec.split("@", 1)
    return name, version


def _update_npm_package_specs(frontend: Path, package_names: list[str], target_version: str) -> None:
    package_json = frontend / "package.json"
    data = json.loads(package_json.read_text())
    changed = False
    for section in ("dependencies", "devDependencies"):
        dependencies = data.get(section) or {}
        if not isinstance(dependencies, dict):
            continue
        for name in package_names:
            if name in dependencies:
                dependencies[name] = f"^{target_version}"
                changed = True
    if not changed:
        raise DependencyUpdateError("No npm package.json entries matched the peer dependency retry group.")
    package_json.write_text(json.dumps(data, indent=2) + "\n")


def _apply_npm_recovery_specs(frontend: Path, specs: list[str]) -> None:
    package_json = frontend / "package.json"
    data = json.loads(package_json.read_text())
    changed = False
    for spec in specs:
        name, version = _split_npm_spec(spec)
        for section in ("dependencies", "devDependencies"):
            dependencies = data.get(section) or {}
            if not isinstance(dependencies, dict):
                continue
            if name in dependencies:
                dependencies[name] = f"^{version}"
                changed = True
    if not changed:
        raise DependencyUpdateError("No npm package.json entries matched the peer dependency recovery plan.")
    package_json.write_text(json.dumps(data, indent=2) + "\n")


def _repair_tsconfig_for_typescript_6(frontend: Path, command_output: str) -> bool:
    output = command_output.lower()
    if "ts5107" not in output and "moduleresolution=node10" not in output:
        return False
    tsconfig = frontend / "tsconfig.json"
    if not tsconfig.exists():
        return False
    try:
        data = json.loads(tsconfig.read_text())
    except json.JSONDecodeError:
        return False
    compiler_options = data.get("compilerOptions")
    if not isinstance(compiler_options, dict):
        return False
    current = str(compiler_options.get("moduleResolution") or "").lower()
    if current not in {"node", "node10"}:
        return False
    compiler_options["moduleResolution"] = "Bundler"
    tsconfig.write_text(json.dumps(data, indent=2) + "\n")
    return True


def _repair_vite_type_declarations(frontend: Path, command_output: str) -> bool:
    output = command_output.lower()
    if "ts2882" not in output and "side-effect import" not in output:
        return False
    if ".css" not in output and "vite/client" not in output:
        return False
    src = frontend / "src"
    if not src.exists():
        return False
    declaration = src / "vite-env.d.ts"
    existing = declaration.read_text() if declaration.exists() else ""
    line = '/// <reference types="vite/client" />'
    if line in existing:
        return False
    declaration.write_text((existing.rstrip() + "\n" if existing.strip() else "") + line + "\n")
    return True


def _copy_frontend_build_context(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for filename in ["package.json", "package-lock.json", "tsconfig.json", "vite.config.ts", "index.html"]:
        source_path = source / filename
        if source_path.exists():
            shutil.copy2(source_path, destination / filename)
    public = source / "public"
    if public.exists():
        shutil.copytree(public, destination / "public")
    shutil.copytree(source / "src", destination / "src")


def _copy_manifest_to_staging_root(live_root: Path, staged_root: Path, relative_path: str) -> None:
    if not relative_path:
        raise DependencyUpdateError("Dependency manifest path is missing.")
    source = safe_file(live_root, relative_path)
    if not source.exists():
        raise DependencyUpdateError(f"Manifest not found: {relative_path}")
    target = safe_file(staged_root, relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _docker_images_from_text(text: str, source_name: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if source_name == "Dockerfile":
        for match in re.finditer(r"^\s*FROM\s+([^\s]+)", text, flags=re.MULTILINE):
            rows.append(_docker_image_row(match.group(1), "FROM"))
    else:
        for match in re.finditer(r"^\s*image:\s*['\"]?([^'\"\s]+)", text, flags=re.MULTILINE):
            rows.append(_docker_image_row(match.group(1), "image"))
    return rows


def _docker_image_row(raw: str, source: str) -> dict[str, str]:
    image = raw.split("@", 1)[0]
    name, _, tag = image.rpartition(":")
    if "/" in tag and not name:
        name, tag = image, "latest"
    if not name:
        name, tag = image, "latest"
    return {"raw": raw, "name": name, "tag": tag or "latest", "source": source}


def _is_newer(candidate: str, current: str) -> bool:
    try:
        return Version(candidate) > Version(current)
    except InvalidVersion:
        return candidate != current


def _parse_analysis_json(text: str) -> dict[str, Any]:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?", "", clean).strip()
        clean = re.sub(r"```$", "", clean).strip()
    match = re.search(r"\{.*\}", clean, flags=re.DOTALL)
    payload = json.loads(match.group(0) if match else clean)
    verdict = str(payload.get("verdict") or "warning").lower()
    if verdict not in {"safe", "warning", "breaking"}:
        verdict = "warning"
    return {
        "verdict": verdict,
        "summary_markdown": str(payload.get("summary_markdown") or payload.get("summary") or ""),
        "breaking_changes": payload.get("breaking_changes") if isinstance(payload.get("breaking_changes"), list) else [],
        "verification_steps": payload.get("verification_steps") if isinstance(payload.get("verification_steps"), list) else [],
        "suggested_diff": str(payload.get("suggested_diff") or ""),
    }


def _parse_recovery_json(text: str) -> dict[str, Any] | None:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?", "", clean).strip()
        clean = re.sub(r"```$", "", clean).strip()
    match = re.search(r"\{.*\}", clean, flags=re.DOTALL)
    try:
        payload = json.loads(match.group(0) if match else clean)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    strategy = str(payload.get("approved_strategy") or "none")
    if strategy not in {"npm_peer_group", "npm_clean_install", "pip_constraint_backtrack", "docker_pull_retry", "none"}:
        strategy = "none"
    return {
        "root_cause": str(payload.get("root_cause") or ""),
        "confidence": payload.get("confidence"),
        "safe_to_retry": bool(payload.get("safe_to_retry")),
        "approved_strategy": strategy,
        "summary": str(payload.get("summary") or payload.get("root_cause") or "No recovery summary returned."),
        "commands": payload.get("commands") if isinstance(payload.get("commands"), list) else [],
    }


def _heuristic_analysis(
    dependency: ExternalDependency,
    target_version: str,
    changelog: dict[str, Any],
    usage: dict[str, Any],
) -> dict[str, Any]:
    body = str(changelog.get("body") or "").lower()
    current = dependency.current_version or ""
    breaking_markers = ["breaking", "removed", "drop support", "migration", "major", "incompatible"]
    warning_markers = ["deprecated", "security", "auth", "api", "schema", "typescript", "pydantic"]
    if any(marker in body for marker in breaking_markers):
        verdict = "breaking"
    elif any(marker in body for marker in warning_markers) or usage.get("reference_count", 0) == 0:
        verdict = "warning"
    else:
        try:
            verdict = "breaking" if Version(target_version).major > Version(current).major else "safe"
        except Exception:
            verdict = "warning"
    return {
        "verdict": verdict,
        "summary_markdown": (
            f"**{dependency.package_name}** `{current or 'unknown'}` -> `{target_version}` was reviewed with local heuristics.\n\n"
            f"Local references found: `{usage.get('reference_count', 0)}`.\n"
            "Run provider-backed analysis for a stronger changelog review before applying risky updates."
        ),
        "breaking_changes": [],
        "verification_steps": [
            "Review package release notes.",
            "Run backend compile checks.",
            "Run frontend build when frontend packages changed.",
            "Verify affected integrations from API & Integrations.",
        ],
        "suggested_diff": "",
    }


def _provider_model(runtime: Any, provider: str) -> str:
    return {
        "openai": runtime.openai_model,
        "gemini": runtime.gemini_model,
        "claude": runtime.anthropic_model,
        "anthropic": runtime.anthropic_model,
        "ollama": runtime.ollama_model,
    }.get(provider, "")


def _setting_relevant_to_dependency(key: str, dependency: ExternalDependency | None) -> bool:
    if dependency is None:
        return True
    area = dependency.dependant_area.lower()
    prefixes = {
        "unifi": "unifi_protect_",
        "icloud": "icloud_",
        "discord": "discord_",
        "dvla": "dvla_",
        "home assistant": "home_assistant_",
        "esphome": "esphome_",
        "notifications": "apprise_",
        "backend": "",
        "frontend": "",
        "system": "",
    }
    return any(label in area and (not prefix or key.startswith(prefix)) for label, prefix in prefixes.items())


def _split_rg_line(line: str) -> tuple[str, int, str]:
    parts = line.split(":", 2)
    if len(parts) != 3:
        return "", 0, line
    try:
        line_no = int(parts[1])
    except ValueError:
        line_no = 0
    return parts[0], line_no, parts[2]


def _update_python_requirement(dependency: ExternalDependency, target_version: str, root: Path | None = None) -> None:
    path = (root or _workspace_root()) / str(dependency.manifest_path or "")
    if not path.exists():
        raise DependencyUpdateError(f"Python manifest not found: {dependency.manifest_path}")
    text = path.read_text()
    normalized = normalize_package_name(dependency.package_name)
    changed = False

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        quote_char = match.group("quote")
        spec = match.group("spec")
        try:
            requirement = Requirement(spec)
        except InvalidRequirement:
            return match.group(0)
        if normalize_package_name(requirement.name) != normalized:
            return match.group(0)
        extras = f"[{','.join(sorted(requirement.extras))}]" if requirement.extras else ""
        marker = f"; {requirement.marker}" if requirement.marker else ""
        changed = True
        return f"{quote_char}{requirement.name}{extras}=={target_version}{marker}{quote_char}"

    next_text = re.sub(r"(?P<quote>[\"'])(?P<spec>[^\"']+)(?P=quote)", replace, text)
    if not changed:
        raise DependencyUpdateError(f"Could not find {dependency.package_name} in {dependency.manifest_path}.")
    path.write_text(next_text)


def _update_docker_image_tag(dependency: ExternalDependency, target_version: str, root: Path | None = None) -> None:
    path = (root or _workspace_root()) / str(dependency.manifest_path or "")
    if not path.exists():
        raise DependencyUpdateError(f"Docker manifest not found: {dependency.manifest_path}")
    raw = str((dependency.metadata_ or {}).get("raw") or dependency.requirement_spec or "")
    image_name = str((dependency.metadata_ or {}).get("name") or dependency.package_name)
    if not raw or not image_name:
        raise DependencyUpdateError("Docker image metadata is incomplete.")
    replacement = f"{image_name}:{target_version}"
    text = path.read_text()
    if raw not in text:
        raise DependencyUpdateError(f"Could not find image reference {raw} in {dependency.manifest_path}.")
    path.write_text(text.replace(raw, replacement, 1))


ARCHIVE_PROCESS_TIMEOUT = 600


async def _create_zstd_archive(source: Path, archive_path: Path) -> None:
    zstd = shutil.which("zstd")
    if not zstd:
        raise DependencyUpdateError("zstd is required to create dependency update backups.")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the compressed output private until the bounded process has ended.
    # Cancellation drains both the tar thread and the process before cleanup.
    with tempfile.TemporaryDirectory(prefix=".iacs-dependency-archive-", dir=archive_path.parent) as tmp:
        tar_path, compressed = Path(tmp) / "backup.tar", Path(tmp) / "backup.tar.zst"
        await owned_release_io(_write_tar_archive, source, tar_path)
        result = await run_dependency_process(
            [zstd, "-T0", "-3", "-f", str(tar_path), "-o", str(compressed)], Path(tmp), ARCHIVE_PROCESS_TIMEOUT)
        if result.returncode != 0:
            raise DependencyUpdateError("zstd backup compression failed: " + result.stdout.decode(errors="replace").strip())
        os.replace(compressed, archive_path)


async def _extract_archive(archive_path: Path, destination: Path) -> None:
    if archive_path.name.endswith(".tar.zst"):
        zstd = shutil.which("zstd")
        if not zstd:
            raise DependencyUpdateError("zstd is required to restore dependency update backups.")
        with tempfile.TemporaryDirectory(prefix="iacs-dependency-extract-") as tmp:
            tar_path = Path(tmp) / "backup.tar"
            result = await run_dependency_process(
                [zstd, "-d", "-f", str(archive_path), "-o", str(tar_path)], Path(tmp), ARCHIVE_PROCESS_TIMEOUT)
            if result.returncode != 0:
                raise DependencyUpdateError("zstd backup decompression failed: " + result.stdout.decode(errors="replace").strip())
            await owned_release_io(_extract_tar_archive, tar_path, destination)
        return
    await owned_release_io(_extract_tar_archive, archive_path, destination)


def _write_tar_archive(source: Path, tar_path: Path) -> None:
    with tarfile.open(tar_path, "w") as archive:
        archive.add(source, arcname=".")


def _extract_tar_archive(archive_path: Path, destination: Path) -> None:
    with tarfile.open(archive_path, "r:*") as archive:
        _safe_extract_archive(archive, destination)


def _safe_extract_archive(archive: tarfile.TarFile, destination: Path) -> None:
    destination_root = destination.resolve()
    for member in archive.getmembers():
        target = (destination / member.name).resolve()
        try:
            target.relative_to(destination_root)
        except ValueError as exc:
            raise DependencyUpdateError("Backup archive contains an unsafe path.") from exc
        if member.issym() or member.islnk():
            link_target = (target.parent / member.linkname).resolve()
            try:
                link_target.relative_to(destination_root)
            except ValueError as exc:
                raise DependencyUpdateError("Backup archive contains an unsafe link.") from exc
    archive.extractall(destination, filter="data")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(text)


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit] + "\n\n[truncated]"


@lru_cache
def get_dependency_update_service() -> DependencyUpdateService:
    return DependencyUpdateService()
