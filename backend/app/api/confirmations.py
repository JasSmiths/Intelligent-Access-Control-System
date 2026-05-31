from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.action_confirmations import ActionConfirmationError, consume_action_confirmation


async def require_confirmed_action(
    session: AsyncSession,
    *,
    user: User,
    action: str,
    payload: dict[str, Any],
    confirmation_token: str | None,
) -> None:
    try:
        await consume_action_confirmation(
            session,
            user=user,
            action=action,
            payload=payload,
            confirmation_token=confirmation_token,
        )
    except ActionConfirmationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
