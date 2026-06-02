from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.services.snapshots import (
    SNAPSHOT_HEIGHT,
    SNAPSHOT_WIDTH,
    access_event_snapshot_relative_path,
    access_event_snapshot_url,
    apply_snapshot_to_access_event,
    get_snapshot_manager,
)
from app.services.snapshot_recovery import protect_event_id_from_access_event
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS
from app.services.unifi_protect import get_unifi_protect_service

logger = get_logger(__name__)

GATE_CAMERA_IDENTIFIER = "camera.gate"


async def capture_access_event_snapshot(event: Any, *, trace: Any | None = None) -> None:
    if not getattr(event, "id", None):
        return
    manager = get_snapshot_manager()
    protect_event_id = protect_event_id_from_access_event(event)
    span = (
        trace.start_span(
            "Capture Access Event Snapshot",
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            attributes={
                "event_id": str(event.id),
                "registration_number": event.registration_number,
                "camera": GATE_CAMERA_IDENTIFIER,
            },
        )
        if trace
        else None
    )
    capture_error: Exception | None = None
    metadata = None
    source = "camera_snapshot"
    try:
        metadata = await manager.capture_access_event_snapshot(event.id, camera=GATE_CAMERA_IDENTIFIER)
    except Exception as exc:
        capture_error = exc
        logger.info(
            "access_event_snapshot_capture_skipped",
            extra={
                "event_id": str(event.id),
                "registration_number": event.registration_number,
                "camera": GATE_CAMERA_IDENTIFIER,
                "error": str(exc),
                "fallback_protect_event_id": protect_event_id,
            },
        )

    if metadata is None and protect_event_id:
        try:
            media = await get_unifi_protect_service().event_thumbnail(
                protect_event_id,
                width=SNAPSHOT_WIDTH,
                height=SNAPSHOT_HEIGHT,
            )
            metadata = await manager.store_image(
                media.content,
                relative_path=access_event_snapshot_relative_path(event.id),
                url=access_event_snapshot_url(event.id),
                camera=GATE_CAMERA_IDENTIFIER,
                captured_at=event.occurred_at,
            )
            source = "protect_event_thumbnail"
        except Exception as exc:
            logger.info(
                "access_event_snapshot_thumbnail_capture_skipped",
                extra={
                    "event_id": str(event.id),
                    "registration_number": event.registration_number,
                    "protect_event_id": protect_event_id,
                    "error": str(exc),
                },
            )
            if capture_error is None:
                capture_error = exc

    if metadata is None:
        if span:
            output = {
                "captured": False,
                "reason": "capture_failed" if capture_error else "unifi_protect_unconfigured",
                "protect_event_id": protect_event_id,
            }
            if capture_error:
                span.finish(status="error", error=capture_error, output_payload=output)
            else:
                span.finish(output_payload=output)
        return

    apply_snapshot_to_access_event(event, metadata)
    if span:
        span.finish(
            output_payload={
                "captured": True,
                "source": source,
                "protect_event_id": protect_event_id,
                "snapshot_path": metadata.relative_path,
                "bytes": metadata.bytes,
                "width": metadata.width,
                "height": metadata.height,
            }
        )
