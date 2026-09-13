"""File-only release contracts. Every source and target is a disposable fixture."""
import asyncio
import fcntl
import json
from pathlib import Path
import threading

import pytest

from app.services import release_artifacts as artifacts


def write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    return path


def source(root):
    for name in ["backend/app/example.py", "backend/README.md", "backend/pyproject.toml", "backend/uv.lock",
                 "backend/alembic/script.py.mako", "frontend/package.json", "frontend/package-lock.json",
                 "frontend/src/asset.woff2", "scripts/backend-pytest", "scripts/load-test.mjs"]:
        write(root, name, "initial " + name)
    return {name: artifacts.sha256(root / name) for name in artifacts.source_files(root)}


def markers(root):
    return [json.loads(path.read_text()) for path in (root / "data/backend/release-transactions").glob("*/transaction.json")]


def test_release_identity_covers_build_templates_extensionless_scripts_and_assets(tmp_path):
    root, snapshot = tmp_path / "repo", tmp_path / "snapshot"
    expected = source(root)
    write(root, "backend/app/.env", "synthetic excluded credential")
    write(root, "data/private.json", "synthetic excluded data")
    identity = artifacts.capture_source(root, snapshot, schema_revisions=["revision-synthetic"], image_identity=None)
    assert identity["files"] == expected
    assert identity["image_identity_status"] == "unavailable"
    assert not (snapshot / "backend/app/.env").exists() and not (snapshot / "data").exists()
    artifacts.validate_source(snapshot, identity)
    write(snapshot, "scripts/load-test.mjs", "changed")
    with pytest.raises(artifacts.ReleaseArtifactError, match="hashes"):
        artifacts.validate_source(snapshot, identity)


@pytest.mark.parametrize("relative", ["../escape", "/tmp/escape"])
def test_escaping_manifest_paths_are_rejected(tmp_path, relative):
    with pytest.raises(artifacts.ReleaseArtifactError, match="escapes"):
        artifacts.safe_file(tmp_path, relative)


@pytest.mark.parametrize("directory", [False, True])
def test_source_symlinks_fail_instead_of_claiming_an_incomplete_identity(tmp_path, directory):
    root = tmp_path / "repo"
    source(root)
    target = tmp_path / "external"
    if directory:
        target.mkdir()
    else:
        target.write_text("external source")
    (root / "backend/app/linked").symlink_to(target, target_is_directory=directory)
    with pytest.raises(artifacts.ReleaseArtifactError, match="symlink"):
        artifacts.capture_source(root, tmp_path / "snapshot", schema_revisions=[], image_identity=None)


def test_symlinked_journal_parent_is_rejected_before_writes(tmp_path):
    root, staged, outside = tmp_path / "repo", tmp_path / "staged", tmp_path / "outside"
    expected = source(root)
    outside.mkdir()
    (root / "data").symlink_to(outside, target_is_directory=True)
    write(staged, "backend/uv.lock", "candidate")
    with pytest.raises(artifacts.ReleaseArtifactError, match="symlink"):
        artifacts.publish_manifest_set(staged, root, ["backend/uv.lock"], expected={"backend/uv.lock": expected["backend/uv.lock"]})
    assert not list(outside.iterdir())


def test_fixed_pending_symlink_cannot_redirect_promotion(tmp_path):
    root, staged = tmp_path / "repo", tmp_path / "staged"
    expected = source(root)
    victim = write(tmp_path, "victim", "untouched")
    (root / "backend/.uv.lock.iacs-candidate").symlink_to(victim)
    write(staged, "backend/uv.lock", "candidate")
    result = artifacts.publish_manifest_set(staged, root, ["backend/uv.lock"], expected={"backend/uv.lock": expected["backend/uv.lock"]})
    assert victim.read_text() == "untouched" and result["status"] == "source_prepared"
    assert (root / "backend/uv.lock").read_text() == "candidate"


def test_writer_after_preimage_is_preserved_without_claiming_rollback_ownership(tmp_path, monkeypatch):
    root, staged = tmp_path / "repo", tmp_path / "staged"
    original = source(root)
    name = "backend/uv.lock"
    write(staged, name, "candidate")
    marker = artifacts._write_marker

    def after_preimage(path, record):
        marker(path, record)
        if record["state"] == "prepared" and record["attempting"] is None:
            write(root, name, "foreign editor")

    monkeypatch.setattr(artifacts, "_write_marker", after_preimage)
    with pytest.raises(artifacts.ReleaseArtifactError, match="review"):
        artifacts.publish_manifest_set(staged, root, [name], expected={name: original[name]})
    assert (root / name).read_text() == "foreign editor"
    assert markers(root)[0]["replaced"] == []
    assert markers(root)[0]["state"] == "review_required"


