"""Bounded optional UniFi stream processing; durable webhook intake is separate."""

import asyncio
import json
import sys
from collections import OrderedDict, deque
from contextvars import ContextVar
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Protocol

from app.core.logging import get_logger
from app.services.event_bus import event_bus
from app.services.lpr_timing import (
    LprTimingObservation,
    extract_unifi_protect_track_observations,
    get_lpr_timing_recorder,
)
from app.services.unifi_protect_contracts import (
    ProtectEvidenceGeneration,
    ProtectProbeContext,
    ProtectWorkValidity,
)
from app.services.vehicle_visual_detections import (
    VehiclePresenceObservation,
    VehicleVisualObservation,
    extract_unifi_protect_track_vehicle_visual_observations,
    extract_unifi_track_presence_observations,
    get_vehicle_presence_tracker,
    get_vehicle_visual_detection_recorder,
)

_current_runtime: ContextVar["UnifiProtectRuntime | None"] = ContextVar(
    "protect_runtime", default=None
)

logger = get_logger(__name__)
CALLBACK_RECORD_CAPACITY = 256
CALLBACK_BYTE_CAPACITY = 16 * 1024 * 1024
PROBE_PENDING_CAPACITY = 64
PROBE_WORKERS = 4
PROBE_BYTE_CAPACITY = 16 * 1024 * 1024
RETENTION_CAPACITY = 4096
RETENTION_TTL_SECONDS = 600.0
UPDATE_MIN_INTERVAL_SECONDS = 5.0
PROBE_DELAYS_SECONDS = (0.0, 0.5, 0.5, 1.0, 2.0, 4.0, 4.0, 6.0, 7.0)
# Covers deque/set/dict slot over-allocation and the charged-entry tuple.
ENTRY_CONTAINER_ALLOWANCE = 1024


class ProtectProbePort(Protocol):
    async def fetch_track(self, event_id: str, deadline: float) -> Any: ...


@dataclass(frozen=True, slots=True)
class ProtectCallbackWork:
    admitted_at: float
    validity: ProtectWorkValidity
    timing: tuple[LprTimingObservation, ...]
    visual: tuple[VehicleVisualObservation, ...]
    presence: tuple[VehiclePresenceObservation, ...]
    event_type: str
    payload: bytes
    probe: ProtectProbeContext | None = None


@dataclass(slots=True)
class ProtectRuntimeCounters:
    accepted: int = 0
    handled: int = 0
    coalesced: int = 0
    callback_drops: int = 0
    callback_oversize: int = 0
    probe_drops: int = 0
    probe_oversize: int = 0
    stale_callbacks: int = 0
    stale_probes: int = 0
    stream_gaps: int = 0
    last_gap_reason: str | None = None
    last_gap_at: str | None = None


class ProtectRetention:
    def __init__(self) -> None:
        self._entries: OrderedDict[str, float] = OrderedDict()

    def _expire(self, now: float) -> None:
        while self._entries and next(iter(self._entries.values())) <= now - RETENTION_TTL_SECONDS:
            self._entries.popitem(last=False)

    def get(self, key: str, now: float) -> float | None:
        self._expire(now)
        return self._entries.get(key)

    def put(self, key: str, now: float) -> None:
        self._expire(now)
        self._entries.pop(key, None)
        self._entries[key] = now
        while len(self._entries) > RETENTION_CAPACITY:
            self._entries.popitem(last=False)

    def size(self, now: float) -> int:
        self._expire(now)
        return len(self._entries)


def retained_size(value: object) -> int:
    """Conservative deep charge: repeated references are deliberately counted."""
    size = sys.getsizeof(value)
    if is_dataclass(value) and not isinstance(value, type):
        namespace = getattr(value, "__dict__", None)
        if namespace is not None:
            size += sys.getsizeof(namespace)
        return size + sum(retained_size(getattr(value, field.name)) for field in fields(value))
    if isinstance(value, (tuple, list)):
        return size + sum(retained_size(item) for item in value)
    if isinstance(value, dict):
        return size + sum(retained_size(key) + retained_size(item) for key, item in value.items())
    return size


