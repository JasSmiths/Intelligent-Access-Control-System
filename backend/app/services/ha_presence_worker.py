"""Dormant single-slot HA lifetime; importing this owner never starts a sender.

The global lease denotes one actual attempt and cleanup, not idle leadership.
Original send eligibility never renews. Known receipts settle after the I/O timer
exits, while renewal retains capacity through real session and client cleanup.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.modules.home_assistant.client import HomeAssistantClient
from app.modules.home_assistant.input_boolean_contracts import HaDelivery
from app.modules.home_assistant.input_booleans import command_frozen_input_boolean
from app.services.durable_work import WorkReadiness, WorkTopic, WorkWake, work_wake
from app.services.ha_presence_attempts import HaPresenceAttemptStore
from app.services.ha_presence_contracts import HaLeaseLost, PreparedHaAttempt
from app.services.ha_presence_database import (
    HA_OPERATION_SECONDS,
    HA_RECEIPT_SECONDS,
    HA_RENEWAL_SECONDS,
)

logger = get_logger(__name__)


@dataclass
class _AttemptLifetime:
    prepared: PreparedHaAttempt | None = None
    client: HomeAssistantClient | None = None
    active: asyncio.Task[HaDelivery | None] | asyncio.Task[None] | None = None
    renewal: asyncio.Task[None] | None = None
    lost: bool = False
    settling: bool = False


class HaPresenceWorker:
    def __init__(
        self, store: HaPresenceAttemptStore, *,
        client_factory: Callable[[], HomeAssistantClient] = HomeAssistantClient,
        wake: WorkWake | None = None,
    ) -> None:
        self.store, self._client_factory = store, client_factory
        self._task: asyncio.Task[None] | None = None
        self._cleanup: asyncio.Task[None] | None = None
        self._active: asyncio.Task[bool] | None = None
        self._wake = wake if wake is not None else work_wake(WorkTopic.HA)
        self._stopping = False

    async def start(self) -> None:
        if self._task is not None:
            return
        if self._cleanup is not None and not self._cleanup.done():
            raise RuntimeError("ha_presence_cleanup_in_progress")
        self._cleanup, self._stopping = None, False
        self._task = asyncio.create_task(self._run(), name="ha-presence-worker")

    def owns_capacity(self) -> bool:
        """Local actual cleanup remains occupied even after durable lease loss."""
        return self._active is not None and not self._active.done()

    def wake(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        self._stopping = True
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(self._stop_owned(), name="ha-presence-stop")
        await drain_owned(self._cleanup)

    async def _stop_owned(self) -> None:
        if self._task is not None:
            if not self._task.done() and not self._task.cancelling():
                self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while not self._stopping:
            generation = self._wake.generation
            try:
                pending = await self.store.readiness()
                if not pending.ready:
                    await self._wake.wait(generation, pending, fallback_seconds=0.5)
                    continue
                await self._recover()
                self._active = asyncio.create_task(self._process_owned(), name="ha-presence-attempt")
                try:
                    attempted = await asyncio.shield(self._active)
                finally:
                    if not self._active.done() and not self._active.cancelling():
                        self._active.cancel()
                    await drain_owned(asyncio.gather(self._active, return_exceptions=True))
                    self._active = None
                if not attempted:
                    await self._wake.wait(generation, WorkReadiness(False, 0.5), fallback_seconds=0.5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - Durable claims remain recoverable; never log provider text.
                logger.warning("ha_presence_worker_failed", extra={"exception_class":type(exc).__name__})
                await asyncio.sleep(.5)

    async def _recover(self) -> None:
        child = asyncio.create_task(self.store.recover_expired_attempts(), name="ha-presence-recovery")
        try:
            async with asyncio.timeout(HA_OPERATION_SECONDS):
                await asyncio.shield(child)
        finally:
            # Timer exits first. Active SQL is cancelled once; actual close drains.
            if not child.done() and not child.cancelling():
                child.cancel()
            await drain_owned(asyncio.gather(child, return_exceptions=True))

    async def _process_owned(self) -> bool:
        lifetime = _AttemptLifetime()
        deadline = asyncio.get_running_loop().time()+HA_OPERATION_SECONDS
        operation: asyncio.Task[HaDelivery | None] | None = None
        cancelled = False
        expired = False
        timer_active = False
        error: BaseException | None = None
        try:
            async with asyncio.timeout_at(deadline) as timer:
                timer_active = True
                # The callback shortens the same original timer after DB preparation.
                def committed(prepared: PreparedHaAttempt) -> None:
                    lifetime.prepared = prepared
                    if timer_active and not timer.expired():
                        timer.reschedule(min(deadline, prepared.start_deadline))
                    lifetime.renewal = asyncio.create_task(self._renew(lifetime), name="ha-presence-renew")
                operation = asyncio.create_task(self._perform_io(lifetime, deadline, committed), name="ha-presence-io")
                lifetime.active = operation
                await asyncio.shield(operation)
        except TimeoutError as exc:
            expired, error = True, exc
        except asyncio.CancelledError as exc:
            parent = asyncio.current_task()
            if lifetime.lost and (parent is None or not parent.cancelling()):
                error = HaLeaseLost("ha_attempt_capacity_lost")
            else:
                cancelled, error = True, exc
        except Exception as exc:  # noqa: BLE001 - No fresh provider work after any preparation failure.
            error = exc
        finally:
            timer_active = False
            # Never let a timer/second stop re-cancel actual cancellation cleanup.
            if operation is not None:
                if not operation.done() and not operation.cancelling():
                    operation.cancel()
                try:
                    await drain_owned(asyncio.gather(operation, return_exceptions=True))
                except asyncio.CancelledError:
                    cancelled = True  # Actual operation settled; still finish resource cleanup.
        delivery: HaDelivery | None = None
        if operation is not None and operation.done() and not operation.cancelled():
            try:
                delivery = operation.result()
            except Exception as exc:  # noqa: BLE001 - Committed claim still requires conservative no-send accounting.
                error = error or exc
        if delivery is None and lifetime.prepared is not None:
            delivery = HaDelivery("not_sent", "operation_expired" if expired else "operation_cancelled")
        cleanup = asyncio.create_task(self._settle_owned(lifetime, delivery), name="ha-presence-settlement")
        await drain_owned(cleanup)
        if error is not None and not isinstance(error, (TimeoutError, asyncio.CancelledError)):
            logger.warning("ha_presence_attempt_failed", extra={"exception_class":type(error).__name__})
        if cancelled:
            raise asyncio.CancelledError
        return lifetime.prepared is not None

    async def _perform_io(
        self, lifetime: _AttemptLifetime, deadline: float,
        on_committed: Callable[[PreparedHaAttempt], None],
    ) -> HaDelivery | None:
        prepared = await self.store.prepare_attempt(deadline, on_committed=on_committed)
        if prepared is None:
            return None
        if lifetime.lost:
            raise HaLeaseLost("ha_attempt_lease_lost_before_send")
        if self._stopping or asyncio.get_running_loop().time() >= prepared.start_deadline:
            return HaDelivery("not_sent", "operation_expired")
        try:
            lifetime.client = self._client_factory()
        except Exception:  # noqa: BLE001 - Client construction happens before any POST.
            return HaDelivery("not_sent", "client_unavailable")
        return await command_frozen_input_boolean(lifetime.client, prepared.entity_id, prepared.action,
            connection=prepared.connection, operation_deadline=prepared.start_deadline)

    async def _settle_owned(self, lifetime: _AttemptLifetime, delivery: HaDelivery | None) -> None:
        prepared = lifetime.prepared
        try:
            if prepared is not None and delivery is not None and not lifetime.lost:
                receipt = asyncio.create_task(self.store.record_receipt(prepared.claim, delivery), name="ha-presence-receipt")
                lifetime.active = receipt
                try:
                    async with asyncio.timeout(HA_RECEIPT_SECONDS):
                        await asyncio.shield(receipt)
                except asyncio.CancelledError:
                    parent = asyncio.current_task()
                    if not lifetime.lost or (parent is not None and parent.cancelling()):
                        raise
                    # Renewal cancels only the actual receipt child. It must not
                    # terminate this retained cleanup or the long-lived worker.
                    logger.warning("ha_presence_receipt_ownership_lost", extra={"attempt_id": str(prepared.claim.attempt_id)})
                except Exception as exc:  # noqa: BLE001 - Never relabel/rewrite an already committed receipt after close failure.
                    logger.warning("ha_presence_receipt_unconfirmed", extra={"attempt_id":str(prepared.claim.attempt_id),"exception_class":type(exc).__name__})
                finally:
                    if not receipt.done() and not receipt.cancelling():
                        receipt.cancel()
                    await drain_owned(asyncio.gather(receipt, return_exceptions=True))
        finally:
            lifetime.active = None
            try:
                if lifetime.client is not None:
                    await drain_owned(lifetime.client.close())
            finally:
                lifetime.settling = True
                if lifetime.renewal is not None:
                    if not lifetime.renewal.done() and not lifetime.renewal.cancelling():
                        lifetime.renewal.cancel()
                    await drain_owned(asyncio.gather(lifetime.renewal, return_exceptions=True))
                if prepared is not None:
                    release = asyncio.create_task(self.store.release_capacity(prepared.claim), name="ha-presence-release")
                    try:
                        async with asyncio.timeout(HA_RECEIPT_SECONDS):
                            await asyncio.shield(release)
                    finally:
                        if not release.done() and not release.cancelling():
                            release.cancel()
                        await drain_owned(asyncio.gather(release, return_exceptions=True))

    async def _renew(self, lifetime: _AttemptLifetime) -> None:
        assert lifetime.prepared is not None
        try:
            while not lifetime.settling:
                await asyncio.sleep(HA_RENEWAL_SECONDS)
                if not lifetime.settling and not await self.store.renew_attempt(lifetime.prepared.claim):
                    raise HaLeaseLost("ha_attempt_capacity_lost")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - Authority loss cancels active I/O/SQL once, never cleanup.
            lifetime.lost = True
            active = lifetime.active
            if active is not None and not active.done() and not active.cancelling():
                active.cancel()
