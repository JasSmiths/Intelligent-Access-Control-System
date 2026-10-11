"""Short calendar-run admissions and Account→Run ownership transactions."""
from __future__ import annotations

import math
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import event, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.db.session import AsyncSessionLocal
from app.models import ICloudCalendarAccount, ICloudCalendarSyncRun, SystemSetting
from app.modules.icloud_calendar.contracts import CalendarCoverage, CalendarWork
from app.services.auth_root import reload_auth_root_for_transaction
from app.services.calendar_sync_contracts import (
    CALENDAR_LEASE_SECONDS,
    CalendarAccountCheckpoint,
    CalendarAccountClaim,
    CalendarAttempt,
    CalendarOutcomeUnknown,
    CalendarOwnershipLost,
    CalendarRunConflict,
    CalendarRunContext,
    CalendarSettingRevision,
    checked_calendar_checkpoints,
)
from app.services.icloud_calendar_sdk import CalendarSDKBusy
from app.services.settings import get_runtime_config_for_session
from app.services.workflows.execution_contracts import AutomationExecution, checked_execution

CalendarAuthorization = Callable[[AsyncSession, bool], Awaitable[datetime | None]]
logger = get_logger(__name__)
_CALENDAR_TRANSACTION_STARTED = "calendar_transaction_started"


def calendar_new_session(sessions: async_sessionmaker[AsyncSession]) -> AsyncSession:
    """Remember actual DB use even if transport loss resets local tx state."""
    session = sessions()
    session.info[_CALENDAR_TRANSACTION_STARTED] = False
    def began(sync_session: object, transaction: object, connection: object) -> None:
        session.info[_CALENDAR_TRANSACTION_STARTED] = True
    event.listen(session.sync_session, "after_begin", began)
    return session


async def calendar_close(session: AsyncSession) -> None:
    """Retain failed close's actual invalidation cleanup before owner release."""
    try:
        await drain_owned(session.close())
    except BaseException:
        try:
            await drain_owned(session.invalidate())
        except BaseException:  # noqa: BLE001 - Observe both cleanup failures; never infer transaction outcome.
            logger.error("Calendar session close and invalidation cleanup failed")
        raise



async def calendar_rollback(session: AsyncSession) -> bool:
    """Positive live rollback; invalidated local state cannot prove settlement."""
    try:
        if not session.in_transaction():
            # Only a tracked session that never began DB work is a definite
            # no-effect path. Local state reset after earlier DB use proves none.
            return session.info.get(_CALENDAR_TRANSACTION_STARTED) is False
        connection = await session.connection()
        if connection.closed or connection.invalidated:
            return False
        await drain_owned(session.rollback())
        return not connection.invalidated
    except BaseException:  # noqa: BLE001 - Positive settlement is required; faults/cancellation remain unknown.
        return False


