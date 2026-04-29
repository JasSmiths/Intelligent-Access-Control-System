import asyncio
import inspect
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.unifi_protect.package import activate_unifi_protect_package_overlay
from app.services.settings import RuntimeConfig

logger = get_logger(__name__)


class UnifiProtectError(RuntimeError):
    """Raised when UniFi Protect rejects or cannot complete a request."""


class UnifiProtectNotConfiguredError(UnifiProtectError):
    """Raised when Protect settings are incomplete."""


@dataclass(frozen=True)
class ProtectMedia:
    content: bytes
    content_type: str


def is_unifi_protect_configured(config: RuntimeConfig) -> bool:
    return bool(
        config.unifi_protect_host
        and config.unifi_protect_username
        and config.unifi_protect_password
        and config.unifi_protect_api_key
    )


def public_unifi_protect_configured_value(config: RuntimeConfig) -> dict[str, Any]:
    return {
        "configured": is_unifi_protect_configured(config),
        "host": config.unifi_protect_host,
        "port": config.unifi_protect_port,
        "verify_ssl": config.unifi_protect_verify_ssl,
        "snapshot_width": config.unifi_protect_snapshot_width,
        "snapshot_height": config.unifi_protect_snapshot_height,
    }


async def build_unifi_protect_client(config: RuntimeConfig):
    if not is_unifi_protect_configured(config):
        raise UnifiProtectNotConfiguredError("UniFi Protect host, username, password, and API key are required.")

    activate_unifi_protect_package_overlay()
    try:
        from uiprotect import ProtectApiClient
        from uiprotect.data import ModelType
    except ImportError as exc:
        raise UnifiProtectError("The uiprotect package is not installed in the backend environment.") from exc

    return ProtectApiClient(
        host=config.unifi_protect_host,
        port=config.unifi_protect_port,
        username=config.unifi_protect_username,
        password=config.unifi_protect_password,
        api_key=config.unifi_protect_api_key,
        verify_ssl=config.unifi_protect_verify_ssl,
        cache_dir=settings.data_dir / "unifi-protect-cache",
        config_dir=settings.data_dir / "unifi-protect-config",
        store_sessions=False,
        subscribed_models={ModelType.CAMERA, ModelType.EVENT},
        ignore_stats=True,
    )


async def close_unifi_protect_client(api: Any) -> None:
    seen: set[int] = set()
    for method_name in (
        "async_disconnect_ws",
        "disconnect_ws",
        "close_session",
        "close_public_api_session",
        "async_close",
        "close",
        "async_logout",
        "logout",
    ):
        await _call_unifi_cleanup(api, method_name, f"api.{method_name}")

    for attr_name in (
        "session",
        "_session",
        "public_api_session",
        "_public_api_session",
        "aiohttp_session",
        "_aiohttp_session",
        "ws",
        "_ws",
        "websocket",
        "_websocket",
    ):
        await _close_unifi_resource(getattr(api, attr_name, None), attr_name, seen)


async def _close_unifi_resource(resource: Any, label: str, seen: set[int]) -> None:
    if resource is None:
        return
    identity = id(resource)
    if identity in seen:
        return
    seen.add(identity)
    for method_name in ("async_close", "close", "disconnect", "release"):
        await _call_unifi_cleanup(resource, method_name, f"{label}.{method_name}")


async def _call_unifi_cleanup(owner: Any, method_name: str, label: str) -> None:
    method = getattr(owner, method_name, None)
    if not callable(method):
        return
    try:
        result = method()
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        logger.debug("unifi_protect_close_failed", extra={"method": label, "error": str(exc)})


async def load_unifi_protect_bootstrap(api: Any) -> None:
    try:
        await api.update()
        update_public = getattr(api, "update_public", None)
        if callable(update_public):
            await update_public()
    except Exception as exc:
        raise UnifiProtectError(_protect_error_message(exc)) from exc


def subscribe_unifi_protect(
    api: Any,
    message_callback: Callable[[Any], None],
    state_callback: Callable[[Any], None],
) -> list[Callable[[], None]]:
    unsubscribers: list[Callable[[], None]] = []
    for method_name in (
        "subscribe_websocket",
        "subscribe_events_websocket",
        "subscribe_devices_websocket",
    ):
        method = getattr(api, method_name, None)
        if callable(method):
            try:
                unsubscribers.append(method(message_callback))
            except Exception as exc:
                logger.warning("unifi_protect_subscription_failed", extra={"method": method_name, "error": str(exc)})

    for method_name in (
        "subscribe_websocket_state",
        "subscribe_events_websocket_state",
        "subscribe_devices_websocket_state",
    ):
        method = getattr(api, method_name, None)
        if callable(method):
            try:
                unsubscribers.append(method(state_callback))
            except Exception as exc:
                logger.debug("unifi_protect_state_subscription_failed", extra={"method": method_name, "error": str(exc)})
    return unsubscribers


