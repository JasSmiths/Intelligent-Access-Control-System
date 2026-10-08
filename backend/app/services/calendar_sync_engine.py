"""One admitted calendar attempt: fixed manifest, detached SDK work, account commits."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.models import ICloudCalendarSyncRun
from app.modules.icloud_calendar.client import ICloudCalendarClient
from app.modules.icloud_calendar.contracts import (
    CalendarSnapshot,
    ICloudCalendarClientError,
    ICloudCalendarIncomplete,
    ICloudCalendarReauthRequired,
    ICloudCalendarStopped,
    ICloudCalendarTimeout,
)
from app.modules.icloud_calendar.native import CalendarNativeOwner
from app.services.action_confirmations import ActionConfirmationError, persist_confirmation_rejection
from app.services.calendar_reconciliation import CalendarAccountReconciliation
from app.services.calendar_sync_commit import CalendarAccountCommit
from app.services.calendar_sync_contracts import (
    CALENDAR_RENEW_SECONDS,
    CalendarAccountCheckpoint,
    CalendarAccountClaim,
    CalendarOutcomeUnknown,
    CalendarOwnershipLost,
    CalendarRunContext,
    checked_calendar_checkpoints,
)
from app.services.calendar_sync_sessions import CalendarDetachedSession, CalendarSyncSessions
from app.services.calendar_sync_store import (
    CalendarAuthorization,
    CalendarRunAdmission,
    CalendarSyncStore,
    calendar_session,
    calendar_sql_budget,
)
from app.services.icloud_calendar_sdk import CalendarSDKOwner
from app.services.workflows.execution_contracts import AutomationExecution

logger = get_logger(__name__)


@dataclass(frozen=True)
class CalendarSyncReceipt:
    run_id: uuid.UUID
    checkpoints: tuple[CalendarAccountCheckpoint, ...]
    uncertain_account_id: uuid.UUID | None = None
    interrupted: bool = False
    cleanup_failed: bool = False
    events: tuple[tuple[str, dict[str, Any]], ...] = field(default=(), repr=False)
    context: CalendarRunContext | None = field(default=None, repr=False)


class CalendarAttemptLease:
    """Renewal lives through actual SDK cleanup; it never resets work's deadline."""
    def __init__(self, store: CalendarSyncStore, admission: CalendarRunAdmission,
                 native: CalendarNativeOwner) -> None:
        if admission.context.attempt is None:
            raise CalendarOwnershipLost("Calendar attempt has no current lease.")
        self.store, self.run_id = store, admission.run_id
        self.token, self.native = admission.context.attempt.token, native
        self.claim: CalendarAccountClaim | None = None
        # Claim/apply/release transitions exclude renewal. Native work does not.
        self.transition = asyncio.Lock()
        self.finished = asyncio.Event()
        self.failure: Exception | None = None
        self.child = asyncio.create_task(self._renew())

    async def _renew(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self.finished.wait(), timeout=CALENDAR_RENEW_SECONDS)
                return
            except TimeoutError:
                pass
            async with self.transition:
                if self.finished.is_set():
                    return
                try:
                    owned = await self.store.renew(self.run_id, self.token, self.claim)
                    if not owned:
                        raise CalendarOwnershipLost("Calendar ownership was lost while the actual operation drained.")
                except Exception as exc:  # noqa: BLE001 - Ownership failure stops native work, whose cleanup remains owned.
                    self.failure = exc
                    self.native.request_stop()
                    return

    async def stop(self) -> None:
        self.finished.set()
        await drain_owned(self.child)


