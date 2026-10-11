"""Admission and actual-thread ownership, independent of the real SDK fixtures."""
import asyncio
import threading
from datetime import UTC, datetime, timedelta

import pytest

from app.services.icloud_calendar_sdk import CalendarSDKBusy, CalendarSDKOwner, PendingCalendarAuth


class InertClients:
    def __init__(self):
        self.detached = []
        self.closed = []
        self.cleanup_started = threading.Event()
        self.cleanup_release = None

    def detach(self, session):
        self.detached.append(session)

    def cleanup_auth_session(self, session):
        self.cleanup_started.set()
        if self.cleanup_release is not None:
            assert self.cleanup_release.wait(5)
        self.closed.append(session)


@pytest.mark.asyncio
async def test_reserve_before_constructor_and_verify_remains_available_at_eight():
    clients = InertClients()
    now = datetime(2026, 10, 8, tzinfo=UTC)
    owner = CalendarSDKOwner(clients, clock=lambda: now)
    constructed = []

    async def start(native):
        owner.reserve_pending_capacity()
        session = object()
        constructed.append(session)
        pending = PendingCalendarAuth(str(len(constructed)), session, now + timedelta(minutes=10),
                                      None, None, "actor")
        await owner.retain(pending)
        return pending.handshake_id

    for _ in range(8):
        await owner.run(start)
    with pytest.raises(CalendarSDKBusy, match="Eight"):
        await owner.run(start)
    assert len(constructed) == 8 and len(clients.detached) == 8

    async def verify(native):
        assert owner.pending("1") is not None
        await owner.drop("1")
        return "verified"

    assert await owner.run(verify) == "verified"
    await owner.stop()
    assert len(clients.closed) == 8


@pytest.mark.asyncio
async def test_cancelled_actual_thread_keeps_slot_until_its_cleanup_finishes():
    clients = InertClients()
    owner = CalendarSDKOwner(clients)
    started, release = threading.Event(), threading.Event()
    cleanup = []
    watchers = []

    def worker():
        started.set()
        assert release.wait(5)

    async def action(native):
        try:
            await asyncio.to_thread(worker)
            native.work.check()
        finally:
            cleanup.append("actual thread returned")

    async def disconnect():
        watchers.append("created")
        await asyncio.Future()

    caller = asyncio.create_task(owner.run(action, disconnect=disconnect))
    assert await asyncio.to_thread(started.wait, 1)
    caller.cancel()
    await asyncio.sleep(0)
    with pytest.raises(CalendarSDKBusy):
        await owner.run(action, disconnect=disconnect)
    assert watchers == ["created"] and not cleanup and not caller.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert cleanup == ["actual thread returned"] and owner._active is None
    await owner.stop()


@pytest.mark.asyncio
async def test_expired_clients_are_unusable_and_cleanup_retains_capacity():
    clients = InertClients()
    now = datetime(2026, 10, 8, tzinfo=UTC)
    owner = CalendarSDKOwner(clients, clock=lambda: now)

    async def start(native):
        owner.reserve_pending_capacity()
        await owner.retain(PendingCalendarAuth("one", object(), now + timedelta(minutes=10),
                                             None, None, "actor"))

    await owner.run(start)
    now += timedelta(minutes=10)
    assert owner.pending("one") is None
    clients.cleanup_release = threading.Event()
    reached = []

    async def next_operation(native):
        reached.append("admitted after cleanup")

    caller = asyncio.create_task(owner.run(next_operation))
    assert await asyncio.to_thread(clients.cleanup_started.wait, 1)
    with pytest.raises(CalendarSDKBusy):
        await owner.run(next_operation)
    assert reached == []
    clients.cleanup_release.set()
    await caller
    assert reached == ["admitted after cleanup"] and len(clients.closed) == 1
    await owner.stop()


@pytest.mark.asyncio
async def test_disconnect_port_stops_owned_work_and_known_receipt_survives_late_stop():
    owner = CalendarSDKOwner(InertClients())
    disconnected, accepted = asyncio.Event(), asyncio.Event()
    release = asyncio.Event()

    async def receive():
        await disconnected.wait()

    async def action(native):
        accepted.set()
        await release.wait()
        # Models an already committed account checkpoint; no late budget check.
        return {"accepted": True}

    caller = asyncio.create_task(owner.run(action, disconnect=receive))
    await accepted.wait()
    disconnected.set()
    await asyncio.sleep(0)
    assert not caller.done() and owner._active is not None
    release.set()
    assert await caller == {"accepted": True}
    await owner.stop()


@pytest.mark.asyncio
async def test_repeated_caller_cancel_cannot_discard_accepted_receipt_after_cleanup():
    owner = CalendarSDKOwner(InertClients())
    accepted = asyncio.Event()
    cleanup_started, cleanup_release = threading.Event(), threading.Event()
    receipt = {"accepted_accounts": ["first"], "remaining_accounts": ["second"]}

    async def action(native):
        accepted.set()
        def actual_cleanup():
            cleanup_started.set()
            assert cleanup_release.wait(5)
        await asyncio.to_thread(actual_cleanup)
        return receipt

    caller = asyncio.create_task(owner.run(action))
    await accepted.wait()
    assert await asyncio.to_thread(cleanup_started.wait, 1)
    for _ in range(3):
        caller.cancel()
        await asyncio.sleep(0)
        assert not caller.done() and owner._active is not None
    cleanup_release.set()
    assert await caller == receipt
    assert owner._active is None
    await owner.stop()


@pytest.mark.asyncio
async def test_process_admission_is_inert_and_restarts_only_after_actual_cleanup():
    client = InertClients()
    owner = CalendarSDKOwner(client)
    await owner.start()
    assert not owner._closing and owner._active is None and owner._pending == {}
    await owner.stop()
    await owner.start()
    assert not owner._closing and owner._stopping is None
    async def no_effect(native):
        native.work.check()
        return "inert"
    assert await owner.run(no_effect) == "inert"
    await owner.stop()


@pytest.mark.asyncio
async def test_optional_observation_uses_original_remaining_deadline_and_drains_late_cleanup():
    from time import monotonic
    from app.modules.icloud_calendar.contracts import CalendarWork
    from app.services.icloud_calendar import ICloudCalendarService
    service = ICloudCalendarService(InertClients())
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    publications, cancels = [], []
    original_deadline = monotonic() + .08
    async def action(native):
        await asyncio.sleep(.04)
        native.work.check()
        return {"status": "accepted"}
    async def observe():
        entered.set()
        try:
            await asyncio.Future()
            publications.append("must not start after original expiry")
        finally:
            cancels.append(asyncio.current_task().cancelling())
            cleanup.set()
            await release.wait()
    async def after(receipt, native):
        return await service._observe_accepted(receipt, observe, work=native.work)
    request = asyncio.create_task(service.sdk.run(action, work=CalendarWork(original_deadline), after_result=after))
    await entered.wait()
    await asyncio.wait_for(cleanup.wait(), .3)
    assert monotonic() >= original_deadline and not request.done()
    assert cancels == [1] and publications == [] and service.sdk._active is not None
    stopped = asyncio.create_task(service.stop())
    await asyncio.sleep(.01)
    assert not stopped.done()
    release.set()
    assert (await request)["status"] == "accepted"
    await stopped
    assert service.sdk._active is None and publications == []
