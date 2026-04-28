import asyncio
import json
import re
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from app.modules.lpr.base import PlateRead
from app.services.event_bus import event_bus


MAX_LPR_TIMING_OBSERVATIONS = 2000
PLATE_VALUE_KEYS = {
    "detectedlicenseplate",
    "detected_license_plate",
    "licenseplate",
    "license_plate",
    "licenceplate",
    "licence_plate",
    "matchedname",
    "matched_name",
    "names_top_k",
    "namestopk",
    "ocr",
    "plate",
    "plate_number",
    "platenumber",
    "recognized_plate",
    "recognizedplate",
    "registration",
    "registration_number",
    "registrationnumber",
    "topkcandidate",
    "top_k_candidate",
    "vrn",
}
PLATE_KEY_MARKERS = ("license", "licence", "plate", "lpr", "registration", "vrn", "matched", "candidate", "ocr")
CONTEXT_VALUE_KEYS = {"name", "value", "val", "text", "matchedname", "matched_name"}
OBJECT_PLATE_ATTRS = (
    "license_plate",
    "licensePlate",
    "licence_plate",
    "licencePlate",
    "plate",
    "plate_number",
    "plateNumber",
    "registration",
    "registration_number",
    "registrationNumber",
    "vrn",
    "matched_name",
    "matchedName",
    "recognized_plate",
    "recognizedPlate",
    "detected_license_plate",
    "detectedLicensePlate",
    "current_license_plate",
    "currentLicensePlate",
    "last_license_plate",
    "lastLicensePlate",
    "top_k_candidate",
    "topKCandidate",
    "names_top_k",
    "namesTopK",
)


@dataclass(frozen=True)
class LprTimingObservation:
    id: str
    source: str
    source_detail: str
    registration_number: str
    received_at: str
    raw_value: str | None = None
    candidate_kind: str = "normalized_plate"
    captured_at: str | None = None
    event_id: str | None = None
    camera_id: str | None = None
    camera_name: str | None = None
    confidence: float | None = None
    confidence_scale: str | None = None
    protect_action: str | None = None
    protect_model: str | None = None
    smart_detect_types: list[str] | None = None
    payload_path: str | None = None


class LprTimingRecorder:
    """In-memory diagnostic timing feed for comparing LPR sources."""

    def __init__(self) -> None:
        self._observations: deque[LprTimingObservation] = deque(maxlen=MAX_LPR_TIMING_OBSERVATIONS)
        self._lock = asyncio.Lock()

    async def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        async with self._lock:
            observations = list(self._observations)[-limit:]
        return [asdict(observation) for observation in reversed(observations)]

    async def clear(self) -> None:
        async with self._lock:
            self._observations.clear()
        await event_bus.publish("lpr_timing.cleared", {})

    async def record_webhook_plate(self, read: PlateRead, *, received_at: datetime | None = None) -> None:
        observation = LprTimingObservation(
            id=str(uuid.uuid4()),
            source="webhook",
            source_detail="ubiquiti_lpr_webhook",
            registration_number=_normalize_plate(read.registration_number),
            received_at=_isoformat(received_at or _now_utc()),
            raw_value=read.registration_number,
            captured_at=_isoformat(read.captured_at),
            confidence=read.confidence,
            confidence_scale="0_1",
        )
        await self._append_and_publish(observation)

    async def record_unifi_protect_message(self, message: Any, *, received_at: datetime | None = None) -> None:
        observations = extract_unifi_protect_lpr_observations(message, received_at=received_at)
        for observation in observations:
            await self._append_and_publish(observation)

    async def record_unifi_protect_track(
        self,
        track: Any,
        *,
        event: Any = None,
        event_id: str | None = None,
        received_at: datetime | None = None,
        probe_attempt: int | None = None,
    ) -> int:
        observations = extract_unifi_protect_track_observations(
            track,
            event=event,
            event_id=event_id,
            received_at=received_at,
            probe_attempt=probe_attempt,
        )
        for observation in observations:
            await self._append_and_publish(observation)
        return len(observations)

    async def _append_and_publish(self, observation: LprTimingObservation) -> None:
        async with self._lock:
            self._observations.append(observation)
        await event_bus.publish("lpr_timing.observed", {"observation": asdict(observation)})


def extract_unifi_protect_lpr_observations(
    message: Any,
    *,
    received_at: datetime | None = None,
) -> list[LprTimingObservation]:
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

    observations: list[LprTimingObservation] = []
    observations.extend(_observations_from_object_attributes(new_obj, common))
    observations.extend(_observations_from_object_dict(new_obj, common))
    observations.extend(_observations_from_event_object(new_obj, common))
    observations.extend(_observations_from_changed_data(changed_data, common))
    return _dedupe_observations(observations)


