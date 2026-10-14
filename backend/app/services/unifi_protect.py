import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import asdict
from datetime import UTC, datetime
from functools import lru_cache
from time import monotonic
from typing import Any, TypeVar

from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.modules.unifi_protect.client import (
    ProtectRecoveryPage,
    UnifiProtectError,
    build_unifi_protect_client,
    close_unifi_protect_client,
    get_unifi_protect_event_thumbnail,
    get_unifi_protect_event_video,
    get_unifi_protect_snapshot,
    get_unifi_protect_track,
    is_unifi_protect_configured,
    is_unifi_protect_stream_metadata_only,
    list_bootstrap_cameras,
    list_unifi_protect_events,
    load_unifi_protect_bootstrap,
    protect_connection_key,
    public_unifi_protect_configured_value,
    read_protect_recovery_page,
    serialize_unifi_camera,
    serialize_unifi_event,
    subscribe_unifi_protect,
    websocket_message_payload,
)
from app.services.event_bus import event_bus
from app.services.lpr_timing import (
    extract_unifi_protect_lpr_observations,
    extract_unifi_protect_track_observations,
    unifi_protect_timing_fields,
)
from app.services.settings import get_runtime_config
from app.services.unifi_protect_contracts import (
    ProtectEvidenceGeneration,
    ProtectProbeContext,
    ProtectWorkValidity,
)
from app.services.unifi_protect_operations import ProtectOperations, ProtectOperationUnavailable
from app.services.unifi_protect_runtime import (
    ProtectCallbackWork,
    ProtectRuntimeCounters,
    UnifiProtectRuntime,
)
from app.services.vehicle_visual_detections import (
    extract_unifi_protect_vehicle_visual_observations,
    extract_unifi_realtime_presence_observations,
    unifi_protect_visual_fields,
)

T = TypeVar("T")
_current_connection: ContextVar["UnifiProtectIntegrationService | None"] = ContextVar(
    "protect_connection", default=None
)
logger = get_logger(__name__)

GATE_LPR_CAMERA_NAME = "gate lpr"
GATE_LPR_CAMERA_DEVICE = "942A6FD09D64"
PROTECT_WEBSOCKET_CHANNELS = ("private", "events", "devices")