def list_bootstrap_cameras(api: Any) -> list[Any]:
    cameras = getattr(getattr(api, "bootstrap", None), "cameras", {}) or {}
    return list(cameras.values())


def bootstrap_camera(api: Any, camera_id: str) -> Any | None:
    cameras = getattr(getattr(api, "bootstrap", None), "cameras", {}) or {}
    return cameras.get(camera_id)


async def get_camera_by_identifier(api: Any, identifier: str) -> Any:
    normalized = identifier.strip().lower()
    camera = bootstrap_camera(api, identifier)
    if camera is not None:
        return camera
    best_camera: Any | None = None
    best_score = 0
    for candidate in list_bootstrap_cameras(api):
        candidate_values = {
            str(getattr(candidate, "id", "")).lower(),
            str(getattr(candidate, "name", "")).lower(),
            str(getattr(candidate, "display_name", "")).lower(),
        }
        if normalized in candidate_values:
            return candidate
        score = max(_camera_identifier_score(normalized, value) for value in candidate_values)
        if score > best_score:
            best_camera = candidate
            best_score = score
    if best_camera is not None and best_score >= 2:
        return best_camera
    try:
        return await api.get_camera(identifier)
    except Exception as exc:
        raise UnifiProtectError(f"UniFi Protect camera not found: {identifier}") from exc


def _camera_identifier_score(requested: str, candidate: str) -> int:
    requested_key = _camera_match_key(requested)
    candidate_key = _camera_match_key(candidate)
    if not requested_key or not candidate_key:
        return 0
    if requested_key == candidate_key:
        return 100
    if requested_key in candidate_key or candidate_key in requested_key:
        return 50
    requested_tokens = set(requested_key.split())
    candidate_tokens = set(candidate_key.split())
    return len(requested_tokens & candidate_tokens)


