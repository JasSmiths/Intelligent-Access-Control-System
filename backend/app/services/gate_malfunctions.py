import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import (
    AccessEvent,
    GateMalfunctionState,
    GateMalfunctionNotificationOutbox,
    GateMalfunctionTimelineEvent,
    GateStateObservation,
    Person,
    TelemetrySpan,
    TelemetryTrace,
    Vehicle,
)
from app.models.enums import AccessDirection, GateMalfunctionStatus
from app.modules.gate.base import GateState
from app.modules.notifications.base import NotificationContext
from app.modules.registry import UnsupportedModuleError, get_gate_controller
from app.services.event_bus import RealtimeEvent, event_bus
from app.services.home_assistant import get_home_assistant_service
from app.services.maintenance import is_maintenance_mode_active
from app.services.notifications import get_notification_service
from app.services.telemetry import (
    TELEMETRY_CATEGORY_GATE_MALFUNCTION,
    sanitize_payload,
    span_id,
    trace_id,
)

logger = get_logger(__name__)

MALFUNCTION_TRIGGER_SECONDS = 5 * 60
ATTEMPT_OFFSETS_SECONDS = {
    1: 5 * 60,
    2: 5 * 60 + 45,
    3: 10 * 60 + 45,
    4: 70 * 60 + 45,
    5: 190 * 60 + 45,
}
MILESTONE_TRIGGERS = [
    (30 * 60, "gate_malfunction_30m", "Gate malfunction open for 30 minutes", "warning"),
    (60 * 60, "gate_malfunction_60m", "Gate malfunction open for 60 minutes", "critical"),
    (120 * 60, "gate_malfunction_2hrs", "Gate malfunction open for 2 hours", "critical"),
]
UNSAFE_GATE_STATES = {GateState.OPEN, GateState.OPENING, GateState.CLOSING}
UNRESOLVED_STATUSES = {GateMalfunctionStatus.ACTIVE, GateMalfunctionStatus.FUBAR}
NOTIFICATION_TERMINAL_STATUSES = {"sent", "skipped"}
NOTIFICATION_RETRY_SECONDS = [60, 5 * 60, 15 * 60]
NOTIFICATION_SENDING_STALE_SECONDS = 5 * 60
ATTEMPT_CLAIM_STALE_SECONDS = 5 * 60


@dataclass(frozen=True)
class GateSnapshot:
    entity_id: str
    name: str
    state: GateState
    state_changed_at: datetime | None
    observed_at: datetime

    @property
    def unsafe_open(self) -> bool:
        return self.state in UNSAFE_GATE_STATES


@dataclass(frozen=True)
class GateMalfunctionReadContext:
    id: uuid.UUID
    gate_entity_id: str
    gate_name: str | None
    status: GateMalfunctionStatus
    opened_at: datetime
    declared_at: datetime
    resolved_at: datetime | None
    last_gate_state: str | None

    def as_payload(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "gate_entity_id": self.gate_entity_id,
            "gate_name": self.gate_name,
            "status": self.status.value,
            "opened_at": self.opened_at.isoformat(),
            "declared_at": self.declared_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "last_gate_state": self.last_gate_state,
        }


async def active_stuck_open_malfunction_at(
    session: AsyncSession,
    *,
    observed_at: datetime,
    gate_state: GateState | str | None = None,
    gate_entity_id: str | None = None,
) -> GateMalfunctionReadContext | None:
    state = _coerce_gate_state_value(gate_state)
    if state not in UNSAFE_GATE_STATES:
        return None
    observed = observed_at if observed_at.tzinfo else observed_at.replace(tzinfo=UTC)
    query = select(GateMalfunctionState).where(
        GateMalfunctionState.opened_at <= observed,
        GateMalfunctionState.declared_at <= observed,
        or_(
            GateMalfunctionState.status.in_(list(UNRESOLVED_STATUSES)),
            GateMalfunctionState.resolved_at >= observed,
        ),
    )
    if gate_entity_id:
        query = query.where(GateMalfunctionState.gate_entity_id == gate_entity_id)
    query = query.order_by(
        GateMalfunctionState.opened_at.desc(),
        GateMalfunctionState.declared_at.desc(),
    ).limit(1)
    row = await session.scalar(query)
    if not row:
        return None
    return GateMalfunctionReadContext(
        id=row.id,
        gate_entity_id=row.gate_entity_id,
        gate_name=row.gate_name,
        status=row.status,
        opened_at=row.opened_at,
        declared_at=row.declared_at,
        resolved_at=row.resolved_at,
        last_gate_state=row.last_gate_state,
    )


def _coerce_gate_state_value(value: Any) -> GateState:
    if isinstance(value, GateState):
        return value
    try:
        return GateState(str(value or "").lower())
    except ValueError:
        return GateState.UNKNOWN


