from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models import User
from app.services.auth import AdminRequiredError, AuthError, authenticate_request, require_admin


async def current_user(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> User:
    state_user = getattr(request.state, "user", None)
    if state_user:
        return state_user

    user = await authenticate_request(session, request)
    if not user:
        raise AuthError()
    request.state.user = user
    return user


async def admin_user(user: User = Depends(current_user)) -> User:
    try:
        require_admin(user)
    except AdminRequiredError:
        raise
    return user
