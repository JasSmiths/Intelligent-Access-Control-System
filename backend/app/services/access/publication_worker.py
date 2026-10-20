"""One independent owned publication lane inside access completion ownership."""

from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.services.access.pipeline_contracts import PipelineLeaseLost
from app.services.access.publication import (
    PUBLICATION_RENEWAL_SECONDS,
    PUBLICATION_TIMEOUT_SECONDS,
    AccessPublicationStore,
    PublicationClaim,
    PublicationMessage,
)
from app.services.durable_work import WorkReadiness, WorkTopic, WorkWake, work_wake
from app.services.realtime_contracts import PublicationTransport, RealtimePublishReceipt

logger = get_logger(__name__)


class AccessPublicationLane:
    """Construction is inert; no new top-level application service/dispatcher.

    The access completion owner starts/stops this fixed single lane separately
    from its two media slots. Every processing/renewal child is awaited on stop.
    """

    def __init__(self, store: AccessPublicationStore, transport: PublicationTransport, wake: WorkWake | None = None) -> None:
        self.store, self.transport = store, transport
        self._task: asyncio.Task[None] | None = None
        self._wake = wake if wake is not None else work_wake(WorkTopic.PUBLICATION)
        self._stopping = False
        self._cleanup: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            if self._cleanup is not None and not self._cleanup.done():
                raise RuntimeError("publication_cleanup_in_progress")
            self._cleanup = None
            self._stopping = False
            self._task = asyncio.create_task(self._run(), name="access-publication")

    def wake(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        self._stopping = True
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(self._stop_owned(), name="access-publication-stop")
        await drain_owned(self._cleanup)

    async def _stop_owned(self) -> None:
        task = self._task
        if task is not None:
            if not task.done() and not task.cancelling():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while not self._stopping:
            generation = self._wake.generation
            try:
                pending = await self.store.readiness()
                if not pending.ready:
                    await self._wake.wait(generation, pending, fallback_seconds=0.5)
                    continue
                claim = await self.store.claim()
                if claim is not None:
                    await self.deliver(claim)
                    continue
            except PipelineLeaseLost:
                pass  # A different current owner will recover the checkpoint.
            except Exception as exc:  # noqa: BLE001 - optional publication remains durably retryable
                logger.warning(
                    "access_publication_failed", extra={"exception_class": type(exc).__name__}
                )
                await asyncio.sleep(0.5)
                continue
            await self._wake.wait(generation, WorkReadiness(False, 0.5), fallback_seconds=0.5)

    async def deliver(self, claim: PublicationClaim) -> None:
        """One owned operation; IDs/claims only, no ORM state over transport I/O."""
        child = asyncio.create_task(self._deliver_owned(claim), name="access-publication-delivery")
        try:
            await asyncio.shield(child)
        finally:
            if not child.done() and not child.cancelling():
                child.cancel()
            await drain_owned(asyncio.gather(child, return_exceptions=True))

    async def _deliver_owned(self, claim: PublicationClaim) -> None:
        operation = asyncio.create_task(self._send(claim), name="access-publication-send")
        lost = asyncio.Event()
        renewal = asyncio.create_task(
            self._renew(claim, operation, lost), name="access-publication-renew"
        )
        try:
            await asyncio.shield(operation)
        except asyncio.CancelledError:
            parent = asyncio.current_task()
            if lost.is_set() and (parent is None or not parent.cancelling()):
                raise PipelineLeaseLost("publication_lease_lost") from None
            raise
        finally:
            for child in (operation, renewal):
                if not child.done() and not child.cancelling():
                    child.cancel()
            await drain_owned(asyncio.gather(operation, renewal, return_exceptions=True))

    async def _renew(
        self, claim: PublicationClaim, operation: asyncio.Task[None], lost: asyncio.Event
    ) -> None:
        try:
            while not operation.done():
                await asyncio.sleep(PUBLICATION_RENEWAL_SECONDS)
                if not operation.done():
                    await self.store.renew(claim)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - losing DB ownership must cancel the admitted transport operation
            lost.set()
            if not operation.done() and not operation.cancelling():
                operation.cancel()

    async def _send(self, claim: PublicationClaim) -> None:
        try:
            message = await self.store.load_message(claim)
            lease_deadline = await self.store.assert_owned(claim)
            if asyncio.get_running_loop().time() >= lease_deadline:
                raise PipelineLeaseLost("publication_lease_lost")
            deadline = min(lease_deadline, asyncio.get_running_loop().time() + PUBLICATION_TIMEOUT_SECONDS)
            receipt = await self._publish(message, deadline)
            if receipt.accepted and message.visitor_event is not None:
                event_type, payload = message.visitor_event
                receipt = await self._publish(PublicationMessage(message.revision, event_type, payload), deadline)
            if receipt.accepted:
                await self.store.acknowledge(claim, message.revision)
            else:
                await self.store.retry(claim)
        except asyncio.CancelledError:
            raise  # The lease/result is retained; shutdown creates no detached write.
        except PipelineLeaseLost:
            raise
        except Exception:  # noqa: BLE001 - transport/projection failures retain current facts for retry
            await self.store.retry(claim)

    async def _publish(self, message: PublicationMessage, deadline: float) -> RealtimePublishReceipt:
        """Keep transport cleanup outside its timer and retain its actual child."""
        async def invoke() -> RealtimePublishReceipt:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("publication_start_deadline_expired")
            return await self.transport.publish(message.event_type, message.payload)

        child = asyncio.create_task(invoke(), name="access-publication-transport")
        try:
            async with asyncio.timeout_at(deadline):
                return await asyncio.shield(child)
        finally:
            if not child.done() and not child.cancelling():
                child.cancel()
            await drain_owned(asyncio.gather(child, return_exceptions=True))
