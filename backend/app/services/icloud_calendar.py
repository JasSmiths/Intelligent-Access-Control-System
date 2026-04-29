import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import ICloudCalendarAccount, ICloudCalendarSyncRun, User, VisitorPass
from app.models.enums import VisitorPassStatus
from app.modules.icloud_calendar.client import (
    ICloudAuthSession,
    ICloudCalendarClient,
    ICloudCalendarClientError,
    ICloudCalendarEvent,
    ICloudCalendarReauthRequired,
)
from app.services.event_bus import event_bus
from app.services.settings import get_runtime_config
from app.services.telemetry import (
    TELEMETRY_CATEGORY_INTEGRATIONS,
    actor_from_user,
    audit_diff,
    write_audit_log,
)
from app.services.visitor_passes import (
    DEFAULT_WINDOW_MINUTES,
    VISITOR_PASS_ACTIVE_STATUSES,
    VisitorPassError,
    get_visitor_pass_service,
    serialize_visitor_pass,
    visitor_pass_audit_snapshot,
)

logger = get_logger(__name__)

ICLOUD_CALENDAR_SOURCE = "icloud_calendar"
ICLOUD_OPEN_GATE_MARKER = "Open Gate"
ICLOUD_SYNC_LOOKAHEAD_DAYS = 14
ICLOUD_AUTH_HANDSHAKE_TTL_SECONDS = 10 * 60


class ICloudCalendarError(ValueError):
    """Raised for user-actionable iCloud Calendar integration errors."""


@dataclass
class PendingICloudAuth:
    handshake_id: str
    apple_id: str
    auth_session: ICloudAuthSession
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def expired(self, now: datetime | None = None) -> bool:
        checked = now or datetime.now(tz=UTC)
        return (checked - self.created_at).total_seconds() > ICLOUD_AUTH_HANDSHAKE_TTL_SECONDS


