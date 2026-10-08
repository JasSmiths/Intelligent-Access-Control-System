"""Short fenced session reads and failed-account checkpoints; no vendor I/O."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Literal

from sqlalchemy import select

from app.core.crypto import decrypt_secret
from app.models import ICloudCalendarAccount, ICloudCalendarSyncRun, SystemSetting
from app.modules.icloud_calendar.contracts import CalendarWork, ICloudCalendarReauthRequired
from app.services.auth_root import reload_auth_root_for_transaction
from app.services.calendar_sync_contracts import (
    CalendarAccountCheckpoint,
    CalendarAccountClaim,
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
    calendar_session,
    calendar_sql_budget,
    stage_calendar_checkpoint,
)
from app.services.settings import get_runtime_config_for_session


@dataclass(frozen=True)
class CalendarDetachedSession:
    bundle: dict[str, Any] = field(repr=False)
    lease_deadline: float

    def before_dispatch(self, work: CalendarWork) -> None:
        work.check()
        if monotonic() >= self.lease_deadline:
            raise CalendarOwnershipLost("Calendar source ownership expired during session cleanup. No provider request started.")


class CalendarSyncSessions:
    def __init__(self, store: CalendarSyncStore) -> None:
        self.store = store

    async def read_session(
        self, claim: CalendarAccountClaim, *, authorize: CalendarAuthorization, work: CalendarWork,
    ) -> CalendarDetachedSession:
        async with calendar_session(self.store.sessions) as session:
            await calendar_sql_budget(session, work)
            await reload_auth_root_for_transaction(session)
            await calendar_sql_budget(session, work)
            await authorize(session, False)
            await calendar_sql_budget(session, work)
            account = await session.scalar(select(ICloudCalendarAccount)
                .where(ICloudCalendarAccount.id == claim.account_id).with_for_update())
            await calendar_sql_budget(session, work)
            run = await session.get(ICloudCalendarSyncRun, claim.run_id)
            await calendar_sql_budget(session, work)
            config = await get_runtime_config_for_session(session)
            await calendar_sql_budget(session, work)
            setting = await session.get(SystemSetting, "site_timezone")
            revision = CalendarSettingRevision(setting is not None, setting.updated_at if setting else None)
            await calendar_sql_budget(session, work)
            # Anchor before awaiting the DB sample, so query/close latency cannot
            # manufacture extra provider-start eligibility after this read.
            anchor = monotonic()
            now = await calendar_database_clock(session)
            context = CalendarRunContext.stored(run.run_context) if run else None
            if (account is None or context is None or context.attempt is None
                or context.attempt.token != claim.attempt_token or context.attempt.lease_until <= now
                or account.sync_generation != claim.generation or account.sync_lease_token != claim.account_token
                or account.sync_lease_until is None or account.sync_lease_until <= now
                or not account.is_active or account.encrypted_session_bundle != claim.encrypted_session_bundle
                or context.site_timezone != config.site_timezone or revision != claim.setting_revision):
                raise CalendarOwnershipLost("Calendar account or attempt changed before provider dispatch.")
            try:
                bundle = json.loads(decrypt_secret(claim.encrypted_session_bundle))
            except (ValueError, UnicodeError) as exc:
                raise ICloudCalendarReauthRequired("Stored iCloud session could not be decoded. Reconnect this account.") from exc
            if not isinstance(bundle, dict):
                raise ICloudCalendarReauthRequired("Stored iCloud session is invalid. Reconnect this account.")
            lease_until = min(account.sync_lease_until, context.attempt.lease_until)
            detached = CalendarDetachedSession(bundle, anchor + (lease_until - now).total_seconds())
        detached.before_dispatch(work)
        return detached

    async def record_failure(
        self, claim: CalendarAccountClaim, *,
        status: Literal["requires_reauth", "incomplete", "interrupted", "error"],
        detail: str,
    ) -> CalendarAccountCheckpoint | None:
        # Actual SDK cleanup has finished. This short transaction records no new
        # account effects and never extends the original work eligibility.
        async with calendar_session(self.store.sessions) as session:
            await calendar_sql_budget(session, None)
            account = await session.scalar(select(ICloudCalendarAccount)
                .where(ICloudCalendarAccount.id == claim.account_id).with_for_update())
            await calendar_sql_budget(session, None)
            run = await session.scalar(select(ICloudCalendarSyncRun)
                .where(ICloudCalendarSyncRun.id == claim.run_id).with_for_update())
            if account is None or run is None:
                return None
            context = CalendarRunContext.stored(run.run_context)
            await calendar_sql_budget(session, None)
            now = await calendar_database_clock(session)
            if (context.attempt is None or context.attempt.token != claim.attempt_token
                or context.attempt.lease_until <= now or account.sync_generation != claim.generation
                or account.sync_lease_token != claim.account_token or account.sync_lease_until is None
                or account.sync_lease_until <= now or not account.is_active
                or account.encrypted_session_bundle != claim.encrypted_session_bundle):
                return None
            old = next((item for item in checked_calendar_checkpoints(run.account_results)
                if item.account_id == claim.account_id and item.terminal), None)
            if old is not None:
                return old
            checkpoint = CalendarAccountCheckpoint(account_id=claim.account_id, apple_id=claim.apple_id,
                display_name=claim.display_name, attempt_token=claim.attempt_token, status=status, error=detail)
            stage_calendar_checkpoint(run, context, checkpoint)
            account.last_sync_status, account.last_error = status, detail
            if status == "requires_reauth":
                account.status = "requires_reauth"
            await assert_calendar_before_commit(session, claim.run_id, claim.attempt_token, work=None, claim=claim)
            await session.commit()
            return checkpoint
