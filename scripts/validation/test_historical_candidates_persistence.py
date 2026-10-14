"""Immutable historical identity/media and atomic repair on isolated PostgreSQL."""

import asyncio
import threading
import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import func, select, text, update

from app.db.session import AsyncSessionLocal, engine
from app.models import (AccessChartDirtyState, AccessCompletionWork, AccessEvent, Anomaly, AuditLog,
    GateCommandRecord, GateStateObservation, HaPresenceDesired, HistoricalAccessCandidate,
    MovementSagaRecord, MovementSessionRecord, Person, Presence, Vehicle)
from app.models.enums import AccessDecision, AccessDirection, PresenceState
from app.services import restart_backfill as owner
from app.services.auth_root import AUTH_ROOT_LOCK_KEY
from app.services.ha_presence_contracts import HaCoordinationBusy
from app.services.historical_candidates import HistoricalCandidateStore, HistoricalClaimLost
from app.services.leaderboard import LeaderboardService
from app.services.restart_backfill import (MISSED_EVENT_BACKFILL_SOURCE, MISSED_EVENT_RECONCILIATION_SOURCE,
    MissedAccessEventBackfillResult, MissedAccessEventBackfillService, ProtectBackfillCandidate)
from app.services.settings import get_runtime_config
from app.services.snapshots import SnapshotManager, access_event_snapshot_relative_path

pytest_plugins = ('test_recovery_boundaries',)
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def historical_resources(isolated_resources, monkeypatch, tmp_path):
    async with AsyncSessionLocal() as session:
        await session.execute(text('TRUNCATE historical_access_candidates,access_chart_dirty_state,access_completion_work,ha_presence_desired,ha_presence_targets,ha_presence_coordinator CASCADE'))
        await session.commit()
    from app.modules.unifi_protect.client import ProtectRecoveryPage, protect_connection_key
    from app.services.unifi_protect import UnifiProtectIntegrationService
    async with AsyncSessionLocal() as session:
        await session.execute(text('TRUNCATE historical_discovery_progress'))
        await session.commit()
    runtime = replace(await get_runtime_config(), schedule_default_policy='allow')
    monkeypatch.setattr(owner, 'get_runtime_config', AsyncMock(return_value=runtime))
    monkeypatch.setattr(owner.settings, 'data_dir', tmp_path)
    fixture = SimpleNamespace(active_fixture_connections=0)
    image = BytesIO(); Image.new('RGB', (32, 24), 'blue').save(image, format='JPEG')
    async def thumbnail(*args, **kwargs):
        assert engine.pool.checkedout() == fixture.active_fixture_connections, 'Historical media held a database connection'
        return SimpleNamespace(content=image.getvalue())
    protect = UnifiProtectIntegrationService()
    protect._api_discovery_key = protect_connection_key(runtime)
    protect._ensure_api = AsyncMock(return_value=object())
    protect.configured = AsyncMock(return_value=False)
    protect.recovery_page = AsyncMock(return_value=(ProtectRecoveryPage(0, []), 0))
    protect.event_lpr_track = AsyncMock(side_effect=AssertionError('Original candidate was fetched again'))
    protect.event_thumbnail = AsyncMock(side_effect=thumbnail)
    monkeypatch.setattr(owner, 'get_unifi_protect_service', lambda: protect)
    charts = LeaderboardService(); monkeypatch.setattr(charts, 'wake', Mock())
    monkeypatch.setattr(owner, 'get_leaderboard_service', lambda: charts)
    fixture.protect, fixture.root, fixture.charts = protect, tmp_path, charts
    yield fixture


async def rows(model):
    async with AsyncSessionLocal() as session:
        return list(await session.scalars(select(model)))


def candidate(identity='historical-original', *, at=None, plate='SYNTH01'):
    return ProtectBackfillCandidate(identity, plate, at or datetime.now(UTC)-timedelta(days=4), .99,
        'synthetic-camera', 'Synthetic', {'original':'immutable'}, {'original':'track'})


