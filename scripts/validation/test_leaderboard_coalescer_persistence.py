"""Chart generation progress and original notification authority on isolated PG."""

import asyncio
import socket
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select, text, update
from test_recovery_boundaries import DATABASE_NAME

from app.db.session import AsyncSessionLocal, engine
from app.models import AccessChartDirtyState, AccessEvent, LeaderboardState, NotificationRun, Person, Vehicle
from app.models.enums import AccessDecision, AccessDirection
from app.services import leaderboard as owner
from app.services.leaderboard import KNOWN_TOP_STATE_KEY, LeaderboardService
from app.services.leaderboard_dirty import ChartLeaseLost, LeaderboardDirtyStore
from app.services.leaderboard_notifications import chart_notification_id, chart_notification_origin
from app.services.notification_authorization import NotificationAuthorization
from app.services.notification_runs import ClaimLost, NotificationRunStore
from app.services.settings import get_runtime_config

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def resources(monkeypatch):
    original = socket.socket.connect
    def connect(sock, address):
        assert isinstance(address, tuple) and address[:2] == ('127.0.0.1', 5432), 'Unexpected chart socket'
        return original(sock, address)
    async def no_http(*args, **kwargs):
        raise AssertionError('Chart fixture attempted a provider request')
    monkeypatch.setattr(socket.socket, 'connect', connect)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', no_http)
    monkeypatch.setattr(owner.event_bus, 'publish', AsyncMock())
    notice = SimpleNamespace(run_store=NotificationRunStore(), dispatcher=SimpleNamespace(wake=Mock()))
    monkeypatch.setattr(owner, 'get_notification_service', lambda: notice)
    async with AsyncSessionLocal() as session:
        assert await session.scalar(text('SELECT current_database()')) == DATABASE_NAME
        assert await session.scalar(text('SELECT count(*) FROM system_settings')) == 0
        await session.execute(text('TRUNCATE access_chart_dirty_state,leaderboard_state,notification_runs,access_events,people,vehicles CASCADE'))
        await session.commit()
    try:
        yield notice
    finally:
        await engine.dispose()


async def clock():
    async with AsyncSessionLocal() as session:
        return await session.scalar(select(func.clock_timestamp()))


async def ready():
    async with AsyncSessionLocal() as session:
        await session.execute(update(AccessChartDirtyState).values(first_dirty_at=func.clock_timestamp()-text("interval '11 seconds'")))
        await session.commit()


async def state():
    async with AsyncSessionLocal() as session:
        return await session.get(AccessChartDirtyState, 1)


async def notices():
    async with AsyncSessionLocal() as session:
        return list(await session.scalars(select(NotificationRun).order_by(NotificationRun.queued_at, NotificationRun.id)))


async def add_winner(service, index, count, *, live=True, origin=None):
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        person = Person(first_name=f'Synthetic{index}', display_name=f'Synthetic {index}')
        vehicle = Vehicle(registration_number=f'SYN{index}')
        session.add_all([person, vehicle]); await session.flush()
        events = [AccessEvent(id=uuid.uuid4(), registration_number=vehicle.registration_number,
            vehicle_id=vehicle.id, person_id=person.id, occurred_at=now,
            decision=AccessDecision.GRANTED, direction=AccessDirection.ENTRY, confidence=.99, source='synthetic-chart') for _ in range(count)]
        session.add_all(events); await session.flush()
        await service.mark_access_in_session(session, events[-1], origin_at=(origin or now) if live else None)
        await session.commit()
        return events[-1].id


async def prior():
    async with AsyncSessionLocal() as session:
        session.add(LeaderboardState(key=KNOWN_TOP_STATE_KEY, registration_number='OLD', read_count=1))
        await session.commit()


async def test_participant_rollback_quiet_window_and_maximum_batch_wait():
    service = LeaderboardService()
    async with AsyncSessionLocal() as session:
        await service.mark_dirty_in_session(session)
        async with AsyncSessionLocal() as observer:
            assert await observer.get(AccessChartDirtyState, 1) is None
        await session.rollback()
    assert await state() is None
    async with AsyncSessionLocal() as session:
        await service.mark_dirty_in_session(session); await session.commit()
    assert await service.dirty_store.claim() is None
    await ready()  # Last mark remains fresh; first mark enforces the ten-second cap.
    claim = await service.dirty_store.claim()
    assert claim.generation == 1 and claim.live_origin_at is None
    assert await service.dirty_store.claim() is None
    await service.evaluate_claim(claim)
    assert (await state()).acknowledged_generation == 1


