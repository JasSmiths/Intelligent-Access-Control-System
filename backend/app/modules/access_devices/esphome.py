from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, AsyncIterator

from app.core.logging import get_logger
from app.modules.access_devices.base import (
    ACCESS_DEVICE_KIND_GARAGE_DOOR,
    ACCESS_DEVICE_KIND_GATE,
    AccessDeviceBinding,
    AccessDeviceCommandResult,
    AccessDeviceDiscoveryItem,
    AccessDeviceProviderStatus,
    AccessDeviceProviderUnavailable,
)
from app.modules.gate.base import GateState
from app.services.settings import get_runtime_config


CONNECT_TIMEOUT_SECONDS = 30.0
DISCOVERY_TIMEOUT_SECONDS = 10.0
LIVE_DISCOVERY_WAIT_SECONDS = 1.5
STATE_SAMPLE_TIMEOUT_SECONDS = 3.0
STATE_STREAM_INITIAL_RECONNECT_SECONDS = 0.5
STATE_STREAM_MAX_RECONNECT_SECONDS = 5.0
COMMAND_LIVE_STATE_WAIT_SECONDS = 0.25
COLD_COMMAND_CONNECT_BUDGET_SECONDS = 0.75
STATE_EVENT_QUEUE_MAX_SIZE = 1000

logger = get_logger(__name__)


@dataclass
class _ESPHomeStateRecord:
    state: GateState
    raw_state: str
    updated_at: datetime
    updated_monotonic: float


