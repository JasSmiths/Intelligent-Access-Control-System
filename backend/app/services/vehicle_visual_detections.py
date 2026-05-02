import asyncio
import re
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from app.services.event_bus import event_bus


MAX_VEHICLE_VISUAL_OBSERVATIONS = 500
MAX_VEHICLE_PRESENCE_OBSERVATIONS = 1000
VEHICLE_COLOR_KEYS = ("color", "colour", "vehicleColor", "vehicleColour")
VEHICLE_TYPE_KEYS = ("vehicleType", "vehicle_type", "vehicleClass", "vehicle_class")
PLATE_KEYS = (
    "licensePlate",
    "license_plate",
    "licencePlate",
    "licence_plate",
    "matchedName",
    "matched_name",
    "name",
    "plate",
    "registration",
    "registrationNumber",
    "registration_number",
    "vrn",
)
VEHICLE_PRESENCE_TYPES = {"vehicle", "licenseplate", "license_plate"}
VEHICLE_PRESENCE_ACTIVE_FIELDS = (
    "isVehicleCurrentlyDetected",
    "is_vehicle_currently_detected",
    "isLicensePlateCurrentlyDetected",
    "is_license_plate_currently_detected",
)
VEHICLE_PRESENCE_ACTIVE_LABELS = {"vehicle", "license_plate", "licenseplate"}


@dataclass(frozen=True)
class VehicleVisualObservation:
    id: str
    source: str
    source_detail: str
    observed_vehicle_type: str
    observed_vehicle_color: str
    received_at: str
    registration_number: str | None = None
    captured_at: str | None = None
    event_id: str | None = None
    camera_id: str | None = None
    camera_name: str | None = None
    vehicle_type_confidence: float | None = None
    vehicle_color_confidence: float | None = None
    payload_path: str | None = None


@dataclass(frozen=True)
class VehiclePresenceObservation:
    id: str
    source: str
    source_detail: str
    active: bool
    observed_at: str
    registration_number: str | None = None
    event_id: str | None = None
    camera_id: str | None = None
    camera_name: str | None = None
    device_id: str | None = None
    ended_at: str | None = None
    payload_path: str | None = None


class VehicleVisualDetectionRecorder:
    """In-memory feed of UniFi Protect vehicle colour/type observations."""

    def __init__(self) -> None:
        self._observations: deque[VehicleVisualObservation] = deque(
            maxlen=MAX_VEHICLE_VISUAL_OBSERVATIONS
        )
        self._lock = asyncio.Lock()

    async def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self._lock:
            observations = list(self._observations)[-limit:]
        return [asdict(observation) for observation in reversed(observations)]

    async def clear(self) -> None:
        async with self._lock:
            self._observations.clear()
        await event_bus.publish("vehicle_visual_detection.cleared", {})

    async def record_unifi_payload(
        self,
        payload: Any,
        *,
        registration_number: str | None = None,
        received_at: datetime | None = None,
    ) -> int:
        observations = extract_unifi_payload_vehicle_visual_observations(
            payload,
            registration_number=registration_number,
            received_at=received_at,
        )
        for observation in observations:
            await self._append_and_publish(observation)
        return len(observations)

    async def record_unifi_protect_message(
        self,
        message: Any,
        *,
        received_at: datetime | None = None,
    ) -> int:
        observations = extract_unifi_protect_vehicle_visual_observations(
            message,
            received_at=received_at,
        )
        for observation in observations:
            await self._append_and_publish(observation)
        return len(observations)

    async def record_unifi_protect_track(
        self,
        track: Any,
        *,
        event: Any = None,
        event_id: str | None = None,
        received_at: datetime | None = None,
        probe_attempt: int | None = None,
    ) -> int:
        observations = extract_unifi_protect_track_vehicle_visual_observations(
            track,
            event=event,
            event_id=event_id,
            received_at=received_at,
            probe_attempt=probe_attempt,
        )
        for observation in observations:
            await self._append_and_publish(observation)
        return len(observations)

    async def recent_match(
        self,
        registration_number: str,
        *,
        occurred_at: datetime | None = None,
        max_age_seconds: float = 45.0,
    ) -> dict[str, Any] | None:
        normalized_plate = _normalize_plate(registration_number)
        if not normalized_plate:
            return None

        async with self._lock:
            observations = list(self._observations)

        candidates = [
            observation
            for observation in observations
            if observation.registration_number == normalized_plate
        ]
        if not candidates:
            return None

        def score(observation: VehicleVisualObservation) -> tuple[float, int, float]:
            distance = _time_distance_seconds(observation, occurred_at)
            confidence = max(
                observation.vehicle_color_confidence or 0.0,
                observation.vehicle_type_confidence or 0.0,
            )
            completeness = int(bool(observation.observed_vehicle_color)) + int(
                bool(observation.observed_vehicle_type)
            )
            return (distance if distance is not None else 999999.0, -completeness, -confidence)

        if occurred_at is not None:
            candidates = [
                observation
                for observation in candidates
                if (distance := _time_distance_seconds(observation, occurred_at)) is None
                or distance <= max_age_seconds
            ]
        if not candidates:
            return None

        return asdict(sorted(candidates, key=score)[0])

    async def _append_and_publish(self, observation: VehicleVisualObservation) -> None:
        async with self._lock:
            self._observations.append(observation)
        await event_bus.publish(
            "vehicle_visual_detection.observed",
            {"observation": asdict(observation)},
        )


