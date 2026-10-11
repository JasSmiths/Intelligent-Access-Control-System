"""First-capture and immutable intake contracts; disposable PostgreSQL, inert gate."""
import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from test_recovery_boundaries import isolated_resources as isolated_resources

from app.db.session import AsyncSessionLocal
from app.models import LprCoordinatorState, LprIngestEvent, Vehicle
from app.modules.gate.base import GateState
from app.modules.lpr.base import PlateRead
from app.modules.lpr.ubiquiti import UbiquitiLprAdapter
from app.services import access_events, lpr_ingest
from app.services.access.pipeline_contracts import TrustedGateObservation
from app.services.access.pipeline_store import database_now
from app.services.access.read_codec import read_from_ingest
from app.services.access.reads import (
    EXTERNAL_ADMISSION_PAYLOAD_KEY,
    GATE_MALFUNCTION_PAYLOAD_KEY,
    GATE_OBSERVATION_PAYLOAD_KEY,
    KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY,
    LPR_INGEST_EVENT_PAYLOAD_KEY,
    PRESERVE_GATE_OBSERVATION_PAYLOAD_KEY,
    PROCESSING_ATTEMPT_PAYLOAD_KEY,
    VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY,
)
from app.services.lpr_ingest import LprIngestRepository

pytestmark=pytest.mark.asyncio
NOW=datetime(2026,10,7,12,tzinfo=UTC)


@pytest_asyncio.fixture(autouse=True)
async def intake_resources(isolated_resources,monkeypatch):
    async with AsyncSessionLocal() as session:
        await session.execute(text('TRUNCATE lpr_coordinator_state, lpr_ingest_events CASCADE'))
        await session.commit()
    monkeypatch.setattr(access_events,'is_maintenance_mode_active',AsyncMock(return_value=False))


def read(**kwargs):
    return PlateRead(registration_number='AB12CDE',confidence=.9,source='synthetic',captured_at=NOW,
        raw_payload=kwargs or {'first':'retained'},candidate_registration_numbers=('AB12CDE','AB12CDF'))


async def persist(repository,value,key,received_at=NOW):
    return await repository.persist_read(value,received_at=received_at,idempotency_key=key,
        normalized_payload=access_events.AccessEventService()._lpr_ingest_normalized_payload(value))


async def ready_rows():
    async with AsyncSessionLocal() as session:
        return list(await session.scalars(select(LprIngestEvent).where(
            LprIngestEvent.observation_state == 'ready',
            LprIngestEvent.status == 'pending',
        ).order_by(LprIngestEvent.ready_ordinal).limit(50)))


async def captured_read():
    async with AsyncSessionLocal() as session:
        return read_from_ingest(await session.scalar(select(LprIngestEvent)))


@pytest.mark.parametrize('terminal_status',['succeeded','failed','skipped','held'])
async def test_duplicate_during_capture_and_after_terminal_never_rewrites_or_recaptures(monkeypatch,terminal_status):
    started,release=asyncio.Event(),asyncio.Event()
    async def current_state():
        started.set()
        await release.wait()
        return GateState.OPEN
    gate=SimpleNamespace(current_state=AsyncMock(side_effect=current_state))
    monkeypatch.setattr(access_events,'get_gate_controller',lambda key:gate)
    service=access_events.AccessEventService()
    first=asyncio.create_task(service.enqueue_plate_read(read()))
    await asyncio.wait_for(started.wait(),5)
    await service.enqueue_plate_read(replace(read(),confidence=.1,raw_payload={'replacement':True}))
    assert (await service.backlog_status())['queue_depth']==0
    release.set()
    await asyncio.wait_for(first,5)
    assert (await service.backlog_status())['queue_depth']==1
    gate.current_state.assert_awaited_once()
    async with AsyncSessionLocal() as session:
        row=await session.scalar(select(LprIngestEvent))
        original=(row.normalized_payload,row.original_observation,row.received_at,row.ready_ordinal)
        identity=row.id
        assert row.observation_state=='ready' and row.original_observation['state']=='open'
        assert row.normalized_payload['raw_payload']['first']=='retained'
        row.status=terminal_status
        row.processed_at=NOW
        await session.commit()
    await service.enqueue_plate_read(replace(read(),raw_payload={'later':'must not win'}))
    async with AsyncSessionLocal() as session:
        row=await session.get(LprIngestEvent,identity)
        assert row.status==terminal_status and row.processed_at==NOW
        assert (row.normalized_payload,row.original_observation,row.received_at,row.ready_ordinal)==original
        decoded=read_from_ingest(row)
        assert decoded.raw_payload[GATE_OBSERVATION_PAYLOAD_KEY]==original[1]
    gate.current_state.assert_awaited_once()
    assert (await service.backlog_status())['queue_depth']==0


