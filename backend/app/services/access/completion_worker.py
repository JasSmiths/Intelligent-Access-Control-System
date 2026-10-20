"""Fixed access-completion slots and their independent publication lane.

Composition is inert. The core/consumer cutover supplies explicit stage owners
and starts this lifetime; no facade, provider registry or alternative authority
is introduced here. A slot includes the actual operation's cleanup lifetime.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.services.access.completion_store import (
    COMPLETION_ACTIVE_SLOTS,
    COMPLETION_RENEWAL_SECONDS,
    COMPLETION_TIMEOUT_SECONDS,
    AccessCompletionStore,
)
from app.services.access.pipeline_contracts import CompletionClaim, PipelineLeaseLost
from app.services.access.publication_worker import AccessPublicationLane
from app.services.durable_work import (
    WorkReadiness,
    WorkTopic,
    WorkWake,
    wait_with_capacity,
    work_wake,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class CompletionStageOwners:
    """Closed explicit ports; each stage prepares and fences its own domain input."""

    visual: Callable[[CompletionClaim, float], Awaitable[None]]
    snapshot: Callable[[CompletionClaim, float], Awaitable[None]]
    visitor_link: Callable[[CompletionClaim, float], Awaitable[None]]
    zone_shadow: Callable[[CompletionClaim, float], Awaitable[None]]


class AccessCompletionOwner:
    def __init__(
        self,
        store: AccessCompletionStore,
        stages: CompletionStageOwners,
        publication: AccessPublicationLane,
        wake: WorkWake | None = None,
    ) -> None:
        self.store, self.stages, self.publication = store, stages, publication
        self._task: asyncio.Task[None] | None = None
        self._cleanup: asyncio.Task[None] | None = None
        self._operations: set[asyncio.Task[None]] = set()
        self._wake = wake if wake is not None else work_wake(WorkTopic.COMPLETION)
        self._stopping = False

    async def start(self) -> None:
        if self._task is not None:
            return
        if self._cleanup is not None and not self._cleanup.done():
            raise RuntimeError("completion_cleanup_in_progress")
        self._cleanup = None
        self._stopping = False
        await self.publication.start()
        self._task = asyncio.create_task(self._run(), name="access-completion")

    def wake(self) -> None:
        self._wake.set()
        self.publication.wake()

    async def stop(self) -> None:
        self._stopping = True
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(self._stop_owned(), name="access-completion-stop")
        await drain_owned(self._cleanup)

    async def _stop_owned(self) -> None:
        try:
            if self._task is not None:
                if not self._task.done() and not self._task.cancelling():
                    self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
                self._task = None
        finally:
            await self.publication.stop()

    async def _run(self) -> None:
        try:
            while not self._stopping:
                try:
                    for task in tuple(self._operations):
                        if task.done():
                            self._operations.remove(task)
                            task.result()
                    generation = self._wake.generation
                    if len(self._operations) == COMPLETION_ACTIVE_SLOTS:
                        await wait_with_capacity(self._wake, generation, WorkReadiness(), self._operations,
                                                 full=True, fallback_seconds=0.5)
                        continue
                    pending = await self.store.readiness()
                    if not pending.ready:
                        await wait_with_capacity(self._wake, generation, pending, self._operations,
                                                 full=False, fallback_seconds=0.5)
                        continue
                    claims = await self.store.claim(COMPLETION_ACTIVE_SLOTS - len(self._operations))
                    for claim in claims:
                        self._operations.add(asyncio.create_task(
                            self._process(claim), name="access-completion-operation"
                        ))
                    if not claims:
                        await wait_with_capacity(self._wake, generation, WorkReadiness(False, 0.5),
                                                 self._operations, full=False, fallback_seconds=0.5)
                except Exception as exc:  # noqa: BLE001 - durable optional claims remain recoverable
                    logger.warning("access_completion_worker_failed",
                                   extra={"exception_class": type(exc).__name__})
                    await asyncio.sleep(0.5)
        finally:
            children = tuple(self._operations)
            for child in children:
                if not child.done() and not child.cancelling():
                    child.cancel()
            await drain_owned(asyncio.gather(*children, return_exceptions=True))
            self._operations.clear()

    async def _process(self, claim: CompletionClaim) -> None:
        child = asyncio.create_task(self._process_owned(claim), name="access-completion-job")
        try:
            await asyncio.shield(child)
        finally:
            if not child.done() and not child.cancelling():
                child.cancel()
            await drain_owned(asyncio.gather(child, return_exceptions=True))

    async def _process_owned(self, claim: CompletionClaim) -> None:
        operation: asyncio.Task[None] | None = None
        renewal: asyncio.Task[None] | None = None
        lost = asyncio.Event()
        error: Exception | None = None
        try:
            # Includes cancellable DB preparation. The child retains real session
            # cleanup after the deadline; no provider starts after cancelled input.
            operation_deadline = asyncio.get_running_loop().time() + COMPLETION_TIMEOUT_SECONDS
            async with asyncio.timeout_at(operation_deadline) as timeout:
                deadline = min(operation_deadline, await self.store.assert_owned(claim))
                timeout.reschedule(deadline)
                operation = asyncio.create_task(self._dispatch(claim, deadline),
                                                name="access-completion-stage")
                renewal = asyncio.create_task(self._renew(claim, operation, lost),
                                              name="access-completion-renew")
                try:
                    await asyncio.shield(operation)
                except asyncio.CancelledError:
                    parent = asyncio.current_task()
                    if lost.is_set() and (parent is None or not parent.cancelling()):
                        raise PipelineLeaseLost("completion_lease_lost") from None
                    raise
        except Exception as exc:  # noqa: BLE001 - retry only after every actual child settles
            error = PipelineLeaseLost("completion_lease_lost") if lost.is_set() else exc
        finally:
            children = tuple(child for child in (operation, renewal) if child is not None)
            for child in children:
                if not child.done() and not child.cancelling():
                    child.cancel()
            await drain_owned(asyncio.gather(*children, return_exceptions=True))
        if error is not None:
            logger.warning("access_completion_stage_failed",
                           extra={"event_id": str(claim.event_id), "stage": claim.stage,
                                  "exception_class": type(error).__name__})
            if isinstance(error, PipelineLeaseLost):
                return  # The current durable owner alone may change this claim.
            try:
                await self.store.retry(claim)
            except PipelineLeaseLost:
                pass  # Expiry/takeover is recovered by the bounded claim scan.

    async def _dispatch(self, claim: CompletionClaim, deadline: float) -> None:
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("completion_start_deadline_expired")
        if claim.stage == "visual":
            await self.stages.visual(claim, deadline)
        elif claim.stage == "snapshot":
            await self.stages.snapshot(claim, deadline)
        elif claim.stage == "visitor_link":
            await self.stages.visitor_link(claim, deadline)
        elif claim.stage == "zone_shadow":
            await self.stages.zone_shadow(claim, deadline)
        else:
            raise ValueError("Publication belongs to its independent lane")

    async def _renew(
        self, claim: CompletionClaim, operation: asyncio.Task[None], lost: asyncio.Event
    ) -> None:
        try:
            while not operation.done():
                await asyncio.sleep(COMPLETION_RENEWAL_SECONDS)
                if not operation.done():
                    await self.store.renew(claim)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - lost authority cancels the actual stage exactly once
            lost.set()
            if not operation.done() and not operation.cancelling():
                operation.cancel()