class VehiclePresenceTracker:
    """In-memory UniFi vehicle-presence feed for access-event suppression."""

    def __init__(self) -> None:
        self._observations: deque[VehiclePresenceObservation] = deque(
            maxlen=MAX_VEHICLE_PRESENCE_OBSERVATIONS
        )
        self._lock = asyncio.Lock()

    async def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self._lock:
            observations = list(self._observations)[-limit:]
        return [asdict(observation) for observation in reversed(observations)]

    async def clear(self) -> None:
        async with self._lock:
            self._observations.clear()
        await event_bus.publish("vehicle_presence.cleared", {})

    async def record_unifi_payload(
        self,
        payload: Any,
        *,
        registration_number: str | None = None,
        received_at: datetime | None = None,
    ) -> int:
        observation = _presence_from_lpr_payload(
            payload,
            registration_number=registration_number,
            received_at=received_at or _now_utc(),
        )
        if observation is None:
            return 0
        await self._append_and_publish(observation)
        return 1

    async def record_unifi_realtime_payload(
        self,
        payload: dict[str, Any],
        *,
        received_at: datetime | None = None,
    ) -> int:
        observations = _presence_from_realtime_payload(
            payload,
            received_at=received_at or _now_utc(),
        )
        for observation in observations:
            await self._append_and_publish(observation)
        return len(observations)

    async def record_unifi_protect_track(
        self,
        track: Any,
        *,
        event: Any = None,
        event_id: str | None = None,
        received_at: datetime | None = None,
        probe_attempt: int | None = None,
    ) -> int:
        observations = _presence_from_track(
            track,
            event=event,
            event_id=event_id,
            received_at=received_at or _now_utc(),
            probe_attempt=probe_attempt,
        )
        for observation in observations:
            await self._append_and_publish(observation)
        return len(observations)

    async def recent_evidence(
        self,
        *,
        registration_number: str | None = None,
        event_ids: set[str] | None = None,
        camera_id: str | None = None,
        device_id: str | None = None,
        observed_at: datetime | None = None,
        max_age_seconds: float = 180.0,
    ) -> dict[str, Any] | None:
        checked_at = observed_at or _now_utc()
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=UTC)
        normalized_plate = _normalize_plate(registration_number or "")
        event_ids = {str(item) for item in (event_ids or set()) if str(item)}
        camera_id = str(camera_id or "").strip() or None
        device_id = str(device_id or "").strip() or None

        async with self._lock:
            observations = list(self._observations)

        candidates: list[tuple[int, float, VehiclePresenceObservation]] = []
        for observation in observations:
            if not observation.active:
                continue
            observed = _datetime_from_any(observation.observed_at)
            if observed is None:
                continue
            if _has_later_vehicle_presence_end(observation, observations, checked_at):
                continue
            age_seconds = abs((checked_at.astimezone(UTC) - observed.astimezone(UTC)).total_seconds())
            if age_seconds > max_age_seconds:
                continue

            score = 0
            if normalized_plate and observation.registration_number == normalized_plate:
                score += 8
            if event_ids and observation.event_id in event_ids:
                score += 6
            if camera_id and observation.camera_id == camera_id:
                score += 2
            if device_id and observation.device_id == device_id:
                score += 2
            if not score:
                continue
            candidates.append((score, -age_seconds, observation))

        if not candidates:
            return None
        _score, age_sort, best = max(candidates, key=lambda item: (item[0], item[1]))
        payload = asdict(best)
        payload["age_seconds"] = abs(age_sort)
        return payload

    async def _append_and_publish(self, observation: VehiclePresenceObservation) -> None:
        async with self._lock:
            self._observations.append(observation)
        await event_bus.publish(
            "vehicle_presence.observed",
            {"observation": asdict(observation)},
        )