class ICloudCalendarService:
    def __init__(self, client: ICloudCalendarClient | None = None) -> None:
        self._client = client or ICloudCalendarClient()
        self._pending: dict[str, PendingICloudAuth] = {}
        self._pending_lock = asyncio.Lock()

    async def list_accounts(self, session: AsyncSession) -> list[ICloudCalendarAccount]:
        return list(
            (
                await session.scalars(
                    select(ICloudCalendarAccount)
                    .where(ICloudCalendarAccount.is_active.is_(True))
                    .order_by(ICloudCalendarAccount.apple_id)
                )
            ).all()
        )

    async def recent_sync_runs(self, session: AsyncSession, limit: int = 5) -> list[ICloudCalendarSyncRun]:
        return list(
            (
                await session.scalars(
                    select(ICloudCalendarSyncRun)
                    .order_by(ICloudCalendarSyncRun.started_at.desc())
                    .limit(max(1, min(limit, 25)))
                )
            ).all()
        )

    async def start_auth(
        self,
        session: AsyncSession,
        *,
        apple_id: str,
        password: str,
        user: User,
    ) -> dict[str, Any]:
        normalized_apple_id = _clean_apple_id(apple_id)
        if not password:
            raise ICloudCalendarError("Apple ID password is required to start iCloud Calendar setup.")

        try:
            auth_session = await asyncio.to_thread(self._client.start_auth, normalized_apple_id, password)
        except ICloudCalendarClientError as exc:
            raise ICloudCalendarError(str(exc)) from exc
        try:
            if self._client.requires_security_key(auth_session):
                raise ICloudCalendarError(
                    "This Apple account is asking for a security key. IACS currently supports code verification only."
                )
            if self._client.requires_2fa(auth_session):
                await asyncio.to_thread(self._client.request_2fa_code, auth_session)
                handshake_id = uuid.uuid4().hex
                async with self._pending_lock:
                    self._pending[handshake_id] = PendingICloudAuth(
                        handshake_id=handshake_id,
                        apple_id=normalized_apple_id,
                        auth_session=auth_session,
                    )
                    self._prune_expired_pending_locked()
                await write_audit_log(
                    session,
                    category=TELEMETRY_CATEGORY_INTEGRATIONS,
                    action="icloud_calendar.auth_2fa_required",
                    actor=actor_from_user(user),
                    actor_user_id=user.id,
                    target_entity="ICloudCalendarAccount",
                    target_label=normalized_apple_id,
                    metadata={"apple_id": normalized_apple_id},
                )
                return {
                    "status": "requires_2fa",
                    "requires_2fa": True,
                    "handshake_id": handshake_id,
                    "apple_id": normalized_apple_id,
                    "detail": "Enter the six-digit Apple verification code to finish connecting this iCloud Calendar account.",
                }
            if self._client.requires_legacy_2sa(auth_session):
                raise ICloudCalendarError(
                    "This Apple account is using the older two-step verification flow. IACS currently supports six-digit code verification only."
                )

            account = await self._store_authenticated_account(session, auth_session, user=user)
            self._client.cleanup_auth_session(auth_session)
            await self._publish_accounts_changed(session)
            return {
                "status": "connected",
                "requires_2fa": False,
                "account": serialize_icloud_account(account),
            }
        except Exception:
            self._client.cleanup_auth_session(auth_session)
            raise

    async def verify_auth(
        self,
        session: AsyncSession,
        *,
        handshake_id: str,
        code: str,
        user: User,
    ) -> dict[str, Any]:
        pending = await self._get_pending(handshake_id)
        if not pending:
            raise ICloudCalendarError("The iCloud verification session has expired. Start account setup again.")
        auth_session = pending.auth_session
        if not re.fullmatch(r"\d{6}", str(code or "").strip()):
            raise ICloudCalendarError("Enter the six-digit Apple verification code.")
        try:
            verified = await asyncio.to_thread(self._client.validate_2fa_code, auth_session, str(code).strip())
        except ICloudCalendarClientError as exc:
            raise ICloudCalendarError(str(exc)) from exc
        if not verified:
            raise ICloudCalendarError("Apple rejected that verification code. Check the code and try again.")
        try:
            trusted = await asyncio.to_thread(self._client.trust_session, auth_session)
        except ICloudCalendarClientError as exc:
            await self._drop_pending(handshake_id, cleanup=True)
            raise ICloudCalendarError(str(exc)) from exc
        if not trusted:
            await self._drop_pending(handshake_id, cleanup=True)
            raise ICloudCalendarError("Apple verified the code but did not trust the session. Try setup again.")
        account = await self._store_authenticated_account(session, auth_session, user=user)
        await self._drop_pending(handshake_id, cleanup=True)
        await self._publish_accounts_changed(session)
        return {"status": "connected", "account": serialize_icloud_account(account)}

    async def remove_account(
        self,
        session: AsyncSession,
        account: ICloudCalendarAccount,
        *,
        user: User,
    ) -> ICloudCalendarAccount:
        before = icloud_account_audit_snapshot(account)
        account.is_active = False
        account.status = "removed"
        account.encrypted_session_bundle = None
        account.last_error = None
        await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            action="icloud_calendar.account_removed",
            actor=actor_from_user(user),
            actor_user_id=user.id,
            target_entity="ICloudCalendarAccount",
            target_id=account.id,
            target_label=account.apple_id,
            diff=audit_diff(before, icloud_account_audit_snapshot(account)),
        )
        cancelled_payloads = await self._cancel_future_source_passes_for_account(
            session,
            account,
            actor=actor_from_user(user),
            actor_user_id=user.id,
            reason="iCloud Calendar account removed",
        )
        await session.flush()
        await self._publish_accounts_changed(session)
        for payload in cancelled_payloads:
            await event_bus.publish("visitor_pass.cancelled", {"visitor_pass": payload, "source": ICLOUD_CALENDAR_SOURCE})
        return account

    async def get_account(self, session: AsyncSession, account_id: uuid.UUID) -> ICloudCalendarAccount | None:
        return await session.scalar(
            select(ICloudCalendarAccount).where(
                ICloudCalendarAccount.id == account_id,
                ICloudCalendarAccount.is_active.is_(True),
            )
        )

    async def sync_all(
        self,
        *,
        trigger_source: str = "ui",
        triggered_by_user_id: uuid.UUID | str | None = None,
        actor: str = "System",
    ) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            run = await self._create_sync_run(
                session,
                trigger_source=trigger_source,
                triggered_by_user_id=triggered_by_user_id,
            )
            try:
                result, visitor_events = await self._sync_all_in_session(session, run=run, actor=actor, actor_user_id=triggered_by_user_id)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                async with AsyncSessionLocal() as failure_session:
                    run = await self._create_sync_run(
                        failure_session,
                        trigger_source=trigger_source,
                        triggered_by_user_id=triggered_by_user_id,
                    )
                    run.status = "error"
                    run.finished_at = datetime.now(tz=UTC)
                    run.error = str(exc)
                    await failure_session.commit()
                raise

        await event_bus.publish("icloud_calendar.sync_completed", {"sync": result})
        for event_type, payload in visitor_events:
            await event_bus.publish(event_type, payload)
        return result

    async def _sync_all_in_session(
        self,
        session: AsyncSession,
        *,
        run: ICloudCalendarSyncRun,
        actor: str,
        actor_user_id: uuid.UUID | str | None,
    ) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
        accounts = await self.list_accounts(session)
        config = await get_runtime_config()
        starts_at, ends_at = _sync_range(config.site_timezone)
        visitor_service = get_visitor_pass_service()
        visitor_events: list[tuple[str, dict[str, Any]]] = []
        account_results: list[dict[str, Any]] = []

        run.account_count = len(accounts)
        for account in accounts:
            account_result = _empty_account_sync_result(account)
            seen_source_references: set[str] = set()
            try:
                if not account.encrypted_session_bundle:
                    raise ICloudCalendarReauthRequired("Reconnect this account before syncing.")
                events = await asyncio.to_thread(
                    self._client.fetch_events,
                    apple_id=account.apple_id,
                    session_bundle=_decrypt_session_bundle(account.encrypted_session_bundle),
                    starts_at=starts_at,
                    ends_at=ends_at,
                )
                account_result["events_scanned"] = len(events)
                for event in events:
                    if not event_contains_open_gate(event):
                        continue
                    account_result["events_matched"] += 1
                    source_reference = source_reference_for_event(account.id, event)
                    seen_source_references.add(source_reference)
                    try:
                        changed, event_type, payload = await self._upsert_event_pass(
                            session,
                            visitor_service,
                            account=account,
                            event=event,
                            source_reference=source_reference,
                            actor=actor,
                            actor_user_id=actor_user_id,
                        )
                    except VisitorPassError as exc:
                        account_result["passes_skipped"] += 1
                        account_result.setdefault("skips", []).append({"event": event.title, "reason": str(exc)})
                        continue
                    if changed == "created":
                        account_result["passes_created"] += 1
                    elif changed == "updated":
                        account_result["passes_updated"] += 1
                    else:
                        account_result["passes_skipped"] += 1
                    if event_type and payload:
                        visitor_events.append((event_type, payload))

                cancelled = await self._cancel_missing_calendar_passes(
                    session,
                    account=account,
                    seen_source_references=seen_source_references,
                    actor=actor,
                    actor_user_id=actor_user_id,
                )
                account_result["passes_cancelled"] += len(cancelled)
                for payload in cancelled:
                    visitor_events.append(
                        ("visitor_pass.cancelled", {"visitor_pass": payload, "source": ICLOUD_CALENDAR_SOURCE})
                    )
                account.status = "connected"
                account.last_error = None
                account.last_sync_status = "ok"
            except ICloudCalendarReauthRequired as exc:
                account.status = "requires_reauth"
                account.last_error = str(exc)
                account.last_sync_status = "error"
                account_result["status"] = "requires_reauth"
                account_result["error"] = str(exc)
            except Exception as exc:
                account.status = "error"
                account.last_error = str(exc) or "Unable to sync iCloud Calendar."
                account.last_sync_status = "error"
                account_result["status"] = "error"
                account_result["error"] = account.last_error
            finally:
                account.last_sync_at = datetime.now(tz=UTC)
                account.last_sync_summary = account_result
                account_results.append(account_result)

        totals = _sync_totals(account_results)
        run.events_scanned = totals["events_scanned"]
        run.events_matched = totals["events_matched"]
        run.passes_created = totals["passes_created"]
        run.passes_updated = totals["passes_updated"]
        run.passes_cancelled = totals["passes_cancelled"]
        run.passes_skipped = totals["passes_skipped"]
        run.account_results = account_results
        run.status = "ok" if all(row.get("status") == "ok" for row in account_results) else "partial"
        run.finished_at = datetime.now(tz=UTC)

        await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            action="icloud_calendar.sync",
            actor=actor,
            actor_user_id=actor_user_id,
            target_entity="ICloudCalendarSyncRun",
            target_id=run.id,
            target_label="iCloud Calendar sync",
            metadata=serialize_icloud_sync_run(run),
        )
        return serialize_icloud_sync_run(run), visitor_events

    async def _upsert_event_pass(
        self,
        session: AsyncSession,
        visitor_service,
        *,
        account: ICloudCalendarAccount,
        event: ICloudCalendarEvent,
        source_reference: str,
        actor: str,
        actor_user_id: uuid.UUID | str | None,
    ) -> tuple[str, str | None, dict[str, Any] | None]:
        existing = await session.scalar(
            select(VisitorPass)
            .where(VisitorPass.source_reference == source_reference)
            .with_for_update()
            .limit(1)
        )
        valid_from, valid_until = visitor_window_for_event(event)
        metadata = source_metadata_for_event(account, event)
        if existing is None:
            visitor_pass = await visitor_service.create_pass(
                session,
                visitor_name=event.title,
                expected_time=event.starts_at,
                window_minutes=DEFAULT_WINDOW_MINUTES,
                valid_from=valid_from,
                valid_until=valid_until,
                source=ICLOUD_CALENDAR_SOURCE,
                source_reference=source_reference,
                source_metadata=metadata,
                created_by_user_id=actor_user_id,
                actor=actor,
            )
            await session.flush()
            return "created", "visitor_pass.created", {
                "visitor_pass": serialize_visitor_pass(visitor_pass),
                "source": ICLOUD_CALENDAR_SOURCE,
            }

        if not calendar_pass_can_be_reconciled(existing):
            return "skipped", None, None

        before = visitor_pass_audit_snapshot(existing)
        await visitor_service.update_pass(
            session,
            existing,
            visitor_name=event.title,
            expected_time=event.starts_at,
            window_minutes=DEFAULT_WINDOW_MINUTES,
            valid_from=valid_from,
            valid_until=valid_until,
            source_metadata=metadata,
            actor=actor,
            actor_user_id=actor_user_id,
        )
        await session.flush()
        if before == visitor_pass_audit_snapshot(existing):
            return "skipped", None, None
        return "updated", "visitor_pass.updated", {
            "visitor_pass": serialize_visitor_pass(existing),
            "source": ICLOUD_CALENDAR_SOURCE,
        }

    async def _cancel_missing_calendar_passes(
        self,
        session: AsyncSession,
        *,
        account: ICloudCalendarAccount,
        seen_source_references: set[str],
        actor: str,
        actor_user_id: uuid.UUID | str | None,
    ) -> list[dict[str, Any]]:
        prefix = f"icloud:{account.id}:"
        rows = (
            await session.scalars(
                select(VisitorPass)
                .where(
                    VisitorPass.creation_source == ICLOUD_CALENDAR_SOURCE,
                    VisitorPass.source_reference.like(f"{prefix}%"),
                    VisitorPass.status.in_(VISITOR_PASS_ACTIVE_STATUSES),
                )
                .with_for_update()
            )
        ).all()
        payloads: list[dict[str, Any]] = []
        visitor_service = get_visitor_pass_service()
        for visitor_pass in rows:
            if visitor_pass.source_reference in seen_source_references:
                continue
            await visitor_service.cancel_pass(
                session,
                visitor_pass,
                actor=actor,
                actor_user_id=actor_user_id,
                reason="iCloud Calendar event no longer contains Open Gate or is no longer returned by sync.",
            )
            await session.flush()
            payloads.append(serialize_visitor_pass(visitor_pass))
        return payloads

    async def _cancel_future_source_passes_for_account(
        self,
        session: AsyncSession,
        account: ICloudCalendarAccount,
        *,
        actor: str,
        actor_user_id: uuid.UUID | str | None,
        reason: str,
    ) -> list[dict[str, Any]]:
        prefix = f"icloud:{account.id}:"
        rows = (
            await session.scalars(
                select(VisitorPass)
                .where(
                    VisitorPass.creation_source == ICLOUD_CALENDAR_SOURCE,
                    VisitorPass.source_reference.like(f"{prefix}%"),
                    VisitorPass.status.in_(VISITOR_PASS_ACTIVE_STATUSES),
                )
                .with_for_update()
            )
        ).all()
        visitor_service = get_visitor_pass_service()
        payloads: list[dict[str, Any]] = []
        for visitor_pass in rows:
            await visitor_service.cancel_pass(
                session,
                visitor_pass,
                actor=actor,
                actor_user_id=actor_user_id,
                reason=reason,
            )
            payloads.append(serialize_visitor_pass(visitor_pass))
        return payloads

    async def _store_authenticated_account(
        self,
        session: AsyncSession,
        auth_session: ICloudAuthSession,
        *,
        user: User,
    ) -> ICloudCalendarAccount:
        encrypted_bundle = _encrypt_session_bundle(self._client.session_bundle(auth_session))
        existing = await session.scalar(
            select(ICloudCalendarAccount).where(ICloudCalendarAccount.apple_id == auth_session.apple_id)
        )
        now = datetime.now(tz=UTC)
        if existing:
            before = icloud_account_audit_snapshot(existing)
            existing.display_name = existing.display_name or auth_session.apple_id
            existing.status = "connected"
            existing.is_active = True
            existing.encrypted_session_bundle = encrypted_bundle
            existing.last_auth_at = now
            existing.last_error = None
            existing.created_by_user_id = existing.created_by_user_id or user.id
            account = existing
            action = "icloud_calendar.account_reconnected"
            diff_before = before
        else:
            account = ICloudCalendarAccount(
                apple_id=auth_session.apple_id,
                display_name=auth_session.apple_id,
                status="connected",
                is_active=True,
                encrypted_session_bundle=encrypted_bundle,
                last_auth_at=now,
                created_by_user_id=user.id,
            )
            session.add(account)
            action = "icloud_calendar.account_connected"
            diff_before = {}
        await session.flush()
        await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            action=action,
            actor=actor_from_user(user),
            actor_user_id=user.id,
            target_entity="ICloudCalendarAccount",
            target_id=account.id,
            target_label=account.apple_id,
            diff=audit_diff(diff_before, icloud_account_audit_snapshot(account)),
        )
        return account

    async def _create_sync_run(
        self,
        session: AsyncSession,
        *,
        trigger_source: str,
        triggered_by_user_id: uuid.UUID | str | None,
    ) -> ICloudCalendarSyncRun:
        run = ICloudCalendarSyncRun(
            started_at=datetime.now(tz=UTC),
            trigger_source=_clean_source(trigger_source),
            triggered_by_user_id=_coerce_uuid(triggered_by_user_id),
        )
        session.add(run)
        await session.flush()
        return run

    async def _publish_accounts_changed(self, session: AsyncSession) -> None:
        accounts = await self.list_accounts(session)
        await event_bus.publish(
            "icloud_calendar.accounts_changed",
            {"accounts": [serialize_icloud_account(account) for account in accounts]},
        )

    async def _get_pending(self, handshake_id: str) -> PendingICloudAuth | None:
        async with self._pending_lock:
            self._prune_expired_pending_locked()
            return self._pending.get(str(handshake_id or ""))

    async def _drop_pending(self, handshake_id: str, *, cleanup: bool) -> PendingICloudAuth | None:
        async with self._pending_lock:
            pending = self._pending.pop(str(handshake_id or ""), None)
        if cleanup and pending:
            self._client.cleanup_auth_session(pending.auth_session)
        return pending

    def _prune_expired_pending_locked(self) -> None:
        expired = [key for key, pending in self._pending.items() if pending.expired()]
        for key in expired:
            pending = self._pending.pop(key)
            self._client.cleanup_auth_session(pending.auth_session)


