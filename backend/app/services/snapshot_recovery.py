import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import AccessEvent, Anomaly
from app.modules.dvla.vehicle_enquiry import normalize_registration_number
from app.modules.unifi_protect.client import UnifiProtectError
from app.services.alert_snapshots import ALERT_SNAPSHOT_CONTEXT_KEY, alert_snapshot_metadata_from_event
from app.services.snapshots import (
    SNAPSHOT_CONTENT_TYPE,
    SnapshotMetadata,
    access_event_snapshot_relative_path,
    access_event_snapshot_url,
    apply_snapshot_to_access_event,
    get_snapshot_manager,
)
from app.services.unifi_protect import get_unifi_protect_service

logger = get_logger(__name__)

SNAPSHOT_RECOVERY_LIMIT = 250
SNAPSHOT_RECOVERY_WIDTH = 320
SNAPSHOT_RECOVERY_HEIGHT = 180
SNAPSHOT_RECOVERY_CAMERA = "camera.gate"
SNAPSHOT_RECOVERY_PAYLOAD_KEY = "snapshot_recovery"
SNAPSHOT_RECOVERY_EVENT_LOOKUP_WINDOW = timedelta(minutes=5)
SNAPSHOT_RECOVERY_EVENT_LOOKUP_LIMIT = 120


@dataclass(frozen=True)
class ProtectSnapshotEvidence:
    event_id: str
    source: str
    camera_id: str | None = None
    camera_name: str | None = None
    captured_at: datetime | None = None
    confidence: float | None = None
    event: dict[str, Any] | None = None
    track_candidate: dict[str, Any] | None = None
    delta_seconds: float | None = None


@dataclass
class SnapshotRecoveryResult:
    scanned: int = 0
    archived: int = 0
    restored: int = 0
    skipped: int = 0
    errors: int = 0
    repopulated_alerts: int = 0


async def recover_missing_access_event_snapshots(*, limit: int = SNAPSHOT_RECOVERY_LIMIT) -> SnapshotRecoveryResult:
    """Rebuild missing access-event snapshots from retained UniFi Protect event thumbnails."""

    manager = get_snapshot_manager()
    protect = get_unifi_protect_service()
    result = SnapshotRecoveryResult()
    thumbnail_cache: dict[str, bytes] = {}
    recovered_event_ids: set[uuid.UUID] = set()

    if not await protect.configured():
        return result

    async with AsyncSessionLocal() as session:
        events = (
            await session.scalars(
                select(AccessEvent)
                .options(selectinload(AccessEvent.anomalies))
                .order_by(AccessEvent.occurred_at.desc())
                .limit(limit)
            )
        ).all()
        result.scanned = len(events)

        for event in events:
            if _snapshot_file_exists(event):
                if await asyncio.to_thread(_ensure_snapshot_archive, manager, event):
                    result.archived += 1
                continue

            evidence = await protect_snapshot_evidence_for_access_event(protect, event)
            if not evidence:
                _set_recovery_status(event, "skipped", reason="no_protect_event_match")
                result.skipped += 1
                continue

            try:
                content = thumbnail_cache.get(evidence.event_id)
                if content is None:
                    media = await protect.event_thumbnail(
                        evidence.event_id,
                        width=SNAPSHOT_RECOVERY_WIDTH,
                        height=SNAPSHOT_RECOVERY_HEIGHT,
                    )
                    content = media.content
                    thumbnail_cache[evidence.event_id] = content

                metadata = await manager.store_image(
                    content,
                    relative_path=access_event_snapshot_relative_path(event.id),
                    url=access_event_snapshot_url(event.id),
                    camera=evidence.camera_name or evidence.camera_id or SNAPSHOT_RECOVERY_CAMERA,
                    captured_at=event.occurred_at or datetime.now(tz=UTC),
                )
                apply_snapshot_to_access_event(event, metadata)
                _attach_protect_snapshot_evidence(event, evidence)
                _set_recovery_status(
                    event,
                    "restored",
                    protect_event_id=evidence.event_id,
                    source=evidence.source,
                    delta_seconds=evidence.delta_seconds,
                )
                recovered_event_ids.add(event.id)
                result.restored += 1
            except Exception as exc:
                _set_recovery_status(event, "unavailable", protect_event_id=evidence.event_id, reason=str(exc))
                result.errors += 1
                logger.info(
                    "access_event_snapshot_recovery_failed",
                    extra={
                        "event_id": str(event.id),
                        "registration_number": event.registration_number,
                        "protect_event_id": evidence.event_id,
                        "error": str(exc),
                    },
                )

        await session.flush()

        if recovered_event_ids:
            anomalies = (
                await session.scalars(
                    select(Anomaly)
                    .options(selectinload(Anomaly.event))
                    .where(Anomaly.event_id.in_(recovered_event_ids))
                )
            ).all()
            for anomaly in anomalies:
                if not anomaly.event:
                    continue
                snapshot = alert_snapshot_metadata_from_event(anomaly.event)
                if not snapshot:
                    continue
                context = dict(anomaly.context or {})
                if context.get(ALERT_SNAPSHOT_CONTEXT_KEY) == snapshot:
                    continue
                context[ALERT_SNAPSHOT_CONTEXT_KEY] = snapshot
                anomaly.context = context
                result.repopulated_alerts += 1

        if result.archived or result.restored or result.repopulated_alerts or result.errors or result.skipped:
            await session.commit()
        else:
            await session.rollback()

    if result.archived or result.restored or result.errors:
        logger.info(
            "access_event_snapshot_recovery_completed",
            extra={
                "scanned": result.scanned,
                "archived": result.archived,
                "restored": result.restored,
                "skipped": result.skipped,
                "errors": result.errors,
                "repopulated_alerts": result.repopulated_alerts,
            },
        )
    return result


