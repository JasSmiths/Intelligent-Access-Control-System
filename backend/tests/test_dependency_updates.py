import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tarfile
import threading
import shutil
import stat
import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import app.services.dependency_updates as dependency_updates_module
from app.models import DependencyUpdateBackup, ExternalDependency, User
from app.services.dependency_updates import (
    DependencyCommandError,
    DependencyUpdateError,
    DependencyUpdateService,
    GENERATED_COMPOSE,
    _create_zstd_archive,
    _docker_images_from_text,
    _extract_archive,
    _npm_peer_update_group,
    _npm_recovery_plan,
    _repair_tsconfig_for_typescript_6,
    _repair_vite_type_declarations,
    _sha256_file,
    _update_docker_image_tag,
    _update_python_requirement,
    _workspace_root,
)


class _FakeJobLog:
    async def info(self, message: str) -> None:
        pass

    async def warning(self, message: str) -> None:
        pass

    async def stdout(self, message: str) -> None:
        pass

    async def error(self, message: str) -> None:
        pass


def _storage_runtime(**overrides):
    values = {
        "dependency_update_backup_storage_mode": "samba",
        "dependency_update_backup_mount_source": "//nas/iacs",
        "dependency_update_backup_mount_options": "username=iacs,password=secret,vers=3.0,rw",
        "dependency_update_backup_config_status": "active",
        "dependency_update_backup_min_free_bytes": 1,
        "dependency_update_backup_retention_days": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_dependency_manifest_discovery_enrolls_backend_frontend_and_images(tmp_path, monkeypatch) -> None:
    root = tmp_path
    monkeypatch.setenv("IACS_WORKSPACE_DIR", str(root))
    (root / "backend").mkdir()
    (root / "frontend").mkdir()
    (root / "backend" / "pyproject.toml").write_text(
        """
[project]
dependencies = [
  "fastapi>=0.115.0",
  "uiprotect==10.3.1",
]

[project.optional-dependencies]
dev = ["pytest>=8.3.0"]
"""
    )
    (root / "frontend" / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"react": "^18.3.1"},
                "devDependencies": {"vite": "^6.4.2"},
            }
        )
    )
    (root / "frontend" / "package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "": {"dependencies": {"react": "^18.3.1"}},
                    "node_modules/react": {"version": "18.3.1"},
                    "node_modules/scheduler": {"version": "0.23.2"},
                }
            }
        )
    )
    (root / "backend" / "Dockerfile").write_text("FROM python:3.12-slim AS runtime\n")
    (root / "docker-compose.yml").write_text("services:\n  redis:\n    image: redis:7-alpine\n")

    rows = DependencyUpdateService()._discover_dependencies()
    identities = {(row.ecosystem, row.package_name, row.is_direct) for row in rows}

    assert ("python", "fastapi", True) in identities
    assert ("python", "uiprotect", True) in identities
    assert ("npm", "react", True) in identities
    assert ("npm", "scheduler", False) in identities
    assert ("docker_image", "python", True) in identities
    assert ("docker_image", "redis", True) in identities


def test_discord_dependency_enrolls_with_discord_messaging_area(tmp_path, monkeypatch) -> None:
    root = tmp_path
    monkeypatch.setenv("IACS_WORKSPACE_DIR", str(root))
    (root / "backend").mkdir()
    (root / "frontend").mkdir()
    (root / "backend" / "pyproject.toml").write_text(
        """
[project]
dependencies = [
  "discord.py>=2.4.0",
]
"""
    )
    (root / "frontend" / "package.json").write_text(json.dumps({"dependencies": {}}))

    rows = DependencyUpdateService()._discover_dependencies()
    discord_row = next(row for row in rows if row.package_name == "discord.py")

    assert discord_row.ecosystem == "python"
    assert discord_row.is_direct is True
    assert discord_row.dependant_area == "Discord Messaging"


@pytest.mark.asyncio
async def test_dependency_storage_status_redacts_saved_mount_options(tmp_path, monkeypatch) -> None:
    secret = "username=iacs,password=secret,vers=3.0,rw"

    async def fake_runtime():
        return _storage_runtime(dependency_update_backup_mount_options=secret)

    monkeypatch.setattr(dependency_updates_module, "get_runtime_config", fake_runtime)
    monkeypatch.setattr(dependency_updates_module, "_backup_root", lambda: tmp_path / "backups")

    status = await DependencyUpdateService().storage_status()

    assert status["mount_options"] == ""
    assert status["mount_options_configured"] is True
    assert status["mount_options_redacted"] is True
    assert "password=secret" not in json.dumps(status)