class ESPHomeAccessDeviceProvider:
    provider_key = "esphome"
    display_name = "ESPHome"
    state_subscription_supported = True

    def __init__(self) -> None:
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=STATE_EVENT_QUEUE_MAX_SIZE)
        self._sessions: dict[str, _ESPHomeDeviceSession] = {}
        self._sessions_lock = asyncio.Lock()

    async def configured(self) -> bool:
        return bool(await self._configured_devices())

    async def status(self, *, refresh: bool = False) -> AccessDeviceProviderStatus:
        devices = await self._configured_devices()
        if not devices:
            return AccessDeviceProviderStatus(provider=self.provider_key, configured=False)
        if refresh:
            await self._sync_sessions(devices)
        device_results: list[dict[str, Any]] = []
        for device in devices:
            session = self._sessions.get(str(device["id"]))
            device_results.append(
                {
                    **_safe_device_metadata(device),
                    "connected": bool(session and session.connected),
                    "cover_count": session.cover_count if session else None,
                    "last_error": session.last_error if session else None,
                    "last_seen_at": session.last_seen_at.isoformat() if session and session.last_seen_at else None,
                }
            )
        connected = any(device.get("connected") for device in device_results)
        errors = [str(device["last_error"]) for device in device_results if device.get("last_error")]
        return AccessDeviceProviderStatus(
            provider=self.provider_key,
            configured=True,
            connected=connected,
            degraded=bool(errors),
            last_error=errors[0] if errors else None,
            metadata={"devices": device_results},
        )

    async def discover_covers(self, device_id: str | None = None) -> list[AccessDeviceDiscoveryItem]:
        configured_devices = await self._configured_devices()
        devices = configured_devices
        if device_id:
            devices = [device for device in devices if str(device["id"]) == device_id]
            if not devices:
                raise AccessDeviceProviderUnavailable(f"ESPHome device is not configured: {device_id}")
        await self._sync_sessions(configured_devices)
        items: list[AccessDeviceDiscoveryItem] = []
        errors: list[str] = []
        for device in devices:
            session = await self._session_for_device(device)
            try:
                items.extend(await session.discovery_items(wait_timeout=LIVE_DISCOVERY_WAIT_SECONDS))
            except AccessDeviceProviderUnavailable as exc:
                errors.append(str(exc))
                logger.debug(
                    "esphome_live_discovery_unavailable",
                    extra={
                        "device_id": str(device.get("id") or ""),
                        "device_name": str(device.get("name") or ""),
                        "host": str(device.get("host") or ""),
                        "error": str(exc),
                    },
                )
                if device_id:
                    raise
        if device_id and not items:
            detail = errors[0] if errors else f"ESPHome live stream has not discovered covers for {device_id}."
            raise AccessDeviceProviderUnavailable(detail)
        return items

    async def verify_live_device(self, device_id: str) -> list[AccessDeviceDiscoveryItem]:
        devices = await self._configured_devices()
        matches = [device for device in devices if str(device["id"]) == device_id]
        if not matches:
            raise AccessDeviceProviderUnavailable(f"ESPHome device is not configured: {device_id}")
        await self._sync_sessions(devices)
        session = await self._session_for_device(matches[0])
        return await session.discovery_items(
            require_connected=True,
            wait_timeout=LIVE_DISCOVERY_WAIT_SECONDS,
        )

    async def current_state(self, binding: AccessDeviceBinding) -> GateState:
        device = await self._device_for_binding(binding)
        session = await self._session_for_device(device)
        cover = self._cover_from_binding_config(binding) or session.resolve_cover(binding)
        if cover is None:
            raise AccessDeviceProviderUnavailable(f"ESPHome cover is not available: {binding.external_id}")
        return session.current_state(int(cover.key))

    async def command_cover(
        self,
        binding: AccessDeviceBinding,
        action: str,
        reason: str,
    ) -> AccessDeviceCommandResult:
        if action not in {"open", "close"}:
            return AccessDeviceCommandResult(
                accepted=False,
                state=GateState.UNKNOWN,
                detail=f"Unsupported cover action: {action}",
                provider=self.provider_key,
                external_id=binding.external_id,
            )
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        device = await self._device_for_binding(binding)
        device_resolved_at = loop.time()
        session = await self._session_for_device(device)
        configured_cover = self._cover_from_binding_config(binding)
        cover = configured_cover or session.resolve_cover(binding)
        cover_resolution = "binding_config" if configured_cover is not None else "live_metadata"
        if session.connected and cover is not None:
            return await session.command_cover(
                binding,
                action,
                reason,
                cover=cover,
                started_at=started_at,
                device_resolved_at=device_resolved_at,
                cover_resolution=cover_resolution,
            )
        fallback_reason = (
            "live stream is not connected"
            if not session.connected
            else f"cover metadata is not available for {binding.external_id}"
        )
        return await self._command_cover_cold(
            device,
            binding,
            action,
            reason,
            started_at=started_at,
            device_resolved_at=device_resolved_at,
            fallback_reason=fallback_reason,
            configured_cover=configured_cover,
        )

    async def subscribe_state_changes(self) -> AsyncIterator[dict[str, Any]]:
        await self._sync_sessions()
        try:
            while True:
                yield await self._events.get()
        finally:
            await self.close()

    async def _configured_devices(self) -> list[dict[str, Any]]:
        config = await get_runtime_config()
        return [
            device
            for device in config.esphome_devices
            if device.get("enabled", True) and str(device.get("host") or "").strip()
        ]

    async def _sync_sessions(self, devices: list[dict[str, Any]] | None = None) -> None:
        devices = devices if devices is not None else await self._configured_devices()
        current_ids = {str(device["id"]) for device in devices}
        sessions_to_stop: list[_ESPHomeDeviceSession] = []
        async with self._sessions_lock:
            for device_id, session in list(self._sessions.items()):
                if device_id not in current_ids:
                    sessions_to_stop.append(session)
                    del self._sessions[device_id]
            for device in devices:
                device_id = str(device["id"])
                session = self._sessions.get(device_id)
                if session is None:
                    session = _ESPHomeDeviceSession(self, device, self._events)
                    self._sessions[device_id] = session
                else:
                    session.update_device(device)
                session.start()
        for session in sessions_to_stop:
            await session.stop()

    async def _session_for_device(self, device: dict[str, Any]) -> "_ESPHomeDeviceSession":
        async with self._sessions_lock:
            device_id = str(device["id"])
            session = self._sessions.get(device_id)
            if session is None:
                session = _ESPHomeDeviceSession(self, device, self._events)
                self._sessions[device_id] = session
            else:
                session.update_device(device)
            session.start()
            return session

    async def close(self) -> None:
        async with self._sessions_lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        if sessions:
            await asyncio.gather(*(session.stop() for session in sessions), return_exceptions=True)

    async def _device_for_binding(self, binding: AccessDeviceBinding) -> dict[str, Any]:
        devices = await self._configured_devices()
        device_id = str(binding.config.get("device_id") or "").strip()
        if not device_id:
            device_id, _external_id = _split_device_external_id(binding.external_id)
        if device_id:
            for device in devices:
                if str(device["id"]) == device_id:
                    return device
            raise AccessDeviceProviderUnavailable(f"ESPHome device is not configured: {device_id}")
        if not devices:
            raise AccessDeviceProviderUnavailable("ESPHome host is not configured.")
        if len(devices) > 1:
            raise AccessDeviceProviderUnavailable(
                "ESPHome binding must include a device_id when multiple ESPHome devices are configured."
            )
        return devices[0]

    async def _connected_client(
        self,
        device: dict[str, Any],
        on_stop: Any | None = None,
        *,
        timeout_budget: float | None = None,
    ) -> tuple[Any, Any]:
        host = str(device.get("host") or "").strip()
        if not host:
            raise AccessDeviceProviderUnavailable("ESPHome host is not configured.")
        aio = importlib.import_module("aioesphomeapi")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_budget if timeout_budget is not None else None
        client = None

        def remaining(default: float) -> float:
            if deadline is None:
                return default
            value = deadline - loop.time()
            if value <= 0:
                raise asyncio.TimeoutError
            return min(default, value)

        try:
            timeout = float(device.get("timeout_seconds") or CONNECT_TIMEOUT_SECONDS)
            client = aio.APIClient(
                address=host,
                port=int(device.get("port") or 6053),
                noise_psk=str(device.get("encryption_key") or "").strip() or None,
            )
            await asyncio.wait_for(client.start_resolve_host(on_stop), timeout=remaining(min(timeout, 15.0)))
            await asyncio.wait_for(client.start_connection(), timeout=remaining(timeout))
            await asyncio.wait_for(
                client.finish_connection(login=False),
                timeout=remaining(CONNECT_TIMEOUT_SECONDS),
            )
            return aio, client
        except Exception as exc:
            if client is not None:
                await _disconnect(client)
            raise AccessDeviceProviderUnavailable(_connect_error_detail(exc)) from exc

    async def _command_cover_cold(
        self,
        device: dict[str, Any],
        binding: AccessDeviceBinding,
        action: str,
        reason: str,
        *,
        started_at: float,
        device_resolved_at: float,
        fallback_reason: str,
        configured_cover: Any | None,
    ) -> AccessDeviceCommandResult:
        loop = asyncio.get_running_loop()
        cold_started_at = loop.time()
        try:
            aio, client = await self._connected_client(
                device,
                timeout_budget=COLD_COMMAND_CONNECT_BUDGET_SECONDS,
            )
        except AccessDeviceProviderUnavailable as exc:
            detail = f"{fallback_reason}; cold reconnect failed: {exc}"
            raise AccessDeviceProviderUnavailable(detail) from exc
        connected_at = loop.time()
        try:
            if configured_cover is not None:
                cover = configured_cover
            else:
                remaining = COLD_COMMAND_CONNECT_BUDGET_SECONDS - (loop.time() - cold_started_at)
                if remaining <= 0:
                    raise AccessDeviceProviderUnavailable(
                        f"{fallback_reason}; cold reconnect did not leave time to resolve cover metadata"
                    )
                cover = await asyncio.wait_for(self._resolve_cover(aio, client, binding), timeout=remaining)
            cover_resolved_at = loop.time()
            client.cover_command(key=int(cover.key), position=1.0 if action == "open" else 0.0)
            command_sent_at = loop.time()
            state = await self._sample_cover_state(
                aio,
                client,
                int(cover.key),
                timeout=COMMAND_LIVE_STATE_WAIT_SECONDS,
            )
            sampled_at = loop.time()
            timing_ms = {
                "device_lookup": _elapsed_ms(started_at, device_resolved_at),
                "connect": _elapsed_ms(device_resolved_at, connected_at),
                "cover_resolution": _elapsed_ms(connected_at, cover_resolved_at),
                "command_call": _elapsed_ms(cover_resolved_at, command_sent_at),
                "elapsed_to_send": _elapsed_ms(started_at, command_sent_at),
                "state_sample": _elapsed_ms(command_sent_at, sampled_at),
                "total": _elapsed_ms(started_at, sampled_at),
            }
            metadata = {
                "device_id": str(device["id"]),
                "device_name": str(device["name"]),
                "host": str(device["host"]),
                "key": int(cover.key),
                "object_id": str(getattr(cover, "object_id", "") or ""),
                "cover_resolution": "binding_config" if configured_cover is not None else "discovery",
                "command_transport": "cold_connect",
                "live_stream_connected": False,
                "fallback_reason": fallback_reason,
                "timing_ms": timing_ms,
            }
            logger.info(
                "esphome_cover_command_state_sampled",
                extra={
                    "device_id": metadata["device_id"],
                    "device_name": metadata["device_name"],
                    "host": metadata["host"],
                    "external_id": binding.external_id,
                    "action": action,
                    "state": state.value,
                    "key": int(cover.key),
                    "command_transport": "cold_connect",
                    "fallback_reason": fallback_reason,
                    "timing_ms": timing_ms,
                },
            )
            return AccessDeviceCommandResult(
                accepted=True,
                state=state,
                detail=reason,
                provider=self.provider_key,
                external_id=binding.external_id,
                metadata=metadata,
            )
        except AccessDeviceProviderUnavailable:
            raise
        except Exception as exc:
            raise AccessDeviceProviderUnavailable(str(exc)) from exc
        finally:
            await _disconnect(client)

    async def _cover_metadata_by_key(self, aio: Any, client: Any, device: dict[str, Any]) -> dict[int, dict[str, Any]]:
        entities, _services = await asyncio.wait_for(
            client.list_entities_services(),
            timeout=DISCOVERY_TIMEOUT_SECONDS,
        )
        metadata: dict[int, dict[str, Any]] = {}
        for entity in entities:
            if not _is_cover_info(aio, entity):
                continue
            record = _cover_metadata_from_entity(entity, device)
            metadata[int(record["key"])] = record
        return metadata

    async def _resolve_cover(self, aio: Any, client: Any, binding: AccessDeviceBinding) -> Any:
        entities, _services = await asyncio.wait_for(
            client.list_entities_services(),
            timeout=DISCOVERY_TIMEOUT_SECONDS,
        )
        _device_id, external_id = _split_device_external_id(binding.external_id)
        configured_key = binding.config.get("key")
        for entity in entities:
            if not _is_cover_info(aio, entity):
                continue
            object_id = str(getattr(entity, "object_id", "") or "")
            name = str(getattr(entity, "name", "") or "")
            if (
                external_id in {object_id, name, str(getattr(entity, "key", ""))}
                or (configured_key is not None and str(configured_key) == str(getattr(entity, "key", "")))
            ):
                return entity
        raise AccessDeviceProviderUnavailable(f"ESPHome cover is not available: {external_id}")

    def _cover_from_binding_config(self, binding: AccessDeviceBinding) -> Any | None:
        configured_key = binding.config.get("key")
        if configured_key is None:
            return None
        try:
            key = int(configured_key)
        except (TypeError, ValueError) as exc:
            raise AccessDeviceProviderUnavailable("ESPHome cover binding has an invalid key.") from exc
        _device_id, external_id = _split_device_external_id(binding.external_id)
        object_id = str(binding.config.get("object_id") or external_id or "")
        name = str(binding.config.get("name") or object_id or key)
        return SimpleNamespace(key=key, object_id=object_id, name=name)

    async def _sample_cover_state(
        self,
        aio: Any,
        client: Any,
        key: int,
        *,
        timeout: float = STATE_SAMPLE_TIMEOUT_SECONDS,
    ) -> GateState:
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=1)

        def on_state(state: Any) -> None:
            if not _is_cover_state(aio, state) or int(getattr(state, "key", -1)) != key:
                return
            try:
                queue.put_nowait(state)
            except asyncio.QueueFull:
                logger.debug("esphome_state_sample_queue_full", extra={"key": key})

        try:
            client.subscribe_states(on_state)
            state = await asyncio.wait_for(queue.get(), timeout=timeout)
            return _cover_state_label(state)
        except asyncio.TimeoutError:
            logger.debug("esphome_state_sample_timeout", extra={"key": key, "timeout": timeout})
            return GateState.UNKNOWN
        except Exception as exc:
            logger.warning("esphome_state_sample_failed", extra={"key": key, "error": str(exc)})
            return GateState.UNKNOWN