def extract_unifi_protect_track_observations(
    track: Any,
    *,
    event: Any = None,
    event_id: str | None = None,
    received_at: datetime | None = None,
    probe_attempt: int | None = None,
) -> list[LprTimingObservation]:
    received_at = received_at or _now_utc()
    raw_track = _model_to_debug_dict(track)
    if not isinstance(raw_track, dict):
        return []
    payload = raw_track.get("payload")
    if not isinstance(payload, list):
        return []

    resolved_event_id = event_id or _track_event_id(raw_track) or str(getattr(event, "id", "") or "") or None
    common = _protect_common_fields(event, action="track_probe", model="event", received_at=received_at)
    common["event_id"] = resolved_event_id or common["event_id"]

    observations: list[LprTimingObservation] = []
    seen_plates: set[str] = set()
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            continue
        key, value = _track_plate_value(row)
        if value is None:
            continue
        plate = _plate_from_value(value)
        if not plate or plate in seen_plates:
            continue
        seen_plates.add(plate)
        path = f"smartDetectTrack.payload[{index}].{key}"
        detail = f"smart_detect_track.{key}"
        if probe_attempt is not None:
            detail = f"{detail}.attempt_{probe_attempt}"
        observation = _observation_from_candidate(
            value,
            source="uiprotect_track",
            source_detail=detail,
            payload_path=path,
            common=common,
            captured_at=_isoformat(_datetime_from_unix_ms(row.get("timestamp"))),
            confidence=_float_or_none(row.get("confidence")),
            confidence_scale="0_100",
        )
        if observation is not None:
            observations.append(observation)
    return _dedupe_observations(observations)


def _observations_from_object_attributes(
    obj: Any,
    common: dict[str, Any],
) -> list[LprTimingObservation]:
    if obj is None:
        return []
    observations: list[LprTimingObservation] = []
    for attr in OBJECT_PLATE_ATTRS:
        if not hasattr(obj, attr):
            continue
        value = getattr(obj, attr, None)
        observation = _observation_from_candidate(
            value,
            source_detail=f"object_attribute.{attr}",
            payload_path=f"new_obj.{attr}",
            common=common,
            confidence=_float_or_none(
                getattr(obj, "license_plate_confidence", None)
                or getattr(obj, "plate_confidence", None)
                or getattr(obj, "confidence", None)
            ),
            confidence_scale="0_100",
        )
        if observation is not None:
            observations.append(observation)
    return observations


def _observations_from_object_dict(
    obj: Any,
    common: dict[str, Any],
) -> list[LprTimingObservation]:
    raw = _model_to_debug_dict(obj)
    if not isinstance(raw, dict):
        return []
    return [
        observation
        for path, key, value in _walk_lpr_candidate_values(raw, "new_obj")
        if (
            observation := _observation_from_candidate(
                value,
                source_detail=f"object_payload.{key}",
                payload_path=path,
                common=common,
            )
        )
        is not None
    ]


def _observations_from_event_object(
    event: Any,
    common: dict[str, Any],
) -> list[LprTimingObservation]:
    metadata = getattr(event, "metadata", None)
    thumbnails = list(getattr(metadata, "detected_thumbnails", None) or [])
    observations: list[LprTimingObservation] = []
    for index, thumbnail in enumerate(thumbnails):
        captured_at = _isoformat(getattr(thumbnail, "clock_best_wall", None) or getattr(event, "start", None))
        observations.extend(
            _thumbnail_candidate_observations(
                thumbnail,
                common=common,
                index=index,
                captured_at=captured_at,
            )
        )
    return observations


def _observations_from_changed_data(
    changed_data: Any,
    common: dict[str, Any],
) -> list[LprTimingObservation]:
    observations: list[LprTimingObservation] = []
    for path, key, value in _walk_lpr_candidate_values(changed_data):
        observation = _observation_from_candidate(
            value,
            source_detail=f"websocket.changed_data.{key}",
            payload_path=path,
            common=common,
        )
        if observation is not None:
            observations.append(observation)
    return observations