async def test_continuous_arrivals_during_every_aggregation_do_not_starve_leader_progress(monkeypatch):
    service = LeaderboardService(); await prior()
    await add_winner(service, 1, 2)
    original = service._current_top_known_leader
    iteration = 1
    async def aggregate(session):
        nonlocal iteration
        top = await original(session)
        iteration += 1
        # This commits while aggregation still owns its read session, proving no
        # dirty/source mutation lock is held. Every aggregation gets a trailing B.
        await add_winner(service, iteration, iteration * 3)
        return top
    monkeypatch.setattr(service, '_current_top_known_leader', aggregate)
    for expected in range(1, 4):
        await ready(); claim = await service.dirty_store.claim()
        await service.evaluate_claim(claim)
        current = await state()
        assert current.acknowledged_generation == expected
        assert current.requested_generation == expected + 1 and current.first_dirty_at is not None
        async with AsyncSessionLocal() as session:
            assert (await session.get(LeaderboardState, KNOWN_TOP_STATE_KEY)).registration_number == f'SYN{expected}'
        assert len(await notices()) == expected
        assert (await notices())[-1].context['leaderboard_origin']['generation'] == expected


@pytest.mark.parametrize('trailing_live', [False, True])
async def test_claimed_a_authority_survives_silent_trailing_without_origin_refresh(trailing_live):
    service = LeaderboardService(); await prior()
    origin_a = await clock() - timedelta(seconds=40)
    identity_a = await add_winner(service, 1, 2, origin=origin_a)
    await ready(); claim_a = await service.dirty_store.claim()
    async with AsyncSessionLocal() as session:
        await service.mark_dirty_in_session(session); await session.commit()
    if trailing_live:
        identity_b = await add_winner(service, 2, 4)
    await service.evaluate_claim(claim_a)
    saved = (await notices())[0]
    assert saved.id == chart_notification_id(claim_a.generation)
    assert saved.context['leaderboard_origin'] == chart_notification_origin(claim_a.generation, identity_a, origin_a)
    current = await state()
    assert current.acknowledged_generation == claim_a.generation < current.requested_generation
    assert current.live_event_id == (identity_b if trailing_live else None)
    assert current.live_origin_at != origin_a
    await ready(); trailing = await service.dirty_store.claim()
    assert trailing.live_event_id == (identity_b if trailing_live else None)


async def test_silent_only_claim_and_first_leader_initialize_without_notice():
    service = LeaderboardService()
    await add_winner(service, 1, 2)
    await ready(); await service.evaluate_claim(await service.dirty_store.claim())
    assert not await notices()
    await add_winner(service, 2, 4, live=False)
    await ready(); await service.evaluate_claim(await service.dirty_store.claim())
    assert not await notices()
    async with AsyncSessionLocal() as session:
        assert (await session.get(LeaderboardState, KNOWN_TOP_STATE_KEY)).registration_number == 'SYN2'


@pytest.mark.parametrize('trailing_live', [False, True])
async def test_crash_reclaim_uses_latest_generation_with_original_provenance(trailing_live):
    service = LeaderboardService(); await prior()
    await add_winner(service, 1, 2)
    await ready(); old = await service.dirty_store.claim()
    identity_b = await add_winner(service, 2, 4, live=trailing_live)
    async with AsyncSessionLocal() as session:
        await session.execute(update(AccessChartDirtyState).values(lease_until=func.clock_timestamp()-text("interval '1 second'")))
        await session.commit()
    await ready(); recovered = await service.dirty_store.claim()
    assert recovered.generation == old.generation + 1 and recovered.token != old.token
    assert recovered.live_event_id == (identity_b if trailing_live else None)
    with pytest.raises(ChartLeaseLost):
        await service.evaluate_claim(old)
    await service.evaluate_claim(recovered)
    assert len(await notices()) == int(trailing_live)
    assert (await state()).acknowledged_generation == recovered.generation


async def test_expired_original_deadline_commits_leader_without_new_notice():
    service = LeaderboardService(); await prior()
    await add_winner(service, 1, 2, origin=await clock()-timedelta(seconds=301))
    await ready(); await service.evaluate_claim(await service.dirty_store.claim())
    assert not await notices() and (await state()).acknowledged_generation == 1


