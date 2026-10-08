"""Actual database-close ownership for the access completion/publication lanes.

The operation child receives cancellation during active SQL. Its connection close
must settle before the owning slot or application resources can be released.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.task_lifecycle import drain_owned


@asynccontextmanager
async def completion_session(factory: Callable[[], AsyncSession]) -> AsyncIterator[AsyncSession]:
    session = factory()
    try:
        yield session
    finally:
        await drain_owned(session.close())