@pytest.mark.asyncio
async def test_dependency_storage_config_preserves_omitted_secret_and_redacts_outputs(tmp_path, monkeypatch) -> None:
    secret = "username=iacs,password=secret,vers=3.0,rw"
    updates: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    async def fake_runtime():
        return _storage_runtime(dependency_update_backup_mount_options=secret)

    async def fake_update_settings(payload, **_kwargs):
        updates.append(payload)
        return []

    class FakeEventBus:
        async def publish(self, topic, payload):
            events.append({"topic": topic, "payload": payload})

    monkeypatch.setenv("IACS_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(dependency_updates_module, "get_runtime_config", fake_runtime)
    monkeypatch.setattr(dependency_updates_module, "update_settings", fake_update_settings)
    monkeypatch.setattr(dependency_updates_module, "emit_audit_log", lambda **kwargs: audits.append(kwargs))
    monkeypatch.setattr(dependency_updates_module, "event_bus", FakeEventBus())
    monkeypatch.setattr(dependency_updates_module, "_backup_root", lambda: tmp_path / "backups")

    result = await DependencyUpdateService().save_storage_config(
        {"mode": "samba", "mount_source": "//nas/iacs-2"},
        user=User(id=uuid.uuid4(), username="admin", full_name="Admin"),
    )

    generated = (tmp_path / GENERATED_COMPOSE).read_text()
    assert "password=secret" in generated
    assert "dependency_update_backup_mount_options" not in updates[0]
    assert result["mount_options"] == ""
    assert result["mount_options_configured"] is True
    assert result["mount_options_redacted"] is True
    assert "password=secret" not in json.dumps(result)
    assert "password=secret" not in json.dumps(audits, default=str)
    assert "password=secret" not in json.dumps(events, default=str)
    assert audits[0]["metadata"]["mount_options_changed"] is False


@pytest.mark.asyncio
async def test_dependency_storage_config_explicit_empty_clears_secret(tmp_path, monkeypatch) -> None:
    updates: list[dict[str, object]] = []

    async def fake_runtime():
        return _storage_runtime()

    async def fake_update_settings(payload, **_kwargs):
        updates.append(payload)
        return []

    class FakeEventBus:
        async def publish(self, topic, payload):
            return None

    monkeypatch.setenv("IACS_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(dependency_updates_module, "get_runtime_config", fake_runtime)
    monkeypatch.setattr(dependency_updates_module, "update_settings", fake_update_settings)
    monkeypatch.setattr(dependency_updates_module, "emit_audit_log", lambda **kwargs: None)
    monkeypatch.setattr(dependency_updates_module, "event_bus", FakeEventBus())
    monkeypatch.setattr(dependency_updates_module, "_backup_root", lambda: tmp_path / "backups")

    result = await DependencyUpdateService().save_storage_config(
        {"mode": "samba", "mount_source": "//nas/iacs", "mount_options": ""},
        user=User(id=uuid.uuid4(), username="admin", full_name="Admin"),
    )

    assert updates[0]["dependency_update_backup_mount_options"] == ""
    assert "password=secret" not in (tmp_path / GENERATED_COMPOSE).read_text()
    assert result["mount_options_configured"] is False
    assert result["mount_options_redacted"] is False


def test_generated_compose_override_records_host_mounted_remote_storage(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IACS_WORKSPACE_DIR", str(tmp_path))
    service = DependencyUpdateService()

    service._write_generated_compose_override("nfs", "nas.local:/volume/iacs", "addr=nas.local,rw")
    generated_path = tmp_path / GENERATED_COMPOSE
    nfs = generated_path.read_text()
    assert "mode: nfs" in nfs
    assert 'host_path: "nas.local:/volume/iacs"' in nfs
    assert 'mount_options: "addr=nas.local,rw"' in nfs
    assert "volumes:" not in nfs
    assert stat.S_IMODE(generated_path.stat().st_mode) == 0o600

    service._write_generated_compose_override("samba", "//nas/iacs", "username=iacs,vers=3.0,rw")
    samba = generated_path.read_text()
    assert "mode: samba" in samba
    assert 'host_path: "//nas/iacs"' in samba
    assert 'mount_options: "username=iacs,vers=3.0,rw"' in samba
    assert "volumes:" not in samba
    assert stat.S_IMODE(generated_path.stat().st_mode) == 0o600


def test_generated_compose_override_quotes_storage_scalars(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IACS_WORKSPACE_DIR", str(tmp_path))
    service = DependencyUpdateService()

    service._write_generated_compose_override(
        "samba",
        "//nas/iacs\nvolumes:\n  injected: {}",
        "username=iacs,password=secret\nx-bad: true",
    )
    generated = (tmp_path / GENERATED_COMPOSE).read_text()

    assert "\n  injected: {}" not in generated
    assert "\nx-bad: true" not in generated
    assert "\\nvolumes:" in generated
    assert "\\nx-bad:" in generated


def test_manifest_snapshot_excludes_generated_compose_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IACS_WORKSPACE_DIR", str(tmp_path))
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    (tmp_path / GENERATED_COMPOSE).write_text("mount_options: username=iacs,password=secret\n")

    snapshot = DependencyUpdateService()._write_manifest_snapshot(tmp_path / "staging")

    assert GENERATED_COMPOSE not in {row["path"] for row in snapshot["files"]}
    assert not (tmp_path / "staging" / "manifests" / GENERATED_COMPOSE).exists()


def test_docker_image_parser_handles_from_and_compose_images() -> None:
    dockerfile_rows = _docker_images_from_text("FROM python:3.12-slim AS runtime\n", "Dockerfile")
    compose_rows = _docker_images_from_text("services:\n  db:\n    image: postgres:16-alpine\n", "docker-compose.yml")

    assert dockerfile_rows == [{"raw": "python:3.12-slim", "name": "python", "tag": "3.12-slim", "source": "FROM"}]
    assert compose_rows == [{"raw": "postgres:16-alpine", "name": "postgres", "tag": "16-alpine", "source": "image"}]


def test_workspace_root_prefers_configured_existing_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IACS_WORKSPACE_DIR", str(tmp_path))
    assert _workspace_root() == tmp_path


def test_update_python_requirement_rewrites_matching_dependency(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IACS_WORKSPACE_DIR", str(tmp_path))
    (tmp_path / "backend").mkdir()
    manifest = tmp_path / "backend" / "pyproject.toml"
    manifest.write_text('[project]\ndependencies = ["fastapi>=0.115.0", "sqlalchemy[asyncio]>=2.0.36"]\n')
    dependency = ExternalDependency(
        ecosystem="python",
        package_name="sqlalchemy",
        normalized_name="sqlalchemy",
        current_version="2.0.36",
        dependant_area="Database",
        manifest_path="backend/pyproject.toml",
        manifest_section="project.dependencies",
    )

    _update_python_requirement(dependency, "2.0.49")

    assert '"sqlalchemy[asyncio]==2.0.49"' in manifest.read_text()


def test_update_docker_image_tag_rewrites_single_reference(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IACS_WORKSPACE_DIR", str(tmp_path))
    manifest = tmp_path / "docker-compose.yml"
    manifest.write_text("services:\n  redis:\n    image: redis:7-alpine\n")
    dependency = ExternalDependency(
        ecosystem="docker_image",
        package_name="redis",
        normalized_name="redis",
        current_version="7-alpine",
        dependant_area="Realtime Infrastructure",
        manifest_path="docker-compose.yml",
        manifest_section="image",
        metadata_={"raw": "redis:7-alpine", "name": "redis"},
    )

    _update_docker_image_tag(dependency, "7.4-alpine")

    assert "image: redis:7.4-alpine" in manifest.read_text()


def test_npm_peer_update_group_retries_scoped_direct_dependencies(tmp_path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "@acme/editor-mention": "^3.22.4",
                    "@acme/editor-react": "^3.22.4",
                    "@acme/editor-kit": "^3.22.4",
                    "react": "^18.3.1",
                },
                "devDependencies": {"@types/react": "^18.3.18"},
            }
        )
    )
    dependency = ExternalDependency(
        ecosystem="npm",
        package_name="@acme/editor-mention",
        normalized_name="@acme/editor-mention",
        current_version="3.22.4",
        dependant_area="Notification Template Editor",
        manifest_path="frontend/package.json",
        manifest_section="dependencies",
        is_direct=True,
    )

    group = _npm_peer_update_group(frontend, dependency, "3.22.5", "npm ERR! ERESOLVE unable to resolve dependency tree")

    assert group == [
        "@acme/editor-mention@3.22.5",
        "@acme/editor-react@3.22.5",
        "@acme/editor-kit@3.22.5",
    ]


async def test_npm_recovery_plan_pairs_react_and_react_dom(tmp_path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "react": "^18.3.1",
                    "react-dom": "^18.3.1",
                }
            }
        )
    )
    dependency = ExternalDependency(
        ecosystem="npm",
        package_name="react",
        normalized_name="react",
        current_version="18.3.1",
        dependant_area="Frontend Runtime",
        manifest_path="frontend/package.json",
        manifest_section="dependencies",
        is_direct=True,
    )

    plan = await _npm_recovery_plan(
        frontend,
        dependency,
        "19.2.5",
        'npm ERR! ERESOLVE unable to resolve dependency tree\nnpm ERR! peer react@"^18.3.1" from react-dom@18.3.1',
    )

    assert plan
    assert plan.strategy == "npm_peer_group"
    assert plan.specs == ["react@19.2.5", "react-dom@19.2.5"]
    assert plan.regenerate_lockfile is True