@pytest.mark.parametrize("foreign_target", ["backend/pyproject.toml", "backend/uv.lock"])
def test_failed_promotion_only_restores_its_unchanged_afterimages(tmp_path, monkeypatch, foreign_target):
    root, staged = tmp_path / "repo", tmp_path / "staged"
    original = source(root)
    paths = ["backend/pyproject.toml", "backend/uv.lock"]
    for name in paths:
        write(staged, name, "candidate " + name)
    replace = artifacts._replace

    def interrupted(src, dest, **kwargs):
        if dest == root / paths[1]:
            write(root, foreign_target, "foreign editor")
            raise OSError("synthetic second-file failure")
        replace(src, dest, **kwargs)

    monkeypatch.setattr(artifacts, "_replace", interrupted)
    with pytest.raises(artifacts.ReleaseArtifactError, match="review"):
        artifacts.publish_manifest_set(staged, root, paths, expected={name: original[name] for name in paths})
    assert (root / foreign_target).read_text() == "foreign editor"
    other = next(name for name in paths if name != foreign_target)
    assert artifacts.sha256(root / other) == original[other]
    assert markers(root)[0]["replaced"] == [paths[0]]
    assert markers(root)[0]["state"] == "review_required"


def test_immediate_fence_rechecks_live_after_candidate_copy(tmp_path, monkeypatch):
    root, staged = tmp_path / "repo", tmp_path / "staged"
    original = source(root)
    name = "backend/uv.lock"
    write(staged, name, "candidate")
    copy = artifacts.shutil.copyfileobj

    def edited_while_copying(reader, writer, *args, **kwargs):
        copy(reader, writer, *args, **kwargs)
        if Path(reader.name) == staged / name:
            write(root, name, "foreign editor")

    monkeypatch.setattr(artifacts.shutil, "copyfileobj", edited_while_copying)
    with pytest.raises(artifacts.ReleaseArtifactError, match="review"):
        artifacts.publish_manifest_set(staged, root, [name], expected={name: original[name]})
    assert (root / name).read_text() == "foreign editor"
    assert markers(root)[0]["replaced"] == []


def test_fsync_failure_after_replacement_still_owns_its_afterimage(tmp_path, monkeypatch):
    root, staged = tmp_path / "repo", tmp_path / "staged"
    original = source(root)
    name = "backend/uv.lock"
    write(staged, name, "candidate")
    sync = artifacts._sync_directory
    failed = False

    def fail_once(path):
        nonlocal failed
        if path == root / "backend" and not failed and (root / name).read_text() == "candidate":
            failed = True
            raise OSError("synthetic fsync failure")
        sync(path)

    monkeypatch.setattr(artifacts, "_sync_directory", fail_once)
    with pytest.raises(OSError, match="fsync"):
        artifacts.publish_manifest_set(staged, root, [name], expected={name: original[name]})
    assert failed and artifacts.sha256(root / name) == original[name]
    assert markers(root)[0]["state"] == "rolled_back"
    assert markers(root)[0]["replaced"] == [name]


def test_full_source_change_during_promotion_preserves_editor_and_restores_owned_file(tmp_path, monkeypatch):
    root, staged = tmp_path / "repo", tmp_path / "staged"
    original = source(root)
    name = "backend/uv.lock"
    write(staged, name, "candidate")
    replace = artifacts._replace

    def source_edit(src, dest, **kwargs):
        replace(src, dest, **kwargs)
        if dest == root / name and src == staged / name:
            write(root, "backend/app/example.py", "new executor")

    monkeypatch.setattr(artifacts, "_replace", source_edit)
    with pytest.raises(artifacts.ReleaseArtifactError, match="review"):
        artifacts.publish_manifest_set(staged, root, [name], expected={name: original[name]}, expected_source=original)
    assert artifacts.sha256(root / name) == original[name]
    assert (root / "backend/app/example.py").read_text() == "new executor"
    assert markers(root)[0]["source_changed"] is True


