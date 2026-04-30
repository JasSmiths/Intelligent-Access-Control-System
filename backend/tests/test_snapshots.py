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


def image_bytes() -> bytes:
    image = Image.new("RGB", (1200, 800), color=(40, 90, 130))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


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


def test_snapshot_manager_rejects_unsafe_paths(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.snapshots.settings.data_dir", tmp_path)
    manager = SnapshotManager()

    with pytest.raises(FileNotFoundError):
        manager.resolve_path("../outside.jpg", must_exist=False)
    with pytest.raises(FileNotFoundError):
        manager.resolve_path("/tmp/outside.jpg", must_exist=False)


def test_access_event_snapshot_payload_uses_event_metadata() -> None:
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
