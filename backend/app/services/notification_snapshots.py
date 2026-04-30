import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

SNAPSHOT_ROOT = settings.data_dir / "notification-snapshots"
SNAPSHOT_TTL = timedelta(hours=24)
SNAPSHOT_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{24,80}\.(?:jpg|jpeg|png)$")


@dataclass(frozen=True)
class StoredNotificationSnapshot:
    path: Path
    url_path: str
    content_type: str


def store_notification_snapshot(content: bytes, content_type: str) -> StoredNotificationSnapshot:
    cleanup_expired_notification_snapshots()
    SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    normalized_content_type = _normalized_content_type(content_type)
    suffix = ".png" if normalized_content_type == "image/png" else ".jpg"
    token = secrets.token_urlsafe(32)
    path = SNAPSHOT_ROOT / f"{token}{suffix}"
    path.write_bytes(content)
    return StoredNotificationSnapshot(
        path=path,
        url_path=f"/api/v1/notification-snapshots/{path.name}",
        content_type=normalized_content_type,
    )


def notification_snapshot_absolute_url(snapshot: StoredNotificationSnapshot) -> str | None:
    if not settings.public_base_url:
        return None
    base_url = str(settings.public_base_url).rstrip("/")
    return f"{base_url}{snapshot.url_path}"


def notification_snapshot_path(filename: str) -> Path:
    if not SNAPSHOT_FILENAME_PATTERN.fullmatch(filename):
        raise FileNotFoundError(filename)
    path = (SNAPSHOT_ROOT / filename).resolve()
    root = SNAPSHOT_ROOT.resolve()
    if root not in path.parents:
        raise FileNotFoundError(filename)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(filename)
    return path


def notification_snapshot_content_type(path: Path) -> str:
    return "image/png" if path.suffix.lower() == ".png" else "image/jpeg"


def cleanup_expired_notification_snapshots(now: datetime | None = None) -> None:
    if not SNAPSHOT_ROOT.exists():
        return
    cutoff = (now or datetime.now(UTC)) - SNAPSHOT_TTL
    for path in SNAPSHOT_ROOT.iterdir():
        if not path.is_file():
            continue
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if modified < cutoff:
                path.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("notification_snapshot_cleanup_failed", extra={"path": str(path), "error": str(exc)})


def delete_notification_snapshot(path: str | Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("notification_snapshot_cleanup_failed", extra={"path": str(path), "error": str(exc)})


def _normalized_content_type(content_type: str) -> str:
    return "image/png" if "png" in content_type.lower() else "image/jpeg"
