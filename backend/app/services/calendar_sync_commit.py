"""One account's domain effects, required audit and durable checkpoint commit."""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.models import ICloudCalendarAccount, ICloudCalendarSyncRun, SystemSetting
from app.modules.icloud_calendar.contracts import CalendarWork
from app.services.auth_root import reload_auth_root_for_transaction
from app.services.calendar_sync_contracts import (
    CalendarAccountCheckpoint,
    CalendarAccountClaim,
    CalendarOutcomeUnknown,
    CalendarOwnershipLost,
    CalendarRunContext,
    CalendarSettingRevision,
    checked_calendar_checkpoints,
)
from app.services.calendar_sync_store import (
    CalendarAuthorization,
    CalendarSyncStore,
    assert_calendar_before_commit,
    calendar_database_clock,
    calendar_close,
    calendar_new_session,
    calendar_sql_budget,
    stage_calendar_checkpoint,
)
from app.services.settings import get_runtime_config_for_session
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS, write_audit_log

logger = get_logger(__name__)


@dataclass(frozen=True)
class CalendarAccountApply:
    checkpoint: CalendarAccountCheckpoint
    events: tuple[tuple[str, dict[str, Any]], ...] = ()


CalendarAccountStage = Callable[[AsyncSession, ICloudCalendarAccount, CalendarRunContext], Awaitable[CalendarAccountApply]]