async def test_npm_recovery_plan_repairs_direct_peer_package_to_latest(tmp_path, monkeypatch) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text(
        json.dumps(
            {
                "devDependencies": {
                    "@vitejs/plugin-react": "^4.7.0",
                    "typescript": "^5.7.2",
                    "vite": "^8.0.10",
                }
            }
        )
    )
    async def latest(name, cwd):
        return "6.0.1" if name == "@vitejs/plugin-react" else "8.0.10"
    monkeypatch.setattr(dependency_updates_module, "_npm_latest_version", latest)
    dependency = ExternalDependency(
        ecosystem="npm",
        package_name="typescript",
        normalized_name="typescript",
        current_version="5.7.2",
        dependant_area="Frontend Tooling",
        manifest_path="frontend/package.json",
        manifest_section="devDependencies",
        is_direct=True,
    )

    plan = await _npm_recovery_plan(
        frontend,
        dependency,
        "6.0.3",
        'npm ERR! Found: vite@8.0.10\nnpm ERR! peer vite@"^4.2.0 || ^5.0.0 || ^6.0.0 || ^7.0.0" from @vitejs/plugin-react@4.7.0',
    )

    assert plan
    assert "typescript@6.0.3" in plan.specs
    assert "@vitejs/plugin-react@6.0.1" in plan.specs
    assert plan.regenerate_lockfile is False