class _ESPHomeDeviceSession:
    def __init__(
        self,
        provider: ESPHomeAccessDeviceProvider,
        device: dict[str, Any],
        events: asyncio.Queue[dict[str, Any]],
    ) -> None:
        self.provider = provider
        self.device = dict(device)
        self.events = events
        self.task: asyncio.Task | None = None
        self.stop_event = asyncio.Event()
        self.command_lock = asyncio.Lock()
        self.client: Any | None = None
        self.aio: Any | None = None
        self.connected = False
        self.last_error: str | None = None
        self.cover_metadata: dict[int, dict[str, Any]] = {}
        self.states: dict[int, _ESPHomeStateRecord] = {}
        self.state_events: dict[int, asyncio.Event] = {}
        self.metadata_loaded = asyncio.Event()
        self.last_seen_at: datetime | None = None

    @property
    def device_id(self) -> str:
        return str(self.device["id"])

    @property
    def cover_count(self) -> int:
        return len(self.cover_metadata)

    def update_device(self, device: dict[str, Any]) -> None:
        self.device = dict(device)

    def start(self) -> None:
        if self.task and not self.task.done():
            return
        self.stop_event = asyncio.Event()
        self.task = asyncio.create_task(
            self._run(),
            name=f"esphome-{self.device_id}-cover-state-stream",
        )

    async def stop(self) -> None:
        self.stop_event.set()
        task = self.task
        client = self.client
        if task:
            task.cancel()
        if client is not None:
            await _disconnect(client)
        if task:
            await asyncio.gather(task, return_exceptions=True)
        self.task = None
        self.client = None
        self.aio = None
        self.connected = False

    def resolve_cover(self, binding: AccessDeviceBinding) -> Any | None:
        _device_id, external_id = _split_device_external_id(binding.external_id)
        configured_key = binding.config.get("key")
        for key, metadata in self.cover_metadata.items():
            if configured_key is not None and str(configured_key) == str(key):
                return SimpleNamespace(
                    key=key,
                    object_id=str(metadata.get("object_id") or ""),
                    name=str(metadata.get("name") or key),
                )
            candidates = {
                str(metadata.get("external_id") or "").strip(),
                str(metadata.get("object_id") or "").strip(),
                str(metadata.get("name") or "").strip(),
                str(key),
            }
            if external_id in {candidate for candidate in candidates if candidate}:
                return SimpleNamespace(
                    key=key,
                    object_id=str(metadata.get("object_id") or ""),
                    name=str(metadata.get("name") or key),
                )
        return None

    def current_state(self, key: int) -> GateState:
        if not self.connected:
            raise AccessDeviceProviderUnavailable(
                f"ESPHome native API stream is not connected for {self.device.get('name') or self.device_id}."
            )
        record = self.states.get(key)
        return record.state if record else GateState.UNKNOWN

    async def discovery_items(
        self,
        *,
        require_connected: bool = False,
        wait_timeout: float = LIVE_DISCOVERY_WAIT_SECONDS,
    ) -> list[AccessDeviceDiscoveryItem]:
        if not self.cover_metadata:
            try:
                await asyncio.wait_for(self.metadata_loaded.wait(), timeout=wait_timeout)
            except asyncio.TimeoutError:
                pass
        if require_connected and not self.connected:
            raise AccessDeviceProviderUnavailable(
                self.last_error
                or f"ESPHome native API stream is not connected for {self.device.get('name') or self.device_id}."
            )
        if not self.cover_metadata:
            raise AccessDeviceProviderUnavailable(
                self.last_error
                or f"ESPHome live stream has not discovered covers for {self.device.get('name') or self.device_id}."
            )
        items: list[AccessDeviceDiscoveryItem] = []
        for key, metadata in sorted(self.cover_metadata.items(), key=lambda item: str(item[1].get("name") or item[0])):
            record = self.states.get(key)
            item_metadata = {
                **metadata,
                "discovery_source": "live_stream",
                "stream_connected": self.connected,
            }
            if record is not None:
                item_metadata["raw_state"] = record.raw_state
                item_metadata["state_updated_at"] = record.updated_at.isoformat()
            items.append(
                AccessDeviceDiscoveryItem(
                    external_id=str(metadata.get("external_id") or key),
                    name=str(metadata.get("name") or metadata.get("object_id") or key),
                    kind=str(metadata.get("kind") or ACCESS_DEVICE_KIND_GATE),
                    state=record.state.value if record is not None else GateState.UNKNOWN.value,
                    metadata=item_metadata,
                )
            )
        return items

    async def command_cover(
        self,
        binding: AccessDeviceBinding,
        action: str,
        reason: str,
        *,
        cover: Any,
        started_at: float,
        device_resolved_at: float,
        cover_resolution: str,
    ) -> AccessDeviceCommandResult:
        async with self.command_lock:
            if not self.connected or self.client is None:
                raise AccessDeviceProviderUnavailable(
                    f"ESPHome native API stream is not connected for {self.device.get('name') or self.device_id}."
                )
            loop = asyncio.get_running_loop()
            live_ready_at = loop.time()
            key = int(cover.key)
            try:
                self.client.cover_command(key=key, position=1.0 if action == "open" else 0.0)
            except Exception as exc:
                detail = _connect_error_detail(exc)
                await self._force_disconnect(detail)
                raise AccessDeviceProviderUnavailable(detail) from exc
            command_sent_at = loop.time()
            timing_ms = {
                "device_lookup": _elapsed_ms(started_at, device_resolved_at),
                "live_ready": _elapsed_ms(device_resolved_at, live_ready_at),
                "cover_resolution": 0.0,
                "command_call": _elapsed_ms(live_ready_at, command_sent_at),
                "elapsed_to_send": _elapsed_ms(started_at, command_sent_at),
            }
            logger.info(
                "esphome_cover_command_sent",
                extra={
                    "device_id": self.device_id,
                    "device_name": str(self.device["name"]),
                    "host": str(self.device["host"]),
                    "external_id": binding.external_id,
                    "action": action,
                    "key": key,
                    "object_id": str(getattr(cover, "object_id", "") or ""),
                    "cover_resolution": cover_resolution,
                    "command_transport": "live_stream",
                    "timing_ms": timing_ms,
                },
            )
            state = await self.wait_state_after(
                key,
                after=command_sent_at,
                timeout=COMMAND_LIVE_STATE_WAIT_SECONDS,
            )
            sampled_at = loop.time()
            timing_ms = {
                **timing_ms,
                "state_sample": _elapsed_ms(command_sent_at, sampled_at),
                "total": _elapsed_ms(started_at, sampled_at),
            }
            metadata = {
                "device_id": self.device_id,
                "device_name": str(self.device["name"]),
                "host": str(self.device["host"]),
                "key": key,
                "object_id": str(getattr(cover, "object_id", "") or ""),
                "cover_resolution": cover_resolution,
                "command_transport": "live_stream",
                "live_stream_connected": True,
                "timing_ms": timing_ms,
            }
            logger.info(
                "esphome_cover_command_state_sampled",
                extra={
                    "device_id": metadata["device_id"],
                    "device_name": metadata["device_name"],
                    "host": metadata["host"],
                    "external_id": binding.external_id,
                    "action": action,
                    "state": state.value,
                    "key": key,
                    "command_transport": "live_stream",
                    "timing_ms": timing_ms,
                },
            )
            return AccessDeviceCommandResult(
                accepted=True,
                state=state,
                detail=reason,
                provider=self.provider.provider_key,
                external_id=binding.external_id,
                metadata=metadata,
            )

    async def wait_state_after(self, key: int, *, after: float, timeout: float) -> GateState:
        record = self.states.get(key)
        if record and record.updated_monotonic >= after:
            return record.state
        event = self.state_events.setdefault(key, asyncio.Event())
        event.clear()
        record = self.states.get(key)
        if record and record.updated_monotonic >= after:
            return record.state
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        record = self.states.get(key)
        return record.state if record else GateState.UNKNOWN

    async def _run(self) -> None:
        reconnect_delay = STATE_STREAM_INITIAL_RECONNECT_SECONDS
        while not self.stop_event.is_set():
            client = None
            expected_disconnect = False
            stopped = asyncio.Event()

            async def on_stop(was_expected: bool, stopped_event: asyncio.Event = stopped) -> None:
                nonlocal expected_disconnect
                expected_disconnect = was_expected
                stopped_event.set()

            try:
                if not self.cover_metadata:
                    self.metadata_loaded.clear()
                aio, client = await self.provider._connected_client(self.device, on_stop=on_stop)
                self.cover_metadata = await self.provider._cover_metadata_by_key(aio, client, self.device)
                self.metadata_loaded.set()
                self.aio = aio
                self.client = client
                self.connected = True
                self.last_error = None
                self._queue_event(
                    {
                        "type": "connected",
                        "provider": self.provider.provider_key,
                        "device_id": self.device_id,
                        "device_name": str(self.device["name"]),
                        "host": str(self.device["host"]),
                        "cover_count": len(self.cover_metadata),
                    }
                )
                client.subscribe_states(self._on_state)
                reconnect_delay = STATE_STREAM_INITIAL_RECONNECT_SECONDS
                await stopped.wait()
                if self.stop_event.is_set():
                    return
                detail = (
                    "ESPHome native API connection closed."
                    if expected_disconnect
                    else "ESPHome native API connection dropped."
                )
                raise AccessDeviceProviderUnavailable(detail)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                detail = _connect_error_detail(exc) if isinstance(exc, Exception) else str(exc)
                self.connected = False
                self.last_error = detail
                self.client = None
                self.aio = None
                self.metadata_loaded.set()
                self._queue_disconnected(detail)
                await self._sleep_before_reconnect(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, STATE_STREAM_MAX_RECONNECT_SECONDS)
            finally:
                if client is not None:
                    await _disconnect(client)
                if self.client is client:
                    self.client = None
                    self.aio = None
                    self.connected = False

    def _on_state(self, state: Any) -> None:
        aio = self.aio
        if aio is None or not _is_cover_state(aio, state):
            return
        key = int(getattr(state, "key", -1))
        metadata = self.cover_metadata.get(key)
        if not metadata:
            return
        now = datetime.now(tz=UTC)
        record = _ESPHomeStateRecord(
            state=_cover_state_label(state),
            raw_state=_cover_raw_state_label(state),
            updated_at=now,
            updated_monotonic=asyncio.get_running_loop().time(),
        )
        self.states[key] = record
        self.last_seen_at = now
        event = self.state_events.get(key)
        if event:
            event.set()
        self._queue_event(
            {
                "type": "state",
                "provider": self.provider.provider_key,
                **metadata,
                "state": record.state.value,
                "raw_state": record.raw_state,
            }
        )

    async def _force_disconnect(self, detail: str) -> None:
        self.connected = False
        self.last_error = detail
        self._queue_disconnected(detail)
        client = self.client
        self.client = None
        self.aio = None
        if client is not None:
            await _disconnect(client)

    async def _sleep_before_reconnect(self, delay: float) -> None:
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            return

    def _queue_disconnected(self, detail: str) -> None:
        self._queue_event(
            {
                "type": "disconnected",
                "provider": self.provider.provider_key,
                "device_id": self.device_id,
                "device_name": str(self.device.get("name") or ""),
                "host": str(self.device.get("host") or ""),
                "last_error": detail,
            }
        )

    def _queue_event(self, event: dict[str, Any]) -> None:
        try:
            self.events.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "esphome_state_event_queue_full",
                extra={
                    "provider": event.get("provider"),
                    "device_id": event.get("device_id"),
                    "type": event.get("type"),
                },
            )