def _has_later_vehicle_presence_end(
    active_observation: VehiclePresenceObservation,
    observations: list[VehiclePresenceObservation],
    checked_at: datetime,
) -> bool:
    active_at = _datetime_from_any(active_observation.observed_at)
    if active_at is None:
        return False
    for observation in observations:
        if observation.active:
            continue
        if not _presence_identity_overlaps(active_observation, observation):
            continue
        ended_at = _datetime_from_any(observation.ended_at or observation.observed_at)
        if ended_at and active_at <= ended_at <= checked_at.astimezone(UTC):
            return True
    return False


def _presence_identity_overlaps(
    left: VehiclePresenceObservation,
    right: VehiclePresenceObservation,
) -> bool:
    if left.event_id and right.event_id and left.event_id == right.event_id:
        return True
    if left.camera_id and right.camera_id and left.camera_id == right.camera_id:
        return True
    if left.device_id and right.device_id and left.device_id == right.device_id:
        return True
    if left.registration_number and right.registration_number and left.registration_number == right.registration_number:
        return True
    return False


def _presence_from_lpr_payload(
    payload: Any,
    *,
    registration_number: str | None,
    received_at: datetime,
) -> VehiclePresenceObservation | None:
    event_id = _string_or_none(_dict_deep_first(payload, ("eventId", "event_id")))
    camera_id = _string_or_none(_dict_deep_first(payload, ("cameraId", "camera_id")))
    device_id = _string_or_none(_dict_deep_first(payload, ("device", "deviceId", "device_id")))
    captured_at = _datetime_from_any(_dict_deep_first(payload, ("capturedAt", "captured_at", "timestamp", "time")))
    observed_at = captured_at or received_at
    plate = _normalize_plate(registration_number or str(_dict_deep_first(payload, PLATE_KEYS) or ""))
    if not (plate or event_id or camera_id or device_id):
        return None
    return VehiclePresenceObservation(
        id=str(uuid.uuid4()),
        source="webhook",
        source_detail="ubiquiti_lpr_webhook",
        active=True,
        observed_at=_isoformat(observed_at) or _isoformat(received_at) or "",
        registration_number=plate or None,
        event_id=event_id,
        camera_id=camera_id,
        device_id=device_id,
        payload_path="payload",
    )


