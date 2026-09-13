"""Maintenance persistence shared by mutation and hardware checkpoint owners.

No provider, notification or realtime imports belong here. The singleton lock
covers creation as well as mutation and remains held until the caller commits.
"""
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import MaintenanceModeState

MAINTENANCE_STATE_ID = 1


async def is_maintenance_mode_active() -> bool:
    async with AsyncSessionLocal() as session:
        row = await session.get(MaintenanceModeState, MAINTENANCE_STATE_ID)
        return bool(row and row.is_active)


async def assert_hardware_enabled(session: AsyncSession) -> None:
    """Fence maintenance changes through the hardware attempt checkpoint."""
    await _lock_maintenance(session)
    row = await session.scalar(select(MaintenanceModeState).where(MaintenanceModeState.id == MAINTENANCE_STATE_ID)
                               .execution_options(populate_existing=True))
    if row is not None and row.is_active:
        raise ValueError("Maintenance Mode is active. Hardware commands are disabled.")


async def get_maintenance_state(session: AsyncSession, *, lock: bool = False) -> MaintenanceModeState:
    if lock:
        await _lock_maintenance(session)
    await session.execute(insert(MaintenanceModeState).values(id=MAINTENANCE_STATE_ID, is_active=False)
                          .on_conflict_do_nothing(index_elements=[MaintenanceModeState.id]))
    query = select(MaintenanceModeState).where(MaintenanceModeState.id == MAINTENANCE_STATE_ID)
    if lock:
        query = query.with_for_update()
    return await session.scalar(query.execution_options(populate_existing=True))


async def _lock_maintenance(session: AsyncSession) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('iacs:maintenance'))"))
