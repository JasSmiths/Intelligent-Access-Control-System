"""Inert publication-lane transport/cancellation ownership; PG covers the real store."""
import asyncio
import time
import uuid

import pytest

from app.services.access import publication_worker
from app.services.access.pipeline_contracts import PipelineLeaseLost
from app.services.access.publication import AccessPublicationStore,PublicationClaim,PublicationMessage
from app.services.access.publication_worker import AccessPublicationLane
from app.services.realtime_contracts import RealtimePublishReceipt


class Store(AccessPublicationStore):
    def __init__(self,count=1):
        self.claims=[PublicationClaim(uuid.uuid4(),uuid.uuid4(),uuid.uuid4()) for _ in range(count)]
        self.acked=[];self.retried=[];self.claim_count=0;self.renew_lost=False
    async def claim(self):
        self.claim_count+=1
        return self.claims.pop(0) if self.claims else None
    async def load_message(self,claim):
        return PublicationMessage(1,'access_event.finalized',{'event_id':str(claim.event_id),'resource_revision':1})
    async def assert_owned(self,claim):return asyncio.get_running_loop().time()+120
    async def acknowledge(self,claim,revision):self.acked.append((claim,revision));return False
    async def retry(self,claim):self.retried.append(claim)
    async def renew(self,claim):
        if self.renew_lost:raise PipelineLeaseLost('lost')


class Port:
    def __init__(self,accepted=True):self.accepted=accepted;self.calls=[]
    async def publish(self,event_type,payload):
        self.calls.append((event_type,payload))
        return RealtimePublishReceipt('in_process',self.accepted)


@pytest.mark.asyncio
async def test_delivery_checks_explicit_receipt_before_acknowledging():
    for accepted in (False,True):
        store=Store();port=Port(accepted);lane=AccessPublicationLane(store,port)
        claim=await store.claim();await lane.deliver(claim)
        assert bool(store.acked) is accepted
        assert bool(store.retried) is not accepted
        assert port.calls==[('access_event.finalized',{'event_id':str(claim.event_id),'resource_revision':1})]


@pytest.mark.asyncio
async def test_transport_timeout_settles_actual_child_then_retains_work_for_retry(monkeypatch):
    monkeypatch.setattr(publication_worker,'PUBLICATION_TIMEOUT_SECONDS',.02)
    cancelled=asyncio.Event()
    class BlockedPort:
        async def publish(self,event_type,payload):
            try:await asyncio.Event().wait()
            finally:cancelled.set()
    store=Store();lane=AccessPublicationLane(store,BlockedPort());claim=await store.claim()
    await lane.deliver(claim)
    assert cancelled.is_set() and store.retried==[claim] and not store.acked


@pytest.mark.asyncio
async def test_renewal_loss_cancels_and_awaits_transport_without_acknowledgement(monkeypatch):
    monkeypatch.setattr(publication_worker,'PUBLICATION_RENEWAL_SECONDS',.01)
    cancelled=asyncio.Event()
    class BlockedPort:
        async def publish(self,event_type,payload):
            try:await asyncio.Event().wait()
            finally:cancelled.set()
    store=Store();store.renew_lost=True;lane=AccessPublicationLane(store,BlockedPort())
    with pytest.raises(PipelineLeaseLost):await lane.deliver(await store.claim())
    assert cancelled.is_set() and not store.acked and not store.retried


@pytest.mark.asyncio
async def test_cancelled_stop_drains_actual_transport_and_renewal_before_returning():
    started,cancelled,cleanup=asyncio.Event(),asyncio.Event(),asyncio.Event()
    class CleanupPort:
        async def publish(self,event_type,payload):
            started.set()
            try:await asyncio.Event().wait()
            finally:
                cancelled.set();await cleanup.wait()
    store=Store();lane=AccessPublicationLane(store,CleanupPort());await lane.start();await started.wait()
    stop=asyncio.create_task(lane.stop());await cancelled.wait();stop.cancel()
    await asyncio.sleep(.02);assert not stop.done()
    await lane.start();assert store.claim_count==1
    cleanup.set()
    with pytest.raises(asyncio.CancelledError):await stop
    assert lane._task is None and not store.acked and not store.retried
    assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and task.get_name().startswith('access-publication')]


@pytest.mark.asyncio
async def test_lane_does_not_lease_another_identifier_behind_its_blocked_slot():
    started,release=asyncio.Event(),asyncio.Event()
    class BlockedPort(Port):
        async def publish(self,event_type,payload):
            started.set();await release.wait();return await super().publish(event_type,payload)
    store=Store(2);lane=AccessPublicationLane(store,BlockedPort());await lane.start();await started.wait()
    assert store.claim_count==1
    release.set()
    for _ in range(50):
        if len(store.acked)==2:break
        await asyncio.sleep(.01)
    await lane.stop();assert len(store.acked)==2


@pytest.mark.asyncio
async def test_renewal_loss_before_send_deadline_retains_actual_transport_cleanup_and_lane(monkeypatch):
    monkeypatch.setattr(publication_worker, "PUBLICATION_RENEWAL_SECONDS", 0.01)
    monkeypatch.setattr(publication_worker, "PUBLICATION_TIMEOUT_SECONDS", 0.04)
    entered, cleaning, release, finished = (asyncio.Event() for _ in range(4))
    cancellations = 0

    class PreparedStore(Store):
        async def load_message(self, claim):
            # Preparation exceeds the transport timeout. Renewal first fails
            # only after publish starts, before its own original deadline.
            await asyncio.sleep(0.07)
            return await super().load_message(claim)

        async def renew(self, claim):
            if entered.is_set():
                raise PipelineLeaseLost("fixture_lost_during_transport")

    class CleanupPort:
        async def publish(self, event_type, payload):
            nonlocal cancellations
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellations += 1
                cleaning.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancellations += 1
                    raise
                raise
            finally:
                finished.set()

    store = PreparedStore(2)
    lane = AccessPublicationLane(store, CleanupPort())
    await lane.start()
    try:
        await asyncio.wait_for(cleaning.wait(), 1)
        # Cross the send deadline while its first cancellation cleanup remains
        # blocked. The actual single lane must not lease another identifier.
        await asyncio.sleep(0.07)
        assert cancellations == 1
        assert not finished.is_set() and store.claim_count == 1
        assert not store.acked and not store.retried
        stop = asyncio.create_task(lane.stop())
        await asyncio.sleep(0)
        stop.cancel()
        await asyncio.sleep(0.01)
        stop.cancel()
        assert not stop.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await stop
        assert cancellations == 1 and finished.is_set()
        assert store.claim_count == 1 and not store.acked and not store.retried
    finally:
        release.set()
        await lane.stop()
    assert not [task for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
                and task.get_name().startswith("access-publication")]


@pytest.mark.asyncio
async def test_delayed_actual_transport_child_cannot_start_after_original_deadline(monkeypatch):
    create_task = asyncio.create_task

    def delayed_task(coroutine, *, name=None):
        async def delayed():
            # Simulate a blocked loop after dispatch but before actual invoke;
            # the child runs before the already-due timeout callback.
            time.sleep(0.03)
            return await coroutine
        return create_task(delayed(), name=name)

    monkeypatch.setattr(publication_worker.asyncio, "create_task", delayed_task)
    port = Port()
    lane = AccessPublicationLane(Store(), port)
    message = PublicationMessage(1, "access_event.finalized", {"resource_revision": 1})
    with pytest.raises(TimeoutError):
        await lane._publish(message, asyncio.get_running_loop().time() + 0.01)
    assert not port.calls