async def test_npm_recovery_plan_keeps_strict_peer_resolution_for_scoped_groups(tmp_path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "@acme/editor-mention": "^3.22.4",
                    "@acme/editor-model": "^3.22.4",
                    "@acme/editor-react": "^3.22.4",
                    "@acme/editor-kit": "^3.22.4",
                    "@acme/editor-suggestion": "^3.22.4",
                }
            }
        )
    )
    dependency = ExternalDependency(
        ecosystem="npm",
        package_name="@acme/editor-mention",
        normalized_name="@acme/editor-mention",
        current_version="3.22.4",
        dependant_area="Notification Template Editor",
        manifest_path="frontend/package.json",
        manifest_section="dependencies",
        is_direct=True,
    )

    plan = await _npm_recovery_plan(
        frontend,
        dependency,
        "3.22.5",
        "npm ERR! ERESOLVE unable to resolve dependency tree",
    )

    assert plan
    assert plan.regenerate_lockfile is True
    assert set(plan.specs) == {
        "@acme/editor-mention@3.22.5",
        "@acme/editor-model@3.22.5",
        "@acme/editor-react@3.22.5",
        "@acme/editor-kit@3.22.5",
        "@acme/editor-suggestion@3.22.5",
    }


@pytest.mark.asyncio
async def test_npm_clean_lock_recovery_updates_only_staged_frontend(tmp_path, monkeypatch) -> None:
    live = tmp_path / "live"
    staged = tmp_path / "staged"
    live.mkdir()
    staged.mkdir()
    package_json = {
        "dependencies": {
            "@acme/editor-mention": "^3.22.5",
            "@acme/editor-model": "^3.22.5",
            "@acme/editor-react": "^3.22.5",
            "@acme/editor-kit": "^3.22.5",
            "@acme/editor-suggestion": "^3.22.5",
        }
    }
    (live / "package.json").write_text(json.dumps(package_json))
    (staged / "package.json").write_text(json.dumps(package_json))
    (staged / "package-lock.json").write_text(
        json.dumps({"packages": {"node_modules/@acme/editor-core": {"version": "3.22.5"}}})
    )
    dependency = ExternalDependency(
        ecosystem="npm",
        package_name="@acme/editor-suggestion",
        normalized_name="@acme/editor-suggestion",
        current_version="3.22.5",
        dependant_area="Notification Template Editor",
        manifest_path="frontend/package.json",
        manifest_section="dependencies",
        is_direct=True,
    )
    service = DependencyUpdateService()
    commands: list[list[str]] = []

    async def fake_run_command(command, *, cwd, log, timeout):
        commands.append(command)
        assert cwd == staged
        if command == ["npm", "install", "--package-lock-only", "--no-audit"]:
            (staged / "package-lock.json").write_text(json.dumps({"packages": {}}))
        return ""

    monkeypatch.setattr(service, "_run_command", fake_run_command)

    recovered = await service._attempt_npm_recovery(
        staged,
        dependency,
        "3.23.1",
        DependencyCommandError(
            ["npm", "install", "@acme/editor-suggestion@3.23.1", "--package-lock-only"],
            1,
            "npm ERR! ERESOLVE could not resolve\nnpm ERR! peer @acme/editor-core@3.23.1",
        ),
        cast(Any, _FakeJobLog()),
    )

    assert recovered is True
    assert commands[0] == ["npm", "install", "--package-lock-only", "--no-audit"]
    assert all("--legacy-peer-deps" not in command for command in commands)
    assert "@acme/editor-suggestion@3.23.1" not in commands[0]
    assert json.loads(live.joinpath("package.json").read_text()) == package_json
    staged_dependencies = json.loads(staged.joinpath("package.json").read_text())["dependencies"]
    assert set(staged_dependencies.values()) == {"^3.23.1"}


