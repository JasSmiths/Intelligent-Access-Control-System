"""Actual compressed backup/source restoration, entirely inside disposable test resources.

Run only through the guarded PostgreSQL namespace used by test_recovery_boundaries.
Requires the pinned backend's real zstd executable and IACS_IMAGE_IDENTITY recorded
by the runner. No package download, image activation, database restore or live
source path is exercised. Database dump/restore is a separate lead-owned check.
"""
from test_recovery_boundaries import isolated_resources as isolated_resources

import json
import os
from pathlib import Path
import re
import shutil
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.models import DependencyUpdateBackup, DependencyUpdateJob, User
from app.models.enums import UserRole
from app.services import dependency_updates as owner
from app.services import release_artifacts as artifacts

pytestmark = pytest.mark.asyncio

SOURCE = {
    "AGENTS.md": "Synthetic archive rehearsal only.\n",
    "README.md": "Synthetic system source.\n",
    "backend/app/example.py": "VALUE = 'synthetic-before'\n",
    "backend/alembic/versions/synthetic.py": "# Inert source fixture; never executed.\n",
    "backend/pyproject.toml": '[project]\nname="synthetic"\nversion="1"\n',
    "backend/uv.lock": "synthetic-python-lock-before\n",
    "backend/Dockerfile": "# Synthetic build input; never built.\n",
    "backend/alembic.ini": "# Synthetic build input; never executed.\n",
    "frontend/src/main.ts": "export const marker = 'synthetic-before';\n",
    "frontend/public/marker.txt": "synthetic public source\n",
    "frontend/package.json": '{"name":"synthetic","version":"1"}\n',
    "frontend/package-lock.json": '{"name":"synthetic","lockfileVersion":3,"packages":{}}\n',
    "frontend/tsconfig.json": '{"compilerOptions":{}}\n',
    "frontend/Dockerfile": "# Synthetic build input; never built.\n",
    "scripts/synthetic.py": "# Inert operational source.\n",
    "docs/synthetic.md": "Synthetic documentation.\n",
    ".github/workflows/synthetic.yml": "# Inert CI source.\n",
}
PROMOTED = {
    "backend/uv.lock": "synthetic-python-lock-after\n",
    "frontend/package-lock.json": '{"name":"synthetic","lockfileVersion":3,"packages":{"changed":{}}}\n',
    "frontend/src/vite-env.d.ts": '/// <reference types="vite/client" />\n',
}


def hashes(root):
    return {name: artifacts.sha256(root / name) for name in artifacts.source_files(root)}


@pytest_asyncio.fixture
async def rehearsal(isolated_resources, monkeypatch, tmp_path):
    assert shutil.which("zstd"), "Pinned backend must supply zstd; no skipped archive rehearsal"
    image = os.environ.get("IACS_IMAGE_IDENTITY", "")
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", image), "Runner must record its pinned image ID explicitly"
    root, storage = tmp_path / "repo", tmp_path / "backup-storage"
    for name, content in SOURCE.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    storage.mkdir()
    monkeypatch.setattr(owner, "_workspace_root", lambda: root)
    monkeypatch.setattr(owner, "JOB_LOG_DIR", tmp_path / "job-logs")
    monkeypatch.setattr(owner, "emit_audit_log", lambda **kwargs: None)
    service = owner.DependencyUpdateService()
    monkeypatch.setattr(service, "storage_status", AsyncMock(return_value={"ok": True, "backup_root": str(storage)}))
    monkeypatch.setattr(service, "sync_enrollment", AsyncMock(return_value={}))

    async def inert_post_restore_check(command, *, cwd, log, timeout):
        assert command == ["python", "-m", "compileall", "-q", "backend/app"]
        assert cwd == root and timeout == 180
        return "Post-restore syntax check intentionally inert in file/archive rehearsal."

    # Archive compression/decompression still uses the actual process owner/zstd.
    # Only the unrelated post-restore application syntax check is inert.
    compile_check = AsyncMock(side_effect=inert_post_restore_check)
    monkeypatch.setattr(service, "_run_command", compile_check)
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE dependency_update_jobs, dependency_update_backups, external_dependencies CASCADE"))
        user = User(username=f"synthetic-restore-{uuid.uuid4().hex}", full_name="Synthetic Restore Admin",
                    password_hash="unused-inert", role=UserRole.ADMIN, is_active=True)
        session.add(user)
        await session.commit()
        revisions = sorted((await session.scalars(text("SELECT version_num FROM alembic_version"))).all())
    assert revisions, "Rehearsal requires the actual migrated disposable PostgreSQL schema"
    original = hashes(root)
    assert set(original) == set(SOURCE)
    # dependency_id=None exercises actual system backup without package downloads.
    public_backup = await service.create_backup(None, reason="synthetic-archive-rehearsal", user=user)
    async with AsyncSessionLocal() as session:
        backup = await session.get(DependencyUpdateBackup, uuid.UUID(public_backup["id"]))
    identity = backup.metadata_["release_identity"]
    assert identity["files"] == original
    assert identity["schema_revisions"] == revisions and identity["image_identity"] == image
    assert identity["locks"] == {name: original[name] for name in ("backend/uv.lock", "frontend/package-lock.json")}
    archive = Path(backup.archive_path)
    assert archive.is_relative_to(storage) and archive.suffixes == [".tar", ".zst"]
    assert artifacts.sha256(archive) == backup.checksum_sha256
    assert archive.stat().st_size == backup.size_bytes > 0
    validation = await service._validate_backup_archive(backup)
    assert validation["release_identity"] == identity
    assert validation["settings_count"] == 0 and validation["artifact_count"] == 0

    candidate = tmp_path / "candidate"
    for name, content in PROMOTED.items():
        path = candidate / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    promotion = await artifacts.prepare_and_publish(candidate, root, sorted(PROMOTED),
        expected_source=original, release_context=identity, checks=["inert synthetic manifest bytes"])
    assert promotion["before"]["frontend/src/vite-env.d.ts"] is None
    async with AsyncSessionLocal() as session:
        saved = await session.get(DependencyUpdateBackup, backup.id)
        saved.metadata_ = {**saved.metadata_, "promotion": promotion}
        job = DependencyUpdateJob(kind="restore", actor="Synthetic Restore Admin", actor_user_id=user.id,
            backup_id=backup.id, status="queued", phase="queued", result={})
        session.add(job)
        await session.commit()
    value = SimpleNamespace(service=service, user=user, job=job, backup=backup, archive=archive,
        root=root, original=original, identity=identity, promotion=promotion,
        compile_check=compile_check, tmp=tmp_path)
    try:
        yield value
    finally:
        await service.stop()