def _presence_from_realtime_payload(
    payload: dict[str, Any],
    *,
    received_at: datetime,
) -> list[VehiclePresenceObservation]:
    observations: list[VehiclePresenceObservation] = []
    camera = payload.get("camera")
    if isinstance(camera, dict):
        active_labels = {
            _normalize_detection_label(item)
            for item in (((camera.get("detections") or {}).get("active")) or [])
        }
        explicit_active = [
            bool(_dict_deep_first(camera, (field,)))
            for field in VEHICLE_PRESENCE_ACTIVE_FIELDS
            if _dict_deep_first(camera, (field,)) is not None
        ]
        if active_labels & VEHICLE_PRESENCE_ACTIVE_LABELS or any(explicit_active):
            observations.append(
                VehiclePresenceObservation(
                    id=str(uuid.uuid4()),
                    source="uiprotect_camera",
                    source_detail="camera.current_vehicle_detection",
                    active=True,
                    observed_at=_isoformat(received_at) or "",
                    camera_id=_string_or_none(camera.get("id")),
                    camera_name=_string_or_none(camera.get("name")),
                    payload_path="camera.detections.active",
                )
            )
        elif explicit_active and not any(explicit_active):
            observations.append(
                VehiclePresenceObservation(
                    id=str(uuid.uuid4()),
                    source="uiprotect_camera",
                    source_detail="camera.current_vehicle_detection",
                    active=False,
                    observed_at=_isoformat(received_at) or "",
                    camera_id=_string_or_none(camera.get("id")),
                    camera_name=_string_or_none(camera.get("name")),
                    ended_at=_isoformat(received_at),
                    payload_path="camera.detections.active",
                )
            )

    event = payload.get("event")
    if isinstance(event, dict):
        smart_types = {
            _normalize_detection_label(item)
            for item in (event.get("smart_detect_types") or [])
        }
        if smart_types & VEHICLE_PRESENCE_TYPES:
            ended_at = _datetime_from_any(event.get("end"))
            start = _datetime_from_any(event.get("start"))
            observations.append(
                VehiclePresenceObservation(
                    id=str(uuid.uuid4()),
                    source="uiprotect_event",
                    source_detail="event.vehicle_detection",
                    active=ended_at is None,
                    observed_at=_isoformat(ended_at or start or received_at) or "",
                    event_id=_string_or_none(event.get("id")),
                    camera_id=_string_or_none(event.get("camera_id")),
                    camera_name=_string_or_none(event.get("camera_name")),
                    ended_at=_isoformat(ended_at),
                    payload_path="event.smart_detect_types",
                )
            )
    return observations


def _presence_from_track(
    track: Any,
    *,
    event: Any,
    event_id: str | None,
    received_at: datetime,
    probe_attempt: int | None,
) -> list[VehiclePresenceObservation]:
    raw_track = _model_to_debug_dict(track)
    if not isinstance(raw_track, dict):
        return []
    payload = raw_track.get("payload")
    if not isinstance(payload, list):
        return []

    common = _protect_common_fields(event, action="track_probe", model="event", received_at=received_at)
    resolved_event_id = (
        event_id
        or _string_or_none(raw_track.get("eventId") or raw_track.get("event_id"))
        or common.get("event_id")
    )
    camera_id = _string_or_none(raw_track.get("cameraId") or raw_track.get("camera_id")) or common.get("camera_id")
    detail = "smart_detect_track.vehicle"
    if probe_attempt is not None:
        detail = f"{detail}.attempt_{probe_attempt}"

    observations: list[VehiclePresenceObservation] = []
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            continue
        object_type = _normalize_detection_label(_dict_get_any(row, ("objectType", "object_type", "type")))
        plate = _normalize_plate(str(_dict_get_any(row, PLATE_KEYS) or ""))
        has_vehicle_attributes = bool(_dict_get_any(row, ("attributes", "attrs")))
        if object_type not in VEHICLE_PRESENCE_TYPES and not plate and not has_vehicle_attributes:
            continue
        observed_at = _datetime_from_any(_dict_get_any(row, ("timestamp", "capturedAt", "captured_at", "time")))
        observations.append(
            VehiclePresenceObservation(
                id=str(uuid.uuid4()),
                source="uiprotect_track",
                source_detail=detail,
                active=True,
                observed_at=_isoformat(observed_at or received_at) or "",
                registration_number=plate or None,
                event_id=resolved_event_id,
                camera_id=camera_id,
                camera_name=common.get("camera_name"),
                payload_path=f"smartDetectTrack.payload[{index}]",
            )
        )
    return observations


def _normalize_detection_label(value: Any) -> str:
    return str(_enum_value(value) or value or "").strip().replace("-", "_").replace(" ", "_").lower()


