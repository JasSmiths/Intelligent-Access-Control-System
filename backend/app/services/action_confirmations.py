import hashlib
import hmac
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_secret import get_auth_secret
from app.models import ActionConfirmation, User
from app.services.telemetry import (
    TELEMETRY_CATEGORY_INTEGRATIONS,
    actor_from_user,
    emit_audit_log,
)

CONFIRMATION_TTL_SECONDS = 120


class ActionConfirmationError(Exception):
    def __init__(self, detail: str, *, status_code: int = 403) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def confirmation_payload_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        _canonical_payload(payload),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def create_action_confirmation(
    session: AsyncSession,
    *,
    user: User,
    action: str,
    payload: dict[str, Any],
    target_entity: str | None = None,
    target_id: str | None = None,
    target_label: str | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_action = _normalize_action(action)
    token = secrets.token_urlsafe(32)
    now = datetime.now(tz=UTC)
    payload_hash = confirmation_payload_hash(payload)
    row = ActionConfirmation(
        token_hash=confirmation_token_hash(token),
        action=normalized_action,
        payload_hash=payload_hash,
        actor_user_id=user.id,
        target_entity=target_entity,
        target_id=target_id,
        target_label=target_label,
        expires_at=now + timedelta(seconds=CONFIRMATION_TTL_SECONDS),
        metadata_={
            **(metadata or {}),
            "reason": reason or "",
        },
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    emit_audit_log(
        category=TELEMETRY_CATEGORY_INTEGRATIONS,
        action="real_world_action.confirmation.created",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity=target_entity,
        target_id=target_id,
        target_label=target_label,
        metadata={
            "confirmation_id": str(row.id),
            "action": normalized_action,
            "payload_hash": payload_hash,
            "expires_at": row.expires_at.isoformat(),
            "reason": reason or "",
        },
    )
    return {
        "confirmation_id": str(row.id),
        "confirmation_token": token,
        "action": normalized_action,
        "expires_at": row.expires_at.isoformat(),
    }


async def consume_action_confirmation(
    session: AsyncSession,
    *,
    user: User,
    action: str,
    payload: dict[str, Any],
    confirmation_token: str | None,
) -> ActionConfirmation:
    if not confirmation_token:
        raise ActionConfirmationError(
            "Server-side confirmation is required for this action.",
            status_code=428,
        )
    token_hash = confirmation_token_hash(confirmation_token)
    row = await find_action_confirmation(session, token_hash)
    if row is None:
        _emit_confirmation_rejected(
            user,
            action=action,
            payload_hash=confirmation_payload_hash(payload),
            reason="not_found",
        )
        raise ActionConfirmationError("Confirmation is invalid or has expired.", status_code=403)

    expected_payload_hash = confirmation_payload_hash(payload)
    try:
        _validate_confirmation_row(
            row,
            user=user,
            action=action,
            payload_hash=expected_payload_hash,
            now=datetime.now(tz=UTC),
        )
    except ActionConfirmationError as exc:
        row.consumed_at = row.consumed_at or datetime.now(tz=UTC)
        row.outcome = "rejected"
        await session.commit()
        _emit_confirmation_rejected(
            user,
            action=action,
            payload_hash=expected_payload_hash,
            reason=exc.detail,
            row=row,
        )
        raise

    row.consumed_at = datetime.now(tz=UTC)
    row.outcome = "consumed"
    await session.commit()
    emit_audit_log(
        category=TELEMETRY_CATEGORY_INTEGRATIONS,
        action="real_world_action.confirmation.consumed",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity=row.target_entity,
        target_id=row.target_id,
        target_label=row.target_label,
        metadata={
            "confirmation_id": str(row.id),
            "action": row.action,
            "payload_hash": row.payload_hash,
        },
    )
    return row


async def find_action_confirmation(
    session: AsyncSession,
    token_hash: str,
) -> ActionConfirmation | None:
    return await session.scalar(
        select(ActionConfirmation)
        .where(ActionConfirmation.token_hash == token_hash)
        .with_for_update()
    )


def confirmation_token_hash(token: str) -> str:
    return hmac.new(
        get_auth_secret().encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _validate_confirmation_row(
    row: ActionConfirmation,
    *,
    user: User,
    action: str,
    payload_hash: str,
    now: datetime,
) -> None:
    normalized_action = _normalize_action(action)
    if row.consumed_at is not None:
        raise ActionConfirmationError("Confirmation was already used.", status_code=409)
    if row.expires_at <= now:
        raise ActionConfirmationError("Confirmation has expired.", status_code=403)
    if str(row.actor_user_id) != str(user.id):
        raise ActionConfirmationError("Confirmation belongs to a different user.", status_code=403)
    if row.action != normalized_action:
        raise ActionConfirmationError("Confirmation action does not match this request.", status_code=403)
    if row.payload_hash != payload_hash:
        raise ActionConfirmationError("Confirmation payload does not match this request.", status_code=403)


def _canonical_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _canonical_payload(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
            if item is not None and str(key) != "confirmation_token"
        }
    if isinstance(value, list | tuple):
        return [_canonical_payload(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _emit_confirmation_rejected(
    user: User,
    *,
    action: str,
    payload_hash: str,
    reason: str,
    row: ActionConfirmation | None = None,
) -> None:
    emit_audit_log(
        category=TELEMETRY_CATEGORY_INTEGRATIONS,
        action="real_world_action.confirmation.rejected",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity=row.target_entity if row else None,
        target_id=row.target_id if row else None,
        target_label=row.target_label if row else None,
        outcome="failed",
        level="warning",
        metadata={
            "confirmation_id": str(row.id) if row else None,
            "action": _normalize_action(action),
            "payload_hash": payload_hash,
            "reason": reason,
        },
    )


def _normalize_action(action: str) -> str:
    normalized = action.strip()
    if not normalized:
        raise ActionConfirmationError("Confirmation action is required.", status_code=400)
    return normalized[:120]
