import sys
from importlib import metadata as importlib_metadata
from pathlib import Path

import pytest

import app.modules.unifi_protect.package as package_module
import app.services.unifi_protect_updates as updates_module
from app.services.unifi_protect_updates import UnifiProtectUpdateError, UnifiProtectUpdateService


def test_activate_overlay_removes_stale_overlay_paths_and_moves_active_first(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "unifi-protect-package"
    versions_dir = package_root / "versions"
    active_path = versions_dir / "10.4.1"
    stale_path = versions_dir / "10.5.1"
    active_path.mkdir(parents=True)
    stale_path.mkdir()
    active_marker = package_root / "active.json"
    active_marker.write_text(
        f'{{"mode": "overlay", "version": "10.4.1", "path": "{active_path}"}}'
    )

    monkeypatch.setattr(package_module, "PACKAGE_ROOT", package_root)
    monkeypatch.setattr(package_module, "VERSIONS_DIR", versions_dir)
    monkeypatch.setattr(package_module, "ACTIVE_MARKER", active_marker)

    original_path = list(sys.path)
    keep_path = str(tmp_path / "keep")
    sys.path[:] = [str(stale_path), keep_path, str(active_path), *original_path]
    try:
        state = package_module.activate_unifi_protect_package_overlay()

        assert state.version == "10.4.1"
        assert sys.path[0] == str(active_path)
        assert str(stale_path) not in sys.path
        assert keep_path in sys.path
    finally:
        sys.path[:] = original_path


@pytest.mark.asyncio
async def test_install_overlay_adds_missing_direct_package_dependencies(tmp_path, monkeypatch) -> None:
    captured: list[tuple[str, ...]] = []

    class FakeProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def fake_create_subprocess_exec(*cmd: str, **_: object) -> FakeProcess:
        captured.append(cmd)
        staging = Path(cmd[cmd.index("--target") + 1])
        staging.mkdir(parents=True, exist_ok=True)
        if cmd[-1] == "uiprotect==10.5.1":
            dist_info = staging / "uiprotect-10.5.1.dist-info"
            dist_info.mkdir()
            (dist_info / "METADATA").write_text(
                "Metadata-Version: 2.4\n"
                "Name: uiprotect\n"
                "Version: 10.5.1\n"
                "Requires-Dist: aiozoneinfo>=0.2.3\n"
            )
        return FakeProcess()

    def fake_installed_distribution_version(package_name: str) -> str:
        if package_name == "aiozoneinfo":
            raise importlib_metadata.PackageNotFoundError(package_name)
        return "999"

    monkeypatch.setattr(updates_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(
        updates_module,
        "_installed_distribution_version",
        fake_installed_distribution_version,
    )

    target_path = tmp_path / "10.5.1"
    await UnifiProtectUpdateService()._install_overlay("10.5.1", target_path)

    assert target_path.exists()
    assert len(captured) == 2
    assert "--no-deps" in captured[0]
    assert captured[0][-1] == "uiprotect==10.5.1"
    assert any(item.startswith("aiozoneinfo") for item in captured[1])


@pytest.mark.asyncio
async def test_install_overlay_keeps_existing_target_when_staging_fails(tmp_path, monkeypatch) -> None:
    class FakeProcess:
        returncode = 1

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b"install failed"

    async def fake_create_subprocess_exec(*_: str, **__: object) -> FakeProcess:
        return FakeProcess()

    monkeypatch.setattr(updates_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    target_path = tmp_path / "10.5.1"
    target_path.mkdir()
    (target_path / "existing").write_text("still active")

    with pytest.raises(UnifiProtectUpdateError):
        await UnifiProtectUpdateService()._install_overlay("10.5.1", target_path)

    assert (target_path / "existing").read_text() == "still active"