def _camera_match_key(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", value.lower())
    tokens = [
        token
        for token in cleaned.split()
        if token not in {"camera", "cam", "snapshot", "image", "latest", "the", "from", "of"}
    ]
    return " ".join(tokens)


async def get_event_by_id(api: Any, event_id: str) -> Any:
    events = getattr(getattr(api, "bootstrap", None), "events", {}) or {}
    event = events.get(event_id)
    if event is not None:
        return event
    try:
        return await api.get_event(event_id)
    except Exception as exc:
        raise UnifiProtectError(f"UniFi Protect event not found: {event_id}") from exc


async def list_unifi_protect_events(
    api: Any,
    *,
    camera_id: str | None = None,
    event_type: str | None = None,
    limit: int = 25,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[Any]:
    activate_unifi_protect_package_overlay()
    try:
        from uiprotect.data import EventType
    except ImportError as exc:
        raise UnifiProtectError("The uiprotect package is not installed in the backend environment.") from exc

    start = since or datetime.now(tz=UTC) - timedelta(hours=24)
    end = until or datetime.now(tz=UTC) + timedelta(seconds=10)
    types = None
    if event_type:
        try:
            types = [EventType(event_type)]
        except ValueError as exc:
            raise UnifiProtectError(f"Unsupported UniFi Protect event type: {event_type}") from exc

    try:
        events = await api.get_events(
            start=start,
            end=end,
            limit=max(limit * 3, limit),
            types=types,
            sorting="desc",
        )
    except Exception as exc:
        raise UnifiProtectError(_protect_error_message(exc)) from exc

    filtered = [
        event
        for event in events
        if not camera_id or str(getattr(event, "camera_id", "")) == camera_id
    ]
    return filtered[:limit]


async def get_unifi_protect_snapshot(
    api: Any,
    camera_id: str,
    *,
    width: int | None = None,
    height: int | None = None,
    channel: str | None = None,
) -> ProtectMedia:
    camera = await get_camera_by_identifier(api, camera_id)
    try:
        if channel == "package":
            content = await camera.get_package_snapshot(width=width, height=height)
        else:
            content = await camera.get_snapshot(width=width, height=height)
    except Exception as exc:
        raise UnifiProtectError(_protect_error_message(exc)) from exc
    if not content:
        raise UnifiProtectError("UniFi Protect did not return a camera snapshot.")
    return ProtectMedia(content=content, content_type="image/jpeg")


async def get_unifi_protect_event_thumbnail(
    api: Any,
    event_id: str,
    *,
    width: int | None = None,
    height: int | None = None,
) -> ProtectMedia:
    event = await get_event_by_id(api, event_id)
    thumbnail_id = getattr(event, "thumbnail_id", None) or event_id
    try:
        content = await api.get_event_thumbnail(thumbnail_id, width=width, height=height, retry_timeout=2)
    except Exception as exc:
        raise UnifiProtectError(_protect_error_message(exc)) from exc
    if not content:
        raise UnifiProtectError("UniFi Protect did not return an event thumbnail.")
    return ProtectMedia(content=content, content_type="image/jpeg")


async def get_unifi_protect_event_video(api: Any, event_id: str) -> ProtectMedia:
    event = await get_event_by_id(api, event_id)
    try:
        content = await event.get_video()
    except Exception as exc:
        raise UnifiProtectError(_protect_error_message(exc)) from exc
    if not content:
        raise UnifiProtectError("UniFi Protect did not return an event video clip.")
    return ProtectMedia(content=content, content_type="video/mp4")


def serialize_unifi_camera(camera: Any) -> dict[str, Any]:
    camera_id = str(getattr(camera, "id", ""))
    return {
        "id": camera_id,
        "name": str(getattr(camera, "display_name", None) or getattr(camera, "name", None) or camera_id),
        "model": _enum_value(getattr(camera, "type", None) or getattr(camera, "model", None)),
        "state": _enum_value(getattr(camera, "state", None)),
        "is_adopted": bool(getattr(camera, "is_adopted", getattr(camera, "is_adopted_by_us", False))),
        "is_recording": bool(getattr(camera, "is_recording", False)),
        "is_recording_enabled": bool(getattr(camera, "is_recording_enabled", False)),
        "is_video_ready": bool(getattr(camera, "is_video_ready", False)),
        "is_motion_detected": bool(getattr(camera, "is_motion_detected", False)),
        "is_smart_detected": bool(getattr(camera, "is_smart_detected", False)),
        "last_motion_at": _isoformat(getattr(camera, "last_motion", None)),
        "last_motion_event_id": getattr(camera, "last_motion_event_id", None),
        "last_smart_detect_at": _isoformat(getattr(camera, "last_smart_detect", None)),
        "last_smart_detect_event_id": getattr(camera, "last_smart_detect_event_id", None),
        "last_smart_audio_detect_at": _isoformat(getattr(camera, "last_smart_audio_detect", None)),
        "last_smart_audio_detect_event_id": getattr(camera, "last_smart_audio_detect_event_id", None),
        "channels": [_serialize_channel(channel) for channel in getattr(camera, "channels", [])],
        "feature_flags": _serialize_camera_features(camera),
        "detections": _serialize_camera_detections(camera),
        "snapshot_url": f"/api/v1/integrations/unifi-protect/cameras/{camera_id}/snapshot",
    }


def serialize_unifi_event(event: Any) -> dict[str, Any]:
    event_id = str(getattr(event, "id", ""))
    camera = getattr(event, "camera", None)
    camera_id = str(getattr(event, "camera_id", "") or "")
    return {
        "id": event_id,
        "type": _enum_value(getattr(event, "type", None)),
        "camera_id": camera_id,
        "camera_name": str(getattr(camera, "display_name", "") or getattr(camera, "name", "") or camera_id),
        "start": _isoformat(getattr(event, "start", None)),
        "end": _isoformat(getattr(event, "end", None)),
        "score": int(getattr(event, "score", 0) or 0),
        "smart_detect_types": [_enum_value(item) for item in getattr(event, "smart_detect_types", [])],
        "thumbnail_url": f"/api/v1/integrations/unifi-protect/events/{event_id}/thumbnail",
        "video_url": f"/api/v1/integrations/unifi-protect/events/{event_id}/video" if getattr(event, "end", None) else None,
        "metadata": _safe_metadata(getattr(event, "metadata", None)),
    }


def websocket_message_payload(message: Any) -> dict[str, Any]:
    new_obj = getattr(message, "new_obj", None)
    old_obj = getattr(message, "old_obj", None)
    changed_data = _redact_payload(getattr(message, "changed_data", {}) or {})
    model_key = str(changed_data.get("modelKey") or _enum_value(getattr(new_obj, "model", None)) or "")
    payload = {
        "action": _enum_value(getattr(message, "action", None)),
        "model": model_key,
        "changed_data": changed_data,
        "object_id": str(getattr(new_obj, "id", "") or getattr(old_obj, "id", "") or changed_data.get("id", "")),
    }
    if _looks_like_camera(new_obj, model_key):
        payload["camera"] = serialize_unifi_camera(new_obj)
    if _looks_like_event(new_obj, model_key):
        payload["event"] = serialize_unifi_event(new_obj)
    return payload


def _serialize_channel(channel: Any) -> dict[str, Any]:
    return {
        "id": str(getattr(channel, "id", "")),
        "name": str(getattr(channel, "name", "") or getattr(channel, "id", "")),
        "width": getattr(channel, "width", None),
        "height": getattr(channel, "height", None),
        "fps": getattr(channel, "fps", None),
        "bitrate": getattr(channel, "bitrate", None),
        "is_rtsp_enabled": bool(getattr(channel, "is_rtsp_enabled", False)),
        "is_package": bool(getattr(channel, "is_package", False)),
    }


def _serialize_camera_features(camera: Any) -> dict[str, Any]:
    flags = getattr(camera, "feature_flags", None)
    return {
        "has_smart_detect": bool(getattr(flags, "has_smart_detect", False)),
        "has_package_camera": bool(getattr(flags, "has_package_camera", False)),
        "has_mic": bool(getattr(camera, "has_mic", False)),
        "smart_detect_types": [_enum_value(item) for item in getattr(flags, "smart_detect_types", [])],
        "smart_detect_audio_types": [_enum_value(item) for item in (getattr(flags, "smart_detect_audio_types", None) or [])],
    }


def _serialize_camera_detections(camera: Any) -> dict[str, Any]:
    mappings = {
        "person": "is_person_currently_detected",
        "vehicle": "is_vehicle_currently_detected",
        "license_plate": "is_license_plate_currently_detected",
        "package": "is_package_currently_detected",
        "animal": "is_animal_currently_detected",
        "face": "is_face_currently_detected",
        "speaking": "is_speak_currently_detected",
        "barking": "is_bark_currently_detected",
        "smoke": "is_smoke_currently_detected",
        "siren": "is_siren_currently_detected",
        "baby_cry": "is_baby_cry_currently_detected",
        "car_horn": "is_car_horn_currently_detected",
        "glass_break": "is_glass_break_currently_detected",
    }
    active = [label for label, attr in mappings.items() if bool(getattr(camera, attr, False))]
    return {"active": active}


def _looks_like_camera(obj: Any, model_key: str) -> bool:
    return obj is not None and (
        model_key.lower() == "camera"
        or hasattr(obj, "channels")
        or hasattr(obj, "is_video_ready")
    )


def _looks_like_event(obj: Any, model_key: str) -> bool:
    return obj is not None and (
        model_key.lower() == "event"
        or hasattr(obj, "smart_detect_types")
        or hasattr(obj, "camera_id") and hasattr(obj, "start")
    )


def _safe_metadata(metadata: Any) -> dict[str, Any]:
    if metadata is None:
        return {}
    raw = _model_to_dict(metadata)
    if not isinstance(raw, dict):
        return {}
    allowed = {
        "licensePlate",
        "detectedThumbnails",
        "sensorId",
        "lightId",
        "cameraId",
        "objectType",
    }
    return {key: value for key, value in raw.items() if key in allowed}


def _model_to_dict(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_model_to_dict(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _model_to_dict(item) for key, item in value.items()}
    unifi_dict = getattr(value, "unifi_dict", None)
    if callable(unifi_dict):
        try:
            return unifi_dict()
        except Exception:
            return str(value)
    return str(value)


def _redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sensitive_markers = {"token", "password", "key", "secret", "rtsp", "rtsps", "stream"}
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if any(marker in str(key).lower() for marker in sensitive_markers):
            clean[str(key)] = "[redacted]"
        else:
            clean[str(key)] = _model_to_dict(value)
    return clean


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _isoformat(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


def _protect_error_message(exc: Exception) -> str:
    message = str(exc) or exc.__class__.__name__
    return f"UniFi Protect error: {message}"


def run_coroutine_threadsafe(coro: Any) -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        logger.debug("unifi_protect_callback_without_running_loop")
