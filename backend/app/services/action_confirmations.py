import hashlib
import hmac
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_secret import get_auth_secret
from app.core.task_lifecycle import drain_owned
from app.db.session import AsyncSessionLocal
from app.models import ActionConfirmation, User
from app.services.telemetry import (
    TELEMETRY_CATEGORY_INTEGRATIONS,
    actor_from_user,
    current_request_id,
    current_trace_id,
    write_audit_log,
)

CONFIRMATION_TTL_SECONDS = 120
ISO_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


@dataclass(frozen=True)
class ConfirmationRejection:
    """Scalar audit handoff; no ORM instances, payload or bearer token survive."""

    actor: str
    actor_user_id: uuid.UUID
    action: str
    payload_hash: str
    reason: str
    confirmation_id: uuid.UUID | None
    target_entity: str | None
    target_id: str | None
    target_label: str | None
    trace_id: str | None
    request_id: str | None


class ActionConfirmationError(Exception):
    def __init__(
        self, detail: str, *, status_code: int = 403,
        rejection: ConfirmationRejection | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.rejection = rejection


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
        id=uuid.uuid4(),
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
    await write_audit_log(
        session,
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
    await session.commit()
    await session.refresh(row)
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
    expected_hardware_plan: dict[str, Any] | None = None,
    commit: bool = True,
) -> ActionConfirmation:
    """Standalone calls own commit; participants return rejection evidence.

    A commit=False caller rolls back its full transaction, then awaits
    persist_confirmation_rejection. An independent audit must not wait on the
    caller's actor FK while that caller still holds User FOR UPDATE.
    """
    if not confirmation_token:
        raise ActionConfirmationError(
            "Server-side confirmation is required for this action.",
            status_code=428,
        )
    token_hash = confirmation_token_hash(confirmation_token)
    row = await find_action_confirmation(session, token_hash)
    expected_payload_hash = confirmation_payload_hash(payload)
    if row is None:
        rejection = _confirmation_rejection(
            user, action=action, payload_hash=expected_payload_hash, reason="not_found",
        )
        if commit:
            await _write_confirmation_rejection(session, rejection)
            await session.commit()
        raise ActionConfirmationError(
            "Confirmation is invalid or has expired.", status_code=403, rejection=rejection,
        )

    try:
        _validate_confirmation_row(
            row, user=user, action=action, payload_hash=expected_payload_hash,
            now=datetime.now(tz=UTC),
        )
        if (
            expected_hardware_plan is not None
            and (row.metadata_ or {}).get("hardware_plan") != expected_hardware_plan
        ):
            raise ActionConfirmationError(
                "Hardware targets or configuration changed. Create a fresh confirmation.",
                status_code=409,
            )
    except ActionConfirmationError as exc:
        exc.rejection = _confirmation_rejection(
            user, action=action, payload_hash=expected_payload_hash, reason=exc.detail, row=row,
        )
        if commit:
            if row.consumed_at is None:
                row.consumed_at = datetime.now(tz=UTC)
                row.outcome = "rejected"
            await _write_confirmation_rejection(session, exc.rejection)
            await session.commit()
        raise

    row.consumed_at = datetime.now(tz=UTC)
    row.outcome = "consumed"
    await write_audit_log(
        session,
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
    if commit:
        await session.commit()
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
        raise ActionConfirmationError(
            "Confirmation action does not match this request.", status_code=403
        )
    if row.payload_hash != payload_hash:
        raise ActionConfirmationError(
            "Confirmation payload does not match this request.", status_code=403
        )


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
        return _canonical_timestamp(value)
    if isinstance(value, str) and ISO_TIMESTAMP_PATTERN.fullmatch(value):
        try:
            return _canonical_timestamp(datetime.fromisoformat(value))
        except ValueError:
            return value
    return value


def _canonical_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        return value.isoformat()
    normalized = value.astimezone(UTC)
    timestamp = normalized.strftime("%Y-%m-%dT%H:%M:%S")
    if normalized.microsecond:
        timestamp = f"{timestamp}.{normalized.microsecond:06d}".rstrip("0")
    return f"{timestamp}Z"


def _confirmation_rejection(
    user: User,
    *,
    action: str,
    payload_hash: str,
    reason: str,
    row: ActionConfirmation | None = None,
) -> ConfirmationRejection:
    return ConfirmationRejection(
        actor=actor_from_user(user), actor_user_id=user.id,
        action=_normalize_action(action), payload_hash=payload_hash, reason=reason,
        confirmation_id=row.id if row else None,
        target_entity=row.target_entity if row else None,
        target_id=row.target_id if row else None,
        target_label=row.target_label if row else None,
        trace_id=current_trace_id(), request_id=current_request_id(),
    )


async def _write_confirmation_rejection(
    session: AsyncSession, rejection: ConfirmationRejection,
) -> None:
    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_INTEGRATIONS,
        action="real_world_action.confirmation.rejected",
        actor=rejection.actor, actor_user_id=rejection.actor_user_id,
        target_entity=rejection.target_entity, target_id=rejection.target_id,
        target_label=rejection.target_label, outcome="failed", level="warning",
        trace_id=rejection.trace_id, request_id=rejection.request_id,
        metadata={
            "confirmation_id": str(rejection.confirmation_id) if rejection.confirmation_id else None,
            "action": rejection.action, "payload_hash": rejection.payload_hash,
            "reason": rejection.reason,
        },
    )


async def persist_confirmation_rejection(rejection: ConfirmationRejection | None) -> None:
    """Await only after the participant's entire transaction has rolled back."""
    if rejection is None:
        return  # Missing-token428 has never been an audited rejection path.
    session = AsyncSessionLocal()
    try:
        await _write_confirmation_rejection(session, rejection)
        await session.commit()
    finally:
        await drain_owned(session.close())


def _normalize_action(action: str) -> str:
    normalized = action.strip()
    if not normalized:
        raise ActionConfirmationError("Confirmation action is required.", status_code=400)
    return normalized[:120]
