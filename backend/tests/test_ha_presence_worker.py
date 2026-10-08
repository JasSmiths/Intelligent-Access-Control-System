"""Dormant HA owner lifetimes; every provider and database port is inert."""
import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.task_lifecycle import drain_owned
from app.modules.home_assistant.client import HomeAssistantClient, HomeAssistantState
from app.modules.home_assistant.input_boolean_contracts import FrozenHaConnection
from app.services.ha_presence_attempts import HaPresenceAttemptStore
from app.services.ha_presence_contracts import HaAttemptClaim, PreparedHaAttempt
from app.services.ha_presence_database import ha_session
from app.services.ha_presence_worker import HaPresenceWorker


def prepared(seconds=10):
    now = datetime.now(UTC)
    return PreparedHaAttempt(
        HaAttemptClaim(uuid.uuid4(), 'fixture-target', 1, 1, uuid.uuid4(), 1),
        FrozenHaConnection('http://synthetic.invalid', 'fixture-secret'),
        'input_boolean.fixture', 'turn_off', now, now+timedelta(minutes=5),
        asyncio.get_running_loop().time()+seconds,
    )


class Store(HaPresenceAttemptStore):
    def __init__(self, claim):
        self.claim = claim
        self.prepares = 0
        self.renewals = 0
        self.receipts = []
        self.releases = 0
        self.progressed = asyncio.Event()
        self.renew = None
        self.receipt = None
        self.prepare_close = None

    async def recover_expired_attempts(self):
        return 0

    async def prepare_attempt(self, operation_deadline, *, on_committed=None):
        self.prepares += 1
        if self.prepares > 1:
            self.progressed.set()
            return None
        if on_committed is not None:
            on_committed(self.claim)
        if self.prepare_close is not None:
            await self.prepare_close()
        return self.claim

    async def renew_attempt(self, claim):
        assert claim == self.claim.claim
        self.renewals += 1
        return True if self.renew is None else await self.renew()

    async def record_receipt(self, claim, delivery):
        assert claim == self.claim.claim
        self.receipts.append(delivery)
        if self.receipt is not None:
            await self.receipt()

    async def release_capacity(self, claim):
        assert claim == self.claim.claim
        self.releases += 1
        return True


class Client(HomeAssistantClient):
    def __init__(self):
        self.sent = 0
        self.closed = False
        self.observation = None
        self.cleanup = None

    async def call_service(self, service_name, service_data, *, runtime_config=None, start_deadline=None):
        self.sent += 1
        return {}

    async def get_state(self, entity_id, *, runtime_config=None, start_deadline=None):
        if self.observation is not None:
            await self.observation()
        return HomeAssistantState(entity_id, 'off', {})

    async def close(self):
        if self.cleanup is not None:
            await self.cleanup()
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['active_sql', 'normal_actual_close'])
async def test_receipt_renewal_loss_drains_once_and_long_lived_worker_continues(monkeypatch, phase):
    """Losing a child receipt lease must not look like cancellation of the service."""
    monkeypatch.setattr('app.services.ha_presence_worker.HA_RENEWAL_SECONDS', .001)
    store, client = Store(prepared()), Client()
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancellations = 0
    if phase == 'active_sql':
        async def receipt():
            nonlocal cancellations
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellations += 1
                cleaning.set()
                await release.wait()
                raise
    else:
        session = AsyncSession()
        actual_close = session.close
        async def close():
            cleaning.set()
            await release.wait()
            await actual_close()
        monkeypatch.setattr(session, 'close', close)
        async def receipt():
            nonlocal cancellations
            try:
                async with ha_session(lambda: session):
                    entered.set()  # Terminal receipt already persisted in this inert port.
            except asyncio.CancelledError:
                cancellations += 1
                raise
    async def renew():
        await entered.wait()
        return False
    store.receipt, store.renew = receipt, renew
    worker = HaPresenceWorker(store, client_factory=lambda: client)
    await worker.start()
    try:
        await asyncio.wait_for(cleaning.wait(), 1)
        await asyncio.sleep(.01)
        assert worker._task is not None and not worker._task.done()
        assert store.prepares == 1 and store.releases == 0 and not client.closed
        if phase == 'active_sql':
            assert cancellations == 1
        release.set()
        await asyncio.wait_for(store.progressed.wait(), 1)
        assert cancellations == 1 and len(store.receipts) == 1
        assert client.closed and store.releases == 1
        assert worker._task is not None and not worker._task.done()
    finally:
        release.set()
        await worker.stop()