async def reserve(value=None, *, source=MISSED_EVENT_BACKFILL_SOURCE):
    value = value or candidate()
    payload = asdict(value); payload['captured_at'] = value.captured_at.isoformat()
    payload['backfill_reason'] = 'Synthetic original reason'
    payload['discovery_source'] = source
    store = HistoricalCandidateStore()
    assert await store.reserve(protect_event_id=value.protect_event_id,
        captured_at=value.captured_at, candidate=payload)
    return store, value


async def due():
    async with AsyncSessionLocal() as session:
        await session.execute(update(HistoricalAccessCandidate).values(due_at=func.clock_timestamp()-text("interval '1 second'")))
        await session.commit()


async def resident(at):
    async with AsyncSessionLocal() as session:
        person = Person(display_name='Synthetic historical resident', is_active=True)
        vehicle = Vehicle(registration_number='SYNTH01', owner=person, is_active=True)
        session.add_all([person, vehicle]); await session.flush()
        session.add(Presence(person_id=person.id, state=PresenceState.EXITED, last_changed_at=at-timedelta(days=1)))
        session.add(GateStateObservation(gate_entity_id='synthetic-gate', state='closed', observed_at=at))
        await session.commit()
        return person.id


async def test_reservation_is_durable_immutable_and_mutates_no_domain_state():
    store, original = await reserve()
    before = (await rows(HistoricalAccessCandidate))[0]
    changed = asdict(candidate(original.protect_event_id, at=original.captured_at+timedelta(days=1), plate='CHANGED'))
    changed['captured_at'] = changed['captured_at'].isoformat()
    assert not await store.reserve(protect_event_id=original.protect_event_id,
        captured_at=original.captured_at+timedelta(days=1), candidate=changed)
    after = (await rows(HistoricalAccessCandidate))[0]
    assert after.event_id == before.event_id and after.candidate == before.candidate
    assert after.captured_at == original.captured_at and after.attempt_count == 0
    assert not await rows(AccessEvent) and not await rows(MovementSagaRecord) and not await rows(AuditLog)
    claim = await store.claim()
    assert claim.event_id == before.event_id and claim.candidate['registration_number'] == 'SYNTH01'


async def test_old_startup_candidate_drains_when_discovery_disabled_and_outside_window(historical_resources):
    store, value = await reserve(); await resident(value.captured_at)
    result = await MissedAccessEventBackfillService(source=MISSED_EVENT_RECONCILIATION_SOURCE).run(
        window_start_override=datetime.now(UTC)-timedelta(minutes=2))
    assert result.backfilled == 1 and result.reason == 'unifi_protect_not_configured'
    event, = await rows(AccessEvent); saved, = await rows(HistoricalAccessCandidate)
    assert event.id == saved.event_id and saved.state == 'completed'
    assert event.source == MISSED_EVENT_BACKFILL_SOURCE and event.occurred_at == value.captured_at
    assert event.raw_payload['backfill']['reason'] == 'Synthetic original reason'
    assert event.raw_payload['backfill']['hardware_actions_suppressed']
    assert event.snapshot_path and (historical_resources.root/event.snapshot_path).is_file()
    work, = await rows(AccessCompletionWork)
    assert work.stage == 'publication' and work.expected_revision == event.resource_revision == 1
    dirty, = await rows(AccessChartDirtyState)
    assert dirty.requested_generation == 1 and dirty.live_origin_at is None
    desired, = await rows(HaPresenceDesired)
    assert desired.event_id == event.id and not desired.eligible and desired.outcome == 'historical_invalidation'
    assert not await rows(GateCommandRecord)
    historical_resources.protect.event_lpr_track.assert_not_awaited()
    historical_resources.protect.event_thumbnail.assert_awaited_once()


