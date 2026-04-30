import asyncio
import io
import re
import secrets
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import settings
from app.core.logging import get_logger
from app.services.unifi_protect import get_unifi_protect_service

logger = get_logger(__name__)

SNAPSHOT_CONTENT_TYPE = "image/jpeg"
SNAPSHOT_HEIGHT = 180
SNAPSHOT_INITIAL_QUALITY = 54
SNAPSHOT_MAX_BYTES = 24 * 1024
SNAPSHOT_MIN_QUALITY = 30
SNAPSHOT_TIMEOUT_SECONDS = 5.0
SNAPSHOT_WIDTH = 320
ACCESS_EVENT_SNAPSHOT_CAMERA = "camera.gate"
NOTIFICATION_SNAPSHOT_ROOT = "notification-snapshots"
NOTIFICATION_SNAPSHOT_TTL = timedelta(hours=24)
NOTIFICATION_SNAPSHOT_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{24,80}\.(?:jpg|jpeg|png)$")


class SnapshotError(RuntimeError):
    """Raised when snapshot media cannot be processed or stored."""


@dataclass(frozen=True)
class SnapshotMetadata:
    relative_path: str
    url: str
    camera: str
    captured_at: datetime
    content_type: str
    bytes: int
    width: int
    height: int

    def as_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["captured_at"] = self.captured_at.isoformat()
        return payload


@dataclass(frozen=True)
class StoredSnapshot:
    relative_path: str
    path: Path
    url_path: str
    content_type: str