class GateMalfunctionService:
    """Persistent state machine for stuck-open gate recovery."""

    def __init__(self, poll_interval_seconds: float = 15.0) -> None:
        self._poll_interval_seconds = poll_interval_seconds
        self._worker: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._tick_lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        if self._worker and not self._worker.done():
            return
        self._stop_event.clear()
        event_bus.subscribe(self._handle_realtime_event)
        self._worker = asyncio.create_task(self._run(), name="gate-malfunction-scheduler")
        self._started = True
        logger.info("gate_malfunction_service_started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._worker:
            await self._worker
        if self._started:
            event_bus.unsubscribe(self._handle_realtime_event)
        self._worker = None
        self._started = False
        logger.info("gate_malfunction_service_stopped")

    async def active(self, *, include_timeline: bool = False) -> list[dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.scalars(
                    select(GateMalfunctionState)
                    .options(selectinload(GateMalfunctionState.timeline_events))
                    .where(GateMalfunctionState.status.in_(list(UNRESOLVED_STATUSES)))
                    .order_by(GateMalfunctionState.opened_at.desc())
                )
            ).all()
            return [await self._serialize_malfunction(session, row, include_timeline=include_timeline) for row in rows]

    async def history(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        include_timeline: bool = False,
    ) -> list[dict[str, Any]]:
        page = await self.history_page(
            status=status,
            limit=limit,
            include_timeline=include_timeline,
        )
        return page["items"]

    async def history_page(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        include_timeline: bool = False,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        bounded_limit = max(1, min(limit, 100))
        async with AsyncSessionLocal() as session:
            query = (
                select(GateMalfunctionState)
                .options(selectinload(GateMalfunctionState.timeline_events))
                .order_by(GateMalfunctionState.opened_at.desc(), GateMalfunctionState.id.desc())
            )
            if status:
                try:
                    query = query.where(GateMalfunctionState.status == GateMalfunctionStatus(status))
                except ValueError:
                    return {"items": [], "next_cursor": None}
            cursor_opened_at, cursor_id = self._parse_history_cursor(cursor)
            if cursor_opened_at and cursor_id:
                query = query.where(
                    or_(
                        GateMalfunctionState.opened_at < cursor_opened_at,
                        (
                            (GateMalfunctionState.opened_at == cursor_opened_at)
                            & (GateMalfunctionState.id < cursor_id)
                        ),
                    )
                )
            rows = (await session.scalars(query.limit(bounded_limit + 1))).all()
            items = rows[:bounded_limit]
            next_cursor = self._history_cursor(items[-1]) if len(rows) > bounded_limit and items else None
            return {
                "items": [
                    await self._serialize_malfunction(session, row, include_timeline=include_timeline)
                    for row in items
                ],
                "next_cursor": next_cursor,
            }

    async def trace(self, malfunction_id: uuid.UUID | str) -> dict[str, Any] | None:
        row_id = self._coerce_uuid(malfunction_id)
        if row_id is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.get(
                GateMalfunctionState,
                row_id,
                options=[selectinload(GateMalfunctionState.timeline_events)],
            )
            if not row:
                return None
            return await self._serialize_malfunction(session, row, include_timeline=True)

    async def override(
        self,
        malfunction_id: uuid.UUID | str,
        *,
        action: str,
        reason: str,
        actor: str,
        confirm: bool,
    ) -> dict[str, Any]:
        normalized_action = action.strip().lower()
        if normalized_action not in {"recheck_live_state", "run_attempt_now", "mark_resolved", "mark_fubar"}:
            return {"changed": False, "error": "Unsupported malfunction override action."}
        if not confirm:
            return {
                "changed": False,
                "requires_confirmation": True,
                "confirmation_field": "confirm",
                "detail": f"Confirm {normalized_action.replace('_', ' ')} for this gate malfunction.",
                "action": normalized_action,
                "malfunction_id": str(malfunction_id),
                "target": str(malfunction_id),
            }

        row_id = self._coerce_uuid(malfunction_id)
        if row_id is None:
            return {"changed": False, "error": "Invalid malfunction ID."}

        if normalized_action == "recheck_live_state":
            snapshot = await self._current_gate_snapshot(refresh=True)
            if snapshot.state == GateState.CLOSED:
                await self._resolve_for_closed_gate(snapshot, reason=f"Manual recheck by {actor}: {reason}")
            return {
                "changed": snapshot.state == GateState.CLOSED,
                "state": snapshot.state.value,
                "gate_entity_id": snapshot.entity_id,
            }

        if normalized_action == "run_attempt_now":
            return await self._execute_attempt_for_id(row_id, actor=actor, reason=reason, manual=True)

        queue_fubar_notification = False
        async with AsyncSessionLocal() as session:
            row = await session.scalar(
                select(GateMalfunctionState)
                .where(GateMalfunctionState.id == row_id)
                .with_for_update()
            )
            if not row:
                return {"changed": False, "error": "Gate malfunction not found."}
            now = datetime.now(tz=UTC)
            if normalized_action == "mark_resolved":
                if row.status == GateMalfunctionStatus.RESOLVED:
                    payload = await self._serialize_malfunction(session, row, include_timeline=True)
                    return {"changed": False, "malfunction": payload}
                await self._mark_resolved(
                    session,
                    row,
                    now,
                    reason=f"Manual override by {actor}: {reason}",
                    snapshot=None,
                )
            else:
                if row.status != GateMalfunctionStatus.ACTIVE:
                    return {
                        "changed": False,
                        "error": "Only active malfunctions can be manually marked FUBAR.",
                    }
                await self._mark_fubar(
                    session,
                    row,
                    now,
                    reason=f"Manual override by {actor}: {reason}",
                )
                queue_fubar_notification = True
            await session.commit()
            payload = await self._serialize_malfunction(session, row, include_timeline=True)

        await event_bus.publish("gate_malfunction.updated", payload)
        if queue_fubar_notification:
            await self._queue_notification(
                row_id,
                "gate_malfunction_fubar",
                "Gate malfunction is FUBAR",
                "critical",
                now,
            )
        return {"changed": True, "malfunction": payload}

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.evaluate_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("gate_malfunction_tick_failed")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def evaluate_once(self) -> None:
        async with self._tick_lock:
            now = datetime.now(tz=UTC)
            snapshot = await self._current_gate_snapshot(refresh=True)
            if snapshot.state == GateState.CLOSED:
                await self._resolve_for_closed_gate(snapshot, reason="Gate is closed.")

            maintenance_active = await is_maintenance_mode_active()
            if not maintenance_active and snapshot.unsafe_open:
                await self._declare_if_needed(snapshot, now)

            if not maintenance_active:
                await self._execute_due_attempts(now)
                await self._send_due_milestones(now)
                await self._process_due_notifications(now)

    async def _handle_realtime_event(self, event: RealtimeEvent) -> None:
        if event.type == "gate.state_changed":
            task = asyncio.create_task(self.evaluate_once(), name="gate-malfunction-realtime-evaluation")
            task.add_done_callback(_log_background_task_error)
            return
        if event.type not in {"notification.sent", "notification.failed", "notification.skipped"}:
            return
        malfunction_id = event.payload.get("malfunction_id")
        if not malfunction_id:
            return
        await self._record_notification_dispatch(event.type, event.payload)

    async def _current_gate_snapshot(self, *, refresh: bool) -> GateSnapshot:
        observed_at = datetime.now(tz=UTC)
        status: dict[str, Any] = {}
        try:
            status = await get_home_assistant_service().status(refresh=refresh)
        except Exception as exc:
            logger.warning("gate_malfunction_ha_status_failed", extra={"error": str(exc)})

        gates = status.get("gate_entities") if isinstance(status.get("gate_entities"), list) else []
        primary = gates[0] if gates and isinstance(gates[0], dict) else {}
        entity_id = str(primary.get("entity_id") or status.get("gate_entity_id") or settings.home_assistant_gate_entity_id or "primary_gate")
        name = str(primary.get("name") or entity_id)
        raw_state = str(status.get("current_gate_state") or primary.get("state") or "")
        changed_at = self._parse_datetime(
            status.get("current_gate_state_changed_at")
            or primary.get("state_changed_at")
            or primary.get("last_changed")
        )
        state = self._coerce_gate_state(raw_state)

        if state == GateState.UNKNOWN:
            try:
                state = self._coerce_gate_state(await get_gate_controller(settings.gate_controller).current_state())
            except UnsupportedModuleError as exc:
                logger.warning("gate_malfunction_controller_unavailable", extra={"error": str(exc)})
            except Exception as exc:
                logger.warning("gate_malfunction_current_state_failed", extra={"error": str(exc)})

        return GateSnapshot(
            entity_id=entity_id,
            name=name,
            state=state,
            state_changed_at=changed_at,
            observed_at=observed_at,
        )

    async def _declare_if_needed(self, snapshot: GateSnapshot, now: datetime) -> None:
        opened_at = snapshot.state_changed_at or now
        opened_long_enough = opened_at <= now - timedelta(seconds=MALFUNCTION_TRIGGER_SECONDS)

        async with AsyncSessionLocal() as session:
            existing = await session.scalar(
                select(GateMalfunctionState)
                .where(
                    GateMalfunctionState.gate_entity_id == snapshot.entity_id,
                    GateMalfunctionState.status.in_(list(UNRESOLVED_STATUSES)),
                )
                .order_by(GateMalfunctionState.opened_at.desc())
            )
            resolved_payload: dict[str, Any] | None = None
            if existing:
                reopened_at = await self._reopened_after_existing(session, existing, snapshot)
                if reopened_at:
                    await self._mark_resolved(
                        session,
                        existing,
                        reopened_at,
                        reason="Gate closed and a new unsafe-open episode began.",
                        snapshot=None,
                    )
                    await session.commit()
                    resolved_payload = await self._serialize_malfunction(
                        session,
                        existing,
                        include_timeline=True,
                    )
                    if not opened_long_enough:
                        await event_bus.publish("gate_malfunction.resolved", resolved_payload)
                        await event_bus.publish("gate_malfunction.updated", resolved_payload)
                        return
                else:
                    existing.last_gate_state = snapshot.state.value
                    existing.last_checked_at = now
                    await session.commit()
                    return

            if not opened_long_enough:
                return

            if existing and resolved_payload is not None:
                await event_bus.publish("gate_malfunction.resolved", resolved_payload)
                await event_bus.publish("gate_malfunction.updated", resolved_payload)

            duplicate = await session.scalar(
                select(GateMalfunctionState.id).where(
                    GateMalfunctionState.gate_entity_id == snapshot.entity_id,
                    GateMalfunctionState.status.in_(list(UNRESOLVED_STATUSES)),
                )
            )
            if duplicate:
                return

            last_event = await self._last_known_vehicle_event(session, opened_at)
            row = GateMalfunctionState(
                gate_entity_id=snapshot.entity_id,
                gate_name=snapshot.name,
                status=GateMalfunctionStatus.ACTIVE,
                opened_at=opened_at,
                declared_at=now,
                fix_attempts_count=0,
                next_attempt_scheduled_at=opened_at + timedelta(seconds=ATTEMPT_OFFSETS_SECONDS[1]),
                last_known_vehicle_event_id=last_event.id if last_event else None,
                telemetry_trace_id=trace_id(),
                last_gate_state=snapshot.state.value,
                last_checked_at=now,
            )
            session.add(row)
            await session.flush()
            await self._create_trace(session, row, snapshot)
            if last_event:
                await self._add_timeline_event(
                    session,
                    row,
                    kind="preceding_event",
                    occurred_at=last_event.occurred_at,
                    title=await self._access_event_label(session, last_event),
                    details=self._access_event_details(last_event),
                )
            await self._add_timeline_event(
                session,
                row,
                kind="declared",
                occurred_at=now,
                title="Malfunction declared",
                details={
                    "gate_state": snapshot.state.value,
                    "opened_at": opened_at.isoformat(),
                    "next_attempt_scheduled_at": row.next_attempt_scheduled_at.isoformat(),
                },
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return
            payload = await self._serialize_malfunction(session, row, include_timeline=True)
            row_id = row.id

        await event_bus.publish("gate_malfunction.declared", payload)
        await event_bus.publish("gate_malfunction.updated", payload)
        await self._queue_notification(
            row_id,
            "gate_malfunction_initial",
            "Gate malfunction detected",
            "warning",
            now,
        )

    async def _reopened_after_existing(
        self,
        session,
        existing: GateMalfunctionState,
        snapshot: GateSnapshot,
    ) -> datetime | None:
        closed_observation = await session.scalar(
            select(GateStateObservation)
            .where(
                GateStateObservation.gate_entity_id == existing.gate_entity_id,
                GateStateObservation.state == GateState.CLOSED.value,
                GateStateObservation.observed_at > existing.opened_at,
                GateStateObservation.observed_at <= snapshot.observed_at,
            )
            .order_by(GateStateObservation.observed_at.desc())
            .limit(1)
        )
        if closed_observation:
            return closed_observation.state_changed_at or closed_observation.observed_at

        current_transition = None
        if snapshot.state_changed_at:
            current_transition = await session.scalar(
                select(GateStateObservation)
                .where(
                    GateStateObservation.gate_entity_id == existing.gate_entity_id,
                    GateStateObservation.state == snapshot.state.value,
                    GateStateObservation.state_changed_at == snapshot.state_changed_at,
                )
                .order_by(GateStateObservation.observed_at.desc())
                .limit(1)
            )
            if (
                current_transition
                and current_transition.previous_state == GateState.CLOSED.value
                and snapshot.state_changed_at > existing.opened_at
            ):
                return snapshot.state_changed_at

        if (
            snapshot.state_changed_at
            and existing.last_checked_at
            and snapshot.state_changed_at > existing.opened_at
            and snapshot.state_changed_at > existing.last_checked_at
            and (current_transition is None or current_transition.previous_state is None)
        ):
            return snapshot.state_changed_at
        return None

    async def _execute_due_attempts(self, now: datetime) -> None:
        stale_claim_cutoff = now - timedelta(seconds=ATTEMPT_CLAIM_STALE_SECONDS)
        async with AsyncSessionLocal() as session:
            rows = (
                await session.scalars(
                    select(GateMalfunctionState)
                    .where(
                        GateMalfunctionState.status == GateMalfunctionStatus.ACTIVE,
                        GateMalfunctionState.next_attempt_scheduled_at.is_not(None),
                        GateMalfunctionState.next_attempt_scheduled_at <= now,
                        or_(
                            GateMalfunctionState.attempt_claim_token.is_(None),
                            GateMalfunctionState.attempt_claimed_at.is_(None),
                            GateMalfunctionState.attempt_claimed_at <= stale_claim_cutoff,
                        ),
                    )
                    .order_by(GateMalfunctionState.next_attempt_scheduled_at, GateMalfunctionState.opened_at)
                )
            ).all()
        for row in rows:
            await self._execute_attempt_for_id(
                row.id,
                actor="System",
                reason="Scheduled gate malfunction recovery",
                require_due_at=now,
            )

    async def _execute_attempt_for_id(
        self,
        malfunction_id: uuid.UUID,
        *,
        actor: str,
        reason: str,
        manual: bool = False,
        require_due_at: datetime | None = None,
    ) -> dict[str, Any]:
        claim = await self._claim_attempt(
            malfunction_id,
            actor=actor,
            manual=manual,
            require_due_at=require_due_at,
        )
        if not claim.get("claimed"):
            return claim

        claim_token = str(claim["claim_token"])
        snapshot = await self._current_gate_snapshot(refresh=True)
        if snapshot.state == GateState.CLOSED:
            return await self._finalize_claimed_resolution(
                malfunction_id,
                claim_token=claim_token,
                snapshot=snapshot,
                reason="Gate closed before recovery attempt.",
            )
        if await is_maintenance_mode_active():
            return await self._release_attempt_claim(
                malfunction_id,
                claim_token=claim_token,
                detail="Maintenance Mode became active before the recovery command was sent.",
            )

        attempt_number = int(claim["attempt_number"])
        gate_label = str(claim.get("gate_name") or claim.get("gate_entity_id") or "gate")
        command_reason = f"{reason}: attempt {attempt_number} for {gate_label}"
        try:
            result = await get_gate_controller(settings.gate_controller).open_gate(
                command_reason,
                bypass_schedule=True,
            )
            accepted = result.accepted
            detail = result.detail or command_reason
            state = result.state.value
        except Exception as exc:
            accepted = False
            detail = str(exc)
            state = GateState.FAULT.value

        after_attempt = (
            await self._current_gate_snapshot(refresh=True)
            if attempt_number >= max(ATTEMPT_OFFSETS_SECONDS)
            else None
        )
        return await self._finalize_claimed_attempt(
            malfunction_id,
            claim_token=claim_token,
            actor=actor,
            manual=manual,
            accepted=accepted,
            detail=detail,
            state=state,
            after_attempt=after_attempt,
        )

    async def _claim_attempt(
        self,
        malfunction_id: uuid.UUID,
        *,
        actor: str,
        manual: bool,
        require_due_at: datetime | None,
    ) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            row = await session.scalar(
                select(GateMalfunctionState)
                .where(GateMalfunctionState.id == malfunction_id)
                .with_for_update()
            )
            if not row:
                return {"changed": False, "error": "Gate malfunction not found."}
            now = datetime.now(tz=UTC)
            if row.status != GateMalfunctionStatus.ACTIVE:
                payload = await self._serialize_malfunction(session, row, include_timeline=True)
                return {"changed": False, "malfunction": payload, "detail": "Malfunction is not active."}
            if require_due_at and (
                row.next_attempt_scheduled_at is None or row.next_attempt_scheduled_at > require_due_at
            ):
                payload = await self._serialize_malfunction(session, row, include_timeline=True)
                return {"changed": False, "malfunction": payload, "detail": "Recovery attempt is no longer due."}
            if await is_maintenance_mode_active():
                payload = await self._serialize_malfunction(session, row, include_timeline=True)
                return {
                    "changed": False,
                    "malfunction": payload,
                    "paused": True,
                    "detail": "Maintenance Mode is active; recovery attempts are paused.",
                }
            if row.attempt_claim_token and row.attempt_claimed_at and (
                now - row.attempt_claimed_at
            ).total_seconds() < ATTEMPT_CLAIM_STALE_SECONDS:
                payload = await self._serialize_malfunction(session, row, include_timeline=True)
                return {
                    "changed": False,
                    "malfunction": payload,
                    "claimed": False,
                    "detail": "Recovery attempt is already claimed.",
                }
            claim_token = uuid.uuid4().hex
            attempt_number = row.fix_attempts_count + 1
            row.attempt_claim_token = claim_token
            row.attempt_claimed_at = now
            row.last_checked_at = now
            await session.commit()
            return {
                "claimed": True,
                "changed": False,
                "claim_token": claim_token,
                "attempt_number": attempt_number,
                "gate_entity_id": row.gate_entity_id,
                "gate_name": row.gate_name,
                "manual": manual,
                "actor": actor,
            }

    async def _release_attempt_claim(
        self,
        malfunction_id: uuid.UUID,
        *,
        claim_token: str,
        detail: str,
    ) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            row = await session.scalar(
                select(GateMalfunctionState)
                .where(GateMalfunctionState.id == malfunction_id)
                .with_for_update()
            )
            if not row:
                return {"changed": False, "error": "Gate malfunction not found."}
            if row.attempt_claim_token != claim_token:
                payload = await self._serialize_malfunction(session, row, include_timeline=True)
                return {"changed": False, "malfunction": payload, "detail": "Recovery claim no longer owns this attempt."}
            row.attempt_claim_token = None
            row.attempt_claimed_at = None
            row.last_checked_at = datetime.now(tz=UTC)
            await session.commit()
            payload = await self._serialize_malfunction(session, row, include_timeline=True)

        await event_bus.publish("gate_malfunction.updated", payload)
        return {"changed": False, "malfunction": payload, "paused": True, "detail": detail}

    async def _finalize_claimed_resolution(
        self,
        malfunction_id: uuid.UUID,
        *,
        claim_token: str,
        snapshot: GateSnapshot,
        reason: str,
    ) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            row = await session.scalar(
                select(GateMalfunctionState)
                .where(GateMalfunctionState.id == malfunction_id)
                .with_for_update()
            )
            if not row:
                return {"changed": False, "error": "Gate malfunction not found."}
            if row.attempt_claim_token != claim_token:
                payload = await self._serialize_malfunction(session, row, include_timeline=True)
                return {"changed": False, "malfunction": payload, "detail": "Recovery claim no longer owns this attempt."}
            if row.status != GateMalfunctionStatus.ACTIVE:
                row.attempt_claim_token = None
                row.attempt_claimed_at = None
                await session.commit()
                payload = await self._serialize_malfunction(session, row, include_timeline=True)
                return {"changed": False, "malfunction": payload, "detail": "Malfunction is not active."}
            now = datetime.now(tz=UTC)
            await self._mark_resolved(session, row, now, reason=reason, snapshot=snapshot)
            row.attempt_claim_token = None
            row.attempt_claimed_at = None
            await session.commit()
            payload = await self._serialize_malfunction(session, row, include_timeline=True)

        await event_bus.publish("gate_malfunction.resolved", payload)
        await event_bus.publish("gate_malfunction.updated", payload)
        return {"changed": True, "malfunction": payload}

    async def _finalize_claimed_attempt(
        self,
        malfunction_id: uuid.UUID,
        *,
        claim_token: str,
        actor: str,
        manual: bool,
        accepted: bool,
        detail: str,
        state: str,
        after_attempt: GateSnapshot | None,
    ) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            row = await session.scalar(
                select(GateMalfunctionState)
                .where(GateMalfunctionState.id == malfunction_id)
                .with_for_update()
            )
            if not row:
                return {"changed": False, "error": "Gate malfunction not found."}
            if row.attempt_claim_token != claim_token:
                payload = await self._serialize_malfunction(session, row, include_timeline=True)
                return {"changed": False, "malfunction": payload, "detail": "Recovery claim no longer owns this attempt."}
            if row.status != GateMalfunctionStatus.ACTIVE:
                row.attempt_claim_token = None
                row.attempt_claimed_at = None
                await session.commit()
                payload = await self._serialize_malfunction(session, row, include_timeline=True)
                return {"changed": False, "malfunction": payload, "detail": "Malfunction is not active."}
            now = datetime.now(tz=UTC)
            attempt_number = row.fix_attempts_count + 1
            row.fix_attempts_count = attempt_number
            row.last_gate_state = state
            row.last_checked_at = now
            row.attempt_claim_token = None
            row.attempt_claimed_at = None
            await self._add_timeline_event(
                session,
                row,
                kind="manual_attempt" if manual else "attempt",
                occurred_at=now,
                title=f"Resolution attempt {attempt_number}",
                details={
                    "actor": actor,
                    "accepted": accepted,
                    "state": state,
                    "detail": detail,
                    "scheduled_at": row.next_attempt_scheduled_at.isoformat() if row.next_attempt_scheduled_at else None,
                },
                attempt_number=attempt_number,
                status="ok" if accepted else "error",
            )

            if attempt_number >= max(ATTEMPT_OFFSETS_SECONDS):
                if after_attempt and after_attempt.state == GateState.CLOSED:
                    await self._mark_resolved(session, row, now, reason="Gate closed after final recovery attempt.", snapshot=after_attempt)
                else:
                    await self._mark_fubar(session, row, now, reason="Automated recovery attempts exhausted.")
            elif row.status == GateMalfunctionStatus.ACTIVE:
                row.next_attempt_scheduled_at = row.opened_at + timedelta(seconds=ATTEMPT_OFFSETS_SECONDS[attempt_number + 1])
                await self._update_trace(session, row)

            await session.commit()
            payload = await self._serialize_malfunction(session, row, include_timeline=True)

        await event_bus.publish("gate_malfunction.attempt_executed", payload)
        await event_bus.publish("gate_malfunction.updated", payload)
        if payload["status"] == GateMalfunctionStatus.FUBAR.value:
            await self._queue_notification(
                malfunction_id,
                "gate_malfunction_fubar",
                "Gate malfunction is FUBAR",
                "critical",
                datetime.now(tz=UTC),
            )
        return {"changed": True, "malfunction": payload}

    async def _send_due_milestones(self, now: datetime) -> None:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.scalars(
                    select(GateMalfunctionState)
                    .options(selectinload(GateMalfunctionState.timeline_events))
                    .where(GateMalfunctionState.status == GateMalfunctionStatus.ACTIVE)
                )
            ).all()
            due: list[tuple[uuid.UUID, str, str, str, datetime]] = []
            for row in rows:
                elapsed = max(0, int((now - row.opened_at).total_seconds()))
                already = {
                    event.notification_trigger
                    for event in row.timeline_events
                    if event.notification_trigger
                }
                candidates = [
                    (trigger, subject, severity)
                    for seconds, trigger, subject, severity in MILESTONE_TRIGGERS
                    if elapsed >= seconds and trigger not in already
                ]
                if candidates:
                    trigger, subject, severity = candidates[-1]
                    due.append((row.id, trigger, subject, severity, now))

        for malfunction_id, trigger, subject, severity, occurred_at in due:
            await self._queue_notification(malfunction_id, trigger, subject, severity, occurred_at)

    async def _resolve_for_closed_gate(self, snapshot: GateSnapshot, *, reason: str) -> None:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.scalars(
                    select(GateMalfunctionState)
                    .where(
                        GateMalfunctionState.gate_entity_id == snapshot.entity_id,
                        GateMalfunctionState.status.in_(list(UNRESOLVED_STATUSES)),
                    )
                )
            ).all()
            if not rows:
                return
            now = datetime.now(tz=UTC)
            payloads = []
            for row in rows:
                await self._mark_resolved(session, row, now, reason=reason, snapshot=snapshot)
            await session.commit()
            for row in rows:
                payloads.append(await self._serialize_malfunction(session, row, include_timeline=True))

        for payload in payloads:
            await event_bus.publish("gate_malfunction.resolved", payload)
            await event_bus.publish("gate_malfunction.updated", payload)

    async def _mark_resolved(
        self,
        session,
        row: GateMalfunctionState,
        now: datetime,
        *,
        reason: str,
        snapshot: GateSnapshot | None,
    ) -> None:
        if row.status == GateMalfunctionStatus.RESOLVED:
            return
        row.status = GateMalfunctionStatus.RESOLVED
        row.resolved_at = now
        row.next_attempt_scheduled_at = None
        row.attempt_claim_token = None
        row.attempt_claimed_at = None
        row.last_gate_state = snapshot.state.value if snapshot else GateState.CLOSED.value
        row.last_checked_at = now
        await self._add_timeline_event(
            session,
            row,
            kind="resolved",
            occurred_at=now,
            title="Malfunction resolved",
            details={
                "reason": reason,
                "total_downtime_seconds": max(0, int((now - row.opened_at).total_seconds())),
            },
        )
        await self._update_trace(session, row)

    async def _mark_fubar(self, session, row: GateMalfunctionState, now: datetime, *, reason: str) -> None:
        if row.status == GateMalfunctionStatus.FUBAR:
            return
        if row.status == GateMalfunctionStatus.RESOLVED:
            return
        row.status = GateMalfunctionStatus.FUBAR
        row.fubar_at = now
        row.next_attempt_scheduled_at = None
        row.attempt_claim_token = None
        row.attempt_claimed_at = None
        row.last_gate_state = row.last_gate_state or GateState.OPEN.value
        row.last_checked_at = now
        await self._add_timeline_event(
            session,
            row,
            kind="fubar",
            occurred_at=now,
            title="FUBAR declared",
            details={
                "reason": reason,
                "fix_attempts_count": row.fix_attempts_count,
                "total_downtime_seconds": max(0, int((now - row.opened_at).total_seconds())),
            },
            status="error",
        )
        await self._update_trace(session, row)

    async def _queue_notification(
        self,
        malfunction_id: uuid.UUID,
        trigger: str,
        subject: str,
        severity: str,
        occurred_at: datetime,
    ) -> None:
        outbox_id: uuid.UUID | None = None
        async with AsyncSessionLocal() as session:
            row = await session.get(GateMalfunctionState, malfunction_id)
            if not row:
                return
            outbox = await session.scalar(
                select(GateMalfunctionNotificationOutbox).where(
                    GateMalfunctionNotificationOutbox.malfunction_id == row.id,
                    GateMalfunctionNotificationOutbox.trigger == trigger,
                )
            )
            if outbox and outbox.status in NOTIFICATION_TERMINAL_STATUSES:
                return
            if outbox is None:
                outbox = GateMalfunctionNotificationOutbox(
                    malfunction_id=row.id,
                    trigger=trigger,
                    subject=subject,
                    severity=severity,
                    occurred_at=occurred_at,
                    status="pending",
                    next_retry_at=occurred_at,
                )
                session.add(outbox)
                await self._add_timeline_event(
                    session,
                    row,
                    kind="notification_requested",
                    occurred_at=occurred_at,
                    title=f"{subject} notification queued",
                    details={"trigger": trigger, "severity": severity},
                    notification_trigger=trigger,
                )
                await self._update_trace(session, row)
            elif outbox.status == "failed" and (
                outbox.next_retry_at is None or outbox.next_retry_at <= datetime.now(tz=UTC)
            ):
                outbox.status = "pending"
                outbox.subject = subject
                outbox.severity = severity
                outbox.next_retry_at = datetime.now(tz=UTC)
            else:
                outbox.subject = subject
                outbox.severity = severity
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return
            payload = await self._serialize_malfunction(session, row, include_timeline=True)
            outbox_id = outbox.id

        await event_bus.publish("gate_malfunction.updated", payload)
        if outbox_id:
            await self._process_notification_id(outbox_id)

    async def _process_due_notifications(self, now: datetime) -> None:
        stale_cutoff = now - timedelta(seconds=NOTIFICATION_SENDING_STALE_SECONDS)
        async with AsyncSessionLocal() as session:
            rows = (
                await session.scalars(
                    select(GateMalfunctionNotificationOutbox)
                    .where(
                        or_(
                            and_(
                                GateMalfunctionNotificationOutbox.status == "pending",
                                or_(
                                    GateMalfunctionNotificationOutbox.next_retry_at.is_(None),
                                    GateMalfunctionNotificationOutbox.next_retry_at <= now,
                                ),
                            ),
                            and_(
                                GateMalfunctionNotificationOutbox.status == "failed",
                                GateMalfunctionNotificationOutbox.attempts_count < len(NOTIFICATION_RETRY_SECONDS),
                                or_(
                                    GateMalfunctionNotificationOutbox.next_retry_at.is_(None),
                                    GateMalfunctionNotificationOutbox.next_retry_at <= now,
                                ),
                            ),
                            and_(
                                GateMalfunctionNotificationOutbox.status == "sending",
                                or_(
                                    GateMalfunctionNotificationOutbox.last_attempt_at.is_(None),
                                    GateMalfunctionNotificationOutbox.last_attempt_at <= stale_cutoff,
                                ),
                            ),
                        ),
                    )
                    .order_by(GateMalfunctionNotificationOutbox.next_retry_at, GateMalfunctionNotificationOutbox.occurred_at)
                    .limit(20)
                )
            ).all()

        for row in rows:
            await self._process_notification_id(row.id)

    async def _process_notification_id(self, outbox_id: uuid.UUID) -> None:
        async with AsyncSessionLocal() as session:
            outbox = await session.scalar(
                select(GateMalfunctionNotificationOutbox)
                .where(GateMalfunctionNotificationOutbox.id == outbox_id)
                .with_for_update()
            )
            if not outbox or outbox.status in NOTIFICATION_TERMINAL_STATUSES:
                return
            now = datetime.now(tz=UTC)
            if outbox.status == "sending" and outbox.last_attempt_at and (
                now - outbox.last_attempt_at
            ).total_seconds() < NOTIFICATION_SENDING_STALE_SECONDS:
                return
            if (
                outbox.status == "failed"
                and outbox.next_retry_at is None
                and outbox.attempts_count >= len(NOTIFICATION_RETRY_SECONDS)
            ):
                return
            if outbox.status == "failed" and outbox.next_retry_at and outbox.next_retry_at > now:
                return
            row = await session.get(GateMalfunctionState, outbox.malfunction_id)
            if not row:
                outbox.status = "skipped"
                outbox.last_error = "Gate malfunction no longer exists."
                await session.commit()
                return
            outbox.status = "sending"
            outbox.attempts_count += 1
            outbox.last_attempt_at = now
            outbox.next_retry_at = None
            facts = await self._notification_facts(session, row, occurred_at=outbox.occurred_at)
            event_type = outbox.trigger
            subject = outbox.subject
            severity = outbox.severity
            await session.commit()

        try:
            result = await get_notification_service().process_context_with_result(
                NotificationContext(
                    event_type=event_type,
                    subject=subject,
                    severity=severity,
                    facts=facts,
                )
            )
            await self._finalize_outbox_result(outbox_id, result.status, "; ".join(result.failures))
        except Exception as exc:
            await self._finalize_outbox_result(outbox_id, "failed", str(exc))
            logger.exception("gate_malfunction_notification_dispatch_failed")

    async def _finalize_outbox_result(self, outbox_id: uuid.UUID, status: str, error: str = "") -> None:
        async with AsyncSessionLocal() as session:
            outbox = await session.scalar(
                select(GateMalfunctionNotificationOutbox)
                .where(GateMalfunctionNotificationOutbox.id == outbox_id)
                .with_for_update()
            )
            if not outbox or outbox.status in NOTIFICATION_TERMINAL_STATUSES:
                return
            if status == "sent":
                outbox.status = "sent"
                outbox.next_retry_at = None
                outbox.last_error = None
            elif status == "skipped":
                outbox.status = "skipped"
                outbox.next_retry_at = None
                outbox.last_error = error
            else:
                outbox.status = "failed"
                outbox.last_error = error
                retry_index = max(0, min(outbox.attempts_count - 1, len(NOTIFICATION_RETRY_SECONDS) - 1))
                outbox.next_retry_at = (
                    datetime.now(tz=UTC) + timedelta(seconds=NOTIFICATION_RETRY_SECONDS[retry_index])
                    if outbox.attempts_count < len(NOTIFICATION_RETRY_SECONDS)
                    else None
                )
            await session.commit()

    async def _record_notification_dispatch(self, event_type: str, payload: dict[str, Any]) -> None:
        malfunction_id = self._coerce_uuid(payload.get("malfunction_id"))
        if malfunction_id is None:
            return
        trigger = str(payload.get("event_type") or "")
        async with AsyncSessionLocal() as session:
            row = await session.get(GateMalfunctionState, malfunction_id)
            if not row:
                return
            if trigger:
                outbox = await session.scalar(
                    select(GateMalfunctionNotificationOutbox)
                    .where(
                        GateMalfunctionNotificationOutbox.malfunction_id == row.id,
                        GateMalfunctionNotificationOutbox.trigger == trigger,
                    )
                    .with_for_update()
                )
                if outbox:
                    if event_type == "notification.sent":
                        outbox.status = "sent"
                        outbox.next_retry_at = None
                        outbox.last_error = None
                    elif event_type == "notification.skipped" and outbox.status != "sent":
                        skip_reason = str(payload.get("reason") or "")
                        if skip_reason != "conditions_not_met" and outbox.status != "failed":
                            outbox.status = "skipped"
                            outbox.next_retry_at = None
                            outbox.last_error = skip_reason
                    elif event_type == "notification.failed" and outbox.status not in NOTIFICATION_TERMINAL_STATUSES:
                        outbox.status = "failed"
                        outbox.last_error = str(payload.get("error") or "")
                        retry_index = max(0, min(outbox.attempts_count - 1, len(NOTIFICATION_RETRY_SECONDS) - 1))
                        outbox.next_retry_at = (
                            datetime.now(tz=UTC) + timedelta(seconds=NOTIFICATION_RETRY_SECONDS[retry_index])
                            if outbox.attempts_count < len(NOTIFICATION_RETRY_SECONDS)
                            else None
                        )
            channel = str(payload.get("channel") or payload.get("reason") or "workflow")
            title = {
                "notification.sent": "Notification sent",
                "notification.failed": "Notification failed",
                "notification.skipped": "Notification skipped",
            }.get(event_type, "Notification update")
            await self._add_timeline_event(
                session,
                row,
                kind=event_type.replace(".", "_"),
                occurred_at=datetime.now(tz=UTC),
                title=title,
                details={
                    "rule_name": payload.get("rule_name"),
                    "event_type": payload.get("event_type"),
                    "channel": channel,
                    "delivered": payload.get("delivered"),
                    "reason": payload.get("reason"),
                    "error": payload.get("error"),
                },
                notification_trigger=str(payload.get("event_type") or ""),
                notification_channel=channel,
                status="error" if event_type == "notification.failed" else "ok",
            )
            await self._update_trace(session, row)
            await session.commit()
            serialized = await self._serialize_malfunction(session, row, include_timeline=True)
        await event_bus.publish("gate_malfunction.updated", serialized)

    async def _create_trace(self, session, row: GateMalfunctionState, snapshot: GateSnapshot) -> None:
        trace = TelemetryTrace(
            trace_id=str(row.telemetry_trace_id),
            name=f"Gate Malfunction - {row.gate_name or row.gate_entity_id}",
            category=TELEMETRY_CATEGORY_GATE_MALFUNCTION,
            status=row.status.value,
            level="warning",
            started_at=row.opened_at,
            actor="System",
            source=row.gate_entity_id,
            summary=f"{row.gate_name or row.gate_entity_id} has been open for more than 5 minutes.",
            context={
                "malfunction_id": str(row.id),
                "status": row.status.value,
                "gate_entity_id": row.gate_entity_id,
                "gate_name": row.gate_name,
                "gate_state": snapshot.state.value,
                "opened_at": row.opened_at.isoformat(),
                "declared_at": row.declared_at.isoformat(),
                "next_attempt_scheduled_at": row.next_attempt_scheduled_at.isoformat() if row.next_attempt_scheduled_at else None,
                "fix_attempts_count": row.fix_attempts_count,
                "attempt_in_progress": False,
            },
        )
        session.add(trace)
        await event_bus.publish(
            "telemetry.trace.created",
            {
                "trace_id": trace.trace_id,
                "name": trace.name,
                "category": trace.category,
                "status": trace.status,
                "level": trace.level,
                "duration_ms": trace.duration_ms,
                "registration_number": trace.registration_number,
            },
        )

    async def _update_trace(self, session, row: GateMalfunctionState) -> None:
        if not row.telemetry_trace_id:
            return
        trace = await session.get(TelemetryTrace, row.telemetry_trace_id)
        if not trace:
            return
        finished_at = row.resolved_at or row.fubar_at
        trace.status = row.status.value
        trace.level = "error" if row.status == GateMalfunctionStatus.FUBAR else "warning" if row.status == GateMalfunctionStatus.ACTIVE else "info"
        trace.ended_at = finished_at
        trace.duration_ms = (
            max(0.0, (finished_at - row.opened_at).total_seconds() * 1000)
            if finished_at
            else max(0.0, (datetime.now(tz=UTC) - row.opened_at).total_seconds() * 1000)
        )
        trace.summary = self._trace_summary(row)
        trace.context = sanitize_payload(
            {
                **(trace.context or {}),
                "malfunction_id": str(row.id),
                "status": row.status.value,
                "opened_at": row.opened_at.isoformat(),
                "declared_at": row.declared_at.isoformat(),
                "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
                "fubar_at": row.fubar_at.isoformat() if row.fubar_at else None,
                "next_attempt_scheduled_at": row.next_attempt_scheduled_at.isoformat() if row.next_attempt_scheduled_at else None,
                "fix_attempts_count": row.fix_attempts_count,
                "attempt_in_progress": bool(row.attempt_claim_token),
                "attempt_claimed_at": row.attempt_claimed_at.isoformat() if row.attempt_claimed_at else None,
                "last_gate_state": row.last_gate_state,
                "last_known_vehicle_event_id": str(row.last_known_vehicle_event_id) if row.last_known_vehicle_event_id else None,
                "total_downtime_seconds": self._downtime_seconds(row),
            }
        )

    async def _add_timeline_event(
        self,
        session,
        row: GateMalfunctionState,
        *,
        kind: str,
        occurred_at: datetime,
        title: str,
        details: dict[str, Any] | None = None,
        attempt_number: int | None = None,
        notification_trigger: str | None = None,
        notification_channel: str | None = None,
        status: str = "ok",
    ) -> None:
        generated_span_id = span_id()
        step_order = int(
            await session.scalar(
                select(func.count()).select_from(GateMalfunctionTimelineEvent).where(
                    GateMalfunctionTimelineEvent.malfunction_id == row.id
                )
            )
            or 0
        ) + 1
        safe_details = sanitize_payload(details or {})
        session.add(
            GateMalfunctionTimelineEvent(
                malfunction_id=row.id,
                kind=kind,
                occurred_at=occurred_at,
                title=title,
                details=safe_details,
                attempt_number=attempt_number,
                notification_trigger=notification_trigger,
                notification_channel=notification_channel,
                telemetry_span_id=generated_span_id,
                status=status,
            )
        )
        if row.telemetry_trace_id:
            session.add(
                TelemetrySpan(
                    span_id=generated_span_id,
                    trace_id=str(row.telemetry_trace_id),
                    name=title,
                    category=TELEMETRY_CATEGORY_GATE_MALFUNCTION,
                    step_order=step_order,
                    started_at=occurred_at,
                    ended_at=occurred_at,
                    duration_ms=0,
                    status=status,
                    attributes={"kind": kind, "attempt_number": attempt_number},
                    output_payload=safe_details,
                )
            )

    async def _last_known_vehicle_event(self, session, opened_at: datetime) -> AccessEvent | None:
        return await session.scalar(
            select(AccessEvent)
            .options(selectinload(AccessEvent.vehicle))
            .where(
                AccessEvent.occurred_at <= opened_at,
                AccessEvent.direction.in_([AccessDirection.ENTRY, AccessDirection.EXIT]),
            )
            .order_by(AccessEvent.occurred_at.desc())
            .limit(1)
        )

    async def _notification_facts(
        self,
        session,
        row: GateMalfunctionState,
        *,
        occurred_at: datetime,
    ) -> dict[str, str]:
        last_known_vehicle = await self._last_known_vehicle_label(session, row.last_known_vehicle_event_id)
        resolution_time = row.resolved_at or row.fubar_at
        return {
            "message": self._trace_summary(row),
            "malfunction_id": str(row.id),
            "telemetry_trace_id": str(row.telemetry_trace_id or ""),
            "malfunction_duration": self._format_duration(self._downtime_seconds(row, now=occurred_at)),
            "malfunction_opened_time": row.opened_at.isoformat(),
            "malfunction_fix_attempt_time": occurred_at.isoformat(),
            "malfunction_fix_attempts": str(row.fix_attempts_count),
            "malfunction_resolution_time": resolution_time.isoformat() if resolution_time else "",
            "last_known_vehicle": last_known_vehicle,
            "gate_status": row.last_gate_state or "",
            "entity_id": row.gate_entity_id,
            "occurred_at": occurred_at.isoformat(),
        }

    async def _serialize_malfunction(
        self,
        session,
        row: GateMalfunctionState,
        *,
        include_timeline: bool,
    ) -> dict[str, Any]:
        last_known_vehicle = await self._last_known_vehicle_label(session, row.last_known_vehicle_event_id)
        payload = {
            "id": str(row.id),
            "gate_entity_id": row.gate_entity_id,
            "gate_name": row.gate_name,
            "status": row.status.value,
            "opened_at": row.opened_at.isoformat(),
            "declared_at": row.declared_at.isoformat(),
            "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
            "fubar_at": row.fubar_at.isoformat() if row.fubar_at else None,
            "fix_attempts_count": row.fix_attempts_count,
            "next_attempt_scheduled_at": row.next_attempt_scheduled_at.isoformat() if row.next_attempt_scheduled_at else None,
            "attempt_in_progress": bool(row.attempt_claim_token),
            "attempt_claimed_at": row.attempt_claimed_at.isoformat() if row.attempt_claimed_at else None,
            "last_known_vehicle_event_id": str(row.last_known_vehicle_event_id) if row.last_known_vehicle_event_id else None,
            "last_known_vehicle": last_known_vehicle,
            "telemetry_trace_id": row.telemetry_trace_id,
            "last_gate_state": row.last_gate_state,
            "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
            "total_downtime_seconds": self._downtime_seconds(row),
            "summary": self._trace_summary(row),
        }
        if include_timeline:
            timeline = (
                await session.scalars(
                    select(GateMalfunctionTimelineEvent)
                    .where(GateMalfunctionTimelineEvent.malfunction_id == row.id)
                    .order_by(GateMalfunctionTimelineEvent.occurred_at, GateMalfunctionTimelineEvent.created_at)
                )
            ).all()
            payload["timeline"] = [self._serialize_timeline_event(event) for event in timeline]
        return payload

    def _serialize_timeline_event(self, event: GateMalfunctionTimelineEvent) -> dict[str, Any]:
        return {
            "id": str(event.id),
            "kind": event.kind,
            "occurred_at": event.occurred_at.isoformat(),
            "title": event.title,
            "details": event.details or {},
            "attempt_number": event.attempt_number,
            "notification_trigger": event.notification_trigger,
            "notification_channel": event.notification_channel,
            "telemetry_span_id": event.telemetry_span_id,
            "status": event.status,
        }

    async def _last_known_vehicle_label(self, session, event_id: uuid.UUID | None) -> str:
        if not event_id:
            return "No preceding vehicle event"
        event = await session.get(AccessEvent, event_id, options=[selectinload(AccessEvent.vehicle)])
        if not event:
            return "Preceding event no longer exists"
        return await self._access_event_label(session, event)

    async def _access_event_label(self, session, event: AccessEvent) -> str:
        person = await session.get(Person, event.person_id) if event.person_id else None
        vehicle = event.vehicle or (await session.get(Vehicle, event.vehicle_id) if event.vehicle_id else None)
        subject = person.display_name if person else event.registration_number
        vehicle_label = self._vehicle_label(vehicle, event.registration_number)
        verb = "entered" if event.direction == AccessDirection.ENTRY else "exited"
        return f"{subject} {verb} in {vehicle_label}" if vehicle_label else f"{subject} {verb}"

    def _access_event_details(self, event: AccessEvent) -> dict[str, Any]:
        return {
            "event_id": str(event.id),
            "registration_number": event.registration_number,
            "direction": event.direction.value,
            "decision": event.decision.value,
            "occurred_at": event.occurred_at.isoformat(),
        }

    def _vehicle_label(self, vehicle: Vehicle | None, fallback: str) -> str:
        if not vehicle:
            return fallback
        label = " ".join(part for part in [vehicle.make, vehicle.model] if part)
        return label or vehicle.description or vehicle.registration_number or fallback

    def _trace_summary(self, row: GateMalfunctionState) -> str:
        gate = row.gate_name or row.gate_entity_id
        if row.status == GateMalfunctionStatus.RESOLVED:
            return f"{gate} malfunction resolved after {self._format_duration(self._downtime_seconds(row))}."
        if row.status == GateMalfunctionStatus.FUBAR:
            return f"{gate} is FUBAR after {row.fix_attempts_count} automated recovery attempts."
        return f"{gate} is open; next recovery attempt is scheduled after {row.fix_attempts_count} attempts."

    def _downtime_seconds(self, row: GateMalfunctionState, *, now: datetime | None = None) -> int:
        end = row.resolved_at or row.fubar_at or now or datetime.now(tz=UTC)
        return max(0, int((end - row.opened_at).total_seconds()))

    def _format_duration(self, seconds: int) -> str:
        remaining = max(0, int(seconds))
        hours, remaining = divmod(remaining, 3600)
        minutes, seconds = divmod(remaining, 60)
        if hours:
            return f"{hours}h {minutes}m"
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    def _coerce_gate_state(self, value: Any) -> GateState:
        return _coerce_gate_state_value(value)

    def _parse_datetime(self, value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    def _history_cursor(self, row: GateMalfunctionState) -> str:
        return f"{row.opened_at.isoformat()}|{row.id}"

    def _parse_history_cursor(self, value: str | None) -> tuple[datetime | None, uuid.UUID | None]:
        if not value or "|" not in value:
            return None, None
        opened_at_text, row_id_text = value.split("|", 1)
        opened_at = self._parse_datetime(opened_at_text)
        row_id = self._coerce_uuid(row_id_text)
        return opened_at, row_id

    def _coerce_uuid(self, value: Any) -> uuid.UUID | None:
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError):
            return None


@lru_cache
def get_gate_malfunction_service() -> GateMalfunctionService:
    return GateMalfunctionService()


def _log_background_task_error(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("gate_malfunction_background_task_failed")
