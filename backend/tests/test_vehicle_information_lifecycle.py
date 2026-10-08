"""Actual optional lookup children and shared-client ownership; no external I/O."""
import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from test_process_lifecycle import owned_resources as owned_resources

from app import main
from app.models import NotificationRun
from app.services import notifications
from app.services import vehicle_information as information
from app.services import vehicle_information_jobs as jobs
from app.services.access.completion_results import InformationClaim
from app.services.vehicle_information_contracts import ProviderResult

pytestmark = pytest.mark.asyncio


def service():
    cache = SimpleNamespace(aclose=AsyncMock())
    dvsa = SimpleNamespace(close=AsyncMock())
    return information.VehicleInformationService(cache=cache, dvsa=dvsa)


def config():
    return SimpleNamespace(site_timezone="Europe/London")


async def pause():
    await asyncio.sleep(.03)


async def test_unexpected_provider_failure_drains_actual_sibling_before_slot_and_close(monkeypatch):
    owner = service()
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = ValueError("synthetic unexpected decode")

    async def provider(kind, *_args, **_kwargs):
        if kind == "dvla":
            await entered.wait()
            raise original
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    monkeypatch.setattr(owner, "_provider", provider)
    call = asyncio.create_task(owner.lookup("AB12CDE", config=config()))
    await cleaning.wait()
    close = asyncio.create_task(owner.close())
    await pause()
    assert not call.done() and not close.done() and len(owner._calls) == 1
    owner.dvsa.close.assert_not_awaited()
    release.set()
    with pytest.raises(ValueError) as error:
        await call
    assert error.value is original
    await close
    owner.dvsa.close.assert_awaited_once()
    owner.cache.aclose.assert_awaited_once()
    assert not owner._calls


async def test_repeated_lookup_cancellation_retains_provider_cleanup_and_call_receipt(monkeypatch):
    owner = service()
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    active = 0
    settled = []

    async def provider(kind, *_args, **_kwargs):
        nonlocal active
        active += 1
        if active == 2:
            entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            settled.append(kind)
            active -= 1

    monkeypatch.setattr(owner, "_provider", provider)
    call = asyncio.create_task(owner.lookup("AB12CDE", config=config()))
    await entered.wait()
    call.cancel()
    await cleaning.wait()
    call.cancel()
    close = asyncio.create_task(owner.close())
    await pause()
    assert active == 2 and not call.done() and not close.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await call
    await close
    assert sorted(settled) == ["dvla", "dvsa"] and active == 0
    assert not owner._calls


async def test_successful_parallel_provider_results_keep_order_and_normal_statuses(monkeypatch):
    owner = service()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def provider(kind, *_args, **_kwargs):
        calls.append(kind)
        if len(calls) == 2:
            entered.set()
        await release.wait()
        return ProviderResult(status="failed" if kind == "dvla" else "deferred")

    monkeypatch.setattr(owner, "_provider", provider)
    call = asyncio.create_task(owner.lookup("AB12CDE", config=config()))
    await entered.wait()
    assert calls == ["dvla", "dvsa"] and not call.done()
    release.set()
    result = await call
    assert [(key, value.status) for key, value in result.results.items()] == [("dvla", "failed"), ("dvsa", "deferred")]
    await owner.close()


