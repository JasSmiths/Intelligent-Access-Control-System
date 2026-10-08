"""Current Admin request authority; bearer material is transient and never logged."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import SessionTransaction

from app.models import User
from app.models.enums import UserRole
from app.services.auth import authenticated_token


class CalendarAuthorityError(ValueError):
    """The original request session no longer has current Admin authority."""


@dataclass(frozen=True)
class CalendarRequestAuthority:
    actor_user_id: uuid.UUID
    token: str = field(repr=False)

    async def authorize(self, session: AsyncSession, final: bool) -> datetime | None:
        if final:
            # Do not wait for actor administration while holding visitor/intake
            # locks. A conflict rolls back this account and remains recoverable.
            await session.scalar(select(User.id).where(User.id == self.actor_user_id).with_for_update(nowait=True))
        verified = await authenticated_token(session, self.token)
        if (verified is None or verified.user.id != self.actor_user_id
            or verified.user.role != UserRole.ADMIN):
            raise CalendarAuthorityError("Your Admin session is no longer valid. Sign in again before explicitly resuming this calendar run.")
        try:
            expires_at = datetime.fromtimestamp(verified.expires_at, UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise CalendarAuthorityError("Your session expiry cannot authorize calendar synchronization. Sign in again.") from exc
        return expires_at if final else None


@dataclass
class CalendarConfirmedAuthority:
    """One requester-bound confirmation joined to this calendar transaction."""
    request: CalendarRequestAuthority
    action: str
    payload: dict[str, str]
    confirmation_token: str | None = field(repr=False)
    _consumed_transaction: SessionTransaction | None = field(default=None, init=False, repr=False)

    async def authorize(self, session: AsyncSession, final: bool) -> datetime | None:
        until = await self.request.authorize(session, final)
        transaction = session.sync_session.get_transaction()
        if self._consumed_transaction is None or self._consumed_transaction is not transaction:
            from app.services.action_confirmations import consume_action_confirmation
            user = await session.get(User, self.request.actor_user_id)
            if user is None:
                raise CalendarAuthorityError("Your Admin account is no longer available.")
            await consume_action_confirmation(session, user=user, action=self.action,
                payload=self.payload, confirmation_token=self.confirmation_token, commit=False)
            transaction = session.sync_session.get_transaction()
            assert transaction is not None
            self._consumed_transaction = transaction

        return until
