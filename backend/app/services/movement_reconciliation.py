import asyncio
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import AccessDevice, AccessEvent, GateCommandRecord, GateStateObservation, MovementSagaRecord, Person
from app.models.enums import GateCommandState, MovementSagaState
from app.modules.access_devices.base import ACCESS_DEVICE_KIND_GATE
from app.modules.gate.base import GateState
from app.modules.home_assistant.covers import normalize_cover_entities
from app.modules.notifications.base import NotificationContext
from app.modules.registry import get_gate_controller
from app.services.event_bus import event_bus
from app.services.movement.presence import commit_presence_for_event
from app.services.movement_ledger import get_movement_ledger_repository, movement_saga_summary
from app.services.notifications import get_notification_service
from app.services.person_presence_input_booleans import apply_person_presence_input_boolean_actions
from app.services.settings import get_runtime_config
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS, write_audit_log

logger = get_logger(__name__)

RECONCILIATION_INTERVAL_SECONDS = 15.0
RECONCILIATION_GRACE_SECONDS = 20.0
GATE_OPEN_CONFIRMATION_WINDOW_SECONDS = 120.0
GATE_OBSERVATION_CLOCK_SKEW_SECONDS = 2.0
GATE_OBSERVATION_OPEN_STATES = {GateState.OPEN.value, GateState.OPENING.value}
STANDALONE_GATE_COMMAND_STALE_SECONDS = GATE_OPEN_CONFIRMATION_WINDOW_SECONDS + RECONCILIATION_GRACE_SECONDS


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
                    .order_by(GateCommandRecord.updated_at.asc())
                    .limit(25)
                )
            ).all()
            for command in standalone_commands:
                count += await self._reconcile_standalone_gate_command(session, command)
            await session.commit()
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
        now = datetime.now(tz=UTC)
        if command.state == GateCommandState.LEASED:
            lease_expires_at = command.lease_expires_at or command.updated_at or command.leased_at
            if lease_expires_at and lease_expires_at > now:
                return 0
            command.lease_token = None
            command.lease_expires_at = None
            command.completed_at = lease_expires_at or now
            detail = "Standalone gate command lease expired before completion."
            await self._ledger.mark_gate_command_reconciled(session, command, detail=detail, success=False)
            await self._audit_standalone_gate_command_reconciliation(session, command, detail, success=False)
            await self._publish_standalone_command_failed(command, detail)
            return 1

        observed_open = await self._gate_open_observation_after_command(session, command)
        if observed_open:
            state = _gate_state_from_observation(observed_open)
            detail = (
                f"Standalone gate command reconciled from {state.value} observation "
                f"at {observed_open.observed_at.isoformat()}."
            )
            await self._ledger.mark_gate_command_reconciled(session, command, detail=detail, success=True)
            await self._audit_standalone_gate_command_reconciliation(session, command, detail, success=True)
            await self._publish_standalone_command_reconciled(command, state)
            return 1

        completed_at = command.completed_at or command.updated_at or command.created_at
        if completed_at and now - completed_at < timedelta(seconds=STANDALONE_GATE_COMMAND_STALE_SECONDS):
            return 0

        state = await self._current_gate_state()
        detail = (
            "Standalone gate command accepted but no open/opening gate observation was recorded "
            f"within {int(GATE_OPEN_CONFIRMATION_WINDOW_SECONDS)} seconds; latest gate state is {state.value}."
        )
        await self._ledger.mark_gate_command_reconciled(session, command, detail=detail, success=False)
        await self._audit_standalone_gate_command_reconciliation(session, command, detail, success=False)
        await self._publish_standalone_command_failed(command, detail)
        return 1

    async def _reconcile_saga(
        self,
        session,
        saga: MovementSagaRecord,
        *,
        presence_input_boolean_jobs: list[tuple[Person, AccessEvent]] | None = None,
    ) -> int:
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
            return await self._complete_reconciled_saga(
                session,
                saga,
                command,
                state,
                detail,
                presence_input_boolean_jobs=presence_input_boolean_jobs,
            )

        state = await self._current_gate_state()
        if state in {GateState.OPEN, GateState.OPENING}:
            return await self._complete_reconciled_saga(
                session,
                saga,
                command,
                state,
                f"Gate state reconciled as {state.value}.",
                presence_input_boolean_jobs=presence_input_boolean_jobs,
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
        *,
        presence_input_boolean_jobs: list[tuple[Person, AccessEvent]] | None = None,
    ) -> int:
        presence_committed = saga.presence_committed
        if not presence_committed:
            presence_committed = await self._commit_presence_if_possible(
                session,
                saga,
                presence_input_boolean_jobs=presence_input_boolean_jobs,
            )
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
        observation_ids = await self._gate_observation_ids_for_command(session, command)
        if not observation_ids:
            logger.warning(
                "movement_reconciliation_gate_identity_unresolved",
                extra={
                    "gate_command_id": str(command.id),
                    "gate_key": command.gate_key,
                    "source": command.source,
                },
            )
            return None
        return await session.scalar(
            select(GateStateObservation)
            .where(
                GateStateObservation.state.in_(GATE_OBSERVATION_OPEN_STATES),
                GateStateObservation.gate_entity_id.in_(sorted(observation_ids)),
                GateStateObservation.observed_at >= window_start,
                GateStateObservation.observed_at <= window_end,
            )
            .order_by(GateStateObservation.observed_at.asc())
        )

    async def _gate_observation_ids_for_command(
        self,
        session,
        command: GateCommandRecord,
    ) -> set[str]:
        observation_ids = self._gate_observation_ids_from_command_metadata(command)
        if observation_ids:
            return observation_ids
        observation_ids.update(await self._configured_gate_observation_ids(session))
        return observation_ids

    def _gate_observation_ids_from_command_metadata(self, command: GateCommandRecord) -> set[str]:
        ids: set[str] = set()
        gate_key = str(command.gate_key or "").strip()
        if gate_key and gate_key != "default":
            ids.add(gate_key)
        metadata = command.command_metadata if isinstance(command.command_metadata, dict) else {}
        for key in ("gate_entity_id", "entity_id", "device_key", "external_id"):
            self._add_observation_id(ids, metadata.get(key))
        outcomes = metadata.get("access_device_outcomes")
        if isinstance(outcomes, list):
            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    continue
                for key in ("entity_id", "device_key", "external_id"):
                    self._add_observation_id(ids, outcome.get(key))
                outcome_metadata = outcome.get("metadata")
                if isinstance(outcome_metadata, dict):
                    for key in ("entity_id", "device_key", "external_id"):
                        self._add_observation_id(ids, outcome_metadata.get(key))
        return ids

    async def _configured_gate_observation_ids(self, session) -> set[str]:
        ids: set[str] = set()
        try:
            rows = (
                await session.scalars(
                    select(AccessDevice)
                    .options(selectinload(AccessDevice.provider_bindings))
                    .where(
                        AccessDevice.kind == ACCESS_DEVICE_KIND_GATE,
                        AccessDevice.enabled.is_(True),
                        AccessDevice.open_for_access.is_(True),
                    )
                )
            ).all()
        except Exception as exc:
            logger.debug(
                "movement_reconciliation_access_device_identity_lookup_failed",
                extra={"error": str(exc)},
            )
            rows = []
        for row in rows:
            self._add_observation_id(ids, row.key)
            for binding in row.provider_bindings:
                if binding.enabled:
                    self._add_observation_id(ids, binding.external_id)

        try:
            runtime = await get_runtime_config()
            gate_entities = normalize_cover_entities(
                runtime.home_assistant_gate_entities,
                default_open_service=runtime.home_assistant_gate_open_service,
            )
        except Exception as exc:
            logger.debug(
                "movement_reconciliation_gate_identity_lookup_failed",
                extra={"error": str(exc)},
            )
            gate_entities = []
        for entity in gate_entities:
            if isinstance(entity, dict):
                self._add_observation_id(ids, entity.get("entity_id"))
        return ids

    def _add_observation_id(self, ids: set[str], value: Any) -> None:
        text = str(value or "").strip()
        if text:
            ids.add(text)

    async def _current_gate_state(self) -> GateState:
        try:
            return await get_gate_controller("configured").current_state()
        except Exception as exc:
            logger.warning("movement_reconciliation_gate_state_failed", extra={"error": str(exc)})
            return GateState.UNKNOWN

    async def _commit_presence_if_possible(
        self,
        session,
        saga: MovementSagaRecord,
        *,
        presence_input_boolean_jobs: list[tuple[Person, AccessEvent]] | None = None,
    ) -> bool:
        event = saga.access_event
        if not event or not event.person_id:
            return False
        committed = await commit_presence_for_event(session, event, log_prefix="movement_reconciliation")
        if not committed:
            return False
        if (
            presence_input_boolean_jobs is not None
            and not (saga.decision_payload or {}).get("historical_repair")
        ):
            person = await session.get(Person, event.person_id)
            if person:
                presence_input_boolean_jobs.append((person, event))
        return committed

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

    async def _publish_standalone_command_reconciled(
        self,
        command: GateCommandRecord,
        state: GateState,
    ) -> None:
        await event_bus.publish(
            "gate.command.reconciled",
            {
                "movement_saga": None,
                "gate_command_id": str(command.id),
                "gate_state": state.value,
                "detail": command.detail,
            },
        )

    async def _publish_saga_failed(self, saga: MovementSagaRecord, detail: str) -> None:
        await event_bus.publish(
            "movement_saga.failed",
            {"movement_saga": movement_saga_summary(saga), "detail": detail},
        )

    async def _publish_standalone_command_failed(self, command: GateCommandRecord, detail: str) -> None:
        await event_bus.publish(
            "gate.command.reconciliation_failed",
            {
                "gate_command_id": str(command.id),
                "gate_key": command.gate_key,
                "detail": detail,
            },
        )

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
            outcome="success" if success else "failed",
            level="info" if success else "warning",
            metadata={
                "source": command.source,
                "gate_key": command.gate_key,
                "state": command.state.value,
                "accepted": command.accepted,
                "detail": detail,
            },
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