def test_repair_tsconfig_for_typescript_6_migrates_node_resolution(tmp_path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    tsconfig = frontend / "tsconfig.json"
    tsconfig.write_text(
        json.dumps(
            {
                "compilerOptions": {
                    "module": "ESNext",
                    "moduleResolution": "Node",
                    "jsx": "react-jsx",
                }
            }
        )
    )

    changed = _repair_tsconfig_for_typescript_6(
        frontend,
        'tsconfig.json(13,25): error TS5107: Option "moduleResolution=node10" is deprecated',
    )

    assert changed is True
    assert json.loads(tsconfig.read_text())["compilerOptions"]["moduleResolution"] == "Bundler"


def test_repair_vite_type_declarations_adds_vite_env(tmp_path) -> None:
    frontend = tmp_path / "frontend"
    (frontend / "src").mkdir(parents=True)

    changed = _repair_vite_type_declarations(
        frontend,
        "src/main.tsx(78,8): error TS2882: Cannot find module or type declarations for side-effect import of './styles.css'.",
    )

    assert changed is True
    assert (frontend / "src" / "vite-env.d.ts").read_text() == '/// <reference types="vite/client" />\n'


async def test_zstd_backup_archive_round_trip(tmp_path) -> None:
    if not shutil.which("zstd"):
        pytest.skip("zstd binary is not installed")
    source = tmp_path / "source"
    source.mkdir()
    (source / "backup.json").write_text('{"ok": true}')
    (source / "manifests").mkdir()
    (source / "manifests" / "pyproject.toml").write_text("[project]\n")
    archive = tmp_path / "backup.tar.zst"
    destination = tmp_path / "restore"
    destination.mkdir()

    await _create_zstd_archive(source, archive)
    await _extract_archive(archive, destination)

    assert archive.exists()
    assert (destination / "backup.json").read_text() == '{"ok": true}'
    assert (destination / "manifests" / "pyproject.toml").read_text() == "[project]\n"


async def test_backup_archive_validation_checks_manifests_and_artifacts(tmp_path) -> None:
    if not shutil.which("zstd"):
        pytest.skip("zstd binary is not installed")
    backup_id = uuid.uuid4()
    source = tmp_path / "source"
    manifest = source / "manifests" / "backend" / "pyproject.toml"
    artifact = source / "artifacts" / "packaging-24.0-py3-none-any.whl"
    manifest.parent.mkdir(parents=True)
    artifact.parent.mkdir(parents=True)
    manifest.write_text("[project]\n")
    artifact.write_text("offline wheel bytes")
    (source / "settings.json").write_text("[]")
    (source / "backup.json").write_text(json.dumps({"schema_version": 1, "backup_id": str(backup_id)}))
    archive = tmp_path / "backup.tar.zst"
    await _create_zstd_archive(source, archive)

    backup = DependencyUpdateBackup(
        id=backup_id,
        package_name="packaging",
        ecosystem="python",
        version="24.0",
        reason="test",
        archive_path=str(archive),
        storage_root=str(tmp_path),
        checksum_sha256=_sha256_file(archive),
        size_bytes=archive.stat().st_size,
        manifest_snapshot={"files": [{"path": "backend/pyproject.toml", "sha256": _sha256_file(manifest)}]},
        config_snapshot={"settings_count": 0},
        metadata_={"archive_format": "tar.zst"},
    )

    result = await DependencyUpdateService()._validate_backup_archive(backup)

    assert result["manifest_count"] == 1
    assert result["artifact_count"] == 1
    assert result["settings_count"] == 0


async def test_backup_archive_validation_rejects_missing_manifest_snapshot(tmp_path) -> None:
    if not shutil.which("zstd"):
        pytest.skip("zstd binary is not installed")
    backup_id = uuid.uuid4()
    source = tmp_path / "source"
    source.mkdir()
    (source / "backup.json").write_text(json.dumps({"schema_version": 1, "backup_id": str(backup_id)}))
    (source / "settings.json").write_text("[]")
    archive = tmp_path / "backup.tar.zst"
    await _create_zstd_archive(source, archive)
    backup = DependencyUpdateBackup(
        id=backup_id,
        package_name="packaging",
        ecosystem="python",
        version="24.0",
        reason="test",
        archive_path=str(archive),
        storage_root=str(tmp_path),
        checksum_sha256=_sha256_file(archive),
        size_bytes=archive.stat().st_size,
        manifest_snapshot={"files": []},
        config_snapshot={"settings_count": 0},
        metadata_={"archive_format": "tar.zst"},
    )

    with pytest.raises(DependencyUpdateError, match="manifest snapshots"):
        await DependencyUpdateService()._validate_backup_archive(backup)


def _release_source(root):
    from app.services import release_artifacts
    files = {
        "backend/app/example.py": "pass\n",
        "backend/pyproject.toml": '[project]\nname="synthetic"\nversion="1"\n',
        "backend/uv.lock": "synthetic original lock\n",
        "frontend/package.json": '{"devDependencies":{"typescript":"1"}}',
        "frontend/package-lock.json": '{"packages":{}}',
        "frontend/tsconfig.json": '{"compilerOptions":{}}',
        "frontend/src/main.ts": "export const original = true;\n",
    }
    for name, value in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    return {name: release_artifacts.sha256(root / name) for name in release_artifacts.source_files(root)}


async def test_frontend_candidate_creates_fresh_context_and_publishes_the_checked_bytes(tmp_path, monkeypatch):
    from app.services import release_artifacts
    root = tmp_path / "repo"
    _release_source(root)
    service = DependencyUpdateService()
    dependency = ExternalDependency(ecosystem="npm", package_name="typescript", is_direct=True)
    commands = []
    checked = {}

    async def command(args, *, cwd, log, timeout):
        assert cwd != root / "frontend"
        commands.append(args)
        assert (cwd / "src/main.ts").exists()
        if args[:2] == ["npm", "install"]:
            (cwd / "package.json").write_text('{"devDependencies":{"typescript":"2"}}')
            (cwd / "package-lock.json").write_text('{"packages":{"typescript":{"version":"2"}}}')
        if args == ["npm", "run", "build"]:
            checked.update({name: release_artifacts.sha256(cwd / name) for name in ("package.json", "package-lock.json")})
        return ""

    monkeypatch.setattr(service, "_run_command", command)
    authority = AsyncMock()
    monkeypatch.setattr(service, "_assert_executor", authority)
    job_id = uuid.uuid4()
    receipt = await service._apply_npm_update_transactionally(root / "frontend", dependency, "2", _FakeJobLog(), job_id=job_id)
    assert authority.await_count == 2
    authority.assert_awaited_with(job_id)
    assert ["npm", "ci", "--include=optional", "--no-audit"] in commands
    assert commands[-1] == ["npm", "run", "build"]
    assert receipt["deployment"] == "not_performed"
    assert {name: receipt["files"]["frontend/" + name] for name in checked} == checked
    assert {name: release_artifacts.sha256(root / "frontend" / name) for name in checked} == checked


async def test_frontend_build_cannot_verify_different_unpromoted_source(tmp_path, monkeypatch):
    from app.services import release_artifacts
    root = tmp_path / "repo"
    original = _release_source(root)
    service = DependencyUpdateService()
    dependency = ExternalDependency(ecosystem="npm", package_name="typescript", is_direct=True)

    async def command(args, *, cwd, log, timeout):
        if args == ["npm", "run", "build"]:
            (cwd / "src/main.ts").write_text("export const changed = true;")
        return ""

    monkeypatch.setattr(service, "_run_command", command)
    with pytest.raises(release_artifacts.ReleaseArtifactError, match="outside"):
        await service._apply_npm_update_transactionally(root / "frontend", dependency, "2", _FakeJobLog(), job_id=uuid.uuid4())
    assert {name: release_artifacts.sha256(root / name) for name in original} == original


@pytest.mark.parametrize("change", [None, "foreign_edit", "missing_ownership"])
async def test_backup_restore_removes_only_owned_new_file_with_recorded_absent_preimage(tmp_path, monkeypatch, change):
    from app.services import release_artifacts
    root, backup_root, staged = tmp_path / "repo", tmp_path / "backup", tmp_path / "staged"
    original = _release_source(root)
    identity = release_artifacts.capture_source(root, backup_root / "source", schema_revisions=["r1"], image_identity="synthetic-image")
    (backup_root / "release.json").write_text(json.dumps(identity))
    service = DependencyUpdateService()
    service._write_manifest_snapshot(backup_root, source_root=backup_root / "source")
    archive = tmp_path / "backup.tar"
    dependency_updates_module._write_tar_archive(backup_root, archive)
    name = "frontend/src/vite-env.d.ts"
    (staged / name).parent.mkdir(parents=True)
    (staged / name).write_text('/// <reference types="vite/client" />\n')
    promotion = release_artifacts.publish_manifest_set(staged, root, [name], expected={name: None}, expected_source=original)

    class SchemaSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def scalars(self, statement): return SimpleNamespace(all=lambda: ["r1"])

    monkeypatch.setattr(dependency_updates_module, "AsyncSessionLocal", SchemaSession)
    monkeypatch.setattr(dependency_updates_module, "_workspace_root", lambda: root)
    monkeypatch.setenv("IACS_IMAGE_IDENTITY", "synthetic-image")
    monkeypatch.setattr(service, "_assert_executor", AsyncMock())
    if change == "foreign_edit":
        (root / name).write_text("foreign edit")
    if change == "missing_ownership":
        promotion.pop("before")
    if change:
        with pytest.raises((DependencyUpdateError, release_artifacts.ReleaseArtifactError)):
            await service._restore_backup_manifests(archive, job_id=uuid.uuid4(), promotion=promotion)
        assert (root / name).exists()
        if change == "foreign_edit":
            assert (root / name).read_text() == "foreign edit"
    else:
        restoration = await service._restore_backup_manifests(archive, job_id=uuid.uuid4(), promotion=promotion)
        assert restoration["files"] == {name: None}
        assert not (root / name).exists()
        assert {name: release_artifacts.sha256(root / name) for name in release_artifacts.source_files(root)} == original


async def test_cancelled_archive_validation_drains_file_thread_before_removing_staging(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "proof").write_text("synthetic bytes")
    archive = tmp_path / "backup.tar"
    dependency_updates_module._write_tar_archive(source, archive)
    backup = SimpleNamespace(archive_path=str(archive), checksum_sha256=_sha256_file(archive))
    service = DependencyUpdateService()
    entered, release = threading.Event(), threading.Event()
    staging_paths = []

    def validation(staging, backup, checksum):
        staging_paths.append(staging)
        entered.set()
        assert release.wait(5)
        assert (staging / "proof").read_text() == "synthetic bytes"
        return {"ok": True}

    monkeypatch.setattr(service, "_validate_backup_snapshot", validation)
    task = asyncio.create_task(service._validate_backup_archive(backup))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and staging_paths[0].exists()
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)
    assert task.cancelled() and not staging_paths[0].exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux bounded process owner")
