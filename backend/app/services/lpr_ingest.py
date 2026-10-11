import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import LprIngestEvent
from app.models.access_pipeline import LPR_READY_ORDINAL_SEQUENCE
from app.modules.gate.base import GateState
from app.modules.lpr.base import PlateRead
from app.services.access.completion_database import access_session
from app.services.access.pipeline_store import database_now
from app.services.movement.sessions import datetime_from_payload

LPR_INGEST_PENDING_BATCH_SIZE = 50
LPR_OBSERVATION_LEASE_SECONDS = 20
LPR_GATE_CAPTURE_TIMEOUT_SECONDS = 10
LPR_INGEST_STATUS_PENDING = "pending"


class LprIngestRepository:
    def __init__(self, session_factory: Callable[[], AsyncSession] = AsyncSessionLocal) -> None:
        self.session_factory = session_factory

    async def persist_read(
        self,
        read: PlateRead,
        *,
        received_at: datetime,
        idempotency_key: str,
        normalized_payload: dict[str, Any],
    ) -> tuple[LprIngestEvent, bool]:
        # The unique insert winner alone owns capture. Neither a duplicate nor
        # recovery can replace input, receipt, first observation or terminal state.
        async with access_session(self.session_factory) as session:
            now = await database_now(session)
            identity, token = uuid.uuid4(), uuid.uuid4()
            inserted = await session.scalar(
                insert(LprIngestEvent)
                .values(id=identity, idempotency_key=idempotency_key, source=read.source,
                    registration_number=read.registration_number, captured_at=read.captured_at,
                    received_at=received_at, normalized_payload=normalized_payload,
                    status=LPR_INGEST_STATUS_PENDING, observation_state="observing",
                    capture_token=token, capture_until=now + timedelta(seconds=LPR_OBSERVATION_LEASE_SECONDS),
                    attempt_count=0)
                .on_conflict_do_nothing(index_elements=[LprIngestEvent.idempotency_key])
                .returning(LprIngestEvent.id)
            )
            row = await session.scalar(select(LprIngestEvent).where(
                LprIngestEvent.idempotency_key == idempotency_key))
            if row is None:
                raise RuntimeError("Durable LPR insert was not visible")
            await session.commit()
            return row, inserted is not None

    async def complete_observation(
        self, ingest_id: uuid.UUID, token: uuid.UUID, observation: dict[str, Any]
    ) -> LprIngestEvent | None:
        """Publish a first observation under the fixed capture lease and ready order."""
        state = observation.get("state")
        if not isinstance(state, str) or state not in {value.value for value in GateState} or datetime_from_payload(observation.get("observed_at")) is None:
            raise ValueError("A canonical original gate observation is required")
        async with access_session(self.session_factory) as session:
            # This lock is held only through publication commit. Window/domain
            # transactions never acquire it, so they cannot spend capture leases.
            await session.execute(select(func.pg_advisory_xact_lock(
                func.hashtextextended("iacs:lpr:ready-publication", 0))))
            row = await session.get(LprIngestEvent, ingest_id, with_for_update=True, populate_existing=True)
            now = await database_now(session)
            if (row is None or row.observation_state != "observing" or row.capture_token != token
                or row.capture_until is None or row.capture_until <= now):
                return None
            capture_until = row.capture_until
            row.original_observation = dict(observation)
            row.observation_completed_at, row.observation_state = now, "ready"
            ordinal = await session.scalar(select(LPR_READY_ORDINAL_SEQUENCE.next_value()))
            if not isinstance(ordinal, int):
                raise TypeError("Ready ordinal allocator unavailable")
            row.ready_ordinal = ordinal
            row.capture_token, row.capture_until = None, None
            await session.flush()
            if capture_until <= await database_now(session):
                await session.rollback()
                return None
            await session.commit()
            return row

    async def suppress_capture(self, ingest_id: uuid.UUID, token: uuid.UUID, reason: str) -> bool:
        async with access_session(self.session_factory) as session:
            row = await session.get(LprIngestEvent, ingest_id, with_for_update=True, populate_existing=True)
            now = await database_now(session)
            if (row is None or row.observation_state != "observing" or row.capture_token != token
                or row.capture_until is None or row.capture_until <= now):
                return False
            row.status, row.observation_state, row.terminal_reason = "skipped", "suppressed", reason
            row.processed_at, row.capture_token, row.capture_until = now, None, None
            await session.commit()
            return True

    async def hold_expired_observations(self) -> int:
        """Live capture is untouched; abandoned capture is held without recapture."""
        async with access_session(self.session_factory) as session:
            rows = list(await session.scalars(select(LprIngestEvent).where(
                LprIngestEvent.observation_state == "observing",
                LprIngestEvent.capture_until <= await database_now(session))
                .order_by(LprIngestEvent.capture_until, LprIngestEvent.id)
                .limit(LPR_INGEST_PENDING_BATCH_SIZE).with_for_update(skip_locked=True)))
            now = await database_now(session)
            for row in rows:
                row.status, row.observation_state = "held", "held"
                row.terminal_reason, row.processed_at = "original_gate_observation_unavailable", now
                row.capture_token, row.capture_until = None, None
            await session.commit()
            return len(rows)