async def _disconnect(client: Any) -> None:
    try:
        await client.disconnect()
    except Exception as exc:
        logger.debug("esphome_disconnect_failed", extra={"error": str(exc)})


def _is_cover_info(aio: Any, entity: Any) -> bool:
    cover_info = getattr(aio, "CoverInfo", None)
    if cover_info is not None:
        return isinstance(entity, cover_info)
    return entity.__class__.__name__ == "CoverInfo"


def _is_cover_state(aio: Any, state: Any) -> bool:
    cover_state = getattr(aio, "CoverState", None)
    if cover_state is not None:
        return isinstance(state, cover_state)
    return state.__class__.__name__ == "CoverState"


def _cover_metadata_from_entity(entity: Any, device: dict[str, Any]) -> dict[str, Any]:
    key = int(getattr(entity, "key"))
    object_id = str(getattr(entity, "object_id", "") or "")
    name = str(getattr(entity, "name", "") or object_id or key)
    external_id = object_id or name or str(key)
    device_class = str(getattr(entity, "device_class", "") or "").lower()
    label = f"{name} {object_id}".lower()
    kind = (
        ACCESS_DEVICE_KIND_GARAGE_DOOR
        if device_class == "garage" or "garage" in label or "door" in label
        else ACCESS_DEVICE_KIND_GATE
    )
    return {
        "device_id": str(device["id"]),
        "device_name": str(device["name"]),
        "host": str(device["host"]),
        "key": key,
        "object_id": object_id,
        "external_id": external_id,
        "name": name,
        "device_class": device_class,
        "kind": kind,
        "supports_position": bool(getattr(entity, "supports_position", False)),
        "supports_stop": bool(getattr(entity, "supports_stop", False)),
    }


