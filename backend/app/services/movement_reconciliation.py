import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import AccessEvent, GateCommandRecord, MovementSagaRecord, Person
from app.models.enums import GateCommandState, MovementSagaState
from app.modules.gate.base import GateState
from app.services.event_bus import event_bus
from app.services.movement.admission import AdmissionResult, finalize_in_session, recover_unattempted_visitor_reservations
from app.services.access.delivery import recover_garage_outcome_outputs
from app.services.access_device_commands import AccessDeviceCommandJournal
from app.services.actionable_notifications import get_actionable_notification_service
from app.services.movement_ledger import get_movement_ledger_repository, movement_saga_summary
from app.services.person_presence_input_booleans import apply_person_presence_input_boolean_actions
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS, write_audit_log

logger = get_logger(__name__)

RECONCILIATION_INTERVAL_SECONDS = 15.0
RECONCILIATION_GRACE_SECONDS = 20.0


class MovementReconciliationService:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._ledger = get_movement_ledger_repository()
        self._pending_events: list[tuple[str, dict]] = []
        self._saga_cursor: uuid.UUID | None = None
        self._standalone_cursor: uuid.UUID | None = None
        self._reservation_cursor: uuid.UUID | None = None
        self._actionable_cursor: uuid.UUID | None = None

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
        self._pending_events = []
        actionable_outputs = 0
        try:
            actionable_outputs, self._actionable_cursor = await (
                get_actionable_notification_service().reconcile_actionable_outputs(
                    after_id=self._actionable_cursor
                )
            )
        except Exception as exc:  # noqa: BLE001 - independent recovery owners must still run.
            logger.error(
                "actionable_output_reconciliation_failed",
                extra={"error_class": type(exc).__name__},
            )
        garage_outputs = await recover_garage_outcome_outputs()
        recovered_reservations, self._reservation_cursor = await recover_unattempted_visitor_reservations(
            after_id=self._reservation_cursor)
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
                    .where(MovementSagaRecord.id > self._saga_cursor if self._saga_cursor else True)
                    .order_by(MovementSagaRecord.id.asc())
                    .limit(25)
                )
            ).all()
            next_saga_cursor = rows[-1].id if len(rows) == 25 else None
            count = actionable_outputs + garage_outputs + recovered_reservations
            presence_input_boolean_jobs: list[tuple[Person, AccessEvent]] = []
            for saga in rows:
                count += await self._reconcile_saga(
                    session,
                    saga,
                    presence_input_boolean_jobs=presence_input_boolean_jobs,
                )
            standalone_commands = (
                await session.scalars(
                    select(GateCommandRecord)
                    .where(GateCommandRecord.movement_saga_id.is_(None))
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
                    .where(GateCommandRecord.id > self._standalone_cursor if self._standalone_cursor else True)
                    .order_by(GateCommandRecord.id.asc())
                    .limit(25)
                )
            ).all()
            next_standalone_cursor = standalone_commands[-1].id if len(standalone_commands) == 25 else None
            for command in standalone_commands:
                count += await self._reconcile_standalone_gate_command(session, command)
            await session.commit()
            # Advance only after the batch commits; held rows keep their truthful state.
            self._saga_cursor = next_saga_cursor
            self._standalone_cursor = next_standalone_cursor
            publications, self._pending_events = self._pending_events, []
            for event_type, payload in publications:
                await event_bus.publish(event_type, payload)
            for person, event in presence_input_boolean_jobs:
                try:
                    await apply_person_presence_input_boolean_actions(
                        person,
                        event,
                        source="movement_reconciliation_presence_commit",
                    )
                except Exception as exc:
                    logger.warning(
                        "movement_reconciliation_input_boolean_unhandled_failure",
                        extra={
                            "event_id": str(event.id),
                            "person_id": str(person.id),
                            "error": str(exc),
                        },
                    )
            return count

    async def _reconcile_standalone_gate_command(self, session, command: GateCommandRecord) -> int:
        if isinstance(session, AsyncSession):
            await session.refresh(command, with_for_update=True)
        now = (await session.scalar(select(func.clock_timestamp()))
               if isinstance(session, AsyncSession) else datetime.now(tz=UTC))
        if command.state == GateCommandState.LEASED and command.lease_expires_at and command.lease_expires_at > now:
            return 0
        if (command.command_metadata or {}).get("target_plan"):
            before = (command.command_metadata, command.state, command.completed_at,
                      command.requires_reconciliation, command.lease_token, command.lease_expires_at)
            await AccessDeviceCommandJournal().reconcile_parent_in_session(session, command)
            changed = before != (command.command_metadata, command.state, command.completed_at,
                                 command.requires_reconciliation, command.lease_token, command.lease_expires_at)
            if changed:
                await self._audit_standalone_gate_command_reconciliation(session, command,
                    "Per-target command receipts reconciled.", success=command.mechanically_confirmed)
                if command.mechanically_confirmed:
                    await self._publish_standalone_command_reconciled(command, GateState(command.gate_state or "unknown"))
                elif not command.requires_reconciliation:
                    await self._publish_standalone_command_failed(command, "Physical targets were not verified.")
            return int(changed)
        if command.state == GateCommandState.LEASED:
            detail = "Standalone gate command lease expired without a target journal; delivery remains unknown."
            self._ledger.mark_gate_command_uncertain(command, at=now, detail=detail)
            await self._audit_standalone_gate_command_reconciliation(session, command, detail, success=False)
            return 1
        # Historical commands have no immutable physical target receipt. Neither
        # today's configured gate nor elapsed time can establish their outcome.
        return 0

    async def _reconcile_saga(
        self, session, saga: MovementSagaRecord, *,
        presence_input_boolean_jobs: list[tuple[Person, AccessEvent]] | None = None,
    ) -> int:
        now = (await session.scalar(select(func.clock_timestamp()))
               if isinstance(session, AsyncSession) else datetime.now(tz=UTC))
        command = _latest_reconciliation_command(saga.gate_commands)
        if not command:
            pending_since = saga.updated_at or saga.created_at or saga.occurred_at
            if (saga.state == MovementSagaState.PHYSICAL_COMMAND_PENDING and pending_since
                    and now - pending_since < timedelta(seconds=RECONCILIATION_GRACE_SECONDS)):
                return 0
            if saga.access_event_id is not None:
                result = await finalize_in_session(session, saga_id=saga.id)
                await self._queue_presence_effects(session, result, presence_input_boolean_jobs)
                return int(result.changed)
            return 0
        if command.state == GateCommandState.LEASED and command.lease_expires_at and command.lease_expires_at > now:
            return 0
        if (command.command_metadata or {}).get("target_plan"):
            # The finalizer takes saga/event locks before any parent/target locks.
            # Reconciliation cannot bypass its admission or presence transition.
            result = await finalize_in_session(session, saga_id=saga.id, gate_command_id=command.id)
            await self._queue_presence_effects(session, result, presence_input_boolean_jobs)
            if not result.changed:
                return 0
            if result.admission_status == "verified":
                await self._publish_reconciled(result.saga, command, GateState(command.gate_state or "unknown"))
            elif result.admission_status == "denied":
                detail = "The designated entry target was not verified."
                await self._publish_saga_failed(result.saga, detail)
            return 1
        if isinstance(session, AsyncSession):
            await session.refresh(saga, with_for_update=True)
            await session.refresh(command, with_for_update=True)
        if command.state == GateCommandState.LEASED and command.lease_expires_at and command.lease_expires_at > now:
            return 0
        if command.state == GateCommandState.LEASED:
            self._ledger.mark_gate_command_uncertain(command, at=now,
                detail="Gate command lease expired without a target journal; delivery remains unknown.")
            await self._ledger.transition_movement_saga(session, saga, MovementSagaState.RECONCILIATION_REQUIRED,
                detail="Gate command lease expired before completion.", reconciliation_required=True)
            return 1
        return 0

    async def _queue_presence_effects(self, session, result: AdmissionResult,
                                      jobs: list[tuple[Person, AccessEvent]] | None) -> None:
        if (jobs is None or not result.presence_changed or not result.event.person_id
                or result.admission_status == "historical"):
            return
        person = await session.get(Person, result.event.person_id)
        if person:
            jobs.append((person, result.event))

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
        self._pending_events.extend([("movement_saga.reconciled", payload), ("gate.command.reconciled", payload)])

    async def _publish_standalone_command_reconciled(
        self,
        command: GateCommandRecord,
        state: GateState,
    ) -> None:
        self._pending_events.append((
            "gate.command.reconciled",
            {
                "movement_saga": None,
                "gate_command_id": str(command.id),
                "gate_state": state.value,
                "detail": command.detail,
            },
        ))

    async def _publish_saga_failed(self, saga: MovementSagaRecord, detail: str) -> None:
        self._pending_events.append((
            "movement_saga.failed",
            {"movement_saga": movement_saga_summary(saga), "detail": detail},
        ))

    async def _publish_standalone_command_failed(self, command: GateCommandRecord, detail: str) -> None:
        self._pending_events.append((
            "gate.command.reconciliation_failed",
            {
                "gate_command_id": str(command.id),
                "gate_key": command.gate_key,
                "detail": detail,
            },
        ))

    async def _audit_standalone_gate_command_reconciliation(
        self,
        session,
        command: GateCommandRecord,
        detail: str,
        *,
        success: bool,
    ) -> None:
        await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            action="gate.command.reconciliation",
            actor="IACS_Reconciliation",
            target_entity="GateCommand",
            target_id=command.id,
            target_label=command.gate_key,
            outcome="success" if success else "requires_review" if command.requires_reconciliation else "failed",
            level="info" if success else "warning",
            metadata={
                "source": command.source,
                "gate_key": command.gate_key,
                "state": command.state.value,
                "accepted": command.accepted,
                "detail": detail,
            },
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



@lru_cache
def get_movement_reconciliation_service() -> MovementReconciliationService:
    return MovementReconciliationService()