async def test_repeated_discovery_uses_stored_original_before_track_fetch(historical_resources):
    store, original = await reserve(candidate(at=datetime.now(UTC)-timedelta(minutes=1)))
    historical_resources.protect.configured.return_value = True
    from app.modules.unifi_protect.client import ProtectRecoveryPage
    historical_resources.protect.recovery_page.return_value = (ProtectRecoveryPage(1, [{'id':original.protect_event_id, 'start':original.captured_at.isoformat(),
        'type':'smartDetectZone', 'smart_detect_types':['licensePlate']}]), 0)
    result = await MissedAccessEventBackfillService().run()
    assert result.backfilled == 1 and result.duplicates == 1
    assert (await rows(HistoricalAccessCandidate))[0].candidate['protect_event'] == {'original':'immutable'}
    historical_resources.protect.event_lpr_track.assert_not_awaited()


async def test_actual_auth_root_busy_rolls_back_whole_historical_transaction_then_reuses_one_media(historical_resources):
    store, value = await reserve(); person_id = await resident(value.captured_at)
    service = MissedAccessEventBackfillService(); result = MissedAccessEventBackfillResult()
    async with AsyncSessionLocal() as blocker:
        await blocker.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key':AUTH_ROOT_LOCK_KEY})
        historical_resources.active_fixture_connections = 1
        assert await service._drain_candidates(result, limit=1) == 1
        assert result.errors == 1
        for model in (AccessEvent, MovementSagaRecord, MovementSessionRecord, AuditLog, Anomaly,
                AccessCompletionWork, AccessChartDirtyState, HaPresenceDesired):
            assert not await rows(model), model.__name__
        async with AsyncSessionLocal() as session:
            assert (await session.get(Presence, person_id)).state == PresenceState.EXITED
        saved, = await rows(HistoricalAccessCandidate)
        assert saved.state == 'pending' and saved.media_state == 'ready' and saved.reason == 'ha_coordination_busy'
        path = saved.media_result['relative_path']; media_bytes = (historical_resources.root/path).read_bytes()
        await blocker.rollback()
        historical_resources.active_fixture_connections = 0
    await due(); result = MissedAccessEventBackfillResult()
    await service._drain_candidates(result, limit=1)
    assert result.backfilled == 1 and result.errors == 0
    event, = await rows(AccessEvent)
    assert event.id == saved.event_id and event.snapshot_path == path
    assert (historical_resources.root/path).read_bytes() == media_bytes
    assert (await rows(HistoricalAccessCandidate))[0].attempt_count == 2
    historical_resources.protect.event_thumbnail.assert_awaited_once()


async def test_five_busy_attempts_hold_original_candidate_and_artifact_without_refetch(historical_resources, monkeypatch):
    store, value = await reserve()
    monkeypatch.setattr(owner.ha_presence_reservations, 'reserve_all_in_session', AsyncMock(side_effect=HaCoordinationBusy('synthetic busy')))
    service = MissedAccessEventBackfillService()
    identity = (await rows(HistoricalAccessCandidate))[0].event_id
    for expected in range(1, 6):
        await due(); result = MissedAccessEventBackfillResult()
        await service._drain_candidates(result, limit=1)
        saved, = await rows(HistoricalAccessCandidate)
        assert saved.attempt_count == expected and saved.event_id == identity
        assert saved.captured_at == value.captured_at and result.errors == 1
        assert saved.state == ('held' if expected == 5 else 'pending')
        assert (historical_resources.root/saved.media_result['relative_path']).is_file()
        assert not await rows(AccessEvent)
    assert await store.claim() is None
    historical_resources.protect.event_thumbnail.assert_awaited_once()


async def test_expired_claim_before_preparation_reclaims_same_original_identity():
    store, value = await reserve(); first = await store.claim()
    async with AsyncSessionLocal() as session:
        await session.execute(update(HistoricalAccessCandidate).values(lease_until=func.clock_timestamp()-text("interval '1 second'")))
        await session.commit()
    second = await store.claim()
    assert second.id == first.id and second.event_id == first.event_id and second.token != first.token
    assert second.candidate == first.candidate and second.attempt == 2
    with pytest.raises(HistoricalClaimLost):
        await store.begin_media(first)


