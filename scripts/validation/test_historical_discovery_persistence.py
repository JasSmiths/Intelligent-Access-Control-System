"""Bounded discovery against the real adapter/store and isolated PostgreSQL.

Only the external Protect connection/track/media boundaries are inert. The
provider's raw limit, time bounds and descending order are deliberately real.
"""

import asyncio
import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text, update

from app.db.session import AsyncSessionLocal
from app.models import GateCommandRecord, HistoricalAccessCandidate
from app.services import restart_backfill as owner
from app.services.unifi_protect import UnifiProtectIntegrationService

pytest_plugins = ('test_historical_candidates_persistence',)
pytestmark = pytest.mark.asyncio
END = datetime(2026, 10, 14, 12, tzinfo=UTC)
START = END - timedelta(minutes=15)


class RawProtect:
    def __init__(self, count):
        self.bootstrap = SimpleNamespace(cameras={})
        self.requests = []
        self.records = [self.record(i) for i in range(count)]

    @staticmethod
    def record(i):
        at = START + timedelta(seconds=1 + i * 2)
        return {'id': f'synthetic-{i}', 'modelKey': 'event', 'type': 'smartDetectZone',
                'start': int(at.timestamp() * 1000), 'end': int(at.timestamp() * 1000)+1000,
                'camera': 'synthetic-lpr', 'score': 92,
                'smartDetectTypes': ['vehicle', 'licensePlate']}

    async def get_events_raw(self, **kwargs):
        self.requests.append(kwargs)
        assert kwargs['_allow_manual_paginate'] is False
        start, end = (int(kwargs[key].timestamp()*1000) for key in ('start', 'end'))
        records = [r for r in self.records if start <= r['start'] <= end]
        return sorted(records, key=lambda r: (r['start'], r['id']),
                      reverse=kwargs['sorting']=='desc')[:kwargs['limit']]


@pytest_asyncio.fixture
async def provider(historical_resources, monkeypatch):
    async with AsyncSessionLocal() as session:
        if await session.scalar(text("SELECT to_regclass('historical_discovery_progress')")):
            await session.execute(text('TRUNCATE historical_discovery_progress'))
            await session.commit()
    api = RawProtect(3)
    service = UnifiProtectIntegrationService()
    async def run_current(operation, **kwargs):
        return await operation(api)
    service._run_current = run_current
    service._ensure_api = AsyncMock(return_value=api)
    from app.modules.unifi_protect.client import protect_connection_key
    service._api_discovery_key = protect_connection_key(await owner.get_runtime_config())
    service.configured = AsyncMock(return_value=True)
    async def track(identity):
        row = next(r for r in api.records if r['id'] == identity)
        at = datetime.fromtimestamp(row['start']/1000, UTC).isoformat()
        return {'event': {'id': identity, 'start': at}, 'observations': [
            {'registration_number': 'ZZ'+identity.split('-')[-1], 'captured_at': at,
             'confidence': .99}]}
    service.event_lpr_track = AsyncMock(side_effect=track)
    service.event_thumbnail = historical_resources.protect.event_thumbnail
    monkeypatch.setattr(owner, 'get_unifi_protect_service', lambda: service)
    return api, service


async def saved_ids():
    async with AsyncSessionLocal() as session:
        return set(await session.scalars(select(HistoricalAccessCandidate.protect_event_id)))


@pytest.mark.parametrize('count', [0, 1, 3, 99, 100, 101, 301, 401])
async def test_finite_window_is_not_reported_complete_with_unreserved_observations(provider, count):
    api, _ = provider
    api.records = [api.record(i) for i in range(count)]
    expected = {r['id'] for r in api.records}
    for _ in range(40):
        before_requests = len(api.requests)
        result = await owner.MissedAccessEventBackfillService().run(
            startup_at=END, window_start_override=START)
        if hasattr(result, 'discovery_requests'):
            assert result.discovery_requests <= 36
            assert len(api.requests)-before_requests <= 4
            assert all(r['limit'] == 100 for r in api.requests)
        # The old worker equates errors==0 with full coverage. This assertion
        # reproduces that behavior before the explicit coverage contract exists.
        if getattr(result, 'discovery_complete', result.errors == 0):
            assert await saved_ids() == expected
            break
    else:
        pytest.fail('Finite retained workload did not finish within bounded cycles')
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(GateCommandRecord.id).limit(1)) is None