async def test_close_waits_call_receipt_but_not_parent_unrelated_lifetime(monkeypatch):
    owner = service()
    entered, release, unrelated = asyncio.Event(), asyncio.Event(), asyncio.Event()
    call_done = asyncio.Event()

    async def provider(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return ProviderResult(status="disabled")

    async def parent():
        await owner.lookup("AB12CDE", config=config())
        call_done.set()
        await unrelated.wait()

    monkeypatch.setattr(owner, "_provider", provider)
    task = asyncio.create_task(parent())
    await entered.wait()
    close = asyncio.create_task(owner.close())
    await pause()
    assert not close.done()
    release.set()
    await call_done.wait()
    await asyncio.wait_for(close, 1)
    assert not task.done()
    unrelated.set()
    await task


async def test_concurrent_close_and_repeated_cancellation_drain_whole_sequence():
    owner = service()
    entered, release = asyncio.Event(), asyncio.Event()

    async def close_dvsa():
        entered.set()
        await release.wait()

    owner.dvsa.close.side_effect = close_dvsa
    first = asyncio.create_task(owner.close())
    await entered.wait()
    cleanup = owner._cleanup
    first.cancel()
    await pause()
    first.cancel()
    second = asyncio.create_task(owner.close())
    await pause()
    assert not first.done() and not second.done() and owner._cleanup is cleanup
    owner.cache.aclose.assert_not_awaited()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    await owner.close()
    owner.dvsa.close.assert_awaited_once()
    owner.cache.aclose.assert_awaited_once()
    assert owner._closed


@pytest.mark.parametrize("first_error", [RuntimeError("synthetic close"), asyncio.CancelledError()])
async def test_first_client_close_failure_still_attempts_second(first_error):
    owner = service()
    owner.dvsa.close.side_effect = first_error
    owner.cache.aclose.side_effect = ValueError("second close failure")
    with pytest.raises(type(first_error)) as caught:
        await owner.close()
    assert caught.value is first_error
    owner.dvsa.close.assert_awaited_once()
    owner.cache.aclose.assert_awaited_once()
    assert owner._closed


async def test_resource_getter_cannot_recreate_after_stop_and_explicit_restart_is_safe(monkeypatch):
    information.get_vehicle_information_service.cache_clear()
    await information.start()
    created = []

    def construct():
        owner = service_type(cache=SimpleNamespace(aclose=AsyncMock()), dvsa=SimpleNamespace(close=AsyncMock()))
        created.append(owner)
        return owner

    service_type = information.VehicleInformationService
    monkeypatch.setattr(information, "VehicleInformationService", construct)
    try:
        owner = information.get_vehicle_information_service()
        await information.stop()
        assert information.get_vehicle_information_service() is owner
        with pytest.raises(information.VehicleInformationClosed):
            await owner.lookup("AB12CDE", config=config())
        information.get_vehicle_information_service.cache_clear()
        with pytest.raises(information.VehicleInformationClosed):
            information.get_vehicle_information_service()
        assert len(created) == 1
        await information.start()
        replacement = information.get_vehicle_information_service()
        assert replacement is not owner and len(created) == 2
        await information.stop()
    finally:
        information.get_vehicle_information_service.cache_clear()
        await information.start()


def worker_fixture(monkeypatch, owner, count=4):
    worker = jobs.VehicleInformationWorker()
    pending = [(uuid.uuid4(), uuid.uuid4(), "AB12CDE") for _ in range(count)]
    capacities = []

    async def claim(capacity=2):
        capacities.append(capacity)
        selected = pending[:capacity]
        del pending[:capacity]
        return selected

    async def prepare(event_id, token, plate):
        now = datetime.now(UTC)
        return InformationClaim(event_id, token, plate, None, now, now + timedelta(seconds=300)), 90

    async def execute(claim, _budget, _config):
        await owner.lookup(claim.registration_number, config=config())

    monkeypatch.setattr(worker, "_claim", claim)
    monkeypatch.setattr(worker, "_prepare", prepare)
    monkeypatch.setattr(worker, "_execute", execute)
    monkeypatch.setattr(worker, "_fail", AsyncMock())
    monkeypatch.setattr(jobs, "get_runtime_config", AsyncMock(return_value=config()))
    return worker, pending, capacities


async def test_two_actual_worker_slots_remain_occupied_through_timed_out_cleanup(monkeypatch):
    owner = service()
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    active = 0
    cleanups = 0

    async def provider(*_args, **_kwargs):
        nonlocal active, cleanups
        active += 1
        if active == 4:
            entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanups += 1
            if cleanups == 4:
                cleaning.set()
            await release.wait()
            active -= 1

    monkeypatch.setattr(owner, "_provider", provider)
    monkeypatch.setattr(jobs, "INFORMATION_TIMEOUT_SECONDS", .02)
    worker, pending, capacities = worker_fixture(monkeypatch, owner)
    await worker.start()
    await entered.wait()
    await cleaning.wait()
    await asyncio.sleep(.55)
    assert active == 4 and len(worker._operations) == 2 and len(pending) == 2
    assert max(capacities) == 2 and all(0 <= value <= 2 for value in capacities)
    stop = asyncio.create_task(worker.stop())
    await pause()
    stop.cancel()
    await pause()
    assert not stop.done() and active == 4
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await stop
    await worker.stop()
    assert active == 0 and not worker._operations and worker._task is None
    assert not owner._closed  # Worker never closes shared clients.
    await owner.close()


async def test_main_lifespan_waits_actual_worker_cleanup_past_outer_timeout(owned_resources, monkeypatch):
    events, _ = owned_resources
    owner = service()
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def provider(*_args, **_kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    monkeypatch.setattr(owner, "_provider", provider)
    worker, _, _ = worker_fixture(monkeypatch, owner, 1)
    monkeypatch.setattr(main, "get_vehicle_information_worker", lambda: worker)
    monkeypatch.setattr(main, "vehicle_information_resources", SimpleNamespace(start=AsyncMock(), stop=owner.close))
    real_wait_for = asyncio.wait_for

    async def short_stop(awaitable, *, timeout):
        return await real_wait_for(awaitable, timeout=.02)

    monkeypatch.setattr(main.asyncio, "wait_for", short_stop)
    lifespan = main.lifespan(FastAPI())
    await lifespan.__aenter__()
    await entered.wait()
    shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
    await cleaning.wait()
    await asyncio.sleep(.05)
    assert not shutdown.done() and ("stop", "database") not in events
    owner.dvsa.close.assert_not_awaited()
    release.set()
    await shutdown
    assert owner._closed and not worker._operations and events[-1] == ("stop", "database")


async def test_notification_shutdown_lookup_keeps_shared_client_after_worker_stop(owned_resources, monkeypatch):
    events, _ = owned_resources
    owner = service()
    entered, release = asyncio.Event(), asyncio.Event()
    notification = notifications.NotificationService()
    row = NotificationRun(context={"event_type":"unauthorized_plate", "facts":{
        "access_event_id":str(uuid.uuid4()), "registration_number":"AB12CDE"}})

    async def provider(*_args, **_kwargs):
        assert not owner._closing
        entered.set()
        await release.wait()
        return ProviderResult(status="disabled")

    async def stop_notifications():
        assert ("stop", "vehicle_information") in events
        await notification._enrich_unknown_vehicle_notification(row)
        events.append(("stop", "notification"))

    monkeypatch.setattr(owner, "_provider", provider)
    monkeypatch.setattr(information, "get_runtime_config", AsyncMock(return_value=config()))
    monkeypatch.setattr(notifications, "get_vehicle_information_service", lambda: owner)
    monkeypatch.setattr(main, "get_notification_service", lambda: SimpleNamespace(start=AsyncMock(), stop=stop_notifications))
    monkeypatch.setattr(main, "vehicle_information_resources", SimpleNamespace(start=AsyncMock(), stop=owner.close))
    lifespan = main.lifespan(FastAPI())
    await lifespan.__aenter__()
    shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
    await entered.wait()
    assert not owner._closing and not shutdown.done() and ("stop", "database") not in events
    release.set()
    await shutdown
    assert owner._closed and events[-1] == ("stop", "database")


async def test_main_shared_resource_waits_asgi_call_not_unrelated_request_parent(owned_resources, monkeypatch):
    events, _ = owned_resources
    owner = service()
    entered, release, unrelated = asyncio.Event(), asyncio.Event(), asyncio.Event()
    returned = asyncio.Event()

    async def provider(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return ProviderResult(status="disabled")

    monkeypatch.setattr(owner, "_provider", provider)
    monkeypatch.setattr(main, "vehicle_information_resources", SimpleNamespace(start=AsyncMock(), stop=owner.close))
    app = FastAPI()

    @app.get("/synthetic-shared-lookup")
    async def lookup():
        await owner.lookup("AB12CDE", config=config())
        return {"ok": True}

    async def request(client):
        assert (await client.get("/synthetic-shared-lookup")).json() == {"ok":True}
        returned.set()
        await unrelated.wait()

    lifespan = main.lifespan(app)
    await lifespan.__aenter__()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid") as client:
        parent = asyncio.create_task(request(client))
        await entered.wait()
        shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
        await pause()
        assert not shutdown.done() and not owner._closed
        release.set()
        await returned.wait()
        await asyncio.wait_for(shutdown, 1)
        assert not parent.done() and owner._closed and events[-1] == ("stop", "database")
        unrelated.set()
        await parent


async def test_connection_cancellation_keeps_actual_dvsa_cleanup_and_close_receipt(monkeypatch):
    from test_vehicle_information import config as full_config
    owner = service()
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    settled = []

    async def lookup(*_args, **_kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            settled.append("dvsa-test")

    monkeypatch.setattr(information, "get_runtime_config", AsyncMock(return_value=full_config(dvsa_test_registration_number="AB12CDE")))
    owner.dvsa.lookup = lookup
    call = asyncio.create_task(owner.test_connection({}))
    await entered.wait()
    call.cancel()
    await cleaning.wait()
    call.cancel()
    close = asyncio.create_task(owner.close())
    await pause()
    assert not call.done() and not close.done() and len(owner._calls) == 1
    owner.dvsa.close.assert_not_awaited()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await call
    await close
    assert settled == ["dvsa-test"] and not owner._calls
    owner.dvsa.close.assert_awaited_once()
    owner.cache.aclose.assert_awaited_once()


@pytest.mark.parametrize("cause", ["timeout", "lease_lost"])
async def test_result_cleanup_has_one_cancel_and_retains_original_failure_class(monkeypatch, cause):
    owner = service()
    worker, pending, _ = worker_fixture(monkeypatch, owner, 1)
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancellation_count = []

    async def execute(*_args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            cancellation_count.append(asyncio.current_task().cancelling())

    async def renewal(_claim):
        if cause == "lease_lost":
            raise jobs.PipelineLeaseLost("synthetic lost renewal")

    monkeypatch.setattr(worker, "_execute", execute)
    monkeypatch.setattr(worker, "_renew_claim", renewal)
    monkeypatch.setattr(jobs, "INFORMATION_TIMEOUT_SECONDS", .03)
    monkeypatch.setattr(jobs, "INFORMATION_RENEWAL_SECONDS", .005)
    call = asyncio.create_task(worker._process(*pending.pop()))
    await entered.wait()
    await cleaning.wait()
    await asyncio.sleep(.06)  # Also cross the outer timeout during lost-lease cleanup.
    assert not call.done()
    worker._fail.assert_not_awaited()
    release.set()
    await call
    assert cancellation_count == [1]
    error = worker._fail.await_args.args[3]
    assert isinstance(error, TimeoutError if cause == "timeout" else jobs.PipelineLeaseLost)
    await owner.close()


async def test_repeated_worker_stop_keeps_result_transaction_cleanup_in_slot(monkeypatch):
    owner = service()
    worker, _, _ = worker_fixture(monkeypatch, owner, 1)
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    finished = []

    async def execute(*_args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            finished.append(asyncio.current_task().cancelling())

    monkeypatch.setattr(worker, "_execute", execute)
    await worker.start()
    await entered.wait()
    first = asyncio.create_task(worker.stop())
    await cleaning.wait()
    first.cancel()
    await pause()
    first.cancel()
    second = asyncio.create_task(worker.stop())
    await pause()
    assert len(worker._operations) == 1 and not first.done() and not second.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    assert finished == [1] and not worker._operations
    worker._fail.assert_not_awaited()
    await owner.close()


def blocked_real_session(monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    session = AsyncSession()
    entered, release, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    close = session.close

    async def blocked_close():
        entered.set()
        await release.wait()
        await close()
        settled.set()

    monkeypatch.setattr(session, "close", blocked_close)
    return session, entered, release, settled


@pytest.mark.parametrize("entry", ["lookup", "connection"])
@pytest.mark.parametrize("phase", ["normal_exit", "cancelled_query"])
async def test_real_settings_session_cleanup_keeps_admitted_receipt_and_shared_clients(monkeypatch, entry, phase):
    from app.services import settings as setting_owner
    from test_vehicle_information import config as full_config
    owner = service()
    session, closing, release, settled = blocked_real_session(monkeypatch)
    querying = asyncio.Event()

    async def current(_session):
        assert _session is session
        querying.set()
        if phase == "cancelled_query":
            await asyncio.Event().wait()
        return full_config(dvsa_test_registration_number="AB12CDE")

    monkeypatch.setattr(setting_owner, "_RUNTIME_CONFIG_CACHE", None)
    monkeypatch.setattr(setting_owner, "_RUNTIME_CONFIG_CACHE_LOADED_AT", 0)
    monkeypatch.setattr(setting_owner, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(setting_owner, "get_runtime_config_for_session", current)
    monkeypatch.setattr(information, "get_runtime_config", setting_owner.get_runtime_config)
    provider = AsyncMock(side_effect=AssertionError("no provider before prepared settings"))
    monkeypatch.setattr(owner, "_provider", provider)
    owner.dvsa.lookup = provider
    call = asyncio.create_task(owner.lookup("AB12CDE") if entry == "lookup" else owner.test_connection({}))
    await asyncio.wait_for(querying.wait(), 1)
    if phase == "cancelled_query":
        call.cancel()
    await asyncio.wait_for(closing.wait(), 1)
    call.cancel()
    call.cancel()
    close = asyncio.create_task(owner.close())
    try:
        await pause()
        assert not call.done() and not close.done() and not settled.is_set()
        assert len(owner._calls) == 1 and setting_owner._RUNTIME_CONFIG_CACHE is None
        provider.assert_not_awaited()
        owner.dvsa.close.assert_not_awaited()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await call
    await close
    assert settled.is_set() and not owner._calls
    owner.dvsa.close.assert_awaited_once()
    owner.cache.aclose.assert_awaited_once()


@pytest.mark.parametrize("phase", ["normal_exit", "cancelled_query"])
async def test_real_worker_session_close_is_owned_on_first_and_repeated_cancellation(monkeypatch, phase):
    session, closing, release, settled = blocked_real_session(monkeypatch)
    querying = asyncio.Event()
    monkeypatch.setattr(jobs, "AsyncSessionLocal", lambda: session)

    async def operation():
        async with jobs._worker_session() as current:
            assert current is session
            querying.set()
            if phase == "cancelled_query":
                await asyncio.Event().wait()

    call = asyncio.create_task(operation())
    await asyncio.wait_for(querying.wait(), 1)
    if phase == "cancelled_query":
        call.cancel()
    await asyncio.wait_for(closing.wait(), 1)
    call.cancel()
    call.cancel()
    try:
        await pause()
        assert not call.done() and not settled.is_set()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert settled.is_set()


@pytest.mark.parametrize("phase", ["claim", "prepare"])
async def test_main_lifespan_waits_real_database_close_before_disposal(owned_resources, monkeypatch, phase):
    events, _ = owned_resources
    owner = service()
    worker, _, _ = worker_fixture(monkeypatch, owner, 1)
    session, closing, release, settled = blocked_real_session(monkeypatch)
    monkeypatch.setattr(jobs, "AsyncSessionLocal", lambda: session)
    forbidden = AsyncMock(side_effect=AssertionError("cancelled preparation must not start a provider"))
    monkeypatch.setattr(worker, "_execute", forbidden)
    if phase == "claim":
        monkeypatch.setattr(session, "scalars", AsyncMock(return_value=[]))
        monkeypatch.setattr(jobs, "database_now", AsyncMock(return_value=datetime.now(UTC)))
        monkeypatch.setattr(worker, "_claim", jobs.VehicleInformationWorker._claim.__get__(worker))
    else:
        async def prepare(event_id, token, plate):
            async with jobs._worker_session():
                now = datetime.now(UTC)
                return InformationClaim(event_id, token, plate, None, now, now+timedelta(seconds=300)), 90
        monkeypatch.setattr(worker, "_prepare", prepare)
    monkeypatch.setattr(main, "get_vehicle_information_worker", lambda: worker)
    monkeypatch.setattr(main, "vehicle_information_resources", SimpleNamespace(start=AsyncMock(), stop=owner.close))
    lifespan = main.lifespan(FastAPI())
    await lifespan.__aenter__()
    await asyncio.wait_for(closing.wait(), 1)
    shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
    try:
        await pause()
        assert not shutdown.done() and not settled.is_set() and ("stop", "database") not in events
        forbidden.assert_not_awaited()
        owner.dvsa.close.assert_not_awaited()
    finally:
        release.set()
    await shutdown
    assert settled.is_set() and owner._closed and not worker._operations
    assert events[-1] == ("stop", "database")