class SnapshotManager:
    async def capture_access_event_snapshot(
        self,
        event_id: uuid.UUID,
        *,
        camera: str = ACCESS_EVENT_SNAPSHOT_CAMERA,
    ) -> SnapshotMetadata | None:
        return await self.capture_camera_snapshot(
            camera=camera,
            relative_path=access_event_snapshot_relative_path(event_id),
            url=access_event_snapshot_url(event_id),
        )

    async def capture_camera_snapshot(
        self,
        *,
        camera: str,
        relative_path: str,
        url: str,
    ) -> SnapshotMetadata | None:
        protect = get_unifi_protect_service()
        if not await protect.configured():
            return None
        async with asyncio.timeout(SNAPSHOT_TIMEOUT_SECONDS):
            media = await protect.snapshot(camera, width=SNAPSHOT_WIDTH, height=SNAPSHOT_HEIGHT)
        return await self.store_image(
            media.content,
            relative_path=relative_path,
            url=url,
            camera=camera,
            captured_at=datetime.now(tz=UTC),
        )

    def store_notification_snapshot(self, content: bytes, content_type: str) -> StoredSnapshot:
        self.cleanup_expired_notification_snapshots()
        normalized_content_type = normalize_image_content_type(content_type)
        suffix = ".png" if normalized_content_type == "image/png" else ".jpg"
        filename = f"{secrets.token_urlsafe(32)}{suffix}"
        relative_path = notification_snapshot_relative_path(filename)
        path = self.resolve_path(relative_path, must_exist=False)
        self._write_snapshot(path, content)
        return StoredSnapshot(
            relative_path=relative_path,
            path=path,
            url_path=notification_snapshot_url(filename),
            content_type=normalized_content_type,
        )

    def notification_snapshot_path(self, filename: str) -> Path:
        if not NOTIFICATION_SNAPSHOT_FILENAME_PATTERN.fullmatch(filename):
            raise FileNotFoundError(filename)
        return self.resolve_path(notification_snapshot_relative_path(filename))

    def notification_snapshot_content_type(self, path: Path) -> str:
        return "image/png" if path.suffix.lower() == ".png" else SNAPSHOT_CONTENT_TYPE

    def cleanup_expired_notification_snapshots(self, now: datetime | None = None) -> None:
        root = self.resolve_path(NOTIFICATION_SNAPSHOT_ROOT, must_exist=False)
        if not root.exists():
            return
        cutoff = (now or datetime.now(UTC)) - NOTIFICATION_SNAPSHOT_TTL
        for path in root.iterdir():
            if not path.is_file():
                continue
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
                if modified < cutoff:
                    path.unlink(missing_ok=True)
            except OSError as exc:
                logger.debug("notification_snapshot_cleanup_failed", extra={"path": str(path), "error": str(exc)})

    def delete_snapshot_path(self, path: str | Path) -> None:
        try:
            candidate = Path(path)
            if candidate.is_absolute():
                root = settings.data_dir.resolve()
                resolved = candidate.resolve()
                if resolved != root and root not in resolved.parents:
                    return
            else:
                resolved = self.resolve_path(str(candidate), must_exist=False)
            resolved.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("snapshot_delete_failed", extra={"path": str(path), "error": str(exc)})
        except FileNotFoundError:
            return

    async def store_image(
        self,
        content: bytes,
        *,
        relative_path: str,
        url: str,
        camera: str,
        captured_at: datetime | None = None,
    ) -> SnapshotMetadata:
        path = self.resolve_path(relative_path, must_exist=False)
        compacted, width, height = await asyncio.to_thread(self._compact_jpeg, content)
        await asyncio.to_thread(self._write_snapshot, path, compacted)
        return SnapshotMetadata(
            relative_path=relative_path,
            url=url,
            camera=camera,
            captured_at=captured_at or datetime.now(tz=UTC),
            content_type=SNAPSHOT_CONTENT_TYPE,
            bytes=len(compacted),
            width=width,
            height=height,
        )

    def resolve_path(self, relative_path: str, *, must_exist: bool = True) -> Path:
        normalized = PurePosixPath(relative_path)
        if normalized.is_absolute() or ".." in normalized.parts:
            raise FileNotFoundError(relative_path)
        root = settings.data_dir.resolve()
        path = root.joinpath(*normalized.parts).resolve()
        if path != root and root not in path.parents:
            raise FileNotFoundError(relative_path)
        if must_exist and (not path.exists() or not path.is_file()):
            raise FileNotFoundError(relative_path)
        return path

    def _compact_jpeg(self, content: bytes) -> tuple[bytes, int, int]:
        try:
            with Image.open(io.BytesIO(content)) as image:
                image = ImageOps.exif_transpose(image)
                if image.mode != "RGB":
                    image = image.convert("RGB")
                image.thumbnail((SNAPSHOT_WIDTH, SNAPSHOT_HEIGHT), Image.Resampling.LANCZOS)
                width, height = image.size
                best = b""
                for quality in range(SNAPSHOT_INITIAL_QUALITY, SNAPSHOT_MIN_QUALITY - 1, -6):
                    output = io.BytesIO()
                    image.save(
                        output,
                        format="JPEG",
                        quality=quality,
                        optimize=True,
                        progressive=True,
                    )
                    best = output.getvalue()
                    if len(best) <= SNAPSHOT_MAX_BYTES:
                        break
                return best, width, height
        except (OSError, UnidentifiedImageError) as exc:
            raise SnapshotError("Snapshot image could not be processed.") from exc

    def _write_snapshot(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def access_event_snapshot_relative_path(event_id: uuid.UUID) -> str:
    return f"snapshots/access-events/{event_id}.jpg"


def access_event_snapshot_url(event_id: uuid.UUID) -> str:
    return f"/api/v1/events/{event_id}/snapshot"


def notification_snapshot_relative_path(filename: str) -> str:
    return f"{NOTIFICATION_SNAPSHOT_ROOT}/{filename}"


def notification_snapshot_url(filename: str) -> str:
    return f"/api/v1/notification-snapshots/{filename}"


def normalize_image_content_type(content_type: str) -> str:
    return "image/png" if "png" in str(content_type or "").lower() else SNAPSHOT_CONTENT_TYPE


def apply_snapshot_to_access_event(event: Any, metadata: SnapshotMetadata) -> None:
    event.snapshot_path = metadata.relative_path
    event.snapshot_content_type = metadata.content_type
    event.snapshot_bytes = metadata.bytes
    event.snapshot_width = metadata.width
    event.snapshot_height = metadata.height
    event.snapshot_captured_at = metadata.captured_at
    event.snapshot_camera = metadata.camera


def access_event_snapshot_payload(event: Any) -> dict[str, Any]:
    snapshot_path = getattr(event, "snapshot_path", None)
    if not snapshot_path:
        return {
            "snapshot_url": None,
            "snapshot_captured_at": None,
            "snapshot_bytes": None,
            "snapshot_width": None,
            "snapshot_height": None,
            "snapshot_camera": None,
        }
    event_id = getattr(event, "id", None)
    return {
        "snapshot_url": access_event_snapshot_url(event_id) if event_id else None,
        "snapshot_captured_at": (
            event.snapshot_captured_at.isoformat()
            if getattr(event, "snapshot_captured_at", None)
            else None
        ),
        "snapshot_bytes": getattr(event, "snapshot_bytes", None),
        "snapshot_width": getattr(event, "snapshot_width", None),
        "snapshot_height": getattr(event, "snapshot_height", None),
        "snapshot_camera": getattr(event, "snapshot_camera", None),
    }


def get_snapshot_manager() -> SnapshotManager:
    return SnapshotManager()
