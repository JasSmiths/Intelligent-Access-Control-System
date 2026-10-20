"""Compact read-only scheduling facts. Claims retain all mutation authority."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from typing import Any

from sqlalchemy import text

from app.core.task_lifecycle import drain_owned
from app.services.durable_work import WorkReadiness
from app.services.workflow_session import SessionFactory

# Each scalar branch seeks one indexed candidate, never hydrates a queue row.


async def readiness(
    sessions: SessionFactory, branches: tuple[str, ...], *,
    parameters: dict[str, Any] | None = None,
) -> WorkReadiness:
    """One statement, one DB clock; no row locks, ORM hydration or commits."""
    values = ",".join(f"(({branch}))" for branch in branches)
    statement = text(
        "WITH t AS MATERIALIZED (SELECT clock_timestamp() AS now) "
        "SELECT t.now, (SELECT min(v.deadline) FROM (VALUES " + values + ") AS v(deadline)) FROM t"
    )
    context = sessions()
    session = await context.__aenter__()
    try:
        async with asyncio.timeout(5):
            row = (await session.execute(statement, parameters or {})).one()
    finally:
        await drain_owned(context.__aexit__(*sys.exc_info()))
    now, deadline = row
    if not isinstance(now, datetime):
        raise TypeError("database_clock_unavailable")
    if deadline is None:
        return WorkReadiness()
    if not isinstance(deadline, datetime):
        raise TypeError("durable_deadline_unavailable")
    delay = max(0.0, (deadline - now).total_seconds())
    return WorkReadiness(delay == 0, delay)