async def test_real_compressed_archive_restores_full_source_and_owned_absent_preimage(rehearsal):
    item = rehearsal
    unpacked = item.tmp / "roundtrip"
    unpacked.mkdir()
    await owner._extract_archive(item.archive, unpacked)
    artifacts.validate_source(unpacked / "source", item.identity)
    assert hashes(unpacked / "source") == item.original
    assert json.loads((unpacked / "release.json").read_text()) == item.identity
    assert json.loads((unpacked / "settings.json").read_text()) == []
    assert json.loads((unpacked / "backup.json").read_text())["backup_id"] == str(item.backup.id)
    manifest_files = {entry["path"]: entry for entry in item.backup.manifest_snapshot["files"]}
    for name, entry in manifest_files.items():
        assert artifacts.sha256(unpacked / "manifests" / name) == entry["sha256"] == item.original[name]
    assert {"backend/uv.lock", "frontend/package-lock.json"} <= manifest_files.keys()
    assert not (unpacked / "manifests/frontend/src/vite-env.d.ts").exists()

    await item.service._run_job(item.job.id, kind="restore", user=item.user)
    async with AsyncSessionLocal() as session:
        job = await session.get(DependencyUpdateJob, item.job.id)
        backup = await session.get(DependencyUpdateBackup, item.backup.id)
        assert job.status == "completed" and job.result["source_status"] == "prepared"
        assert job.result["deployment"] == "not_performed" and job.result["runtime_health"] == "not_checked"
        receipt = job.result["restoration"]
        assert backup.metadata_["restoration"] == receipt
        assert backup.restored_by_user_id == item.user.id and backup.restored_at is not None
    assert receipt["files"] == {name: item.original.get(name) for name in PROMOTED}
    assert hashes(item.root) == item.original
    assert not (item.root / "frontend/src/vite-env.d.ts").exists()
    assert artifacts.sha256(item.archive) == item.backup.checksum_sha256
    marker = item.root / "data/backend/release-transactions" / receipt["transaction_id"] / "transaction.json"
    assert json.loads(marker.read_text())["state"] == "completed"
    item.compile_check.assert_awaited_once()
    version = await owner.run_dependency_process([shutil.which("zstd"), "--version"], item.tmp, 10)
    assert version.returncode == 0
    (item.tmp / "rehearsal-evidence.json").write_text(json.dumps({
        "case": "full_source_archive_restore", "status": "passed",
        "python": sys.version, "zstd": version.stdout.decode().strip(),
        "running_image_identity_from_runner": item.identity["image_identity"],
        "schema_revisions": item.identity["schema_revisions"],
        "archive_sha256": artifacts.sha256(item.archive),
        "source_before_sha256": artifacts.fingerprint(item.original),
        "source_restored_sha256": artifacts.fingerprint(hashes(item.root)),
        "locks": item.identity["locks"], "restoration": receipt,
        "database_restore": "not_performed", "image_activation": "not_performed",
        "post_restore_compile_check": "inert_test_seam",
    }, indent=2, sort_keys=True))


