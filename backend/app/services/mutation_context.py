"""Authentication invariant for interactive feature mutations."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.enums import UserRole


class MutationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def require_active_admin(user: User | None) -> User:
    if user is None or not user.id or not user.is_active or user.role != UserRole.ADMIN:
        raise MutationError("forbidden", "Active Admin access is required.")
    return user


async def load_active_admin(
    session: AsyncSession,
    user_id: uuid.UUID | str,
    *,
    auth_version: int | None = None,
    lock: bool = False,
) -> User:
    """Revalidate an actor at a mutation checkpoint using current database state.

    When locking, acquire the actor before domain rows and hold until the
    attempted-effect checkpoint commits. A previous request object is not proof.
    """
    try:
        identity = uuid.UUID(str(user_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise MutationError("forbidden", "Active Admin access is required.") from exc
    query = select(User).where(User.id == identity).execution_options(populate_existing=True)
    if lock:
        query = query.with_for_update()
    user = require_active_admin(await session.scalar(query))
    if auth_version is not None and user.auth_session_version != auth_version:
        raise MutationError("actor_changed", "Authentication changed; a fresh confirmation is required.")
    return user
