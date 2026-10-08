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
)
from app.services.realtime_contracts import PublicationTransport

logger = get_logger(__name__)


class AccessPublicationLane:
    """Construction is inert; no new top-level application service/dispatcher.

    The access completion owner starts/stops this fixed single lane separately
    from its two media slots. Every processing/renewal child is awaited on stop.
    """

    def __init__(self, store: AccessPublicationStore, transport: PublicationTransport) -> None:
        self.store, self.transport = store, transport
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False

    async def start(self) -> None:
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._run(), name="access-publication")

    def wake(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        self._stopping = True
        await drain_owned(self._stop_owned())

    async def _stop_owned(self) -> None:
        task = self._task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while not self._stopping:
            self._wake.clear()
            try:
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
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.5)
            except TimeoutError:
                pass

    async def deliver(self, claim: PublicationClaim) -> None:
        """One owned operation; IDs/claims only, no ORM state over transport I/O."""
        operation = asyncio.create_task(self._send(claim), name="access-publication-send")
        lost = asyncio.Event()
        renewal = asyncio.create_task(
            self._renew(claim, operation, lost), name="access-publication-renew"
        )
        try:
            await operation
        except asyncio.CancelledError:
            parent = asyncio.current_task()
            if lost.is_set() and (parent is None or not parent.cancelling()):
                raise PipelineLeaseLost("publication_lease_lost") from None
            raise
        finally:
            operation.cancel()
            renewal.cancel()
            await asyncio.gather(operation, renewal, return_exceptions=True)

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
            operation.cancel()

    async def _send(self, claim: PublicationClaim) -> None:
        try:
            message = await self.store.load_message(claim)
            await self.store.assert_owned(claim)
            async with asyncio.timeout(PUBLICATION_TIMEOUT_SECONDS):
                receipt = await self.transport.publish(message.event_type, message.payload)
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
