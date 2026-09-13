"""Exact release-source evidence and recoverable manifest-set promotion.

This owner does not install, deploy, contact Docker, restore a database, or
activate an executor. A package update changes build inputs; it cannot prove
that a running image has changed or that a schema rollback is compatible.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

SOURCE_DIRECTORIES = ("backend/app", "backend/alembic", "backend/tests", "frontend/src", "frontend/public",
                      "scripts", "docs", ".github")
BUILD_INPUTS = ("backend/pyproject.toml", "backend/uv.lock", "backend/Dockerfile", "backend/alembic.ini", "backend/README.md",
                "frontend/package.json", "frontend/package-lock.json", "frontend/Dockerfile", "frontend/tsconfig.json",
                "frontend/vite.config.ts", "frontend/nginx.conf", "frontend/index.html", "docker-compose.yml",
                "AGENTS.md", "README.md", ".dockerignore", "backend/.dockerignore", "frontend/.dockerignore")
EXCLUDED_PARTS = {"__pycache__", "node_modules", ".venv", "dist", "data", "logs", ".git", ".pytest_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".pem", ".key", ".p12", ".pfx"}


class ReleaseArtifactError(ValueError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_file(root: Path, relative: str) -> Path:
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts:
        raise ReleaseArtifactError("Release artifact path escapes its source root.")
    target = root / value
    ancestors = [root.joinpath(*value.parts[:index]) for index in range(len(value.parts) + 1)]
    if any(item.is_symlink() for item in ancestors) or not target.resolve().is_relative_to(root.resolve()):
        raise ReleaseArtifactError("Release artifacts cannot follow symlinks outside their source root.")
    return target


def source_files(root: Path) -> list[str]:
    selected = {name for name in BUILD_INPUTS if safe_file(root, name).is_file()}
    inspected = 0
    for directory in SOURCE_DIRECTORIES:
        base = safe_file(root, directory)
        if not base.exists():
            continue
        for parent, directories, files in os.walk(base, followlinks=False):
            directories[:] = sorted(name for name in directories if name not in EXCLUDED_PARTS)
            if any((Path(parent) / name).is_symlink() for name in directories):
                raise ReleaseArtifactError("Release source contains an unsupported directory symlink.")
            for name in files:
                inspected += 1
                if inspected > 20000:
                    raise ReleaseArtifactError("Unexpected release source inventory; inspect the source boundary.")
                path = Path(parent) / name
                if name.startswith(".env") or path.suffix in EXCLUDED_SUFFIXES:
                    continue
                if path.is_symlink():
                    raise ReleaseArtifactError("Release source contains an unsupported file symlink.")
                selected.add(path.relative_to(root).as_posix())
    return sorted(selected)


def fingerprint(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def capture_source(root: Path, destination: Path, *, schema_revisions: list[str], image_identity: str | None) -> dict:
    hashes = {name: sha256(safe_file(root, name)) for name in source_files(root)}
    for name, expected in hashes.items():
        target = safe_file(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(safe_file(root, name), target)
        if sha256(target) != expected or sha256(safe_file(root, name)) != expected:
            raise ReleaseArtifactError("Source changed during release snapshot; take a fresh snapshot.")
    if hashes != {name: sha256(safe_file(root, name)) for name in source_files(root)}:
        raise ReleaseArtifactError("Source inventory changed during release snapshot.")
    return {"format": 1, "files": hashes, "source_sha256": fingerprint(hashes),
            "locks": {name: hashes.get(name) for name in ("backend/uv.lock", "frontend/package-lock.json")},
            "schema_revisions": sorted(schema_revisions), "image_identity": image_identity,
            "image_identity_status": "recorded" if image_identity else "unavailable",
            "activation": "separate_release_required"}


def validate_source(snapshot: Path, identity: dict) -> None:
    if identity.get("format") != 1 or not isinstance(identity.get("files"), dict):
        raise ReleaseArtifactError("Backup lacks complete release identity; manual recovery review is required.")
    expected = identity["files"]
    actual = {name: sha256(safe_file(snapshot, name)) for name in source_files(snapshot)}
    if actual != expected or fingerprint(actual) != identity.get("source_sha256"):
        raise ReleaseArtifactError("Release source hashes do not match the recorded backup.")
    if any(not identity.get("locks", {}).get(name) for name in ("backend/uv.lock", "frontend/package-lock.json")):
        raise ReleaseArtifactError("Release source does not contain both dependency locks.")


def assert_manifest_restore_compatible(root: Path, identity: dict, *, schema_revisions: list[str],
                                       image_identity: str | None, restored_paths: list[str]) -> None:
    """A UI manifest restore may not cross an application/schema/image cutover."""
    if sorted(schema_revisions) != identity.get("schema_revisions"):
        raise ReleaseArtifactError("Schema changed since backup; use a separately reviewed compatible recovery build.")
    if not image_identity or image_identity != identity.get("image_identity"):
        raise ReleaseArtifactError("Running image compatibility is unverified; automatic manifest restore is blocked.")
    before = {name: value for name, value in identity.get("files", {}).items() if name not in restored_paths}
    current = {name: sha256(safe_file(root, name)) for name in source_files(root) if name not in restored_paths}
    if before != current:
        raise ReleaseArtifactError("Application source changed since backup; manifest-only rollback is insufficient.")


def publish_manifest_set(staged: Path, root: Path, paths: list[str], *, expected: dict[str, str | None],
                         absent_paths: set[str] | None = None, expected_source: dict[str, str] | None = None) -> dict:
    """Publish one verified set with a durable preimage and interruption marker.

    Each replacement is atomic. The set is recoverable, not filesystem-atomic:
    a process crash leaves its journal for explicit review and blocks another
    promotion. Ordinary exceptions restore only still-owned afterimages. No image is built
    or activated by this function.
    """
    journal_root = safe_file(root, "data/backend/release-transactions")
    journal_root.mkdir(parents=True, exist_ok=True)
    lock_path = safe_file(journal_root, "promotion.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ReleaseArtifactError("Another manifest transaction is in progress.") from exc
        return _publish_locked(staged, root, paths, expected=expected, journal_root=journal_root,
                               absent_paths=absent_paths or set(), expected_source=expected_source)


def _current_hash(path: Path) -> str | None:
    return sha256(path) if path.exists() else None


def _source_hashes(root: Path) -> dict[str, str]:
    return {name: sha256(safe_file(root, name)) for name in source_files(root)}


def _publish_locked(staged: Path, root: Path, paths: list[str], *, expected: dict[str, str | None],
                    journal_root: Path, absent_paths: set[str], expected_source: dict[str, str] | None) -> dict:
    for existing in journal_root.glob("*/transaction.json"):
        if json.loads(existing.read_text()).get("state") not in {"completed", "rolled_back"}:
            raise ReleaseArtifactError("An interrupted manifest transaction requires review before another update.")
    selected = sorted(set(paths))
    if not selected or set(selected) != set(expected) or not absent_paths <= set(selected):
        raise ReleaseArtifactError("Every promoted file requires an expected source hash and explicit deletion intent.")
    if expected_source is not None and _source_hashes(root) != expected_source:
        raise ReleaseArtifactError("Application source changed before promotion; prepare a fresh candidate.")
    after = {}
    for name in selected:
        source, live = safe_file(staged, name), safe_file(root, name)
        if _current_hash(live) != expected[name]:
            raise ReleaseArtifactError("Manifest source changed; prepare a fresh candidate.")
        if name in absent_paths:
            if source.exists():
                raise ReleaseArtifactError("An absent preimage cannot contain replacement bytes.")
            if expected[name] is None:
                raise ReleaseArtifactError("Deletion requires an existing owned afterimage.")
            after[name] = None
        elif source.is_file():
            after[name] = sha256(source)
        else:
            raise ReleaseArtifactError("Candidate is incomplete; prepare a fresh candidate.")
    transaction = journal_root / str(uuid.uuid4())
    transaction.mkdir()
    preimage = transaction / "before"
    for name in selected:
        live = safe_file(root, name)
        if live.exists():
            backup = safe_file(preimage, name)
            backup.parent.mkdir(parents=True, exist_ok=True)
            _replace(live, backup)
            if sha256(backup) != expected[name] or sha256(live) != expected[name]:
                raise ReleaseArtifactError("Manifest source changed while capturing the preimage.")
    _sync_directory(journal_root)
    record = {"format": 1, "state": "prepared", "before": expected,
              "after": after, "replaced": [], "attempting": None}
    marker = transaction / "transaction.json"
    _write_marker(marker, record)
    try:
        for name in selected:
            live = safe_file(root, name)
            # Cooperative writers share flock. These immediate hash fences also
            # detect ordinary editor races, but are not filesystem-atomic CAS.
            if _current_hash(live) != expected[name]:
                raise ReleaseArtifactError("Manifest changed after its preimage was captured.")
            if name not in absent_paths and sha256(safe_file(staged, name)) != after[name]:
                raise ReleaseArtifactError("Candidate changed before replacement.")
            record["attempting"] = name
            _write_marker(marker, record)

            def replaced():
                record["replaced"].append(name)

            if name in absent_paths:
                if _current_hash(live) != expected[name]:
                    raise ReleaseArtifactError("Manifest changed before deletion.")
                live.unlink()
                replaced()
                _sync_directory(live.parent)
            else:
                _replace(safe_file(staged, name), live, on_replaced=replaced,
                         expected_target=expected[name], expected_source=after[name])
            record["attempting"] = None
            _write_marker(marker, record)
        if any(_current_hash(safe_file(root, name)) != after[name] for name in selected):
            raise ReleaseArtifactError("Promoted manifest verification failed.")
        if expected_source is not None:
            promoted_source = dict(expected_source)
            for name, value in after.items():
                if value is None:
                    promoted_source.pop(name, None)
                else:
                    promoted_source[name] = value
            if _source_hashes(root) != promoted_source:
                raise ReleaseArtifactError("Application source changed during promotion.")
        record["state"] = "completed"
        _write_marker(marker, record)
    except Exception as original:
        conflicts, failures = [], []
        for name in reversed(record["replaced"]):
            try:
                live = safe_file(root, name)
                # A rollback has authority only over this transaction's own
                # afterimage. It cannot undo a concurrent edit to any path.
                if _current_hash(live) != after[name]:
                    conflicts.append(name)
                    continue
                if expected[name] is None:
                    live.unlink()
                    _sync_directory(live.parent)
                else:
                    _replace(safe_file(preimage, name), live, expected_target=after[name],
                             expected_source=expected[name])
            except Exception:
                failures.append(name)
        for name in selected:
            try:
                if _current_hash(safe_file(root, name)) != expected[name]:
                    conflicts.append(name)
            except Exception:
                failures.append(name)
        try:
            foreign_source = expected_source is not None and _source_hashes(root) != expected_source
        except Exception:
            foreign_source = True
        record.update(state="review_required" if conflicts or failures or foreign_source else "rolled_back",
                      conflicted_paths=sorted(set(conflicts)), rollback_failed_paths=sorted(set(failures)),
                      source_changed=foreign_source)
        _write_marker(marker, record)
        if record["state"] == "review_required":
            raise ReleaseArtifactError("Manifest transaction requires recovery review; foreign changes were preserved.") from original
        raise
    return {"transaction_id": transaction.name, "files": after, "before": expected, "status": "source_prepared",
            "deployment": "not_performed"}


async def prepare_and_publish(staged: Path, root: Path, paths: list[str], *, expected_source: dict[str, str],
                              release_context: dict, checks: list[str],
                              before_publish: Callable[[], Awaitable[None]] | None = None) -> dict:
    """Retain the exact candidate and checks; promotion never denotes deployment."""
    candidate = safe_file(root, "data/backend/release-candidates/" + str(uuid.uuid4()))
    candidate.mkdir(parents=True)
    identity = await owned_release_io(capture_source, root, candidate / "source",
        schema_revisions=release_context.get("schema_revisions", []),
        image_identity=release_context.get("image_identity"))
    if identity["files"] != expected_source:
        raise ReleaseArtifactError("Source changed while validating the candidate; prepare it again.")
    expected = {name: expected_source.get(name) for name in paths}
    for name in paths:
        _replace(safe_file(staged, name), safe_file(candidate / "source", name))
        identity["files"][name] = sha256(safe_file(candidate / "source", name))
    identity["source_sha256"] = fingerprint(identity["files"])
    identity["locks"] = {name: identity["files"].get(name)
                         for name in ("backend/uv.lock", "frontend/package-lock.json")}
    identity.update(candidate_id=candidate.name, checks=checks, state="verified_source",
                    candidate_image_identity=None, runtime_health="not_checked", deployment="not_performed")
    _write_marker(candidate / "release.json", identity)
    if before_publish is not None:
        await before_publish()
    promotion = await publish_manifest_set_async(candidate / "source", root, paths, expected=expected, expected_source=expected_source)
    return {**promotion, "candidate_id": candidate.name, "source_sha256": identity["source_sha256"],
            "checks": checks, "schema_revisions": identity["schema_revisions"],
            "image_identity": identity["image_identity"], "candidate_image_identity": None}


async def owned_release_io(function, *args, **kwargs):
    """Drain a release file operation before cancellation can dispose its paths.

    Python cannot safely stop a copying thread. The caller therefore retains its
    temporary directory until the operation finishes; spawned processes use the
    bounded dependency-process owner instead.
    """
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if task.done() and not task.cancelled():
            task.exception()
        raise


async def publish_manifest_set_async(staged: Path, root: Path, paths: list[str], *,
                                     expected: dict[str, str | None], absent_paths: set[str] | None = None,
                                     expected_source: dict[str, str] | None = None) -> dict:
    """Keep staging alive until the owned file mutation ends, even on cancellation."""
    return await owned_release_io(publish_manifest_set, staged, root, paths, expected=expected,
                                  absent_paths=absent_paths, expected_source=expected_source)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_marker(path: Path, payload: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".iacs-journal-", dir=path.parent)
    pending = Path(name)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, path)
        _sync_directory(path.parent)
    finally:
        pending.unlink(missing_ok=True)


_UNFENCED = object()


def _replace(source: Path, target: Path, *, on_replaced=None,
             expected_target=_UNFENCED, expected_source: str | None = None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".iacs-candidate-", dir=target.parent)
    pending = Path(name)
    try:
        with source.open("rb") as reader, os.fdopen(descriptor, "wb") as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        if expected_source is not None and sha256(pending) != expected_source:
            raise ReleaseArtifactError("Candidate bytes changed while preparing replacement.")
        if expected_target is not _UNFENCED and (target.is_symlink() or _current_hash(target) != expected_target):
            raise ReleaseArtifactError("Manifest changed immediately before replacement.")
        os.replace(pending, target)
        if on_replaced is not None:
            on_replaced()
        _sync_directory(target.parent)
    finally:
        pending.unlink(missing_ok=True)
