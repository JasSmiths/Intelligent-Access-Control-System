import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import GateCommandRecord, MovementSagaRecord
from app.models.enums import AccessDecision, AccessDirection, GateCommandState, MovementSagaState


MOVEMENT_STATE_RANK = {
    MovementSagaState.OBSERVED: 10,
    MovementSagaState.DIRECTION_RESOLVED: 20,
    MovementSagaState.PHYSICAL_COMMAND_PENDING: 30,
    MovementSagaState.PHYSICAL_COMMAND_ACCEPTED: 40,
    MovementSagaState.PRESENCE_COMMITTED: 50,
    MovementSagaState.COMPLETED: 60,
    MovementSagaState.RECONCILIATION_REQUIRED: 70,
    MovementSagaState.FAILED: 80,
    MovementSagaState.SUPPRESSED: 80,
}

TERMINAL_GATE_COMMAND_STATES = {
    GateCommandState.ACCEPTED,
    GateCommandState.REJECTED,
    GateCommandState.FAILED,
    GateCommandState.RECONCILIATION_REQUIRED,
    GateCommandState.RECONCILED,
}


@dataclass(frozen=True)
class GateCommandLease:
    record: GateCommandRecord
    lease_token: str
    already_completed: bool = False


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def gate_command_idempotency_key(intent: Any) -> str:
    explicit = str(getattr(intent, "idempotency_key", "") or "").strip()
    if explicit:
        return explicit
    event_id = str(getattr(intent, "event_id", "") or "").strip()
    action = str(getattr(intent, "action", "open") or "open")
    gate_key = str(getattr(intent, "gate_key", "default") or "default")
    if event_id:
        return f"gate-command:{action}:{gate_key}:event:{event_id}"
    intent_id = str(getattr(intent, "intent_id", "") or uuid.uuid4())
    return f"gate-command:{action}:{gate_key}:intent:{intent_id}"