async def test_capture_timeout_persists_original_unknown_after_owned_cancellation(monkeypatch):
    settled=asyncio.Event()
    async def current_state():
        try:
            await asyncio.Event().wait()
        finally:
            settled.set()
    gate=SimpleNamespace(current_state=AsyncMock(side_effect=current_state))
    monkeypatch.setattr(access_events,'get_gate_controller',lambda key:gate)
    monkeypatch.setattr(access_events,'LPR_GATE_CAPTURE_TIMEOUT_SECONDS',.02)
    service=access_events.AccessEventService()
    await service.enqueue_plate_read(read())
    assert settled.is_set()
    async with AsyncSessionLocal() as session:
        row=await session.scalar(select(LprIngestEvent))
        assert row.observation_state=='ready' and row.original_observation['state']=='unknown'
        original=dict(row.original_observation)
        identity=row.id
    await service.enqueue_plate_read(read())
    async with AsyncSessionLocal() as session:
        assert (await session.get(LprIngestEvent,identity)).original_observation==original
    gate.current_state.assert_awaited_once()


async def test_live_capture_recovery_and_late_capture_are_fenced():
    repository=LprIngestRepository()
    row,winner=await persist(repository,read(),'capture')
    identity,token=row.id,row.capture_token
    assert winner and token is not None
    assert await repository.hold_expired_observations()==0
    assert await ready_rows()==[]
    duplicate,winner=await persist(repository,replace(read(),raw_payload={'replacement':True}),'capture',NOW+timedelta(days=1))
    assert not winner and duplicate.capture_token==token and duplicate.received_at==NOW
    async with AsyncSessionLocal() as session:
        stored=await session.get(LprIngestEvent,identity,with_for_update=True)
        stored.capture_until=await database_now(session)-timedelta(seconds=1)
        await session.commit()
    observation={'state':'open','observed_at':NOW.isoformat(),'controller':'synthetic'}
    assert not await repository.complete_observation(identity,token,observation)
    assert await repository.hold_expired_observations()==1
    assert not await repository.complete_observation(identity,token,observation)
    async with AsyncSessionLocal() as session:
        stored=await session.get(LprIngestEvent,identity)
        assert stored.status=='held' and stored.terminal_reason=='original_gate_observation_unavailable'
        assert stored.original_observation is None and stored.ready_ordinal is None
    duplicate,winner=await persist(repository,read(),'capture')
    assert not winner and duplicate.status=='held'


async def test_ready_order_tracks_observation_commit_and_keeps_original_receipt():
    repository=LprIngestRepository()
    earlier,_=await persist(repository,read(),'earlier',NOW)
    later,_=await persist(repository,replace(read(),captured_at=NOW+timedelta(seconds=1)),'later',NOW+timedelta(seconds=2))
    observation={'state':'closed','observed_at':NOW.isoformat(),'controller':'synthetic'}
    assert await repository.complete_observation(later.id,later.capture_token,observation)
    assert await repository.complete_observation(earlier.id,earlier.capture_token,observation)
    assert not await repository.complete_observation(later.id,later.capture_token,{'state':'open','observed_at':NOW.isoformat()})
    ready=await ready_rows()
    assert [row.id for row in ready]==[later.id,earlier.id]
    assert ready[0].ready_ordinal < ready[1].ready_ordinal
    assert ready[1].received_at==NOW and ready[0].received_at==NOW+timedelta(seconds=2)
    assert all(row.original_observation==observation for row in ready)


