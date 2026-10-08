"""Complete Apple setup/verification operations with short owned transactions."""
from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.modules.icloud_calendar.client import ICloudAuthSession, ICloudCalendarClient
from app.modules.icloud_calendar.native import CalendarNativeOwner
from app.services.calendar_accounts import CalendarAccountStore, CalendarAuthCapture
from app.services.calendar_sync_store import CalendarAuthorization
from app.services.icloud_calendar_sdk import (
    CALENDAR_PENDING_AUTH_TTL,
    CalendarSDKOwner,
    PendingCalendarAuth,
)

logger = get_logger(__name__)


class CalendarAuthenticationError(ValueError):
    """Safe setup/verification failure; no account acceptance was reported."""


class CalendarAuthentication:
    def __init__(self, client: ICloudCalendarClient, sdk: CalendarSDKOwner,
                 accounts: CalendarAccountStore) -> None:
        self.client, self.sdk, self.accounts = client, sdk, accounts

    async def start(self, *, apple_id: str, password: str, actor: str,
                    actor_user_id: uuid.UUID, authorize: CalendarAuthorization,
                    disconnect: Callable[[], Awaitable[None]] | None = None,
                    after_result: Callable[[dict[str, Any], CalendarNativeOwner], Awaitable[dict[str, Any]]] | None = None) -> dict[str, Any]:
        apple_id = str(apple_id or "").strip().lower()
        if not apple_id or "@" not in apple_id:
            raise CalendarAuthenticationError("Enter a valid Apple ID email address.")
        if not password:
            raise CalendarAuthenticationError("Apple ID password is required to start iCloud Calendar setup.")

        async def owned(native: CalendarNativeOwner) -> dict[str, Any]:
            self.sdk.reserve_pending_capacity()
            capture = await self.accounts.capture(apple_id, authorize=authorize, work=native.work)
            session = await asyncio.to_thread(self.client.start_auth, apple_id, password, operation=native)
            retained, accepted = False, None
            failure: BaseException | None = None
            try:
                native.work.check()
                if self.client.requires_security_key(session):
                    raise CalendarAuthenticationError("This Apple account is asking for a security key. IACS currently supports code verification only.")
                if self.client.requires_2fa(session):
                    await asyncio.to_thread(self.client.request_2fa_code, session)
                    await self.accounts.record_handshake(capture, actor=actor,
                        actor_user_id=actor_user_id, authorize=authorize, work=native.work)
                    pending = PendingCalendarAuth(uuid.uuid4().hex, session,
                        datetime.now(UTC) + CALENDAR_PENDING_AUTH_TTL,
                        str(capture.account_id) if capture.account_id else None,
                        capture.generation, str(actor_user_id))
                    await self.sdk.retain(pending)
                    retained = True
                    return {"status": "requires_2fa", "requires_2fa": True,
                        "handshake_id": pending.handshake_id, "apple_id": apple_id,
                        "detail": "Enter the six-digit Apple verification code to finish connecting this iCloud Calendar account."}
                if self.client.requires_legacy_2sa(session):
                    raise CalendarAuthenticationError("This Apple account uses older two-step verification. IACS supports six-digit code verification only.")
                bundle = await asyncio.to_thread(self.client.session_bundle, session)
                accepted = await self.accounts.persist(capture, bundle, actor=actor,
                    actor_user_id=actor_user_id, authorize=authorize, work=native.work)
                return {"status": "connected", "requires_2fa": False, "account": accepted}
            except BaseException as exc:
                failure = exc
                raise
            finally:
                if not retained:
                    await self._cleanup(session, accepted=accepted is not None, primary=failure)

        return await self.sdk.run(owned, disconnect=disconnect, after_result=after_result)

    async def verify(self, *, handshake_id: str, code: str, actor: str,
                     actor_user_id: uuid.UUID, authorize: CalendarAuthorization,
                     disconnect: Callable[[], Awaitable[None]] | None = None,
                    after_result: Callable[[dict[str, Any], CalendarNativeOwner], Awaitable[dict[str, Any]]] | None = None) -> dict[str, Any]:
        code = str(code or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            raise CalendarAuthenticationError("Enter the six-digit Apple verification code.")

        async def owned(native: CalendarNativeOwner) -> dict[str, Any]:
            pending = self.sdk.pending(handshake_id)
            if pending is None:
                raise CalendarAuthenticationError("The iCloud verification session expired. Start account setup again.")
            if pending.actor_user_id != str(actor_user_id):
                raise CalendarAuthenticationError("This verification session belongs to another Admin. Start account setup again.")
            capture = CalendarAuthCapture(pending.session.apple_id,
                uuid.UUID(pending.account_id) if pending.account_id else None, pending.account_generation)
            accepted, rejected_code = None, False
            failure: BaseException | None = None
            try:
                await self.accounts.recheck(capture, authorize=authorize, work=native.work)
                await asyncio.to_thread(self.client.activate, pending.session, native)
                verified = await asyncio.to_thread(self.client.validate_2fa_code, pending.session, code)
                if not verified:
                    rejected_code = True
                    raise CalendarAuthenticationError("Apple rejected that verification code. Check the code and try again.")
                trusted = await asyncio.to_thread(self.client.trust_session, pending.session)
                if not trusted:
                    raise CalendarAuthenticationError("Apple verified the code but did not trust the session. Try setup again.")
                bundle = await asyncio.to_thread(self.client.session_bundle, pending.session)
                accepted = await self.accounts.persist(capture, bundle, actor=actor,
                    actor_user_id=actor_user_id, authorize=authorize, work=native.work)
                return {"status": "connected", "account": accepted}
            except BaseException as exc:
                failure = exc
                raise
            finally:
                try:
                    if rejected_code:
                        # Retain the same reusable client without active timers.
                        await asyncio.to_thread(self.client.detach, pending.session)
                    else:
                        await self.sdk.drop(handshake_id)
                except Exception:
                    if accepted is None and failure is None:
                        raise
                    logger.warning("Apple verification cleanup failed after its primary result")

        return await self.sdk.run(owned, disconnect=disconnect, after_result=after_result)

    async def _cleanup(self, session: ICloudAuthSession, *, accepted: bool, primary: BaseException | None) -> None:
        try:
            await asyncio.to_thread(self.client.cleanup_auth_session, session)
        except Exception:
            if not accepted and primary is None:
                raise
            logger.warning("Apple setup cleanup failed after its primary result")
