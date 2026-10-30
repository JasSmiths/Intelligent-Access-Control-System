"""One durable silent phone request per live encounter; bounded waiting without locks."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, selectinload

from app.core.task_lifecycle import drain_owned
from app.db.session import AsyncSessionLocal
from app.models import NotificationRun, Person, ResidentRecoveryJourney, Vehicle
from app.models.access_pipeline import LprCoordinatorState, LprDebounceWindow
from app.modules.lpr.base import PlateRead
from app.modules.notifications.base import NotificationContext
from app.services.access.authorization import recognition_deadline
from app.services.access.completion_database import access_session
from app.services.access.pipeline_contracts import WindowClaim
from app.services.access.pipeline_store import assert_window_in_session
from app.services.access.window_store import StoredWindow
from app.services.event_bus import RealtimeEvent, event_bus
from app.services.gate_controller import get_gate_controller
from app.services.notification_runs import NotificationRunStore
from app.services.resident_location_contracts import LOCATION_UPDATED, LocationRefresh
from app.services.resident_recovery import MOBILE_PATTERN, connection_epoch, lock_owner
from app.services.resident_recovery_evidence import configuration_binding, parse_time
from app.services.schedules import evaluate_vehicle_schedule
from app.services.settings import RuntimeConfig, get_runtime_config_for_session
from app.services.workflows.notification_payloads import notification_context_payload

TRIGGER = "resident_location_refresh"
ORIGIN = "resident_location_origin"


async def reserve_in_session(session: AsyncSession, *, person: Person, vehicle: Vehicle,
                             config: RuntimeConfig, read: PlateRead, window: StoredWindow,
                             claim: WindowClaim) -> LocationRefresh | None:
    """Called only by the authorized, exact-plate recovery probe in its transaction."""
    raw = read.raw_payload or {}
    if "backfill" in read.source.casefold() or raw.get("backfill") or raw.get("backfilled") or raw.get("skip_automation_actions"):
        return None
    await assert_window_in_session(session, claim)
    now = await session.scalar(select(func.clock_timestamp()))
    epoch = connection_epoch()
    target = person.home_assistant_mobile_app_notify_service or ""
    if not isinstance(now, datetime) or epoch is None or not MOBILE_PATTERN.fullmatch(target):
        return None
    expires = min(now + timedelta(seconds=20), recognition_deadline(
        min(read.captured_at, window.best_read.captured_at, window.first_read.captured_at),
        window.summary.min_received_at))
    if (expires - now).total_seconds() < 3:
        return None
    # A separate short-lived mutex coalesces all of this resident's vehicles.
    # Person remains a shared authorization read, compatible with garage dispatch.
    mutex = int.from_bytes(person.id.bytes[:8], "big", signed=True)
    if not await session.scalar(select(func.pg_try_advisory_xact_lock(mutex))):
        return None
    run_id = uuid.uuid5(window.summary.evidence.window_id, TRIGGER)
    existing = await session.scalar(select(NotificationRun.id).where(
        NotificationRun.trigger_event == TRIGGER, NotificationRun.subject == str(person.id),
        NotificationRun.queued_at >= now - timedelta(minutes=1)).limit(1))
    if existing or await session.get(NotificationRun, run_id):
        return None
    binding = configuration_binding(person, config)
    origin = {"window_id": str(claim.window_id), "window_token": str(claim.token),
              "coordinator_token": str(claim.coordinator.token), "epoch": claim.coordinator.epoch,
              "person_id": str(person.id), "vehicle_id": str(vehicle.id),
              "registration_number": vehicle.registration_number, "binding": binding,
              "requested_at": now.isoformat(), "expires_at": expires.isoformat(),
              "captured_at": read.captured_at.isoformat(), "notify_service": target}
    action = {"type": "mobile", "target": target, "title": "",
              "message": "request_location_update", "delivery_mode": "literal",
              "resident_location_output": str(run_id)}
    payload = notification_context_payload(NotificationContext(event_type=TRIGGER,
        subject=str(person.id), severity="info", facts={}))
    payload[ORIGIN] = origin
    await NotificationRunStore().enqueue_prepared_in_session(session, payload, run_id=run_id,
        plan=[{"rule": {"id": TRIGGER, "name": "Resident location refresh"},
               "action": action, "state": "pending"}])
    return LocationRefresh(run_id, person.id, vehicle.id, read.source, read.captured_at, now, expires, binding, epoch)


async def wait_for_location(refresh: LocationRefresh) -> LocationRefresh:
    """Wake on committed tracker updates; never keep a transaction open while waiting."""
    wake = asyncio.Event()

    async def updated(event: RealtimeEvent) -> None:
        if event.payload.get("person_id") == str(refresh.person_id):
            wake.set()

    event_bus.subscribe(updated, scope="all_workers", event_types=frozenset({LOCATION_UPDATED}))
    try:
        async with asyncio.timeout(max(0, (refresh.expires_at - datetime.now(UTC)).total_seconds())):
            return await _wait_for_location(refresh, wake)
    except TimeoutError:
        return refresh
    finally:
        event_bus.unsubscribe(updated)
        await drain_owned(_close_request(refresh.run_id))


async def _close_request(run_id: uuid.UUID) -> None:
    async with access_session(AsyncSessionLocal) as session:
        run = await session.get(NotificationRun, run_id, with_for_update=True)
        if run is not None:
            run.context = {**run.context, ORIGIN: {**run.context[ORIGIN], "closed": True}}
            await session.commit()


async def _wait_for_location(refresh: LocationRefresh, wake: asyncio.Event) -> LocationRefresh:
    # Wall and monotonic bounds both apply: a clock correction cannot prolong waiting.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0, (refresh.expires_at - datetime.now(UTC)).total_seconds())
    for _ in range(64):
        wake.clear()
        if loop.time() >= deadline or datetime.now(UTC) >= refresh.expires_at:
            return refresh
        if connection_epoch() != refresh.epoch:
            return refresh
        async with access_session(AsyncSessionLocal) as session:
            row = (await session.execute(select(ResidentRecoveryJourney.latest_at,
                ResidentRecoveryJourney.invalid_reason).where(
                    ResidentRecoveryJourney.person_id == refresh.person_id))).first()
        if row and row.invalid_reason:
            return refresh
        if row and row.latest_at and row.latest_at > refresh.requested_at:
            observed_at = datetime.now(UTC)
            remaining = min(2.0, deadline - loop.time(), (refresh.expires_at - observed_at).total_seconds())
            if remaining <= 0:
                return refresh
            try:
                async with asyncio.timeout(remaining):
                    state = await get_gate_controller("configured").current_state()
                return replace(refresh, gate_observed_at=observed_at, gate_state=state.value)
            except Exception:  # noqa: BLE001 - unavailable gate evidence fails closed into resident approval.
                return refresh
        await wake.wait()
    return refresh


async def authorize_output(session: AsyncSession, payload: dict[str, Any],
                           run_id: uuid.UUID | None, action: dict[str, Any] | None) -> str | None:
    """Current scoped authority, rechecked at the durable provider-attempt checkpoint."""
    origin = payload.get(ORIGIN)
    if not isinstance(origin, dict) or action is None:
        return "location_request_origin_invalid"
    try:
        window_id = uuid.UUID(origin["window_id"])
        if (run_id != uuid.uuid5(window_id, TRIGGER) or action.get("resident_location_output") != str(run_id)
                or action.get("type") != "mobile" or action.get("message") != "request_location_update"):
            return "location_request_origin_invalid"
        saved = await session.scalar(select(NotificationRun.context).where(NotificationRun.id == run_id))
        if not isinstance(saved, dict) or saved.get(ORIGIN, {}).get("closed"):
            return "location_request_encounter_ended"
        # NOWAIT shared locks fence revocation without queueing behind an active mutation.
        coordinator = await session.get(LprCoordinatorState, 1,
            with_for_update={"read": True, "nowait": True}, populate_existing=True)
        window = await session.get(LprDebounceWindow, window_id,
            with_for_update={"read": True, "nowait": True}, populate_existing=True)
        person = await lock_owner(session, uuid.UUID(origin["person_id"]), nowait=True, read_only=True)
        vehicle = await session.scalar(select(Vehicle).options(defer(Vehicle.vehicle_photo_data_url, raiseload=True),
            selectinload(Vehicle.owner).defer(Person.profile_photo_data_url, raiseload=True).defer(Person.notes, raiseload=True),
            selectinload(Vehicle.owner).selectinload(Person.schedule),
            selectinload(Vehicle.schedule)).where(Vehicle.id == uuid.UUID(origin["vehicle_id"])))
        config = await get_runtime_config_for_session(session)
        now = await session.scalar(select(func.clock_timestamp()))
        expires, requested, captured = (parse_time(origin.get(key)) for key in ("expires_at", "requested_at", "captured_at"))
        if not isinstance(now, datetime) or not expires or not requested or not captured:
            return "location_request_origin_invalid"
        if not requested <= now < expires or not 0 < (expires-requested).total_seconds() <= 20:
            return "location_request_expired"
        if (coordinator is None or window is None or window.state != "finalizing"
                or str(coordinator.lease_token) != origin["coordinator_token"]
                or coordinator.epoch != origin["epoch"] or window.coordinator_epoch != coordinator.epoch
                or str(window.lease_token) != origin["window_token"]
                or coordinator.lease_until is None or coordinator.lease_until <= now
                or window.lease_until is None or window.lease_until <= now
                or expires > recognition_deadline(captured, window.min_received_at)):
            return "location_request_encounter_ended"
        if (person is None or vehicle is None or not person.is_active or not vehicle.is_active
                or vehicle.person_id != person.id or vehicle.registration_number != origin["registration_number"]
                or not person.missed_exit_recovery_enabled or not config.missed_exit_recovery_enabled
                or configuration_binding(person, config) != origin["binding"]
                or action.get("target") != person.home_assistant_mobile_app_notify_service
                or action.get("target") != origin["notify_service"]):
            return "location_request_revoked"
        schedule = await evaluate_vehicle_schedule(session, vehicle, captured,
            timezone_name=config.site_timezone, default_policy=config.schedule_default_policy)
        return None if schedule.allowed else "location_request_schedule_denied"
    except (ValueError, TypeError, KeyError):
        return "location_request_origin_invalid"