def extract_unifi_protect_vehicle_visual_observations(
    message: Any,
    *,
    received_at: datetime | None = None,
) -> list[VehicleVisualObservation]:
    received_at = received_at or _now_utc()
    changed_data = getattr(message, "changed_data", {}) or {}
    new_obj = getattr(message, "new_obj", None)
    action = _enum_value(getattr(message, "action", None))
    model = str(
        _dict_get(changed_data, "modelKey")
        or _enum_value(getattr(new_obj, "model", None))
        or ""
    )
    common = _protect_common_fields(new_obj, action=action, model=model, received_at=received_at)

    observations: list[VehicleVisualObservation] = []
    observations.extend(_observations_from_event_object(new_obj, common))
    observations.extend(_observations_from_payload(_model_to_debug_dict(new_obj), "new_obj", common))
    observations.extend(_observations_from_payload(changed_data, "changed_data", common))
    return _dedupe_observations(observations)


def extract_unifi_payload_vehicle_visual_observations(
    payload: Any,
    *,
    registration_number: str | None = None,
    received_at: datetime | None = None,
) -> list[VehicleVisualObservation]:
    common = {
        "event_id": _string_or_none(_dict_deep_first(payload, ("eventId", "event_id", "id"))),
        "camera_id": _string_or_none(_dict_deep_first(payload, ("cameraId", "camera_id"))),
        "camera_name": _string_or_none(_dict_deep_first(payload, ("cameraName", "camera_name"))),
        "received_at": _isoformat(received_at or _now_utc()),
    }
    observations = _observations_from_payload(payload, "payload", common)
    if registration_number:
        normalized_plate = _normalize_plate(registration_number)
        observations = [
            _observation_with_plate(observation, normalized_plate)
            if not observation.registration_number
            else observation
            for observation in observations
        ]
    return _dedupe_observations(observations)


def extract_unifi_protect_track_vehicle_visual_observations(
    track: Any,
    *,
    event: Any = None,
    event_id: str | None = None,
    received_at: datetime | None = None,
    probe_attempt: int | None = None,
) -> list[VehicleVisualObservation]:
    received_at = received_at or _now_utc()
    raw_track = _model_to_debug_dict(track)
    if not isinstance(raw_track, dict):
        return []
    payload = raw_track.get("payload")
    if not isinstance(payload, list):
        return []

    common = _protect_common_fields(event, action="track_probe", model="event", received_at=received_at)
    common["event_id"] = event_id or _string_or_none(raw_track.get("eventId") or raw_track.get("event_id")) or common.get("event_id")
    common["camera_id"] = _string_or_none(raw_track.get("cameraId") or raw_track.get("camera_id")) or common.get("camera_id")
    detail = "smart_detect_track.attributes"
    if probe_attempt is not None:
        detail = f"{detail}.attempt_{probe_attempt}"

    observations: list[VehicleVisualObservation] = []
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            continue
        observation = _observation_from_mapping(
            row,
            common=common,
            source="uiprotect_track",
            source_detail=detail,
            path=f"smartDetectTrack.payload[{index}]",
        )
        if observation is not None:
            observations.append(observation)
    return _dedupe_observations(observations)


def _observations_from_event_object(
    event: Any,
    common: dict[str, Any],
) -> list[VehicleVisualObservation]:
    if event is None:
        return []
    metadata = getattr(event, "metadata", None)
    thumbnails = list(getattr(metadata, "detected_thumbnails", None) or [])
    observations: list[VehicleVisualObservation] = []
    for index, thumbnail in enumerate(thumbnails):
        observation = _observation_from_thumbnail(thumbnail, event, common, index)
        if observation is not None:
            observations.append(observation)
    return observations


