import io
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from app.services.snapshots import (
    SNAPSHOT_CONTENT_TYPE,
    SNAPSHOT_MAX_BYTES,
    SnapshotManager,
    access_event_snapshot_archive_relative_path,
    access_event_snapshot_manifest_relative_path,
    access_event_snapshot_payload,
    access_event_snapshot_relative_path,
    access_event_snapshot_url,
    apply_snapshot_to_access_event,
)
from app.services.notification_snapshots import (
    delete_notification_snapshot,
    notification_snapshot_content_type,
    notification_snapshot_path,
    store_notification_snapshot,
)
from app.services.snapshot_recovery import find_protect_snapshot_evidence_by_plate_time


def image_bytes() -> bytes:
    image = Image.new("RGB", (1200, 800), color=(40, 90, 130))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@pytest.mark.asyncio
async def test_snapshot_recovery_finds_lpr_event_by_plate_and_time() -> None:
    incident_at = datetime(2026, 5, 2, 18, 9, 3, 317079, tzinfo=UTC)

    class FakeProtect:
        async def list_events(self, **kwargs):
            assert kwargs["since"] < incident_at
            assert kwargs["until"] > incident_at
            return [
                {
                    "id": "audio-event",
                    "start": "2026-05-02T18:09:01+00:00",
                    "type": "smartAudioDetect",
                    "camera_name": "Lounge",
                    "smart_detect_types": ["alrmSpeak"],
                },
                {
                    "id": "cab4e6cf-0035-4550-ab03-ab003477ae32",
                    "start": "2026-05-02T18:09:18.922000+00:00",
                    "type": "smartDetectZone",
                    "camera_name": "Gate LPR",
                    "smart_detect_types": ["licensePlate", "vehicle"],
                },
            ]

        async def event_lpr_track(self, event_id):
            assert event_id == "cab4e6cf-0035-4550-ab03-ab003477ae32"
            return {
                "event": {
                    "id": event_id,
                    "camera_id": "942A6FD09D64",
                    "camera_name": "Gate LPR",
                    "start": "2026-05-02T18:09:18.922000+00:00",
                },
                "observations": [
                    {
                        "registration_number": "SVA673",
                        "captured_at": "2026-05-02T18:09:22.341000+00:00",
                        "confidence": 88,
                    }
                ],
            }

    evidence = await find_protect_snapshot_evidence_by_plate_time(
        FakeProtect(),
        SimpleNamespace(id=uuid4(), registration_number="SVA673", occurred_at=incident_at),
    )

    assert evidence is not None
    assert evidence.event_id == "cab4e6cf-0035-4550-ab03-ab003477ae32"
    assert evidence.source == "plate_time_lpr_track"
    assert evidence.camera_name == "Gate LPR"
    assert evidence.confidence == 0.88
    assert evidence.delta_seconds == pytest.approx(19.023921)