def event_contains_open_gate(event: ICloudCalendarEvent) -> bool:
    text = f"{event.notes}\n{event.description}"
    return bool(re.search(r"\bopen\s+gate\b", text, flags=re.IGNORECASE))


def visitor_window_for_event(event: ICloudCalendarEvent) -> tuple[datetime, datetime]:
    return event.starts_at - timedelta(minutes=DEFAULT_WINDOW_MINUTES), event.ends_at


def source_reference_for_event(account_id: uuid.UUID | str, event: ICloudCalendarEvent) -> str:
    return f"icloud:{account_id}:{event.calendar_id}:{event.event_id}"[:255]


def source_metadata_for_event(account: ICloudCalendarAccount, event: ICloudCalendarEvent) -> dict[str, Any]:
    return {
        "account_id": str(account.id),
        "apple_id": account.apple_id,
        "calendar_id": event.calendar_id,
        "event_id": event.event_id,
        "event_title": event.title,
        "event_start": event.starts_at.isoformat(),
        "event_end": event.ends_at.isoformat(),
        "marker": ICLOUD_OPEN_GATE_MARKER,
    }


def calendar_pass_can_be_reconciled(visitor_pass: VisitorPass) -> bool:
    return (
        visitor_pass.creation_source == ICLOUD_CALENDAR_SOURCE
        and visitor_pass.status in {VisitorPassStatus.ACTIVE, VisitorPassStatus.SCHEDULED}
    )