def movement_saga_summary(row: MovementSagaRecord | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": str(row.id),
        "state": row.state.value,
        "reconciliation_required": row.reconciliation_required,
        "gate_command_required": row.gate_command_required,
        "presence_committed": row.presence_committed,
        "failure_detail": row.failure_detail,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class MovementLedgerRepository:
    async def create_movement_saga(
        self,
        session: AsyncSession,
        *,
        idempotency_key: str,
        source: str,
        occurred_at: datetime,
        registration_number: str | None = None,
        person_id: uuid.UUID | None = None,
        vehicle_id: uuid.UUID | None = None,
        direction: AccessDirection | None = None,
        decision: AccessDecision | None = None,
        state: MovementSagaState = MovementSagaState.OBSERVED,
        intent_payload: dict[str, Any] | None = None,
        decision_payload: dict[str, Any] | None = None,
    ) -> MovementSagaRecord:
        existing = await self.movement_saga_by_idempotency_key(session, idempotency_key)
        if existing:
            return existing
        now = utc_now()
        row = MovementSagaRecord(
            idempotency_key=idempotency_key,
            source=source,
            occurred_at=occurred_at,
            registration_number=registration_number,
            person_id=person_id,
            vehicle_id=vehicle_id,
            direction=direction,
            decision=decision,
            state=state,
            intent_payload=intent_payload or {},
            decision_payload=decision_payload or {},
            state_history=[self._history_item(state, at=now, detail="created")],
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            existing = await self.movement_saga_by_idempotency_key(session, idempotency_key)
            if existing:
                return existing
            raise
        return row

    async def movement_saga_by_idempotency_key(
        self,
        session: AsyncSession,
        idempotency_key: str,
    ) -> MovementSagaRecord | None:
        return await session.scalar(
            select(MovementSagaRecord).where(MovementSagaRecord.idempotency_key == idempotency_key)
        )

    async def transition_movement_saga(
        self,
        session: AsyncSession,
        row: MovementSagaRecord,
        state: MovementSagaState,
        *,
        detail: str | None = None,
        access_event_id: uuid.UUID | None = None,
        gate_command_required: bool | None = None,
        presence_committed: bool | None = None,
        reconciliation_required: bool | None = None,
        failure_detail: str | None = None,
        decision_payload: dict[str, Any] | None = None,
    ) -> bool:
        if MOVEMENT_STATE_RANK[state] < MOVEMENT_STATE_RANK[row.state]:
            return False
        if access_event_id is not None:
            row.access_event_id = access_event_id
        if gate_command_required is not None:
            row.gate_command_required = gate_command_required
        if presence_committed is not None:
            row.presence_committed = presence_committed
        if reconciliation_required is not None:
            row.reconciliation_required = reconciliation_required
        if failure_detail is not None:
            row.failure_detail = failure_detail
        if decision_payload is not None:
            row.decision_payload = decision_payload
        row.state = state
        row.state_history = [
            *(row.state_history or []),
            self._history_item(state, detail=detail),
        ][-50:]
        await session.flush()
        return True

    async def claim_gate_command(
        self,
        intent: Any,
        *,
        lease_seconds: float = 60.0,
        wait_seconds: float = 10.0,
        poll_seconds: float = 0.1,
    ) -> GateCommandLease:
        started = utc_now()
        idempotency_key = gate_command_idempotency_key(intent)
        gate_key = str(getattr(intent, "gate_key", "default") or "default")
        while True:
            async with AsyncSessionLocal() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:gate_key))"), {"gate_key": gate_key})
                now = utc_now()
                existing = await session.scalar(
                    select(GateCommandRecord).where(GateCommandRecord.idempotency_key == idempotency_key)
                )
                if existing and existing.state in TERMINAL_GATE_COMMAND_STATES:
                    await session.commit()
                    return GateCommandLease(existing, existing.lease_token or "", already_completed=True)

                existing_in_flight = bool(
                    existing
                    and existing.state == GateCommandState.LEASED
                    and existing.lease_expires_at
                    and existing.lease_expires_at > now
                )
                if not existing_in_flight:
                    active = await session.scalar(
                        select(GateCommandRecord)
                        .where(
                            GateCommandRecord.gate_key == gate_key,
                            GateCommandRecord.state == GateCommandState.LEASED,
                            GateCommandRecord.lease_expires_at.is_not(None),
                            GateCommandRecord.lease_expires_at > now,
                        )
                        .order_by(GateCommandRecord.lease_expires_at.asc())
                        .limit(1)
                    )
                else:
                    active = existing
                if not active:
                    record = existing or self._new_gate_command_record(intent, idempotency_key=idempotency_key)
                    if not existing:
                        session.add(record)
                    lease_token = uuid.uuid4().hex
                    record.state = GateCommandState.LEASED
                    record.lease_token = lease_token
                    record.leased_at = now
                    record.lease_expires_at = now + timedelta(seconds=lease_seconds)
                    record.started_at = now
                    await session.commit()
                    await session.refresh(record)
                    return GateCommandLease(record, lease_token)

            if (utc_now() - started).total_seconds() >= wait_seconds:
                raise TimeoutError(f"Timed out waiting for gate command lease for {gate_key}.")
            await asyncio.sleep(poll_seconds)

    async def complete_gate_command(
        self,
        command_id: uuid.UUID,
        *,
        lease_token: str,
        accepted: bool,
        gate_state: str,
        detail: str | None,
        mechanically_confirmed: bool,
        requires_reconciliation: bool,
        exception_class: str | None = None,
    ) -> GateCommandRecord:
        async with AsyncSessionLocal() as session:
            row = await session.get(GateCommandRecord, command_id)
            if not row:
                raise RuntimeError(f"Gate command {command_id} was not found.")
            if row.lease_token and row.lease_token != lease_token:
                raise RuntimeError(f"Gate command {command_id} lease token did not match.")
            row.accepted = accepted
            row.gate_state = gate_state
            row.detail = detail
            row.mechanically_confirmed = mechanically_confirmed
            row.requires_reconciliation = requires_reconciliation
            row.exception_class = exception_class
            row.completed_at = utc_now()
            row.lease_token = None
            row.lease_expires_at = None
            if exception_class:
                row.state = GateCommandState.FAILED
            elif accepted and requires_reconciliation:
                row.state = GateCommandState.RECONCILIATION_REQUIRED
            elif accepted:
                row.state = GateCommandState.ACCEPTED
            else:
                row.state = GateCommandState.REJECTED
            await session.commit()
            await session.refresh(row)
            return row

    async def mark_gate_command_reconciled(
        self,
        session: AsyncSession,
        row: GateCommandRecord,
        *,
        detail: str,
        success: bool,
    ) -> None:
        row.requires_reconciliation = False
        row.mechanically_confirmed = success
        if success:
            row.accepted = True
        row.detail = detail
        row.state = GateCommandState.RECONCILED if success else GateCommandState.FAILED
        row.completed_at = utc_now()
        await session.flush()

    def outcome_payload_from_record(self, row: GateCommandRecord) -> dict[str, Any]:
        return {
            "command_id": str(row.id),
            "movement_saga_id": str(row.movement_saga_id) if row.movement_saga_id else None,
            "accepted": bool(row.accepted),
            "state": row.gate_state or "unknown",
            "detail": row.detail,
            "mechanically_confirmed": row.mechanically_confirmed,
            "requires_reconciliation": row.requires_reconciliation,
            "exception_class": row.exception_class,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }

    def _new_gate_command_record(self, intent: Any, *, idempotency_key: str) -> GateCommandRecord:
        event_id = self._uuid_or_none(getattr(intent, "event_id", None))
        movement_saga_id = self._uuid_or_none(getattr(intent, "movement_saga_id", None))
        return GateCommandRecord(
            idempotency_key=idempotency_key,
            movement_saga_id=movement_saga_id,
            access_event_id=event_id,
            action=str(getattr(intent, "action", "open") or "open"),
            source=str(getattr(intent, "source", "") or "unknown"),
            gate_key=str(getattr(intent, "gate_key", "default") or "default"),
            controller=str(getattr(intent, "controller_name", "") or "unknown"),
            reason=str(getattr(intent, "reason", "") or "Gate command"),
            actor=getattr(intent, "actor", None),
            registration_number=getattr(intent, "registration_number", None),
            bypass_schedule=bool(getattr(intent, "bypass_schedule", False)),
            command_metadata=getattr(intent, "metadata", None) or {},
        )

    def _history_item(
        self,
        state: MovementSagaState,
        *,
        at: datetime | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        return {
            "state": state.value,
            "at": (at or utc_now()).isoformat(),
            "detail": detail,
        }

    def _uuid_or_none(self, value: Any) -> uuid.UUID | None:
        if not value:
            return None
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError):
            return None


_repository = MovementLedgerRepository()


def get_movement_ledger_repository() -> MovementLedgerRepository:
    return _repository
