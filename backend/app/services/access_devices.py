from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import AccessDevice, AccessDeviceProviderBinding, GateStateObservation
from app.modules.access_devices.base import (
    ACCESS_DEVICE_KIND_GARAGE_DOOR,
    ACCESS_DEVICE_KIND_GATE,
    ACCESS_DEVICE_KINDS,
    AccessDeviceBinding,
    AccessDeviceCommandResult,
    AccessDeviceEntity,
    AccessDeviceProviderUnavailable,
)
from app.modules.access_devices.registry import get_access_device_provider
from app.modules.access_devices.registry import access_device_provider_keys
from app.modules.gate.base import GateState
from app.modules.home_assistant.covers import normalize_cover_entities
from app.services.event_bus import event_bus
from app.services.settings import get_runtime_config

logger = get_logger(__name__)


STATE_POLL_INTERVAL_SECONDS = 10.0
COMMAND_PROVIDER_MAX_ATTEMPTS = 1
CLOSE_COMMAND_PROVIDER_MAX_ATTEMPTS = 2
COMMAND_CONFIRMATION_TIMEOUT_SECONDS = 5.0
CLOSE_COMMAND_CONFIRMATION_TIMEOUT_SECONDS = 60.0
COMMAND_CONFIRMATION_POLL_SECONDS = 0.5
COMMAND_STATE_READ_TIMEOUT_SECONDS = 4.0
COMMAND_EXPECTED_STATES: dict[str, set[GateState]] = {
    "open": {GateState.OPENING, GateState.OPEN},
    "close": {GateState.CLOSING, GateState.CLOSED},
}


@dataclass(frozen=True)
class AccessDeviceProviderAttempt:
    provider: str
    attempt: int = 1
    accepted: bool = False
    unavailable: bool = False
    detail: str | None = None
    state: str | None = None
    verified: bool = False
    confirmation_failed: bool = False


@dataclass(frozen=True)
class AccessDeviceOperationResult:
    device: AccessDeviceEntity
    action: str
    accepted: bool
    state: GateState
    detail: str | None = None
    primary_provider: str | None = None
    used_provider: str | None = None
    failover_used: bool = False
    attempts: list[AccessDeviceProviderAttempt] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        return any(attempt.verified for attempt in self.attempts)

    def as_payload(self) -> dict[str, Any]:
        return {
            "entity_id": self.device.key,
            "device_key": self.device.key,
            "name": self.device.name,
            "kind": self.device.kind,
            "action": self.action,
            "accepted": self.accepted,
            "state": self.state.value,
            "detail": self.detail,
            "primary_provider": self.primary_provider,
            "used_provider": self.used_provider,
            "failover_used": self.failover_used,
            "verified": self.verified,
            "attempts": [attempt.__dict__ for attempt in self.attempts],
            "metadata": self.metadata,
        }