def serialize_icloud_account(account: ICloudCalendarAccount) -> dict[str, Any]:
    return {
        "id": str(account.id),
        "apple_id": account.apple_id,
        "display_name": account.display_name or account.apple_id,
        "status": account.status,
        "is_active": account.is_active,
        "last_auth_at": _iso(account.last_auth_at),
        "last_sync_at": _iso(account.last_sync_at),
        "last_sync_status": account.last_sync_status,
        "last_sync_summary": account.last_sync_summary or None,
        "last_error": account.last_error,
        "created_by_user_id": str(account.created_by_user_id) if account.created_by_user_id else None,
        "created_at": _iso(account.created_at),
        "updated_at": _iso(account.updated_at),
    }


def serialize_icloud_sync_run(run: ICloudCalendarSyncRun) -> dict[str, Any]:
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
        "account_results": run.account_results or [],
        "error": run.error,
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
        "created_by_user_id": str(account.created_by_user_id) if account.created_by_user_id else None,
    }


def _encrypt_session_bundle(bundle: dict[str, Any]) -> str:
    return encrypt_secret(json.dumps(bundle, separators=(",", ":"), sort_keys=True))


def _decrypt_session_bundle(encrypted_bundle: str) -> dict[str, Any]:
    try:
        decoded = json.loads(decrypt_secret(encrypted_bundle))
    except Exception as exc:
        raise ICloudCalendarReauthRequired("Stored iCloud session could not be decoded. Reconnect this account.") from exc
    if not isinstance(decoded, dict):
        raise ICloudCalendarReauthRequired("Stored iCloud session is invalid. Reconnect this account.")
    return decoded