async def cycles(*, maximum=40):
    for _ in range(maximum):
        result = await owner.MissedAccessEventBackfillService().run(startup_at=END, window_start_override=START)
        assert result.discovery_requests <= 36
        if result.discovery_complete or result.discovery_state == 'held':
            return result
    pytest.fail('Discovery did not settle within bounded cycles')


async def test_raw_saturation_precedes_type_camera_and_non_lpr_filtering(provider):
    api, _ = provider
    api.records = [api.record(i) for i in range(301)]
    for row in api.records[:250]:
        row['camera'] = 'front'
        row['smartDetectTypes'] = ['vehicle']
        if int(row['id'].split('-')[-1]) % 2:
            row['type'] = 'motion'
    # Preserve the browsing adapter's established invalid-score exclusion;
    # its raw row still contributes to saturation before eligibility filtering.
    api.records[250]['score'] = -1
    result = await cycles()
    assert result.discovery_complete
    assert await saved_ids() == {r['id'] for r in api.records[251:]}


@pytest.mark.parametrize('count', [99, 100, 101])
async def test_dense_equal_timestamps_are_complete_or_explicitly_held(provider, count):
    api, _ = provider
    api.records = [api.record(i) for i in range(count)]
    for row in api.records:
        row['start'] = int((START+timedelta(minutes=5)).timestamp()*1000)
        row['end'] = row['start']+1000
    result = await cycles()
    assert result.discovery_complete == (count < 100)
    if count >= 100:
        assert result.discovery_state == 'held'
        assert result.discovery_reason == 'discovery_dense_boundary_unproven'
        assert not await saved_ids()
        calls = len(api.requests)
        again = await cycles(maximum=1)
        assert again.discovery_state == 'held' and len(api.requests) == calls


@pytest.mark.parametrize('malformation', ['shape','oversized','identity','timestamp','type','conflict'])
async def test_malformed_raw_pages_are_held_without_false_coverage(provider, malformation):
    api, _ = provider
    rows = copy.deepcopy(api.records)
    if malformation == 'shape': rows = {'items': rows}
    elif malformation == 'oversized': rows = rows*34
    elif malformation == 'identity': rows[0]['id'] = ''
    elif malformation == 'timestamp': rows[0]['start'] = True
    elif malformation == 'type': rows[0]['type'] = 'future-unknown'
    else: rows.append({**rows[0], 'camera':'other-camera'})
    api.get_events_raw = AsyncMock(return_value=rows)
    result = await cycles()
    assert result.discovery_state == 'held' and not result.discovery_complete
    assert result.discovery_reason == 'discovery_invalid_provider_response'
    assert not await saved_ids()


async def test_duplicates_overlap_and_restart_preserve_original_acceptance(provider):
    api, service = provider
    api.records.append(copy.deepcopy(api.records[0]))
    assert (await cycles()).discovery_complete
    original = await saved_ids()
    calls = service.event_lpr_track.await_count
    # A new interval requests an overlap, then a fresh worker resumes it.
    result = await owner.MissedAccessEventBackfillService().run(startup_at=END+timedelta(minutes=1), window_start_override=START)
    while not result.discovery_complete:
        result = await owner.MissedAccessEventBackfillService().run(startup_at=END+timedelta(minutes=1), window_start_override=START)
    assert await saved_ids() == original
    assert service.event_lpr_track.await_count == calls


