"""Finite test-owned input through the real durable claim/window participants."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models import LprIngestEvent
from app.services.access.pipeline_store import AccessPipelineStore
from app.services.access.read_codec import read_with_ingest_identity
from app.services.access.reads import GATE_OBSERVATION_PAYLOAD_KEY, LPR_INGEST_EVENT_PAYLOAD_KEY
from app.services.access.window_store import AccessWindowStore
from app.services.lpr_ingest import LprIngestRepository


@asynccontextmanager
async def claimed_window(read):
    repository, pipeline, windows = LprIngestRepository(), AccessPipelineStore(), AccessWindowStore()
    identity = read.raw_payload.get(LPR_INGEST_EVENT_PAYLOAD_KEY, {}).get("id")
    if identity:
        async with AsyncSessionLocal() as session:
            ingest = await session.get(LprIngestEvent, uuid.UUID(identity))
    else:
        ingest, created = await repository.persist_read(read, received_at=datetime.now(UTC),
            idempotency_key=f"synthetic-window:{uuid.uuid4()}", normalized_payload={
                "registration_number": read.registration_number, "captured_at": read.captured_at.isoformat(),
                "source": read.source, "confidence": read.confidence, "raw_payload": read.raw_payload,
                "candidate_registration_numbers": list(read.candidate_registration_numbers)})
        assert created
    assert ingest is not None
    if ingest.observation_state == "observing":
        assert await repository.complete_observation(ingest.id, ingest.capture_token,
            read.raw_payload[GATE_OBSERVATION_PAYLOAD_KEY]) is not None
    read = read_with_ingest_identity(read, ingest)
    # Concurrent finite callers follow the same durable ready order as the
    # single production coordinator; the fixture never starts a second path.
    async with asyncio.timeout(10):
        while True:
            async with AsyncSessionLocal() as session:
                first = await session.scalar(select(LprIngestEvent.id).where(
                    LprIngestEvent.status == "pending", LprIngestEvent.observation_state == "ready",
                    LprIngestEvent.window_id.is_(None),
                ).order_by(LprIngestEvent.ready_ordinal).limit(1))
            coordinator = await pipeline.acquire_coordinator() if first == ingest.id else None
            if coordinator is not None:
                break
            await asyncio.sleep(.01)
    try:
        claim = await pipeline.claim_next_read(coordinator)
        assert claim is not None and claim.ingest_id == ingest.id
        async with AsyncSessionLocal() as session:
            summary = await windows.append_in_session(session, claim, read, threshold=.78)
            window_claim = await windows.claim_immediate_in_session(
                session, claim, summary.evidence.window_id, reason="synthetic_finalizer")
            stored = await windows.load_in_session(session, window_claim.window_id)
            await session.commit()
        yield stored, window_claim
        # Finish the actual one-read boundary before another test-owned claim.
        assert await pipeline.next_flush_window(coordinator, quiet_seconds=0, max_seconds=0) is None
    finally:
        await pipeline.release_coordinator(coordinator)
