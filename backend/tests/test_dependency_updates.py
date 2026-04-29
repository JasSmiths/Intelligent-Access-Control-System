import json
import shutil
import uuid

import pytest

from app.services.dependency_updates import (
    DependencyUpdateError,
    DependencyUpdateService,
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
from app.models import DependencyUpdateBackup, ExternalDependency


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


def test_generated_compose_override_records_host_mounted_remote_storage(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IACS_WORKSPACE_DIR", str(tmp_path))
    service = DependencyUpdateService()

    service._write_generated_compose_override("nfs", "nas.local:/volume/iacs", "addr=nas.local,rw")
    nfs = (tmp_path / "docker-compose.update-backups.generated.yml").read_text()
    assert "mode: nfs" in nfs
    assert "host_path: nas.local:/volume/iacs" in nfs
    assert "addr=nas.local,rw" in nfs
    assert "volumes:" not in nfs

    service._write_generated_compose_override("samba", "//nas/iacs", "username=iacs,vers=3.0,rw")
    samba = (tmp_path / "docker-compose.update-backups.generated.yml").read_text()
    assert "mode: samba" in samba
    assert "host_path: //nas/iacs" in samba
    assert "username=iacs,vers=3.0,rw" in samba
    assert "volumes:" not in samba


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
                    "@tiptap/extension-mention": "^3.22.4",
                    "@tiptap/react": "^3.22.4",
                    "@tiptap/starter-kit": "^3.22.4",
                    "react": "^18.3.1",
                },
                "devDependencies": {"@types/react": "^18.3.18"},
            }
        )
    )
    dependency = ExternalDependency(
        ecosystem="npm",
        package_name="@tiptap/extension-mention",
        normalized_name="@tiptap/extension-mention",
        current_version="3.22.4",
        dependant_area="Notification Template Editor",
        manifest_path="frontend/package.json",
        manifest_section="dependencies",
        is_direct=True,
    )

    group = _npm_peer_update_group(frontend, dependency, "3.22.5", "npm ERR! ERESOLVE unable to resolve dependency tree")

    assert group == [
        "@tiptap/extension-mention@3.22.5",
        "@tiptap/react@3.22.5",
        "@tiptap/starter-kit@3.22.5",
    ]


def test_npm_recovery_plan_pairs_react_and_react_dom(tmp_path) -> None:
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

    plan = _npm_recovery_plan(
        frontend,
        dependency,
        "19.2.5",
        'npm ERR! ERESOLVE unable to resolve dependency tree\nnpm ERR! peer react@"^18.3.1" from react-dom@18.3.1',
    )

    assert plan
    assert plan.strategy == "npm_peer_group"
    assert plan.specs == ["react@19.2.5", "react-dom@19.2.5"]
    assert plan.use_legacy_peer_deps is False


def test_npm_recovery_plan_repairs_direct_peer_package_to_latest(tmp_path, monkeypatch) -> None:
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
    monkeypatch.setattr("app.services.dependency_updates._npm_latest_version", lambda name, cwd: "6.0.1" if name == "@vitejs/plugin-react" else "8.0.10")
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

    plan = _npm_recovery_plan(
        frontend,
        dependency,
        "6.0.3",
        'npm ERR! Found: vite@8.0.10\nnpm ERR! peer vite@"^4.2.0 || ^5.0.0 || ^6.0.0 || ^7.0.0" from @vitejs/plugin-react@4.7.0',
    )

    assert plan
    assert "typescript@6.0.3" in plan.specs
    assert "@vitejs/plugin-react@6.0.1" in plan.specs
    assert plan.use_legacy_peer_deps is False


def test_npm_recovery_plan_keeps_strict_peer_resolution_for_scoped_groups(tmp_path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "@tiptap/extension-mention": "^3.22.4",
                    "@tiptap/pm": "^3.22.4",
                    "@tiptap/react": "^3.22.4",
                    "@tiptap/starter-kit": "^3.22.4",
                    "@tiptap/suggestion": "^3.22.4",
                }
            }
        )
    )
    dependency = ExternalDependency(
        ecosystem="npm",
        package_name="@tiptap/extension-mention",
        normalized_name="@tiptap/extension-mention",
        current_version="3.22.4",
        dependant_area="Notification Template Editor",
        manifest_path="frontend/package.json",
        manifest_section="dependencies",
        is_direct=True,
    )

    plan = _npm_recovery_plan(
        frontend,
        dependency,
        "3.22.5",
        "npm ERR! ERESOLVE unable to resolve dependency tree",
    )

    assert plan
    assert plan.use_legacy_peer_deps is False
    assert set(plan.specs) == {
        "@tiptap/extension-mention@3.22.5",
        "@tiptap/pm@3.22.5",
        "@tiptap/react@3.22.5",
        "@tiptap/starter-kit@3.22.5",
        "@tiptap/suggestion@3.22.5",
    }


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


def test_zstd_backup_archive_round_trip(tmp_path) -> None:
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

    _create_zstd_archive(source, archive)
    _extract_archive(archive, destination)

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
    _create_zstd_archive(source, archive)

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
    _create_zstd_archive(source, archive)
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
