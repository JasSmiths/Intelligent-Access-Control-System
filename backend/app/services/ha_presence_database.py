"""Short HA transactions retain their actual close, never provider work."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.task_lifecycle import drain_owned
from app.models import (
    HaPresenceCoordinator,
    HaPresenceDesired,
    HaPresenceTarget,
    Person,
    SystemSetting,
)
from app.services.auth_root import try_reload_auth_root_for_transaction
from app.services.durable_work import WorkTopic, notify_work
from app.services.ha_presence_contracts import HA_SETTING_KEYS, HaCoordinationBusy, HaCurrentPerson

HA_LEASE_SECONDS = 30
HA_RENEWAL_SECONDS = 5
HA_OPERATION_SECONDS = 10
HA_RECEIPT_SECONDS = 10
HA_SCAN_SIZE = 32


@asynccontextmanager
async def ha_session(factory: Callable[[], AsyncSession]) -> AsyncIterator[AsyncSession]:
    session = factory()
    try:
        yield session
    finally:
        await drain_owned(session.close())


async def database_now(session: AsyncSession) -> datetime:
    return (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()


async def lock_ha_coordinator(session: AsyncSession) -> HaPresenceCoordinator:
    # Covers first insertion and all later mutation with the same short authority.
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('iacs:ha-presence'))"))
    await session.execute(insert(HaPresenceCoordinator).values(
        id=1, next_generation=0, config_revision=0, epoch=0,
        lease_token=None, lease_until=None,
    ).on_conflict_do_nothing(index_elements=[HaPresenceCoordinator.id]))
    row = await session.scalar(select(HaPresenceCoordinator).where(
        HaPresenceCoordinator.id == 1,
    ).with_for_update().execution_options(populate_existing=True))
    assert row is not None
    return row


async def lock_current_people(
    session: AsyncSession, person_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, HaCurrentPerson]:
    """Current binding authority excludes profile-photo/notes and relationships."""
    rows = (await session.execute(select(
        Person.id, Person.is_active,
        Person.home_assistant_presence_input_boolean_entity_ids,
        Person.home_assistant_presence_input_boolean_entry_action,
        Person.home_assistant_presence_input_boolean_exit_action,
    ).where(Person.id.in_(person_ids)).order_by(Person.id)
        .with_for_update(nowait=True))).all()
    return {row.id: HaCurrentPerson(
        row.id, row.is_active, row.home_assistant_presence_input_boolean_entity_ids,
        row.home_assistant_presence_input_boolean_entry_action,
        row.home_assistant_presence_input_boolean_exit_action,
    ) for row in rows}


async def lock_home_assistant_setting_rows(session: AsyncSession) -> list[SystemSetting]:
    """Exact missing-row and NOWAIT authority shared by reads and invalidation."""
    for key in HA_SETTING_KEYS:
        acquired = await session.scalar(text(
            "SELECT pg_try_advisory_xact_lock(hashtext(:setting))"
        ), {"setting": f"iacs:setting:{key}"})
        if acquired is not True:
            raise HaCoordinationBusy("ha_setting_busy")
    return list((await session.scalars(select(SystemSetting).where(
        SystemSetting.key.in_(HA_SETTING_KEYS),
    ).order_by(SystemSetting.key).with_for_update(nowait=True)
        .execution_options(populate_existing=True))).all())


async def invalidate_configuration_in_session(session: AsyncSession) -> int:
    """No Person locks: settings owner already completed all ordinary mutations."""
    try:
        if await try_reload_auth_root_for_transaction(session) is None:
            raise HaCoordinationBusy("ha_auth_root_busy")
        # Current setting rows are already caller-owned by the writer. The same port
        # also covers a missing setting's advisory authority without a new lock graph.
        await lock_home_assistant_setting_rows(session)
        coordinator = await lock_ha_coordinator(session)
        now = await database_now(session)
        coordinator.next_generation += 1
        coordinator.config_revision += 1
        generation = coordinator.next_generation
        await session.execute(update(HaPresenceDesired).values(
            generation=generation, eligible=False, outcome="configuration_changed",
        ))
        await session.execute(update(HaPresenceTarget).values(
            generation=generation, state="invalidated", updated_at=now,
        ))
        await session.flush()
        await notify_work(session, WorkTopic.HA)
        return generation
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            raise HaCoordinationBusy("ha_current_authority_busy") from exc
        raise