def _observation_from_thumbnail(
    thumbnail: Any,
    event: Any,
    common: dict[str, Any],
    index: int,
) -> VehicleVisualObservation | None:
    attributes = _object_or_dict_value(thumbnail, "attributes")
    color = _attribute_pair(attributes, VEHICLE_COLOR_KEYS)
    vehicle_type = _attribute_pair(attributes, VEHICLE_TYPE_KEYS)
    if color is None and vehicle_type is None:
        return None
    base_path = f"new_obj.metadata.detected_thumbnails[{index}]"
    return _build_observation(
        source="uiprotect_event",
        source_detail="event_thumbnail.attributes",
        common=common,
        registration_number=_plate_from_thumbnail(thumbnail),
        captured_at=_isoformat(
            _datetime_from_any(
                _object_or_dict_value(thumbnail, "clock_best_wall")
                or _object_or_dict_value(thumbnail, "clockBestWall")
                or getattr(event, "start", None)
            )
        ),
        vehicle_type=vehicle_type,
        color=color,
        payload_path=f"{base_path}.attributes",
    )


def _observations_from_payload(
    value: Any,
    path: str,
    common: dict[str, Any],
) -> list[VehicleVisualObservation]:
    observations: list[VehicleVisualObservation] = []
    if isinstance(value, dict):
        if not path.lower().endswith((".attributes", ".attrs")):
            observation = _observation_from_mapping(
                value,
                common=common,
                source="uiprotect_payload",
                source_detail="payload.attributes",
                path=path,
            )
            if observation is not None:
                observations.append(observation)
        for key, item in value.items():
            observations.extend(_observations_from_payload(item, f"{path}.{key}", common))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            observations.extend(_observations_from_payload(item, f"{path}[{index}]", common))
    return observations


def _observation_from_mapping(
    value: dict[str, Any],
    *,
    common: dict[str, Any],
    source: str,
    source_detail: str,
    path: str,
) -> VehicleVisualObservation | None:
    attributes = _dict_get_any(value, ("attributes", "attrs")) or value
    color = _attribute_pair(attributes, VEHICLE_COLOR_KEYS)
    vehicle_type = _attribute_pair(attributes, VEHICLE_TYPE_KEYS)
    if color is None and vehicle_type is None:
        return None
    if not _looks_like_vehicle_visual_context(value, path, color=color, vehicle_type=vehicle_type):
        return None
    captured_at = _isoformat(
        _datetime_from_any(
            _dict_get_any(
                value,
                (
                    "clockBestWall",
                    "clock_best_wall",
                    "capturedAt",
                    "captured_at",
                    "start",
                    "timestamp",
                    "time",
                ),
            )
        )
    )
    return _build_observation(
        source=source,
        source_detail=source_detail,
        common=common,
        registration_number=_plate_from_mapping(value),
        captured_at=captured_at,
        vehicle_type=vehicle_type,
        color=color,
        payload_path=f"{path}.attributes" if attributes is not value else path,
    )


def _build_observation(
    *,
    source: str,
    source_detail: str,
    common: dict[str, Any],
    registration_number: str | None,
    captured_at: str | None,
    vehicle_type: tuple[str, float | None] | None,
    color: tuple[str, float | None] | None,
    payload_path: str | None,
) -> VehicleVisualObservation | None:
    observed_type = _normalize_vehicle_type(vehicle_type[0] if vehicle_type else None)
    observed_color = _normalize_vehicle_color(color[0] if color else None)
    if not observed_type and not observed_color:
        return None
    return VehicleVisualObservation(
        id=str(uuid.uuid4()),
        source=source,
        source_detail=source_detail,
        observed_vehicle_type=observed_type,
        observed_vehicle_color=observed_color,
        registration_number=_normalize_plate(registration_number or ""),
        received_at=str(common.get("received_at") or _isoformat(_now_utc())),
        captured_at=captured_at,
        event_id=_string_or_none(common.get("event_id")),
        camera_id=_string_or_none(common.get("camera_id")),
        camera_name=_string_or_none(common.get("camera_name")),
        vehicle_type_confidence=_float_or_none(vehicle_type[1] if vehicle_type else None),
        vehicle_color_confidence=_float_or_none(color[1] if color else None),
        payload_path=payload_path,
    )