@pytest.mark.parametrize("operation", ["create", "extract"])
@pytest.mark.parametrize("interruption", ["cancel", "timeout"])
async def test_archive_process_is_reaped_before_its_temporary_paths_are_removed(tmp_path, monkeypatch, operation, interruption):
    identity_path = tmp_path / "identity.json"
    fake_zstd = tmp_path / "zstd"
    fake_zstd.write_text(
        f"#!{sys.executable}\n"
        "import json,os,signal,time\nfrom pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"Path({str(identity_path.with_suffix('.pending'))!r}).write_text(json.dumps({{'pid':os.getpid(),'start':Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19],'temporary':os.getcwd()}}))\n"
        f"Path({str(identity_path.with_suffix('.pending'))!r}).replace({str(identity_path)!r})\n"
        "time.sleep(120)\n"
    )
    fake_zstd.chmod(0o700)
    real_which = shutil.which
    monkeypatch.setattr(dependency_updates_module.shutil, "which", lambda name: str(fake_zstd) if name == "zstd" else real_which(name))
    monkeypatch.setattr(dependency_updates_module, "ARCHIVE_PROCESS_TIMEOUT", 1.5 if interruption == "timeout" else 60)
    source = tmp_path / "source"
    source.mkdir()
    (source / "proof").write_text("inert input")
    archive = tmp_path / "backup.tar.zst"
    if operation == "create":
        task = asyncio.create_task(_create_zstd_archive(source, archive))
    else:
        archive.write_text("inert compressed input")
        task = asyncio.create_task(_extract_archive(archive, tmp_path / "restore"))
    identity = None
    try:
        async with asyncio.timeout(5):
            while not identity_path.exists():
                await asyncio.sleep(0.01)
        identity = json.loads(identity_path.read_text())
        assert Path(identity["temporary"]).exists()
        if interruption == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 8)
        else:
            with pytest.raises(dependency_updates_module.DependencyProcessTimeout):
                await asyncio.wait_for(task, 8)
        assert not Path(f"/proc/{identity['pid']}").exists()
        assert not Path(identity["temporary"]).exists()
        if operation == "create":
            assert not archive.exists()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 8)
        # Cleanup only a process whose PID and start identity were recorded by
        # this inert fixture, even when an earlier assertion failed.
        if identity_path.exists():
            identity = json.loads(identity_path.read_text())
            process = Path(f"/proc/{identity['pid']}/stat")
            try:
                if process.read_text().rsplit(")", 1)[1].split()[19] == identity["start"]:
                    os.kill(identity["pid"], signal.SIGKILL)
            except (FileNotFoundError, ProcessLookupError):
                pass