async def test_maintenance_intake_is_durably_held_without_gate_capture(monkeypatch):
    monkeypatch.setattr(access_events,'is_maintenance_mode_active',AsyncMock(return_value=True))
    def forbidden(key):
        raise AssertionError('Maintenance must not call gate observation owner')
    monkeypatch.setattr(access_events,'get_gate_controller',forbidden)
    service=access_events.AccessEventService()
    await service.enqueue_plate_read(read())
    async with AsyncSessionLocal() as session:
        row=await session.scalar(select(LprIngestEvent))
        assert row.status=='skipped' and row.observation_state=='suppressed'
        assert row.terminal_reason=='maintenance_mode_active'
        assert row.original_observation is None and row.normalized_payload['raw_payload']['first']=='retained'
    assert (await service.backlog_status())['queue_depth']==0


@pytest.mark.parametrize('observation',[{}, {'state':{}}, {'state':'open','observed_at':'bad'}])
async def test_malformed_first_observation_cannot_publish_readiness(observation):
    repository=LprIngestRepository()
    row,_=await persist(repository,read(),'malformed')
    with pytest.raises(ValueError,match='canonical original'):
        await repository.complete_observation(row.id,row.capture_token,observation)
    assert await ready_rows()==[]


async def test_pause_after_ready_flush_cannot_commit_expired_capture(monkeypatch):
    repository=LprIngestRepository()
    row,_=await persist(repository,read(),'paused')
    identity,token=row.id,row.capture_token
    calls=0
    deadline=None
    async def checked_clock(session):
        nonlocal calls,deadline
        calls+=1
        now=await database_now(session)
        if calls==1:
            # Shorten this owned row's lease after taking its lock; the first
            # validation cannot accidentally already be expired under host load.
            deadline=now+timedelta(milliseconds=100)
            stored=await session.get(LprIngestEvent,identity)
            stored.capture_until=deadline
        elif calls==2:
            assert not session.dirty and not session.new and not session.deleted
            stored=await session.get(LprIngestEvent,identity)
            assert stored.capture_token is None and stored.capture_until is None
            assert deadline is not None
            async with AsyncSessionLocal() as observer:
                while await database_now(observer)<=deadline:
                    await asyncio.sleep(.01)
        return await database_now(session)
    monkeypatch.setattr(lpr_ingest,'database_now',checked_clock)
    assert not await repository.complete_observation(identity,token,{'state':'open','observed_at':NOW.isoformat()})
    assert calls==2
    async with AsyncSessionLocal() as session:
        stored=await session.get(LprIngestEvent,identity)
        assert stored.observation_state=='observing' and stored.ready_ordinal is None
        assert stored.original_observation is None and stored.capture_token==token
        assert await session.get(LprCoordinatorState,1) is None


async def test_ready_capture_commits_while_worker_coordinator_and_domain_locks_are_held():
    repository=LprIngestRepository()
    row,_=await persist(repository,read(),'independent-ready')
    from app.services.access.pipeline_store import AccessPipelineStore
    claim=await AccessPipelineStore().acquire_coordinator()
    assert claim is not None
    async with AsyncSessionLocal() as blocked_window:
        await blocked_window.get(LprCoordinatorState,1,with_for_update=True)
        # This second lock represents the caller's required domain/audit work.
        await blocked_window.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('fixture:required-domain',0))"))
        completed=await asyncio.wait_for(repository.complete_observation(row.id,row.capture_token,
            {'state':'closed','observed_at':NOW.isoformat()}),5)
        assert completed is not None and completed.ready_ordinal is not None
        async with AsyncSessionLocal() as observer:
            saved=await observer.get(LprIngestEvent,row.id)
            assert saved.observation_state=='ready' and saved.original_observation['state']=='closed'
        await blocked_window.rollback()