@pytest.mark.asyncio
async def test_confirmed_claim_close_renews_capacity_but_never_renews_send_deadline(monkeypatch):
    monkeypatch.setattr('app.services.ha_presence_worker.HA_RENEWAL_SECONDS', .001)
    store, client = Store(prepared(.01)), Client()
    closing, release = asyncio.Event(), asyncio.Event()
    async def close():
        closing.set()
        await release.wait()
    async def prepare_close():
        await drain_owned(close())
    store.prepare_close = prepare_close
    worker = HaPresenceWorker(store, client_factory=lambda: client)
    actual = asyncio.create_task(worker._process_owned())
    await closing.wait()
    await asyncio.sleep(.025)
    assert not actual.done() and store.renewals > 0
    assert client.sent == 0 and store.releases == 0
    release.set()
    assert await actual
    assert client.sent == 0 and store.releases == 1
    assert len(store.receipts) == 1 and store.receipts[0].kind == 'not_sent'


@pytest.mark.asyncio
async def test_timeout_then_repeated_stop_retains_actual_observation_cleanup_and_accepted_truth(monkeypatch):
    monkeypatch.setattr('app.services.ha_presence_worker.HA_RENEWAL_SECONDS', .001)
    store, client = Store(prepared(.01)), Client()
    observing, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancellations = 0
    async def observation():
        nonlocal cancellations
        observing.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellations += 1
            cleaning.set()
            await release.wait()
            raise
    client.observation = observation
    worker = HaPresenceWorker(store, client_factory=lambda: client)
    await worker.start()
    await observing.wait()
    await asyncio.wait_for(cleaning.wait(), 1)
    stops = [asyncio.create_task(worker.stop()) for _ in range(2)]
    await asyncio.sleep(0)
    stops[0].cancel()
    await asyncio.sleep(0)
    stops[0].cancel()
    await asyncio.sleep(.01)
    assert cancellations == 1 and not client.closed and store.releases == 0
    assert all(not task.done() for task in stops)
    release.set()
    await asyncio.gather(*stops, return_exceptions=True)
    assert cancellations == 1 and client.closed and store.releases == 1
    assert len(store.receipts) == 1 and store.receipts[0].kind == 'accepted'
    assert store.receipts[0].reason == 'accepted_observation_unavailable'


@pytest.mark.asyncio
async def test_actual_client_close_holds_capacity_through_repeated_stop(monkeypatch):
    monkeypatch.setattr('app.services.ha_presence_worker.HA_RENEWAL_SECONDS', .001)
    store, client = Store(prepared()), Client()
    closing, release = asyncio.Event(), asyncio.Event()
    async def close():
        closing.set()
        await release.wait()
    client.cleanup = close
    worker = HaPresenceWorker(store, client_factory=lambda: client)
    await worker.start()
    await closing.wait()
    stop = asyncio.create_task(worker.stop())
    await asyncio.sleep(0)
    stop.cancel()
    await asyncio.sleep(0)
    stop.cancel()
    await asyncio.sleep(.01)
    assert not stop.done() and not client.closed and store.releases == 0
    assert len(store.receipts) == 1 and store.receipts[0].kind == 'accepted'
    release.set()
    await asyncio.gather(stop, return_exceptions=True)
    assert client.closed and store.releases == 1 and len(store.receipts) == 1


@pytest.mark.asyncio
async def test_unknown_prepare_commit_never_authorizes_send_or_release(monkeypatch):
    store, client = Store(prepared()), Client()
    async def uncertain(*_args, **_kwargs):
        raise ConnectionError('uncertain attempt commit')
    monkeypatch.setattr(store, 'prepare_attempt', uncertain)
    worker = HaPresenceWorker(store, client_factory=lambda: client)
    assert not await worker._process_owned()
    assert client.sent == 0 and store.receipts == [] and store.releases == 0