class AccessDeviceService:
    """Owns IACS access devices and routes cover commands through provider adapters."""

    def __init__(self) -> None:
        self._poll_task: asyncio.Task | None = None
        self._subscription_tasks: dict[str, asyncio.Task] = {}
        self._subscription_status: dict[str, dict[str, Any]] = {}
        self._state_cache: dict[str, dict[str, Any]] = {}
        self._last_error: str | None = None

    async def start(self) -> None:
        if not self._poll_task or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_states(), name="access-device-state-poller")
        await self._start_state_subscriptions()

    async def stop(self) -> None:
        for task in self._subscription_tasks.values():
            task.cancel()
        if self._subscription_tasks:
            await asyncio.gather(*self._subscription_tasks.values(), return_exceptions=True)
        self._subscription_tasks.clear()
        self._subscription_status.clear()
        for provider_name in access_device_provider_keys():
            provider = get_access_device_provider(provider_name)
            close = getattr(provider, "close", None)
            if close is not None:
                await close()
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self._poll_task = None

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def list_devices(self, *, kind: str | None = None, enabled_only: bool = False) -> list[AccessDeviceEntity]:
        async with AsyncSessionLocal() as session:
            return await self.list_devices_for_session(session, kind=kind, enabled_only=enabled_only)

    async def list_devices_for_session(
        self,
        session: AsyncSession,
        *,
        kind: str | None = None,
        enabled_only: bool = False,
    ) -> list[AccessDeviceEntity]:
        statement = select(AccessDevice).options(selectinload(AccessDevice.provider_bindings))
        if kind:
            statement = statement.where(AccessDevice.kind == kind)
        if enabled_only:
            statement = statement.where(AccessDevice.enabled.is_(True))
        rows = (await session.scalars(statement.order_by(AccessDevice.sort_order, AccessDevice.name))).all()
        return [self._entity_from_row(row) for row in rows]

    async def create_device(self, payload: dict[str, Any]) -> AccessDeviceEntity:
        kind = str(payload.get("kind") or "").strip()
        if kind not in ACCESS_DEVICE_KINDS:
            raise ValueError("Access device kind must be gate or garage_door.")
        key = normalize_access_device_key(str(payload.get("key") or payload.get("name") or ""))
        if not key:
            raise ValueError("Access device key is required.")
        async with AsyncSessionLocal() as session:
            row = AccessDevice(
                key=key,
                kind=kind,
                name=str(payload.get("name") or key),
                enabled=bool(payload.get("enabled", True)),
                schedule_id=self._schedule_id_or_none(payload.get("schedule_id")),
                open_for_access=bool(payload.get("open_for_access", True)),
                sort_order=int(payload.get("sort_order") or 0),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row, ["provider_bindings"])
            return self._entity_from_row(row)

    async def update_device(self, device_id: str, payload: dict[str, Any]) -> AccessDeviceEntity:
        async with AsyncSessionLocal() as session:
            row = await self._device_row(session, device_id)
            for key in ("name", "kind"):
                if key in payload:
                    next_value = str(payload[key] or "").strip()
                    if key == "kind" and next_value not in ACCESS_DEVICE_KINDS:
                        raise ValueError("Access device kind must be gate or garage_door.")
                    setattr(row, key, next_value)
            if "key" in payload:
                row.key = normalize_access_device_key(str(payload["key"] or ""))
            if "enabled" in payload:
                row.enabled = bool(payload["enabled"])
            if "schedule_id" in payload:
                row.schedule_id = self._schedule_id_or_none(payload["schedule_id"])
            if "open_for_access" in payload:
                row.open_for_access = bool(payload["open_for_access"])
            if "sort_order" in payload:
                row.sort_order = int(payload["sort_order"] or 0)
            await session.commit()
            await session.refresh(row, ["provider_bindings"])
            return self._entity_from_row(row)

    async def delete_device(self, device_id: str) -> None:
        async with AsyncSessionLocal() as session:
            row = await self._device_row(session, device_id)
            await session.delete(row)
            await session.commit()

    async def upsert_binding(self, device_id: str, provider: str, payload: dict[str, Any]) -> AccessDeviceEntity:
        async with AsyncSessionLocal() as session:
            row = await self._device_row(session, device_id)
            binding = next((item for item in row.provider_bindings if item.provider == provider), None)
            external_id = str(payload.get("external_id") or "").strip()
            if not external_id:
                if binding:
                    await session.delete(binding)
                    await session.commit()
                    await session.refresh(row, ["provider_bindings"])
                    return self._entity_from_row(row)
                raise ValueError("Provider external ID is required.")
            if binding is None:
                binding = AccessDeviceProviderBinding(
                    access_device_id=row.id,
                    provider=provider,
                    external_id=external_id,
                )
                session.add(binding)
            binding.external_id = external_id
            binding.enabled = bool(payload.get("enabled", True))
            binding.config = dict(payload.get("config") or {})
            await session.commit()
            await session.refresh(row, ["provider_bindings"])
            return self._entity_from_row(row)

    async def status(self, *, refresh: bool = False) -> dict[str, Any]:
        devices = await self.list_devices(enabled_only=True)
        if refresh:
            await self.refresh_states(devices=devices)
        config = await get_runtime_config()
        gate_devices = [device for device in devices if device.kind == ACCESS_DEVICE_KIND_GATE]
        garage_devices = [device for device in devices if device.kind == ACCESS_DEVICE_KIND_GARAGE_DOOR]
        provider_names = self._configured_providers_for_devices(devices)
        provider_statuses = {
            provider_name: (await get_access_device_provider(provider_name).status(refresh=False)).__dict__
            for provider_name in provider_names
        }
        subscription_status = self._subscription_status_payload()
        for provider_name, stream_status in subscription_status.items():
            provider_status = provider_statuses.setdefault(
                provider_name,
                {
                    "provider": provider_name,
                    "configured": True,
                    "connected": False,
                    "degraded": False,
                    "last_error": None,
                    "metadata": {},
                },
            )
            provider_status["connected"] = bool(provider_status.get("connected") or stream_status.get("connected"))
            if stream_status.get("last_error"):
                provider_status["degraded"] = True
                provider_status["last_error"] = provider_status.get("last_error") or stream_status["last_error"]
            metadata = dict(provider_status.get("metadata") or {})
            metadata["state_stream"] = {
                "connected": bool(stream_status.get("connected")),
                "running": bool(stream_status.get("running")),
                "last_error": stream_status.get("last_error"),
                "updated_at": stream_status.get("updated_at"),
            }
            provider_status["metadata"] = metadata
        provider_connected = any(
            bool(status.get("connected") or (status.get("configured") and not status.get("degraded")))
            for status in provider_statuses.values()
        )
        provider_error = next(
            (str(status["last_error"]) for status in provider_statuses.values() if status.get("last_error")),
            None,
        )
        subscription_error = next(
            (
                str(status["last_error"])
                for status in subscription_status.values()
                if status.get("last_error")
            ),
            None,
        )
        current_gate_state = self._cached_state(gate_devices[0].key) if gate_devices else GateState.UNKNOWN.value
        ha_status: dict[str, Any] = {}
        try:
            from app.services.home_assistant import get_home_assistant_service

            ha_status = await get_home_assistant_service().status(refresh=refresh)
        except Exception as exc:
            logger.debug("home_assistant_door_state_merge_failed", extra={"error": str(exc)})
        return {
            "configured": bool(gate_devices or garage_devices),
            "connected": bool(devices and provider_connected and not self._last_error),
            "degraded": bool(
                self._last_error
                or provider_error
                or subscription_error
                or any(status.get("degraded") for status in provider_statuses.values())
            ),
            "last_error": self._last_error or provider_error or subscription_error,
            "listener_running": bool(self._poll_task and not self._poll_task.done()) and all(
                not task.done() for task in self._subscription_tasks.values()
            ),
            "gate_control_provider": config.gate_control_provider,
            "gate_failover_provider": config.gate_failover_provider,
            "provider_status": provider_statuses,
            "state_stream_status": subscription_status,
            "gate_entity_id": gate_devices[0].key if gate_devices else None,
            "gate_entities": [self._device_payload(device) for device in gate_devices],
            "garage_door_entities": [self._device_payload(device) for device in garage_devices],
            "default_media_player": None,
            "last_gate_state": current_gate_state,
            "current_gate_state": current_gate_state,
            "front_door_state": ha_status.get("front_door_state") or "unknown",
            "back_door_state": ha_status.get("back_door_state") or "unknown",
            "keep_gate_open_entity_id": ha_status.get("keep_gate_open_entity_id"),
            "keep_gate_open_state": ha_status.get("keep_gate_open_state"),
            "keep_gate_open_active": bool(ha_status.get("keep_gate_open_active")),
            "state_refreshed_at": self._latest_state_refreshed_at(devices),
        }

    async def refresh_states(self, *, devices: list[AccessDeviceEntity] | None = None) -> None:
        devices = devices if devices is not None else await self.list_devices(enabled_only=True)
        for device in devices:
            try:
                result = await self.read_state(device)
                if result.accepted:
                    await self._remember_state(device, result.state, provider=result.provider, raw_state=result.state.value)
                elif result.detail and "binding is not configured" not in result.detail:
                    self._last_error = result.detail[:500]
            except Exception as exc:
                self._last_error = str(exc)[:500]

    async def read_state(self, device: AccessDeviceEntity) -> AccessDeviceCommandResult:
        provider_names = await self._provider_order_for_device(device)
        if not provider_names:
            return AccessDeviceCommandResult(False, GateState.UNKNOWN, "Provider binding is not configured.")
        last_error: str | None = None
        for provider_name in provider_names:
            binding = device.bindings[provider_name]
            provider = get_access_device_provider(provider_name)
            try:
                state = await provider.current_state(binding)
            except AccessDeviceProviderUnavailable as exc:
                last_error = str(exc)
                continue
            return AccessDeviceCommandResult(True, state, provider=provider_name, external_id=binding.external_id)
        return AccessDeviceCommandResult(False, GateState.UNKNOWN, last_error or "Provider is unavailable.")

    async def handle_provider_state_event(self, provider_name: str, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "state")
        if event_type in {"connected", "disconnected"}:
            self._record_subscription_status(provider_name, event)
            await self._publish_status_snapshot(reason=f"{provider_name}.{event_type}")
            return
        state = self._gate_state_from_event(event.get("state"))
        if state is None:
            return
        self._record_subscription_status(provider_name, {**event, "type": "connected"})
        devices = await self.list_devices(enabled_only=True)
        matched = False
        for device in devices:
            binding = device.bindings.get(provider_name)
            if not binding or not binding.enabled:
                continue
            if not self._binding_matches_provider_event(binding, event):
                continue
            matched = True
            await self._remember_state(
                device,
                state,
                provider=provider_name,
                raw_state=str(event.get("raw_state") or state.value)[:80],
            )
        if not matched:
            logger.debug(
                "access_device_state_event_unmatched",
                extra={
                    "provider": provider_name,
                    "external_id": str(event.get("external_id") or ""),
                    "device_id": str(event.get("device_id") or ""),
                    "key": str(event.get("key") or ""),
                },
            )

    async def command_device(
        self,
        device_key: str,
        action: str,
        reason: str,
        *,
        bypass_schedule: bool = False,
        schedule_source: str | None = None,
    ) -> AccessDeviceOperationResult:
        async with AsyncSessionLocal() as session:
            row = await self._device_row(session, device_key)
            device = self._entity_from_row(row)
            if not device.enabled:
                return self._operation_rejected(device, action, "Access device is disabled.")
            if action == "open" and not bypass_schedule:
                from app.services.schedules import evaluate_schedule_id

                config = await get_runtime_config()
                schedule = await evaluate_schedule_id(
                    session,
                    row.schedule_id,
                    datetime.now(tz=UTC),
                    timezone_name=config.site_timezone,
                    default_policy=config.schedule_default_policy,
                    source=schedule_source or device.kind,
                )
                if not schedule.allowed:
                    return self._operation_rejected(
                        device,
                        action,
                        schedule.reason or "Outside schedule.",
                        metadata={"schedule_denied": True},
                    )
        return await self._command_with_failover(device, action, reason)

    async def open_access_gates(
        self,
        reason: str,
        *,
        bypass_schedule: bool = False,
    ) -> list[AccessDeviceOperationResult]:
        gates = [
            device
            for device in await self.list_devices(kind=ACCESS_DEVICE_KIND_GATE, enabled_only=True)
            if device.open_for_access
        ]
        outcomes: list[AccessDeviceOperationResult] = []
        for gate in gates:
            outcomes.append(
                await self.command_device(
                    gate.key,
                    "open",
                    reason,
                    bypass_schedule=bypass_schedule,
                    schedule_source="gate",
                )
            )
        return outcomes

    async def _command_with_failover(
        self,
        device: AccessDeviceEntity,
        action: str,
        reason: str,
    ) -> AccessDeviceOperationResult:
        provider_names = await self._provider_order_for_device(device)
        if not provider_names:
            return self._operation_rejected(
                device,
                action,
                f"No enabled provider binding is configured for {device.name}.",
            )
        attempts: list[AccessDeviceProviderAttempt] = []
        for index, provider_name in enumerate(provider_names):
            binding = device.bindings[provider_name]
            provider = get_access_device_provider(provider_name)
            max_attempts = (
                CLOSE_COMMAND_PROVIDER_MAX_ATTEMPTS
                if action == "close" and index == 0
                else COMMAND_PROVIDER_MAX_ATTEMPTS
            )
            for attempt_number in range(1, max_attempts + 1):
                started_at = datetime.now(tz=UTC)
                try:
                    result = await provider.command_cover(binding, action, reason)
                    provider_completed_at = datetime.now(tz=UTC)
                except AccessDeviceProviderUnavailable as exc:
                    attempts.append(
                        AccessDeviceProviderAttempt(
                            provider=provider_name,
                            attempt=attempt_number,
                            unavailable=True,
                            detail=str(exc),
                        )
                    )
                    break
                except Exception as exc:
                    attempts.append(
                        AccessDeviceProviderAttempt(
                            provider=provider_name,
                            attempt=attempt_number,
                            unavailable=True,
                            detail=str(exc),
                        )
                    )
                    break

                logger.info(
                    "access_device_provider_command_returned",
                    extra={
                        "device_key": device.key,
                        "device_name": device.name,
                        "kind": device.kind,
                        "provider": provider_name,
                        "action": action,
                        "attempt": attempt_number,
                        "accepted": result.accepted,
                        "state": result.state.value,
                        "provider_call_ms": round(
                            max(0.0, (provider_completed_at - started_at).total_seconds()) * 1000.0,
                            3,
                        ),
                        "metadata": result.metadata,
                    },
                )
                if not result.accepted:
                    attempts.append(
                        AccessDeviceProviderAttempt(
                            provider=provider_name,
                            attempt=attempt_number,
                            accepted=False,
                            detail=result.detail,
                            state=result.state.value,
                        )
                    )
                    break

                confirmed_state, verified, confirmation_detail = await self._confirm_command_effect(
                    device,
                    provider_name,
                    provider,
                    binding,
                    action,
                    initial_state=result.state,
                    started_at=started_at,
                    expected_states={GateState.CLOSED} if action == "close" else None,
                    timeout_seconds=(
                        CLOSE_COMMAND_CONFIRMATION_TIMEOUT_SECONDS
                        if action == "close"
                        else COMMAND_CONFIRMATION_TIMEOUT_SECONDS
                    ),
                )
                attempts.append(
                    AccessDeviceProviderAttempt(
                        provider=provider_name,
                        attempt=attempt_number,
                        accepted=True,
                        detail=result.detail if verified else confirmation_detail,
                        state=confirmed_state.value,
                        verified=verified,
                        confirmation_failed=not verified,
                    )
                )
                await self._remember_state(
                    device,
                    confirmed_state,
                    provider=provider_name,
                    raw_state=confirmed_state.value,
                )
                if verified:
                    return AccessDeviceOperationResult(
                        device=device,
                        action=action,
                        accepted=True,
                        state=confirmed_state,
                        detail=result.detail,
                        primary_provider=provider_names[0],
                        used_provider=provider_name,
                        failover_used=index > 0,
                        attempts=attempts,
                        metadata={
                            **result.metadata,
                            "verified": True,
                            "verified_state": confirmed_state.value,
                            "provider_attempt": attempt_number,
                        },
                    )
                if action == "close":
                    if attempt_number < max_attempts:
                        logger.warning(
                            "access_device_close_retrying_after_unverified_state",
                            extra={
                                "device_key": device.key,
                                "device_name": device.name,
                                "provider": provider_name,
                                "attempt": attempt_number,
                                "state": confirmed_state.value,
                                "detail": confirmation_detail,
                            },
                        )
                        continue
                    logger.warning(
                        "access_device_close_provider_unverified_after_retries",
                        extra={
                            "device_key": device.key,
                            "device_name": device.name,
                            "provider": provider_name,
                            "attempts": max_attempts,
                            "state": confirmed_state.value,
                            "detail": confirmation_detail,
                        },
                    )
                    break
                logger.warning(
                    "access_device_command_accepted_but_unverified",
                    extra={
                        "device_key": device.key,
                        "device_name": device.name,
                        "provider": provider_name,
                        "action": action,
                        "attempt": attempt_number,
                        "state": confirmed_state.value,
                        "detail": confirmation_detail,
                    },
                )
                return AccessDeviceOperationResult(
                    device=device,
                    action=action,
                    accepted=True,
                    state=confirmed_state,
                    detail=confirmation_detail or result.detail,
                    primary_provider=provider_names[0],
                    used_provider=provider_name,
                    failover_used=index > 0,
                    attempts=attempts,
                    metadata={
                        **result.metadata,
                        "verified": False,
                        "verified_state": confirmed_state.value,
                        "provider_attempt": attempt_number,
                        "accepted_unverified": True,
                    },
                )

            if index < len(provider_names) - 1:
                logger.warning(
                    "access_device_command_failing_over",
                    extra={
                        "device_key": device.key,
                        "device_name": device.name,
                        "provider": provider_name,
                        "next_provider": provider_names[index + 1],
                        "action": action,
                    },
                )

        detail = self._failed_command_detail(device, action, attempts)
        return self._operation_rejected(device, action, detail, attempts=attempts)

    async def _confirm_command_effect(
        self,
        device: AccessDeviceEntity,
        provider_name: str,
        provider: Any,
        binding: AccessDeviceBinding,
        action: str,
        *,
        initial_state: GateState,
        started_at: datetime,
        expected_states: set[GateState] | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[GateState, bool, str | None]:
        expected_states = expected_states or COMMAND_EXPECTED_STATES.get(action)
        if not expected_states:
            return initial_state, True, None
        timeout_seconds = COMMAND_CONFIRMATION_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        if initial_state in expected_states:
            return initial_state, True, None

        last_state = initial_state
        last_error: str | None = None
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            cached_state = self._confirmed_cached_state(device.key, started_at)
            if cached_state in expected_states:
                return cached_state, True, None

            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                observed_state = await asyncio.wait_for(
                    provider.current_state(binding),
                    timeout=min(COMMAND_STATE_READ_TIMEOUT_SECONDS, remaining),
                )
            except Exception as exc:
                last_error = str(exc)
            else:
                last_state = observed_state
                if observed_state in expected_states:
                    return observed_state, True, None

            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(COMMAND_CONFIRMATION_POLL_SECONDS, remaining))

        expected = "/".join(sorted(state.value for state in expected_states))
        detail = (
            f"{provider_name} accepted {action}, but {device.name} did not report "
            f"{expected} within {timeout_seconds:.1f}s"
        )
        if last_error:
            detail = f"{detail}; last state check failed: {last_error}"
        else:
            detail = f"{detail}; last observed state: {last_state.value}"
        return last_state, False, detail

    def _confirmed_cached_state(self, device_key: str, started_at: datetime) -> GateState | None:
        cached = self._state_cache.get(device_key)
        if not cached:
            return None
        updated_at_raw = str(cached.get("updated_at") or "")
        try:
            updated_at = datetime.fromisoformat(updated_at_raw)
        except ValueError:
            return None
        if updated_at < started_at:
            return None
        return self._gate_state_from_event(cached.get("state"))

    def _failed_command_detail(
        self,
        device: AccessDeviceEntity,
        action: str,
        attempts: list[AccessDeviceProviderAttempt],
    ) -> str:
        if not attempts:
            return f"No provider accepted {action} for {device.name}."
        failure = next(
            (
                attempt
                for attempt in reversed(attempts)
                if attempt.confirmation_failed or attempt.unavailable or not attempt.accepted
            ),
            attempts[-1],
        )
        return failure.detail or f"{device.name} did not report a successful {action}."

    async def _provider_order_for_device(self, device: AccessDeviceEntity) -> list[str]:
        config = await get_runtime_config()
        preferred = [
            config.gate_control_provider,
            config.gate_failover_provider if config.gate_failover_provider != "none" else "",
            "home_assistant",
            "esphome",
            *sorted(device.bindings),
        ]
        order: list[str] = []
        for provider_name in preferred:
            if not provider_name or provider_name in order:
                continue
            binding = device.bindings.get(provider_name)
            if binding and binding.enabled:
                order.append(provider_name)
        return order

    async def _start_state_subscriptions(self) -> None:
        for provider_name in access_device_provider_keys():
            if provider_name in self._subscription_tasks and not self._subscription_tasks[provider_name].done():
                continue
            provider = get_access_device_provider(provider_name)
            if not getattr(provider, "state_subscription_supported", False):
                continue
            try:
                configured = await provider.configured()
            except Exception as exc:
                self._subscription_status[provider_name] = {
                    "provider": provider_name,
                    "connected": False,
                    "last_error": str(exc)[:500],
                    "updated_at": datetime.now(tz=UTC).isoformat(),
                    "devices": {},
                }
                continue
            if not configured:
                continue
            self._subscription_status.setdefault(
                provider_name,
                {
                    "provider": provider_name,
                    "connected": False,
                    "last_error": None,
                    "updated_at": datetime.now(tz=UTC).isoformat(),
                    "devices": {},
                },
            )
            self._subscription_tasks[provider_name] = asyncio.create_task(
                self._consume_provider_state_changes(provider_name),
                name=f"access-device-{provider_name}-state-stream",
            )

    async def _consume_provider_state_changes(self, provider_name: str) -> None:
        while True:
            provider = get_access_device_provider(provider_name)
            try:
                async for event in provider.subscribe_state_changes():
                    await self.handle_provider_state_event(provider_name, event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_subscription_status(
                    provider_name,
                    {
                        "type": "disconnected",
                        "provider": provider_name,
                        "last_error": str(exc)[:500],
                    },
                )
                await self._publish_status_snapshot(reason=f"{provider_name}.stream_failed")
                logger.warning(
                    "access_device_state_stream_failed",
                    extra={"provider": provider_name, "error": str(exc)},
                )
                await asyncio.sleep(STATE_POLL_INTERVAL_SECONDS)

    async def _publish_status_snapshot(self, *, reason: str) -> None:
        try:
            status = await self.status(refresh=False)
            await event_bus.publish("access_device.status", {"reason": reason, "status": status})
        except Exception as exc:
            logger.debug("access_device_status_publish_failed", extra={"reason": reason, "error": str(exc)})

    def _record_subscription_status(self, provider_name: str, event: dict[str, Any]) -> None:
        status = self._subscription_status.setdefault(
            provider_name,
            {
                "provider": provider_name,
                "connected": False,
                "last_error": None,
                "updated_at": None,
                "devices": {},
            },
        )
        devices = status.setdefault("devices", {})
        device_id = str(event.get("device_id") or "_provider")
        connected = str(event.get("type") or "") != "disconnected"
        if connected and device_id != "_provider":
            devices.pop("_provider", None)
        existing_device_status = devices.get(device_id, {})
        devices[device_id] = {
            "device_id": None if device_id == "_provider" else device_id,
            "name": str(event.get("device_name") or existing_device_status.get("name") or event.get("name") or ""),
            "host": str(event.get("host") or existing_device_status.get("host") or ""),
            "connected": connected,
            "last_error": None if connected else str(event.get("last_error") or "State stream disconnected.")[:500],
            "cover_count": event.get("cover_count", existing_device_status.get("cover_count")),
            "updated_at": datetime.now(tz=UTC).isoformat(),
        }
        errors = [str(item["last_error"]) for item in devices.values() if item.get("last_error")]
        status.update(
            {
                "provider": provider_name,
                "connected": any(bool(item.get("connected")) for item in devices.values()),
                "last_error": errors[0] if errors else None,
                "updated_at": datetime.now(tz=UTC).isoformat(),
                "devices": devices,
            }
        )

    def _subscription_status_payload(self) -> dict[str, dict[str, Any]]:
        payload: dict[str, dict[str, Any]] = {}
        for provider_name, status in self._subscription_status.items():
            task = self._subscription_tasks.get(provider_name)
            devices = status.get("devices") or {}
            payload[provider_name] = {
                **status,
                "running": bool(task and not task.done()),
                "devices": list(devices.values()) if isinstance(devices, dict) else devices,
            }
        return payload

    def _binding_matches_provider_event(self, binding: AccessDeviceBinding, event: dict[str, Any]) -> bool:
        event_device_id = str(event.get("device_id") or "").strip()
        binding_device_id = str(binding.config.get("device_id") or "").strip()
        external_id = binding.external_id.strip()
        if ":" in external_id:
            prefix, suffix = external_id.split(":", 1)
            if prefix and suffix:
                if event_device_id and prefix != event_device_id:
                    return False
                external_id = suffix
        if binding_device_id and event_device_id and binding_device_id != event_device_id:
            return False
        configured_key = binding.config.get("key")
        event_key = event.get("key")
        if configured_key is not None and event_key is not None and str(configured_key) == str(event_key):
            return True
        candidates = {
            str(event.get("external_id") or "").strip(),
            str(event.get("object_id") or "").strip(),
            str(event.get("name") or "").strip(),
            str(event_key or "").strip(),
        }
        return external_id in {candidate for candidate in candidates if candidate}

    def _gate_state_from_event(self, value: Any) -> GateState | None:
        if isinstance(value, GateState):
            return value
        try:
            return GateState(str(value).lower())
        except ValueError:
            return None

    def _configured_providers_for_devices(self, devices: list[AccessDeviceEntity]) -> list[str]:
        providers: set[str] = set()
        for device in devices:
            providers.update(
                provider
                for provider, binding in device.bindings.items()
                if binding.enabled
            )
        ordered = [provider for provider in ("home_assistant", "esphome") if provider in providers]
        ordered.extend(sorted(providers - set(ordered)))
        return ordered

    def _operation_rejected(
        self,
        device: AccessDeviceEntity,
        action: str,
        detail: str | None,
        *,
        attempts: list[AccessDeviceProviderAttempt] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AccessDeviceOperationResult:
        return AccessDeviceOperationResult(
            device=device,
            action=action,
            accepted=False,
            state=GateState.FAULT,
            detail=detail,
            attempts=attempts or [],
            metadata=metadata or {},
        )

    async def _remember_state(
        self,
        device: AccessDeviceEntity,
        state: GateState,
        *,
        provider: str | None,
        raw_state: str | None,
    ) -> None:
        previous = self._state_cache.get(device.key, {})
        previous_state = str(previous.get("state") or GateState.UNKNOWN.value)
        observed_at = datetime.now(tz=UTC)
        self._state_cache[device.key] = {
            "state": state.value,
            "provider": provider,
            "raw_state": raw_state,
            "updated_at": observed_at.isoformat(),
        }
        if previous_state == state.value:
            return
        async with AsyncSessionLocal() as session:
            session.add(
                GateStateObservation(
                    gate_entity_id=device.key,
                    gate_name=device.name,
                    state=state.value,
                    raw_state=raw_state,
                    previous_state=previous_state,
                    observed_at=observed_at,
                    state_changed_at=observed_at,
                    source=provider or "access_device",
                )
            )
            await session.commit()
        event_type = "gate.state_changed" if device.kind == ACCESS_DEVICE_KIND_GATE else "door.state_changed"
        await event_bus.publish(
            event_type,
            {
                "source": provider or "access_device",
                "entity_id": device.key,
                "device_key": device.key,
                "name": device.name,
                "door": "garage_door" if device.kind == ACCESS_DEVICE_KIND_GARAGE_DOOR else "gate",
                "state": state.value,
                "raw_state": raw_state,
                "previous_state": previous_state,
                "state_changed_at": observed_at.isoformat(),
            },
        )

    async def _poll_states(self) -> None:
        while True:
            try:
                await self.refresh_states()
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)[:500]
                logger.warning("access_device_state_poll_failed", extra={"error": str(exc)})
            await asyncio.sleep(STATE_POLL_INTERVAL_SECONDS)

    def _device_payload(self, device: AccessDeviceEntity) -> dict[str, Any]:
        cached = self._state_cache.get(device.key, {})
        return {
            "id": device.key,
            "entity_id": device.key,
            "device_key": device.key,
            "kind": device.kind,
            "name": device.name,
            "enabled": device.enabled,
            "schedule_id": device.schedule_id,
            "open_for_access": device.open_for_access,
            "state": cached.get("state") or GateState.UNKNOWN.value,
            "state_provider": cached.get("provider"),
            "state_changed_at": cached.get("updated_at"),
            "bindings": {
                provider: {
                    "provider": binding.provider,
                    "external_id": binding.external_id,
                    "enabled": binding.enabled,
                    "config": binding.config,
                }
                for provider, binding in device.bindings.items()
            },
        }

    def _cached_state(self, device_key: str) -> str:
        return str(self._state_cache.get(device_key, {}).get("state") or GateState.UNKNOWN.value)

    def _latest_state_refreshed_at(self, devices: list[AccessDeviceEntity]) -> str | None:
        timestamps = [
            str(cached["updated_at"])
            for device in devices
            if (cached := self._state_cache.get(device.key)) and cached.get("updated_at")
        ]
        return max(timestamps) if timestamps else None

    def _entity_from_row(self, row: AccessDevice) -> AccessDeviceEntity:
        return AccessDeviceEntity(
            key=row.key,
            kind=row.kind,
            name=row.name,
            enabled=row.enabled,
            schedule_id=str(row.schedule_id) if row.schedule_id else None,
            open_for_access=row.open_for_access,
            sort_order=row.sort_order,
            bindings={
                binding.provider: AccessDeviceBinding(
                    provider=binding.provider,
                    external_id=binding.external_id,
                    enabled=binding.enabled,
                    config=dict(binding.config or {}),
                )
                for binding in row.provider_bindings
            },
        )

    async def _device_row(self, session: AsyncSession, device_id_or_key: str) -> AccessDevice:
        statement = (
            select(AccessDevice)
            .options(selectinload(AccessDevice.provider_bindings))
            .where((AccessDevice.key == device_id_or_key) | (AccessDevice.id == self._uuid_or_none(device_id_or_key)))
        )
        row = await session.scalar(statement)
        if row is None:
            raise LookupError("Access device was not found.")
        return row

    def _schedule_id_or_none(self, value: Any) -> Any:
        text = str(value or "").strip()
        return self._uuid_or_none(text) if text else None

    def _uuid_or_none(self, value: str | None) -> Any:
        if not value:
            return None
        try:
            import uuid

            return uuid.UUID(str(value))
        except ValueError:
            return None


async def seed_access_devices_from_settings() -> None:
    config = await get_runtime_config()
    async with AsyncSessionLocal() as session:
        existing = {
            row.key: row
            for row in (
                await session.scalars(
                    select(AccessDevice).options(selectinload(AccessDevice.provider_bindings))
                )
            ).all()
        }
        gate_entities = normalize_cover_entities(
            config.home_assistant_gate_entities,
            default_open_service=config.home_assistant_gate_open_service,
        )
        await _seed_configured_entities(
            session,
            existing,
            gate_entities,
            kind=ACCESS_DEVICE_KIND_GATE,
            default_open_for_access=True,
        )
        garage_entities = normalize_cover_entities(
            config.home_assistant_garage_door_entities,
            default_open_service=config.home_assistant_gate_open_service,
        )
        await _seed_configured_entities(
            session,
            existing,
            garage_entities,
            kind=ACCESS_DEVICE_KIND_GARAGE_DOOR,
            default_open_for_access=False,
        )
        await session.commit()


async def _seed_configured_entities(
    session: AsyncSession,
    existing: dict[str, AccessDevice],
    entities: list[dict[str, Any]],
    *,
    kind: str,
    default_open_for_access: bool,
) -> None:
    for index, entity in enumerate(entities):
        entity_id = str(entity.get("entity_id") or "").strip()
        if not entity_id:
            continue
        key = normalize_access_device_key(entity_id)
        row = existing.get(key)
        if row is None:
            row = AccessDevice(
                key=key,
                kind=kind,
                name=str(entity.get("name") or entity_id),
                enabled=bool(entity.get("enabled", True)),
                schedule_id=AccessDeviceService()._uuid_or_none(str(entity.get("schedule_id") or "")),
                open_for_access=default_open_for_access,
                sort_order=index,
            )
            session.add(row)
            await session.flush()
            existing[key] = row
        binding_exists = await session.scalar(
            select(AccessDeviceProviderBinding.id)
            .where(
                AccessDeviceProviderBinding.access_device_id == row.id,
                AccessDeviceProviderBinding.provider == "home_assistant",
            )
            .limit(1)
        )
        if binding_exists is None:
            session.add(
                AccessDeviceProviderBinding(
                    access_device_id=row.id,
                    provider="home_assistant",
                    external_id=entity_id,
                    enabled=bool(entity.get("enabled", True)),
                    config={
                        "open_service": str(entity.get("open_service") or "cover.open_cover"),
                        "close_service": str(entity.get("close_service") or "cover.close_cover"),
                    },
                )
            )


def normalize_access_device_key(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


@lru_cache
def get_access_device_service() -> AccessDeviceService:
    return AccessDeviceService()
