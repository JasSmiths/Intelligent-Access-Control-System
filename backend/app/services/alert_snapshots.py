import asyncio
import io
import uuid
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import settings
from app.core.logging import get_logger
from app.models import Anomaly
from app.models.enums import AnomalyType
from app.services.unifi_protect import get_unifi_protect_service

logger = get_logger(__name__)

ALERT_SNAPSHOT_CAMERA = "camera.gate"
ALERT_SNAPSHOT_CONTEXT_KEY = "snapshot"
ALERT_SNAPSHOT_HEIGHT = 180
ALERT_SNAPSHOT_QUALITY = 54
ALERT_SNAPSHOT_TIMEOUT_SECONDS = 5.0
ALERT_SNAPSHOT_WIDTH = 320


async def capture_alert_snapshot(anomaly: Anomaly) -> None:
    if anomaly.anomaly_type != AnomalyType.UNAUTHORIZED_PLATE:
        return
    if anomaly.id is None:
        anomaly.id = uuid.uuid4()

    try:
        protect = get_unifi_protect_service()
        if not await protect.configured():
            return
        async with asyncio.timeout(ALERT_SNAPSHOT_TIMEOUT_SECONDS):
            media = await protect.snapshot(
                ALERT_SNAPSHOT_CAMERA,
                width=ALERT_SNAPSHOT_WIDTH,
                height=ALERT_SNAPSHOT_HEIGHT,
            )
        content = await asyncio.to_thread(_compact_jpeg, media.content)
        path = alert_snapshot_path(anomaly.id)
        await asyncio.to_thread(_write_snapshot, path, content)
    except Exception as exc:
        logger.info(
            "alert_snapshot_capture_skipped",
            extra={
                "alert_id": str(anomaly.id),
                "camera": ALERT_SNAPSHOT_CAMERA,
                "error": str(exc),
            },
        )
        return

    context = dict(anomaly.context or {})
    context[ALERT_SNAPSHOT_CONTEXT_KEY] = {
        "url": alert_snapshot_url(anomaly.id),
        "camera": ALERT_SNAPSHOT_CAMERA,
        "captured_at": datetime.now(tz=UTC).isoformat(),
        "content_type": "image/jpeg",
        "bytes": len(content),
        "width": ALERT_SNAPSHOT_WIDTH,
        "height": ALERT_SNAPSHOT_HEIGHT,
    }
    anomaly.context = context


def delete_alert_snapshots(rows: list[Anomaly]) -> None:
    for row in rows:
        delete_alert_snapshot(row)


def delete_alert_snapshot(row: Anomaly) -> None:
    if row.id:
        try:
            alert_snapshot_path(row.id).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "alert_snapshot_delete_failed",
                extra={"alert_id": str(row.id), "error": str(exc)},
            )

    context = dict(row.context or {})
    if ALERT_SNAPSHOT_CONTEXT_KEY in context:
        context.pop(ALERT_SNAPSHOT_CONTEXT_KEY, None)
        row.context = context


def alert_snapshot_path(alert_id: uuid.UUID) -> Path:
    return settings.data_dir / "alert-snapshots" / f"{alert_id}.jpg"


def alert_snapshot_url(alert_id: uuid.UUID) -> str:
    return f"/api/v1/alerts/{alert_id}/snapshot"


def alert_snapshot_metadata(row: Anomaly) -> dict | None:
    value = (row.context or {}).get(ALERT_SNAPSHOT_CONTEXT_KEY)
    return value if isinstance(value, dict) else None


def _compact_jpeg(content: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(content)) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            image.thumbnail((ALERT_SNAPSHOT_WIDTH, ALERT_SNAPSHOT_HEIGHT), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(
                output,
                format="JPEG",
                quality=ALERT_SNAPSHOT_QUALITY,
                optimize=True,
                progressive=True,
            )
            return output.getvalue()
    except (OSError, UnidentifiedImageError) as exc:
        logger.info("alert_snapshot_compaction_skipped", extra={"error": str(exc)})
        return content


def _write_snapshot(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