def _thumbnail_candidate_observations(
    thumbnail: Any,
    *,
    common: dict[str, Any],
    index: int,
    captured_at: str | None,
) -> list[LprTimingObservation]:
    observations: list[LprTimingObservation] = []
    base_path = f"new_obj.metadata.detected_thumbnails[{index}]"

    group = _object_or_dict_value(thumbnail, "group")
    group_confidence = _float_or_none(_object_or_dict_value(group, "confidence"))
    for attr, detail, confidence in (
        ("matchedName", "event_thumbnail.group.matchedName", group_confidence),
        ("matched_name", "event_thumbnail.group.matched_name", group_confidence),
        ("name", "event_thumbnail.group.name", group_confidence),
    ):
        value = _object_or_dict_value(group, attr)
        if value is None:
            continue
        observation = _observation_from_candidate(
            value,
            source_detail=detail,
            payload_path=f"{base_path}.group.{attr}",
            common=common,
            captured_at=captured_at,
            confidence=confidence,
            confidence_scale="0_100",
        )
        if observation is not None:
            observations.append(observation)

    if hasattr(thumbnail, "name"):
        observation = _observation_from_candidate(
            getattr(thumbnail, "name", None),
            source_detail="event_thumbnail.name",
            payload_path=f"{base_path}.name",
            common=common,
            captured_at=captured_at,
            confidence=_float_or_none(getattr(thumbnail, "confidence", None)),
            confidence_scale="0_100",
        )
        if observation is not None:
            observations.append(observation)

    attributes = getattr(thumbnail, "attributes", None)
    for key in ("matchedName", "matched_name", "topKCandidate", "top_k_candidate", "namesTopK", "names_top_k"):
        value = _object_or_dict_value(attributes, key)
        if value is None:
            continue
        observation = _observation_from_candidate(
            value,
            source_detail=f"event_thumbnail.attributes.{key}",
            payload_path=f"{base_path}.attributes.{key}",
            common=common,
            captured_at=captured_at,
            confidence=_float_or_none(getattr(value, "confidence", None) or getattr(thumbnail, "confidence", None)),
            confidence_scale="0_100",
        )
        if observation is not None:
            observations.append(observation)

    raw_thumbnail = _model_to_debug_dict(thumbnail)
    for path, key, value in _walk_lpr_candidate_values(raw_thumbnail, base_path):
        observation = _observation_from_candidate(
            value,
            source_detail=f"event_thumbnail.payload.{key}",
            payload_path=path,
            common=common,
            captured_at=captured_at,
            confidence=_float_or_none(getattr(thumbnail, "confidence", None)),
            confidence_scale="0_100",
        )
        if observation is not None:
            observations.append(observation)
    return observations


def _observation_from_candidate(
    value: Any,
    *,
    source: str = "uiprotect",
    source_detail: str,
    payload_path: str,
    common: dict[str, Any],
    captured_at: str | None = None,
    confidence: float | None = None,
    confidence_scale: str | None = None,
) -> LprTimingObservation | None:
    raw_value = _debug_value(value)
    if raw_value is None:
        return None
    plate = _plate_from_value(value) or ""
    return LprTimingObservation(
        id=str(uuid.uuid4()),
        source=source,
        source_detail=source_detail,
        registration_number=plate,
        raw_value=raw_value,
        candidate_kind="normalized_plate" if plate else "possible_lpr_field",
        captured_at=captured_at,
        confidence=confidence,
        confidence_scale=confidence_scale,
        payload_path=payload_path,
        **common,
    )


def _track_plate_value(row: dict[str, Any]) -> tuple[str, Any] | tuple[None, None]:
    for key in ("licensePlate", "license_plate", "name", "matchedName", "matched_name"):
        value = row.get(key)
        if value:
            return key, value
    return None, None


def _track_event_id(track: dict[str, Any]) -> str | None:
    event = track.get("event")
    if isinstance(event, str):
        return event
    if isinstance(event, dict):
        event_id = event.get("id")
        return str(event_id) if event_id else None
    return None


def _object_or_dict_value(value: Any, key: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(key)
    if hasattr(value, key):
        return getattr(value, key)
    raw = _model_to_debug_dict(value)
    if isinstance(raw, dict):
        return raw.get(key)
    return None


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
            return None
    return None


def _debug_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int | float | bool):
        return str(value)
    raw = _model_to_debug_dict(value)
    if raw is None:
        raw = str(value)
    try:
        serialized = json.dumps(raw, default=str, sort_keys=True, separators=(",", ":"))
    except TypeError:
        serialized = str(raw)
    if len(serialized) > 1200:
        return f"{serialized[:1200]}..."
    return serialized


def _walk_lpr_candidate_values(
    value: Any,
    path: str = "changed_data",
    *,
    lpr_context: bool = False,
) -> list[tuple[str, str, Any]]:
    if isinstance(value, dict):
        matches: list[tuple[str, str, Any]] = []
        for key, item in value.items():
            key_text = str(key)
            normalized_key = _normalize_key(key_text)
            next_path = f"{path}.{key_text}"
            key_is_lpr = _is_lpr_candidate_key(key_text) or _path_has_lpr_context(path)
            value_key_in_context = lpr_context and normalized_key in CONTEXT_VALUE_KEYS
            next_context = lpr_context or key_is_lpr
            if key_is_lpr or value_key_in_context:
                matches.append((next_path, key_text, item))
            matches.extend(_walk_lpr_candidate_values(item, next_path, lpr_context=next_context))
        return matches
    if isinstance(value, list):
        matches = []
        for index, item in enumerate(value):
            item_path = f"{path}[{index}]"
            if lpr_context and not isinstance(item, dict | list):
                matches.append((item_path, path.rsplit(".", 1)[-1], item))
            matches.extend(_walk_lpr_candidate_values(item, item_path, lpr_context=lpr_context))
        return matches
    return []