@pytest.mark.parametrize("receipt_kind", [None, "promotion", "restoration"])
def test_failed_job_diagnosis_does_not_claim_no_promotion_or_authorize_retry(receipt_kind):
    receipt = {receipt_kind: {"status": "source_prepared"}} if receipt_kind else {}
    diagnosis = dependency_updates_module._diagnose_dependency_failure(
        "apply", None, None, DependencyCommandError(["npm", "run", "build"], 1, "npm ERR! ERESOLVE peer dependency"),
        job_result=receipt,
    )
    assert diagnosis["source_status"] == ("prepared" if receipt_kind else "unverified")
    assert diagnosis["review_required"] is True
    assert "before promoting" not in diagnosis["safe_state"]
    assert "Deployment was not performed" in diagnosis["safe_state"]
    assert not diagnosis["retry_recommendation"].startswith("Retry")


@pytest.mark.parametrize("fault", ["promotion_commit", "later_verification"])
async def test_postpromotion_failure_retains_file_truth_and_persists_review_without_restoring(tmp_path, monkeypatch, fault):
    from copy import deepcopy
    from app.models import DependencyUpdateJob
    from app.services import release_artifacts
    root = tmp_path / "repo"
    _release_source(root)
    (root / "backend/pyproject.toml").write_text('[project]\nname="synthetic"\nversion="1"\ndependencies=["synthetic==1"]\n')
    monkeypatch.setattr(dependency_updates_module, "_workspace_root", lambda: root)
    dependency = ExternalDependency(id=uuid.uuid4(), ecosystem="python", package_name="synthetic",
        current_version="1", latest_version="2", is_direct=True,
        manifest_path="backend/pyproject.toml", manifest_section="project.dependencies")
    job_id, backup_id = uuid.uuid4(), uuid.uuid4()
    rows = {
        DependencyUpdateJob: SimpleNamespace(backup_id=None, result={}),
        DependencyUpdateBackup: SimpleNamespace(metadata_={}),
    }
    commits = 0

    class MetadataSession:
        async def __aenter__(self):
            self.rows = deepcopy(rows)
            return self
        async def __aexit__(self, *args): pass
        async def get(self, model, row_id, **kwargs): return self.rows[model]
        async def commit(self):
            nonlocal commits
            commits += 1
            if fault == "promotion_commit" and commits == 2:
                raise RuntimeError("synthetic metadata commit failure")
            rows.update(deepcopy(self.rows))

    service = DependencyUpdateService()

    async def backup(*args, **kwargs):
        return {"id": str(backup_id), "metadata": {"release_identity": {"schema_revisions": ["r1"], "image_identity": "synthetic-image"}}}

    async def no_action(*args, **kwargs): pass

    async def command(args, *, cwd, log, timeout):
        if args[:3] == ["uv", "lock", "--upgrade-package"]:
            (cwd / "uv.lock").write_text("synthetic resolved lock for version 2")
        if args[:3] == ["python", "-m", "compileall"]:
            raise RuntimeError("synthetic later verification failure")
        return ""

    monkeypatch.setattr(dependency_updates_module, "AsyncSessionLocal", MetadataSession)
    monkeypatch.setattr(service, "create_backup", backup)
    monkeypatch.setattr(service, "_assert_executor", AsyncMock())
    monkeypatch.setattr(service, "_set_job_phase", no_action)
    monkeypatch.setattr(service, "sync_enrollment", no_action)
    monkeypatch.setattr(service, "_run_command", command)
    with pytest.raises(RuntimeError, match="synthetic"):
        await service._run_apply_job(job_id, dependency, SimpleNamespace(id=uuid.uuid4()), _FakeJobLog(), target_version="2")
    result = rows[DependencyUpdateJob].result
    assert result["promotion"]["status"] == "source_prepared"
    assert rows[DependencyUpdateBackup].metadata_["promotion"] == result["promotion"]
    assert result["rollback"]["attempted"] is False and result["rollback"]["review_required"] is True
    assert (root / "backend/uv.lock").read_text() == "synthetic resolved lock for version 2"
    assert result["promotion"]["files"]["backend/uv.lock"] == release_artifacts.sha256(root / "backend/uv.lock")
    diagnosis = dependency_updates_module._diagnose_dependency_failure("apply", dependency, "2", RuntimeError("later failure"), job_result=result)
    assert diagnosis["source_status"] == "prepared" and diagnosis["review_required"] is True


