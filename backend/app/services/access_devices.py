from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Awaitable, Callable, Literal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.core.recovery_hold import require_effects_enabled
from app.db.session import AsyncSessionLocal
from app.models import AccessDevice, AccessDeviceCommandRecord, AccessDeviceProviderBinding, GateCommandRecord, GateStateObservation, Schedule, User
from app.modules.access_devices.base import (
    ACCESS_DEVICE_KIND_GARAGE_DOOR,
    ACCESS_DEVICE_KIND_GATE,
    ACCESS_DEVICE_KINDS,
    AccessDeviceBinding,
    AccessDeviceCommandResult,
    AccessDeviceEntity,
    AccessDeviceProviderUnavailable,
)
from app.modules.access_devices.registry import (
    access_device_provider_keys,
    get_access_device_provider,
)
from app.modules.gate.base import CommandDelivery, GateCommandNotSent, GateState
from app.modules.home_assistant.covers import normalize_cover_entities
from app.services.access_device_configuration import AccessDeviceConfiguration
from app.services.access_device_commands import (
    AccessDeviceCommandJournal, DeviceCommandClaim, DEVICE_COMMAND_EVIDENCE_WINDOW_SECONDS,
    UNRESOLVED_DEVICE_COMMAND_STATES,
    device_command_receipt,
)
from app.services.event_bus import event_bus
from app.services.settings import RuntimeConfig, get_runtime_config, get_runtime_config_for_session
from app.services.telemetry import (
    TELEMETRY_CATEGORY_INTEGRATIONS,
    current_trace_id,
    emit_audit_log,
    telemetry,
)

logger = get_logger(__name__)


STATE_POLL_INTERVAL_SECONDS = 10.0
COMMAND_CONFIRMATION_TIMEOUT_SECONDS = 5.0
CLOSE_COMMAND_CONFIRMATION_TIMEOUT_SECONDS = 60.0
COMMAND_CONFIRMATION_POLL_SECONDS = 0.5
COMMAND_STATE_READ_TIMEOUT_SECONDS = 4.0


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
    delivery: CommandDelivery | None = None


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
    delivery: CommandDelivery | None = None

    def __post_init__(self) -> None:
        if self.delivery is None:
            object.__setattr__(self, "delivery", CommandDelivery.ACCEPTED if self.accepted else CommandDelivery.REJECTED)

    @property
    def requires_reconciliation(self) -> bool:
        if "requires_reconciliation" in self.metadata:
            return bool(self.metadata["requires_reconciliation"])
        return self.delivery == CommandDelivery.UNKNOWN or (self.accepted and not self.verified)

    @property
    def verified(self) -> bool:
        return bool(self.metadata.get("verified")) or any(attempt.verified for attempt in self.attempts)

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
            "delivery": self.delivery,
            "requires_reconciliation": self.requires_reconciliation,
            "attempts": [attempt.__dict__ for attempt in self.attempts],
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class _DeviceDispatch:
    claim: DeviceCommandClaim
    plan: dict[str, Any]
    bypass_schedule: bool
    schedule_source: str | None
    actor_user_id: str | None
    auth_version: int | None
    authorize_dispatch: Callable[[AsyncSession], Awaitable[None]] | None
    parent_command_id: str | None
    parent_lease_token: str | None
    entry_precondition: dict[str, Any] | None = None


