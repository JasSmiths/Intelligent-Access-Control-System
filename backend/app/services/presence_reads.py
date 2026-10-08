"""Presence scalar compatibility reads and bounded internal presentation."""
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict, cast

from sqlalchemy import func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Person, Presence
from app.models.enums import PresenceState

ROSTER_LIMIT = 6


class PresenceRow(TypedDict):
    person_id: str
    display_name: str
    state: Literal["present", "exited", "unknown"]
    last_changed_at: str | None


class PresenceCounts(TypedDict):
    total: int
    present: int
    exited: int
    unknown: int


class PresenceRoster(TypedDict):
    people: list[PresenceRow]
    hidden: int


class PresenceSummary(TypedDict):
    counts: PresenceCounts
    inside: PresenceRoster


def _row(row: Mapping[str, Any]) -> PresenceRow:
    changed: datetime | None = row["last_changed_at"]
    state: PresenceState = row["state"]
    return {"person_id": str(row["person_id"]), "display_name": row["display_name"],
            "state": cast(Literal["present", "exited", "unknown"], state.value),
            "last_changed_at": changed.isoformat() if changed else None}


async def list_presence_in_session(session: AsyncSession) -> list[PresenceRow]:
    """Retain population-sized public ordering; transfer no unused Person fields."""
    rows = (await session.execute(
        select(Presence.person_id, Person.display_name, Presence.state, Presence.last_changed_at)
        .join(Person, Person.id == Presence.person_id).order_by(Presence.updated_at.desc())
    )).mappings().all()
    return [_row(row) for row in rows]


async def presence_summary_in_session(session: AsyncSession) -> PresenceSummary:
    """Counts and six ordered rows share a single PostgreSQL statement snapshot."""
    counts = select(
        func.count().label("total"),
        func.count().filter(Presence.state == PresenceState.PRESENT).label("present"),
        func.count().filter(Presence.state == PresenceState.EXITED).label("exited"),
    ).select_from(Presence).subquery()
    roster = (
        select(Presence.person_id, Person.display_name, Presence.state, Presence.last_changed_at,
               func.coalesce(Presence.last_changed_at, datetime(1970, 1, 1, tzinfo=UTC)).label("changed_order"),
               Presence.updated_at)
        .join(Person, Person.id == Presence.person_id)
        .where(Presence.state == PresenceState.PRESENT)
        .order_by(func.coalesce(Presence.last_changed_at, datetime(1970, 1, 1, tzinfo=UTC)).desc(),
                  Person.display_name.collate("C"), Presence.updated_at.desc(), Presence.person_id)
        .limit(ROSTER_LIMIT).subquery()
    )
    rows = (await session.execute(
        select(counts, roster).select_from(counts).outerjoin(roster, true())
        .order_by(roster.c.changed_order.desc(), roster.c.display_name.collate("C"),
                  roster.c.updated_at.desc(), roster.c.person_id)
    )).mappings().all()
    first = rows[0]
    total, present, exited = int(first["total"]), int(first["present"]), int(first["exited"])
    people = [_row(row) for row in rows if row["person_id"] is not None]
    return {"counts": {"total": total, "present": present, "exited": exited,
                       "unknown": total - present - exited},
            "inside": {"people": people, "hidden": max(present - len(people), 0)}}
