"""Durable, owner-scoped missed-exit recovery and resident approval authority.

No hardware/provider calls occur while domain transactions hold row locks.
One owner journey is consumed across all vehicles. Approval is a separate
120-second authority; ordinary recognition retains its existing 60-second limit.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth_secret import get_auth_secret
from app.db.session import AsyncSessionLocal
from app.models import (
    AccessEvent,
    GateCommandRecord,
    GateMalfunctionState,
    MaintenanceModeState,
    MissedExitRecoveryAttempt,
    MovementSagaRecord,
    NotificationActionContext,
    NotificationRun,
    Person,
    Presence,
    ResidentRecoveryJourney,
    SystemSetting,
    Vehicle,
)
from app.models.enums import (
    AccessDecision,
    AccessDirection,
    GateCommandState,
    GateMalfunctionStatus,
    MovementSagaState,
    PresenceState,
    TimingClassification,
)
from app.modules.notifications.base import NotificationContext
from app.services.notification_runs import NotificationRunStore
from app.services.resident_recovery_evidence import (
    MAX_SAMPLES,
    POLICY_VERSION,
    configuration_binding,
    evaluate_journey,
    gate_coordinates,
    location_sample,
    parse_time,
)
from app.services.settings import get_runtime_config_for_session
from app.services.workflows.notification_payloads import notification_context_payload

ACTION = "resident.missed_exit.allow_entry"
PREFIX = "iacs:resident_entry:"
MOBILE_PATTERN = re.compile(r"notify\.mobile_app_[A-Za-z0-9_]{1,192}\Z")
SETTINGS_KEYS = ("missed_exit_recovery_enabled", "missed_exit_recovery_gate_latitude",
                 "missed_exit_recovery_gate_longitude", "gate_admission_device_key")
_epoch = str(uuid.uuid4())
_connected = False
_connection_binding: str | None = None


def invalidate_connection(*, connected: bool = False, connection_binding: str | None = None) -> None:
    global _epoch, _connected, _connection_binding
    _epoch, _connected = str(uuid.uuid4()), connected
    _connection_binding = connection_binding if connected else None


def token_for(identity: uuid.UUID) -> str:
    return hmac.new(get_auth_secret().encode(), f"iacs:resident-entry:v1:{identity}".encode(), hashlib.sha256).hexdigest()


def token_hash(token: str) -> str:
    return hmac.new(get_auth_secret().encode(), token.encode(), hashlib.sha256).hexdigest()


def add_stage(attempt: MissedExitRecoveryAttempt, stage: str, status: str, reason: str,
              *, at: datetime | None = None, details: dict | None = None) -> None:
    item = {"at": (at or datetime.now(UTC)).isoformat(), "stage": stage, "status": status, "reason": reason}
    if details:
        item["details"] = details
    attempt.timeline = [*(attempt.timeline or [])[-39:], item]


async def lock_owner(session: AsyncSession, person_id: uuid.UUID, *, nowait: bool = False) -> Person | None:
    return await session.scalar(select(Person).where(Person.id == person_id).with_for_update(nowait=nowait)
                                .execution_options(populate_existing=True))


async def observe_tracker(entity_id: str, new_state: dict, old_state: dict) -> None:
    """Only HA websocket changes enter here; bootstrap and reconnect never replay."""
    received_epoch = _epoch
    if not _connected:
        return
    async with AsyncSessionLocal() as session:
        config = await get_runtime_config_for_session(session)
        from app.modules.home_assistant.client import home_assistant_connection_fingerprint
        if not config.missed_exit_recovery_enabled or _connection_binding != home_assistant_connection_fingerprint(config):
            return
        ids = list((await session.scalars(select(Person.id).where(Person.is_active.is_(True),
            Person.missed_exit_recovery_enabled.is_(True),
            Person.missed_exit_recovery_tracker_entity_id == entity_id))).all())
        if len(ids) != 1:
            return
        person = await lock_owner(session, ids[0])
        if person.missed_exit_recovery_tracker_entity_id != entity_id or not person.missed_exit_recovery_enabled:
            return
        if not _connected or received_epoch != _epoch:
            return
        now = await session.scalar(select(func.clock_timestamp()))
        binding = configuration_binding(person, config)
        await session.execute(insert(ResidentRecoveryJourney).values(person_id=person.id,
            epoch=_epoch, binding=binding, samples=[]).on_conflict_do_nothing(index_elements=["person_id"]))
        row = await session.get(ResidentRecoveryJourney, person.id, with_for_update=True, populate_existing=True)
        if row.epoch != _epoch or row.binding != binding:
            row.epoch, row.binding, row.samples = _epoch, binding, []
            row.coordinate_hash, row.latest_at, row.claimed_event_id, row.consumed_at = None, None, None, None
        sample, reason = location_sample(new_state, old_state, config, now)
        # Attribute-only changes cannot refresh nor destroy still-fresh GPS evidence.
        if reason == "coordinates_unchanged_or_bootstrap":
            return
        if sample is None:
            row.samples, row.invalid_reason = [], reason
            await session.commit()
            return
        at = parse_time(sample["at"])
        if row.consumed_at:
            if sample["distance_m"]-sample["accuracy_m"] < 500:
                return
            row.samples, row.coordinate_hash, row.latest_at = [], None, None
            row.claimed_event_id, row.consumed_at = None, None
        if row.latest_at and at <= row.latest_at:
            row.samples, row.invalid_reason = [], "out_of_order_update"
            await session.commit()
            return
        if sample["coordinate_hash"] == row.coordinate_hash:
            return
        previous = (row.samples or [])[-1] if row.samples else None
        if previous:
            elapsed = (at-parse_time(previous["at"])).total_seconds()
            jump = abs(sample["distance_m"]-previous["distance_m"])-sample["accuracy_m"]-previous["accuracy_m"]
            if elapsed <= 0 or jump/elapsed > 55:
                row.samples, row.invalid_reason = [], "implausible_location_jump"
                row.latest_at, row.coordinate_hash = at, sample.pop("coordinate_hash")
                await session.commit()
                return
            # Terminal arrival is a one-shot snapshot: further near-home fixes
            # cannot extend it; leaving that boundary requires a new away episode.
            if previous["distance_m"]+previous["accuracy_m"] <= 150:
                if sample["distance_m"]+sample["accuracy_m"] <= 150:
                    return
                row.samples = []
        row.coordinate_hash, row.latest_at = sample.pop("coordinate_hash"), at
        row.samples = [s for s in (row.samples or []) if parse_time(s["at"]) >= at-timedelta(hours=2)][-(MAX_SAMPLES-1):] + [sample]
        row.invalid_reason = None
        if not _connected or received_epoch != _epoch:
            await session.rollback()
            return
        await session.commit()


async def has_conflict(session: AsyncSession, person: Person, vehicle: Vehicle, captured_at: datetime) -> bool:
    presence = await session.scalar(select(Presence.state).where(Presence.person_id == person.id))
    latest = await session.scalar(select(AccessEvent).where(AccessEvent.vehicle_id == vehicle.id,
        AccessEvent.decision == AccessDecision.GRANTED, AccessEvent.occurred_at < captured_at,
        ~AccessEvent.source.ilike("%backfill%")).order_by(AccessEvent.occurred_at.desc(), AccessEvent.id.desc()).limit(1))
    return presence == PresenceState.PRESENT or bool(latest and latest.direction == AccessDirection.ENTRY)


async def blockers(session: AsyncSession, *, now: datetime, exclude_saga_id: uuid.UUID | None = None, exclude_command_key: str | None = None) -> str | None:
    maintenance = await session.scalar(select(MaintenanceModeState.is_active))
    if maintenance:
        return "maintenance_mode"
    malfunction = await session.scalar(select(GateMalfunctionState.id).where(
        GateMalfunctionState.status.in_([GateMalfunctionStatus.ACTIVE, GateMalfunctionStatus.FUBAR])).limit(1))
    if malfunction:
        return "gate_malfunction"
    pending_query = select(GateCommandRecord.id).where(or_(
        GateCommandRecord.state.in_([GateCommandState.PENDING, GateCommandState.LEASED]),
        GateCommandRecord.requires_reconciliation.is_(True)))
    if exclude_command_key:
        pending_query = pending_query.where(GateCommandRecord.idempotency_key != exclude_command_key)
    pending = await session.scalar(pending_query.limit(1))
    if pending:
        return "unresolved_gate_command"
    sagas = select(MovementSagaRecord.id).where(MovementSagaRecord.admission_status == "pending")
    if exclude_saga_id:
        sagas = sagas.where(MovementSagaRecord.id != exclude_saga_id)
    if await session.scalar(sagas.limit(1)):
        return "unresolved_entry"
    return None


async def evaluate_phone(session: AsyncSession, *, person: Person, vehicle: Vehicle,
                         read: Any, config: Any, claim: bool = False) -> tuple[bool, str, dict]:
    started = time.monotonic()
    from app.services.access.reads import _is_exact_known_vehicle_plate_match
    checks: dict = {"policy_version": POLICY_VERSION, "connection_current": _connected,
                   "exact_plate": _is_exact_known_vehicle_plate_match(read) and read.registration_number == vehicle.registration_number}
    now = await session.scalar(select(func.clock_timestamp()))
    if not checks["exact_plate"]:
        return False, "plate_not_exact", checks
    from app.services.movement.sessions import (
        explicit_direction_from_read,
        gate_observation_from_read,
    )
    observation = gate_observation_from_read(read)
    observed_at = parse_time(observation.get("observed_at"))
    checks["gate_state"] = observation.get("state")
    checks["gate_observation_age_s"] = (now-observed_at).total_seconds() if observed_at else None
    if observation.get("state") != "closed" or observed_at is None or not 0 <= (now-observed_at).total_seconds() <= 15:
        return False, "fresh_closed_gate_required", checks
    if explicit_direction_from_read(read) == AccessDirection.EXIT:
        return False, "departure_evidence", checks
    blocked = await blockers(session, now=now)
    if blocked:
        return False, blocked, checks
    if not _connected:
        return False, "tracker_disconnected", checks
    if gate_coordinates(config) is None:
        return False, "gate_coordinates_missing", checks
    row = await session.get(ResidentRecoveryJourney, person.id, with_for_update=claim, populate_existing=True)
    if row is None:
        return False, "phone_journey_missing", checks
    if row.epoch != _epoch or row.binding != configuration_binding(person, config):
        return False, "journey_configuration_or_connection_changed", checks
    if row.consumed_at or row.claimed_event_id:
        return False, "journey_already_consumed", checks
    if row.invalid_reason:
        return False, row.invalid_reason, checks
    prior = await session.scalar(select(AccessEvent.occurred_at).outerjoin(MovementSagaRecord,
        MovementSagaRecord.access_event_id == AccessEvent.id).where(
        or_(MovementSagaRecord.id.is_(None), MovementSagaRecord.admission_status == "verified"),
        AccessEvent.vehicle_id == vehicle.id,
        AccessEvent.person_id == person.id, AccessEvent.direction == AccessDirection.ENTRY,
        AccessEvent.decision == AccessDecision.GRANTED, AccessEvent.occurred_at < read.captured_at,
        ~AccessEvent.source.ilike("%backfill%")).order_by(AccessEvent.occurred_at.desc()).limit(1))
    ready, reason, evidence = evaluate_journey(row.samples, prior_entry_at=prior,
                                             captured_at=read.captured_at, now=now)
    checks.update(evidence, phone_evaluation_ms=round((time.monotonic()-started)*1000, 2))
    return ready, reason, checks


async def invalidate_owner_in_session(session: AsyncSession, person_id: uuid.UUID, *, event: AccessEvent) -> None:
    """Entry decision consumes authority before dispatch; never restored on failure."""
    row = await session.get(ResidentRecoveryJourney, person_id, with_for_update=True, populate_existing=True)
    if row:
        row.samples, row.consumed_at = [], datetime.now(UTC)
        row.claimed_event_id = event.id


async def validate_person_configuration(session: AsyncSession, person: Person) -> None:
    entity = (person.missed_exit_recovery_tracker_entity_id or "").strip() or None
    if entity and not re.fullmatch(r"device_tracker\.[a-z0-9_]{1,220}", entity):
        raise ValueError("Select one Home Assistant device_tracker entity.")
    person.missed_exit_recovery_tracker_entity_id = entity
    if entity and await session.scalar(select(Person.id).where(Person.id != person.id,
            Person.missed_exit_recovery_tracker_entity_id == entity).limit(1)):
        raise ValueError("This tracker is already assigned to another resident.")
    if person.missed_exit_recovery_enabled:
        mobile = person.home_assistant_mobile_app_notify_service or ""
        if entity is None or not MOBILE_PATTERN.fullmatch(mobile):
            raise ValueError("Recovery requires a GPS tracker and a mobile_app notification destination.")
        matches = list((await session.scalars(select(Person.id).where(
            Person.home_assistant_mobile_app_notify_service == mobile))).all())
        if any(identity != person.id for identity in matches):
            raise ValueError("Recovery requires a notification destination unique to this resident.")


async def record_attempt(session: AsyncSession, *, event: AccessEvent, saga: MovementSagaRecord,
                         person: Person, recovery: dict, config: Any) -> MissedExitRecoveryAttempt:
    now = await session.scalar(select(func.clock_timestamp()))
    authorized = event.decision == AccessDecision.GRANTED
    payload = event.raw_payload or {}
    historical = ("backfill" in event.source.casefold() or "backfill" in payload
                  or payload.get("backfilled") or payload.get("skip_automation_actions"))
    if historical:
        recovery["approval_eligible"] = False
        recovery["reason"] = "historical_origin_ineligible"
    attempt = MissedExitRecoveryAttempt(id=uuid.uuid4(), occurred_at=event.occurred_at,
        owner_id=person.id, owner_name=person.display_name, vehicle_id=event.vehicle_id,
        registration_number=event.registration_number, event_id=event.id, saga_id=saga.id,
        method=recovery.get("method", "none"), outcome=("departure" if authorized and event.direction == AccessDirection.EXIT else "authorized") if authorized else "denied",
        reason=recovery["reason"], checks=recovery.get("checks", {}), timeline=[], notification={},
        duration_ms=recovery.get("duration_ms"), policy_version=POLICY_VERSION)
    recovery["binding"] = configuration_binding(person, config)
    recovery["attempt_id"] = str(attempt.id)
    event.raw_payload = {**(event.raw_payload or {}), "direction_resolution": {
        **((event.raw_payload or {}).get("direction_resolution") or {}), "missed_exit_recovery": deepcopy(recovery)}}
    add_stage(attempt, "decision", attempt.outcome, attempt.reason, at=now,
        details={"decision": event.decision.value, "direction": event.direction.value,
                 "camera_elapsed_ms": recovery.get("camera_elapsed_ms")})
    session.add(attempt)
    await session.flush()
    if authorized and event.direction == AccessDirection.ENTRY:
        from app.services.access_device_configuration import AccessDeviceConfiguration
        try:
            recovery["target_plan"] = await AccessDeviceConfiguration().preview_gate_open(session=session,
                require_admission=True, automatic_entry_policy=True)
        except (ValueError, LookupError):
            # Coordinator will retain a pre-attempt refusal; no provider is consulted.
            recovery["target_plan"] = None
        # A fresh nested value is essential after the initial flush: mutating an
        # aliased JSONB dictionary silently loses SQLAlchemy's change tracking.
        event.raw_payload = {**event.raw_payload, "direction_resolution": {
            **event.raw_payload["direction_resolution"], "missed_exit_recovery": deepcopy(recovery)}}
        await invalidate_owner_in_session(session, person.id, event=event)
    elif authorized:
        await invalidate_owner_in_session(session, person.id, event=event)
    elif recovery.get("approval_eligible"):
        await reserve_approval(session, attempt=attempt, person=person, config=config, now=now)
    return attempt


async def reserve_approval(session: AsyncSession, *, attempt: MissedExitRecoveryAttempt,
                           person: Person, config: Any, now: datetime) -> None:
    expires = attempt.occurred_at + timedelta(seconds=120)
    if expires <= now:
        attempt.reason, attempt.outcome = "approval_already_expired", "expired"
        return
    blocked = await blockers(session, now=now)
    if blocked:
        attempt.reason = blocked
        return
    try:
        await validate_person_configuration(session, person)
    except ValueError:
        attempt.reason = "resident_destination_invalid"
        return
    # Owner lock held by execution serializes the encounter and all owned vehicles.
    existing = await session.scalar(select(NotificationActionContext).where(
        NotificationActionContext.person_id == person.id, NotificationActionContext.action == ACTION,
        NotificationActionContext.consumed_at.is_(None), NotificationActionContext.expires_at > now)
        .order_by(NotificationActionContext.created_at).limit(1))
    if existing:
        if (existing.metadata_ or {}).get("vehicle_id") != str(attempt.vehicle_id):
            attempt.reason = "other_vehicle_request_pending"
            return
        attempt.action_context_id = existing.id
        attempt.notification = {"status": "coalesced", "expires_at": existing.expires_at.isoformat(),
                                "canonical_attempt_id": (existing.metadata_ or {}).get("attempt_id")}
        attempt.reason, attempt.outcome = "existing_resident_request", "coalesced"
        return
    if not config.gate_admission_device_key:
        attempt.reason = "admission_gate_not_configured"
        return
    from app.services.access_device_configuration import AccessDeviceConfiguration
    try:
        target_plan = await AccessDeviceConfiguration().preview_gate_open(session=session,
            target_device_key=config.gate_admission_device_key, require_admission=True, automatic_entry_policy=True)
    except (LookupError, ValueError):
        attempt.reason = "admission_target_unavailable"
        return
    identity = uuid.uuid4()
    context = NotificationActionContext(id=identity, token_hash=token_hash(token_for(identity)),
        action=ACTION, notify_service=person.home_assistant_mobile_app_notify_service,
        registration_number=attempt.registration_number, access_event_id=attempt.event_id,
        person_id=person.id, expires_at=expires, metadata_={"version": POLICY_VERSION,
            "binding": configuration_binding(person, config), "attempt_id": str(attempt.id),
            "vehicle_id": str(attempt.vehicle_id), "gate_key": config.gate_admission_device_key,
            "target_plan": target_plan})
    session.add(context)
    await session.flush()
    attempt.action_context_id = identity
    attempt.outcome = "awaiting_resident"
    attempt.notification = {"status": "queued", "expires_at": expires.isoformat(),
                            "delivery_id": str(uuid.uuid5(identity, "resident-approval-notification"))}
    message = (f"We couldn't verify your previous exit for {attempt.registration_number}. "
               "If you're waiting at the top gate, touch and hold this notification, then tap Allow entry. "
               "This request expires in two minutes after the plate read.")
    await reserve_mobile_output(session, attempt, person, config, kind="approval", message=message,
                                expires_at=expires, context_id=identity)
    add_stage(attempt, "notification", "queued", "resident_approval_requested", at=now)


async def reserve_mobile_output(session: AsyncSession, attempt: MissedExitRecoveryAttempt,
    person: Person, config: Any, *, kind: str, message: str, expires_at: datetime,
    context_id: uuid.UUID | None = None) -> None:
    identity = uuid.uuid5(context_id or attempt.id, "resident-approval-notification" if kind == "approval" else "resident-apology")
    action = {"type": "mobile", "target": person.home_assistant_mobile_app_notify_service,
              "title": "Allow entry" if kind == "approval" else "Entry confirmed",
              "message": message, "delivery_mode": "literal",
              "resident_recovery_output": {"attempt_id": str(attempt.id), "kind": kind,
                                           "context_id": str(context_id) if context_id else None}}
    payload = notification_context_payload(NotificationContext(event_type="resident_missed_exit_recovery",
        subject=attempt.registration_number, severity="info", facts={"message": message}))
    payload["resident_recovery_origin"] = {"attempt_id": str(attempt.id), "kind": kind,
        "context_id": str(context_id) if context_id else None, "expires_at": expires_at.isoformat(),
        "binding": configuration_binding(person, config), "notify_service": action["target"]}
    await NotificationRunStore().enqueue_prepared_in_session(session, payload, run_id=identity,
        plan=[{"rule": {"id": "resident-recovery-v1", "name": "Resident recovery"}, "action": action, "state": "pending"}])


async def _current_resident_authority(session: AsyncSession, context: NotificationActionContext,
                                      *, now: datetime, dispatch: bool = False) -> tuple[Person, Vehicle, Any]:
    """Current scoped policy. NOWAIT domain locks refuse contended mutation."""
    from app.models import LprIngestEvent, Schedule, ScheduleOverride
    from app.services.schedules import evaluate_vehicle_schedule
    await session.scalars(select(SystemSetting).where(SystemSetting.key.in_(
        [*SETTINGS_KEYS, "site_timezone", "schedule_default_policy"])).order_by(SystemSetting.key)
        .with_for_update(read=True, nowait=True).execution_options(populate_existing=True))
    config = await get_runtime_config_for_session(session)
    if not config.missed_exit_recovery_enabled or context.expires_at <= now:
        raise ValueError("approval_expired_or_disabled")
    metadata = context.metadata_ or {}
    person = await lock_owner(session, context.person_id, nowait=True)
    vehicle = await session.scalar(select(Vehicle).where(Vehicle.id == uuid.UUID(metadata["vehicle_id"]))
        .with_for_update(read=True, nowait=True).execution_options(populate_existing=True))
    if (person is None or not person.is_active or not person.missed_exit_recovery_enabled or
            vehicle is None or not vehicle.is_active or vehicle.person_id != person.id or
            vehicle.registration_number != context.registration_number or
            configuration_binding(person, config) != metadata.get("binding") or
            config.gate_admission_device_key != metadata.get("gate_key") or
            person.home_assistant_mobile_app_notify_service != context.notify_service):
        raise ValueError("resident_configuration_changed")
    matches = list((await session.scalars(select(Person.id).where(
        Person.home_assistant_mobile_app_notify_service == context.notify_service)
        .order_by(Person.id).with_for_update(read=True, nowait=True))).all())
    if matches != [person.id]:
        raise ValueError("resident_destination_not_unique")
    original = await session.get(AccessEvent, context.access_event_id)
    if original is None or original.decision != AccessDecision.DENIED or original.vehicle_id != vehicle.id:
        raise ValueError("original_blocked_read_unavailable")
    original_payload = original.raw_payload or {}
    if ("backfill" in original.source.casefold() or "backfill" in original_payload
            or original_payload.get("backfilled") or original_payload.get("skip_automation_actions")):
        raise ValueError("historical_origin_ineligible")
    if not original_payload.get("direction_resolution", {}).get("missed_exit_recovery", {}).get("approval_eligible"):
        raise ValueError("original_read_not_approval_eligible")
    provenance = await session.scalar(select(func.min(LprIngestEvent.received_at)).where(
        LprIngestEvent.access_event_id == original.id))
    if provenance is None or context.expires_at > min(provenance, original.occurred_at)+timedelta(seconds=120):
        raise ValueError("approval_provenance_invalid")
    changed = select(AccessEvent.id).where(AccessEvent.person_id == person.id,
        AccessEvent.decision == AccessDecision.GRANTED, AccessEvent.created_at > original.created_at)
    if metadata.get("recovery_event_id"):
        changed = changed.where(AccessEvent.id != uuid.UUID(metadata["recovery_event_id"]))
    if await session.scalar(changed.limit(1)):
        raise ValueError("resident_movement_changed")
    from app.services.access_device_configuration import AccessDeviceConfiguration
    plan = await AccessDeviceConfiguration().preview_gate_open(session=session,
        target_device_key=config.gate_admission_device_key, require_admission=True, automatic_entry_policy=True)
    if plan != metadata.get("target_plan"):
        raise ValueError("resident_gate_configuration_changed")
    schedule_ids = {s for s in (person.schedule_id, vehicle.schedule_id) if s}
    if schedule_ids:
        await session.scalars(select(Schedule).where(Schedule.id.in_(schedule_ids)).order_by(Schedule.id)
            .with_for_update(read=True, nowait=True))
    await session.scalars(select(ScheduleOverride).where(ScheduleOverride.person_id == person.id)
        .order_by(ScheduleOverride.id).with_for_update(read=True, nowait=True))
    vehicle = await session.scalar(select(Vehicle).where(Vehicle.id == vehicle.id)
        .options(selectinload(Vehicle.schedule), selectinload(Vehicle.owner).selectinload(Person.schedule))
        .execution_options(populate_existing=True))
    schedule = await evaluate_vehicle_schedule(session, vehicle, now,
        timezone_name=config.site_timezone, default_policy=config.schedule_default_policy)
    if not schedule.allowed:
        raise ValueError("current_schedule_denied")
    blocked = await blockers(session, now=now,
        exclude_saga_id=uuid.UUID(metadata["saga_id"]) if metadata.get("saga_id") else None,
        exclude_command_key=f"gate-command:resident-recovery:{context.id}" if dispatch else None)
    if blocked:
        raise ValueError(blocked)
    return person, vehicle, config


async def handle_resident_action(action_id: str, *, gate_intent_factory, execute_gate,
                                 finalize_admission, admission_input_factory) -> bool:
    """Opaque bearer capability delivered to one unlocked resident iPhone."""
    if not action_id.startswith(PREFIX):
        return False
    token = action_id[len(PREFIX):]
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        return True
    dispatch = None
    async with AsyncSessionLocal() as session:
        # Snapshot to locate owner; final checks reload every authority under locks.
        snapshot = await session.scalar(select(NotificationActionContext).where(
            NotificationActionContext.token_hash == token_hash(token), NotificationActionContext.action == ACTION))
        if snapshot is None:
            return True
        try:
            # Fixed domain lock order: settings -> owner -> vehicle -> context -> attempt.
            async with session.begin_nested():
                now = await session.scalar(select(func.clock_timestamp()))
                person, vehicle, config = await _current_resident_authority(session, snapshot, now=now)
                context = await session.get(NotificationActionContext, snapshot.id,
                    with_for_update={"nowait": True}, populate_existing=True)
                if context.consumed_at:
                    return True
                attempt = await session.get(MissedExitRecoveryAttempt, uuid.UUID(context.metadata_["attempt_id"]),
                    with_for_update={"nowait": True}, populate_existing=True)
                if attempt is None or attempt.outcome != "awaiting_resident":
                    raise ValueError("request_already_resolved")
                event_id = uuid.uuid5(context.id, "resident-recovery-event")
                saga_id = uuid.uuid5(context.id, "resident-recovery-saga")
                original = await session.get(AccessEvent, context.access_event_id)
                recovery = {"attempt_id": str(attempt.id), "context_id": str(context.id),
                    "original_event_id": str(original.id), "authority": "resident_confirmation",
                    "prior_exit_unverified": True}
                event = AccessEvent(id=event_id, vehicle_id=vehicle.id, person_id=person.id,
                    registration_number=vehicle.registration_number, direction=AccessDirection.ENTRY,
                    decision=AccessDecision.GRANTED, confidence=original.confidence,
                    source="resident_missed_exit_recovery", occurred_at=original.occurred_at,
                    timing_classification=TimingClassification.UNKNOWN,
                    raw_payload={"resident_recovery": recovery, "direction_resolution": {
                        "reason": "resident_confirmed_entry", "missed_exit_recovery": {
                            "method": "resident_confirmation", "reason": "resident_confirmed_entry"}}})
                saga = MovementSagaRecord(id=saga_id, idempotency_key=f"resident-recovery:{context.id}",
                    source=event.source, occurred_at=event.occurred_at, access_event_id=event.id,
                    person_id=person.id, vehicle_id=vehicle.id, registration_number=vehicle.registration_number,
                    direction=AccessDirection.ENTRY, decision=AccessDecision.GRANTED,
                    state=MovementSagaState.DIRECTION_RESOLVED, gate_command_required=True,
                    intent_payload={"allowed": True, "resident_recovery": recovery},
                    decision_payload={"reason": "resident_confirmed_entry", "resident_recovery": recovery})
                session.add(event)
                await session.flush()
                session.add(saga)
                await session.flush()
                context.consumed_at, context.outcome = now, "authorized"
                context.metadata_ = {**context.metadata_, "recovery_event_id": str(event_id), "saga_id": str(saga_id)}
                attempt.recovery_event_id, attempt.saga_id = event_id, saga_id
                attempt.method, attempt.outcome, attempt.reason = "resident_confirmation", "authorized", "resident_confirmed_entry"
                attempt.notification = {**attempt.notification, "action_at": now.isoformat()}
                add_stage(attempt, "resident_action", "authorized", "resident_confirmed_entry", at=now)
                await invalidate_owner_in_session(session, person.id, event=event)
                from app.modules.lpr.base import PlateRead
                from app.services.access.delivery import reserve_observation_outputs
                original_best = (original.raw_payload or {}).get("best") or {}
                linked_read = PlateRead(vehicle.registration_number, original.confidence, event.source,
                    original.occurred_at, {**original_best, "direction": "entry", "resident_recovery": recovery})
                session_input = admission_input_factory((linked_read,), original.occurred_at, now, linked_read, config)
                await finalize_admission(session, saga_id=saga_id, session_input=session_input)
                await reserve_observation_outputs(session, event=event, person=person, vehicle=vehicle,
                    visitor_pass=None, visitor_pass_mode=None, anomalies=[])
                from app.services.telemetry import TELEMETRY_CATEGORY_ACCESS, write_audit_log
                await write_audit_log(session, category=TELEMETRY_CATEGORY_ACCESS,
                    action=ACTION, actor=person.display_name, target_entity="AccessEvent", target_id=event.id,
                    outcome="authorized", metadata={"person_id": str(person.id), "original_event_id": str(original.id),
                         "attempt_id": str(attempt.id), "authority": "resident_confirmation"})
                dispatch = (context.id, event.id, saga.id, context.expires_at, person.display_name,
                            vehicle.registration_number, config.gate_admission_device_key, context.metadata_["target_plan"])
            await session.commit()
        except Exception as exc:  # noqa: BLE001 - fence scoped capability on any unexpected domain failure.
            # NOWAIT contention is a deferred tap, not a spent/revoked capability.
            if isinstance(exc, DBAPIError) and getattr(exc.orig, "sqlstate", None) == "55P03":
                return True
            # Rollback nested domain work; record only bounded reason codes, never a token/error payload.
            reason = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[a-z_]{1,120}", str(exc)) else "current_authority_unavailable"
            context = await session.get(NotificationActionContext, snapshot.id, with_for_update=True, populate_existing=True)
            if context and not context.consumed_at:
                context.consumed_at, context.outcome, context.outcome_detail = datetime.now(UTC), "revoked", reason
                attempt = await session.get(MissedExitRecoveryAttempt, uuid.UUID(context.metadata_["attempt_id"]), with_for_update=True)
                if attempt:
                    attempt.outcome, attempt.reason = "expired" if reason == "approval_expired_or_disabled" else "revoked", reason
                    add_stage(attempt, "resident_action", attempt.outcome, reason)
                await session.commit()
            return True
    if dispatch:
        context_id, event_id, saga_id, expires, name, plate, gate_key, target_plan = dispatch
        async def authorize_dispatch(session: AsyncSession) -> None:
            await authorize_recovery_dispatch(session, context_id=context_id, event_id=event_id)
        outcome = await execute_gate(gate_intent_factory(
            reason=f"Resident confirmed entry for {plate} ({name}); prior exit unverified",
            source="resident_missed_exit_recovery", actor=name, registration_number=plate,
            event_id=str(event_id), movement_saga_id=str(saga_id), target_device_key=gate_key, target_plan=target_plan,
            idempotency_key=f"gate-command:resident-recovery:{context_id}",
            intent_id=str(uuid.uuid5(context_id, "resident-gate-open")), expires_at=expires,
            automatic_entry_policy=True, authorize_dispatch=authorize_dispatch,
            metadata={"resident_context_id": str(context_id), "person_id": str(snapshot.person_id),
                      "authority": "resident_confirmation"}))
        async with AsyncSessionLocal() as session:
            await finalize_admission(session, saga_id=saga_id, gate_command_id=outcome.command_id)
            await session.commit()
    return True


async def authorize_recovery_dispatch(session: AsyncSession, *, context_id: uuid.UUID,
                                      event_id: uuid.UUID) -> None:
    try:
        async with session.begin_nested():
            await _authorize_recovery_dispatch_facts(session, context_id=context_id, event_id=event_id)
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            raise ValueError("resident_authority_being_changed") from exc
        raise


async def _authorize_recovery_dispatch_facts(session: AsyncSession, *, context_id: uuid.UUID,
                                            event_id: uuid.UUID) -> None:
    context = await session.get(NotificationActionContext, context_id)
    if context is None:
        raise ValueError("resident_context_missing")
    now = await session.scalar(select(func.clock_timestamp()))
    await _current_resident_authority(session, context, now=now, dispatch=True)
    context = await session.get(NotificationActionContext, context_id,
        with_for_update={"nowait": True}, populate_existing=True)
    if context.action != ACTION or context.outcome != "authorized" or context.metadata_.get("recovery_event_id") != str(event_id):
        raise ValueError("resident_dispatch_revoked")


async def authorize_mobile_output(session: AsyncSession, payload: dict, run_id: uuid.UUID, action: dict | None) -> str | None:
    origin = payload.get("resident_recovery_origin") or {}
    try:
        attempt_id = uuid.UUID(origin["attempt_id"])
        attempt = await session.get(MissedExitRecoveryAttempt, attempt_id)
        now = await session.scalar(select(func.clock_timestamp()))
        expires_at = parse_time(origin.get("expires_at"))
        if attempt is None or expires_at is None or expires_at <= now:
            return "recovery_output_expired"
        person = await lock_owner(session, attempt.owner_id, nowait=True)
        config = await get_runtime_config_for_session(session)
        if (person is None or not person.is_active or not person.missed_exit_recovery_enabled
            or not config.missed_exit_recovery_enabled or configuration_binding(person, config) != origin.get("binding")):
            return "recovery_output_revoked"
        if not action or action.get("target") != person.home_assistant_mobile_app_notify_service or action.get("target") != origin.get("notify_service"):
            return "recovery_output_destination_changed"
        descriptor = action.get("resident_recovery_output") or {}
        if descriptor != {"attempt_id": origin["attempt_id"], "kind": origin["kind"], "context_id": origin.get("context_id")}:
            return "recovery_output_origin_invalid"
        if origin["kind"] == "approval":
            context = await session.get(NotificationActionContext, uuid.UUID(origin["context_id"]))
            if context is None or context.consumed_at or context.expires_at <= now or attempt.outcome != "awaiting_resident":
                return "recovery_request_resolved"
            await _current_resident_authority(session, context, now=now)
            expected = uuid.uuid5(context.id, "resident-approval-notification")
        elif origin["kind"] == "apology" and attempt.outcome == "verified":
            expected = uuid.uuid5(attempt.id, "resident-apology")
        else:
            return "recovery_output_not_verified"
        if expected != run_id:
            return "recovery_output_identity_invalid"
    except (ValueError, TypeError, KeyError):
        return "recovery_output_origin_invalid"
    return None


async def resolve_mobile_actions(action: dict) -> list[dict] | None:
    descriptor = action.get("resident_recovery_output") or {}
    if descriptor.get("kind") != "approval":
        return None
    try:
        identity = uuid.UUID(descriptor["context_id"])
    except (ValueError, KeyError, TypeError):
        return None
    return [{"action": PREFIX+token_for(identity), "title": "Allow entry", "authenticationRequired": True}]


async def record_admission_in_session(session: AsyncSession, *, event: AccessEvent,
    saga: MovementSagaRecord, status: str, receipt: dict | None) -> None:
    attempt = await session.scalar(select(MissedExitRecoveryAttempt).where(or_(
        MissedExitRecoveryAttempt.event_id == event.id, MissedExitRecoveryAttempt.recovery_event_id == event.id))
        .with_for_update(nowait=True).execution_options(populate_existing=True))
    if attempt is None:
        return
    now = await session.scalar(select(func.clock_timestamp()))
    if receipt:
        if receipt.get("command_id"):
            attempt.command_id = uuid.UUID(str(receipt["command_id"]))
        next_outcome = "verified" if status == "verified" else "pending_verification" if receipt.get("requires_reconciliation") else "rejected"
        if attempt.outcome != next_outcome:
            attempt.outcome = next_outcome
            add_stage(attempt, "admission", next_outcome, "gate_admission_"+next_outcome, at=now,
                details={"delivery": receipt.get("delivery"), "admission_verified": status == "verified",
                         "requires_reconciliation": bool(receipt.get("requires_reconciliation"))})
    if status != "verified":
        return
    # Deterministic outbox ID survives repeated finalization and reconciliation.
    person = await session.get(Person, attempt.owner_id)
    config = await get_runtime_config_for_session(session)
    apology_id = uuid.uuid5(attempt.id, "resident-apology")
    if person and await session.get(NotificationRun, apology_id) is None:
        await reserve_mobile_output(session, attempt, person, config, kind="apology",
            message="Apologies for the slower than usual entry. Additional checks were needed because your previous exit wasn't verified.",
            expires_at=now+timedelta(minutes=5))
        attempt.notification = {**attempt.notification, "apology_delivery_id": str(apology_id)}
        add_stage(attempt, "apology", "queued", "verified_entry_apology", at=now)


async def authorize_automatic_recovery_dispatch(session: AsyncSession, *, event_id: uuid.UUID) -> None:
    try:
        async with session.begin_nested():
            await _authorize_automatic_recovery_facts(session, event_id=event_id)
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            raise ValueError("recovery_authority_being_changed") from exc
        raise


async def _authorize_automatic_recovery_facts(session: AsyncSession, *, event_id: uuid.UUID) -> None:
    event = await session.get(AccessEvent, event_id, populate_existing=True)
    recovery = ((event.raw_payload or {}).get("direction_resolution") or {}).get("missed_exit_recovery") if event else None
    if not recovery:
        return
    now = await session.scalar(select(func.clock_timestamp()))
    await session.scalars(select(SystemSetting).where(SystemSetting.key.in_(SETTINGS_KEYS))
        .order_by(SystemSetting.key).with_for_update(read=True, nowait=True))
    person = await lock_owner(session, event.person_id, nowait=True)
    config = await get_runtime_config_for_session(session)
    if not recovery.get("target_plan"):
        raise ValueError("recovery_target_unavailable")
    if (person is None or not person.missed_exit_recovery_enabled or not config.missed_exit_recovery_enabled
            or configuration_binding(person, config) != recovery.get("binding")):
        raise ValueError("recovery_dispatch_configuration_changed")
    own_saga_id = await session.scalar(select(MovementSagaRecord.id).where(MovementSagaRecord.access_event_id == event.id))
    blocked = await blockers(session, now=now, exclude_saga_id=own_saga_id,
                             exclude_command_key=f"gate-command:open:default:event:{event.id}")
    if blocked:
        raise ValueError(blocked)
    if recovery.get("method") == "phone_automatic":
        row = await session.get(ResidentRecoveryJourney, person.id, with_for_update={"nowait": True})
        if (not _connected or row is None or row.epoch != _epoch or row.claimed_event_id != event.id
                or row.latest_at is None or not 0 <= (now-row.latest_at).total_seconds() <= 60):
            raise ValueError("recovery_phone_authority_stale")