def test_owned_absent_preimage_can_remove_only_the_original_afterimage(tmp_path):
    root, staged, restore = tmp_path / "repo", tmp_path / "staged", tmp_path / "restore"
    original = source(root)
    name = "frontend/src/vite-env.d.ts"
    write(staged, name, "candidate declaration")
    promotion = artifacts.publish_manifest_set(staged, root, [name], expected={name: None}, expected_source=original)
    restoration = artifacts.publish_manifest_set(restore, root, [name], expected=promotion["files"], absent_paths={name})
    assert not (root / name).exists() and restoration["files"] == {name: None}
    artifacts.publish_manifest_set(staged, root, [name], expected={name: None})
    write(root, name, "foreign declaration")
    with pytest.raises(artifacts.ReleaseArtifactError, match="changed"):
        artifacts.publish_manifest_set(restore, root, [name], expected=promotion["files"], absent_paths={name})
    assert (root / name).read_text() == "foreign declaration"


def test_intervening_writer_survives_a_stale_promotion(tmp_path):
    root, staged = tmp_path / "repo", tmp_path / "staged"
    original = source(root)
    write(staged, "backend/uv.lock", "candidate A")
    write(root, "backend/uv.lock", "writer B")
    with pytest.raises(artifacts.ReleaseArtifactError, match="changed"):
        artifacts.publish_manifest_set(staged, root, ["backend/uv.lock"], expected={"backend/uv.lock": original["backend/uv.lock"]})
    assert (root / "backend/uv.lock").read_text() == "writer B"


def test_failed_set_replacement_restores_exact_existing_and_absent_preimages(tmp_path, monkeypatch):
    root, staged = tmp_path / "repo", tmp_path / "staged"
    original = source(root)
    paths = ["backend/pyproject.toml", "backend/new-input", "backend/uv.lock"]
    for name in paths:
        write(staged, name, "new " + name)
    expected = {name: original.get(name) for name in paths}
    replace = artifacts._replace
    failed = False
    def fail_once(src, dest, **kwargs):
        nonlocal failed
        if dest == root / "backend/uv.lock" and not failed:
            failed = True
            raise OSError("synthetic disk fault")
        return replace(src, dest, **kwargs)
    monkeypatch.setattr(artifacts, "_replace", fail_once)
    with pytest.raises(OSError, match="disk fault"):
        artifacts.publish_manifest_set(staged, root, paths, expected=expected)
    assert not (root / "backend/new-input").exists()
    assert artifacts.sha256(root / "backend/pyproject.toml") == original["backend/pyproject.toml"]
    assert artifacts.sha256(root / "backend/uv.lock") == original["backend/uv.lock"]
    assert markers(root)[0]["state"] == "rolled_back"


def test_process_interruption_retains_preimages_and_blocks_another_promotion(tmp_path, monkeypatch):
    root, staged = tmp_path / "repo", tmp_path / "staged"
    original = source(root)
    write(staged, "backend/uv.lock", "candidate")
    replace = artifacts._replace
    def interrupt(src, dest, **kwargs):
        if dest == root / "backend/uv.lock":
            raise SystemExit("synthetic process stop")
        replace(src, dest, **kwargs)
    monkeypatch.setattr(artifacts, "_replace", interrupt)
    with pytest.raises(SystemExit):
        artifacts.publish_manifest_set(staged, root, ["backend/uv.lock"], expected={"backend/uv.lock": original["backend/uv.lock"]})
    assert markers(root)[0]["state"] == "prepared"
    monkeypatch.setattr(artifacts, "_replace", replace)
    with pytest.raises(artifacts.ReleaseArtifactError, match="interrupted"):
        artifacts.publish_manifest_set(staged, root, ["backend/uv.lock"], expected={"backend/uv.lock": original["backend/uv.lock"]})


def test_manifest_promotion_refuses_an_active_other_file_owner(tmp_path):
    root, staged = tmp_path / "repo", tmp_path / "staged"
    original = source(root)
    write(staged, "backend/uv.lock", "candidate")
    lock = root / "data/backend/release-transactions/promotion.lock"
    lock.parent.mkdir(parents=True)
    with lock.open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(artifacts.ReleaseArtifactError, match="in progress"):
            artifacts.publish_manifest_set(staged, root, ["backend/uv.lock"], expected={"backend/uv.lock": original["backend/uv.lock"]})
    assert artifacts.sha256(root / "backend/uv.lock") == original["backend/uv.lock"]