@pytest.mark.asyncio
async def test_snapshot_manager_compresses_and_stores_relative_metadata(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.services.snapshots.settings.data_dir", tmp_path)
    manager = SnapshotManager()
    event_id = uuid4()
    captured_at = datetime(2026, 4, 30, 20, 30, tzinfo=UTC)

    metadata = await manager.store_image(
        image_bytes(),
        relative_path=access_event_snapshot_relative_path(event_id),
        url=access_event_snapshot_url(event_id),
        camera="camera.gate",
        captured_at=captured_at,
    )

    path = tmp_path / metadata.relative_path
    assert path.exists()
    assert not Path(metadata.relative_path).is_absolute()
    assert metadata.url == f"/api/v1/events/{event_id}/snapshot"
    assert metadata.camera == "camera.gate"
    assert metadata.captured_at == captured_at
    assert metadata.content_type == SNAPSHOT_CONTENT_TYPE
    assert metadata.bytes == path.stat().st_size
    assert metadata.bytes <= SNAPSHOT_MAX_BYTES
    assert metadata.width <= 320
    assert metadata.height <= 180
    with Image.open(path) as stored:
        assert stored.format == "JPEG"

    archive_path = tmp_path / access_event_snapshot_archive_relative_path(event_id)
    manifest_path = tmp_path / access_event_snapshot_manifest_relative_path(event_id)
    assert archive_path.exists()
    assert archive_path.read_bytes() == path.read_bytes()
    assert manifest_path.exists()
    assert f'"primary_path":"snapshots/access-events/{event_id}.jpg"' in manifest_path.read_text()


@pytest.mark.asyncio
async def test_access_event_snapshot_restores_from_archive(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.snapshots.settings.data_dir", tmp_path)
    manager = SnapshotManager()
    event_id = uuid4()

    metadata = await manager.store_image(
        image_bytes(),
        relative_path=access_event_snapshot_relative_path(event_id),
        url=access_event_snapshot_url(event_id),
        camera="camera.gate",
    )

    primary = tmp_path / metadata.relative_path
    original = primary.read_bytes()
    primary.unlink()

    restored = manager.resolve_path(metadata.relative_path)

    assert restored == primary
    assert primary.read_bytes() == original


@pytest.mark.asyncio
async def test_access_event_snapshot_restores_corrupt_primary_from_archive(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.snapshots.settings.data_dir", tmp_path)
    manager = SnapshotManager()
    event_id = uuid4()

    metadata = await manager.store_image(
        image_bytes(),
        relative_path=access_event_snapshot_relative_path(event_id),
        url=access_event_snapshot_url(event_id),
        camera="camera.gate",
    )

    primary = tmp_path / metadata.relative_path
    original = primary.read_bytes()
    primary.write_bytes(b"corrupt")

    restored = manager.resolve_path(metadata.relative_path)

    assert restored == primary
    assert primary.read_bytes() == original


def test_snapshot_manager_rejects_unsafe_paths(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.snapshots.settings.data_dir", tmp_path)
    manager = SnapshotManager()

    with pytest.raises(FileNotFoundError):
        manager.resolve_path("../outside.jpg", must_exist=False)
    with pytest.raises(FileNotFoundError):
        manager.resolve_path("/tmp/outside.jpg", must_exist=False)


def test_access_event_snapshot_payload_uses_event_metadata(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.snapshots.settings.data_dir", tmp_path)
    event_id = uuid4()
    event = SimpleNamespace(id=event_id)
    metadata = SimpleNamespace(
        relative_path=access_event_snapshot_relative_path(event_id),
        content_type=SNAPSHOT_CONTENT_TYPE,
        bytes=1200,
        width=320,
        height=180,
        captured_at=datetime(2026, 4, 30, 20, 45, tzinfo=UTC),
        camera="camera.gate",
    )

    apply_snapshot_to_access_event(event, metadata)
    (tmp_path / metadata.relative_path).parent.mkdir(parents=True)
    (tmp_path / metadata.relative_path).write_bytes(b"jpeg")
    payload = access_event_snapshot_payload(event)

    assert event.snapshot_path == f"snapshots/access-events/{event_id}.jpg"
    assert payload == {
        "snapshot_url": f"/api/v1/events/{event_id}/snapshot",
        "snapshot_captured_at": "2026-04-30T20:45:00+00:00",
        "snapshot_bytes": 1200,
        "snapshot_width": 320,
        "snapshot_height": 180,
        "snapshot_camera": "camera.gate",
    }


def test_snapshot_manager_archives_existing_legacy_access_event_snapshot(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.snapshots.settings.data_dir", tmp_path)
    manager = SnapshotManager()
    event_id = uuid4()
    relative_path = access_event_snapshot_relative_path(event_id)
    primary = tmp_path / relative_path
    primary.parent.mkdir(parents=True)
    primary.write_bytes(b"legacy-jpeg")
    metadata = SimpleNamespace(
        relative_path=relative_path,
        url=access_event_snapshot_url(event_id),
        camera="camera.gate",
        captured_at=datetime(2026, 4, 30, 20, 45, tzinfo=UTC),
        content_type=SNAPSHOT_CONTENT_TYPE,
        bytes=len(b"legacy-jpeg"),
        width=320,
        height=180,
    )

    assert manager.ensure_access_event_archive(metadata)

    archive = tmp_path / access_event_snapshot_archive_relative_path(event_id)
    manifest = tmp_path / access_event_snapshot_manifest_relative_path(event_id)
    assert archive.read_bytes() == b"legacy-jpeg"
    primary.unlink()
    assert manager.resolve_path(relative_path).read_bytes() == b"legacy-jpeg"
    assert f'"primary_path":"{relative_path}"' in manifest.read_text()


def test_access_event_snapshot_payload_suppresses_missing_files(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.snapshots.settings.data_dir", tmp_path)
    event_id = uuid4()
    event = SimpleNamespace(
        id=event_id,
        snapshot_path=access_event_snapshot_relative_path(event_id),
        snapshot_captured_at=datetime(2026, 4, 30, 20, 45, tzinfo=UTC),
        snapshot_bytes=1200,
        snapshot_width=320,
        snapshot_height=180,
        snapshot_camera="camera.gate",
    )

    assert access_event_snapshot_payload(event) == {
        "snapshot_url": None,
        "snapshot_captured_at": None,
        "snapshot_bytes": None,
        "snapshot_width": None,
        "snapshot_height": None,
        "snapshot_camera": None,
    }


def test_notification_snapshot_wrapper_routes_through_snapshot_manager(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.snapshots.settings.data_dir", tmp_path)

    stored = store_notification_snapshot(b"png-bytes", "image/png")
    filename = Path(stored.path).name

    assert stored.path == notification_snapshot_path(filename)
    assert stored.url_path == f"/api/v1/notification-snapshots/{filename}"
    assert stored.content_type == "image/png"
    assert notification_snapshot_content_type(stored.path) == "image/png"
    assert stored.path.read_bytes() == b"png-bytes"
    assert stored.path.parent == tmp_path / "notification-snapshots"

    delete_notification_snapshot(stored.path)
    assert not stored.path.exists()


def test_notification_delete_refuses_access_event_snapshots(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.snapshots.settings.data_dir", tmp_path)
    event_id = uuid4()
    path = tmp_path / access_event_snapshot_relative_path(event_id)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"durable")

    delete_notification_snapshot(path)

    assert path.exists()


def test_notification_snapshot_ttl_cleanup_is_owned_by_snapshot_manager(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.snapshots.settings.data_dir", tmp_path)
    manager = SnapshotManager()
    stored = manager.store_notification_snapshot(b"jpeg-bytes", "image/jpeg")
    expired_at = datetime.now(tz=UTC) - timedelta(hours=25)
    os.utime(stored.path, (expired_at.timestamp(), expired_at.timestamp()))

    manager.cleanup_expired_notification_snapshots(now=datetime.now(tz=UTC))

    assert not stored.path.exists()
