import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import AccessDeviceCommandRecord, AccessEvent, GateCommandRecord, MovementSagaRecord, MovementSessionRecord
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

MOVEMENT_RECOVERY_TRANSITIONS = {
    (MovementSagaState.FAILED, MovementSagaState.RECONCILIATION_REQUIRED),
    (MovementSagaState.RECONCILIATION_REQUIRED, MovementSagaState.COMPLETED),
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


class GateCommandLeaseLost(RuntimeError):
    """The durable row, rather than a late provider response, owns the outcome."""

    def __init__(self, record: GateCommandRecord) -> None:
        super().__init__("Gate command completion lost its active lease.")
        self.record = record


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


def unattempted_access_gate_evidence(
    row: GateCommandRecord, *, event_id: uuid.UUID, saga_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Recognize only the ledger's retained no-attempt receipt, not event metadata."""
    metadata = row.command_metadata
    if not isinstance(metadata, dict):
        return None
    proof = metadata.get("no_attempt_evidence")
    expected_intent = str(uuid.uuid5(event_id, "automatic-gate-open"))
    if not isinstance(proof, dict):
        return None
    try:
        deadline = datetime.fromisoformat(proof["dispatch_deadline"])
        checked_at = datetime.fromisoformat(proof["checked_at"])
        uuid.UUID(proof["reservation_id"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if (proof.get("kind") != "expired_unattempted_access_gate" or proof.get("version") != 1
            or proof.get("access_event_id") != str(event_id) or proof.get("movement_saga_id") != str(saga_id)
            or proof.get("intent_id") != expected_intent or metadata.get("intent_id") != expected_intent
            or metadata.get("expires_at") != proof["dispatch_deadline"] or metadata.get("target_plan") is not None
            or metadata.get("delivery") != "not_sent" or row.action != "open" or row.gate_key != "default"
            or row.source != "automatic_lpr_grant" or row.access_event_id != event_id or row.movement_saga_id != saga_id
            or row.idempotency_key != f"gate-command:open:default:event:{event_id}"
            or row.state != GateCommandState.REJECTED or row.accepted is not False
            or row.mechanically_confirmed or row.requires_reconciliation or row.started_at is not None
            or row.lease_token is not None or row.lease_expires_at is not None
            or deadline.tzinfo is None or checked_at.tzinfo is None or checked_at <= deadline
            or row.completed_at != checked_at):
        return None
    return proof


def movement_saga_summary(row: MovementSagaRecord | None) -> dict[str, Any] | None:
    if not row:
        return None
    loaded_values = getattr(row, "__dict__", {})
    updated_at = loaded_values.get("updated_at")
    return {
        "id": str(row.id),
        "state": row.state.value,
        "reconciliation_required": row.reconciliation_required,
        "gate_command_required": row.gate_command_required,
        "presence_committed": row.presence_committed,
        "admission_status": getattr(row, "admission_status", None),
        "admission_evidence": getattr(row, "admission_evidence", None),
        "failure_detail": row.failure_detail,
        "updated_at": updated_at.isoformat() if updated_at else None,
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
            created_at=now,
            updated_at=now,
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
        if (
            MOVEMENT_STATE_RANK[state] < MOVEMENT_STATE_RANK[row.state]
            and (row.state, state) not in MOVEMENT_RECOVERY_TRANSITIONS
        ):
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
        now = utc_now()
        row.state = state
        row.updated_at = now
        row.state_history = [
            *(row.state_history or []),
            self._history_item(state, at=now, detail=detail),
        ][-50:]
        await session.flush()
        return True

    async def lock_gate_operation_in_session(
        self, session: AsyncSession, idempotency_key: str, *, wait: bool = True,
    ) -> bool:
        """Acquire before origin/parent rows; the nonblocking mode keeps recovery fair."""
        operation = "pg_advisory_xact_lock" if wait else "pg_try_advisory_xact_lock"
        result = await session.scalar(text(f"SELECT {operation}(hashtext(:gate_key))"),
            {"gate_key": f"iacs:gate-operation:{idempotency_key}"})
        return True if wait else bool(result)

    async def record_unattempted_access_gate_in_session(
        self, session: AsyncSession, *, event: AccessEvent, saga: MovementSagaRecord,
        reservation_id: uuid.UUID, intent_id: uuid.UUID, dispatch_deadline: datetime,
    ) -> GateCommandRecord:
        """Terminalize an absent expired core operation, never a previously attempted one.

        Caller acquired the operation advisory lock BEFORE saga/event locks and
        validated retained reservation, ingest deadline and intact journal lineage.
        No target plan or physical observation is fabricated.
        """
        now = await session.scalar(select(func.clock_timestamp()))
        if (intent_id != uuid.uuid5(event.id, "automatic-gate-open") or saga.access_event_id != event.id
                or event.decision != AccessDecision.GRANTED or event.direction != AccessDirection.ENTRY
                or dispatch_deadline.tzinfo is None or now <= dispatch_deadline):
            raise ValueError("Only an overdue retained automatic entry can be terminalized without sending.")
        key = f"gate-command:open:default:event:{event.id}"
        parent = await session.scalar(select(GateCommandRecord.id).where(or_(
            GateCommandRecord.idempotency_key == key, GateCommandRecord.access_event_id == event.id,
            GateCommandRecord.movement_saga_id == saga.id,
            GateCommandRecord.command_metadata["intent_id"].astext == str(intent_id))))
        child = await session.scalar(select(AccessDeviceCommandRecord.id).where(
            AccessDeviceCommandRecord.intent_id == str(intent_id)))
        if parent is not None or child is not None:
            raise ValueError("Retained command history prevents an absence proof.")
        proof = {"kind": "expired_unattempted_access_gate", "version": 1,
            "reservation_id": str(reservation_id), "access_event_id": str(event.id),
            "movement_saga_id": str(saga.id), "intent_id": str(intent_id),
            "dispatch_deadline": dispatch_deadline.isoformat(), "checked_at": now.isoformat()}
        row = GateCommandRecord(idempotency_key=key, access_event_id=event.id, movement_saga_id=saga.id,
            action="open", source="automatic_lpr_grant", gate_key="default", controller="configured",
            reason="Automatic entry expired before any command was reserved.", actor="Access Event Automation",
            registration_number=event.registration_number, bypass_schedule=False,
            state=GateCommandState.REJECTED, accepted=False, gate_state="unknown", mechanically_confirmed=False,
            requires_reconciliation=False, completed_at=now,
            detail="Recognition deadline passed before command reservation; no gate request was sent.",
            command_metadata={"recovery_version": 2, "intent_id": str(intent_id), "target_plan": None,
                "expires_at": dispatch_deadline.isoformat(), "delivery": "not_sent",
                "admission_verified": False, "target_receipts": [], "no_attempt_evidence": proof})
        session.add(row)
        await session.flush()
        return row

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
                # Parent identity suppresses replay. Physical serialization belongs
                # to the per-device journal shared by gate and direct cover commands.
                await self.lock_gate_operation_in_session(session, idempotency_key)
                now = await session.scalar(select(func.clock_timestamp()))
                existing = await session.scalar(
                    select(GateCommandRecord).where(GateCommandRecord.idempotency_key == idempotency_key).with_for_update()
                )
                if existing and existing.state == GateCommandState.LEASED and (
                    not existing.lease_expires_at or existing.lease_expires_at <= now
                ):
                    self.mark_gate_command_uncertain(existing, at=now, detail="Gate command lease expired; delivery remains unknown.")
                if existing and existing.state in TERMINAL_GATE_COMMAND_STATES:
                    await session.commit()
                    return GateCommandLease(existing, existing.lease_token or "", already_completed=True)

                active = existing if existing and existing.state == GateCommandState.LEASED else None
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
        metadata: dict[str, Any] | None = None,
    ) -> GateCommandRecord:
        async with AsyncSessionLocal() as session:
            row = await session.get(GateCommandRecord, command_id, with_for_update=True)
            if not row:
                raise RuntimeError(f"Gate command {command_id} was not found.")
            now = await session.scalar(select(func.clock_timestamp()))
            if (not lease_token or row.lease_token != lease_token or row.state != GateCommandState.LEASED
                    or not row.lease_expires_at or row.lease_expires_at <= now):
                if row.state == GateCommandState.LEASED and row.lease_token == lease_token:
                    self.mark_gate_command_uncertain(row, at=now, detail="Gate command response arrived after its lease expired.")
                    await session.commit()
                raise GateCommandLeaseLost(row)
            row.accepted = accepted
            row.gate_state = gate_state
            row.detail = detail
            row.mechanically_confirmed = mechanically_confirmed
            row.requires_reconciliation = requires_reconciliation
            row.exception_class = exception_class
            if metadata:
                row.command_metadata = {**(row.command_metadata or {}),
                    **{key: value for key, value in metadata.items() if key != "no_attempt_evidence"}}
            row.completed_at = now
            row.lease_token = None
            row.lease_expires_at = None
            if requires_reconciliation:
                row.state = GateCommandState.RECONCILIATION_REQUIRED
            elif exception_class:
                row.state = GateCommandState.FAILED
            elif mechanically_confirmed:
                row.state = GateCommandState.RECONCILED
            elif accepted:
                row.state = GateCommandState.ACCEPTED
            else:
                row.state = GateCommandState.REJECTED
            await session.commit()
            await session.refresh(row)
            return row

    def mark_gate_command_uncertain(self, row: GateCommandRecord, *, at: datetime, detail: str) -> None:
        """Hold an expired/ambiguous attempt; expiry is not evidence of rejection."""
        row.state = GateCommandState.RECONCILIATION_REQUIRED
        row.accepted = None
        row.mechanically_confirmed = False
        row.requires_reconciliation = True
        row.gate_state = "unknown"
        row.detail = detail
        row.completed_at = at
        row.lease_token = None
        row.lease_expires_at = None
        row.command_metadata = {**(row.command_metadata or {}), "delivery": "unknown"}

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
        payload = {
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
        if (row.access_event_id and row.movement_saga_id and unattempted_access_gate_evidence(
                row, event_id=row.access_event_id, saga_id=row.movement_saga_id)):
            payload.update(delivery="not_sent", admission_verified=False, target_receipts=[])
        return payload

    async def upsert_movement_session(
        self,
        session: AsyncSession,
        *,
        session_key: str,
        source: str,
        registration_number: str,
        normalized_registration_number: str,
        direction: AccessDirection,
        decision: AccessDecision,
        started_at: datetime,
        last_seen_at: datetime,
        access_event_id: uuid.UUID | str | None = None,
        movement_saga_id: uuid.UUID | str | None = None,
        debounce_expires_at: datetime | None = None,
        gate_cycle_expires_at: datetime | None = None,
        idle_expires_at: datetime | None = None,
        camera_id: str | None = None,
        device_id: str | None = None,
        protect_event_ids: list[str] | set[str] | tuple[str, ...] | None = None,
        ocr_variants: list[str] | set[str] | tuple[str, ...] | None = None,
        last_gate_state: str | None = None,
    ) -> MovementSessionRecord:
        row = await session.scalar(
            select(MovementSessionRecord).where(MovementSessionRecord.session_key == session_key)
        )
        if not row:
            row = MovementSessionRecord(
                session_key=session_key,
                source=source,
                registration_number=registration_number,
                normalized_registration_number=normalized_registration_number,
                direction=direction,
                decision=decision,
                started_at=started_at,
                last_seen_at=last_seen_at,
            )
            session.add(row)
        row.access_event_id = self._uuid_or_none(access_event_id)
        row.movement_saga_id = self._uuid_or_none(movement_saga_id)
        row.source = source
        row.registration_number = registration_number
        row.normalized_registration_number = normalized_registration_number
        row.direction = direction
        row.decision = decision
        row.started_at = min(row.started_at or started_at, started_at)
        row.last_seen_at = max(row.last_seen_at or last_seen_at, last_seen_at)
        row.debounce_expires_at = debounce_expires_at
        row.gate_cycle_expires_at = gate_cycle_expires_at
        row.idle_expires_at = idle_expires_at
        row.camera_id = camera_id or row.camera_id
        row.device_id = device_id or row.device_id
        row.protect_event_ids = self._merged_strings(row.protect_event_ids, protect_event_ids)
        row.ocr_variants = self._merged_strings(row.ocr_variants, ocr_variants)
        row.last_gate_state = last_gate_state
        row.is_active = True
        await session.flush()
        return row

    async def movement_sessions_for_exact_suppression(
        self,
        session: AsyncSession,
        *,
        source: str,
        captured_at: datetime,
        limit: int = 100,
    ) -> list[MovementSessionRecord]:
        return list(
            (
                await session.scalars(
                    select(MovementSessionRecord)
                    .where(
                        MovementSessionRecord.source == source,
                        MovementSessionRecord.started_at <= captured_at,
                        or_(
                            MovementSessionRecord.debounce_expires_at >= captured_at,
                            MovementSessionRecord.gate_cycle_expires_at >= captured_at,
                        ),
                    )
                    .order_by(MovementSessionRecord.started_at.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def movement_sessions_for_active_read(
        self,
        session: AsyncSession,
        *,
        source: str | None = None,
        captured_at: datetime,
        lookup_horizon: timedelta,
        limit: int = 100,
    ) -> list[MovementSessionRecord]:
        conditions = [
            MovementSessionRecord.is_active.is_(True),
            MovementSessionRecord.started_at <= captured_at,
            MovementSessionRecord.last_seen_at >= captured_at - lookup_horizon,
        ]
        if source is not None:
            conditions.append(MovementSessionRecord.source == source)
        return list(
            (
                await session.scalars(
                    select(MovementSessionRecord)
                    .where(*conditions)
                    .order_by(MovementSessionRecord.last_seen_at.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def record_movement_session_suppression(
        self,
        session: AsyncSession,
        row: MovementSessionRecord,
        *,
        read_captured_at: datetime,
        idle_expires_at: datetime | None,
        protect_event_ids: list[str] | set[str] | tuple[str, ...] | None,
        ocr_variants: list[str] | set[str] | tuple[str, ...] | None,
        last_gate_state: str | None,
        reason: str,
        matched_by: str,
        presence_evidence: dict[str, Any] | None,
        suppressed_read_payload: dict[str, Any],
    ) -> None:
        row.last_seen_at = max(row.last_seen_at, read_captured_at)
        row.idle_expires_at = max(
            [value for value in (row.idle_expires_at, idle_expires_at) if value],
            default=idle_expires_at,
        )
        row.protect_event_ids = self._merged_strings(row.protect_event_ids, protect_event_ids)
        row.ocr_variants = self._merged_strings(row.ocr_variants, ocr_variants)
        row.last_gate_state = last_gate_state
        row.suppressed_read_count = int(row.suppressed_read_count or 0) + 1
        row.last_suppressed_reason = reason
        row.last_matched_by = matched_by
        row.last_presence_evidence = presence_evidence
        suppressed_reads = list(row.suppressed_reads or [])
        suppressed_reads.append(suppressed_read_payload)
        row.suppressed_reads = suppressed_reads[-20:]
        await session.flush()

    def _new_gate_command_record(self, intent: Any, *, idempotency_key: str) -> GateCommandRecord:
        event_id = self._uuid_or_none(getattr(intent, "event_id", None))
        movement_saga_id = self._uuid_or_none(getattr(intent, "movement_saga_id", None))
        metadata = {key: value for key, value in (getattr(intent, "metadata", None) or {}).items()
                    if key != "no_attempt_evidence"}
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
            command_metadata={**metadata, "recovery_version": 2,
                              "target_plan": getattr(intent, "target_plan", None),
                              "expires_at": (getattr(intent, "expires_at", None).isoformat()
                                             if getattr(intent, "expires_at", None) else None),
                              "intent_id": str(getattr(intent, "intent_id", "") or "")},
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

    def _merged_strings(self, *values: Any) -> list[str]:
        merged: set[str] = set()
        for value in values:
            candidates: list[Any]
            if isinstance(value, str):
                candidates = [value]
            elif isinstance(value, (list, tuple, set)):
                candidates = list(value)
            else:
                candidates = []
            for candidate in candidates:
                text = str(candidate or "").strip()
                if text:
                    merged.add(text)
        return sorted(merged)


_repository = MovementLedgerRepository()


def get_movement_ledger_repository() -> MovementLedgerRepository:
    return _repository