def _executor_connection(*, scalar=True):
    return SimpleNamespace(closed=False, invalidated=False, scalar=AsyncMock(return_value=scalar),
        commit=AsyncMock(), rollback=AsyncMock(), invalidate=AsyncMock(), close=AsyncMock())


async def test_source_mutation_requires_explicit_executor_without_acquiring_one(monkeypatch):
    service = DependencyUpdateService()
    acquire = AsyncMock(side_effect=AssertionError("Mutation must never acquire replacement ownership"))
    monkeypatch.setattr(service, "_acquire_executor", acquire)
    with pytest.raises(DependencyUpdateError, match="ownership was lost"):
        await service._assert_executor(uuid.uuid4())
    acquire.assert_not_awaited()


@pytest.mark.parametrize("state", ["closed", "invalidated", "missing_lock_or_running_job"])
async def test_lost_executor_is_permanently_refused_without_connection_reuse(state):
    service = DependencyUpdateService()
    connection = _executor_connection(scalar=False)
    job_id = uuid.uuid4()
    executor = dependency_updates_module._DependencyExecutor(connection, 123)
    service._executors[job_id] = executor
    if state in {"closed", "invalidated"}:
        setattr(connection, state, True)
    with pytest.raises(DependencyUpdateError, match="ownership was lost"):
        await service._assert_executor(job_id)
    assert executor.lost
    calls = connection.scalar.await_count
    connection.closed = connection.invalidated = False
    connection.scalar.return_value = True
    with pytest.raises(DependencyUpdateError, match="ownership was lost"):
        await service._assert_executor(job_id)
    assert connection.scalar.await_count == calls


async def test_executor_acquisition_refusal_closes_connection(monkeypatch):
    connection = _executor_connection(scalar=False)
    monkeypatch.setattr(dependency_updates_module, "engine", SimpleNamespace(connect=AsyncMock(return_value=connection)))
    assert await DependencyUpdateService()._acquire_executor() is None
    connection.close.assert_awaited_once()


async def test_cancelled_executor_acquisition_invalidates_uncertain_session_lock(monkeypatch):
    connection = _executor_connection()
    connection.scalar.side_effect = asyncio.CancelledError()
    monkeypatch.setattr(dependency_updates_module, "engine", SimpleNamespace(connect=AsyncMock(return_value=connection)))
    with pytest.raises(asyncio.CancelledError):
        await DependencyUpdateService()._acquire_executor()
    connection.invalidate.assert_awaited_once()
    connection.close.assert_awaited_once()


async def test_executor_cleanup_finishes_before_propagating_cancellation():
    connection = _executor_connection()
    entered, release = asyncio.Event(), asyncio.Event()

    async def unlock(*args, **kwargs):
        entered.set()
        await release.wait()
        return True

    connection.scalar.side_effect = unlock
    executor = dependency_updates_module._DependencyExecutor(connection, 123)
    task = asyncio.create_task(DependencyUpdateService()._release_executor(executor))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        connection.close.assert_awaited_once()
        assert executor.lost
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_frontend_candidate_rechecks_ownership_after_retention_before_publishing(tmp_path, monkeypatch):
    from app.services import release_artifacts
    root = tmp_path / "repo"
    original = _release_source(root)
    service = DependencyUpdateService()
    dependency = ExternalDependency(ecosystem="npm", package_name="typescript", is_direct=True)
    monkeypatch.setattr(service, "_run_command", AsyncMock(return_value=""))
    authority = AsyncMock(side_effect=[None, DependencyUpdateError("synthetic ownership lost")])
    monkeypatch.setattr(service, "_assert_executor", authority)
    with pytest.raises(DependencyUpdateError, match="synthetic ownership lost"):
        await service._apply_npm_update_transactionally(root / "frontend", dependency, "2", _FakeJobLog(), job_id=uuid.uuid4())
    assert authority.await_count == 2
    assert {name: release_artifacts.sha256(root / name) for name in original} == original
    assert len(list((root / "data/backend/release-candidates").glob("*/release.json"))) == 1
    assert not (root / "data/backend/release-transactions").exists()
