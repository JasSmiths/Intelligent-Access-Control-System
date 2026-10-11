import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest

from app.services import leaderboard_worker as owner
from app.services.leaderboard_dirty import ChartClaim, ChartLeaseLost, LeaderboardDirtyStore
from app.services.leaderboard_worker import LeaderboardWorker


@pytest.mark.asyncio
async def test_construction_is_inert_and_idle_start_stop_owns_one_task(monkeypatch):
    store = LeaderboardDirtyStore()
    claim = AsyncMock(return_value=None); monkeypatch.setattr(store, 'claim', claim)
    worker = LeaderboardWorker(store, AsyncMock())
    assert worker._task is None and not claim.called
    await worker.start(); task = worker._task
    await worker.start(); assert worker._task is task
    await asyncio.sleep(.01)
    await worker.stop()
    assert task.done() and worker._task is None


@pytest.mark.asyncio
async def test_repeated_stop_cancellation_drains_actual_evaluation_child(monkeypatch):
    store = LeaderboardDirtyStore(); claim = ChartClaim(1, uuid.uuid4(), None, None, None)
    monkeypatch.setattr(store, 'claim', AsyncMock(return_value=claim))
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancellations = 0
    async def evaluate(_claim):
        nonlocal cancellations
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellations += 1; cleanup.set(); await release.wait(); raise
    worker = LeaderboardWorker(store, evaluate)
    await worker.start(); await asyncio.wait_for(entered.wait(), 2)
    stopped = asyncio.create_task(worker.stop()); await asyncio.wait_for(cleanup.wait(), 2)
    stopped.cancel(); await asyncio.sleep(.01); stopped.cancel(); await asyncio.sleep(.01)
    assert not stopped.done() and cancellations == 1
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(stopped, 2)
    assert worker._task is None and worker._cleanup.done() and cancellations == 1
    assert not [task for task in asyncio.all_tasks() if task.get_name() in {'leaderboard-evaluate','leaderboard-renew'}]


@pytest.mark.asyncio
async def test_renewal_loss_cancels_and_drains_calculation_without_final_commit(monkeypatch):
    store = LeaderboardDirtyStore(); claim = ChartClaim(1, uuid.uuid4(), None, None, None)
    monkeypatch.setattr(store, 'renew', AsyncMock(side_effect=ChartLeaseLost('synthetic lost lease')))
    monkeypatch.setattr(owner, 'CHART_RENEW_SECONDS', .01)
    cleaned = asyncio.Event()
    async def evaluate(_claim):
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()
    worker = LeaderboardWorker(store, evaluate)
    with pytest.raises(ChartLeaseLost):
        await asyncio.wait_for(worker.process(claim), 2)
    assert cleaned.is_set() and store.renew.await_count == 1
