import asyncio
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import GateCommandRecord, GateStateObservation, MovementSagaRecord, Presence
from app.models.enums import AccessDirection, GateCommandState, MovementSagaState, PresenceState
from app.modules.gate.base import GateState
from app.modules.notifications.base import NotificationContext
from app.modules.registry import get_gate_controller
from app.services.event_bus import event_bus
from app.services.movement_ledger import get_movement_ledger_repository, movement_saga_summary
from app.services.notifications import get_notification_service

logger = get_logger(__name__)

RECONCILIATION_INTERVAL_SECONDS = 15.0
RECONCILIATION_GRACE_SECONDS = 20.0
GATE_OPEN_CONFIRMATION_WINDOW_SECONDS = 120.0
GATE_OBSERVATION_CLOCK_SKEW_SECONDS = 2.0
GATE_OBSERVATION_OPEN_STATES = {GateState.OPEN.value, GateState.OPENING.value}


class MovementReconciliationService:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._ledger = get_movement_ledger_repository()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="movement-reconciliation")
        logger.info("movement_reconciliation_started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("movement_reconciliation_stopped")

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.reconcile_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("movement_reconciliation_failed", extra={"error": str(exc)})
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=RECONCILIATION_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                continue

    async def reconcile_once(self) -> int:
        now = datetime.now(tz=UTC)
        stale_cutoff = now - timedelta(seconds=RECONCILIATION_GRACE_SECONDS)
        command_saga_ids = (
            select(GateCommandRecord.movement_saga_id)
            .where(GateCommandRecord.movement_saga_id.is_not(None))
            .where(
                or_(
                    GateCommandRecord.requires_reconciliation.is_(True),
                    GateCommandRecord.state == GateCommandState.RECONCILIATION_REQUIRED,
                    and_(
                        GateCommandRecord.state == GateCommandState.LEASED,
                        GateCommandRecord.lease_expires_at.is_not(None),
                        GateCommandRecord.lease_expires_at <= now,
                    ),
                    and_(
                        GateCommandRecord.state == GateCommandState.ACCEPTED,
                        GateCommandRecord.mechanically_confirmed.is_(False),
                        GateCommandRecord.completed_at.is_not(None),
                        GateCommandRecord.completed_at <= stale_cutoff,
                    ),
                )
            )
        )
        async with AsyncSessionLocal() as session:
            rows = (
                await session.scalars(
                    select(MovementSagaRecord)
                    .options(
                        selectinload(MovementSagaRecord.access_event),
                        selectinload(MovementSagaRecord.gate_commands),
                    )
                    .where(
                        or_(
                            MovementSagaRecord.reconciliation_required.is_(True),
                            MovementSagaRecord.state == MovementSagaState.PHYSICAL_COMMAND_PENDING,
                            MovementSagaRecord.id.in_(command_saga_ids),
                        )
                    )
                    .order_by(MovementSagaRecord.updated_at.asc())
                    .limit(25)
                )
            ).all()
            count = 0
            for saga in rows:
                count += await self._reconcile_saga(session, saga)
            await session.commit()
            return count

    async def _reconcile_saga(self, session, saga: MovementSagaRecord) -> int:
        now = datetime.now(tz=UTC)
        command = _latest_reconciliation_command(saga.gate_commands)
        if not command:
            pending_since = saga.updated_at or saga.created_at or saga.occurred_at
            if (
                saga.state == MovementSagaState.PHYSICAL_COMMAND_PENDING
                and pending_since
                and now - pending_since < timedelta(seconds=RECONCILIATION_GRACE_SECONDS)
            ):
                return 0
            await self._ledger.transition_movement_saga(
                session,
                saga,
                MovementSagaState.FAILED,
                detail="No gate command record was available for reconciliation.",
                reconciliation_required=False,
                failure_detail="missing_gate_command",
            )
            await self._publish_saga_failed(saga, "missing_gate_command")
            return 1

        if command.state == GateCommandState.LEASED:
            lease_expires_at = command.lease_expires_at or command.updated_at or command.leased_at
            if lease_expires_at and lease_expires_at > now:
                return 0
            expired_at = lease_expires_at or now
            command.state = GateCommandState.RECONCILIATION_REQUIRED
            command.requires_reconciliation = True
            command.detail = "Gate command lease expired before completion."
            command.completed_at = expired_at
            command.lease_token = None
            command.lease_expires_at = None
            await self._ledger.transition_movement_saga(
                session,
                saga,
                MovementSagaState.RECONCILIATION_REQUIRED,
                detail="Gate command lease expired before completion.",
                reconciliation_required=True,
            )

        observed_open = await self._gate_open_observation_after_command(session, command)
        if observed_open:
            state = _gate_state_from_observation(observed_open)
            detail = (
                f"Gate open observation reconciled as {state.value} "
                f"at {observed_open.observed_at.isoformat()}."
            )
            return await self._complete_reconciled_saga(session, saga, command, state, detail)

        state = await self._current_gate_state()
        if state in {GateState.OPEN, GateState.OPENING}:
            return await self._complete_reconciled_saga(
                session,
                saga,
                command,
                state,
                f"Gate state reconciled as {state.value}.",
            )

        completed_at = command.completed_at or command.updated_at or now
        if now - completed_at < timedelta(seconds=RECONCILIATION_GRACE_SECONDS):
            return 0

        detail = (
            "Gate command accepted but no open/opening gate observation was recorded "
            f"within {int(GATE_OPEN_CONFIRMATION_WINDOW_SECONDS)} seconds; latest gate state is {state.value}."
        )
        await self._ledger.mark_gate_command_reconciled(session, command, detail=detail, success=False)
        await self._ledger.transition_movement_saga(
            session,
            saga,
            MovementSagaState.FAILED,
            detail=detail,
            reconciliation_required=False,
            failure_detail=detail,
        )
        await self._publish_saga_failed(saga, detail)
        await self._notify_reconciliation_failure(saga, command, detail)
        return 1

    async def _complete_reconciled_saga(
        self,
        session,
        saga: MovementSagaRecord,
        command: GateCommandRecord,
        state: GateState,
        detail: str,
    ) -> int:
        presence_committed = saga.presence_committed
        if not presence_committed:
            presence_committed = await self._commit_presence_if_possible(session, saga)
        await self._ledger.mark_gate_command_reconciled(
            session,
            command,
            detail=detail,
            success=True,
        )
        await self._ledger.transition_movement_saga(
            session,
            saga,
            MovementSagaState.COMPLETED,
            detail=detail,
            reconciliation_required=False,
            presence_committed=presence_committed,
        )
        await self._publish_reconciled(saga, command, state)
        return 1

    async def _gate_open_observation_after_command(
        self,
        session,
        command: GateCommandRecord,
    ) -> GateStateObservation | None:
        started_at = command.started_at or command.leased_at or command.created_at
        if not started_at:
            return None
        window_start = started_at - timedelta(seconds=GATE_OBSERVATION_CLOCK_SKEW_SECONDS)
        window_end = started_at + timedelta(seconds=GATE_OPEN_CONFIRMATION_WINDOW_SECONDS)
        return await session.scalar(
            select(GateStateObservation)
            .where(
                GateStateObservation.state.in_(GATE_OBSERVATION_OPEN_STATES),
                GateStateObservation.observed_at >= window_start,
                GateStateObservation.observed_at <= window_end,
            )
            .order_by(GateStateObservation.observed_at.asc())
        )

    async def _current_gate_state(self) -> GateState:
        try:
            return await get_gate_controller(settings.gate_controller).current_state()
        except Exception as exc:
            logger.warning("movement_reconciliation_gate_state_failed", extra={"error": str(exc)})
            return GateState.UNKNOWN

    async def _commit_presence_if_possible(self, session, saga: MovementSagaRecord) -> bool:
        event = saga.access_event
        if not event or not event.person_id:
            return False
        presence = await session.get(Presence, event.person_id)
        if not presence:
            presence = Presence(person_id=event.person_id)
            session.add(presence)
        if presence.last_changed_at and event.occurred_at < presence.last_changed_at:
            logger.info(
                "movement_reconciliation_presence_stale_skipped",
                extra={
                    "movement_saga_id": str(saga.id),
                    "event_id": str(event.id),
                    "event_occurred_at": event.occurred_at.isoformat(),
                    "presence_last_changed_at": presence.last_changed_at.isoformat(),
                },
            )
            return False
        presence.state = (
            PresenceState.PRESENT
            if event.direction == AccessDirection.ENTRY
            else PresenceState.EXITED
        )
        presence.last_event_id = event.id
        presence.last_changed_at = event.occurred_at
        return True

    async def _publish_reconciled(
        self,
        saga: MovementSagaRecord,
        command: GateCommandRecord,
        state: GateState,
    ) -> None:
        payload = {
            "movement_saga": movement_saga_summary(saga),
            "gate_command_id": str(command.id),
            "gate_state": state.value,
        }
        await event_bus.publish("movement_saga.reconciled", payload)
        await event_bus.publish("gate.command.reconciled", payload)

    async def _publish_saga_failed(self, saga: MovementSagaRecord, detail: str) -> None:
        await event_bus.publish(
            "movement_saga.failed",
            {"movement_saga": movement_saga_summary(saga), "detail": detail},
        )

    async def _notify_reconciliation_failure(
        self,
        saga: MovementSagaRecord,
        command: GateCommandRecord,
        detail: str,
    ) -> None:
        await get_notification_service().notify(
            NotificationContext(
                event_type="gate_command_reconciliation_failed",
                subject=saga.registration_number or command.registration_number or "Gate command",
                severity="critical",
                facts={
                    "movement_saga_id": str(saga.id),
                    "gate_command_id": str(command.id),
                    "registration_number": saga.registration_number or command.registration_number,
                    "message": detail,
                },
            )
        )


def _latest_reconciliation_command(commands: list[GateCommandRecord]) -> GateCommandRecord | None:
    candidates = [
        command
        for command in commands
        if command.requires_reconciliation
        or command.state in {GateCommandState.RECONCILIATION_REQUIRED, GateCommandState.LEASED}
        or (
            command.state == GateCommandState.ACCEPTED
            and not command.mechanically_confirmed
        )
    ]
    if not candidates:
        candidates = list(commands)
    return max(candidates, key=lambda command: command.updated_at, default=None)


def _gate_state_from_observation(observation: GateStateObservation) -> GateState:
    try:
        return GateState(str(observation.state))
    except ValueError:
        return GateState.UNKNOWN


@lru_cache
def get_movement_reconciliation_service() -> MovementReconciliationService:
    return MovementReconciliationService()