async def recover_missing_access_event_snapshots_safely() -> None:
    try:
        await recover_missing_access_event_snapshots()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.info("access_event_snapshot_recovery_skipped", extra={"error": str(exc)})


def protect_event_id_from_access_event(event: AccessEvent) -> str | None:
    raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    return protect_event_id_from_payload(raw_payload)


def protect_event_id_from_payload(raw_payload: dict[str, Any]) -> str | None:
    if not isinstance(raw_payload, dict):
        return None
    candidates = [
        _nested_value(raw_payload, ("best", "alarm", "triggers", 0, "eventId")),
        _nested_value(raw_payload, ("best", "alarm", "triggers", 0, "event_id")),
        _nested_value(raw_payload, ("best", "alarm", "eventId")),
        _nested_value(raw_payload, ("best", "alarm", "event_id")),
        _nested_value(raw_payload, ("best", "eventId")),
        _nested_value(raw_payload, ("best", "event_id")),
        _nested_value(raw_payload, ("protect_evidence", "event_id")),
        _nested_value(raw_payload, ("backfill", "protect_event_id")),
        _nested_value(raw_payload, ("snapshot_recovery", "protect_event_id")),
    ]
    vehicle_session = raw_payload.get("vehicle_session")
    if isinstance(vehicle_session, dict):
        protect_event_ids = vehicle_session.get("protect_event_ids")
        if isinstance(protect_event_ids, list):
            candidates.extend(protect_event_ids)

    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return None


async def protect_snapshot_evidence_for_access_event(
    protect: Any,
    event: AccessEvent,
) -> ProtectSnapshotEvidence | None:
    protect_event_id = protect_event_id_from_access_event(event)
    if protect_event_id:
        return ProtectSnapshotEvidence(event_id=protect_event_id, source="access_event_payload")
    return await find_protect_snapshot_evidence_by_plate_time(protect, event)


async def find_protect_snapshot_evidence_by_plate_time(
    protect: Any,
    event: AccessEvent,
) -> ProtectSnapshotEvidence | None:
    registration_number = normalize_registration_number(str(event.registration_number or ""))
    occurred_at = _aware_utc(event.occurred_at) if event.occurred_at else None
    if not registration_number or not occurred_at:
        return None

    try:
        events = await protect.list_events(
            limit=SNAPSHOT_RECOVERY_EVENT_LOOKUP_LIMIT,
            since=occurred_at - SNAPSHOT_RECOVERY_EVENT_LOOKUP_WINDOW,
            until=occurred_at + SNAPSHOT_RECOVERY_EVENT_LOOKUP_WINDOW,
        )
    except Exception as exc:
        logger.info(
            "access_event_snapshot_recovery_protect_lookup_failed",
            extra={
                "event_id": str(event.id),
                "registration_number": registration_number,
                "error": str(exc),
            },
        )
        return None

    best: tuple[float, float, datetime, ProtectSnapshotEvidence] | None = None
    for protect_event in events:
        protect_event_id = str(protect_event.get("id") or "").strip()
        if not protect_event_id or not _event_might_have_lpr(protect_event):
            continue
        try:
            track = await protect.event_lpr_track(protect_event_id)
        except UnifiProtectError:
            continue
        except Exception as exc:
            logger.debug(
                "access_event_snapshot_recovery_track_lookup_failed",
                extra={"protect_event_id": protect_event_id, "error": str(exc)},
            )
            continue

        track_event = track.get("event") if isinstance(track.get("event"), dict) else protect_event
        for observation in track.get("observations") or []:
            if not isinstance(observation, dict):
                continue
            observed_plate = normalize_registration_number(
                str(observation.get("registration_number") or observation.get("raw_value") or "")
            )
            if observed_plate != registration_number:
                continue
            captured_at = (
                _parse_datetime(observation.get("captured_at"))
                or _parse_datetime((track_event or {}).get("start"))
                or _parse_datetime(protect_event.get("start"))
                or occurred_at
            )
            captured_at = _aware_utc(captured_at)
            delta_seconds = abs((captured_at - occurred_at).total_seconds())
            if delta_seconds > SNAPSHOT_RECOVERY_EVENT_LOOKUP_WINDOW.total_seconds():
                continue
            confidence = _confidence_ratio(observation.get("confidence"))
            evidence = ProtectSnapshotEvidence(
                event_id=protect_event_id,
                source="plate_time_lpr_track",
                camera_id=_optional_text((track_event or {}).get("camera_id") or protect_event.get("camera_id")),
                camera_name=_optional_text((track_event or {}).get("camera_name") or protect_event.get("camera_name")),
                captured_at=captured_at,
                confidence=confidence,
                event=track_event if isinstance(track_event, dict) else protect_event,
                track_candidate=dict(observation),
                delta_seconds=delta_seconds,
            )
            score = (delta_seconds, -confidence, captured_at, evidence)
            if best is None or score[:3] < best[:3]:
                best = score

    return best[3] if best else None