class AccessDeviceService:
    """Owns IACS access devices and routes cover commands through provider adapters."""

    def __init__(self) -> None:
        self._configuration = AccessDeviceConfiguration()
        self._journal = AccessDeviceCommandJournal()
        self._reconciliation_cursor: uuid.UUID | None = None
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
        return await self._configuration.list_devices(kind=kind, enabled_only=enabled_only)

    async def list_devices_for_session(self, session: AsyncSession, *, kind: str | None = None,
                                       enabled_only: bool = False) -> list[AccessDeviceEntity]:
        return await self._configuration.list_devices_for_session(session, kind=kind, enabled_only=enabled_only)

    async def device_eligibility(self, devices: list[AccessDeviceEntity]) -> dict[str, dict[str, bool]]:
        return await self._configuration.device_eligibility(devices)

    async def preview_gate_open(self, *, target_device_key: str | None = None,
                                require_admission: bool = False, automatic_entry_policy: bool = False) -> dict[str, Any]:
        return await self._configuration.preview_gate_open(target_device_key=target_device_key,
            require_admission=require_admission, automatic_entry_policy=automatic_entry_policy)

    async def preview_device_command(self, device_key: str, action: str) -> dict[str, Any]:
        return await self._configuration.preview_device_command(device_key, action)

    async def create_device(self, payload: dict[str, Any], *, user: User) -> AccessDeviceEntity:
        from app.services.schedule_assignments import set_schedule_assignment

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
                open_for_access=bool(payload.get("open_for_access", True)),
                sort_order=int(payload.get("sort_order") or 0),
            )
            session.add(row)
            await set_schedule_assignment(session, row, payload.get("schedule_id"), user=user, source="api")
            await session.commit()
            await session.refresh(row, ["provider_bindings"])
            return self._configuration.entity_from_row(row)

    async def update_device(self, device_id: str, payload: dict[str, Any], *, user: User) -> AccessDeviceEntity:
        from app.services.schedule_assignments import set_schedule_assignment

        async with AsyncSessionLocal() as session:
            row = await self._device_row(session, device_id)
            await self._journal.assert_configurable(session, row.id)
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
                await set_schedule_assignment(session, row, payload["schedule_id"], user=user, source="api")
            if "open_for_access" in payload:
                row.open_for_access = bool(payload["open_for_access"])
            if "sort_order" in payload:
                row.sort_order = int(payload["sort_order"] or 0)
            await session.commit()
            await session.refresh(row, ["provider_bindings"])
            return self._configuration.entity_from_row(row)

    async def assign_schedule(
        self, device_id: str, schedule_id: str | None, *, user: User, source: Literal["api", "alfred"],
    ) -> AccessDeviceEntity:
        from app.services.schedule_assignments import set_schedule_assignment

        async with AsyncSessionLocal() as session:
            row = await self._device_row(session, device_id)
            await self._journal.assert_configurable(session, row.id)
            await set_schedule_assignment(session, row, schedule_id, user=user, source=source)
            await session.commit()
            await session.refresh(row, ["provider_bindings"])
            return self._configuration.entity_from_row(row)

    async def delete_device(self, device_id: str) -> None:
        async with AsyncSessionLocal() as session:
            row = await self._device_row(session, device_id)
            await self._journal.assert_configurable(session, row.id)
            await session.delete(row)
            await session.commit()

    async def upsert_binding(self, device_id: str, provider: str, payload: dict[str, Any]) -> AccessDeviceEntity:
        async with AsyncSessionLocal() as session:
            row = await self._device_row(session, device_id)
            await self._journal.assert_configurable(session, row.id)
            binding = next((item for item in row.provider_bindings if item.provider == provider), None)
            external_id = str(payload.get("external_id") or "").strip()
            if not external_id:
                if binding:
                    await session.delete(binding)
                    await session.commit()
                    await session.refresh(row, ["provider_bindings"])
                    return self._configuration.entity_from_row(row)
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
            return self._configuration.entity_from_row(row)

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
        admission_gate = next((device for device in gate_devices
            if device.key == getattr(config, "gate_admission_device_key", None)
            and self._configuration.eligibility(device, config)["admission_eligible"]), None)
        current_gate_state = self._cached_state(admission_gate.key) if admission_gate else GateState.UNKNOWN.value
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
            "gate_entity_id": admission_gate.key if admission_gate else None,
            "gate_admission_device_key": getattr(config, "gate_admission_device_key", None),
            "gate_entities": [{**self._device_payload(device), **self._configuration.eligibility(device, config)} for device in gate_devices],
            "garage_door_entities": [{**self._device_payload(device), **self._configuration.eligibility(device, config)} for device in garage_devices],
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
        async with AsyncSessionLocal() as session:
            devices = devices if devices is not None else await self.list_devices_for_session(session, enabled_only=True)
            config = await get_runtime_config_for_session(session)
        for device in devices:
            try:
                result = await self.read_state(device, runtime_config=config)
                if result.accepted:
                    await self._remember_state(device, result.state, provider=result.provider, raw_state=result.state.value,
                        observed_at=datetime.fromisoformat(result.metadata["observed_at"]),
                        binding_fingerprint=self._configuration.target_snapshot(device, config)["binding_fingerprint"])
                elif result.detail and "binding is not configured" not in result.detail:
                    self._last_error = result.detail[:500]
            except Exception as exc:
                self._last_error = str(exc)[:500]

    async def reconcile_commands(self) -> int:
        """Read-only provider observations may settle receipts; recovery never sends."""
        async with AsyncSessionLocal() as session:
            now = await session.scalar(select(func.clock_timestamp()))
            active = AccessDeviceCommandRecord.state.in_(UNRESOLVED_DEVICE_COMMAND_STATES)
            current = or_(AccessDeviceCommandRecord.state.in_(("prepared", "attempting")),
                          AccessDeviceCommandRecord.attempted_at >= now - timedelta(seconds=DEVICE_COMMAND_EVIDENCE_WINDOW_SECONDS))
            recent_ids = list((await session.scalars(select(AccessDeviceCommandRecord.id).where(active, current)
                .order_by(AccessDeviceCommandRecord.created_at).limit(100))).all())
            historical = select(AccessDeviceCommandRecord.id).where(active, ~current)
            if self._reconciliation_cursor:
                historical = historical.where(AccessDeviceCommandRecord.id > self._reconciliation_cursor)
            historic_ids = list((await session.scalars(historical.order_by(AccessDeviceCommandRecord.id).limit(100))).all())
            self._reconciliation_cursor = historic_ids[-1] if len(historic_ids) == 100 else None
            ids = list(dict.fromkeys([*recent_ids, *historic_ids]))
        changed = 0
        for identity in ids:
            row = await self._journal.reconcile_recorded_observation(identity)
            if not row or row.state not in {"accepted", "unknown"} or not row.attempted_at:
                continue
            if datetime.now(tz=UTC) > row.attempted_at + timedelta(seconds=DEVICE_COMMAND_EVIDENCE_WINDOW_SECONDS):
                continue
            async with AsyncSessionLocal() as session:
                try:
                    device = self._configuration.entity_from_row(await self._device_row(session, str(row.target_device_id)))
                    config = await get_runtime_config_for_session(session)
                except LookupError:
                    continue
            target = self._configuration.target_snapshot(device, config)
            if target["binding_fingerprint"] != row.binding_fingerprint:
                continue
            observation = await self._observe_target(device, row.action, after=row.attempted_at, runtime_config=config)
            if observation:
                resolved = await self._journal.reconcile_observation(identity,
                    binding_fingerprint=row.binding_fingerprint, observation=observation)
                changed += bool(resolved and resolved.state == "verified")
        return changed

    async def command_receipt(self, command_id: uuid.UUID | str | None = None, *,
                              intent_id: str | None = None) -> dict[str, Any] | None:
        if (command_id is None) == (intent_id is None):
            raise ValueError("Specify exactly one command ID or original intent ID.")
        async with AsyncSessionLocal() as session:
            if command_id is not None:
                row = await session.get(AccessDeviceCommandRecord, uuid.UUID(str(command_id)))
            else:
                row = await session.scalar(select(AccessDeviceCommandRecord).where(
                    AccessDeviceCommandRecord.intent_id == intent_id,
                    AccessDeviceCommandRecord.gate_command_id.is_(None))
                    .order_by(AccessDeviceCommandRecord.created_at).limit(1))
            return device_command_receipt(row) if row else None

    async def list_command_receipts(self, *, limit: int = 25,
                                    before_id: uuid.UUID | None = None) -> dict[str, Any]:
        return await self._journal.list_command_receipts(limit=limit, before_id=before_id)

    async def gate_command_projection(self, session: AsyncSession, parent: GateCommandRecord, *,
                                      reconcile: bool = False) -> dict[str, Any] | None:
        return await self._journal.gate_command_projection(session, parent, reconcile=reconcile)

    async def read_admission_gate_state(self) -> GateState:
        async with AsyncSessionLocal() as session:
            config = await get_runtime_config_for_session(session)
            devices = await self.list_devices_for_session(session)
        key = str(config.gate_admission_device_key or "")
        device = next((item for item in devices if item.key == key and self._configuration.eligibility(item, config)["admission_eligible"]), None)
        if device is None:
            return GateState.UNKNOWN
        try:
            return (await self.read_state(device, runtime_config=config)).state
        except Exception:
            return GateState.UNKNOWN

    async def read_state(self, device: AccessDeviceEntity, *, runtime_config: RuntimeConfig | None = None) -> AccessDeviceCommandResult:
        if runtime_config is None:
            async with AsyncSessionLocal() as session:
                runtime_config = await get_runtime_config_for_session(session)
        provider_names = self._configuration.provider_order(device, runtime_config)
        if not provider_names:
            return AccessDeviceCommandResult(False, GateState.UNKNOWN, "Provider binding is not configured.")
        last_error: str | None = None
        for provider_name in provider_names:
            binding = device.bindings[provider_name]
            provider = get_access_device_provider(provider_name)
            try:
                observation = await provider.observe_state(binding, runtime_config=runtime_config)
            except AccessDeviceProviderUnavailable as exc:
                last_error = str(exc)
                continue
            return AccessDeviceCommandResult(True, observation.state, provider=provider_name,
                external_id=binding.external_id, metadata={"observed_at": observation.observed_at.isoformat()})
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
        self, device_key: str, action: str, reason: str, *, bypass_schedule: bool = False,
        schedule_source: str | None = None, intent_id: str | None = None,
        idempotency_key: str | None = None, gate_command_id: str | None = None,
        target_plan: dict[str, Any] | None = None, expires_at: datetime | None = None,
        parent_lease_token: str | None = None,
        actor_user_id: str | None = None, auth_version: int | None = None,
        authorize_dispatch: Callable[[AsyncSession], Awaitable[None]] | None = None,
        origin_context: dict[str, Any] | None = None,
        _entry_precondition: dict[str, Any] | None = None,
    ) -> AccessDeviceOperationResult:
        require_effects_enabled()
        if expires_at is not None and expires_at.tzinfo is None:
            raise ValueError("Command expiry must include a timezone.")
        if not intent_id or not idempotency_key:
            raise ValueError("A stable intent ID and idempotency key are required for device commands.")
        if origin_context is not None and authorize_dispatch is None:
            raise ValueError("Automatic garage provenance requires the current access authorization participant.")
        plan = target_plan or await self.preview_device_command(device_key, action)
        if plan.get("action") != action:
            raise ValueError("The confirmed action differs from the requested device command.")
        target = next((item for item in plan.get("targets", []) if item.get("device_key") == device_key), None)
        if target is None:
            raise ValueError("The requested device is absent from the confirmed target plan.")
        if origin_context is not None and target.get("kind") != ACCESS_DEVICE_KIND_GARAGE_DOOR:
            raise ValueError("Automatic garage provenance requires a garage-door target.")
        replay = await self._journal.replay(operation_key=idempotency_key, target_id=target["target_device_id"],
            action=action, intent_id=intent_id, binding_fingerprint=target["binding_fingerprint"],
            origin_context=origin_context, gate_command_id=gate_command_id)
        if replay:
            device = AccessDeviceEntity(key=replay.device_key, kind=target["kind"], name=replay.device_key,
                                        device_id=str(replay.target_device_id))
            return self._operation_from_record(device, replay)
        async with AsyncSessionLocal() as session:
            if gate_command_id is not None:
                await self._journal.assert_parent_active(session, gate_command_id, parent_lease_token or "")
            await self._journal.lock_target(session, uuid.UUID(target["target_device_id"]))
            devices = await self.list_devices_for_session(session)
            config = await get_runtime_config_for_session(session)
            current_plan = self._configuration.target_plan(devices, config, action=action,
                target_device_key=plan.get("target_device_key"),
                require_admission=bool(plan.get("require_admission")), gate_only=bool(plan.get("gate_only")),
                automatic_entry_policy=bool(plan.get("automatic_entry_policy")))
            if plan != current_plan:
                raise ValueError("Access-device configuration changed; a fresh preview and confirmation are required.")
            device = next(item for item in devices if item.device_id == target["target_device_id"])
            claim = await self._journal.claim(session, target=target, action=action, intent_id=intent_id,
                operation_key=idempotency_key, gate_command_id=gate_command_id, expires_at=expires_at,
                origin_context=origin_context)
            await session.commit()
        if not claim.acquired:
            return self._operation_from_record(device, claim.record)
        dispatch = _DeviceDispatch(claim, plan, bypass_schedule, schedule_source, actor_user_id,
                                   auth_version, authorize_dispatch, gate_command_id, parent_lease_token, _entry_precondition)
        started_at = datetime.now(tz=UTC)
        if plan.get("automatic_entry_policy"):
            mode = (_entry_precondition or {}).get("mode")
            if mode != "fanout" and not (mode == "observe_only"
                    and device.device_id == plan.get("admission_target_device_id")):
                row = await self._journal.finish_without_send(claim,
                    detail="Automatic entry does not require a command." if mode == "observe_only"
                    else "Automatic entry requires fresh designated-gate state; no command was attempted.")
                return self._operation_from_record(device, row)
        try:
            # A new, exact-target observation may prove that no transmission is
            # needed. The reservation is still terminalized with durable evidence.
            observation = await self._observe_target(device, action, after=claim.record.created_at, runtime_config=config)
            if observation:
                try:
                    async with AsyncSessionLocal() as session:
                        await self._validate_target_dispatch(session, device, action, dispatch)
                        row = await self._journal.complete_prepared(session, claim,
                            detail="Target already reports the requested physical state.", observation=observation)
                        await session.commit()
                except ValueError as exc:
                    row = await self._journal.finish_without_send(claim, detail=str(exc))
                result = self._operation_from_record(device, row)
            elif plan.get("automatic_entry_policy") and (_entry_precondition or {}).get("mode") != "fanout":
                row = await self._journal.finish_without_send(claim,
                    detail="Observe-only admission lost fresh opening evidence; no command was attempted.")
                result = self._operation_from_record(device, row)
            else:
                result = await self._command_with_failover(device, action, reason, dispatch=dispatch)
        except asyncio.CancelledError:
            # The committed attempt survives cancellation even if this best-effort
            # checkpoint is itself interrupted. Recovery then holds it as unknown.
            await asyncio.shield(self._journal.finish_attempt(claim, delivery=CommandDelivery.UNKNOWN,
                state=GateState.UNKNOWN, detail="Command worker was cancelled before a conclusive receipt."))
            raise
        self._record_command_evidence(result, reason=reason, started_at=started_at, trace_id=current_trace_id())
        return result

    async def _validate_target_dispatch(self, session: AsyncSession, device: AccessDeviceEntity,
                                       action: str, dispatch: _DeviceDispatch) -> RuntimeConfig:
        from app.services.maintenance_state import assert_hardware_enabled
        from app.services.mutation_context import load_active_admin
        from app.services.schedules import evaluate_schedule_id

        if dispatch.actor_user_id is not None:
            await load_active_admin(session, dispatch.actor_user_id, auth_version=dispatch.auth_version, lock=True)
        if dispatch.parent_command_id:
            await self._journal.assert_parent_active(session, dispatch.parent_command_id,
                                                     dispatch.parent_lease_token or "")
        await self._journal.lock_target(session, uuid.UUID(device.device_id))
        await self._journal.lock_claim(session, dispatch.claim)
        await assert_hardware_enabled(session)
        devices = await self.list_devices_for_session(session)
        config = await get_runtime_config_for_session(session)
        plan = self._configuration.target_plan(devices, config, action=action,
            target_device_key=dispatch.plan.get("target_device_key"),
            require_admission=bool(dispatch.plan.get("require_admission")),
            gate_only=bool(dispatch.plan.get("gate_only")),
            automatic_entry_policy=bool(dispatch.plan.get("automatic_entry_policy")))
        if plan != dispatch.plan:
            raise ValueError("Access-device configuration changed before dispatch; a fresh confirmation is required.")
        # Take the domain checkpoint only after every possibly-blocking command
        # lock. Its owner may lock domain rows; no command send runs inside it.
        if dispatch.authorize_dispatch is not None:
            await dispatch.authorize_dispatch(session)
        elif dispatch.plan.get("require_admission"):
            raise ValueError("Automatic access requires a current domain authorization checkpoint.")
        if dispatch.parent_command_id:
            await self._journal.assert_parent_active(session, dispatch.parent_command_id,
                                                     dispatch.parent_lease_token or "")
        if action == "open" and not dispatch.bypass_schedule:
            current_device = next(item for item in devices if item.device_id == device.device_id)
            schedule_id = self._uuid_or_none(current_device.schedule_id)
            if schedule_id is not None:
                try:
                    async with session.begin_nested():
                        locked_schedule = await session.scalar(select(Schedule).where(Schedule.id == schedule_id)
                            .with_for_update(read=True, nowait=True).execution_options(populate_existing=True))
                except DBAPIError as exc:
                    if getattr(exc.orig, "sqlstate", None) == "55P03":
                        raise ValueError("The assigned device schedule is being edited; a fresh command is required.") from exc
                    raise
                if locked_schedule is None:
                    raise ValueError("The assigned device schedule no longer exists; a fresh command is required.")
            now = await session.scalar(select(func.clock_timestamp()))
            schedule = await evaluate_schedule_id(session, schedule_id, now,
                timezone_name=config.site_timezone, default_policy=config.schedule_default_policy,
                source=dispatch.schedule_source or device.kind)
            if not schedule.allowed:
                raise ValueError(schedule.reason or "Outside schedule.")
        return config

    async def _begin_target_attempt(self, device: AccessDeviceEntity, action: str,
                                    dispatch: _DeviceDispatch, provider: str) -> tuple[bool, RuntimeConfig]:
        entry_sample = None
        if dispatch.plan.get("automatic_entry_policy"):
            if (dispatch.entry_precondition or {}).get("mode") != "fanout":
                raise ValueError("Automatic entry did not establish a closed-gate send precondition.")
            entry_sample = await self._observe_entry_policy(dispatch.plan)
        async with AsyncSessionLocal() as session:
            config = await self._validate_target_dispatch(session, device, action, dispatch)
            if dispatch.plan.get("automatic_entry_policy"):
                now = await session.scalar(select(func.clock_timestamp()))
                if not self._fresh_entry_sample(entry_sample, dispatch.plan, now):
                    raise ValueError("Fresh designated-gate state is unavailable; no new automatic target was attempted.")
            allowed = await self._journal.begin_attempt(session, dispatch.claim, provider=provider,
                                                        external_id=device.bindings[provider].external_id)
            await session.commit()
            return allowed, config

    async def open_access_gates(
        self, reason: str, *, bypass_schedule: bool = False, command_context: Any,
    ) -> list[AccessDeviceOperationResult]:
        context = command_context
        try:
            plan = context.target_plan or await self.preview_gate_open(
                target_device_key=context.target_device_key, require_admission=context.require_admission,
                automatic_entry_policy=context.automatic_entry_policy)
            if context.require_admission and not plan.get("require_admission"):
                raise ValueError("Automatic access requires an admission-validated target plan.")
            if bool(plan.get("automatic_entry_policy")) != context.automatic_entry_policy:
                raise ValueError("The confirmed automatic entry policy differs from the requested operation.")
            if context.command_id:
                await self._journal.freeze_parent_plan(context.command_id, context.lease_token, plan)
        except ValueError as exc:
            # This typed refusal is valid only before entering the target loop.
            raise GateCommandNotSent(str(exc)) from exc
        entry_precondition = None
        if context.automatic_entry_policy:
            sample = await self._observe_entry_policy(plan)
            state = (sample or {}).get("state")
            mode = "fanout" if state == "closed" else "observe_only" if state in {"open", "opening"} else "unresolved"
            entry_precondition = {"mode": mode, **(sample or {"state": "unknown"})}
            if context.command_id:
                entry_precondition = await self._journal.freeze_entry_precondition(
                    context.command_id, context.lease_token, entry_precondition)
        outcomes = []
        for target in plan["targets"]:
            try:
                result = await self.command_device(target["device_key"], "open", reason,
                    bypass_schedule=bypass_schedule, schedule_source="gate", intent_id=context.intent_id,
                    idempotency_key=context.idempotency_key, gate_command_id=context.command_id,
                    target_plan=plan, expires_at=context.expires_at, parent_lease_token=context.lease_token,
                    actor_user_id=context.actor_user_id, auth_version=context.auth_version,
                    authorize_dispatch=context.authorize_dispatch, _entry_precondition=entry_precondition)
            except (ValueError, LookupError) as exc:
                # Preserve earlier target receipts when a later target cannot be
                # attempted. A validation failure must not erase an accepted send.
                async with AsyncSessionLocal() as session:
                    claim = await self._journal.claim(session, target=target, action="open",
                        intent_id=context.intent_id, operation_key=context.idempotency_key,
                        gate_command_id=context.command_id, expires_at=context.expires_at)
                    await session.commit()
                row = (await self._journal.finish_without_send(claim, detail=str(exc))
                       if claim.acquired else claim.record)
                device = AccessDeviceEntity(key=target["device_key"], name=target["device_key"],
                    kind=target["kind"], device_id=target["target_device_id"])
                result = self._operation_from_record(device, row)
            outcomes.append(replace(result, metadata={**result.metadata,
                "admission_target_device_id": plan.get("admission_target_device_id"),
                "automatic_entry_precondition": entry_precondition}))
        return outcomes

    def _operation_from_record(self, device: AccessDeviceEntity, row: AccessDeviceCommandRecord) -> AccessDeviceOperationResult:
        receipt = device_command_receipt(row)
        providers = row.provider_receipts or []
        return AccessDeviceOperationResult(device=device, action=row.action, accepted=receipt["accepted"],
            state=GateState(receipt["state"]), detail=row.detail,
            primary_provider=providers[0]["provider"] if providers else None,
            used_provider=providers[-1]["provider"] if providers else None, failover_used=len(providers) > 1,
            delivery=CommandDelivery(receipt["delivery"]),
            attempts=[AccessDeviceProviderAttempt(provider=item["provider"],
                accepted=item["delivery"] == "accepted", unavailable=item["delivery"] == "not_sent",
                detail=row.detail, state=item.get("state"), delivery=CommandDelivery(item["delivery"]),
                verified=bool(receipt["verified"] and (row.verification_evidence or {}).get("provider") == item["provider"]),
                confirmation_failed=item["delivery"] == "accepted" and not receipt["verified"])
                for item in providers],
            metadata={"verified": receipt["verified"], "target_receipt": receipt,
                      "command_id": receipt["command_id"], "requires_reconciliation": receipt["requires_reconciliation"]})

    async def _observe_target(self, device: AccessDeviceEntity, action: str, *, after: datetime,
                              runtime_config: RuntimeConfig, provider_name: str | None = None,
                              expected_states: set[GateState] | None = None) -> dict[str, Any] | None:
        expected = expected_states or ({GateState.OPEN, GateState.OPENING} if action == "open" else {GateState.CLOSED})
        providers = [provider_name] if provider_name else self._configuration.provider_order(device, runtime_config)
        for name in providers:
            try:
                observation = await asyncio.wait_for(get_access_device_provider(name).observe_state(device.bindings[name], runtime_config=runtime_config),
                                                      timeout=COMMAND_STATE_READ_TIMEOUT_SECONDS)
            except Exception:
                continue
            age = (datetime.now(tz=UTC) - observation.observed_at).total_seconds()
            if observation.state in expected and observation.observed_at >= after and 0 <= age <= 10:
                return {"provider": name, "state": observation.state.value, "observed_at": observation.observed_at}
        return None

    async def _observe_entry_policy(self, plan: dict[str, Any]) -> dict[str, Any] | None:
        # Provider reads occur outside all attempt transactions. The resulting
        # evidence is checked again against DB time after the final blocking lock.
        async with AsyncSessionLocal() as session:
            config = await get_runtime_config_for_session(session)
            devices = await self.list_devices_for_session(session)
            current = self._configuration.target_plan(devices, config, action="open",
                target_device_key=plan.get("target_device_key"), require_admission=True,
                gate_only=True, automatic_entry_policy=True)
            if current != plan:
                raise ValueError("Entry-gate configuration changed; a fresh command plan is required.")
        device = next(item for item in devices if item.device_id == plan["admission_target_device_id"])
        sample = await self._observe_target(device, "open", after=datetime.now(tz=UTC) - timedelta(seconds=10),
            runtime_config=config, expected_states=set(GateState))
        if sample is None:
            return None
        target = next(item for item in plan["targets"] if item["target_device_id"] == device.device_id)
        return {**sample, "observed_at": sample["observed_at"].isoformat(),
                "target_device_id": device.device_id, "binding_fingerprint": target["binding_fingerprint"]}

    @staticmethod
    def _fresh_entry_sample(sample: dict[str, Any] | None, plan: dict[str, Any], now: datetime) -> bool:
        if not sample or sample.get("state") not in {"closed", "open", "opening"}:
            return False
        target = next(item for item in plan["targets"]
                      if item["target_device_id"] == plan["admission_target_device_id"])
        return (sample.get("target_device_id") == target["target_device_id"]
                and sample.get("binding_fingerprint") == target["binding_fingerprint"]
                and 0 <= (now - datetime.fromisoformat(sample["observed_at"])).total_seconds() <= 10)

    async def _command_with_failover(
        self, device: AccessDeviceEntity, action: str, reason: str, *,
        dispatch: _DeviceDispatch | None = None,
    ) -> AccessDeviceOperationResult:
        # Production callers always supply a claimed journal. The optional
        # participant permits isolated provider-contract tests without a database.
        provider_names = ([item["provider"] for target in dispatch.plan["targets"]
                           if target["target_device_id"] == device.device_id for item in target["binding_snapshot"]["providers"]]
                          if dispatch else await self._provider_order_for_device(device))
        attempts: list[AccessDeviceProviderAttempt] = []
        for index, provider_name in enumerate(provider_names):
            binding = device.bindings[provider_name]
            provider = get_access_device_provider(provider_name)
            runtime_config = None
            if dispatch:
                try:
                    may_send, runtime_config = await self._begin_target_attempt(device, action, dispatch, provider_name)
                except ValueError as exc:
                    row = await self._journal.finish_without_send(dispatch.claim, detail=str(exc))
                    return self._operation_from_record(device, row)
                if not may_send:
                    row = await self._journal.finish_without_send(dispatch.claim, detail="Command no longer owns dispatch authority.")
                    return self._operation_from_record(device, row)
            started_at = datetime.now(tz=UTC)
            try:
                result = (await provider.command_cover(binding, action, reason, runtime_config=runtime_config)
                          if dispatch else await provider.command_cover(binding, action, reason))
            except AccessDeviceProviderUnavailable as exc:
                result = AccessDeviceCommandResult(False, GateState.UNKNOWN, str(exc),
                    provider=provider_name, external_id=binding.external_id, delivery=CommandDelivery.NOT_SENT)
            except Exception as exc:
                result = AccessDeviceCommandResult(False, GateState.UNKNOWN, str(exc),
                    provider=provider_name, external_id=binding.external_id, delivery=CommandDelivery.UNKNOWN)
            delivery = result.delivery or CommandDelivery.UNKNOWN
            observation = None
            verified, detail, state = False, result.detail, result.state
            if delivery == CommandDelivery.ACCEPTED:
                runtime_config = runtime_config if runtime_config is not None else await get_runtime_config()
                timeout = (CLOSE_COMMAND_CONFIRMATION_TIMEOUT_SECONDS if action == "close"
                           else COMMAND_CONFIRMATION_TIMEOUT_SECONDS)
                deadline = asyncio.get_running_loop().time() + timeout
                immediate = result.observation
                expected = {GateState.OPEN, GateState.OPENING} if action == "open" else {GateState.CLOSED}
                if immediate and immediate.state in expected and immediate.observed_at >= started_at:
                    observation = {"provider": provider_name, "state": immediate.state.value,
                                   "observed_at": immediate.observed_at}
                while observation is None:
                    observation = await self._observe_target(device, action, after=started_at,
                        provider_name=provider_name, runtime_config=runtime_config)
                    if observation or asyncio.get_running_loop().time() >= deadline:
                        break
                    await asyncio.sleep(COMMAND_CONFIRMATION_POLL_SECONDS)
                verified = observation is not None
                state = GateState(observation["state"]) if observation else GateState.UNKNOWN
                if not verified:
                    expected_label = "closed" if action == "close" else "open/opening"
                    detail = (f"Provider accepted {action}, but {device.name} did not report {expected_label}; "
                              "fresh physical verification is pending.")
            attempts.append(AccessDeviceProviderAttempt(provider=provider_name,
                accepted=delivery == CommandDelivery.ACCEPTED, unavailable=delivery == CommandDelivery.NOT_SENT,
                detail=detail, state=state.value, verified=verified,
                confirmation_failed=delivery == CommandDelivery.ACCEPTED and not verified, delivery=delivery))
            if dispatch:
                row = await self._journal.finish_attempt(dispatch.claim, delivery=delivery,
                    state=state, detail=detail, observation=observation,
                    acceptance_basis=result.metadata.get("acceptance_basis"),
                    has_fallback=index + 1 < len(provider_names))
                if row.state != "prepared":
                    return self._operation_from_record(device, row)
            if delivery == CommandDelivery.NOT_SENT:
                continue
            # Neither rejection nor uncertainty permits another provider or resend.
            return AccessDeviceOperationResult(device=device, action=action,
                accepted=delivery == CommandDelivery.ACCEPTED, state=state, detail=detail,
                primary_provider=provider_names[0], used_provider=provider_name, failover_used=index > 0,
                attempts=attempts, delivery=delivery,
                metadata={**result.metadata, "delivery": delivery.value, "verified": verified,
                          "accepted_unverified": delivery == CommandDelivery.ACCEPTED and not verified})
        if dispatch:
            row = await self._journal.finish_without_send(dispatch.claim,
                detail=self._failed_command_detail(device, action, attempts))
            return self._operation_from_record(device, row)
        return self._operation_rejected(device, action, self._failed_command_detail(device, action, attempts),
                                        attempts=attempts, metadata={"delivery": "not_sent"})

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
        return self._configuration.provider_order(device, await get_runtime_config())


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

    def _record_command_evidence(
        self,
        result: AccessDeviceOperationResult,
        *,
        reason: str,
        started_at: datetime,
        trace_id: str | None,
    ) -> None:
        dispatch_state, reason_code = self._command_evidence_outcome(result)
        payload = result.as_payload()
        payload["dispatch_state"] = dispatch_state
        payload["reason_code"] = reason_code
        payload["command_sent"] = (None if result.delivery == CommandDelivery.UNKNOWN
                                   else result.delivery != CommandDelivery.NOT_SENT and bool(result.attempts or result.accepted))
        ended_at = datetime.now(tz=UTC)
        span_status = (
            "blocked"
            if dispatch_state == "withheld"
            else "error"
            if dispatch_state == "attempted" and not result.accepted
            else "warning"
            if dispatch_state in {"accepted", "uncertain"}
            else "ok"
        )
        telemetry.record_span(
            "Access device command outcome",
            trace_id=trace_id,
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            started_at=started_at,
            ended_at=ended_at,
            attributes={
                "device_key": result.device.key,
                "device_name": result.device.name,
                "device_kind": result.device.kind,
                "action": result.action,
                "dispatch_state": dispatch_state,
                "reason_code": reason_code,
            },
            input_payload={"reason": reason},
            output_payload=payload,
            status=span_status,
            error=result.detail if span_status == "error" else None,
        )
        audit_outcome = (
            "success"
            if dispatch_state == "verified"
            else "accepted"
            if dispatch_state == "accepted"
            else "failed"
            if dispatch_state == "attempted"
            else "requires_review"
            if dispatch_state == "uncertain"
            else "skipped"
        )
        emit_audit_log(
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            action=f"access_device.command.{dispatch_state}",
            actor="IACS",
            target_entity="AccessDevice",
            target_id=result.device.key,
            target_label=result.device.name,
            outcome=audit_outcome,
            level="error" if audit_outcome == "failed" else "warning" if dispatch_state in {"accepted", "uncertain"} else "info",
            trace_id=trace_id,
            metadata=payload,
        )

    def _command_evidence_outcome(
        self,
        result: AccessDeviceOperationResult,
    ) -> tuple[str, str]:
        schedule = result.metadata.get("schedule_evaluation")
        schedule_payload = schedule if isinstance(schedule, dict) else {}
        if result.metadata.get("schedule_denied") or schedule_payload.get("allowed") is False:
            return "withheld", str(
                result.metadata.get("reason_code")
                or schedule_payload.get("reason_code")
                or "schedule_denied"
            )
        reason_code = str(result.metadata.get("reason_code") or "")
        if reason_code == "device_disabled":
            return "withheld", reason_code
        if result.verified:
            return "verified", "device_state_verified"
        if result.delivery == CommandDelivery.UNKNOWN:
            return "uncertain", "provider_delivery_unknown"
        if result.delivery == CommandDelivery.NOT_SENT:
            return "withheld", reason_code or "integration_not_sent"
        if result.accepted:
            return "accepted", "device_state_unverified"
        if result.attempts:
            return "attempted", "integration_rejected"
        return "withheld", reason_code or "integration_not_configured"

    def _operation_rejected(
        self,
        device: AccessDeviceEntity,
        action: str,
        detail: str | None,
        *,
        attempts: list[AccessDeviceProviderAttempt] | None = None,
        metadata: dict[str, Any] | None = None,
        state: GateState = GateState.UNKNOWN,
    ) -> AccessDeviceOperationResult:
        return AccessDeviceOperationResult(
            device=device,
            action=action,
            accepted=False,
            state=state,
            detail=detail,
            attempts=attempts or [],
            metadata=metadata or {},
            delivery=(CommandDelivery.NOT_SENT
                      if not attempts or all(attempt.delivery == CommandDelivery.NOT_SENT for attempt in attempts)
                      else CommandDelivery.REJECTED),
        )

    async def _remember_state(
        self,
        device: AccessDeviceEntity,
        state: GateState,
        *,
        provider: str | None,
        raw_state: str | None,
        observed_at: datetime | None = None,
        binding_fingerprint: str | None = None,
    ) -> None:
        previous = self._state_cache.get(device.key, {})
        previous_state = str(previous.get("state") or GateState.UNKNOWN.value)
        observed_at = observed_at or datetime.now(tz=UTC)
        previous_time = previous.get("updated_at")
        if previous_time and datetime.fromisoformat(previous_time) > observed_at:
            return
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
                    access_device_id=self._uuid_or_none(device.device_id),
                    binding_fingerprint=binding_fingerprint,
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
                await self.reconcile_commands()
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