def _cover_state_label(state: Any) -> GateState:
    operation = _enum_name_or_value(getattr(state, "current_operation", None))
    if operation in {"IS_OPENING", "OPENING", 1}:
        return GateState.OPENING
    if operation in {"IS_CLOSING", "CLOSING", 2}:
        return GateState.CLOSING
    position = getattr(state, "position", None)
    if position is not None:
        try:
            return GateState.CLOSED if float(position) <= 0.0 else GateState.OPEN
        except (TypeError, ValueError):
            pass
    legacy = _enum_name_or_value(getattr(state, "legacy_state", None))
    if legacy in {"CLOSED", 1}:
        return GateState.CLOSED
    if legacy in {"OPEN", 0}:
        return GateState.OPEN
    return GateState.UNKNOWN


def _cover_raw_state_label(state: Any) -> str:
    operation = _enum_name_or_value(getattr(state, "current_operation", None))
    legacy = _enum_name_or_value(getattr(state, "legacy_state", None))
    position = getattr(state, "position", None)
    parts = []
    if operation is not None:
        parts.append(f"operation={operation}")
    if position is not None:
        parts.append(f"position={position}")
    if legacy is not None:
        parts.append(f"legacy={legacy}")
    return ", ".join(parts)[:80] or _cover_state_label(state).value


def _enum_name_or_value(value: Any) -> str | int | None:
    if value is None:
        return None
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    raw_value = getattr(value, "value", None)
    if raw_value is not None:
        return raw_value
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def _elapsed_ms(start: float, end: float) -> float:
    return round(max(0.0, end - start) * 1000.0, 3)


def _split_device_external_id(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    if ":" not in text:
        return "", text
    device_id, external_id = text.split(":", 1)
    if not device_id or not external_id:
        return "", text
    return device_id.strip(), external_id.strip()


def _safe_device_metadata(device: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(device.get("id") or ""),
        "name": str(device.get("name") or ""),
        "host": str(device.get("host") or ""),
        "port": int(device.get("port") or 6053),
        "enabled": bool(device.get("enabled", True)),
        "encryption_key_configured": bool(str(device.get("encryption_key") or "").strip()),
    }


def _connect_error_detail(exc: Exception) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "Timed out while connecting to ESPHome. Check host, port, encryption key, and reachability."
    detail = str(exc).strip()
    normalized = detail.lower()
    if "finishing connection cancelled" in normalized or "connection lost" in normalized:
        return "ESPHome native API handshake did not complete. Check the encryption key and port 6053."
    return detail or exc.__class__.__name__
