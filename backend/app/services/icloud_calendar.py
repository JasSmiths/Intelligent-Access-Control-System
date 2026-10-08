import asyncio
import uuid
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any, Literal

from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.models import ICloudCalendarSyncRun
from app.modules.icloud_calendar.client import (
    ICloudCalendarClient,
)
from app.modules.icloud_calendar.native import CalendarNativeOwner
from app.modules.icloud_calendar.contracts import CalendarWork, ICloudCalendarStopped, ICloudCalendarTimeout
from app.services.action_confirmations import ActionConfirmationError, persist_confirmation_rejection
from app.services.calendar_accounts import CalendarAccountStore, serialize_calendar_checkpoint_receipt, serialize_icloud_sync_run
from app.services.calendar_authentication import CalendarAuthentication
from app.services.calendar_sync_engine import CalendarSyncEngine, CalendarSyncReceipt
from app.services.calendar_sync_store import (
    CalendarAuthorization,
    CalendarSyncStore,
    calendar_session,
    calendar_sql_budget,
)
from app.services.event_bus import event_bus
from app.services.icloud_calendar_sdk import CalendarSDKOwner
from app.services.workflows.execution_contracts import AutomationExecution

logger = get_logger(__name__)
CALENDAR_OBSERVATION_SECONDS = 5