class UnifiProtectIntegrationService:
    """Keeps UniFi Protect cameras and detection events available to IACS."""

    def __init__(self) -> None:
        self._api: Any | None = None
        self._api_discovery_key: str | None = None
        self._lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._media_semaphore = asyncio.Semaphore(4)
        self._unsubscribers: list[Callable[[], None]] = []
        self._last_error: str | None = None
        self._connected = False
        self._websocket_states = {channel: "unknown" for channel in PROTECT_WEBSOCKET_CHANNELS}
        self._stopped = False
        self._stopping = False
        self._stop_done = asyncio.Event()
        self._stop_done.set()
        self._provider_generation = 0
        self._evidence = ProtectEvidenceGeneration()
        self._counters = ProtectRuntimeCounters()
        self._runtime: UnifiProtectRuntime | None = None
        self._operations: ProtectOperations | None = None
        self._draining_runtime: UnifiProtectRuntime | None = None
        self._draining_operations: ProtectOperations | None = None
        self._runtime_status: dict[str, Any] = {}

    async def configured(self) -> bool:
        return is_unifi_protect_configured(await get_runtime_config())

    async def start(self) -> None:
        if self._stopping:
            raise UnifiProtectError("UniFi Protect is stopping.")
        self._stopped = False
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

    def _reject_reentrant_lifecycle(self) -> None:
        if _current_connection.get() is self:
            raise UnifiProtectError(
                "UniFi Protect lifecycle cannot drain its own connection operation."
            )
        for runtime in (self._runtime, self._draining_runtime):
            if runtime is not None and runtime.owns_current():
                raise UnifiProtectError("UniFi Protect lifecycle cannot drain its own worker.")
        for operations in (self._operations, self._draining_operations):
            if operations is not None and operations.owns_current():
                raise UnifiProtectError("UniFi Protect lifecycle cannot drain its own operation.")

    async def stop(self) -> None:
        # Preserve caller identity and reject before changing any lifecycle state.
        self._reject_reentrant_lifecycle()
        self._stopped = True
        await drain_owned(self._stop())

    async def restart(self) -> None:
        self._reject_reentrant_lifecycle()
        if self._stopped:
            raise UnifiProtectError("UniFi Protect is shutting down.")
        await drain_owned(self._stop())
        if self._stopped:
            raise UnifiProtectError("UniFi Protect is shutting down.")
        await self.start()

    async def _stop(self) -> None:
        async with self._lock:
            if self._stopping:
                wait = True
                runtime = operations = api = None
                unsubscribers = []
            else:
                wait = False
                self._stopping = True
                self._stop_done.clear()
                self._provider_generation += 1
                self._evidence.invalidate()
                api, self._api = self._api, None
                runtime, self._runtime = self._runtime, None
                operations, self._operations = self._operations, None
                unsubscribers, self._unsubscribers = self._unsubscribers, []
                self._draining_runtime, self._draining_operations = runtime, operations
                if runtime is not None:
                    runtime.close_admission()
                if operations is not None:
                    operations.close_admission()
                self._connected = False
        if wait:
            await self._stop_done.wait()
            return
        try:
            for unsubscribe in unsubscribers:
                try:
                    unsubscribe()
                except Exception as exc:  # noqa: BLE001 - Unsubscription failure must not abandon actual cleanup.
                    logger.debug("unifi_protect_unsubscribe_failed", extra={"error": str(exc)})
            if runtime is not None:
                await runtime.stop()
                self._runtime_status = runtime.status()
            if operations is not None:
                await operations.drain()
            # An in-progress bootstrap owns its own rejected client close. It
            # cannot publish/subscribe a replacement while stopping is true.
            async with self._connect_lock:
                pass
            if api is not None:
                await close_unifi_protect_client(api)
        finally:
            async with self._lock:
                self._draining_runtime = self._draining_operations = None
                self._websocket_states = {
                    channel: "unknown" for channel in PROTECT_WEBSOCKET_CHANNELS
                }
                self._stopping = False
                self._stop_done.set()

    async def _run_current(
        self,
        operation: Callable[[Any], Awaitable[T]],
        *,
        media: bool = False,
        preserve_outcome: bool = False,
    ) -> T:
        api = await self._ensure_api(subscribe=True)
        if media:
            await self._media_semaphore.acquire()
        try:
            async with self._lock:
                if (
                    self._stopped
                    or self._stopping
                    or self._api is not api
                    or self._operations is None
                ):
                    raise UnifiProtectError("UniFi Protect provider changed or is stopping.")
                operations = self._operations
            try:
                result = await operations.run(lambda: operation(api))
                if not preserve_outcome:
                    async with self._lock:
                        if (
                            self._stopped
                            or self._stopping
                            or self._api is not api
                            or self._operations is not operations
                        ):
                            raise UnifiProtectError("UniFi Protect read became obsolete.")
                return result
            except ProtectOperationUnavailable as exc:
                raise UnifiProtectError(str(exc)) from exc
        finally:
            if media:
                self._media_semaphore.release()

    async def status(self, *, refresh: bool = False) -> dict[str, Any]:
        config = await get_runtime_config()
        status = {
            **public_unifi_protect_configured_value(config),
            "connected": self._connected,
            "realtime_connected": self._realtime_connected(),
            "websocket_states": dict(self._websocket_states),
            "realtime_error": self._realtime_error(),
            "last_error": self._last_error,
            "camera_count": 0,
            "runtime": self._runtime.status() if self._runtime else self._runtime_status,
            "provider_generation": self._provider_generation,
        }
        if not is_unifi_protect_configured(config):
            return status

        try:
            api = await self._ensure_api(subscribe=True, refresh=refresh)
            cameras = list_bootstrap_cameras(api)
            status.update(
                {
                    "connected": self._connected,
                    "realtime_connected": self._realtime_connected(),
                    "websocket_states": dict(self._websocket_states),
                    "realtime_error": self._realtime_error(),
                    "last_error": None,
                    "camera_count": len(cameras),
                    "runtime": self._runtime.status() if self._runtime else self._runtime_status,
                    "provider_generation": self._provider_generation,
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
        camera = gate_lpr_camera_from_bootstrap(
            list_bootstrap_cameras(api), camera_identifier=camera_identifier
        )
        resolved = (
            resolve_camera_smart_zone_names(camera, zone_values)
            if camera is not None
            else zone_values
        )
        day_night, day_night_source = camera_day_night(camera)
        return {
            "camera_id": str(getattr(camera, "id", "") or "") if camera is not None else None,
            "camera_name": str(
                getattr(camera, "display_name", None) or getattr(camera, "name", None) or ""
            )
            if camera is not None
            else None,
            "camera_identifier": camera_identifier,
            "smart_zones": resolved,
            "smart_zone_matches": resolve_camera_smart_zone_matches(camera, zone_values)
            if camera is not None
            else [],
            "time_of_day": day_night,
            "time_of_day_source": day_night_source,
        }

    @property
    def discovery_generation(self) -> int:
        return self._provider_generation

    async def prepare_discovery(self, key: str) -> int:
        await self._ensure_api(subscribe=True)
        self._assert_discovery_authority(key, self._provider_generation)
        return self._provider_generation

    def _assert_discovery_authority(self, key: str, generation: int) -> None:
        if (self._stopped or self._stopping or self._api_discovery_key != key
                or self._provider_generation != generation):
            raise UnifiProtectError('Protect discovery authority changed.')

    @asynccontextmanager
    async def discovery_authority(self, key: str, generation: int) -> AsyncIterator[None]:
        # Only short SQL checkpoints enter this fence; no vendor I/O under it.
        async with self._lock:
            self._assert_discovery_authority(key, generation)
            yield

    async def recovery_page(self, *, key: str, start_ms: int, end_ms: int) -> tuple[ProtectRecoveryPage, int]:
        async def read(api: Any) -> tuple[ProtectRecoveryPage, int]:
            generation = self._provider_generation
            self._assert_discovery_authority(key, generation)
            return await read_protect_recovery_page(api, start_ms=start_ms, end_ms=end_ms), generation
        return await self._run_current(read)

    async def recovery_track(self, key: str, generation: int, event_id: str) -> dict[str, Any]:
        self._assert_discovery_authority(key, generation)
        result = await self.event_lpr_track(event_id)
        self._assert_discovery_authority(key, generation)
        return result

    async def list_events(
        self,
        *,
        camera_id: str | None = None,
        event_type: str | None = None,
        limit: int = 25,
        since=None,
        until=None,
    ) -> list[dict[str, Any]]:
        events = await self._run_current(
            lambda api: list_unifi_protect_events(
                api,
                camera_id=camera_id,
                event_type=event_type,
                limit=limit,
                since=since,
                until=until,
            )
        )
        return [serialize_unifi_event(event) for event in events]

    async def event_lpr_track(self, event_id: str) -> dict[str, Any]:
        return await self._run_current(lambda api: self._event_lpr_track(api, event_id))

    async def _event_lpr_track(self, api: Any, event_id: str) -> dict[str, Any]:
        event = None
        try:
            event = await api.get_event(event_id)
        except Exception as exc:  # noqa: BLE001 - Background integration failure remains observable and recoverable.
            logger.debug(
                "unifi_protect_event_lookup_for_track_failed",
                extra={"event_id": event_id, "error": str(exc)},
            )
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
        # A completed provider receipt remains the test outcome even if teardown
        # or caller cancellation occurred while the actual operation drained.
        receipt: dict[str, Any] | None = None

        async def send(api: Any) -> dict[str, Any]:
            nonlocal receipt
            receipt = await self._send_alarm_webhook_test(api, trigger_id)
            return receipt

        try:
            return await self._run_current(send, preserve_outcome=True)
        except asyncio.CancelledError:
            if receipt is None:
                raise
            return receipt

    async def _send_alarm_webhook_test(self, api: Any, trigger_id: str) -> dict[str, Any]:
        method = getattr(api, "send_alarm_webhook_public", None)
        if not callable(method):
            raise UnifiProtectError(
                "Installed uiprotect package does not expose Alarm Manager webhook tests."
            )
        try:
            result = await method(trigger_id)
        except Exception as exc:
            raise UnifiProtectError(str(exc)) from exc
        return {"trigger_id": trigger_id, "result": result}

    async def snapshot(
        self,
        camera_id: str,
        *,
        width: int | None = None,
        height: int | None = None,
        channel: str | None = None,
    ):
        return await self._run_current(
            lambda api: get_unifi_protect_snapshot(
                api, camera_id, width=width, height=height, channel=channel
            ),
            media=True,
        )

    async def event_thumbnail(
        self, event_id: str, *, width: int | None = None, height: int | None = None
    ):
        return await self._run_current(
            lambda api: get_unifi_protect_event_thumbnail(
                api, event_id, width=width, height=height
            ),
            media=True,
        )

    async def event_video(self, event_id: str):
        return await self._run_current(
            lambda api: get_unifi_protect_event_video(api, event_id), media=True
        )

    async def test_connection(self, values: dict[str, Any] | None = None) -> dict[str, Any]:
        runtime = await get_runtime_config()
        if values:
            runtime = _runtime_with_overrides(runtime, values)
        api = await build_unifi_protect_client(runtime)
        try:
            await load_unifi_protect_bootstrap(api)
            cameras = list_bootstrap_cameras(api)
            if not cameras:
                raise UnifiProtectError(
                    "UniFi Protect connection succeeded, but no readable cameras were returned."
                )
            return {
                "camera_count": len(cameras),
                "cameras": [serialize_unifi_camera(camera) for camera in cameras[:5]],
            }
        finally:
            await drain_owned(close_unifi_protect_client(api))

    @asynccontextmanager
    async def _connection_scope(self) -> AsyncIterator[None]:
        async with self._connect_lock:
            token = _current_connection.set(self)
            try:
                yield
            finally:
                _current_connection.reset(token)

    async def _ensure_api(self, *, subscribe: bool, refresh: bool = False) -> Any:
        if _current_connection.get() is self:
            if self._api is None or refresh or self._stopped or self._stopping:
                raise UnifiProtectError("UniFi Protect connection operation is in progress.")
            return self._api
        async with self._connection_scope():
            async with self._lock:
                if self._stopped or self._stopping:
                    raise UnifiProtectError("UniFi Protect is shutting down.")
                api, operations = self._api, self._operations
            if api is not None:
                if refresh and operations is not None:
                    await operations.run(lambda: load_unifi_protect_bootstrap(api))
                    async with self._lock:
                        if (
                            self._stopped
                            or self._stopping
                            or self._api is not api
                            or self._operations is not operations
                        ):
                            raise UnifiProtectError("UniFi Protect refresh became obsolete.")
                        self._last_error = None
                        self._connected = True
                return api
            config = await get_runtime_config()
            api = await build_unifi_protect_client(config)
            try:
                await load_unifi_protect_bootstrap(api)
                async with self._lock:
                    rejected = self._stopped or self._stopping
                    if not rejected:
                        self._provider_generation += 1
                        generation = self._provider_generation
                        self._api = api
                        self._api_discovery_key = protect_connection_key(config)
                        self._connected = True
                        self._last_error = None
                        operations = ProtectOperations(
                            lambda: self._api is api and self._provider_generation == generation
                        )
                        self._operations = operations
                        runtime = UnifiProtectRuntime(
                            _ProtectTrackPort(api, operations), self._evidence, self._counters
                        )
                        self._runtime = runtime
                        runtime.start()
                if rejected:
                    raise UnifiProtectError("UniFi Protect is shutting down.")
                if subscribe:
                    self._unsubscribers = subscribe_unifi_protect(
                        api,
                        lambda message: self._handle_websocket_message(
                            message, provider_generation=generation
                        ),
                        lambda channel, state: self._handle_websocket_state(
                            channel, state, provider_generation=generation
                        ),
                    )
            except BaseException:
                # Rejected bootstrap resources are not yet owned by stop.
                if self._api is not api:
                    await drain_owned(close_unifi_protect_client(api))
                raise
        # Observers can call status/read/lifecycle APIs; never hold either lock.
        await event_bus.publish(
            "protect.connection.changed",
            {
                **self._connection_payload(),
                "camera_count": len(list_bootstrap_cameras(api)),
            },
        )
        return api

    def _handle_websocket_message(
        self, message: Any, *, provider_generation: int | None = None
    ) -> None:
        runtime = self._runtime
        if self._stopped or self._stopping or runtime is None:
            return
        if provider_generation is not None and provider_generation != self._provider_generation:
            return
        received_at, admitted_at = datetime.now(tz=UTC), monotonic()
        try:
            metadata_only = is_unifi_protect_stream_metadata_only(message)
            payload = websocket_message_payload(message)
            probe_id = None if metadata_only else self._lpr_track_probe_event_id(message)
            event = getattr(message, "new_obj", None)
            probe = (
                ProtectProbeContext(
                    event_id=probe_id,
                    received_at=received_at,
                    admitted_at=admitted_at,
                    deadline=admitted_at + 90.0,
                    timing=unifi_protect_timing_fields(event, received_at),
                    visual=unifi_protect_visual_fields(event, received_at),
                )
                if probe_id
                else None
            )
            event_type = (
                "protect.event.detected"
                if payload.get("event")
                else ("protect.camera.updated" if payload.get("camera") else "protect.updated")
            )
            runtime.admit(
                ProtectCallbackWork(
                    admitted_at=admitted_at,
                    validity=ProtectWorkValidity(self._evidence.stamp()),
                    timing=()
                    if metadata_only
                    else tuple(
                        extract_unifi_protect_lpr_observations(message, received_at=received_at)
                    ),
                    visual=()
                    if metadata_only
                    else tuple(
                        extract_unifi_protect_vehicle_visual_observations(
                            message, received_at=received_at
                        )
                    ),
                    presence=()
                    if metadata_only
                    else extract_unifi_realtime_presence_observations(
                        payload, received_at=received_at
                    ),
                    event_type=event_type,
                    payload=json.dumps(payload, separators=(",", ":")).encode(),
                    probe=probe,
                )
            )
        except Exception as exc:  # noqa: BLE001 - Failed optional extraction creates an immediate evidence gap.
            runtime.gap("callback_extraction")
            logger.debug(
                "unifi_protect_ws_extraction_failed", extra={"error_type": type(exc).__name__}
            )

    def _handle_websocket_state(
        self, channel: str, state: Any, *, provider_generation: int | None = None
    ) -> None:
        if provider_generation is not None and provider_generation != self._provider_generation:
            return
        if self._stopped or self._stopping:
            return
        normalized_state = _websocket_state_name(state)
        if normalized_state not in {"connected", "disconnected", "auth_failed"}:
            normalized_state = "unknown"
        if channel not in self._websocket_states:
            return
        previous = self._websocket_states[channel]
        self._websocket_states[channel] = normalized_state
        runtime = self._runtime
        if runtime is not None:
            if normalized_state != "connected" and normalized_state != previous:
                runtime.gap(f"websocket_{normalized_state}")
            runtime.connection_changed(
                {**self._connection_payload(), "channel": channel, "state": normalized_state}
            )

    def _realtime_connected(self) -> bool:
        return all(
            self._websocket_states[channel] == "connected" for channel in PROTECT_WEBSOCKET_CHANNELS
        )

    def _realtime_error(self) -> str | None:
        failed = [
            channel for channel, state in self._websocket_states.items() if state == "auth_failed"
        ]
        if not failed:
            return None
        return f"UniFi Protect websocket authentication failed: {', '.join(failed)}."

    def _connection_payload(self) -> dict[str, Any]:
        return {
            "configured": True,
            "connected": self._connected,
            "realtime_connected": self._realtime_connected(),
            "websocket_states": dict(self._websocket_states),
            "realtime_error": self._realtime_error(),
        }

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

        event_type = str(
            _enum_value(getattr(event, "type", None)) or _dict_get(changed_data, "type") or ""
        ).lower()
        if event_type == "smartdetectzone":
            return True

        camera = getattr(event, "camera", None)
        camera_name = str(
            getattr(camera, "display_name", "") or getattr(camera, "name", "") or ""
        ).lower()
        if "lpr" in camera_name or "license" in camera_name:
            return True

        changed_text = str(changed_data).lower()
        return (
            "licenseplate" in changed_text
            or "detectedthumbnails" in changed_text
            or "detected_thumbnails" in changed_text
        )


class _ProtectTrackPort:
    def __init__(self, api: Any, operations: ProtectOperations) -> None:
        self._api = api
        self._operations = operations

    async def fetch_track(self, event_id: str, deadline: float) -> Any:
        return await self._operations.run(
            lambda: get_unifi_protect_track(self._api, event_id),
            deadline=deadline,
        )


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


def gate_lpr_camera_from_bootstrap(
    cameras: list[Any], *, camera_identifier: str | None = None
) -> Any | None:
    identifier = _normalize_camera_identifier(camera_identifier)
    if identifier:
        for camera in cameras:
            if identifier in _camera_identifier_values(camera):
                return camera

    for camera in cameras:
        if (
            _normalize_camera_identifier(
                getattr(camera, "display_name", None) or getattr(camera, "name", None)
            )
            == GATE_LPR_CAMERA_NAME
        ):
            return camera

    for camera in cameras:
        if _normalize_camera_identifier(
            getattr(camera, "mac", None)
        ) == _normalize_camera_identifier(GATE_LPR_CAMERA_DEVICE):
            return camera

    for camera in cameras:
        label = _normalize_camera_identifier(
            getattr(camera, "display_name", None) or getattr(camera, "name", None)
        )
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


def resolve_camera_smart_zone_matches(
    camera: Any, zone_values: list[str]
) -> list[dict[str, str | None]]:
    lookup: dict[str, dict[str, str | None]] = {}
    for zone in getattr(camera, "smart_detect_zones", []) or []:
        zone_id = _string_or_none(getattr(zone, "id", None))
        zone_name = _string_or_none(getattr(zone, "name", None))
        match = {"id": zone_id, "name": zone_name}
        for label in (zone_id, zone_name):
            if label:
                lookup[_normalize_smart_zone(label)] = match

    matches: list[dict[str, str | None]] = []
    for value in zone_values:
        text = _string_or_none(value)
        if not text:
            continue
        match = lookup.get(_normalize_smart_zone(text), {})
        matches.append(
            {
                "input": text,
                "id": match.get("id") or text,
                "name": match.get("name") or text,
            }
        )
    return matches


def camera_day_night(camera: Any | None) -> tuple[str, str]:
    if camera is None:
        return "unknown", "camera_unavailable"
    is_dark = _bool_or_none(getattr(camera, "is_dark", None))
    if is_dark is None:
        camera_dict = _camera_to_dict(camera)
        if isinstance(camera_dict, dict):
            is_dark = _bool_or_none(camera_dict.get("isDark"))
    if is_dark is None:
        return "unknown", "unifi_protect.isDark_unavailable"
    return ("night" if is_dark else "day"), "unifi_protect.isDark"


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


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _camera_to_dict(camera: Any) -> dict[str, Any]:
    unifi_dict = getattr(camera, "unifi_dict", None)
    if callable(unifi_dict):
        try:
            value = unifi_dict()
            return value if isinstance(value, dict) else {}
        except Exception:  # noqa: BLE001 - Background integration failure remains observable and recoverable.
            return {}
    return {}


def _dict_get(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def _websocket_state_name(state: Any) -> str:
    name = getattr(state, "name", None)
    if name:
        return str(name).strip().lower().replace("-", "_")
    value = getattr(state, "value", state)
    if value is True:
        return "connected"
    if value is False:
        return "disconnected"
    normalized = str(value).strip().lower().replace("-", "_")
    return normalized.rsplit(".", 1)[-1]


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


@lru_cache
def get_unifi_protect_service() -> UnifiProtectIntegrationService:
    return UnifiProtectIntegrationService()
