import uuid
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.models import Anomaly
from app.services.snapshots import access_event_snapshot_payload

logger = get_logger(__name__)

ALERT_SNAPSHOT_CONTEXT_KEY = "snapshot"


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


def alert_snapshot_metadata_from_event(event: Any) -> dict[str, Any] | None:
    snapshot = access_event_snapshot_payload(event)
    if not snapshot.get("snapshot_url"):
        return None
    return {
        "url": snapshot["snapshot_url"],
        "camera": snapshot.get("snapshot_camera"),
        "captured_at": snapshot.get("snapshot_captured_at"),
        "content_type": getattr(event, "snapshot_content_type", None) or "image/jpeg",
        "bytes": snapshot.get("snapshot_bytes"),
        "width": snapshot.get("snapshot_width"),
        "height": snapshot.get("snapshot_height"),
    }
