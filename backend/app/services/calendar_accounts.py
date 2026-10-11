"""Calendar account transactions, bounded operator removal and canonical public receipts."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.crypto import encrypt_secret
from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.models import AuditLog, ICloudCalendarAccount, ICloudCalendarSyncRun, VisitorPass
from app.models.enums import VisitorPassStatus
from app.modules.icloud_calendar.client import ICLOUD_RECONNECT_REQUIRED_MESSAGE
from app.modules.icloud_calendar.contracts import CalendarWork
from app.services.auth_root import reload_auth_root_for_transaction
from app.services.calendar_sync_contracts import (
    CalendarAccountCheckpoint,
    CalendarRunContext,
    checked_calendar_checkpoints,
)
from app.services.calendar_sync_store import (
    CalendarAuthorization,
    CalendarSyncStore,
    calendar_close,
    calendar_database_clock,
    calendar_new_session,
    calendar_rollback,
    calendar_session,
    calendar_sql_budget,
)
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS, audit_diff, write_audit_log
from app.services.visitor_passes import get_visitor_pass_service

logger = get_logger(__name__)


class CalendarAccountAuthChanged(ValueError):
    """The logical account/session captured before SDK work was replaced."""


class CalendarAccountAuthUnknown(RuntimeError):
    """Read the recorded audit/account outcome before repeating authentication."""


@dataclass(frozen=True)
class CalendarAuthCapture:
    apple_id: str
    account_id: uuid.UUID | None
    generation: int | None


class CalendarAccountStore:
    def __init__(self, store: CalendarSyncStore) -> None:
        self.store = store

    async def capture(self, apple_id: str, *, authorize: CalendarAuthorization,
                      work: CalendarWork) -> CalendarAuthCapture:
        async with calendar_session(self.store.sessions) as session:
            await calendar_sql_budget(session, work)
            await reload_auth_root_for_transaction(session)
            await calendar_sql_budget(session, work)
            await authorize(session, False)
            await calendar_sql_budget(session, work)
            account = await session.scalar(select(ICloudCalendarAccount).where(ICloudCalendarAccount.apple_id == apple_id))
            capture = CalendarAuthCapture(apple_id, account.id if account else None,
                account.sync_generation if account else None)
        work.check()
        return capture

    async def recheck(self, capture: CalendarAuthCapture, *, authorize: CalendarAuthorization,
                      work: CalendarWork) -> None:
        current = await self.capture(capture.apple_id, authorize=authorize, work=work)
        if current != capture:
            raise CalendarAccountAuthChanged("This account changed after Apple setup began. Start a new connection attempt.")

    async def record_handshake(self, capture: CalendarAuthCapture, *, actor: str,
                               actor_user_id: uuid.UUID, authorize: CalendarAuthorization,
                               work: CalendarWork) -> None:
        session = calendar_new_session(self.store.sessions)
        audit_id, accepted, commit_started = uuid.uuid4(), False, False
        failure: Exception | None = None
        try:
            await calendar_sql_budget(session, work)
            await reload_auth_root_for_transaction(session)
            await calendar_sql_budget(session, work)
            account = await session.scalar(select(ICloudCalendarAccount)
                .where(ICloudCalendarAccount.apple_id == capture.apple_id).with_for_update())
            if ((account.id if account else None) != capture.account_id
                or (account.sync_generation if account else None) != capture.generation):
                raise CalendarAccountAuthChanged("This account changed during Apple setup. Start a new connection attempt.")
            await calendar_sql_budget(session, work)
            authority_until = await authorize(session, True)
            await calendar_sql_budget(session, work)
            audit = await write_audit_log(session, category=TELEMETRY_CATEGORY_INTEGRATIONS,
                action="icloud_calendar.auth_2fa_required", actor=actor, actor_user_id=actor_user_id,
                target_entity="ICloudCalendarAccount", target_label=capture.apple_id,
                metadata={"apple_id": capture.apple_id})
            audit.id = audit_id
            await calendar_sql_budget(session, work)
            await session.flush()
            await calendar_sql_budget(session, work)
            with session.no_autoflush:
                now = await calendar_database_clock(session)
            if authority_until is not None and authority_until <= now:
                raise CalendarAccountAuthChanged("Your Admin session expired during Apple setup. Sign in again.")
            work.check()
            if session.new or session.dirty or session.deleted:
                raise RuntimeError("Apple setup audit has a pending write after its final clock sample.")
            commit_started = True
            await session.commit()
            accepted = True
        except Exception as exc:
            failure = exc
            try:
                await drain_owned(session.rollback())
            except Exception:  # noqa: BLE001 - Fresh participating locks resolve the outcome.
                logger.warning("Apple setup audit rollback needs outcome resolution")
            if not commit_started:
                raise
            try:
                accepted = await self._resolve(capture.apple_id, audit_id)
            except Exception as lookup:
                raise CalendarAccountAuthUnknown("Apple setup audit outcome is unknown. Start a new setup only after checking the account state.") from lookup
            if not accepted:
                raise
        finally:
            try:
                await calendar_close(session)
            except Exception:
                if not accepted and failure is None:
                    raise
                logger.warning("Apple setup audit cleanup failed after its primary result")

    async def persist(self, capture: CalendarAuthCapture, bundle: dict[str, Any], *,
                      actor: str, actor_user_id: uuid.UUID, authorize: CalendarAuthorization,
                      work: CalendarWork) -> dict[str, Any]:
        session = calendar_new_session(self.store.sessions)
        prepared: dict[str, Any] | None = None
        accepted: dict[str, Any] | None = None
        commit_started = False
        failure: Exception | None = None
        audit_id = uuid.uuid4()
        try:
            await calendar_sql_budget(session, work)
            await reload_auth_root_for_transaction(session)
            await calendar_sql_budget(session, work)
            await authorize(session, False)
            await calendar_sql_budget(session, work)
            account = await session.scalar(select(ICloudCalendarAccount)
                .where(ICloudCalendarAccount.apple_id == capture.apple_id).with_for_update())
            if ((account.id if account else None) != capture.account_id
                or (account.sync_generation if account else None) != capture.generation):
                raise CalendarAccountAuthChanged("This account changed during Apple setup. Its older result was not saved.")
            before = icloud_account_audit_snapshot(account) if account else {}
            if account is None:
                account = ICloudCalendarAccount(apple_id=capture.apple_id, display_name=capture.apple_id,
                    created_by_user_id=actor_user_id, sync_generation=1)
                session.add(account)
                action = "icloud_calendar.account_connected"
            else:
                account.sync_generation += 1
                account.created_by_user_id = account.created_by_user_id or actor_user_id
                account.display_name = account.display_name or account.apple_id
                action = "icloud_calendar.account_reconnected"
            account.sync_lease_token = account.sync_lease_until = None
            account.status, account.is_active, account.last_error = "connected", True, None
            account.encrypted_session_bundle = encrypt_secret(json.dumps(bundle))
            account.last_auth_at = datetime.now(UTC)
            await calendar_sql_budget(session, work)
            authority_until = await authorize(session, True)
            await calendar_sql_budget(session, work)
            await session.flush()
            await calendar_sql_budget(session, work)
            await session.refresh(account, attribute_names=["updated_at"])
            await calendar_sql_budget(session, work)
            audit = await write_audit_log(session, category=TELEMETRY_CATEGORY_INTEGRATIONS,
                action=action, actor=actor, actor_user_id=actor_user_id,
                target_entity="ICloudCalendarAccount", target_id=account.id, target_label=account.apple_id,
                diff=audit_diff(before, icloud_account_audit_snapshot(account)))
            audit.id = audit_id
            await calendar_sql_budget(session, work)
            await session.flush()
            prepared = serialize_icloud_account(account)
            await calendar_sql_budget(session, work)
            with session.no_autoflush:
                now = await calendar_database_clock(session)
            if authority_until is not None and authority_until <= now:
                raise CalendarAccountAuthChanged("Your Admin session expired before account acceptance. Sign in and start again.")
            work.check()
            if session.new or session.dirty or session.deleted:
                raise RuntimeError("Calendar account acceptance has a pending write after its final clock sample.")
            commit_started = True
            await session.commit()
            accepted = prepared
        except Exception as exc:
            failure = exc
            try:
                await drain_owned(session.rollback())
            except Exception:  # noqa: BLE001 - A fresh participating root/account lock resolves the server outcome.
                logger.warning("Calendar account rollback needs fresh outcome resolution")
            if commit_started:
                try:
                    positive = await self._resolve(capture.apple_id, audit_id)
                except Exception as lookup:
                    raise CalendarAccountAuthUnknown(
                        f"Apple account save outcome is unknown (audit {audit_id}). Read this account before reconnecting again.") from lookup
                if positive:
                    accepted = prepared
                else:
                    raise
            else:
                raise
        finally:
            try:
                await calendar_close(session)
            except Exception:
                if accepted is None and failure is None:
                    raise
                logger.warning("Calendar auth account cleanup failed after its result was established")
        assert accepted is not None
        return accepted

    async def _resolve(self, apple_id: str, audit_id: uuid.UUID) -> bool:
        # Original commit has returned/failed. The fresh participating root and
        # account locks prove its transaction has settled before an absent audit
        # can be treated as rollback. Unknown lookup never permits blind replay.
        positive = False
        session = calendar_new_session(self.store.sessions)
        try:
            await calendar_sql_budget(session, None)
            await reload_auth_root_for_transaction(session)
            await calendar_sql_budget(session, None)
            await session.scalar(select(ICloudCalendarAccount.id)
                .where(ICloudCalendarAccount.apple_id == apple_id).with_for_update())
            await calendar_sql_budget(session, None)
            positive = await session.scalar(select(AuditLog.id).where(AuditLog.id == audit_id)) is not None
        finally:
            try:
                await calendar_close(session)
            except Exception:
                if not positive:
                    raise
                logger.warning("Calendar auth outcome lookup cleanup failed after positive audit")
        return positive

    async def remove(self, account_id: uuid.UUID, *, actor: str, actor_user_id: uuid.UUID,
                     authorize: CalendarAuthorization, work: CalendarWork) -> dict[str, Any]:
        session = calendar_new_session(self.store.sessions)
        prepared: dict[str, Any] | None = None
        accepted: dict[str, Any] | None = None
        commit_started = False
        rolled_back = False
        failure: Exception | None = None
        audit_id, apple_id = uuid.uuid4(), ""
        try:
            await calendar_sql_budget(session, work)
            await reload_auth_root_for_transaction(session)
            await calendar_sql_budget(session, work)
            await authorize(session, False)
            await calendar_sql_budget(session, work)
            account = await session.scalar(select(ICloudCalendarAccount)
                .where(ICloudCalendarAccount.id == account_id).with_for_update())
            if account is None or not account.is_active:
                raise LookupError("iCloud Calendar account not found.")
            apple_id = account.apple_id
            before = icloud_account_audit_snapshot(account)
            # This operator-directed removal deliberately covers all eligible
            # source-prefixed passes, including malformed/out-of-horizon data.
            # It is independent of conservative sync-absence coverage policy.
            visitor_owner, cursor, cancelled = get_visitor_pass_service(), None, 0
            while True:
                await calendar_sql_budget(session, work)
                query = select(VisitorPass).where(
                    VisitorPass.creation_source == "icloud_calendar",
                    VisitorPass.source_reference.startswith(f"icloud:{account_id}:", autoescape=True),
                    VisitorPass.status.in_([VisitorPassStatus.ACTIVE, VisitorPassStatus.SCHEDULED]))
                if cursor is not None:
                    query = query.where(VisitorPass.id > cursor)
                rows = list((await session.scalars(query.order_by(VisitorPass.id).limit(100).with_for_update())).all())
                if not rows:
                    break
                cursor = rows[-1].id
                for visitor_pass in rows:
                    await calendar_sql_budget(session, work)
                    await visitor_owner.cancel_pass(session, visitor_pass, actor=actor,
                        actor_user_id=actor_user_id, reason="iCloud Calendar account removed")
                    cancelled += 1
                await calendar_sql_budget(session, work)
                await session.flush()
            account.sync_generation += 1
            account.sync_lease_token = account.sync_lease_until = None
            account.is_active, account.status = False, "removed"
            account.encrypted_session_bundle, account.last_error = None, None
            await calendar_sql_budget(session, work)
            authority_until = await authorize(session, True)
            await calendar_sql_budget(session, work)
            await session.flush()
            await calendar_sql_budget(session, work)
            await session.refresh(account, attribute_names=["updated_at"])
            await calendar_sql_budget(session, work)
            audit = await write_audit_log(session, category=TELEMETRY_CATEGORY_INTEGRATIONS,
                action="icloud_calendar.account_removed", actor=actor, actor_user_id=actor_user_id,
                target_entity="ICloudCalendarAccount", target_id=account.id, target_label=account.apple_id,
                diff=audit_diff(before, icloud_account_audit_snapshot(account)),
                metadata={"passes_cancelled": cancelled})
            audit.id = audit_id
            await calendar_sql_budget(session, work)
            await session.flush()
            prepared = {"account": serialize_icloud_account(account), "passes_cancelled": cancelled}
            await calendar_sql_budget(session, work)
            with session.no_autoflush:
                now = await calendar_database_clock(session)
            if authority_until is not None and authority_until <= now:
                raise CalendarAccountAuthChanged("Your Admin session expired before account removal. Sign in again.")
            work.check()
            if session.new or session.dirty or session.deleted:
                raise RuntimeError("Calendar removal has a pending write after its final clock sample.")
            commit_started = True
            await session.commit()
            accepted = prepared
        except Exception as exc:
            failure = exc
            rolled_back = await calendar_rollback(session)
            if not commit_started:
                if not rolled_back:
                    raise CalendarAccountAuthUnknown("Calendar removal transaction settlement is unresolved. Read the original account before retrying.") from exc
                raise
            try:
                positive = await self._resolve(apple_id, audit_id)
            except Exception as lookup:
                raise CalendarAccountAuthUnknown("Calendar removal outcome is unknown. Read this account and its passes before repeating removal.") from lookup
            if not positive:
                raise
            accepted = prepared
        finally:
            try:
                await calendar_close(session)
            except Exception:  # noqa: BLE001 - Preserve accepted/settled outcome and classify unresolved cleanup.
                if accepted is None and not rolled_back:
                    raise CalendarAccountAuthUnknown("Calendar removal cleanup is unresolved. Read the original account before retrying.") from failure
                logger.warning("Calendar removal cleanup failed after its settled primary result")
        assert accepted is not None
        return accepted


def serialize_icloud_account(account: ICloudCalendarAccount) -> dict[str, Any]:
    status, last_error = _serialized_icloud_account_status(account)
    return {
        "id": str(account.id),
        "apple_id": account.apple_id,
        "display_name": account.display_name or account.apple_id,
        "status": status,
        "is_active": account.is_active,
        "last_auth_at": _iso(account.last_auth_at),
        "last_sync_at": _iso(account.last_sync_at),
        "last_sync_status": account.last_sync_status,
        "last_sync_summary": {key: value for key, value in account.last_sync_summary.items() if key != "attempt_token"}
        if account.last_sync_summary else None,
        "last_error": last_error,
        "created_by_user_id": str(account.created_by_user_id)
        if account.created_by_user_id
        else None,
        "created_at": _iso(account.created_at),
        "updated_at": _iso(account.updated_at),
    }

def icloud_account_audit_snapshot(account: ICloudCalendarAccount) -> dict[str, Any]:
    return {
        "id": str(account.id) if account.id else None,
        "apple_id": account.apple_id,
        "display_name": account.display_name,
        "status": account.status,
        "is_active": account.is_active,
        "last_auth_at": _iso(account.last_auth_at),
        "last_sync_at": _iso(account.last_sync_at),
        "last_sync_status": account.last_sync_status,
        "last_sync_summary": account.last_sync_summary,
        "last_error": account.last_error,
        "created_by_user_id": str(account.created_by_user_id)
        if account.created_by_user_id
        else None,
    }

def _serialized_icloud_account_status(account: ICloudCalendarAccount) -> tuple[str, str | None]:
    if account.status == "error" and _looks_like_stored_session_password_fallback(
        account.last_error
    ):
        return "requires_reauth", ICLOUD_RECONNECT_REQUIRED_MESSAGE
    return account.status, account.last_error

def _looks_like_stored_session_password_fallback(value: str | None) -> bool:
    return "no password set" in str(value or "").strip().lower()

def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None



def serialize_icloud_sync_run(run: ICloudCalendarSyncRun) -> dict[str, Any]:
    context = CalendarRunContext.stored(run.run_context) if run.run_context else None
    checkpoints = checked_calendar_checkpoints(run.account_results) if context else ()
    return {
        "id": str(run.id),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "status": run.status,
        "trigger_source": run.trigger_source,
        "triggered_by_user_id": str(run.triggered_by_user_id) if run.triggered_by_user_id else None,
        "account_count": run.account_count,
        "events_scanned": run.events_scanned,
        "events_matched": run.events_matched,
        "passes_created": run.passes_created,
        "passes_updated": run.passes_updated,
        "passes_cancelled": run.passes_cancelled,
        "passes_skipped": run.passes_skipped,
        "account_results": [{key: value for key, value in item.items() if key != "attempt_token"}
                            for item in (run.account_results or [])],
        "error": run.error,
        "recovery": _serialize_calendar_recovery(context, checkpoints),
    }



def _serialize_calendar_recovery(context: CalendarRunContext | None,
                                 checkpoints: tuple[CalendarAccountCheckpoint, ...]) -> dict[str, Any] | None:
    if context is None:
        return None
    completed = {item.account_id for item in checkpoints if item.terminal}
    busy = bool(context.attempt and context.attempt.lease_until > datetime.now(UTC))
    return {"original_origin": context.origin,
        "original_actor_user_id": str(context.original_actor_user_id) if context.original_actor_user_id else None,
        "workflow_operation_id": context.execution["operation_id"] if context.execution else None,
        "account_ids": [str(identity) for identity in context.account_ids],
        "completed_account_ids": [str(identity) for identity in context.account_ids if identity in completed],
        "remaining_account_ids": [str(identity) for identity in context.account_ids if identity not in completed],
        "coverage": context.model_dump(mode="json")["coverage"], "busy": busy,
        "can_resume": not busy and any(identity not in completed for identity in context.account_ids),
        "recovery_actor_user_id": str(context.recovery_actor_user_id) if context.recovery_actor_user_id else None}


def serialize_calendar_checkpoint_receipt(run_id: uuid.UUID, context: CalendarRunContext | None,
    checkpoints: tuple[CalendarAccountCheckpoint, ...], *, uncertain: bool) -> dict[str, Any]:
    """Known detached checkpoints remain usable if observational metadata fails."""
    result: dict[str, Any] = {"id": str(run_id), "started_at": None, "finished_at": None,
        "status": "unknown" if uncertain else "partial", "trigger_source": context.origin if context else None,
        "triggered_by_user_id": str(context.original_actor_user_id) if context and context.original_actor_user_id else None,
        "account_count": len(context.account_ids) if context else len(checkpoints),
        "account_results": [item.model_dump(mode="json", exclude={"attempt_token"}) for item in checkpoints],
        "error": "The account receipts are retained. Read this known run before any explicit recovery.",
        "recovery": _serialize_calendar_recovery(context, checkpoints)}
    for name in ("events_scanned", "events_matched", "passes_created", "passes_updated", "passes_cancelled", "passes_skipped"):
        result[name] = sum(getattr(item, name) for item in checkpoints if item.status == "ok")
    if result["recovery"] is not None:
        # Admission's detached lease is not the latest renewed/final context.
        # Only an exact durable reread can offer a fresh confirmed recovery.
        result["recovery"]["can_resume"] = False
    return result