async def change_archive(item, change):
    unpacked = item.tmp / "modified-archive"
    unpacked.mkdir()
    await owner._extract_archive(item.archive, unpacked)
    change(unpacked)
    await owner._create_zstd_archive(unpacked, item.archive)
    # Recompute outer checksum deliberately: inner identity/preimage verification
    # must still reject a self-consistent compressed archive with invalid content.
    async with AsyncSessionLocal() as session:
        backup = await session.get(DependencyUpdateBackup, item.backup.id)
        backup.checksum_sha256 = artifacts.sha256(item.archive)
        backup.size_bytes = item.archive.stat().st_size
        await session.commit()


@pytest.mark.parametrize(("fault", "error_fragment"), [
    ("foreign_manifest", "changed before promotion"),
    ("foreign_application", "Application source changed"),
    ("missing_original_manifest", "Application source changed"),
    ("missing_promoted_lock", "changed before promotion"),
    ("outer_checksum", "checksum validation failed"),
    ("inner_source", "source hashes do not match"),
    ("missing_archived_lock", "source hashes do not match"),
    ("schema_identity", "Schema changed since backup"),
    ("missing_image", "image compatibility is unverified"),
    ("different_image", "image compatibility is unverified"),
    ("absent_preimage_collision", "absent backup preimage"),
])
async def test_archive_recovery_refuses_incompatible_evidence_and_preserves_current_source(rehearsal, monkeypatch, fault, error_fragment):
    item = rehearsal
    if fault == "foreign_manifest":
        (item.root / "backend/uv.lock").write_text("foreign lock edit must survive\n")
    elif fault == "foreign_application":
        (item.root / "backend/app/example.py").write_text("FOREIGN = True\n")
    elif fault == "missing_original_manifest":
        (item.root / "backend/pyproject.toml").unlink()
    elif fault == "missing_promoted_lock":
        (item.root / "backend/uv.lock").unlink()
    elif fault == "outer_checksum":
        with item.archive.open("ab") as stream:
            stream.write(b"synthetic checksum damage")
    elif fault == "inner_source":
        await change_archive(item, lambda path: (path / "source/backend/app/example.py").write_text("TAMPERED = True\n"))
    elif fault == "missing_archived_lock":
        await change_archive(item, lambda path: (path / "source/backend/uv.lock").unlink())
    elif fault == "schema_identity":
        def change(path):
            identity = json.loads((path / "release.json").read_text())
            identity["schema_revisions"] = ["synthetic-incompatible-schema"]
            (path / "release.json").write_text(json.dumps(identity))
        await change_archive(item, change)
    elif fault == "missing_image":
        monkeypatch.delenv("IACS_IMAGE_IDENTITY")
    elif fault == "different_image":
        monkeypatch.setenv("IACS_IMAGE_IDENTITY", "sha256:" + ("0" if item.identity["image_identity"] != "sha256:" + "0" * 64 else "1") * 64)
    elif fault == "absent_preimage_collision":
        def change(path):
            target = path / "manifests/frontend/src/vite-env.d.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("invented preimage\n")
        await change_archive(item, change)
    before = hashes(item.root)
    await item.service._run_job(item.job.id, kind="restore", user=item.user)
    async with AsyncSessionLocal() as session:
        job = await session.get(DependencyUpdateJob, item.job.id)
        backup = await session.get(DependencyUpdateBackup, item.backup.id)
        assert job.status == "failed", job.error
        assert error_fragment.lower() in job.error.lower()
        assert "restoration" not in (job.result or {}) and "restoration" not in backup.metadata_
        assert backup.restored_at is None
    assert hashes(item.root) == before
    assert (item.root / "frontend/src/vite-env.d.ts").read_text() == PROMOTED["frontend/src/vite-env.d.ts"]
    item.compile_check.assert_not_awaited()
    (item.tmp / "rehearsal-evidence.json").write_text(json.dumps({
        "case": fault, "status": "passed", "refusal": error_fragment,
        "source_before_attempt_sha256": artifacts.fingerprint(before),
        "source_after_refusal_sha256": artifacts.fingerprint(hashes(item.root)),
        "restoration_receipt": "absent", "current_source_preserved": True,
    }, indent=2, sort_keys=True))