def _sync_range(timezone_name: str) -> tuple[datetime, datetime]:
    try:
        timezone = ZoneInfo(timezone_name)
    except Exception:
        timezone = ZoneInfo("UTC")
    today = datetime.combine(datetime.now(tz=timezone).date(), time.min, tzinfo=timezone)
    return today.astimezone(UTC), (today + timedelta(days=ICLOUD_SYNC_LOOKAHEAD_DAYS + 1)).astimezone(UTC)


def _empty_account_sync_result(account: ICloudCalendarAccount) -> dict[str, Any]:
    return {
        "account_id": str(account.id),
        "apple_id": account.apple_id,
        "status": "ok",
        "events_scanned": 0,
        "events_matched": 0,
        "passes_created": 0,
        "passes_updated": 0,
        "passes_cancelled": 0,
        "passes_skipped": 0,
    }


def _sync_totals(account_results: list[dict[str, Any]]) -> dict[str, int]:
    keys = [
        "events_scanned",
        "events_matched",
        "passes_created",
        "passes_updated",
        "passes_cancelled",
        "passes_skipped",
    ]
    return {key: sum(int(row.get(key) or 0) for row in account_results) for key in keys}


def _clean_apple_id(value: str) -> str:
    apple_id = str(value or "").strip()
    if not apple_id or "@" not in apple_id:
        raise ICloudCalendarError("Enter a valid Apple ID email address.")
    if len(apple_id) > 255:
        raise ICloudCalendarError("Apple ID must be 255 characters or fewer.")
    return apple_id


def _clean_source(value: str) -> str:
    source = str(value or "ui").strip().lower().replace(" ", "_")
    return source[:80] or "ui"


def _coerce_uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


@lru_cache
def get_icloud_calendar_service() -> ICloudCalendarService:
    return ICloudCalendarService()
