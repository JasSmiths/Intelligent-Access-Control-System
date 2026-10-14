"""Durable restart/backfill candidates; no discovery, media I/O or hardware here."""

from __future__ import annotations

import copy
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.task_lifecycle import drain_owned
from app.db.session import AsyncSessionLocal
from app.models import AccessEvent, HistoricalAccessCandidate
from app.services.snapshots import access_event_snapshot_relative_path, get_snapshot_manager

PROTECT_CANDIDATE_SOURCE = "unifi_protect_lpr"
HISTORICAL_PAGE_SIZE = 32
HISTORICAL_LEASE_SECONDS = 120
HISTORICAL_MAX_ATTEMPTS = 5
HISTORICAL_MEDIA_SECONDS = 20
HISTORICAL_TRANSACTION_SECONDS = 30
_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, 'iacs:historical:protect:v1')


class HistoricalClaimLost(RuntimeError):
    """A stale historical owner must not link media or commit domain changes."""


@dataclass(frozen=True)
class HistoricalClaim:
    id: uuid.UUID
    event_id: uuid.UUID
    source: str
    protect_event_id: str
    captured_at: datetime
    candidate: dict[str, Any]
    token: uuid.UUID
    attempt: int
    media_state: str
    media_result: dict[str, Any]


class HistoricalCandidateStore:
    def __init__(self, sessions: Callable[[], AsyncSession] = AsyncSessionLocal) -> None:
        self.sessions = sessions

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        session = self.sessions()
        try:
            await session.scalar(select(func.set_config('statement_timeout', '30000', True)))
            await session.scalar(select(func.set_config('lock_timeout', '5000', True)))
            yield session
        finally:
            await drain_owned(session.close())

    async def now(self, session: AsyncSession) -> datetime:
        value = await session.scalar(select(func.clock_timestamp()))
        if not isinstance(value, datetime):
            raise HistoricalClaimLost('historical_clock_unavailable')
        return value

    async def contains(self, protect_event_id: str) -> bool:
        async with self.session() as session:
            return await session.scalar(select(HistoricalAccessCandidate.id).where(
                HistoricalAccessCandidate.source == PROTECT_CANDIDATE_SOURCE,
                HistoricalAccessCandidate.protect_event_id == protect_event_id,
            )) is not None

    async def reserve(
        self, *, protect_event_id: str, captured_at: datetime, candidate: dict[str, Any]
    ) -> bool:
        if not protect_event_id or len(protect_event_id) > 180:
            raise ValueError('A bounded historical origin is required')
        if captured_at.tzinfo is None or captured_at.utcoffset() is None:
            raise ValueError('An aware historical capture time is required')
        async with self.session() as session:
            inserted = await self.reserve_in_session(session, protect_event_id=protect_event_id,
                                                     captured_at=captured_at, candidate=candidate)
            await session.commit()
            return inserted

    async def reserve_in_session(self, session: AsyncSession, *, protect_event_id: str,
                                 captured_at: datetime, candidate: dict[str, Any]) -> bool:
        if not protect_event_id or len(protect_event_id) > 180 or captured_at.tzinfo is None:
            raise ValueError('A bounded, aware historical origin is required')
        identity = uuid.uuid5(_NAMESPACE, f'{PROTECT_CANDIDATE_SOURCE}:{protect_event_id}')
        now = await self.now(session)
        inserted = await session.scalar(insert(HistoricalAccessCandidate).values(
                id=identity, source=PROTECT_CANDIDATE_SOURCE, protect_event_id=protect_event_id,
                candidate=copy.deepcopy(candidate), captured_at=captured_at,
                event_id=uuid.uuid5(identity, 'access-event'), state='pending', due_at=now,
                attempt_count=0, media_state='pending', media_result={},
            ).on_conflict_do_nothing(index_elements=[HistoricalAccessCandidate.source,
                HistoricalAccessCandidate.protect_event_id]).returning(HistoricalAccessCandidate.id))
        return inserted is not None

    async def claim(self) -> HistoricalClaim | None:
        async with self.session() as session:
            now = await self.now(session)
            rows = await session.scalars(select(HistoricalAccessCandidate).where(
                HistoricalAccessCandidate.due_at <= now,
                or_(HistoricalAccessCandidate.state == 'pending', and_(
                    HistoricalAccessCandidate.state == 'leased', HistoricalAccessCandidate.lease_until <= now)),
            ).order_by(HistoricalAccessCandidate.due_at, HistoricalAccessCandidate.id)
                .limit(HISTORICAL_PAGE_SIZE).with_for_update(skip_locked=True))
            for row in rows:
                now = await self.now(session)
                if row.media_state == 'preparing':
                    # Preparation might have written the deterministic path before
                    # a crash. Retain ownership and uncertainty; never fetch again
                    # or unlink a path which a concurrent commit could have linked.
                    row.state, row.media_state, row.reason = 'held', 'held', 'media_preparation_interrupted'
                    row.media_result = {**row.media_result, 'status':'held', 'reason':'media_preparation_interrupted'}
                    row.lease_token, row.lease_until, row.media_until = None, None, None
                    continue
                if row.attempt_count >= HISTORICAL_MAX_ATTEMPTS:
                    row.state, row.reason = 'held', row.reason or 'historical_attempts_exhausted'
                    row.lease_token, row.lease_until = None, None
                    continue
                row.state, row.lease_token = 'leased', uuid.uuid4()
                row.lease_until = now + timedelta(seconds=HISTORICAL_LEASE_SECONDS)
                row.attempt_count += 1
                claim = HistoricalClaim(row.id, row.event_id, row.source, row.protect_event_id,
                    row.captured_at, copy.deepcopy(row.candidate), row.lease_token, row.attempt_count,
                    row.media_state, copy.deepcopy(row.media_result))
                await session.flush()
                await self.assert_owned(session, row, claim)
                await session.commit()
                return claim
            await session.commit()
            return None

    async def assert_owned(
        self, session: AsyncSession, row: HistoricalAccessCandidate, claim: HistoricalClaim
    ) -> None:
        now = await self.now(session)
        if (row.id != claim.id or row.event_id != claim.event_id or row.state != 'leased'
                or row.lease_token != claim.token or row.lease_until is None or row.lease_until <= now):
            raise HistoricalClaimLost('historical_lease_lost')

    async def lock_owned(self, session: AsyncSession, claim: HistoricalClaim) -> HistoricalAccessCandidate:
        row = await session.get(HistoricalAccessCandidate, claim.id, with_for_update=True, populate_existing=True)
        if row is None:
            raise HistoricalClaimLost('historical_candidate_unavailable')
        await self.assert_owned(session, row, claim)
        return row

    async def begin_media(self, claim: HistoricalClaim) -> uuid.UUID:
        async with self.session() as session:
            row = await self.lock_owned(session, claim)
            if row.media_state != 'pending':
                raise HistoricalClaimLost('historical_media_already_prepared')
            token = uuid.uuid4()
            row.media_state, row.media_token = 'preparing', token
            row.media_until = row.lease_until
            row.media_result = {'relative_path':access_event_snapshot_relative_path(claim.event_id, claim_token=token)}
            await session.commit()
            return token

    async def finish_media(self, claim: HistoricalClaim, token: uuid.UUID, result: dict[str, Any]) -> None:
        async with self.session() as session:
            row = await self.lock_owned(session, claim)
            if row.media_state != 'preparing' or row.media_token != token:
                raise HistoricalClaimLost('historical_media_ownership_lost')
            row.media_state = 'ready' if result.get('status') == 'ready' else 'unavailable'
            row.media_result, row.media_until = copy.deepcopy(result), None
            await session.commit()

    async def media_result(self, claim: HistoricalClaim) -> dict[str, Any]:
        async with self.session() as session:
            row = await self.lock_owned(session, claim)
            if row.media_state not in {'ready', 'unavailable'}:
                raise HistoricalClaimLost('historical_media_not_settled')
            return copy.deepcopy(row.media_result)

    async def retry(self, claim: HistoricalClaim, *, reason: str) -> None:
        async with self.session() as session:
            row = await self.lock_owned(session, claim)
            row.state = 'held' if row.attempt_count >= HISTORICAL_MAX_ATTEMPTS or row.media_state == 'preparing' else 'pending'
            row.reason = 'media_preparation_interrupted' if row.media_state == 'preparing' else reason
            if row.media_state == 'preparing':
                row.media_state = 'held'
            row.due_at = await self.now(session) + timedelta(seconds=min(60, 2 ** row.attempt_count))
            row.lease_token, row.lease_until = None, None
            await session.commit()

    async def complete_in_session(
        self, session: AsyncSession, row: HistoricalAccessCandidate, claim: HistoricalClaim, *, skipped: bool = False
    ) -> None:
        await self.assert_owned(session, row, claim)
        row.state, row.completed_at = ('skipped' if skipped else 'completed'), await self.now(session)
        row.reason = 'existing_or_suppressed' if skipped else None
        row.lease_token, row.lease_until = None, None
        # Linked/terminal candidates retain their original media receipt. No
        # automatic unlink can race an event commit or destroy recovery evidence.

    async def discard_skipped_media(self, claim: HistoricalClaim) -> bool:
        """Only terminal skipped, proved-unlinked media may be deleted.

        A rollback/pending/held candidate retains its file for retry or review.
        The row lock fences candidate writers; completed/linked events always win.
        """
        async with self.session() as session:
            row = await session.get(HistoricalAccessCandidate, claim.id, with_for_update=True)
            if row is None or row.state != 'skipped' or row.media_state != 'ready' or row.media_token is None:
                return False
            token = row.media_token
            path = access_event_snapshot_relative_path(row.event_id, claim_token=token)
            linked = await session.scalar(select(AccessEvent.id).where(or_(
                AccessEvent.id == row.event_id, AccessEvent.snapshot_path == path)).limit(1))
            if linked is not None or row.media_result.get('relative_path') != path:
                return False
            row.media_state = 'cleanup'
            await session.commit()
        # This settled token cannot be reclaimed, and only its derived paths are
        # removed. SnapshotManager drains every actual thread write/delete.
        await get_snapshot_manager().delete_access_snapshot_claim(claim.event_id, token)
        async with self.session() as session:
            row = await session.get(HistoricalAccessCandidate, claim.id, with_for_update=True)
            if row is not None and row.state == 'skipped' and row.media_state == 'cleanup' and row.media_token == token:
                row.media_state = 'discarded'
                await session.commit()
        return True
