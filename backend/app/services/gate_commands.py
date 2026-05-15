import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Callable

from app.core.config import settings
from app.modules.gate.base import GateController, GateState
from app.modules.registry import UnsupportedModuleError, get_gate_controller
from app.services.movement_ledger import GateCommandLease, get_movement_ledger_repository


MECHANICALLY_OPENING_STATES = {GateState.OPEN, GateState.OPENING}


@dataclass(frozen=True)
class GateCommandIntent:
    reason: str
    source: str
    controller_name: str = settings.gate_controller
    bypass_schedule: bool = False
    action: str = "open"
    gate_key: str = "default"
    event_id: str | None = None
    movement_saga_id: str | None = None
    registration_number: str | None = None
    actor: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    intent_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True)
class GateCommandOutcome:
    intent: GateCommandIntent
    accepted: bool
    state: GateState
    detail: str | None
    started_at: datetime
    completed_at: datetime
    mechanically_confirmed: bool = False
    exception_class: str | None = None
    command_id: str | None = None
    reconciliation_required: bool | None = None

    @property
    def requires_reconciliation(self) -> bool:
        if self.reconciliation_required is not None:
            return self.reconciliation_required
        return self.accepted and not self.mechanically_confirmed

    def as_payload(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent.intent_id,
            "action": self.intent.action,
            "source": self.intent.source,
            "controller": self.intent.controller_name,
            "gate_key": self.intent.gate_key,
            "event_id": self.intent.event_id,
            "movement_saga_id": self.intent.movement_saga_id,
            "command_id": self.command_id,
            "registration_number": self.intent.registration_number,
            "accepted": self.accepted,
            "state": self.state.value,
            "detail": self.detail,
            "mechanically_confirmed": self.mechanically_confirmed,
            "requires_reconciliation": self.requires_reconciliation,
            "exception_class": self.exception_class,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "metadata": self.intent.metadata,
        }


class GateCommandCoordinator:
    """Serializes physical gate commands and records normalized outcomes."""

    def __init__(
        self,
        controller_factory: Callable[[str], GateController] = get_gate_controller,
        ledger: Any | None = None,
    ) -> None:
        self._controller_factory = controller_factory
        self._ledger = ledger if ledger is not None else get_movement_ledger_repository()

    async def execute_open(self, intent: GateCommandIntent) -> GateCommandOutcome:
        lease = await self._claim(intent)
        if lease.already_completed:
            return self._outcome_from_completed_record(intent, lease)

        started_at = lease.record.started_at or datetime.now(tz=UTC)
        try:
            controller = self._controller_factory(intent.controller_name)
            result = await controller.open_gate(
                intent.reason,
                bypass_schedule=intent.bypass_schedule,
            )
        except UnsupportedModuleError as exc:
            outcome = self._exception_outcome(intent, started_at, exc, GateState.UNKNOWN, command_id=str(lease.record.id))
        except Exception as exc:
            outcome = self._exception_outcome(intent, started_at, exc, GateState.FAULT, command_id=str(lease.record.id))
        else:
            completed_at = datetime.now(tz=UTC)
            outcome = GateCommandOutcome(
                intent=intent,
                accepted=result.accepted,
                state=result.state,
                detail=result.detail,
                mechanically_confirmed=bool(
                    result.accepted and result.state in MECHANICALLY_OPENING_STATES
                ),
                started_at=started_at,
                completed_at=completed_at,
                command_id=str(lease.record.id),
            )

        if self._ledger is not None and lease.record.id is not None:
            await self._ledger.complete_gate_command(
                lease.record.id,
                lease_token=lease.lease_token,
                accepted=outcome.accepted,
                gate_state=outcome.state.value,
                detail=outcome.detail,
                mechanically_confirmed=outcome.mechanically_confirmed,
                requires_reconciliation=outcome.requires_reconciliation,
                exception_class=outcome.exception_class,
            )
        return outcome

    async def _claim(self, intent: GateCommandIntent) -> GateCommandLease:
        if self._ledger is None:
            started_at = datetime.now(tz=UTC)
            return GateCommandLease(
                record=type(
                    "EphemeralGateCommand",
                    (),
                    {"id": None, "started_at": started_at, "state": None},
                )(),
                lease_token="",
            )
        return await self._ledger.claim_gate_command(intent)

    def _outcome_from_completed_record(
        self,
        intent: GateCommandIntent,
        lease: GateCommandLease,
    ) -> GateCommandOutcome:
        state = GateState.UNKNOWN
        try:
            state = GateState(str(lease.record.gate_state or "unknown"))
        except ValueError:
            pass
        started_at = lease.record.started_at or lease.record.created_at or datetime.now(tz=UTC)
        completed_at = lease.record.completed_at or lease.record.updated_at or started_at
        return GateCommandOutcome(
            intent=intent,
            accepted=bool(lease.record.accepted),
            state=state,
            detail=lease.record.detail,
            mechanically_confirmed=lease.record.mechanically_confirmed,
            exception_class=lease.record.exception_class,
            started_at=started_at,
            completed_at=completed_at,
            command_id=str(lease.record.id),
            reconciliation_required=lease.record.requires_reconciliation,
        )

    def _exception_outcome(
        self,
        intent: GateCommandIntent,
        started_at: datetime,
        exc: Exception,
        state: GateState,
        command_id: str | None = None,
    ) -> GateCommandOutcome:
        return GateCommandOutcome(
            intent=intent,
            accepted=False,
            state=state,
            detail=str(exc),
            exception_class=exc.__class__.__name__,
            started_at=started_at,
            completed_at=datetime.now(tz=UTC),
            command_id=command_id,
        )


@lru_cache
def get_gate_command_coordinator() -> GateCommandCoordinator:
    return GateCommandCoordinator()