async def test_provider_failure_retries_captured_page_after_restart(provider):
    api, service = provider
    actual = service.event_lpr_track.side_effect
    failed = False
    async def track(identity):
        nonlocal failed
        if identity == 'synthetic-1' and not failed:
            failed = True
            raise RuntimeError('Synthetic unavailable track')
        return await actual(identity)
    service.event_lpr_track.side_effect = track
    result = await owner.MissedAccessEventBackfillService().run(startup_at=END, window_start_override=START)
    assert not result.discovery_complete and result.errors == 1
    assert await saved_ids() == {'synthetic-0'}
    request_count = len(api.requests)
    assert (await cycles()).discovery_complete
    assert len(api.requests) == request_count  # Captured page survives restart.
    assert await saved_ids() == {r['id'] for r in api.records}
    assert [call.args[0] for call in service.event_lpr_track.await_args_list].count('synthetic-0') == 1


async def test_explicit_plate_event_without_resolved_track_is_held(provider):
    _, service = provider
    service.event_lpr_track.side_effect = None
    service.event_lpr_track.return_value = {'event': {}, 'observations': []}
    result = await cycles()
    assert result.discovery_state == 'held' and not result.discovery_complete
    assert result.discovery_reason == 'discovery_lpr_track_unresolved'
    assert not await saved_ids()


@pytest.mark.parametrize('interruption', ['error', 'cancel'])
async def test_interruption_after_durable_acceptance_cannot_lose_or_repeat_original_evidence(provider, monkeypatch, interruption):
    from app.services.historical_discovery import HistoricalDiscoveryStore
    _, service = provider
    original = HistoricalDiscoveryStore.checkpoint
    interrupted = False
    async def checkpoint(self, claim, **kwargs):
        nonlocal interrupted
        status = await original(self, claim, **kwargs)
        if kwargs.get('candidate') and not interrupted:
            interrupted = True
            if interruption == 'cancel': raise asyncio.CancelledError
            raise RuntimeError('Synthetic crash after accepted checkpoint')
        return status
    monkeypatch.setattr(HistoricalDiscoveryStore, 'checkpoint', checkpoint)
    if interruption == 'cancel':
        with pytest.raises(asyncio.CancelledError):
            await owner.MissedAccessEventBackfillService().run(startup_at=END, window_start_override=START)
    else:
        assert not (await owner.MissedAccessEventBackfillService().run(startup_at=END, window_start_override=START)).discovery_complete
    assert await saved_ids() == {'synthetic-0'}
    assert (await cycles()).discovery_complete
    assert [call.args[0] for call in service.event_lpr_track.await_args_list].count('synthetic-0') == 1


async def test_configuration_change_immediately_before_acceptance_is_fenced(provider):
    from app.models import SystemSetting
    _, service = provider
    actual = service.event_lpr_track.side_effect
    async def track(identity):
        value = await actual(identity)
        async with AsyncSessionLocal() as session:
            await session.merge(SystemSetting(key='unifi_protect_host', category='integrations',
                value={'plain':'new-controller.invalid'}, is_secret=False))
            await session.commit()
        return value
    service.event_lpr_track.side_effect = track
    try:
        result = await owner.MissedAccessEventBackfillService().run(startup_at=END, window_start_override=START)
        assert not result.discovery_complete and result.errors == 1
        assert not await saved_ids()
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(SystemSetting).where(SystemSetting.key=='unifi_protect_host'))
            await session.commit()


async def test_provider_generation_change_immediately_before_acceptance_is_fenced(provider):
    _, service = provider
    actual = service.event_lpr_track.side_effect
    async def track(identity):
        value = await actual(identity)
        service._provider_generation += 1
        return value
    service.event_lpr_track.side_effect = track
    result = await owner.MissedAccessEventBackfillService().run(startup_at=END, window_start_override=START)
    assert not result.discovery_complete and not await saved_ids()