class UnifiProtectRuntime:
    def __init__(
        self,
        probe_port: ProtectProbePort,
        evidence: ProtectEvidenceGeneration,
        counters: ProtectRuntimeCounters,
    ) -> None:
        self._probe_port = probe_port
        self.evidence = evidence
        self.counters = counters
        self._callbacks: deque[tuple[ProtectCallbackWork, int]] = deque()
        self._probes: deque[tuple[ProtectProbeContext, ProtectWorkValidity, int]] = deque()
        self._probe_ids: set[str] = set()
        self._active_probe_contexts: dict[int, ProtectProbeContext] = {}
        self._callback_count = 0
        self._callback_bytes = 0
        self._probe_bytes = 0
        self._active_callback: ProtectCallbackWork | None = None
        self._callback_wake = asyncio.Event()
        self._probe_wake = asyncio.Event()
        self._connection_update: bytes | None = None
        self._updates = ProtectRetention()
        self._finished = ProtectRetention()
        self._tasks: tuple[asyncio.Task[None], ...] = ()
        self._admitting = True

    def start(self) -> None:
        if self._tasks:
            return
        self._tasks = (
            asyncio.create_task(self._consume(), name="unifi-protect-callback-consumer"),
            *(
                asyncio.create_task(self._probe_worker(index), name=f"unifi-protect-probe:{index}")
                for index in range(PROBE_WORKERS)
            ),
        )

    def owns_current(self) -> bool:
        return _current_runtime.get() is self or asyncio.current_task() in self._tasks

    def close_admission(self) -> None:
        self._admitting = False
        self.evidence.invalidate()

    def gap(self, reason: str) -> None:
        self.evidence.invalidate()
        self.counters.stream_gaps += 1
        self.counters.last_gap_reason = reason
        self.counters.last_gap_at = datetime.now(UTC).isoformat()

    def admit(self, work: ProtectCallbackWork) -> bool:
        if not self._admitting:
            self.counters.callback_drops += 1
            return False
        charge = retained_size(work) + ENTRY_CONTAINER_ALLOWANCE
        if (
            self._callback_count >= CALLBACK_RECORD_CAPACITY
            or self._callback_bytes + charge > CALLBACK_BYTE_CAPACITY
        ):
            self.counters.callback_drops += 1
            self.counters.callback_oversize += int(charge > CALLBACK_BYTE_CAPACITY)
            self.gap("callback_capacity")
            return False
        self._callbacks.append((work, charge))
        self._callback_count += 1
        self._callback_bytes += charge
        self.counters.accepted += 1
        self._callback_wake.set()
        return True

    def connection_changed(self, payload: dict[str, Any]) -> None:
        if not self._admitting:
            return
        if self._connection_update is not None:
            self.counters.coalesced += 1
        self._connection_update = json.dumps(payload, separators=(",", ":")).encode()
        self._callback_wake.set()

    def _admit_probe(self, context: ProtectProbeContext, validity: ProtectWorkValidity) -> None:
        now = monotonic()
        if (
            context.event_id in self._probe_ids
            or self._finished.get(context.event_id, now) is not None
        ):
            return
        if not validity.current() or now >= context.deadline:
            self.counters.stale_probes += 1
            return
        charge = retained_size(context) + retained_size(validity) + ENTRY_CONTAINER_ALLOWANCE
        if (
            len(self._probes) >= PROBE_PENDING_CAPACITY
            or self._probe_bytes + charge > PROBE_BYTE_CAPACITY
        ):
            self.counters.probe_drops += 1
            self.counters.probe_oversize += int(charge > PROBE_BYTE_CAPACITY)
            self.gap("probe_capacity")
            return
        self._probe_ids.add(context.event_id)
        self._probes.append((context, validity, charge))
        self._probe_bytes += charge
        self._probe_wake.set()

    async def _consume(self) -> None:
        _current_runtime.set(self)
        while self._admitting:
            await self._callback_wake.wait()
            self._callback_wake.clear()
            payload, self._connection_update = self._connection_update, None
            if payload is not None:
                await self._publish("protect.connection.changed", json.loads(payload))
            while self._callbacks:
                work, charge = self._callbacks.popleft()
                self._active_callback = work
                payload_dict: dict[str, Any] | None = None
                try:
                    if not work.validity.current():
                        self.counters.stale_callbacks += 1
                        continue
                    if work.probe is not None:
                        self._admit_probe(
                            work.probe,
                            ProtectWorkValidity(work.validity.stamp, work.probe.deadline),
                        )
                    await get_lpr_timing_recorder().record_observations(work.timing, work.validity)
                    await get_vehicle_visual_detection_recorder().record_observations(
                        work.visual, work.validity
                    )
                    await get_vehicle_presence_tracker().record_observations(
                        work.presence, work.validity
                    )
                    if work.validity.current():
                        payload_dict = json.loads(work.payload)
                        if self._should_publish(work.event_type, payload_dict):
                            await self._publish(work.event_type, payload_dict)
                    self.counters.handled += 1
                except Exception as exc:  # noqa: BLE001 - Optional stream failure invalidates current evidence and remains observable.
                    self.gap("callback_processing")
                    logger.warning(
                        "unifi_protect_callback_failed", extra={"error_type": type(exc).__name__}
                    )
                finally:
                    self._active_callback = None
                    self._callback_count -= 1
                    self._callback_bytes -= charge
                    # Idle workers must not retain a just-released record or its
                    # expanded publication payload in their coroutine locals.
                    del work, charge
                    payload_dict = None

    def _should_publish(self, event_type: str, payload: dict[str, Any]) -> bool:
        if event_type not in {"protect.updated", "protect.camera.updated"}:
            return True
        key = f"{event_type}:{payload.get('object_id') or payload.get('model') or 'global'}"
        now = monotonic()
        previous = self._updates.get(key, now)
        if previous is not None and now - previous < UPDATE_MIN_INTERVAL_SECONDS:
            self.counters.coalesced += 1
            return False
        self._updates.put(key, now)
        return True

    async def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        try:
            await event_bus.publish(event_type, payload)
        except Exception as exc:  # noqa: BLE001 - Optional publication cannot terminate the fixed worker.
            logger.warning("unifi_protect_publish_failed", extra={"error_type": type(exc).__name__})

    async def _probe_worker(self, index: int) -> None:
        _current_runtime.set(self)
        while self._admitting:
            await self._probe_wake.wait()
            if not self._probes:
                self._probe_wake.clear()
                continue
            context, validity, charge = self._probes.popleft()
            if not self._probes:
                self._probe_wake.clear()
            self._active_probe_contexts[index] = context
            try:
                await self._probe(context, validity)
            except Exception as exc:  # noqa: BLE001 - SDK failures must release capacity without killing a fixed worker.
                logger.debug("unifi_protect_probe_failed", extra={"error_type": type(exc).__name__})
            finally:
                self._active_probe_contexts.pop(index, None)
                self._probe_ids.discard(context.event_id)
                self._probe_bytes -= charge
                self._finished.put(context.event_id, monotonic())
                del context, validity, charge

    async def _probe(self, context: ProtectProbeContext, validity: ProtectWorkValidity) -> None:
        for attempt, delay in enumerate(PROBE_DELAYS_SECONDS, start=1):
            if not validity.current():
                self.counters.stale_probes += 1
                return
            if delay:
                await asyncio.sleep(min(delay, max(0.0, context.deadline - monotonic())))
            if not validity.current():
                self.counters.stale_probes += 1
                return
            try:
                track = await self._probe_port.fetch_track(context.event_id, context.deadline)
            except Exception as exc:  # noqa: BLE001 - Retriable SDK errors retain the immutable original deadline.
                logger.debug(
                    "unifi_protect_probe_attempt_failed",
                    extra={"attempt": attempt, "error_type": type(exc).__name__},
                )
                continue
            if not validity.current():
                self.counters.stale_probes += 1
                return
            timing = tuple(
                extract_unifi_protect_track_observations(
                    track,
                    context=context.timing,
                    event_id=context.event_id,
                    received_at=context.received_at,
                    probe_attempt=attempt,
                )
            )
            visual = tuple(
                extract_unifi_protect_track_vehicle_visual_observations(
                    track,
                    context=context.visual,
                    event_id=context.event_id,
                    received_at=context.received_at,
                    probe_attempt=attempt,
                )
            )
            presence = extract_unifi_track_presence_observations(
                track,
                context=context.visual,
                event_id=context.event_id,
                received_at=context.received_at,
                probe_attempt=attempt,
            )
            del track
            await get_lpr_timing_recorder().record_observations(timing, validity)
            await get_vehicle_visual_detection_recorder().record_observations(visual, validity)
            await get_vehicle_presence_tracker().record_observations(presence, validity)
            if timing:
                return

    async def stop(self) -> None:
        self.close_admission()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = ()
        self.counters.callback_drops += len(self._callbacks)
        self.counters.probe_drops += len(self._probes)
        self._callbacks.clear()
        self._probes.clear()
        self._probe_ids.clear()
        self._connection_update = None
        self._callback_count = self._callback_bytes = self._probe_bytes = 0
        self._updates = ProtectRetention()
        self._finished = ProtectRetention()

    def status(self) -> dict[str, Any]:
        now = monotonic()
        callback_times = [work.admitted_at for work, _ in self._callbacks]
        if self._active_callback is not None:
            callback_times.append(self._active_callback.admitted_at)
        probe_times = [context.admitted_at for context, _, _ in self._probes]
        probe_times.extend(context.admitted_at for context in self._active_probe_contexts.values())
        return {
            "callback_record_capacity": CALLBACK_RECORD_CAPACITY,
            "callback_byte_capacity": CALLBACK_BYTE_CAPACITY,
            "callback_records": self._callback_count,
            "callback_bytes": self._callback_bytes,
            "callback_oldest_age_seconds": max(0.0, now - min(callback_times))
            if callback_times
            else 0.0,
            "probe_pending_capacity": PROBE_PENDING_CAPACITY,
            "probe_workers": PROBE_WORKERS,
            "probe_byte_capacity": PROBE_BYTE_CAPACITY,
            "probe_bytes": self._probe_bytes,
            "probe_pending": len(self._probes),
            "probe_active": len(self._active_probe_contexts),
            "probe_oldest_age_seconds": max(0.0, now - min(probe_times)) if probe_times else 0.0,
            "finished_ids": self._finished.size(now),
            "update_keys": self._updates.size(now),
            "retention_capacity": RETENTION_CAPACITY,
            "retention_ttl_seconds": RETENTION_TTL_SECONDS,
            "evidence_generation": self.evidence.value,
            **{field.name: getattr(self.counters, field.name) for field in fields(self.counters)},
        }
