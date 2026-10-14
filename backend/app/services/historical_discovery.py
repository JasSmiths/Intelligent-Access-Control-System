"""Fenced, bounded discovery checkpoints; SQL locks never span provider work."""

import copy
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HistoricalDiscoveryProgress
from app.modules.unifi_protect.client import protect_connection_key
from app.services.historical_candidates import HistoricalCandidateStore, HistoricalClaimLost
from app.services.settings import get_runtime_config_for_session

DISCOVERY_RAW_LIMIT = 100
DISCOVERY_RAW_REQUEST_LIMIT = 4
DISCOVERY_REQUEST_LIMIT = 36  # List, event-detail and track reads share this budget.
DISCOVERY_RECORD_LIMIT = 32
DISCOVERY_SECONDS = 10
DISCOVERY_LEASE_SECONDS = 60
DISCOVERY_MAX_WINDOW_MS = 7 * 86_400_000


def milliseconds(value: datetime) -> int:
    if value.tzinfo is None:
        raise ValueError('An aware discovery time is required')
    return int(value.timestamp() * 1000)


def utc_from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, UTC)


@dataclass
class DiscoveryClaim:
    key: str
    token: uuid.UUID
    start: int
    end: int
    source: str
    reason: str
    frontier: list[list[int]]
    page: list[dict[str, Any]]
    index: int


@dataclass(frozen=True)
class DiscoveryStatus:
    state: str
    complete: bool
    covered_through: datetime
    reason: str | None = None
    required_start: datetime | None = None