async def test_interrupted_preparation_is_held_without_provider_refetch(historical_resources):
    store, value = await reserve(); first = await store.claim(); token = await store.begin_media(first)
    path = access_event_snapshot_relative_path(first.event_id, claim_token=token)
    artifact = historical_resources.root/path; artifact.parent.mkdir(parents=True); artifact.write_bytes(b'uncertain owned artifact')
    async with AsyncSessionLocal() as session:
        await session.execute(update(HistoricalAccessCandidate).values(lease_until=func.clock_timestamp()-text("interval '1 second'")))
        await session.commit()
    result = await MissedAccessEventBackfillService().run()
    saved, = await rows(HistoricalAccessCandidate)
    assert saved.state == saved.media_state == 'held' and saved.reason == 'media_preparation_interrupted'
    assert saved.media_result['relative_path'] == path and artifact.read_bytes() == b'uncertain owned artifact'
    historical_resources.protect.event_thumbnail.assert_not_awaited()
    assert not await rows(AccessEvent)


async def test_repeated_cancellation_drains_actual_snapshot_thread_before_releasing_candidate(historical_resources, monkeypatch):
    await reserve()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = SnapshotManager._write_snapshot
    def blocked_write(self, *args, **kwargs):
        entered.set(); assert release.wait(5)
        try:
            return original(self, *args, **kwargs)
        finally:
            finished.set()
    monkeypatch.setattr(SnapshotManager, '_write_snapshot', blocked_write)
    service = MissedAccessEventBackfillService(); result = MissedAccessEventBackfillResult()
    task = asyncio.create_task(service._drain_candidates(result, limit=1))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel(); await asyncio.sleep(.02); task.cancel(); await asyncio.sleep(.02)
        assert not task.done() and not finished.is_set()
        saved, = await rows(HistoricalAccessCandidate)
        assert saved.state == 'leased' and saved.media_state == 'preparing'
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert finished.is_set()
    saved, = await rows(HistoricalAccessCandidate)
    assert saved.state == saved.media_state == 'held'
    assert (historical_resources.root/saved.media_result['relative_path']).is_file()
    historical_resources.protect.event_thumbnail.assert_awaited_once()
    assert not await rows(AccessEvent)


async def test_media_timeout_single_cancels_and_drains_actual_provider_child(historical_resources, monkeypatch):
    await reserve()
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancellations = 0
    async def provider(*args, **kwargs):
        nonlocal cancellations
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellations += 1; cleanup.set()
            await release.wait()
            raise
    historical_resources.protect.event_thumbnail.side_effect = provider
    monkeypatch.setattr(owner, 'HISTORICAL_MEDIA_SECONDS', .02)
    task = asyncio.create_task(MissedAccessEventBackfillService()._drain_candidates(MissedAccessEventBackfillResult(), limit=1))
    await asyncio.wait_for(entered.wait(), 5); await asyncio.wait_for(cleanup.wait(), 5)
    task.cancel(); await asyncio.sleep(.02)
    assert not task.done() and cancellations == 1
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    saved, = await rows(HistoricalAccessCandidate)
    assert saved.state == saved.media_state == 'held' and cancellations == 1
    historical_resources.protect.event_thumbnail.assert_awaited_once()


async def test_rollback_retains_media_and_successful_concurrent_link_prevents_cleanup(historical_resources):
    store, value = await reserve(); claim = await store.claim(); token = await store.begin_media(claim)
    service = MissedAccessEventBackfillService()
    media = await service._prepare_protect_thumbnail(claim, token, value)
    await store.finish_media(claim, token, media)
    path = historical_resources.root/media['relative_path']
    async with store.session() as session:
        saved = await store.lock_owned(session, claim)
        await store.complete_in_session(session, saved, claim, skipped=True)
        await session.rollback()
    assert not await store.discard_skipped_media(claim) and path.is_file()
    # Another committed event linked this same owned file before cleanup.
    async with store.session() as session:
        saved = await store.lock_owned(session, claim)
        session.add(AccessEvent(id=uuid.uuid4(), registration_number='LINKED', occurred_at=value.captured_at,
            decision=AccessDecision.DENIED, direction=AccessDirection.DENIED, confidence=.1, source='synthetic-linked',
            snapshot_path=media['relative_path']))
        await store.complete_in_session(session, saved, claim, skipped=True); await session.commit()
    assert not await store.discard_skipped_media(claim) and path.is_file()


