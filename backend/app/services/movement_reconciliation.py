import asyncio
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import GateCommandRecord, MovementSagaRecord, Presence
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
        async with AsyncSessionLocal() as session:
            rows = (
                await session.scalars(
                    select(MovementSagaRecord)
                    .options(
                        selectinload(MovementSagaRecord.access_event),
                        selectinload(MovementSagaRecord.gate_commands),
                    )
                    .where(MovementSagaRecord.reconciliation_required.is_(True))
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
        command = _latest_reconciliation_command(saga.gate_commands)
        if not command:
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

        state = await self._current_gate_state()
        if state in {GateState.OPEN, GateState.OPENING}:
            presence_committed = saga.presence_committed
            if not presence_committed:
                presence_committed = await self._commit_presence_if_possible(session, saga)
            await self._ledger.mark_gate_command_reconciled(
                session,
                command,
                detail=f"Gate state reconciled as {state.value}.",
                success=True,
            )
            await self._ledger.transition_movement_saga(
                session,
                saga,
                MovementSagaState.COMPLETED,
                detail=f"Gate state reconciled as {state.value}.",
                reconciliation_required=False,
                presence_committed=presence_committed,
            )
            await self._publish_reconciled(saga, command, state)
            return 1

        completed_at = command.completed_at or command.updated_at or datetime.now(tz=UTC)
        if datetime.now(tz=UTC) - completed_at < timedelta(seconds=RECONCILIATION_GRACE_SECONDS):
            return 0

        detail = f"Gate command accepted but latest gate state is {state.value}."
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
        if command.requires_reconciliation or command.state == GateCommandState.RECONCILIATION_REQUIRED
    ]
    if not candidates:
        candidates = list(commands)
    return max(candidates, key=lambda command: command.updated_at, default=None)


@lru_cache
def get_movement_reconciliation_service() -> MovementReconciliationService:
    return MovementReconciliationService()