@asynccontextmanager
async def calendar_session(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """Query cancellation remains active; actual connection close cannot detach."""
    session = calendar_new_session(sessions)
    try:
        yield session
    except BaseException as primary:
        rolled_back = await calendar_rollback(session)
        try:
            await calendar_close(session)
        except BaseException:  # noqa: BLE001 - A settled primary survives late close failure.
            if not rolled_back:
                raise CalendarOutcomeUnknown("Calendar transaction cleanup is unresolved. Read the original account/run before retrying.") from primary
        if not rolled_back:
            raise CalendarOutcomeUnknown("Calendar transaction settlement is unresolved. Read the original account/run before retrying.") from primary
        raise
    else:
        await calendar_close(session)


@dataclass(frozen=True)
class CalendarRunAdmission:
    run_id: uuid.UUID
    context: CalendarRunContext
    checkpoints: tuple[CalendarAccountCheckpoint, ...]
    proceed: bool


async def calendar_sql_budget(session: AsyncSession, work: CalendarWork | None) -> None:
    """Cleanup can retain a lease after expiry, never renew work eligibility."""
    milliseconds = 5000 if work is None else max(1, math.ceil(work.remaining() * 1000))
    await session.execute(text("SELECT set_config('statement_timeout', :statement, true), set_config('lock_timeout', :lock, true)"),
                          {"statement": str(milliseconds), "lock": str(min(1000, milliseconds))})
    if work is not None:
        work.check()


async def calendar_database_clock(session: AsyncSession) -> datetime:
    value = (await session.execute(select(func.clock_timestamp()))).scalar_one()
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise TypeError("Calendar database clock must be timezone-aware.")
    return value


def capture_calendar_coverage(site_timezone: str) -> CalendarCoverage:
    timezone = ZoneInfo(site_timezone)
    today = datetime.now(timezone).date()
    starts_at = datetime.combine(today, time.min, tzinfo=timezone)
    ends_at = starts_at + timedelta(days=15)
    return CalendarCoverage.capture(starts_at.astimezone(UTC), ends_at.astimezone(UTC))


def _attempt(context: CalendarRunContext, token: uuid.UUID, now: datetime) -> CalendarAttempt:
    attempt = context.attempt
    if attempt is None or attempt.token != token or attempt.lease_until <= now:
        raise CalendarOwnershipLost("The calendar run attempt is no longer owned. Read its durable result before resuming.")
    return attempt


def stage_calendar_checkpoint(
    run: ICloudCalendarSyncRun, context: CalendarRunContext, checkpoint: CalendarAccountCheckpoint,
) -> None:
    if checkpoint.account_id not in context.account_ids:
        raise CalendarRunConflict("Calendar checkpoint is outside its immutable account manifest.")
    previous = checked_calendar_checkpoints(run.account_results)
    by_account = {item.account_id: item for item in previous}
    old = by_account.get(checkpoint.account_id)
    if old is not None and old.terminal:
        raise CalendarRunConflict("An accepted or terminal account checkpoint cannot repeat.")
    by_account[checkpoint.account_id] = checkpoint
    ordered = [by_account[identity] for identity in context.account_ids if identity in by_account]
    run.account_results = [item.model_dump(mode="json") for item in ordered]
    for name in ("events_scanned", "events_matched", "passes_created", "passes_updated", "passes_cancelled", "passes_skipped"):
        setattr(run, name, sum(getattr(item, name) for item in ordered if item.status == "ok"))


async def assert_calendar_before_commit(
    session: AsyncSession, run_id: uuid.UUID, token: uuid.UUID,
    *, work: CalendarWork | None, claim: CalendarAccountClaim | None = None,
    original_run_until: datetime | None = None,
    original_account_until: datetime | None = None,
    authority_until: datetime | None = None,
) -> None:
    """After every write flush, reread exact fences and a fresh DB clock.

    Original expiry checks prevent a renewal's own staged extension from reviving
    an owner that expired during its flush. No write may follow this final sample.
    """
    await calendar_sql_budget(session, work)
    await session.flush()
    await calendar_sql_budget(session, work)
    with session.no_autoflush:
        stored = await session.scalar(select(ICloudCalendarSyncRun.run_context)
            .where(ICloudCalendarSyncRun.id == run_id))
        context = CalendarRunContext.stored(stored) if stored is not None else None
        account_values = None
        if claim is not None:
            await calendar_sql_budget(session, work)
            account_values = (await session.execute(select(
                ICloudCalendarAccount.sync_generation, ICloudCalendarAccount.sync_lease_token,
                ICloudCalendarAccount.sync_lease_until).where(ICloudCalendarAccount.id == claim.account_id))).one_or_none()
        await calendar_sql_budget(session, work)
        now = await calendar_database_clock(session)
    if context is None:
        raise CalendarOwnershipLost("Calendar run disappeared before commit.")
    _attempt(context, token, now)
    if original_run_until is not None and original_run_until <= now:
        raise CalendarOwnershipLost("Original calendar run lease expired during renewal.")
    if claim is not None and (account_values is None or account_values[0] != claim.generation
        or account_values[1] != claim.account_token or account_values[2] is None
        or account_values[2] <= now):
        raise CalendarOwnershipLost("Calendar account ownership changed before commit.")
    if original_account_until is not None and original_account_until <= now:
        raise CalendarOwnershipLost("Original calendar account lease expired during renewal.")
    if authority_until is not None and authority_until <= now:
        raise CalendarOwnershipLost("Current workflow or request authority expired before account acceptance.")
    if work is not None:
        work.check()
    if session.new or session.dirty or session.deleted:
        raise RuntimeError("Calendar acceptance has an unflushed write after its final fence sample.")


class CalendarSyncStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession] = AsyncSessionLocal) -> None:
        self.sessions = sessions

    async def admit(
        self, run_id: uuid.UUID, *, origin: Literal["ui", "workflow"],
        actor_user_id: uuid.UUID | None, execution: AutomationExecution | None,
        authorize: CalendarAuthorization, work: CalendarWork,
    ) -> CalendarRunAdmission:
        if origin == "workflow":
            checked_execution(execution)
            if execution is None or uuid.UUID(execution["operation_id"]) != run_id:
                raise CalendarRunConflict("Calendar run ID must equal the checked workflow operation ID.")
        async with calendar_session(self.sessions) as session:
            await calendar_sql_budget(session, work)
            await reload_auth_root_for_transaction(session)
            await calendar_sql_budget(session, work)
            await authorize(session, False)
            # An existing ordinary admission is a receipt read, never a resume.
            await calendar_sql_budget(session, work)
            existing = await session.get(ICloudCalendarSyncRun, run_id)
            if existing is not None:
                context = CalendarRunContext.stored(existing.run_context)
                if not context.matches_origin(origin, actor_user_id, execution):
                    raise CalendarRunConflict("This calendar run ID belongs to another request or origin.")
                return CalendarRunAdmission(run_id, context, checked_calendar_checkpoints(existing.account_results), False)
            await calendar_sql_budget(session, work)
            config = await get_runtime_config_for_session(session)
            await calendar_sql_budget(session, work)
            account_ids = tuple((await session.scalars(select(ICloudCalendarAccount.id)
                .where(ICloudCalendarAccount.is_active.is_(True))
                .order_by(ICloudCalendarAccount.apple_id, ICloudCalendarAccount.id))).all())
            await calendar_sql_budget(session, work)
            now = await calendar_database_clock(session)
            context = CalendarRunContext(origin=origin, original_actor_user_id=actor_user_id,
                execution=execution, account_ids=account_ids, site_timezone=config.site_timezone,
                coverage=capture_calendar_coverage(config.site_timezone),
                attempt=CalendarAttempt(token=uuid.uuid4(), lease_until=now + timedelta(seconds=CALENDAR_LEASE_SECONDS)))
            await calendar_sql_budget(session, work)
            inserted = await session.scalar(insert(ICloudCalendarSyncRun).values(
                id=run_id, started_at=now, status="running", trigger_source=origin,
                triggered_by_user_id=actor_user_id, account_count=len(account_ids),
                run_context=context.model_dump(mode="json"), account_results=[])
                .on_conflict_do_nothing(index_elements=[ICloudCalendarSyncRun.id])
                .returning(ICloudCalendarSyncRun.id))
            work.check()
            if inserted is None:
                # Another process admitted the known UUID. Do not manufacture a
                # second manifest or execute this process's uncommitted context.
                await calendar_sql_budget(session, work)
                existing = await session.get(ICloudCalendarSyncRun, run_id)
                if existing is None:
                    raise CalendarRunConflict("Calendar admission changed; read the known run ID before retrying.")
                context = CalendarRunContext.stored(existing.run_context)
                if not context.matches_origin(origin, actor_user_id, execution):
                    raise CalendarRunConflict("This calendar run ID belongs to another request or origin.")
                return CalendarRunAdmission(run_id, context, checked_calendar_checkpoints(existing.account_results), False)
            assert context.attempt is not None
            await assert_calendar_before_commit(session, run_id, context.attempt.token, work=work)
            await session.commit()
            return CalendarRunAdmission(run_id, context, (), True)

    async def resume(
        self, run_id: uuid.UUID, *, recovery_actor_user_id: uuid.UUID,
        authorize: CalendarAuthorization, work: CalendarWork,
    ) -> CalendarRunAdmission:
        async with calendar_session(self.sessions) as session:
            await calendar_sql_budget(session, work)
            await reload_auth_root_for_transaction(session)
            await calendar_sql_budget(session, work)
            await authorize(session, False)
            await calendar_sql_budget(session, work)
            run = await session.scalar(select(ICloudCalendarSyncRun).where(ICloudCalendarSyncRun.id == run_id).with_for_update())
            if run is None:
                raise CalendarRunConflict("Calendar run was not found.")
            context = CalendarRunContext.stored(run.run_context)
            checkpoints = checked_calendar_checkpoints(run.account_results)
            await calendar_sql_budget(session, work)
            now = await calendar_database_clock(session)
            if context.attempt is not None and context.attempt.lease_until > now:
                raise CalendarSDKBusy("This calendar run still has an active owner. Wait for its actual cleanup before resuming.")
            await calendar_sql_budget(session, work)
            authority_until = await authorize(session, True)
            completed = {item.account_id for item in checkpoints if item.terminal}
            if all(identity in completed for identity in context.account_ids):
                await calendar_sql_budget(session, work)
                await session.flush()
                await calendar_sql_budget(session, work)
                with session.no_autoflush:
                    final_now = await calendar_database_clock(session)
                if authority_until is not None and authority_until <= final_now:
                    raise CalendarOwnershipLost("Your Admin session expired before the recovery receipt.")
                work.check()
                if session.new or session.dirty or session.deleted:
                    raise RuntimeError("Calendar recovery receipt has a pending write after its final clock.")
                await session.commit()
                return CalendarRunAdmission(run_id, context, checkpoints, False)
            context = context.model_copy(update={"attempt": CalendarAttempt(token=uuid.uuid4(),
                lease_until=now + timedelta(seconds=CALENDAR_LEASE_SECONDS),
                recovery_actor_user_id=recovery_actor_user_id),
                "recovery_actor_user_id": recovery_actor_user_id, "recovery_source": "ui"})
            run.run_context = context.model_dump(mode="json")
            run.status, run.finished_at, run.error = "running", None, None
            from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS, write_audit_log
            await calendar_sql_budget(session, work)
            await write_audit_log(session, category=TELEMETRY_CATEGORY_INTEGRATIONS,
                action="icloud_calendar.sync_resumed", actor="Calendar recovery", actor_user_id=recovery_actor_user_id,
                target_entity="ICloudCalendarSyncRun", target_id=run_id,
                metadata={"original_origin": context.origin, "completed_accounts": len(completed),
                    "remaining_accounts": len(context.account_ids) - len(completed)})
            assert context.attempt is not None
            await assert_calendar_before_commit(session, run_id, context.attempt.token, work=work, authority_until=authority_until)
            await session.commit()
            return CalendarRunAdmission(run_id, context, checkpoints, True)

    async def claim_account(
        self, admission: CalendarRunAdmission, account_id: uuid.UUID,
        *, authorize: CalendarAuthorization, work: CalendarWork,
    ) -> CalendarAccountClaim | None:
        attempt = admission.context.attempt
        if attempt is None or account_id not in admission.context.account_ids:
            raise CalendarOwnershipLost("Calendar account claim has no admitted run attempt.")
        async with calendar_session(self.sessions) as session:
            await calendar_sql_budget(session, work)
            await reload_auth_root_for_transaction(session)
            await calendar_sql_budget(session, work)
            await authorize(session, False)
            await calendar_sql_budget(session, work)
            account = await session.scalar(select(ICloudCalendarAccount)
                .where(ICloudCalendarAccount.id == account_id).with_for_update())
            await calendar_sql_budget(session, work)
            run = await session.scalar(select(ICloudCalendarSyncRun)
                .where(ICloudCalendarSyncRun.id == admission.run_id).with_for_update())
            if run is None:
                raise CalendarOwnershipLost("Calendar run disappeared before its account claim.")
            context = CalendarRunContext.stored(run.run_context)
            await calendar_sql_budget(session, work)
            now = await calendar_database_clock(session)
            _attempt(context, attempt.token, now)
            if any(item.account_id == account_id and item.terminal for item in checked_calendar_checkpoints(run.account_results)):
                return None
            await calendar_sql_budget(session, work)
            config = await get_runtime_config_for_session(session)
            await calendar_sql_budget(session, work)
            setting = await session.get(SystemSetting, "site_timezone")
            status: Literal["removed", "stale_configuration", "busy", "requires_reauth"] | None = None
            detail = ""
            if account is None or not account.is_active:
                status, detail = "removed", "This logical calendar account was removed."
            elif config.site_timezone != context.site_timezone:
                status, detail = "stale_configuration", "The site's calendar timezone changed. Start a deliberate new run for the new policy."
            elif account.sync_lease_token is not None and account.sync_lease_until is not None and account.sync_lease_until > now:
                status, detail = "busy", "This account is owned by another calendar attempt."
            elif not account.encrypted_session_bundle:
                status, detail = "requires_reauth", "Reconnect this account before explicitly resuming the same run."
            if status is not None:
                stage_calendar_checkpoint(run, context, CalendarAccountCheckpoint(account_id=account_id,
                    apple_id=account.apple_id if account else "", display_name=(account.display_name or account.apple_id) if account else "",
                    attempt_token=attempt.token, status=status, error=detail))
                await assert_calendar_before_commit(session, admission.run_id, attempt.token, work=work)
                await session.commit()
                return None
            assert account is not None and account.encrypted_session_bundle
            account.sync_generation += 1
            account.sync_lease_token = uuid.uuid4()
            account.sync_lease_until = now + timedelta(seconds=CALENDAR_LEASE_SECONDS)
            claim = CalendarAccountClaim(admission.run_id, attempt.token, account.id,
                account.sync_lease_token, account.sync_generation, account.apple_id,
                account.display_name or account.apple_id, account.encrypted_session_bundle,
                CalendarSettingRevision(setting is not None, setting.updated_at if setting else None))
            await assert_calendar_before_commit(session, admission.run_id, attempt.token, work=work, claim=claim)
            await session.commit()
            return claim

    async def renew(
        self, run_id: uuid.UUID, token: uuid.UUID, claim: CalendarAccountClaim | None,
    ) -> bool:
        work = None  # Renewal is short ownership cleanup, never new work eligibility.
        async with calendar_session(self.sessions) as session:
            await calendar_sql_budget(session, None)
            account = None
            if claim is not None:
                await calendar_sql_budget(session, work)
                account = await session.scalar(select(ICloudCalendarAccount)
                    .where(ICloudCalendarAccount.id == claim.account_id).with_for_update())
            await calendar_sql_budget(session, work)
            run = await session.scalar(select(ICloudCalendarSyncRun)
                .where(ICloudCalendarSyncRun.id == run_id).with_for_update())
            if run is None:
                return False
            context = CalendarRunContext.stored(run.run_context)
            await calendar_sql_budget(session, work)
            now = await calendar_database_clock(session)
            try:
                attempt = _attempt(context, token, now)
            except CalendarOwnershipLost:
                return False
            if claim is not None and (account is None or account.sync_generation != claim.generation
                or account.sync_lease_token != claim.account_token or account.sync_lease_until is None
                or account.sync_lease_until <= now):
                return False
            original_run_until = attempt.lease_until
            original_account_until = account.sync_lease_until if account is not None else None
            until = now + timedelta(seconds=CALENDAR_LEASE_SECONDS)
            if account is not None:
                account.sync_lease_until = until
            run.run_context = context.model_copy(update={"attempt": attempt.model_copy(update={"lease_until": until})}).model_dump(mode="json")
            try:
                await assert_calendar_before_commit(session, run_id, token, work=None, claim=claim,
                    original_run_until=original_run_until, original_account_until=original_account_until)
            except CalendarOwnershipLost:
                return False
            await session.commit()
            return True

    async def release_account(self, claim: CalendarAccountClaim) -> None:
        work = None
        async with calendar_session(self.sessions) as session:
            await calendar_sql_budget(session, None)
            await calendar_sql_budget(session, work)
            account = await session.scalar(select(ICloudCalendarAccount)
                .where(ICloudCalendarAccount.id == claim.account_id).with_for_update())
            if account is not None and account.sync_generation == claim.generation and account.sync_lease_token == claim.account_token:
                account.sync_lease_token, account.sync_lease_until = None, None
                await session.commit()

    async def finish_attempt(self, run_id: uuid.UUID, token: uuid.UUID) -> None:
        work = None
        async with calendar_session(self.sessions) as session:
            await calendar_sql_budget(session, None)
            await calendar_sql_budget(session, work)
            run = await session.scalar(select(ICloudCalendarSyncRun)
                .where(ICloudCalendarSyncRun.id == run_id).with_for_update())
            if run is None:
                return
            context = CalendarRunContext.stored(run.run_context)
            if context.attempt is None or context.attempt.token != token:
                return
            checkpoints = checked_calendar_checkpoints(run.account_results)
            completed = {item.account_id for item in checkpoints if item.terminal}
            run.status = "ok" if all(identity in completed for identity in context.account_ids) and all(item.status == "ok" for item in checkpoints) else "partial"
            await calendar_sql_budget(session, work)
            run.finished_at = await calendar_database_clock(session)
            run.run_context = context.model_copy(update={"attempt": None}).model_dump(mode="json")
            await session.commit()
