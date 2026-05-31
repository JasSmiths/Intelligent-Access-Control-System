import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import LprIngestEvent
from app.modules.lpr.base import PlateRead

LPR_INGEST_PENDING_BATCH_SIZE = 50
LPR_INGEST_PROCESSING_STALE_SECONDS = 300.0
LPR_INGEST_STATUS_PENDING = "pending"
LPR_INGEST_STATUS_PROCESSING = "processing"
LPR_INGEST_STATUS_SUCCEEDED = "succeeded"
LPR_INGEST_STATUS_FAILED = "failed"
LPR_INGEST_STATUS_SKIPPED = "skipped"


class LprIngestRepository:
    def __init__(self, session_factory=AsyncSessionLocal) -> None:
        self.session_factory = session_factory

    async def persist_read(
        self,
        read: PlateRead,
        *,
        received_at: datetime,
        idempotency_key: str,
        normalized_payload: dict[str, Any],
    ) -> tuple[LprIngestEvent, bool]:
        async with self.session_factory() as session:
            row = await session.scalar(
                select(LprIngestEvent).where(LprIngestEvent.idempotency_key == idempotency_key)
            )
            if row:
                row.normalized_payload = normalized_payload
                if row.status == LPR_INGEST_STATUS_FAILED:
                    row.status = LPR_INGEST_STATUS_PENDING
                    row.last_error = None
                    row.processing_started_at = None
                    row.processed_at = None
                    await session.commit()
                    return row, True
                await session.commit()
                return row, False

            row = LprIngestEvent(
                idempotency_key=idempotency_key,
                source=read.source,
                registration_number=read.registration_number,
                captured_at=read.captured_at,
                received_at=received_at,
                normalized_payload=normalized_payload,
                status=LPR_INGEST_STATUS_PENDING,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(LprIngestEvent).where(LprIngestEvent.idempotency_key == idempotency_key)
                )
                if existing:
                    return existing, False
                raise
            return row, True

    async def pending_rows(self) -> list[LprIngestEvent]:
        stale_before = datetime.now(tz=UTC) - timedelta(seconds=LPR_INGEST_PROCESSING_STALE_SECONDS)
        async with self.session_factory() as session:
            return list(
                (
                    await session.scalars(
                        select(LprIngestEvent)
                        .where(
                            or_(
                                LprIngestEvent.status == LPR_INGEST_STATUS_PENDING,
                                (
                                    (LprIngestEvent.status == LPR_INGEST_STATUS_PROCESSING)
                                    & (LprIngestEvent.processing_started_at <= stale_before)
                                ),
                            )
                        )
                        .order_by(LprIngestEvent.received_at.asc())
                        .limit(LPR_INGEST_PENDING_BATCH_SIZE)
                    )
                ).all()
            )

    async def claim_for_processing(self, ingest_id: uuid.UUID) -> bool:
        now = datetime.now(tz=UTC)
        stale_before = now - timedelta(seconds=LPR_INGEST_PROCESSING_STALE_SECONDS)
        async with self.session_factory() as session:
            row = await session.get(LprIngestEvent, ingest_id)
            if row is None:
                return True
            if row.status in {
                LPR_INGEST_STATUS_SUCCEEDED,
                LPR_INGEST_STATUS_SKIPPED,
                LPR_INGEST_STATUS_FAILED,
            }:
                return False
            if (
                row.status == LPR_INGEST_STATUS_PROCESSING
                and row.processing_started_at is not None
                and row.processing_started_at > stale_before
            ):
                return False
            row.status = LPR_INGEST_STATUS_PROCESSING
            row.processing_started_at = now
            row.attempt_count = int(row.attempt_count or 0) + 1
            row.last_error = None
            await session.commit()
        return True

    async def mark_ids_succeeded(
        self,
        ids: list[uuid.UUID],
        *,
        access_event_id: uuid.UUID | None,
        movement_saga_id: uuid.UUID | None,
    ) -> None:
        async with self.session_factory() as session:
            await self.mark_ids_succeeded_in_session(
                session,
                ids,
                access_event_id=access_event_id,
                movement_saga_id=movement_saga_id,
            )
            await session.commit()

    async def mark_ids_succeeded_in_session(
        self,
        session: AsyncSession,
        ids: list[uuid.UUID],
        *,
        access_event_id: uuid.UUID | None,
        movement_saga_id: uuid.UUID | None,
    ) -> None:
        if not ids:
            return
        now = datetime.now(tz=UTC)
        rows = (
            await session.scalars(
                select(LprIngestEvent).where(LprIngestEvent.id.in_(ids))
            )
        ).all()
        for row in rows:
            row.status = LPR_INGEST_STATUS_SUCCEEDED
            row.access_event_id = access_event_id
            row.movement_saga_id = movement_saga_id
            row.processing_started_at = None
            row.processed_at = now
            row.last_error = None

    async def mark_succeeded(
        self,
        ingest_id: uuid.UUID,
        *,
        access_event_id: uuid.UUID | None = None,
        movement_saga_id: uuid.UUID | None = None,
    ) -> None:
        async with self.session_factory() as session:
            row = await session.get(LprIngestEvent, ingest_id)
            if row is None:
                return
            row.status = LPR_INGEST_STATUS_SUCCEEDED
            row.access_event_id = access_event_id
            row.movement_saga_id = movement_saga_id
            row.processing_started_at = None
            row.processed_at = datetime.now(tz=UTC)
            row.last_error = None
            await session.commit()

    async def mark_terminal(self, ingest_id: uuid.UUID, *, status: str, detail: str) -> None:
        async with self.session_factory() as session:
            row = await session.get(LprIngestEvent, ingest_id)
            if row is None:
                return
            row.status = status
            row.processing_started_at = None
            row.processed_at = datetime.now(tz=UTC)
            row.last_error = detail[:1000]
            await session.commit()