class CalendarSyncEngine:
    def __init__(self, store: CalendarSyncStore, client: ICloudCalendarClient,
                 sdk: CalendarSDKOwner) -> None:
        self.store, self.client, self.sdk = store, client, sdk
        self.sessions = CalendarSyncSessions(store)
        self.commit = CalendarAccountCommit(store)

    async def run(
        self, run_id: uuid.UUID, *, origin: Literal["ui", "workflow"],
        actor: str, actor_user_id: uuid.UUID | None, execution: AutomationExecution | None,
        authorize: CalendarAuthorization, resume: bool = False,
        resume_authorize: CalendarAuthorization | None = None,
        disconnect: Callable[[], Awaitable[None]] | None = None,
        after_result: Callable[[CalendarSyncReceipt, CalendarNativeOwner], Awaitable[CalendarSyncReceipt]] | None = None,
    ) -> CalendarSyncReceipt:
        async def action(native: CalendarNativeOwner) -> CalendarSyncReceipt:
            if resume:
                if actor_user_id is None or origin != "ui" or resume_authorize is None:
                    raise ValueError("Calendar recovery requires explicit current Admin authority.")
                try:
                    admission = await self.store.resume(run_id, recovery_actor_user_id=actor_user_id,
                        authorize=resume_authorize, work=native.work)
                except ActionConfirmationError as exc:
                    # Admission session has settled and released its locks.
                    # Required rejection audit remains owned through cancellation.
                    await persist_confirmation_rejection(exc.rejection)
                    raise
            else:
                admission = await self.store.admit(run_id, origin=origin, actor_user_id=actor_user_id,
                    execution=execution, authorize=authorize, work=native.work)
            if not admission.proceed:
                return CalendarSyncReceipt(run_id, admission.checkpoints, context=admission.context)
            return await self._attempt(admission, native, actor, actor_user_id, authorize)
        return await self.sdk.run(action, disconnect=disconnect, after_result=after_result)

    async def _attempt(
        self, admission: CalendarRunAdmission, native: CalendarNativeOwner,
        actor: str, actor_user_id: uuid.UUID | None, authorize: CalendarAuthorization,
    ) -> CalendarSyncReceipt:
        lease = CalendarAttemptLease(self.store, admission, native)
        accepted = {item.account_id: item for item in admission.checkpoints}
        events: list[tuple[str, dict[str, Any]]] = []
        uncertain: uuid.UUID | None = None
        interrupted = cleanup_failed = False
        try:
            for account_id in admission.context.account_ids:
                if account_id in accepted and accepted[account_id].terminal:
                    continue
                try:
                    native.work.check()
                    async with lease.transition:
                        claim = await self.store.claim_account(admission, account_id,
                            authorize=authorize, work=native.work)
                        lease.claim = claim
                    if claim is None:
                        continue
                    detached = await self.sessions.read_session(claim, authorize=authorize, work=native.work)
                    # The worker rechecks after executor scheduling too. Neither
                    # close nor executor delay grants a fresh relative budget.
                    snapshot = await asyncio.to_thread(self._fetch, claim, detached, admission, native)
                    native.work.check()
                    async with lease.transition:
                        result = await self.commit.apply(claim, work=native.work, authorize=authorize,
                            stage=CalendarAccountReconciliation(claim, snapshot, work=native.work,
                                actor=actor, actor_user_id=actor_user_id), actor=actor, actor_user_id=actor_user_id)
                        # Accepted receipt is retained before any later cleanup
                        # or metadata publication can fail.
                        accepted[account_id] = result.checkpoint
                        events.extend(result.events)
                    # Realtime publication is composed by the service after the
                    # attempt receipt; durable capture already belongs to passes.
                except CalendarOutcomeUnknown:
                    uncertain = account_id
                    break
                except (ICloudCalendarStopped, ICloudCalendarTimeout, CalendarOwnershipLost):
                    interrupted = True
                    claim = lease.claim
                    if claim is not None:
                        failure = await self._failure(claim, status="interrupted",
                            detail="Calendar attempt stopped before account acceptance. Explicitly resume this run.")
                        if failure is not None:
                            accepted[account_id] = failure
                    break
                except ICloudCalendarClientError as exc:
                    claim = lease.claim
                    if claim is None:
                        raise
                    status: Literal["requires_reauth", "incomplete", "error"] = (
                        "requires_reauth" if isinstance(exc, ICloudCalendarReauthRequired) else
                        "incomplete" if isinstance(exc, ICloudCalendarIncomplete) else "error")
                    failure = await self._failure(claim, status=status, detail=str(exc))
                    if failure is not None:
                        accepted[account_id] = failure
                except Exception as exc:  # noqa: BLE001 - Stop unknown owner failures without discarding earlier accepted receipts.
                    interrupted = True
                    logger.error("Calendar account stopped before acceptance (%s)", type(exc).__name__)
                    claim = lease.claim
                    if claim is not None:
                        failure = await self._failure(claim, status="error",
                            detail="Calendar account could not be accepted. Read this run before explicitly resuming it.")
                        if failure is not None:
                            accepted[account_id] = failure
                    break
                finally:
                    claim = lease.claim
                    if claim is not None and uncertain is None:
                        async with lease.transition:
                            try:
                                await self.store.release_account(claim)
                            except Exception:  # noqa: BLE001 - Observe arbitrary lease-cleanup failure without reversing a known receipt.
                                cleanup_failed = True
                                logger.error("Calendar account lease cleanup failed for %s", account_id)
                            lease.claim = None
        finally:
            # No renewer/SQL cleanup is detached. For an uncertain commit retain
            # its durable fences; a later authorized recovery must resolve them.
            await lease.stop()
            if uncertain is None:
                try:
                    await self.store.finish_attempt(admission.run_id, lease.token)
                except Exception:  # noqa: BLE001 - Preserve accepted domain receipts across arbitrary final cleanup failure.
                    cleanup_failed = True
                    logger.error("Calendar attempt cleanup failed for %s", admission.run_id)
        try:
            async with calendar_session(self.store.sessions) as session:
                await calendar_sql_budget(session, None)
                row = await session.get(ICloudCalendarSyncRun, admission.run_id)
                if row is not None:
                    accepted.update({item.account_id: item for item in checked_calendar_checkpoints(row.account_results)})
        except Exception:  # noqa: BLE001 - Durable known receipts remain truthful if this optional final read fails.
            cleanup_failed = True
        ordered = tuple(accepted[identity] for identity in admission.context.account_ids if identity in accepted)
        return CalendarSyncReceipt(admission.run_id, ordered, uncertain, interrupted,
            cleanup_failed or lease.failure is not None, tuple(events), admission.context)


    def _fetch(self, claim: CalendarAccountClaim, detached: CalendarDetachedSession,
               admission: CalendarRunAdmission, native: CalendarNativeOwner) -> CalendarSnapshot:
        detached.before_dispatch(native.work)
        return self.client.fetch_snapshot(apple_id=claim.apple_id, session_bundle=detached.bundle,
            coverage=admission.context.coverage, operation=native)

    async def _failure(self, claim: CalendarAccountClaim, *,
        status: Literal["requires_reauth", "incomplete", "interrupted", "error"], detail: str,
    ) -> CalendarAccountCheckpoint | None:
        # This checkpoint has no domain effects. A failed diagnostic write must
        # never relabel earlier accepted domain commits or disclose raw errors.
        try:
            return await self.sessions.record_failure(claim, status=status, detail=detail)
        except Exception as exc:  # noqa: BLE001 - Preserve accepted receipts when a separate diagnostic transaction fails.
            logger.error("Calendar failure checkpoint unavailable (%s)", type(exc).__name__)
            return None
