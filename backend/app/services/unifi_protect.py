import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
from functools import lru_cache
from time import monotonic
from typing import Any, Callable

from app.core.logging import get_logger
from app.modules.unifi_protect.client import (
    UnifiProtectError,
    build_unifi_protect_client,
    close_unifi_protect_client,
    get_unifi_protect_event_thumbnail,
    get_unifi_protect_event_video,
    get_unifi_protect_snapshot,
    is_unifi_protect_configured,
    list_bootstrap_cameras,
    list_unifi_protect_events,
    load_unifi_protect_bootstrap,
    public_unifi_protect_configured_value,
    serialize_unifi_camera,
    serialize_unifi_event,
    subscribe_unifi_protect,
    websocket_message_payload,
)
from app.services.event_bus import event_bus
from app.services.lpr_timing import extract_unifi_protect_track_observations, get_lpr_timing_recorder
from app.services.settings import get_runtime_config
from app.services.vehicle_visual_detections import (
    get_vehicle_presence_tracker,
    get_vehicle_visual_detection_recorder,
)

logger = get_logger(__name__)

PROTECT_UPDATE_PUBLISH_MIN_INTERVAL_SECONDS = 5.0
LPR_TRACK_PROBE_DELAYS_SECONDS = (0.0, 0.5, 0.5, 1.0, 2.0, 4.0, 4.0, 6.0, 7.0)
GATE_LPR_CAMERA_NAME = "gate lpr"
GATE_LPR_CAMERA_DEVICE = "942A6FD09D64"


