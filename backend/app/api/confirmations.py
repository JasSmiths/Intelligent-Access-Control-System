from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActionConfirmation, User
from app.services.action_confirmations import ActionConfirmationError, consume_action_confirmation
from app.services.mutation_context import MutationError


async def require_confirmed_action(
    session: AsyncSession,
    *,
    user: User,
    action: str,
    payload: dict[str, Any],
    confirmation_token: str | None,
    expected_hardware_plan: dict[str, Any] | None = None,
) -> ActionConfirmation:
    try:
        return await consume_action_confirmation(
            session,
            user=user,
            action=action,
            payload=payload,
            confirmation_token=confirmation_token,
            expected_hardware_plan=expected_hardware_plan,
        )
    except ActionConfirmationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def send_confirmed_notification(
    session, *, user, action, payload, confirmation_token, context,
    direct_action=None, rules_override=None, ephemeral_config=None,
):
    """Commit accepted notification work before starting the common dispatcher."""
    from app.services.notifications import get_notification_service

    service = get_notification_service()
    try:
        identity, claimed = await service.reserve_confirmed_request(
            session, user=user, action=action, payload=payload, confirmation_token=confirmation_token,
            context=context, direct_action=direct_action, rules_override=rules_override,
            ephemeral_config=ephemeral_config,
        )
        await session.commit()
    except ActionConfirmationError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except MutationError as exc:
        await session.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    result = await service.dispatch_reserved(identity, claimed, ephemeral_config=ephemeral_config)
    if result.status != "sent" or result.failed_count:
        reason = "; ".join(result.failures or result.skipped_reasons)
        raise HTTPException(
            status_code=503,
            detail=reason or "Notification was not verified as delivered. Inspect its delivery record before sending again.",
            headers={"X-IACS-Notification-Run-ID": str(identity)},
        )
    return result