async def test_notification_intake_failure_rolls_back_leader_and_ack_then_same_claim_retries(resources, monkeypatch):
    service = LeaderboardService(); await prior()
    await add_winner(service, 1, 2); await ready(); claim = await service.dirty_store.claim()
    original = resources.run_store.enqueue_in_session
    monkeypatch.setattr(resources.run_store, 'enqueue_in_session', AsyncMock(side_effect=RuntimeError('synthetic intake failure')))
    with pytest.raises(RuntimeError, match='synthetic intake'):
        await service.evaluate_claim(claim)
    assert (await state()).acknowledged_generation == 0 and not await notices()
    async with AsyncSessionLocal() as session:
        assert (await session.get(LeaderboardState, KNOWN_TOP_STATE_KEY)).registration_number == 'OLD'
    monkeypatch.setattr(resources.run_store, 'enqueue_in_session', original)
    await service.evaluate_claim(claim)
    assert len(await notices()) == 1 and (await state()).acknowledged_generation == 1


async def notification_run(*, origin=True, age=0):
    store = NotificationRunStore(); identity = chart_notification_id(100)
    payload = {'event_type':'leaderboard_overtake','subject':'Synthetic chart','severity':'info','facts':{}}
    if origin:
        payload['leaderboard_origin'] = chart_notification_origin(100, uuid.uuid4(), await clock()-timedelta(seconds=age))
    await store.create(payload, run_id=identity)
    row = await store.claim(identity)
    await store.save_plan(identity, row.claim_token, [{'state':'pending','rule':{'id':'synthetic'},
        'action':{'id':'synthetic','type':'in_app','title':'Synthetic','message':'Synthetic'}}])
    return store, identity, row.claim_token


@pytest.mark.parametrize('legacy', [False, True])
async def test_late_queued_and_legacy_unattempted_notices_retire_before_attempt(legacy):
    store, identity, token = await notification_run(origin=not legacy, age=301)
    authority = NotificationAuthorization(config=AsyncMock(return_value=await get_runtime_config()), actionable=Mock())
    with pytest.raises(ClaimLost):
        await store.begin_action(identity, token, 0, authorize_origin=authority.authorize_attempt)
    row = await store.get(identity)
    assert row.delivery_plan[0]['state'] == 'skipped'
    assert row.delivery_plan[0]['reason'] == ('leaderboard_origin_unavailable' if legacy else 'leaderboard_origin_expired')


async def test_final_authority_after_blocking_configuration_uses_fresh_database_clock():
    store, identity, token = await notification_run(age=298)
    entered = asyncio.Event()
    waiter_pid = None
    runtime = await get_runtime_config()
    async def config(session):
        nonlocal waiter_pid
        waiter_pid = await session.scalar(text('SELECT pg_backend_pid()'))
        entered.set()
        await session.execute(text('SELECT pg_advisory_xact_lock(734221)'))
        return runtime
    authority = NotificationAuthorization(config=config, actionable=Mock())
    async def final(session, payload, run_id):
        _, policy = await authority.authorize_attempt_with_config(session, payload, run_id, final=True)
        return policy
    async with AsyncSessionLocal() as blocker:
        blocker_pid = await blocker.scalar(text('SELECT pg_backend_pid()'))
        await blocker.execute(text('SELECT pg_advisory_xact_lock(734221)'))
        task = asyncio.create_task(store.begin_action(identity, token, 0,
            authorize_origin=authority.authorize_attempt, refresh_authorization=final))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            async with AsyncSessionLocal() as observer:
                for _ in range(100):
                    blocking = await observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid':waiter_pid})
                    if blocker_pid in blocking:
                        break
                    await asyncio.sleep(.01)
                assert blocker_pid in blocking
            # Actual final configuration work blocks through the ORIGINAL deadline.
            await blocker.execute(text('SELECT pg_sleep(2.1)'))
        finally:
            await blocker.rollback()
            if not entered.is_set():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        with pytest.raises(ClaimLost):
            await asyncio.wait_for(task, 5)
    assert (await store.get(identity)).delivery_plan[0]['reason'] == 'leaderboard_origin_expired'


async def test_expired_origin_does_not_reauthorize_or_replay_already_attempted_recovery():
    store, identity, token = await notification_run(age=301)
    # This checkpoint predates expiry; simulate process loss after actual attempt.
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationRun, identity)
        row.delivery_plan = [{**row.delivery_plan[0], 'state':'attempting'}]
        row.lease_expires_at = await session.scalar(select(func.clock_timestamp()))-timedelta(seconds=1)
        await session.commit()
    assert await store.claim(identity) is None
    row = await store.get(identity)
    assert row.status == 'review_required' and row.delivery_plan[0]['state'] == 'unknown'
    assert row.review_reason == 'provider_outcome_unknown'