class ICloudCalendarService:
    def __init__(self, client: ICloudCalendarClient | None = None) -> None:
        self._client = client or ICloudCalendarClient()
        self.sdk = CalendarSDKOwner(self._client)
        self._sync_store = CalendarSyncStore()
        self._sync_engine = CalendarSyncEngine(self._sync_store, self._client, self.sdk)
        self._accounts = CalendarAccountStore(self._sync_store)
        self._authentication = CalendarAuthentication(self._client, self.sdk, self._accounts)

    async def start(self) -> None:
        await self.sdk.start()

    async def start_auth(self, *, apple_id: str, password: str, actor: str,
                         actor_user_id: uuid.UUID, authorize: CalendarAuthorization,
                         disconnect: Callable[[], Awaitable[None]] | None = None) -> dict[str, Any]:
        return await self._authentication.start(apple_id=apple_id, password=password,
            actor=actor, actor_user_id=actor_user_id, authorize=authorize, disconnect=disconnect,
            after_result=self._observe_auth_result)

    async def verify_auth(self, *, handshake_id: str, code: str, actor: str,
                          actor_user_id: uuid.UUID, authorize: CalendarAuthorization,
                          disconnect: Callable[[], Awaitable[None]] | None = None) -> dict[str, Any]:
        return await self._authentication.verify(handshake_id=handshake_id, code=code,
            actor=actor, actor_user_id=actor_user_id, authorize=authorize, disconnect=disconnect,
            after_result=self._observe_auth_result)

    async def _observe_auth_result(self, result: dict[str, Any], native: CalendarNativeOwner) -> dict[str, Any]:
        return await self._observe_accepted(result, self._publish_account_change, work=native.work) if result.get("status") == "connected" else result

    async def remove_account(self, account_id: uuid.UUID, *, actor: str,
                             actor_user_id: uuid.UUID, authorize: CalendarAuthorization,
                             disconnect: Callable[[], Awaitable[None]] | None = None) -> dict[str, Any]:
        async def owned(native: CalendarNativeOwner) -> dict[str, Any]:
            try:
                return await self._accounts.remove(account_id, actor=actor,
                    actor_user_id=actor_user_id, authorize=authorize, work=native.work)
            except ActionConfirmationError as exc:
                # Account owner has settled rollback/actual close. Keep required
                # rejection persistence in this uncancelled admitted child.
                await persist_confirmation_rejection(exc.rejection)
                raise
        async def after(result: dict[str, Any], native: CalendarNativeOwner) -> dict[str, Any]:
            async def observe() -> None:
                await self._publish_account_change()
                native.work.check()
                if result["passes_cancelled"]:
                    await event_bus.publish("visitor_pass.cancelled", {"source": "icloud_calendar",
                        "account_id": str(account_id), "cancelled_count": result["passes_cancelled"]})
            return await self._observe_accepted(result, observe, work=native.work)
        result = await self.sdk.run(owned, disconnect=disconnect, after_result=after)
        return result["account"]

    @staticmethod
    async def _observe_accepted(
        receipt: dict[str, Any], observe: Callable[[], Awaitable[dict[str, Any] | None]],
        *, work: CalendarWork,
    ) -> dict[str, Any]:
        """Bounded observation cannot erase acceptance or detach actual cleanup."""
        try:
            observation_seconds = min(CALENDAR_OBSERVATION_SECONDS, work.remaining())
        except (ICloudCalendarStopped, ICloudCalendarTimeout):
            return receipt
        async def observation() -> dict[str, Any] | None:
            work.check()
            return await observe()
        child = asyncio.create_task(observation())
        try:
            # Exit this timer before the sole child cancellation. A second
            # caller cancellation during drain cannot deliver another cancel.
            async with asyncio.timeout(observation_seconds):
                improved = await asyncio.shield(child)
            return improved if improved is not None else receipt
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - Known receipt wins observation failure/cancellation.
            if not child.done() and not child.cancelling():
                child.cancel()
            try:
                await drain_owned(child)
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - Retrieve actual cleanup before returning known acceptance.
                pass
            logger.warning("Calendar observation interrupted after durable receipt")
            return receipt

    @staticmethod
    async def _publish_account_change() -> dict[str, Any] | None:
        try:
            await event_bus.publish("icloud_calendar.accounts_changed", {})
        except Exception:  # noqa: BLE001 - Realtime cannot reverse durable acceptance.
            logger.warning("Calendar account realtime invalidation failed after acceptance")

    async def sync_all(
        self, *, run_id: uuid.UUID, origin: Literal["ui", "workflow"],
        actor_user_id: uuid.UUID | None, actor: str, execution: AutomationExecution | None,
        authorize: CalendarAuthorization, resume: bool = False,
        resume_authorize: CalendarAuthorization | None = None,
        disconnect: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        response: dict[str, Any] | None = None
        async def after(receipt: CalendarSyncReceipt, native: CalendarNativeOwner) -> CalendarSyncReceipt:
            nonlocal response
            result = serialize_calendar_checkpoint_receipt(run_id, receipt.context,
                receipt.checkpoints, uncertain=receipt.uncertain_account_id is not None)

            if receipt.interrupted:
                result["interrupted"] = True
            if receipt.cleanup_failed:
                result["cleanup_pending"] = True

            async def observe() -> dict[str, Any]:
                improved = result
                async with calendar_session(self._sync_store.sessions) as session:
                    await calendar_sql_budget(session, None)
                    row = await session.get(ICloudCalendarSyncRun, run_id)
                    if row is not None:
                        improved = serialize_icloud_sync_run(row)
                if receipt.uncertain_account_id is not None:
                    improved["status"] = "unknown"
                    improved["error"] = "An account commit is unresolved. Read this known run and explicitly recover after its current ownership settles."
                if receipt.interrupted:
                    improved["interrupted"] = True
                if receipt.cleanup_failed:
                    improved["cleanup_pending"] = True
                # Durable audit/intents already committed; publication is observation.
                native.work.check()
                await event_bus.publish("icloud_calendar.sync_completed", {"sync": improved})
                native.work.check()
                await event_bus.publish("icloud_calendar.accounts_changed", {})
                for event_type, payload in receipt.events:
                    native.work.check()
                    await event_bus.publish(event_type, payload)
                return improved
            response = await self._observe_accepted(result, observe, work=native.work)
            return receipt
        await self._sync_engine.run(run_id, origin=origin, actor=actor,
            actor_user_id=actor_user_id, execution=execution, authorize=authorize,
            resume=resume, resume_authorize=resume_authorize, disconnect=disconnect, after_result=after)
        assert response is not None
        return response

    async def stop(self) -> None:
        await self.sdk.stop()



@lru_cache(maxsize=1)
def get_icloud_calendar_service() -> ICloudCalendarService:
    return ICloudCalendarService()
