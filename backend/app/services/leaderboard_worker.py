"""One owned evaluator and one durable trailing chart generation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.services.leaderboard_dirty import (
    CHART_RENEW_SECONDS,
    ChartClaim,
    ChartLeaseLost,
    LeaderboardDirtyStore,
)

logger = get_logger(__name__)


class LeaderboardWorker:
    def __init__(self, store: LeaderboardDirtyStore, evaluate: Callable[[ChartClaim], Coroutine[Any, Any, None]]) -> None:
        self.store, self.evaluate = store, evaluate
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False
        self._cleanup: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            if self._cleanup is not None and not self._cleanup.done():
                raise RuntimeError("chart_cleanup_in_progress")
            self._cleanup, self._stopping = None, False
            self._task = asyncio.create_task(self._run(), name="leaderboard-refresh")

    def wake(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        self._stopping = True
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(self._stop_owned(), name="leaderboard-stop")
        await drain_owned(self._cleanup)

    async def _stop_owned(self) -> None:
        if self._task is not None:
            if not self._task.done() and not self._task.cancelling():
                self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while not self._stopping:
            self._wake.clear()
            try:
                claim = await self.store.claim()
                if claim is not None:
                    await self.process(claim)
                    continue
            except ChartLeaseLost:
                pass
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - durable dirty work survives evaluator/database failures
                logger.warning("Leaderboard refresh deferred")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.5)
            except TimeoutError:
                pass

    async def process(self, claim: ChartClaim) -> None:
        operation = asyncio.create_task(self.evaluate(claim), name="leaderboard-evaluate")
        lost = asyncio.Event()
        renewal = asyncio.create_task(self._renew(claim, operation, lost), name="leaderboard-renew")
        try:
            await asyncio.shield(operation)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if lost.is_set() and (current is None or not current.cancelling()):
                raise ChartLeaseLost("chart_lease_lost") from None
            raise
        finally:
            for child in (operation, renewal):
                if not child.done() and not child.cancelling():
                    child.cancel()
            await drain_owned(asyncio.gather(operation, renewal, return_exceptions=True))

    async def _renew(self, claim: ChartClaim, operation: asyncio.Task[None], lost: asyncio.Event) -> None:
        try:
            while not operation.done():
                await asyncio.sleep(CHART_RENEW_SECONDS)
                if not operation.done():
                    await self.store.renew(claim)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - ownership loss fences the calculation and its final commit
            lost.set()
            if not operation.done() and not operation.cancelling():
                operation.cancel()