class HistoricalDiscoveryStore:
    """Compose the existing candidate transaction owner; own only discovery."""

    def __init__(self, candidates: HistoricalCandidateStore | None = None) -> None:
        self.candidates = candidates or HistoricalCandidateStore()

    @staticmethod
    def status(row: HistoricalDiscoveryProgress) -> DiscoveryStatus:
        covered = row.frontier[0][0] if row.frontier else row.end_ms
        if row.queued_start_ms is not None:
            covered = min(covered, row.queued_start_ms)
        return DiscoveryStatus(row.state, row.state == 'completed' and row.queued_start_ms is None,
                               utc_from_ms(covered), row.hold_reason,
                               utc_from_ms(min(row.start_ms, row.queued_start_ms or row.start_ms)))

    async def request_and_claim(self, *, key: str, start: int, end: int,
                                source: str, reason: str) -> tuple[DiscoveryClaim | None, DiscoveryStatus]:
        if start >= end:
            raise ValueError('Discovery requires a nonempty interval')
        async with self.candidates.session() as session:
            now = await self.candidates.now(session)
            await session.execute(insert(HistoricalDiscoveryProgress).values(
                provider_key=key, state='pending', start_ms=start, end_ms=end, source=source,
                reason=reason, frontier=[[start, end]], page=[], page_index=0, updated_at=now,
            ).on_conflict_do_nothing(index_elements=[HistoricalDiscoveryProgress.provider_key]))
            row = await session.get(HistoricalDiscoveryProgress, key, with_for_update=True,
                                    populate_existing=True)
            assert row is not None
            # Capture only coverage outside the fixed active interval. Newer
            # work includes a two-minute overlap, bounded by the request itself.
            additions = []
            if start < row.start_ms:
                additions.append((start, min(end, row.start_ms)))
            if end > row.end_ms:
                additions.append((max(start, row.end_ms - 120_000), end))
            for lo, hi in additions:
                if lo >= hi:
                    continue
                if row.queued_start_ms is None:
                    row.queued_start_ms, row.queued_end_ms = lo, hi
                    row.queued_source, row.queued_reason = source, reason
                else:
                    if lo < row.queued_start_ms:
                        row.queued_source, row.queued_reason = source, reason
                    row.queued_start_ms = min(row.queued_start_ms, lo)
                    row.queued_end_ms = max(row.queued_end_ms or hi, hi)
            if (row.end_ms-row.start_ms > DISCOVERY_MAX_WINDOW_MS or
                    (row.queued_start_ms is not None and row.queued_end_ms is not None and
                     row.queued_end_ms-row.queued_start_ms > DISCOVERY_MAX_WINDOW_MS)):
                row.state, row.hold_reason = 'held', 'discovery_requested_horizon_exceeded'
                row.lease_token = row.lease_until = None
            if row.state == 'completed' and row.queued_start_ms is not None:
                assert row.queued_end_ms is not None and row.queued_source is not None and row.queued_reason is not None
                row.start_ms, row.end_ms = row.queued_start_ms, row.queued_end_ms
                row.source, row.reason = row.queued_source, row.queued_reason
                row.frontier, row.page, row.page_index = [[row.start_ms, row.end_ms]], [], 0
                row.queued_start_ms = row.queued_end_ms = None
                row.queued_source = row.queued_reason = None
                row.state = 'pending'
            now = await self.candidates.now(session)
            if (row.state in {'held', 'completed'} or
                    (row.state == 'leased' and row.lease_until is not None and row.lease_until > now)):
                status = self.status(row)
                await session.commit()
                return None, status
            row.state, row.lease_token = 'leased', uuid.uuid4()
            row.lease_until = now + timedelta(seconds=DISCOVERY_LEASE_SECONDS)
            row.updated_at = now
            claim = DiscoveryClaim(key, row.lease_token, row.start_ms, row.end_ms,
                row.source, row.reason, copy.deepcopy(row.frontier), copy.deepcopy(row.page), row.page_index)
            await session.commit()
            return claim, self.status(row)

    async def _authority(self, session: AsyncSession, key: str) -> None:
        for setting in ('unifi_protect_api_key', 'unifi_protect_host', 'unifi_protect_password',
                        'unifi_protect_port', 'unifi_protect_username', 'unifi_protect_verify_ssl'):
            await session.execute(text('SELECT pg_advisory_xact_lock(hashtext(:setting))'),
                                  {'setting': f'iacs:setting:{setting}'})
        if protect_connection_key(await get_runtime_config_for_session(session)) != key:
            raise HistoricalClaimLost('historical_discovery_configuration_changed')

    async def checkpoint(self, claim: DiscoveryClaim, *, release: bool = False,
                         hold: str | None = None, authoritative: bool = True,
                         candidate: dict[str, Any] | None = None) -> DiscoveryStatus:
        if len(claim.frontier) > 64 or len(claim.page) > DISCOVERY_RAW_LIMIT:
            raise ValueError('Discovery checkpoint exceeds its bounds')
        async with self.candidates.session() as session:
            if authoritative:
                await self._authority(session, claim.key)
            row = await session.get(HistoricalDiscoveryProgress, claim.key, with_for_update=True,
                                    populate_existing=True)
            now = await self.candidates.now(session)
            if (row is None or row.state != 'leased' or row.lease_token != claim.token or
                    row.lease_until is None or row.lease_until <= now):
                raise HistoricalClaimLost('historical_discovery_lease_lost')
            if candidate is not None:
                await self.candidates.reserve_in_session(session, protect_event_id=candidate['protect_event_id'],
                    captured_at=datetime.fromisoformat(candidate['captured_at']), candidate=candidate)
                # Reservation and page advancement are one commit. A crash
                # before it rolls back both; pre-existing acceptance still wins.
                if row.lease_until <= await self.candidates.now(session):
                    raise HistoricalClaimLost('historical_discovery_lease_lost')
            # Do not overwrite concurrent requests merged into the queued range.
            row.frontier, row.page, row.page_index = copy.deepcopy(claim.frontier), copy.deepcopy(claim.page), claim.index
            row.updated_at = now
            if row.queued_start_ms is not None and row.queued_end_ms is not None and row.queued_end_ms-row.queued_start_ms > DISCOVERY_MAX_WINDOW_MS:
                hold = 'discovery_requested_horizon_exceeded'
            if hold or not claim.frontier or release:
                row.state = 'held' if hold else ('pending' if claim.frontier else 'completed')
                row.hold_reason = hold
                row.lease_token = row.lease_until = None
            status = self.status(row)
            await session.commit()
            return status