class UnifiProtectIntegrationService:
    """Keeps UniFi Protect cameras and detection events available to IACS."""

    def __init__(self) -> None:
        self._api: Any | None = None
        self._lock = asyncio.Lock()
        self._media_semaphore = asyncio.Semaphore(4)
        self._unsubscribers: list[Callable[[], None]] = []
        self._last_error: str | None = None
        self._connected = False
        self._last_update_publish_at: dict[str, float] = {}
        self._lpr_track_probe_event_ids: set[str] = set()
        self._lpr_track_probe_finished_event_ids: set[str] = set()

    async def configured(self) -> bool:
        return is_unifi_protect_configured(await get_runtime_config())

    async def start(self) -> None:
        if not await self.configured():
            logger.info("unifi_protect_not_configured")
            return
        try:
            await self._ensure_api(subscribe=True)
        except UnifiProtectError as exc:
            self._last_error = str(exc)
            logger.warning("unifi_protect_start_failed", extra={"error": self._last_error})
            await event_bus.publish(
                "protect.connection.changed",
                {"configured": True, "connected": False, "error": self._last_error},
            )

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def restart(self) -> None:
        async with self._lock:
            await self._stop_locked()
        await self.start()

    async def status(self, *, refresh: bool = False) -> dict[str, Any]:
        config = await get_runtime_config()
        status = {
            **public_unifi_protect_configured_value(config),
            "connected": self._connected,
            "last_error": self._last_error,
            "camera_count": 0,
        }
        if not is_unifi_protect_configured(config):
            return status

        try:
            api = await self._ensure_api(subscribe=True, refresh=refresh)
            cameras = list_bootstrap_cameras(api)
            status.update(
                {
                    "connected": True,
                    "last_error": None,
                    "camera_count": len(cameras),
                }
            )
        except UnifiProtectError as exc:
            self._last_error = str(exc)
            status.update({"connected": False, "last_error": self._last_error})
        return status

    async def list_cameras(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        api = await self._ensure_api(subscribe=True, refresh=refresh)
        return [serialize_unifi_camera(camera) for camera in list_bootstrap_cameras(api)]

    async def resolve_lpr_smart_zone_names(
        self,
        zone_values: list[str],
        *,
        camera_identifier: str | None = None,
    ) -> dict[str, Any]:
        api = await self._ensure_api(subscribe=True)
        camera = gate_lpr_camera_from_bootstrap(list_bootstrap_cameras(api), camera_identifier=camera_identifier)
        resolved = resolve_camera_smart_zone_names(camera, zone_values) if camera is not None else zone_values
        return {
            "camera_id": str(getattr(camera, "id", "") or "") if camera is not None else None,
            "camera_name": str(getattr(camera, "display_name", None) or getattr(camera, "name", None) or "") if camera is not None else None,
            "camera_identifier": camera_identifier,
            "smart_zones": resolved,
        }

    async def list_events(
        self,
        *,
        camera_id: str | None = None,
        event_type: str | None = None,
        limit: int = 25,
        since=None,
        until=None,
    ) -> list[dict[str, Any]]:
        api = await self._ensure_api(subscribe=True)
        events = await list_unifi_protect_events(
            api,
            camera_id=camera_id,
            event_type=event_type,
            limit=limit,
            since=since,
            until=until,
        )
        return [serialize_unifi_event(event) for event in events]

    async def event_lpr_track(self, event_id: str) -> dict[str, Any]:
        api = await self._ensure_api(subscribe=True)
        event = None
        try:
            event = await api.get_event(event_id)
        except Exception as exc:
            logger.debug("unifi_protect_event_lookup_for_track_failed", extra={"event_id": event_id, "error": str(exc)})
        try:
            track = await api.api_request_obj(f"events/{event_id}/smartDetectTrack")
        except Exception as exc:
            raise UnifiProtectError(str(exc)) from exc
        observations = extract_unifi_protect_track_observations(
            track,
            event=event,
            event_id=event_id,
            received_at=datetime.now(tz=UTC),
        )
        return {
            "event": serialize_unifi_event(event) if event is not None else {"id": event_id},
            "observations": [asdict(observation) for observation in observations],
            "count": len(observations),
        }

    async def send_alarm_webhook_test(self, trigger_id: str) -> dict[str, Any]:
        api = await self._ensure_api(subscribe=True)
        method = getattr(api, "send_alarm_webhook_public", None)
        if not callable(method):
            raise UnifiProtectError("Installed uiprotect package does not expose Alarm Manager webhook tests.")
        try:
            result = await method(trigger_id)
        except Exception as exc:
            raise UnifiProtectError(str(exc)) from exc
        return {"trigger_id": trigger_id, "result": result}

    async def snapshot(self, camera_id: str, *, width: int | None = None, height: int | None = None, channel: str | None = None):
        api = await self._ensure_api(subscribe=True)
        async with self._media_semaphore:
            return await get_unifi_protect_snapshot(api, camera_id, width=width, height=height, channel=channel)

    async def event_thumbnail(self, event_id: str, *, width: int | None = None, height: int | None = None):
        api = await self._ensure_api(subscribe=True)
        async with self._media_semaphore:
            return await get_unifi_protect_event_thumbnail(api, event_id, width=width, height=height)

    async def event_video(self, event_id: str):
        api = await self._ensure_api(subscribe=True)
        async with self._media_semaphore:
            return await get_unifi_protect_event_video(api, event_id)

    async def test_connection(self, values: dict[str, Any] | None = None) -> dict[str, Any]:
        runtime = await get_runtime_config()
        if values:
            runtime = _runtime_with_overrides(runtime, values)
        api = await build_unifi_protect_client(runtime)
        try:
            await load_unifi_protect_bootstrap(api)
            cameras = list_bootstrap_cameras(api)
            if not cameras:
                raise UnifiProtectError("UniFi Protect connection succeeded, but no readable cameras were returned.")
            return {"camera_count": len(cameras), "cameras": [serialize_unifi_camera(camera) for camera in cameras[:5]]}
        finally:
            await close_unifi_protect_client(api)

    async def _ensure_api(self, *, subscribe: bool, refresh: bool = False) -> Any:
        async with self._lock:
            if self._api is not None:
                if refresh:
                    await load_unifi_protect_bootstrap(self._api)
                    self._last_error = None
                    self._connected = True
                return self._api

            config = await get_runtime_config()
            api = await build_unifi_protect_client(config)
            await load_unifi_protect_bootstrap(api)
            self._api = api
            self._connected = True
            self._last_error = None
            if subscribe:
                self._unsubscribers = subscribe_unifi_protect(
                    api,
                    self._handle_websocket_message,
                    self._handle_websocket_state,
                )
            await event_bus.publish(
                "protect.connection.changed",
                {"configured": True, "connected": True, "camera_count": len(list_bootstrap_cameras(api))},
            )
            return api

    async def _stop_locked(self) -> None:
        for unsubscribe in self._unsubscribers:
            try:
                unsubscribe()
            except Exception as exc:
                logger.debug("unifi_protect_unsubscribe_failed", extra={"error": str(exc)})
        self._unsubscribers.clear()

        if self._api is not None:
            await close_unifi_protect_client(self._api)
        self._api = None
        self._connected = False

    def _handle_websocket_message(self, message: Any) -> None:
        loop: asyncio.AbstractEventLoop | None = None
        try:
            received_at = datetime.now(tz=UTC)
            loop = asyncio.get_running_loop()
            loop.create_task(get_lpr_timing_recorder().record_unifi_protect_message(message, received_at=received_at))
            loop.create_task(
                get_vehicle_visual_detection_recorder().record_unifi_protect_message(
                    message,
                    received_at=received_at,
                )
            )
            event_id = self._lpr_track_probe_event_id(message)
            if (
                event_id
                and event_id not in self._lpr_track_probe_event_ids
                and event_id not in self._lpr_track_probe_finished_event_ids
            ):
                self._lpr_track_probe_event_ids.add(event_id)
                loop.create_task(self._probe_lpr_track(event_id, getattr(message, "new_obj", None)))
        except RuntimeError:
            logger.debug("unifi_protect_lpr_timing_without_loop")
        except Exception as exc:
            logger.debug("unifi_protect_lpr_timing_failed", extra={"error": str(exc)})

        try:
            payload = websocket_message_payload(message)
        except Exception as exc:
            logger.debug("unifi_protect_ws_payload_failed", extra={"error": str(exc)})
            return
        try:
            (loop or asyncio.get_running_loop()).create_task(
                get_vehicle_presence_tracker().record_unifi_realtime_payload(payload, received_at=received_at)
            )
        except RuntimeError:
            logger.debug("unifi_protect_vehicle_presence_without_loop")

        event_type = "protect.updated"
        if payload.get("camera"):
            event_type = "protect.camera.updated"
        if payload.get("event"):
            event_type = "protect.event.detected"
        if event_type in {"protect.updated", "protect.camera.updated"} and not self._should_publish_update(event_type, payload):
            return
        try:
            (loop or asyncio.get_running_loop()).create_task(event_bus.publish(event_type, payload))
        except RuntimeError:
            logger.debug("unifi_protect_ws_without_loop")

    def _handle_websocket_state(self, state: Any) -> None:
        connected = str(getattr(state, "value", state)).upper() == "CONNECTED"
        self._connected = connected
        try:
            asyncio.get_running_loop().create_task(
                event_bus.publish(
                    "protect.connection.changed",
                    {"configured": True, "connected": connected, "state": str(getattr(state, "value", state))},
                )
            )
        except RuntimeError:
            logger.debug("unifi_protect_state_without_loop")

    def _should_publish_update(self, event_type: str, payload: dict[str, Any]) -> bool:
        object_id = str(payload.get("object_id") or payload.get("model") or "global")
        key = f"{event_type}:{object_id}"
        now = monotonic()
        previous = self._last_update_publish_at.get(key, 0.0)
        if now - previous < PROTECT_UPDATE_PUBLISH_MIN_INTERVAL_SECONDS:
            return False
        self._last_update_publish_at[key] = now
        return True

    async def _probe_lpr_track(self, event_id: str, event: Any) -> None:
        try:
            api = await self._ensure_api(subscribe=True)
            for attempt, delay in enumerate(LPR_TRACK_PROBE_DELAYS_SECONDS, start=1):
                if delay:
                    await asyncio.sleep(delay)
                try:
                    track = await api.api_request_obj(f"events/{event_id}/smartDetectTrack")
                except Exception as exc:
                    logger.debug(
                        "unifi_protect_lpr_track_probe_failed",
                        extra={"event_id": event_id, "attempt": attempt, "error": str(exc)},
                    )
                    continue

                count = await get_lpr_timing_recorder().record_unifi_protect_track(
                    track,
                    event=event,
                    event_id=event_id,
                    received_at=datetime.now(tz=UTC),
                    probe_attempt=attempt,
                )
                await get_vehicle_visual_detection_recorder().record_unifi_protect_track(
                    track,
                    event=event,
                    event_id=event_id,
                    received_at=datetime.now(tz=UTC),
                    probe_attempt=attempt,
                )
                await get_vehicle_presence_tracker().record_unifi_protect_track(
                    track,
                    event=event,
                    event_id=event_id,
                    received_at=datetime.now(tz=UTC),
                    probe_attempt=attempt,
                )
                if count:
                    self._lpr_track_probe_finished_event_ids.add(event_id)
                    return
        finally:
            self._lpr_track_probe_event_ids.discard(event_id)

    def _lpr_track_probe_event_id(self, message: Any) -> str | None:
        new_obj = getattr(message, "new_obj", None)
        changed_data = getattr(message, "changed_data", {}) or {}
        model = str(
            _dict_get(changed_data, "modelKey")
            or _enum_value(getattr(new_obj, "model", None))
            or ""
        ).lower()
        if model and model != "event":
            return None
        if new_obj is None and model != "event":
            return None

        event_id = str(getattr(new_obj, "id", "") or _dict_get(changed_data, "id") or "") or None
        if not event_id:
            return None
        if self._looks_like_lpr_event(new_obj, changed_data):
            return event_id
        return None

    def _looks_like_lpr_event(self, event: Any, changed_data: dict[str, Any]) -> bool:
        smart_types = [
            str(_enum_value(item) or "").lower()
            for item in (
                getattr(event, "smart_detect_types", None)
                or _dict_get(changed_data, "smartDetectTypes")
                or _dict_get(changed_data, "smart_detect_types")
                or []
            )
        ]
        if any(item == "licenseplate" for item in smart_types):
            return True

        event_type = str(_enum_value(getattr(event, "type", None)) or _dict_get(changed_data, "type") or "").lower()
        if event_type == "smartdetectzone":
            return True

        camera = getattr(event, "camera", None)
        camera_name = str(
            getattr(camera, "display_name", "")
            or getattr(camera, "name", "")
            or ""
        ).lower()
        if "lpr" in camera_name or "license" in camera_name:
            return True

        changed_text = str(changed_data).lower()
        return "licenseplate" in changed_text or "detectedthumbnails" in changed_text or "detected_thumbnails" in changed_text


def _runtime_with_overrides(runtime, values: dict[str, Any]):
    fields = {field: getattr(runtime, field) for field in runtime.__dataclass_fields__}
    for key in fields:
        if key in values:
            fields[key] = values[key]
    fields["unifi_protect_port"] = int(fields["unifi_protect_port"] or 443)
    fields["unifi_protect_verify_ssl"] = _as_bool(fields["unifi_protect_verify_ssl"])
    fields["unifi_protect_snapshot_width"] = int(fields["unifi_protect_snapshot_width"] or 1280)
    fields["unifi_protect_snapshot_height"] = int(fields["unifi_protect_snapshot_height"] or 720)
    return runtime.__class__(**fields)


def gate_lpr_camera_from_bootstrap(cameras: list[Any], *, camera_identifier: str | None = None) -> Any | None:
    identifier = _normalize_camera_identifier(camera_identifier)
    if identifier:
        for camera in cameras:
            if identifier in _camera_identifier_values(camera):
                return camera

    for camera in cameras:
        if _normalize_camera_identifier(getattr(camera, "display_name", None) or getattr(camera, "name", None)) == GATE_LPR_CAMERA_NAME:
            return camera

    for camera in cameras:
        if _normalize_camera_identifier(getattr(camera, "mac", None)) == _normalize_camera_identifier(GATE_LPR_CAMERA_DEVICE):
            return camera

    for camera in cameras:
        label = _normalize_camera_identifier(getattr(camera, "display_name", None) or getattr(camera, "name", None))
        if label and "gate" in label and "lpr" in label:
            return camera
    return None


def resolve_camera_smart_zone_names(camera: Any, zone_values: list[str]) -> list[str]:
    lookup: dict[str, list[str]] = {}
    for zone in getattr(camera, "smart_detect_zones", []) or []:
        zone_id = _string_or_none(getattr(zone, "id", None))
        zone_name = _string_or_none(getattr(zone, "name", None))
        labels = [value for value in (zone_id, zone_name) if value]
        for label in labels:
            lookup[_normalize_smart_zone(label)] = labels

    resolved: list[str] = []
    for value in zone_values:
        text = _string_or_none(value)
        if not text:
            continue
        resolved.append(text)
        resolved.extend(lookup.get(_normalize_smart_zone(text), []))
    return _dedupe_preserving_order(resolved)


def _camera_identifier_values(camera: Any) -> set[str]:
    return {
        value
        for value in (
            _normalize_camera_identifier(getattr(camera, "id", None)),
            _normalize_camera_identifier(getattr(camera, "mac", None)),
            _normalize_camera_identifier(getattr(camera, "display_name", None)),
            _normalize_camera_identifier(getattr(camera, "name", None)),
        )
        if value
    }


def _normalize_camera_identifier(value: Any) -> str:
    return str(value or "").strip().casefold()


def _normalize_smart_zone(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().replace("_", " ").replace("-", " ").split())


def _string_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value).strip()


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = _normalize_smart_zone(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(str(value).strip())
    return deduped


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _dict_get(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


@lru_cache
def get_unifi_protect_service() -> UnifiProtectIntegrationService:
    return UnifiProtectIntegrationService()
