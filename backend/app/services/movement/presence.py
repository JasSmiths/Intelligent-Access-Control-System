from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.models import AccessEvent, Person, Presence
from app.models.enums import AccessDecision, AccessDirection, PresenceState

logger = get_logger(__name__)


async def commit_presence_for_event(session: Any, event: Any, *, log_prefix: str = "movement") -> bool:
    if not getattr(event, "person_id", None):
        return False
    presence = await session.get(Presence, event.person_id)
    if not presence:
        presence = Presence(person_id=event.person_id)
        session.add(presence)
    if presence.last_changed_at and event.occurred_at < presence.last_changed_at:
        logger.info(
            f"{log_prefix}_presence_stale_skipped",
            extra={
                "event_id": str(event.id),
                "event_occurred_at": event.occurred_at.isoformat(),
                "presence_last_changed_at": presence.last_changed_at.isoformat(),
            },
        )
        return False
    presence.state = PresenceState.PRESENT if event.direction == AccessDirection.ENTRY else PresenceState.EXITED
    presence.last_event_id = event.id
    presence.last_changed_at = event.occurred_at
    return True


async def commit_latest_presence_for_person(session: Any, person: Person, event: AccessEvent) -> bool:
    latest_event = await session.scalar(
        select(AccessEvent)
        .where(
            AccessEvent.person_id == person.id,
            AccessEvent.decision == AccessDecision.GRANTED,
            AccessEvent.direction.in_([AccessDirection.ENTRY, AccessDirection.EXIT]),
        )
        .order_by(AccessEvent.occurred_at.desc(), AccessEvent.created_at.desc())
        .limit(1)
    )
    return await commit_presence_for_event(session, latest_event or event, log_prefix="backfill")