def _attribute_pair(attributes: Any, keys: tuple[str, ...]) -> tuple[str, float | None] | None:
    if attributes is None:
        return None
    get_value = getattr(attributes, "get_value", None)
    if callable(get_value):
        for key in keys:
            value = get_value(key)
            if value:
                raw_attr = _object_or_dict_value(attributes, key)
                return str(value), _attribute_confidence(raw_attr)

    raw = _model_to_debug_dict(attributes)
    if not isinstance(raw, dict):
        return None
    normalized_keys = {_normalize_key(key) for key in keys}
    for key, value in raw.items():
        if _normalize_key(key) not in normalized_keys:
            continue
        text = _attribute_text(value)
        if text:
            return text, _attribute_confidence(value)
    return None


def _attribute_text(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("val", "value", "name", "label"):
            if value.get(key):
                return str(value[key])
        return None
    raw_value = getattr(value, "val", None) or getattr(value, "value", None)
    if raw_value:
        return str(raw_value)
    if isinstance(value, str | int | float):
        return str(value)
    return None


def _attribute_confidence(value: Any) -> float | None:
    if isinstance(value, dict):
        return _float_or_none(value.get("confidence"))
    return _float_or_none(getattr(value, "confidence", None))


def _looks_like_vehicle_visual_context(
    value: dict[str, Any],
    path: str,
    *,
    color: tuple[str, float | None] | None,
    vehicle_type: tuple[str, float | None] | None,
) -> bool:
    if vehicle_type is not None:
        return True
    type_value = str(_dict_get_any(value, ("type", "objectType", "object_type")) or "").lower()
    if type_value in {"vehicle", "licenseplate", "license_plate"}:
        return True
    smart_types = _dict_get_any(value, ("smartDetectTypes", "smart_detect_types"))
    if isinstance(smart_types, list) and any(
        str(_enum_value(item) or item).lower() in {"vehicle", "licenseplate"}
        for item in smart_types
    ):
        return True
    return bool(color and any(token in path.lower() for token in ("vehicle", "thumbnail", "smartdetect")))


def _plate_from_thumbnail(thumbnail: Any) -> str | None:
    group = _object_or_dict_value(thumbnail, "group")
    for key in ("matchedName", "matched_name", "name"):
        value = _object_or_dict_value(group, key)
        if value:
            return str(value)
    for key in ("name", "licensePlate", "license_plate", "plate"):
        value = _object_or_dict_value(thumbnail, key)
        if value:
            return str(value)
    attributes = _object_or_dict_value(thumbnail, "attributes")
    raw_attributes = _model_to_debug_dict(attributes)
    if isinstance(raw_attributes, dict):
        return _plate_from_mapping(raw_attributes)
    return None


def _plate_from_mapping(value: dict[str, Any]) -> str | None:
    found = _dict_get_any(value, PLATE_KEYS)
    if found is None:
        group = _dict_get_any(value, ("group",))
        if isinstance(group, dict):
            found = _dict_get_any(group, ("matchedName", "matched_name", "name"))
    if isinstance(found, list):
        found = next((item for item in found if isinstance(item, str) and _normalize_plate(item)), None)
    return str(found) if found else None


def _protect_common_fields(
    event: Any,
    *,
    action: str | None,
    model: str | None,
    received_at: datetime,
) -> dict[str, Any]:
    camera = getattr(event, "camera", None)
    return {
        "event_id": str(getattr(event, "id", "") or "") or None,
        "camera_id": str(getattr(event, "camera_id", "") or "") or None,
        "camera_name": str(getattr(camera, "display_name", "") or getattr(camera, "name", "") or "") or None,
        "protect_action": action,
        "protect_model": model,
        "received_at": _isoformat(received_at),
    }


def _dedupe_observations(observations: list[VehicleVisualObservation]) -> list[VehicleVisualObservation]:
    deduped: list[VehicleVisualObservation] = []
    seen: set[tuple[Any, ...]] = set()
    for observation in observations:
        key = (
            observation.registration_number,
            observation.event_id,
            observation.camera_id,
            observation.captured_at,
            observation.observed_vehicle_type,
            observation.observed_vehicle_color,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(observation)
    return deduped


def _observation_with_plate(
    observation: VehicleVisualObservation,
    registration_number: str,
) -> VehicleVisualObservation:
    payload = asdict(observation)
    payload["registration_number"] = registration_number
    return VehicleVisualObservation(**payload)


def _time_distance_seconds(
    observation: VehicleVisualObservation,
    occurred_at: datetime | None,
) -> float | None:
    if occurred_at is None:
        return None
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)
    candidates = [
        _datetime_from_any(observation.captured_at),
        _datetime_from_any(observation.received_at),
    ]
    distances = [
        abs((candidate.astimezone(UTC) - occurred_at.astimezone(UTC)).total_seconds())
        for candidate in candidates
        if candidate is not None
    ]
    return min(distances) if distances else None


def _normalize_vehicle_type(value: Any) -> str:
    text = _clean_label(value)
    if not text:
        return ""
    normalized = _normalize_key(text)
    mapping = {
        "car": "Car",
        "sedan": "Car",
        "saloon": "Car",
        "coupe": "Car",
        "hatchback": "Car",
        "convertible": "Car",
        "estate": "Car",
        "wagon": "Car",
        "suv": "SUV",
        "crossover": "SUV",
        "van": "Van",
        "minivan": "Van",
        "truck": "Truck",
        "pickup": "Truck",
        "pickuptruck": "Truck",
        "lorry": "Truck",
        "motorcycle": "Motorcycle",
        "motorbike": "Motorcycle",
        "bike": "Motorcycle",
        "bus": "Bus",
        "vehicle": "Vehicle",
    }
    return mapping.get(normalized, text.title())


def _normalize_vehicle_color(value: Any) -> str:
    text = _clean_label(value)
    if not text:
        return ""
    normalized = _normalize_key(text)
    mapping = {
        "gray": "Grey",
        "grey": "Grey",
        "silver": "Silver",
        "white": "White",
        "black": "Black",
        "blue": "Blue",
        "red": "Red",
        "green": "Green",
        "yellow": "Yellow",
        "orange": "Orange",
        "brown": "Brown",
        "beige": "Beige",
        "gold": "Gold",
        "purple": "Purple",
    }
    return mapping.get(normalized, text.title())


def _clean_label(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("_", " ").replace("-", " ")).strip()


def _normalize_plate(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value).upper()


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _object_or_dict_value(value: Any, key: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return _dict_get_any(value, (key,))
    if hasattr(value, key):
        return getattr(value, key)
    snake_key = re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
    if hasattr(value, snake_key):
        return getattr(value, snake_key)
    raw = _model_to_debug_dict(value)
    if isinstance(raw, dict):
        return _dict_get_any(raw, (key, snake_key))
    return None


def _dict_get_any(value: dict[str, Any], keys: tuple[str, ...]) -> Any:
    normalized_keys = {_normalize_key(key) for key in keys}
    for key, item in value.items():
        if _normalize_key(key) in normalized_keys:
            return item
    return None


def _dict_deep_first(value: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        direct = _dict_get_any(value, keys)
        if direct is not None:
            return direct
        for item in value.values():
            found = _dict_deep_first(item, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _dict_deep_first(item, keys)
            if found is not None:
                return found
    return None


def _dict_get(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def _model_to_debug_dict(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_model_to_debug_dict(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _model_to_debug_dict(item) for key, item in value.items()}
    unifi_dict = getattr(value, "unifi_dict", None)
    if callable(unifi_dict):
        try:
            return unifi_dict()
        except Exception:
            pass
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json")
        except Exception:
            pass
    try:
        return {
            key: _model_to_debug_dict(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    except TypeError:
        return str(value)


def _datetime_from_any(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, int | float):
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


@lru_cache
def get_vehicle_visual_detection_recorder() -> VehicleVisualDetectionRecorder:
    return VehicleVisualDetectionRecorder()


@lru_cache
def get_vehicle_presence_tracker() -> VehiclePresenceTracker:
    return VehiclePresenceTracker()