async def test_lease_takeover_and_concurrent_earlier_queue_preserve_coverage_and_fence_old_writer(provider):
    from app.models import HistoricalDiscoveryProgress
    from app.services.historical_candidates import HistoricalClaimLost
    from app.services.historical_discovery import HistoricalDiscoveryStore, milliseconds
    from app.modules.unifi_protect.client import protect_connection_key
    store = HistoricalDiscoveryStore()
    key = protect_connection_key(await owner.get_runtime_config())
    claim, _ = await store.request_and_claim(key=key, start=milliseconds(START), end=milliseconds(END), source='periodic', reason='Synthetic')
    assert claim is not None
    earlier = milliseconds(START-timedelta(minutes=10))
    busy, _ = await store.request_and_claim(key=key, start=earlier, end=milliseconds(END), source='startup', reason='Synthetic earlier')
    assert busy is None
    claim.frontier = []
    status = await store.checkpoint(claim)
    assert not status.complete and milliseconds(status.covered_through) == earlier
    resumed, _ = await store.request_and_claim(key=key, start=milliseconds(START), end=milliseconds(END), source='periodic', reason='Synthetic')
    assert resumed is not None and resumed.start == earlier
    async with AsyncSessionLocal() as session:
        await session.execute(update(HistoricalDiscoveryProgress).values(lease_until=START))
        await session.commit()
    replacement, _ = await store.request_and_claim(key=key, start=earlier, end=milliseconds(END), source='startup', reason='Synthetic')
    assert replacement is not None and replacement.token != resumed.token
    with pytest.raises(HistoricalClaimLost):
        await store.checkpoint(resumed)


async def test_queued_horizon_is_bounded_and_held(provider):
    from app.services.historical_discovery import HistoricalDiscoveryStore, milliseconds
    from app.modules.unifi_protect.client import protect_connection_key
    store = HistoricalDiscoveryStore()
    key = protect_connection_key(await owner.get_runtime_config())
    claim, _ = await store.request_and_claim(key=key, start=milliseconds(START), end=milliseconds(END), source='startup', reason='Synthetic')
    assert claim is not None
    claim2, status = await store.request_and_claim(key=key, start=milliseconds(START-timedelta(days=8)), end=milliseconds(END), source='startup', reason='Synthetic')
    assert claim2 is None and status.state == 'held' and not status.complete
    assert status.reason == 'discovery_requested_horizon_exceeded'


@pytest.mark.parametrize('held', [False, True])
async def test_periodic_overlap_does_not_recursively_extend_an_incomplete_window(provider, monkeypatch, held):
    api, _ = provider
    api.records = [api.record(i) for i in range(101 if held else 99)]
    if held:
        for row in api.records:
            row['start'] = int((START-timedelta(minutes=2)).timestamp()*1000)
            row['end'] = row['start']+1000
    ticks, requests, results = 0, [], []
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return END+timedelta(minutes=2*min(ticks, 3))
    actual = owner.MissedAccessEventBackfillService.run
    async def recording(self, **kwargs):
        requests.append(kwargs['window_start_override'])
        result = await actual(self, **kwargs)
        results.append(result)
        return result
    async def sleep(_):
        nonlocal ticks
        ticks += 1
        if len(results) >= 40 or (len(results) >= 4 and
                (results[-1].discovery_complete or results[-1].discovery_state == 'held')):
            raise asyncio.CancelledError
    # Replace only this owner's scheduling surface; never the global asyncio
    # sleep used by database/provider cancellation and fixture cleanup.
    scheduling = SimpleNamespace(**{name:getattr(asyncio,name) for name in dir(asyncio) if not name.startswith('__')})
    scheduling.sleep = sleep
    monkeypatch.setattr(owner, 'asyncio', scheduling)
    monkeypatch.setattr(owner, 'datetime', Clock)
    monkeypatch.setattr(owner.MissedAccessEventBackfillService, 'run', recording)
    with pytest.raises(asyncio.CancelledError):
        await owner.run_missed_access_event_reconciliation(interval_seconds=0)
    assert len(results) >= 4
    assert all(value >= requests[0] for value in requests), 'Overlap invented progressively older required coverage'
    if held:
        assert results[-1].discovery_state == 'held' and not results[-1].discovery_complete
    else:
        assert results[-1].discovery_complete
        assert await saved_ids() == {row['id'] for row in api.records}
