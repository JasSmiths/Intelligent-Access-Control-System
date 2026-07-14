import sys
from importlib import metadata as importlib_metadata
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.modules.unifi_protect.package as package_module
import app.services.unifi_protect_updates as updates_module
from app.services.unifi_protect_updates import UnifiProtectUpdateError, UnifiProtectUpdateService


def test_package_install_command_uses_uv_when_venv_pip_is_absent(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(updates_module.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(updates_module.shutil, "which", lambda name: "/usr/local/bin/uv")
    monkeypatch.setattr(updates_module.sys, "executable", "/app/.venv/bin/python")

    cmd = updates_module._package_install_command(
        tmp_path,
        ["uiprotect==15.3.0"],
        no_deps=True,
    )

    assert cmd[:5] == ["/usr/local/bin/uv", "pip", "install", "--python", "/app/.venv/bin/python"]
    assert "--no-cache" in cmd
    assert "--target" in cmd
    assert str(tmp_path) in cmd
    assert "--no-deps" in cmd
    assert cmd[-1] == "uiprotect==15.3.0"


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


def test_stable_versions_between_filters_range_prereleases_and_yanked_versions() -> None:
    releases = {
        "15.3.0": [{}],
        "15.4.0": [{}],
        "15.4.1rc1": [{}],
        "15.4.2": [],
        "15.5.0": [{"yanked": True}],
        "15.6.0": [{}],
        "invalid": [{}],
    }

    versions = updates_module._stable_versions_between(releases, "15.3.0", "15.5.0")

    assert versions == ["15.4.0"]


@pytest.mark.asyncio
async def test_release_notes_between_fetches_every_stable_release_and_bounds_combined_body(monkeypatch) -> None:
    service = UnifiProtectUpdateService()
    fetched: list[str] = []

    async def fake_stable_versions(current: str, target: str) -> list[str]:
        assert (current, target) == ("15.3.0", "15.5.0")
        return ["15.4.0", "15.4.1", "15.5.0"]

    async def fake_release_notes(version: str) -> dict[str, object]:
        fetched.append(version)
        return {
            "source": f"https://example.test/api/{version}",
            "title": f"Release {version}",
            "body": version + ("x" * 50000),
            "published_at": "2026-07-01T00:00:00Z",
            "html_url": f"https://example.test/releases/{version}",
        }

    monkeypatch.setattr(service, "_stable_release_versions", fake_stable_versions)
    monkeypatch.setattr(service, "_release_notes", fake_release_notes)

    notes = await service._release_notes_between("15.3.0", "15.5.0")

    assert fetched == ["15.4.0", "15.4.1", "15.5.0"]
    assert notes["versions"] == ["15.4.0", "15.4.1", "15.5.0"]
    assert notes["version_count"] == 3
    assert len(notes["body"]) <= updates_module.MAX_COMBINED_RELEASE_NOTES_CHARS
    assert all(f"## {version}:" in notes["body"] for version in notes["versions"])
    assert notes["html_url"] == "https://example.test/releases/15.5.0"


@pytest.mark.asyncio
async def test_release_notes_between_fails_closed_when_release_index_is_unavailable(monkeypatch) -> None:
    service = UnifiProtectUpdateService()
    fetched: list[str] = []

    async def failed_release_index(_current: str, _target: str) -> list[str]:
        raise UnifiProtectUpdateError("release index unavailable")

    async def fake_release_notes(version: str) -> dict[str, object]:
        fetched.append(version)
        return {
            "source": f"https://pypi.org/project/uiprotect/{version}/",
            "title": f"uiprotect {version}",
            "body": "Fallback release notes.",
            "html_url": f"https://pypi.org/project/uiprotect/{version}/",
        }

    monkeypatch.setattr(service, "_stable_release_versions", failed_release_index)
    monkeypatch.setattr(service, "_release_notes", fake_release_notes)

    with pytest.raises(UnifiProtectUpdateError, match="release index unavailable"):
        await service._release_notes_between("15.3.0", "15.5.0")

    assert fetched == []


@pytest.mark.asyncio
async def test_release_notes_between_preserves_per_release_source_fallback(monkeypatch) -> None:
    service = UnifiProtectUpdateService()

    async def fake_stable_versions(_current: str, _target: str) -> list[str]:
        return ["15.4.0", "15.5.0"]

    async def partly_failed_release_notes(version: str) -> dict[str, object]:
        if version == "15.4.0":
            raise RuntimeError("GitHub and PyPI release detail unavailable")
        return {
            "source": "https://example.test/api/15.5.0",
            "title": "Release 15.5.0",
            "body": "Available release notes.",
            "html_url": "https://example.test/releases/15.5.0",
        }

    monkeypatch.setattr(service, "_stable_release_versions", fake_stable_versions)
    monkeypatch.setattr(service, "_release_notes", partly_failed_release_notes)

    notes = await service._release_notes_between("15.3.0", "15.5.0")

    assert notes["versions"] == ["15.4.0", "15.5.0"]
    assert "Release notes could not be retrieved" in notes["body"]
    assert notes["releases"][0]["html_url"] == "https://pypi.org/project/uiprotect/15.4.0/"


@pytest.mark.asyncio
async def test_verify_exercises_read_only_event_media_track_and_websocket_paths(monkeypatch) -> None:
    camera = SimpleNamespace(id="camera-1")
    event = SimpleNamespace(
        id="event-1",
        thumbnail_id="thumbnail-1",
        smart_detect_types=[SimpleNamespace(value="licensePlate")],
    )
    api_calls: list[str] = []
    event_queries: list[dict[str, object]] = []
    closed: list[object] = []

    class FakeApi:
        async def api_request_obj(self, path: str) -> dict[str, object]:
            api_calls.append(path)
            return {"payload": []}

    api = FakeApi()

    async def fake_runtime_config() -> object:
        return object()

    async def fake_build_client(_runtime: object) -> FakeApi:
        return api

    async def fake_noop(_api: object) -> None:
        return None

    async def fake_close(client: object) -> None:
        closed.append(client)

    async def fake_snapshot(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(content=b"snapshot")

    async def fake_events(*_args: object, **kwargs: object) -> list[SimpleNamespace]:
        event_queries.append(kwargs)
        return [event]

    async def fake_event(_api: object, event_id: str) -> SimpleNamespace:
        assert event_id == "event-1"
        return event

    async def fake_thumbnail(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(content=b"thumbnail")

    async def fake_websockets(_api: object) -> dict[str, object]:
        return {"status": "passed", "channels": {}}

    monkeypatch.setattr(updates_module, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(updates_module, "build_unifi_protect_client", fake_build_client)
    monkeypatch.setattr(updates_module, "load_unifi_protect_bootstrap", fake_noop)
    monkeypatch.setattr(updates_module, "close_unifi_protect_client", fake_close)
    monkeypatch.setattr(updates_module, "list_bootstrap_cameras", lambda _api: [camera])
    monkeypatch.setattr(updates_module, "get_unifi_protect_snapshot", fake_snapshot)
    monkeypatch.setattr(updates_module, "list_unifi_protect_events", fake_events)
    monkeypatch.setattr(updates_module, "get_event_by_id", fake_event)
    monkeypatch.setattr(updates_module, "get_unifi_protect_event_thumbnail", fake_thumbnail)
    monkeypatch.setattr(updates_module, "_verify_websocket_establishment", fake_websockets)
    monkeypatch.setattr(updates_module, "current_unifi_protect_version", lambda: "15.12.2")
    monkeypatch.setattr(updates_module, "serialize_unifi_camera", lambda value: {"id": value.id})

    verification = await UnifiProtectUpdateService().verify()

    assert verification["camera_count"] == 1
    assert verification["checks"]["event_history"] == {"status": "passed", "event_count": 1}
    assert verification["checks"]["single_event"]["status"] == "passed"
    assert verification["checks"]["event_thumbnail"] == {"status": "passed", "bytes": 9}
    assert verification["checks"]["smart_detect_track"]["status"] == "passed"
    assert verification["checks"]["websockets"]["status"] == "passed"
    assert len(event_queries) == 1
    assert event_queries[0]["event_type"] == "smartDetectZone"
    assert event_queries[0]["limit"] == updates_module.VERIFY_EVENT_LIMIT
    assert event_queries[0]["since"] is not None
    assert api_calls == ["events/event-1/smartDetectTrack"]
    assert closed == [api]


@pytest.mark.asyncio
async def test_verify_skips_event_specific_checks_when_no_recent_event_exists(monkeypatch) -> None:
    api = object()
    camera = SimpleNamespace(id="camera-1")

    async def fake_runtime_config() -> object:
        return object()

    async def fake_build_client(_runtime: object) -> object:
        return api

    async def fake_noop(*_args: object, **_kwargs: object) -> None:
        return None

    async def fake_snapshot(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(content=b"snapshot")

    async def fake_events(*_args: object, **_kwargs: object) -> list[object]:
        return []

    async def should_not_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("event-specific verification should have been skipped")

    async def fake_websockets(_api: object) -> dict[str, object]:
        return {"status": "passed", "channels": {}}

    monkeypatch.setattr(updates_module, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(updates_module, "build_unifi_protect_client", fake_build_client)
    monkeypatch.setattr(updates_module, "load_unifi_protect_bootstrap", fake_noop)
    monkeypatch.setattr(updates_module, "close_unifi_protect_client", fake_noop)
    monkeypatch.setattr(updates_module, "list_bootstrap_cameras", lambda _api: [camera])
    monkeypatch.setattr(updates_module, "get_unifi_protect_snapshot", fake_snapshot)
    monkeypatch.setattr(updates_module, "list_unifi_protect_events", fake_events)
    monkeypatch.setattr(updates_module, "get_event_by_id", should_not_run)
    monkeypatch.setattr(updates_module, "get_unifi_protect_event_thumbnail", should_not_run)
    monkeypatch.setattr(updates_module, "_verify_websocket_establishment", fake_websockets)
    monkeypatch.setattr(updates_module, "current_unifi_protect_version", lambda: "15.12.2")
    monkeypatch.setattr(updates_module, "serialize_unifi_camera", lambda value: {"id": value.id})

    verification = await UnifiProtectUpdateService().verify()

    assert verification["checks"]["event_history"] == {"status": "passed", "event_count": 0}
    assert verification["checks"]["single_event"]["status"] == "skipped"
    assert verification["checks"]["event_thumbnail"]["status"] == "skipped"
    assert verification["checks"]["smart_detect_track"]["status"] == "skipped"


@pytest.mark.asyncio
async def test_websocket_verification_requires_all_three_channels_to_connect() -> None:
    unsubscribed: list[str] = []

    class FakeApi:
        def __init__(self) -> None:
            self.callbacks: dict[str, object] = {}

        def _subscribe_state(self, channel: str, callback: object):
            self.callbacks[channel] = callback
            return lambda: unsubscribed.append(f"{channel}-state")

        def _subscribe(self, channel: str):
            callback = self.callbacks[channel]
            callback(SimpleNamespace(name="CONNECTED"))
            return lambda: unsubscribed.append(f"{channel}-messages")

        def subscribe_websocket_state(self, callback: object):
            return self._subscribe_state("private", callback)

        def subscribe_events_websocket_state(self, callback: object):
            return self._subscribe_state("events", callback)

        def subscribe_devices_websocket_state(self, callback: object):
            return self._subscribe_state("devices", callback)

        def subscribe_websocket(self, _callback: object):
            return self._subscribe("private")

        def subscribe_events_websocket(self, _callback: object):
            return self._subscribe("events")

        def subscribe_devices_websocket(self, _callback: object):
            return self._subscribe("devices")

    result = await updates_module._verify_websocket_establishment(FakeApi(), timeout_seconds=0.1)

    assert result["status"] == "passed"
    assert set(result["channels"]) == {"private", "events", "devices"}
    assert len(unsubscribed) == 6