@pytest.mark.parametrize("change", ["schema", "image", "source", "unknown_image"])
def test_manifest_only_restore_cannot_cross_release_compatibility_boundary(tmp_path, change):
    root, snapshot = tmp_path / "repo", tmp_path / "snapshot"
    source(root)
    identity = artifacts.capture_source(root, snapshot, schema_revisions=["r1"], image_identity="sha256:synthetic")
    schema, image = ["r1"], "sha256:synthetic"
    if change == "schema": schema = ["r2"]
    if change == "image": image = "sha256:other"
    if change == "unknown_image": image = None
    if change == "source": write(root, "backend/app/example.py", "different executor")
    with pytest.raises(artifacts.ReleaseArtifactError):
        artifacts.assert_manifest_restore_compatible(root, identity, schema_revisions=schema,
            image_identity=image, restored_paths=["backend/uv.lock"])


@pytest.mark.asyncio
async def test_candidate_retains_complete_source_and_precise_checks_without_claiming_deployment(tmp_path):
    root, staged = tmp_path / "repo", tmp_path / "staged"
    original = source(root)
    write(staged, "backend/uv.lock", "candidate")
    result = await artifacts.prepare_and_publish(staged, root, ["backend/uv.lock"], expected_source=original,
        release_context={"schema_revisions": ["r1"], "image_identity": "sha256:synthetic"}, checks=["synthetic locked check"])
    candidate = root / "data/backend/release-candidates" / result["candidate_id"]
    identity = json.loads((candidate / "release.json").read_text())
    artifacts.validate_source(candidate / "source", identity)
    assert result["files"]["backend/uv.lock"] == artifacts.sha256(root / "backend/uv.lock")
    assert result["checks"] == ["synthetic locked check"]
    assert identity["candidate_image_identity"] is None and identity["deployment"] == "not_performed"
    # A later writer is not silently undone by restoring this transaction.
    write(root, "backend/uv.lock", "later writer")
    with pytest.raises(artifacts.ReleaseArtifactError, match="changed"):
        artifacts.publish_manifest_set(staged, root, ["backend/uv.lock"], expected=result["files"])
    assert (root / "backend/uv.lock").read_text() == "later writer"


@pytest.mark.asyncio
async def test_cancellation_waits_for_owned_promotion_before_staging_can_be_disposed(tmp_path, monkeypatch):
    root, staged = tmp_path / "repo", tmp_path / "staged"
    original = source(root)
    write(staged, "backend/uv.lock", "candidate")
    entered, release = threading.Event(), threading.Event()
    replace = artifacts._replace
    def held(src, dest, **kwargs):
        if dest == root / "backend/uv.lock":
            entered.set()
            assert release.wait(5)
        replace(src, dest, **kwargs)
    monkeypatch.setattr(artifacts, "_replace", held)
    task = asyncio.create_task(artifacts.publish_manifest_set_async(staged, root, ["backend/uv.lock"],
                                                                expected={"backend/uv.lock": original["backend/uv.lock"]}))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    assert markers(root)[0]["state"] == "completed"
    assert (root / "backend/uv.lock").read_text() == "candidate"


@pytest.mark.asyncio
async def test_candidate_is_retained_but_promotion_requires_current_execution_owner(tmp_path):
    root, staged = tmp_path / "repo", tmp_path / "staged"
    original = source(root)
    write(staged, "backend/uv.lock", "candidate")
    async def refuse():
        candidates = list((root / "data/backend/release-candidates").iterdir())
        assert len(candidates) == 1 and (candidates[0] / "release.json").exists()
        assert (candidates[0] / "source/backend/uv.lock").read_text() == "candidate"
        raise artifacts.ReleaseArtifactError("Executor ownership changed")
    with pytest.raises(artifacts.ReleaseArtifactError, match="ownership"):
        await artifacts.prepare_and_publish(staged, root, ["backend/uv.lock"], expected_source=original,
            release_context={"schema_revisions": ["r1"], "image_identity": "sha256:synthetic"},
            checks=["synthetic locked check"], before_publish=refuse)
    assert artifacts.sha256(root / "backend/uv.lock") == original["backend/uv.lock"]