async def test_webhook_forged_owner_fields_are_inspectable_but_not_operational(monkeypatch):
    forged={GATE_OBSERVATION_PAYLOAD_KEY:{'state':'open','observed_at':NOW.isoformat()},
        PRESERVE_GATE_OBSERVATION_PAYLOAD_KEY:True, KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY:{'exact':True},
        GATE_MALFUNCTION_PAYLOAD_KEY:{'id':'forged'},VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY:{'kind':'departure'},
        EXTERNAL_ADMISSION_PAYLOAD_KEY:{'mode':'arrival'}, PROCESSING_ATTEMPT_PAYLOAD_KEY:999,
        LPR_INGEST_EVENT_PAYLOAD_KEY:{'id':'forged'}, 'unrelated_vendor_evidence':{'ordered':[2,1]}}
    incoming=UbiquitiLprAdapter().to_plate_read({'registrationNumber':'AB12CDE','confidence':.9,
        'capturedAt':NOW.isoformat(),**forged})
    gate=SimpleNamespace(current_state=AsyncMock(return_value=GateState.CLOSED))
    monkeypatch.setattr(access_events,'get_gate_controller',lambda key:gate)
    service=access_events.AccessEventService()
    await service.enqueue_plate_read(incoming)
    queued=await captured_read()
    gate.current_state.assert_awaited_once()
    assert queued.raw_payload[GATE_OBSERVATION_PAYLOAD_KEY]['state']=='closed'
    assert queued.raw_payload['unrelated_vendor_evidence']=={'ordered':[2,1]}
    for key in forged.keys()-{GATE_OBSERVATION_PAYLOAD_KEY,LPR_INGEST_EVENT_PAYLOAD_KEY,'unrelated_vendor_evidence'}:
        assert key not in queued.raw_payload
    async with AsyncSessionLocal() as session:
        saved=await session.scalar(select(LprIngestEvent))
        original=saved.normalized_payload['raw_payload']
        assert all(original[key]==value for key,value in forged.items())
        assert queued==read_from_ingest(saved)
        session.add(Vehicle(registration_number='AB12CDE',is_active=True))
        await session.commit()
    classified=await service._read_with_known_vehicle_match(queued)
    assert classified.raw_payload[KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY]['exact'] is True


async def test_trusted_observation_uses_explicit_typed_port_and_canonical_live_recovery(monkeypatch):
    def forbidden(key):
        raise AssertionError('Explicit trusted observation must not recapture')
    monkeypatch.setattr(access_events,'get_gate_controller',forbidden)
    service=access_events.AccessEventService()
    trusted=TrustedGateObservation(GateState.OPEN,NOW,controller='synthetic',entity_id='cover.synthetic')
    await service.enqueue_plate_read(read(),observation=trusted)
    queued=await captured_read()
    async with AsyncSessionLocal() as session:
        row=await session.scalar(select(LprIngestEvent))
        assert queued==read_from_ingest(row)
        assert row.original_observation==trusted.as_payload()
    with pytest.raises(ValueError,match='canonical state'):
        TrustedGateObservation('not-an-enum',NOW)


async def test_ready_publication_lock_serializes_commit_order_without_worker_lock(monkeypatch):
    repository=LprIngestRepository()
    first,_=await persist(repository,read(),'ordered-first')
    second,_=await persist(repository,replace(read(),captured_at=NOW+timedelta(seconds=1)),'ordered-second')
    flushed,release=asyncio.Event(),asyncio.Event()
    calls=0
    async def controlled_clock(session):
        nonlocal calls
        if asyncio.current_task().get_name()=='first-ready':
            calls+=1
            if calls==2:
                assert not session.dirty and not session.new and not session.deleted
                stored=await session.get(LprIngestEvent,first.id)
                assert stored.capture_token is None and stored.capture_until is None
                flushed.set()
                await release.wait()
        return await database_now(session)
    monkeypatch.setattr(lpr_ingest,'database_now',controlled_clock)
    observation={'state':'closed','observed_at':NOW.isoformat()}
    a=asyncio.create_task(repository.complete_observation(first.id,first.capture_token,observation),name='first-ready')
    await asyncio.wait_for(flushed.wait(),5)
    b=asyncio.create_task(repository.complete_observation(second.id,second.capture_token,observation),name='second-ready')
    try:
        async with AsyncSessionLocal() as observer:
            async def blocked():
                while not await observer.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted)")):
                    await observer.execute(text('SELECT pg_stat_clear_snapshot()'))
                    await asyncio.sleep(.01)
            await asyncio.wait_for(blocked(),5)
            assert not b.done()
            assert list(await observer.scalars(select(LprIngestEvent.ready_ordinal).where(LprIngestEvent.ready_ordinal.is_not(None))))==[]
        release.set()
        result_a,result_b=await asyncio.wait_for(asyncio.gather(a,b),5)
        assert result_a is not None and result_b is not None
        assert result_a.ready_ordinal < result_b.ready_ordinal
        assert [row.id for row in await ready_rows()]==[first.id,second.id]
    finally:
        release.set()
        for task in (a,b):
            if not task.done(): task.cancel()
        await asyncio.gather(a,b,return_exceptions=True)