async def test_terminal_skipped_proved_unlinked_claim_deletes_only_its_token_paths(historical_resources):
    store, value = await reserve(); claim = await store.claim(); token = await store.begin_media(claim)
    media = await MissedAccessEventBackfillService()._prepare_protect_thumbnail(claim, token, value)
    await store.finish_media(claim, token, media)
    path = historical_resources.root/media['relative_path']; assert path.is_file()
    unrelated = historical_resources.root/'unrelated.jpg'; unrelated.write_bytes(b'preserved')
    async with store.session() as session:
        saved = await store.lock_owned(session, claim)
        await store.complete_in_session(session, saved, claim, skipped=True); await session.commit()
    assert await store.discard_skipped_media(claim)
    assert not path.exists() and unrelated.read_bytes() == b'preserved'
    assert (await rows(HistoricalAccessCandidate))[0].media_state == 'discarded'


async def test_concurrent_startup_and_periodic_discovery_share_one_provider_identity(historical_resources, monkeypatch):
    from app.modules.unifi_protect.client import ProtectRecoveryPage, protect_connection_key
    from app.services.unifi_protect import UnifiProtectIntegrationService
    value = candidate(at=datetime.now(UTC)-timedelta(minutes=1), plate='UNKNOWN1')
    async with AsyncSessionLocal() as session:
        await session.execute(text('TRUNCATE historical_discovery_progress'))
        await session.commit()
    protect = UnifiProtectIntegrationService()
    protect._api_discovery_key = protect_connection_key(await get_runtime_config())
    protect._ensure_api = AsyncMock(return_value=object())
    protect.configured = AsyncMock(return_value=True)
    protect.recovery_page = AsyncMock(return_value=(ProtectRecoveryPage(1, [{'id':value.protect_event_id,
        'type':'smartDetectZone', 'start':value.captured_at.isoformat(), 'smart_detect_types':['licensePlate']}]), 0))
    started, release = asyncio.Event(), asyncio.Event()
    async def track(*args, **kwargs):
        started.set()
        await release.wait()
        return {'event':{'id':value.protect_event_id, 'start':value.captured_at.isoformat()},
            'observations':[{'registration_number':'UNKNOWN1', 'captured_at':value.captured_at.isoformat(), 'confidence':.99}]}
    protect.event_lpr_track = AsyncMock(side_effect=track)
    protect.event_thumbnail = historical_resources.protect.event_thumbnail
    monkeypatch.setattr(owner, 'get_unifi_protect_service', lambda: protect)
    at = datetime.now(UTC)
    startup = MissedAccessEventBackfillService(source=MISSED_EVENT_BACKFILL_SOURCE, backfill_reason='Original startup reason')
    periodic = MissedAccessEventBackfillService(source=MISSED_EVENT_RECONCILIATION_SOURCE, backfill_reason='Original periodic reason')
    task = asyncio.create_task(startup.run(startup_at=at))
    try:
        await asyncio.wait_for(started.wait(), 5)
        busy = await asyncio.wait_for(periodic.run(startup_at=at), 5)
        assert not busy.discovery_complete
    finally:
        release.set()
    results = [await asyncio.wait_for(task, 10), busy]
    saved, = await rows(HistoricalAccessCandidate)
    event, = await rows(AccessEvent); saga, = await rows(MovementSagaRecord)
    assert saved.source == 'unifi_protect_lpr' and saved.state == 'completed'
    assert event.id == saved.event_id == saga.access_event_id and event.decision == AccessDecision.DENIED
    assert event.source == saved.candidate['discovery_source']
    assert event.raw_payload['backfill']['reason'] == saved.candidate['backfill_reason']
    assert sum(result.backfilled for result in results) == 1
    protect.event_lpr_track.assert_awaited_once()
    protect.event_thumbnail.assert_awaited_once()
    assert not await rows(GateCommandRecord)
