import asyncio
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
from app.services.settings import get_runtime_config

logger = get_logger(__name__)

PROTECT_UPDATE_PUBLISH_MIN_INTERVAL_SECONDS = 5.0


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

    async def list_events(
        self,
        *,
        camera_id: str | None = None,
        event_type: str | None = None,
        limit: int = 25,
        since=None,
    ) -> list[dict[str, Any]]:
        api = await self._ensure_api(subscribe=True)
        events = await list_unifi_protect_events(
            api,
            camera_id=camera_id,
            event_type=event_type,
            limit=limit,
            since=since,
        )
        return [serialize_unifi_event(event) for event in events]

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
        try:
            payload = websocket_message_payload(message)
        except Exception as exc:
            logger.debug("unifi_protect_ws_payload_failed", extra={"error": str(exc)})
            return

        event_type = "protect.updated"
        if payload.get("camera"):
            event_type = "protect.camera.updated"
        if payload.get("event"):
            event_type = "protect.event.detected"
        if event_type in {"protect.updated", "protect.camera.updated"} and not self._should_publish_update(event_type, payload):
            return
        try:
            asyncio.get_running_loop().create_task(event_bus.publish(event_type, payload))
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


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@lru_cache
def get_unifi_protect_service() -> UnifiProtectIntegrationService:
    return UnifiProtectIntegrationService()
