"""Actual slot/close ownership; genuine PG fixtures cover database authority."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from test_process_lifecycle import owned_resources as owned_resources

from app import main
from app.services.access import completion_worker
from app.services.access.completion_database import access_session
from app.services.access.completion_store import AccessCompletionStore
from app.services.access.completion_worker import AccessCompletionOwner, CompletionStageOwners
from app.services.access.pipeline_contracts import CompletionClaim, PipelineLeaseLost
from app.services.access.publication import (
    AccessPublicationStore,
    PublicationClaim,
    PublicationMessage,
)
from app.services.access.publication_worker import AccessPublicationLane
from app.services.realtime_contracts import RealtimePublishReceipt


def claim(stage="visual"):
    now = datetime.now(UTC)
    return CompletionClaim(uuid.uuid4(), uuid.uuid4(), stage, uuid.uuid4(), now,
                           now + timedelta(seconds=300), 1, ("fixture",))


class CompletionStore(AccessCompletionStore):
    def __init__(self, claims=()):
        self.claims = list(claims)
        self.capacities = []
        self.retried = []
        self.renew_lost = False
        self.budget = 90

    async def claim(self, available_slots):
        self.capacities.append(available_slots)
        result = self.claims[:available_slots]
        del self.claims[:available_slots]
        return result

    async def assert_owned(self, claimed):
        return asyncio.get_running_loop().time() + self.budget

    async def renew(self, claimed):
        if self.renew_lost:
            raise PipelineLeaseLost("fixture_lease_lost")

    async def retry(self, claimed, *, delay_seconds=1):
        self.retried.append(claimed)


class PublicationStore(AccessPublicationStore):
    def __init__(self):
        self.pending = PublicationClaim(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        self.acked = asyncio.Event()

    async def claim(self):
        result, self.pending = self.pending, None
        return result

    async def load_message(self, claimed):
        return PublicationMessage(1, "access_event.finalized", {"resource_revision": 1})

    async def assert_owned(self, claimed):
        return asyncio.get_running_loop().time() + 120

    async def renew(self, claimed):
        return None

    async def acknowledge(self, claimed, revision):
        self.acked.set()
        return False

    async def retry(self, claimed):
        raise AssertionError("inert successful publication must not retry")


class Transport:
    async def publish(self, event_type, payload):
        return RealtimePublishReceipt("in_process", True)


def owner(store, port):
    publication = AccessPublicationLane(PublicationStore(), Transport())
    return AccessCompletionOwner(store, CompletionStageOwners(port, port, port, port), publication)


@pytest.mark.asyncio
@pytest.mark.parametrize("normal_exit", [True, False])
async def test_real_session_close_retained_through_repeated_cancellation(monkeypatch, normal_exit):
    closing, release, active = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = AsyncSession.close
    closes = 0

    async def blocked_close(session):
        nonlocal closes
        closes += 1
        closing.set()
        await release.wait()
        await original(session)

    monkeypatch.setattr(AsyncSession, "close", blocked_close)

    async def operation():
        async with access_session(AsyncSession):
            active.set()
            if not normal_exit:
                await asyncio.Event().wait()

    task = asyncio.create_task(operation())
    await active.wait()
    if not normal_exit:
        task.cancel()
    await closing.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done() and closes == 1
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closes == 1


@pytest.mark.asyncio
async def test_two_actual_slots_include_timed_out_cleanup_but_publication_is_independent(monkeypatch):
    store = CompletionStore([claim(), claim("snapshot"), claim("zone_shadow")])
    store.budget = 0.01
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    started, cancelled = [], []

    async def port(claimed, deadline):
        started.append(claimed)
        if len(started) == 2:
            entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(claimed)
            if len(cancelled) == 2:
                cleaning.set()
            await release.wait()

    worker = owner(store, port)
    await worker.start()
    await entered.wait()
    await cleaning.wait()
    await worker.publication.store.acked.wait()
    await asyncio.sleep(0.02)
    assert len(started) == len(worker._operations) == 2
    assert len(store.claims) == 1 and not store.retried
    stop = asyncio.create_task(worker.stop())
    await asyncio.sleep(0)
    stop.cancel()
    again = asyncio.create_task(worker.stop())
    await asyncio.sleep(0.01)
    stop.cancel()
    assert not stop.done() and not again.done()
    release.set()
    await asyncio.gather(stop, again, return_exceptions=True)
    assert not worker._operations and worker._task is None
    assert worker.publication._task is None and len(started) == 2
    assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()
                and task.get_name().startswith("access-completion")]


@pytest.mark.asyncio
async def test_explicit_closed_stage_ports_receive_exact_claim_and_publication_cannot_dispatch():
    called = []

    def port(stage):
        async def execute(claimed, deadline):
            called.append((stage, claimed))
        return execute

    store = CompletionStore()
    worker = AccessCompletionOwner(store, CompletionStageOwners(
        port("visual"), port("snapshot"), port("visitor_link"), port("zone_shadow")
    ), AccessPublicationLane(PublicationStore(), Transport()))
    for stage in ("visual", "snapshot", "visitor_link", "zone_shadow"):
        claimed = claim(stage)
        await worker._process(claimed)
        assert called[-1] == (stage, claimed)
    with pytest.raises(ValueError):
        await worker._dispatch(claim("publication"), asyncio.get_running_loop().time() + 1)
    assert len(called) == 4 and not store.retried


@pytest.mark.asyncio
async def test_renewal_loss_single_cancels_cleanup_and_does_not_retry_as_timeout(monkeypatch):
    monkeypatch.setattr(completion_worker, "COMPLETION_RENEWAL_SECONDS", 0.01)
    monkeypatch.setattr(completion_worker, "COMPLETION_TIMEOUT_SECONDS", 0.04)
    store = CompletionStore()
    store.renew_lost = True
    cleaning, release = asyncio.Event(), asyncio.Event()
    cancellations = 0

    async def port(claimed, deadline):
        nonlocal cancellations
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellations += 1
            cleaning.set()
            await release.wait()
            raise

    worker = owner(store, port)
    task = asyncio.create_task(worker._process(claim()))
    await cleaning.wait()
    await asyncio.sleep(0.06)
    assert not task.done() and cancellations == 1 and not store.retried
    release.set()
    await task
    assert cancellations == 1 and not store.retried


@pytest.mark.asyncio
async def test_cancelled_preparation_never_enters_any_stage_and_retains_actual_close(monkeypatch):
    closing, release, query = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = AsyncSession.close

    async def blocked_close(session):
        closing.set()
        await release.wait()
        await original(session)

    monkeypatch.setattr(AsyncSession, "close", blocked_close)

    class PreparingStore(CompletionStore):
        async def assert_owned(self, claimed):
            async with access_session(AsyncSession):
                query.set()
                await asyncio.Event().wait()

    async def forbidden(claimed, deadline):
        raise AssertionError("cancelled preparation cannot start a stage")

    worker = owner(PreparingStore(), forbidden)
    task = asyncio.create_task(worker._process(claim()))
    await query.wait()
    task.cancel()
    await closing.wait()
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_publication_stop_retains_one_cancelled_transport_cleanup_under_repeated_stop():
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancellations = 0

    class Port:
        async def publish(self, event_type, payload):
            nonlocal cancellations
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellations += 1
                cleaning.set()
                await release.wait()
                raise

    store = PublicationStore()
    lane = AccessPublicationLane(store, Port())
    await lane.start()
    await started.wait()
    first = asyncio.create_task(lane.stop())
    await cleaning.wait()
    first.cancel()
    second = asyncio.create_task(lane.stop())
    first.cancel()
    await asyncio.sleep(0.01)
    assert not first.done() and not second.done() and cancellations == 1
    release.set()
    await asyncio.gather(first, second, return_exceptions=True)
    assert lane._task is None and cancellations == 1 and not store.acked.is_set()


@pytest.mark.asyncio
async def test_session_close_consumes_original_start_deadline_without_dispatch(monkeypatch):
    closing, release = asyncio.Event(), asyncio.Event()
    actual = AsyncSession.close
    async def close(session):
        closing.set()
        await release.wait()
        await actual(session)
    monkeypatch.setattr(AsyncSession, "close", close)
    class DelayedStore(CompletionStore):
        async def assert_owned(self, claimed):
            async with access_session(AsyncSession):
                return asyncio.get_running_loop().time()+.01
    store = DelayedStore()
    calls = []
    async def stage(claimed, deadline):
        calls.append(claimed)
    task = asyncio.create_task(owner(store, stage)._process(claim()))
    await closing.wait()
    await asyncio.sleep(.02)
    release.set()
    await task
    assert not calls and len(store.retried) == 1


@pytest.mark.asyncio
async def test_publication_close_past_current_lease_never_sends(monkeypatch):
    closing, release = asyncio.Event(), asyncio.Event()
    actual = AsyncSession.close
    async def close(session):
        closing.set()
        await release.wait()
        await actual(session)
    monkeypatch.setattr(AsyncSession, "close", close)
    class DelayedStore(PublicationStore):
        async def assert_owned(self, claimed):
            async with access_session(AsyncSession):
                return asyncio.get_running_loop().time()+.01
    class ForbiddenTransport:
        async def publish(self, *_args):
            raise AssertionError("Expired lease cannot send")
    store = DelayedStore()
    task = asyncio.create_task(AccessPublicationLane(store, ForbiddenTransport()).deliver(store.pending))
    await closing.wait()
    await asyncio.sleep(.02)
    release.set()
    with pytest.raises(PipelineLeaseLost):
        await task
    assert not store.acked.is_set()


@pytest.mark.asyncio
async def test_injected_completion_owner_main_lifespan_retains_real_close_before_database_disposal(owned_resources, monkeypatch):
    # Composition proof only: production activation awaits the atomic core and
    # frontend-consumer cutover; no provider/resource ordering is claimed here.
    events, _ = owned_resources
    closing, release = asyncio.Event(), asyncio.Event()
    original = AsyncSession.close
    async def close(session):
        closing.set()
        await release.wait()
        await original(session)
    monkeypatch.setattr(AsyncSession, "close", close)
    async def stage(claimed, deadline):
        async with access_session(AsyncSession):
            pass
    worker = owner(CompletionStore([claim()]), stage)
    monkeypatch.setattr(main, "get_access_event_service", lambda: worker)
    lifespan = main.lifespan(FastAPI())
    await lifespan.__aenter__()
    await asyncio.wait_for(closing.wait(), 1)
    shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
    try:
        await asyncio.sleep(.02)
        shutdown.cancel()
        await asyncio.sleep(.02)
        assert not shutdown.done() and ("stop", "database") not in events
    finally:
        release.set()
    await shutdown
    assert events[-1] == ("stop", "database") and not worker._operations


@pytest.mark.asyncio
async def test_standalone_zone_owner_retains_actual_close_after_committed_suppression(monkeypatch):
    from unittest.mock import AsyncMock

    from app.modules.lpr.base import PlateRead
    from app.services import lpr_zone_shadow
    closing, release = asyncio.Event(), asyncio.Event()
    session = AsyncSession()
    original = session.close
    commit = AsyncMock()
    async def close():
        closing.set()
        await release.wait()
        await original()
    monkeypatch.setattr(session, "commit", commit)
    monkeypatch.setattr(session, "close", close)
    monkeypatch.setattr(lpr_zone_shadow, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(lpr_zone_shadow, "record_zone_shadow_in_session", AsyncMock(return_value=[]))
    call = asyncio.create_task(lpr_zone_shadow.LprZoneShadowService().record_decision(
        PlateRead("AB12CDE", .9, "synthetic", datetime.now(UTC), {}),
        access_event_id=None, actual_decision="denied", actual_direction="entry"))
    await closing.wait()
    call.cancel()
    await asyncio.sleep(0)
    call.cancel()
    await asyncio.sleep(.01)
    assert not call.done()
    commit.assert_awaited_once()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await call


@pytest.mark.asyncio
async def test_synchronously_delayed_preparation_cannot_renew_outer_operation_budget(monkeypatch):
    import time
    monkeypatch.setattr(completion_worker, "COMPLETION_TIMEOUT_SECONDS", .01)
    class DelayedStore(CompletionStore):
        async def assert_owned(self, claimed):
            time.sleep(.03)
            return asyncio.get_running_loop().time()+90
    store = DelayedStore()
    calls = []
    async def stage(claimed, deadline):
        calls.append(claimed)
    await owner(store, stage)._process(claim())
    assert not calls
