from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccessEvent, Presence
from app.models.enums import AccessDecision, AccessDirection, PresenceState

@dataclass(frozen=True)
class PresenceTransition:
    changed: bool
    result: str


def event_order(event: AccessEvent) -> tuple[datetime, datetime, str]:
    """Deterministic conflict resolution, not an inferred physical sequence."""
    return _aware(event.occurred_at), _aware(event.created_at), str(event.id)


async def apply_eligible_event_in_session(session: AsyncSession, event: AccessEvent) -> PresenceTransition:
    """Apply an event whose admission was established by movement.admission.

    The person lock also serializes first insertion, where no Presence row exists
    to lock yet. Every runtime writer uses this transition; it never commits.
    """
    if (event.person_id is None or event.decision != AccessDecision.GRANTED
            or event.direction not in {AccessDirection.ENTRY, AccessDirection.EXIT}):
        return PresenceTransition(False, "ineligible")
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:person))"),
                          {"person": f"iacs:presence:{event.person_id}"})
    row = await session.get(Presence, event.person_id, with_for_update=True, populate_existing=True)
    if row is not None:
        if row.last_event_id == event.id:
            return PresenceTransition(False, "already_applied")
        if row.last_changed_at is not None:
            if _aware(event.occurred_at) < _aware(row.last_changed_at):
                return PresenceTransition(False, "stale")
            if _aware(event.occurred_at) == _aware(row.last_changed_at):
                previous = await session.get(AccessEvent, row.last_event_id, populate_existing=True) if row.last_event_id else None
                if previous is None:
                    return PresenceTransition(False, "legacy_equal_time")
                if event_order(event) <= event_order(previous):
                    return PresenceTransition(False, "stale")
    else:
        row = Presence(person_id=event.person_id)
        session.add(row)
    row.state = PresenceState.PRESENT if event.direction == AccessDirection.ENTRY else PresenceState.EXITED
    row.last_event_id, row.last_changed_at = event.id, event.occurred_at
    await session.flush()
    return PresenceTransition(True, "applied")


def _aware(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)