def _snapshot_file_exists(event: AccessEvent) -> bool:
    if not event.snapshot_path:
        return False
    try:
        get_snapshot_manager().resolve_path(event.snapshot_path)
    except FileNotFoundError:
        return False
    return True


def _ensure_snapshot_archive(manager, event: AccessEvent) -> bool:
    if not event.snapshot_path:
        return False
    metadata = SnapshotMetadata(
        relative_path=event.snapshot_path,
        url=access_event_snapshot_url(event.id),
        camera=event.snapshot_camera or SNAPSHOT_RECOVERY_CAMERA,
        captured_at=event.snapshot_captured_at or event.occurred_at or datetime.now(tz=UTC),
        content_type=event.snapshot_content_type or SNAPSHOT_CONTENT_TYPE,
        bytes=event.snapshot_bytes or 0,
        width=event.snapshot_width or SNAPSHOT_RECOVERY_WIDTH,
        height=event.snapshot_height or SNAPSHOT_RECOVERY_HEIGHT,
    )
    try:
        return manager.ensure_access_event_archive(metadata)
    except Exception as exc:
        logger.info(
            "access_event_snapshot_archive_failed",
            extra={
                "event_id": str(event.id),
                "registration_number": event.registration_number,
                "error": str(exc),
            },
        )
        return False


def _recovery_status(event: AccessEvent) -> str | None:
    raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    value = raw_payload.get(SNAPSHOT_RECOVERY_PAYLOAD_KEY)
    if not isinstance(value, dict):
        return None
    status = str(value.get("status") or "").strip()
    return status or None


def _set_recovery_status(
    event: AccessEvent,
    status: str,
    *,
    protect_event_id: str | None = None,
    reason: str | None = None,
    source: str | None = None,
    delta_seconds: float | None = None,
) -> None:
    raw_payload = dict(event.raw_payload or {})
    payload: dict[str, Any] = {
        "status": status,
        "attempted_at": datetime.now(tz=UTC).isoformat(),
    }
    if protect_event_id:
        payload["protect_event_id"] = protect_event_id
    if source:
        payload["source"] = source
    if delta_seconds is not None:
        payload["delta_seconds"] = round(delta_seconds, 3)
    if reason:
        payload["reason"] = reason[:500]
    raw_payload[SNAPSHOT_RECOVERY_PAYLOAD_KEY] = payload
    event.raw_payload = raw_payload


def _attach_protect_snapshot_evidence(event: AccessEvent, evidence: ProtectSnapshotEvidence) -> None:
    if evidence.source != "plate_time_lpr_track":
        return
    raw_payload = dict(event.raw_payload or {})
    raw_payload["protect_evidence"] = {
        key: value
        for key, value in {
            "event_id": evidence.event_id,
            "camera_id": evidence.camera_id,
            "camera_name": evidence.camera_name,
            "captured_at": evidence.captured_at.isoformat() if evidence.captured_at else None,
            "confidence": evidence.confidence,
            "event": evidence.event,
            "track_candidate": evidence.track_candidate,
            "source": evidence.source,
        }.items()
        if value is not None
    }
    event.raw_payload = raw_payload


def _nested_value(value: Any, path: tuple[Any, ...]) -> Any:
    current = value
    for key in path:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and isinstance(key, int) and 0 <= key < len(current):
            current = current[key]
        else:
            return None
    return current


def _event_might_have_lpr(event: dict[str, Any]) -> bool:
    smart_types = [str(item or "").lower() for item in event.get("smart_detect_types") or []]
    if any("license" in item or "plate" in item for item in smart_types):
        return True
    text = " ".join(
        str(value or "")
        for value in (
            event.get("type"),
            event.get("camera_name"),
            event.get("camera_id"),
            event.get("name"),
        )
    ).lower()
    return "lpr" in text or "license" in text or "plate" in text


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _aware_utc(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _confidence_ratio(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    if confidence > 1:
        confidence = confidence / 100
    return max(0.0, min(1.0, confidence))