class CalendarAccountCommit:
    def __init__(self, store: CalendarSyncStore) -> None:
        self.store = store

    async def apply(
        self, claim: CalendarAccountClaim, *, work: CalendarWork,
        authorize: CalendarAuthorization, stage: CalendarAccountStage,
        actor: str, actor_user_id: uuid.UUID | None,
    ) -> CalendarAccountApply:
        session = calendar_new_session(self.store.sessions)
        accepted: CalendarAccountApply | None = None
        failure: BaseException | None = None
        commit_started = False
        prepared: CalendarAccountApply | None = None
        try:
            await calendar_sql_budget(session, work)
            await reload_auth_root_for_transaction(session)
            await calendar_sql_budget(session, work)
            await authorize(session, False)
            await calendar_sql_budget(session, work)
            account = await session.scalar(select(ICloudCalendarAccount)
                .where(ICloudCalendarAccount.id == claim.account_id).with_for_update())
            await calendar_sql_budget(session, work)
            run = await session.scalar(select(ICloudCalendarSyncRun)
                .where(ICloudCalendarSyncRun.id == claim.run_id))
            await calendar_sql_budget(session, work)
            now = await calendar_database_clock(session)
            if account is None or run is None:
                raise CalendarOwnershipLost("Calendar account/run disappeared before apply.")
            context = CalendarRunContext.stored(run.run_context)
            if (context.attempt is None or context.attempt.token != claim.attempt_token
                or context.attempt.lease_until <= now or account.sync_generation != claim.generation
                or account.sync_lease_token != claim.account_token or account.sync_lease_until is None
                or account.sync_lease_until <= now or not account.is_active
                or account.encrypted_session_bundle != claim.encrypted_session_bundle):
                raise CalendarOwnershipLost("Calendar account or attempt changed while fetching. Its old result cannot apply.")
            await calendar_sql_budget(session, work)
            config = await get_runtime_config_for_session(session)
            await calendar_sql_budget(session, work)
            setting = await session.get(SystemSetting, "site_timezone")
            revision = CalendarSettingRevision(setting is not None, setting.updated_at if setting else None)
            if config.site_timezone != context.site_timezone or revision != claim.setting_revision:
                raise CalendarOwnershipLost("Calendar policy changed while fetching. Read the run and explicitly resume with current policy.")
            work.check()
            prepared = await stage(session, account, context)
            checkpoint = prepared.checkpoint
            if (checkpoint.account_id != claim.account_id or checkpoint.attempt_token != claim.attempt_token
                or checkpoint.status != "ok"):
                raise ValueError("Calendar account stage returned an invalid accepted checkpoint.")
            # Domain/visitor/intake locks precede final workflow NOWAIT locks;
            # only then may this account stage acquire its CalendarRun lock.
            await calendar_sql_budget(session, work)
            authority_until = await authorize(session, True)
            await calendar_sql_budget(session, work)
            run = await session.scalar(select(ICloudCalendarSyncRun)
                .where(ICloudCalendarSyncRun.id == claim.run_id).with_for_update()
                .execution_options(populate_existing=True))
            if run is None:
                raise CalendarOwnershipLost("Calendar run disappeared before its checkpoint.")
            current_context = CalendarRunContext.stored(run.run_context)
            if (current_context.attempt is None or current_context.attempt.token != claim.attempt_token
                or current_context.account_ids != context.account_ids
                or current_context.coverage != context.coverage
                or current_context.site_timezone != context.site_timezone):
                raise CalendarOwnershipLost("Calendar run attempt changed before its checkpoint.")
            stage_calendar_checkpoint(run, current_context, checkpoint)
            account.status, account.last_error, account.last_sync_status = "connected", None, "ok"
            account.last_sync_at, account.last_sync_summary = now, checkpoint.model_dump(mode="json")
            await calendar_sql_budget(session, work)
            await write_audit_log(session, category=TELEMETRY_CATEGORY_INTEGRATIONS,
                action="icloud_calendar.sync_account", actor=actor, actor_user_id=actor_user_id,
                target_entity="ICloudCalendarSyncRun", target_id=run.id,
                target_label="iCloud Calendar account checkpoint",
                metadata=checkpoint.model_dump(mode="json"))
            await assert_calendar_before_commit(session, claim.run_id, claim.attempt_token,
                work=work, claim=claim, authority_until=authority_until)
            commit_started = True
            await session.commit()
            accepted = prepared
        except Exception as exc:
            failure = exc
            if commit_started:
                # Fresh Account→Run locks wait for the original transaction to
                # settle; absence is never trusted from an unlocked receipt read.
                try:
                    await drain_owned(session.rollback())
                except (SQLAlchemyError, OSError, RuntimeError) as settlement_error:
                    logger.warning("Calendar original commit settlement needs fresh locked resolution: %s", type(settlement_error).__name__)
                try:
                    durable = await self._resolve_account(claim)
                except Exception as resolution_error:
                    raise CalendarOutcomeUnknown(
                        f"Calendar account outcome is unknown for run {claim.run_id}. Read and explicitly recover that same run; do not start a replacement attempt.") from resolution_error
                if durable is not None and prepared is not None:
                    accepted = CalendarAccountApply(durable, prepared.events)
                else:
                    raise
            else:
                await drain_owned(session.rollback())
                raise
        finally:
            try:
                await calendar_close(session)
            except Exception:
                if accepted is None and failure is None:
                    raise
                logger.warning("Calendar session cleanup failed; preserving established account outcome")
        assert accepted is not None
        return accepted

    async def _resolve_account(self, claim: CalendarAccountClaim) -> CalendarAccountCheckpoint | None:
        session = calendar_new_session(self.store.sessions)
        durable: CalendarAccountCheckpoint | None = None
        try:
            await calendar_sql_budget(session, None)
            await session.scalar(select(ICloudCalendarAccount.id)
                .where(ICloudCalendarAccount.id == claim.account_id).with_for_update())
            await calendar_sql_budget(session, None)
            run = await session.scalar(select(ICloudCalendarSyncRun)
                .where(ICloudCalendarSyncRun.id == claim.run_id).with_for_update())
            if run is None:
                return None
            durable = next((item for item in checked_calendar_checkpoints(run.account_results)
                if item.account_id == claim.account_id and item.attempt_token == claim.attempt_token
                and item.status == "ok"), None)
        finally:
            try:
                await calendar_close(session)
            except Exception:
                if durable is None:
                    raise
                logger.warning("Calendar outcome lookup cleanup failed after its durable receipt was observed")
        return durable
