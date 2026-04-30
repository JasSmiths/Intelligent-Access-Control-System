from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.services.snapshots import (
    NOTIFICATION_SNAPSHOT_TTL as SNAPSHOT_TTL,
    get_snapshot_manager,
)


@dataclass(frozen=True)
class StoredNotificationSnapshot:
    path: Path
    url_path: str
    content_type: str


def store_notification_snapshot(content: bytes, content_type: str) -> StoredNotificationSnapshot:
    stored = get_snapshot_manager().store_notification_snapshot(content, content_type)
    return StoredNotificationSnapshot(
        path=stored.path,
        url_path=stored.url_path,
        content_type=stored.content_type,
    )


def notification_snapshot_absolute_url(snapshot: StoredNotificationSnapshot) -> str | None:
    from app.core.config import settings

    if not settings.public_base_url:
        return None
    base_url = str(settings.public_base_url).rstrip("/")
    return f"{base_url}{snapshot.url_path}"


def notification_snapshot_path(filename: str) -> Path:
    return get_snapshot_manager().notification_snapshot_path(filename)


def notification_snapshot_content_type(path: Path) -> str:
    return get_snapshot_manager().notification_snapshot_content_type(path)


def cleanup_expired_notification_snapshots(now: datetime | None = None) -> None:
    get_snapshot_manager().cleanup_expired_notification_snapshots(now)


def delete_notification_snapshot(path: str | Path) -> None:
    get_snapshot_manager().delete_snapshot_path(path)