def _is_lpr_candidate_key(key: str) -> bool:
    normalized = _normalize_key(key)
    compact = normalized.replace("_", "")
    return normalized in PLATE_VALUE_KEYS or compact in PLATE_VALUE_KEYS or any(marker in compact for marker in PLATE_KEY_MARKERS)


def _path_has_lpr_context(path: str) -> bool:
    normalized = _normalize_key(path).replace("_", "")
    return any(marker in normalized for marker in PLATE_KEY_MARKERS)


def _protect_common_fields(
    obj: Any,
    *,
    action: str | None,
    model: str | None,
    received_at: datetime,
) -> dict[str, Any]:
    is_camera = bool(obj is not None and (str(model or "").lower() == "camera" or hasattr(obj, "channels")))
    camera = obj if is_camera else getattr(obj, "camera", None)
    camera_id = str(
        (getattr(obj, "id", None) if is_camera else None)
        or getattr(obj, "camera_id", "")
        or getattr(camera, "id", "")
        or ""
    ) or None
    camera_name = str(
        getattr(camera, "display_name", "")
        or getattr(camera, "name", "")
        or ""
    ) or None
    return {
        "received_at": _isoformat(received_at),
        "event_id": None if is_camera else str(getattr(obj, "id", "") or "") or None,
        "camera_id": camera_id,
        "camera_name": camera_name,
        "protect_action": action,
        "protect_model": model,
        "smart_detect_types": [_enum_value(item) for item in getattr(obj, "smart_detect_types", [])],
    }


def _dedupe_observations(observations: list[LprTimingObservation]) -> list[LprTimingObservation]:
    seen: set[tuple[str, str | None, str, str | None, str | None]] = set()
    unique: list[LprTimingObservation] = []
    for observation in observations:
        key = (
            observation.source,
            observation.event_id,
            observation.registration_number,
            observation.payload_path,
            observation.raw_value,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(observation)
    return unique


def _plate_from_value(value: Any) -> str | None:
    if value is None:
        return None
    candidate = _text_value(value)
    if candidate is None:
        return None
    if candidate.lower() in {
        "false",
        "licenseplate",
        "license plate",
        "licenceplate",
        "licence plate",
        "true",
        "vehicle",
        "unknown",
    }:
        return None
    if _looks_like_numeric_score(candidate):
        return None
    cleaned = _normalize_plate(candidate)
    if not 3 <= len(cleaned) <= 12:
        return None
    return cleaned


def _text_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, list):
        for item in value:
            nested = _text_value(item)
            if nested and _plate_from_value(nested):
                return nested
        return _text_value(value[0]) if value else None
    if isinstance(value, dict):
        for key in (
            "matchedName",
            "matched_name",
            "licensePlate",
            "license_plate",
            "licencePlate",
            "licence_plate",
            "plate",
            "plateNumber",
            "plate_number",
            "registration",
            "registrationNumber",
            "registration_number",
            "vrn",
            "topKCandidate",
            "top_k_candidate",
            "candidate",
            "val",
            "value",
            "text",
            "name",
        ):
            if key in value:
                nested = _text_value(value[key])
                if nested:
                    return nested
        return None
    for attr in (
        "matched_name",
        "matchedName",
        "license_plate",
        "licensePlate",
        "licence_plate",
        "licencePlate",
        "plate",
        "plate_number",
        "plateNumber",
        "registration",
        "registration_number",
        "registrationNumber",
        "vrn",
        "top_k_candidate",
        "topKCandidate",
        "candidate",
        "val",
        "value",
        "text",
        "name",
    ):
        if hasattr(value, attr):
            nested = _text_value(getattr(value, attr))
            if nested:
                return nested
    return None


def _normalize_plate(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()


def _looks_like_numeric_score(value: str) -> bool:
    return re.fullmatch(r"[+-]?\d+\.\d+", value.strip()) is not None


def _normalize_key(value: str) -> str:
    return value.strip().replace("-", "_").lower()


def _dict_get(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _datetime_from_unix_ms(value: Any) -> datetime | None:
    if not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


@lru_cache
def get_lpr_timing_recorder() -> LprTimingRecorder:
    return LprTimingRecorder()
