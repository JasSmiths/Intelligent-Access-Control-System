import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.recovery_hold import require_effects_enabled
from app.modules.gate.base import GateCommandDelivery, GateCommandContext, GateCommandNotSent, GateController, GateState
from app.modules.registry import UnsupportedModuleError, get_gate_controller
from app.services.movement_ledger import GateCommandLease, GateCommandLeaseLost, gate_command_idempotency_key, get_movement_ledger_repository


MECHANICALLY_OPENING_STATES = {GateState.OPEN, GateState.OPENING}


@dataclass(frozen=True)
class GateCommandIntent:
    reason: str
    source: str
    controller_name: str = "configured"
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
    target_device_key: str | None = None
    target_plan: dict[str, Any] | None = None
    expires_at: datetime | None = None
    require_admission: bool = True
    automatic_entry_policy: bool = False
    actor_user_id: str | None = None
    auth_version: int | None = None
    authorize_dispatch: Callable[[AsyncSession], Awaitable[None]] | None = None


@dataclass(frozen=True)
class GateCommandOutcome:
    intent: GateCommandIntent
    accepted: bool
    state: GateState
    detail: str | None
    started_at: datetime
    completed_at: datetime
    mechanically_confirmed: bool = False
    admission_verified: bool = False
    target_receipts: list[dict[str, Any]] = field(default_factory=list)
    exception_class: str | None = None
    command_id: str | None = None
    reconciliation_required: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    delivery: GateCommandDelivery | None = None

    def __post_init__(self) -> None:
        if self.delivery is None:
            object.__setattr__(self, "delivery", GateCommandDelivery.ACCEPTED if self.accepted else GateCommandDelivery.REJECTED)

    @property
    def requires_reconciliation(self) -> bool:
        if self.reconciliation_required is not None:
            return self.reconciliation_required
        return self.delivery == GateCommandDelivery.UNKNOWN or (self.accepted and not self.mechanically_confirmed)

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
            "delivery": self.delivery,
            "state": self.state.value,
            "detail": self.detail,
            "mechanically_confirmed": self.mechanically_confirmed,
            "admission_verified": self.admission_verified,
            "target_receipts": self.target_receipts,
            "requires_reconciliation": self.requires_reconciliation,
            "exception_class": self.exception_class,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "metadata": {**self.intent.metadata, **self.metadata},
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

    async def preview_manual_gate_open(self) -> dict[str, Any]:
        """Preview the same configured owner used for manual command execution."""
        return await self._controller_factory("configured").preview_manual_gate_open()

    async def execute_open(self, intent: GateCommandIntent) -> GateCommandOutcome:
        require_effects_enabled()
        lease = await self._claim(intent)
        if lease.already_completed:
            return self._outcome_from_completed_record(intent, lease)

        started_at = lease.record.started_at or datetime.now(tz=UTC)
        try:
            try:
                controller = self._controller_factory(intent.controller_name)
            except UnsupportedModuleError as exc:
                raise GateCommandNotSent(str(exc)) from exc
            result = await controller.open_gate(
                intent.reason,
                bypass_schedule=intent.bypass_schedule,
                command_context=GateCommandContext(
                    command_id=str(lease.record.id) if lease.record.id else None, lease_token=lease.lease_token,
                    intent_id=intent.intent_id, idempotency_key=gate_command_idempotency_key(intent),
                    target_device_key=intent.target_device_key, target_plan=intent.target_plan,
                    expires_at=intent.expires_at, require_admission=intent.require_admission,
                    automatic_entry_policy=intent.automatic_entry_policy,
                    actor_user_id=intent.actor_user_id, auth_version=intent.auth_version,
                    authorize_dispatch=intent.authorize_dispatch),
            )
        except GateCommandNotSent as exc:
            outcome = self._exception_outcome(intent, started_at, exc, GateState.UNKNOWN,
                                              command_id=str(lease.record.id), delivery=GateCommandDelivery.NOT_SENT)
        except Exception as exc:
            outcome = self._exception_outcome(intent, started_at, exc, GateState.UNKNOWN, command_id=str(lease.record.id))
            outcome = await self._retain_exception_receipts(outcome)
        else:
            completed_at = datetime.now(tz=UTC)
            outcome = GateCommandOutcome(
                intent=intent,
                accepted=result.accepted,
                state=result.state,
                detail=result.detail,
                metadata={**(result.metadata or {}), "delivery": result.delivery},
                delivery=result.delivery,
                reconciliation_required=((result.metadata or {})["requires_reconciliation"]
                                         if "requires_reconciliation" in (result.metadata or {}) else None),
                mechanically_confirmed=bool((result.metadata or {}).get("mechanically_confirmed",
                    result.accepted and result.state in MECHANICALLY_OPENING_STATES)),
                admission_verified=bool((result.metadata or {}).get("admission_verified")),
                target_receipts=list((result.metadata or {}).get("target_receipts", [])),
                started_at=started_at,
                completed_at=completed_at,
                command_id=str(lease.record.id),
            )

        if self._ledger is not None and lease.record.id is not None:
            try:
                await self._ledger.complete_gate_command(
                    lease.record.id, lease_token=lease.lease_token, accepted=outcome.accepted,
                    gate_state=outcome.state.value, detail=outcome.detail,
                    mechanically_confirmed=outcome.mechanically_confirmed,
                    requires_reconciliation=outcome.requires_reconciliation,
                    exception_class=outcome.exception_class,
                    metadata={**outcome.metadata, "delivery": outcome.delivery},
                )
            except GateCommandLeaseLost as exc:
                return self._outcome_from_completed_record(intent, GateCommandLease(exc.record, "", already_completed=True))
        return outcome

    async def _retain_exception_receipts(self, outcome: GateCommandOutcome) -> GateCommandOutcome:
        """A controller exception cannot erase children that already committed."""
        if outcome.command_id is None:
            return outcome
        try:
            payload = await self.get_receipt(outcome.command_id)
            if not payload or not payload.get("target_receipts"):
                return outcome
            projection = {key: payload[key] for key in (
                "accepted", "state", "delivery", "mechanically_confirmed", "admission_verified",
                "target_receipts", "requires_reconciliation")}
            return replace(outcome, accepted=projection["accepted"], state=GateState(projection["state"]),
                delivery=GateCommandDelivery(projection["delivery"]),
                mechanically_confirmed=projection["mechanically_confirmed"],
                admission_verified=projection["admission_verified"], target_receipts=projection["target_receipts"],
                reconciliation_required=projection["requires_reconciliation"],
                metadata={**outcome.metadata, **projection})
        except Exception:
            # The durable journal remains authoritative if the database itself is
            # unavailable. An unproven result stays unknown and is never retried.
            return outcome

    async def get_receipt(self, command_id: uuid.UUID | str | None = None, *,
                          intent_id: str | None = None) -> dict[str, Any] | None:
        from sqlalchemy import select

        from app.db.session import AsyncSessionLocal
        from app.models import GateCommandRecord
        if (command_id is None) == (intent_id is None):
            raise ValueError("Specify exactly one command ID or original intent ID.")
        async with AsyncSessionLocal() as session:
            if command_id is not None:
                row = await session.get(GateCommandRecord, uuid.UUID(str(command_id)))
            else:
                row = await session.scalar(select(GateCommandRecord).where(
                    GateCommandRecord.command_metadata["intent_id"].astext == intent_id)
                    .order_by(GateCommandRecord.created_at).limit(1))
            if row is None:
                return None
            return await self._receipt_for_row(session, row)

    async def _receipt_for_row(self, session: AsyncSession, row: Any) -> dict[str, Any]:
        from app.services.access_device_commands import AccessDeviceCommandJournal

        projection = await AccessDeviceCommandJournal().gate_command_projection(session, row)
        payload = self._ledger.outcome_payload_from_record(row)
        return {**payload, **(projection or {}), "intent_id": (row.command_metadata or {}).get("intent_id"),
                "command_status": row.state.value}

    async def list_receipts(self, *, limit: int = 25, before_id: uuid.UUID | None = None) -> dict[str, Any]:
        from sqlalchemy import select, tuple_

        from app.db.session import AsyncSessionLocal
        from app.models import GateCommandRecord

        if not 1 <= limit <= 100:
            raise ValueError("Receipt page size must be between 1 and 100.")
        async with AsyncSessionLocal() as session:
            statement = select(GateCommandRecord)
            if before_id is not None:
                cursor = await session.get(GateCommandRecord, before_id)
                if cursor is None:
                    raise ValueError("The command receipt cursor is no longer available.")
                statement = statement.where(tuple_(GateCommandRecord.created_at, GateCommandRecord.id)
                                            < tuple_(cursor.created_at, cursor.id))
            rows = list((await session.scalars(statement.order_by(
                GateCommandRecord.created_at.desc(), GateCommandRecord.id.desc()).limit(limit + 1))).all())
            return {"items": [await self._receipt_for_row(session, row) for row in rows[:limit]],
                    "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None}

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
        metadata = getattr(lease.record, "command_metadata", None) or {}
        try:
            delivery = GateCommandDelivery(metadata.get("delivery"))
        except (TypeError, ValueError):
            delivery = (GateCommandDelivery.ACCEPTED if lease.record.accepted
                        else GateCommandDelivery.UNKNOWN if lease.record.requires_reconciliation
                        else GateCommandDelivery.REJECTED)
        return GateCommandOutcome(
            intent=intent,
            accepted=bool(lease.record.accepted),
            state=state,
            detail=lease.record.detail,
            mechanically_confirmed=lease.record.mechanically_confirmed,
            admission_verified=bool(metadata.get("admission_verified")),
            target_receipts=list(metadata.get("target_receipts", [])),
            exception_class=lease.record.exception_class,
            started_at=started_at,
            completed_at=completed_at,
            command_id=str(lease.record.id),
            reconciliation_required=lease.record.requires_reconciliation,
            metadata=metadata,
            delivery=delivery,
        )

    def _exception_outcome(
        self,
        intent: GateCommandIntent,
        started_at: datetime,
        exc: Exception,
        state: GateState,
        command_id: str | None = None,
        delivery: GateCommandDelivery = GateCommandDelivery.UNKNOWN,
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
            delivery=delivery,
        )


@lru_cache
def get_gate_command_coordinator() -> GateCommandCoordinator:
    return GateCommandCoordinator()
