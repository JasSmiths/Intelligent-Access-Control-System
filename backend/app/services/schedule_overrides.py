"""Create an audited temporary schedule allowance after adapter confirmation."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Person, ScheduleOverride, User
from app.services.event_bus import event_bus
from app.services.schedule_operations import ScheduleOperationError, require_schedule_admin
from app.services.telemetry import TELEMETRY_CATEGORY_CRUD, actor_from_user, write_audit_log

logger = get_logger(__name__)


async def create_schedule_override(
    session: AsyncSession, *, person_id: uuid.UUID, starts_at: datetime, duration_minutes: int,
    reason: str, user: User, source: Literal["alfred"],
) -> ScheduleOverride:
    require_schedule_admin(user)
    if starts_at.tzinfo is None or starts_at.utcoffset() is None:
        raise ScheduleOperationError("invalid_override", "Override start time must include a timezone")
    if not 1 <= duration_minutes <= 1440:
        raise ScheduleOperationError("invalid_override", "Override duration must be between 1 and 1440 minutes")
    person = await session.get(Person, person_id)
    if person is None:
        raise ScheduleOperationError("person_not_found", "Person not found")
    # Audit canonical UTC values; the adapter still presents the site timezone.
    starts_at = starts_at.astimezone(UTC)
    override = ScheduleOverride(
        person_id=person.id, starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=duration_minutes), reason=reason.strip(),
        created_by_user_id=user.id, source=source, is_active=True,
    )
    try:
        session.add(override)
        await session.flush()
        await write_audit_log(
            session, category=TELEMETRY_CATEGORY_CRUD, action="schedule.override.create",
            actor=actor_from_user(user), actor_user_id=user.id,
            target_entity="ScheduleOverride", target_id=override.id, target_label=person.display_name,
            diff={"old": {}, "new": {
                "id": str(override.id), "person_id": str(person.id),
                "starts_at": override.starts_at.isoformat(), "ends_at": override.ends_at.isoformat(),
                "reason": override.reason, "is_active": True,
            }}, metadata={"source": source},
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return override


async def publish_schedule_override_created(payload: dict) -> None:
    """Realtime delivery must not change the outcome of a committed override."""
    try:
        await event_bus.publish("schedule.override_created", payload)
    except Exception:  # noqa: BLE001 - The durable override/audit already committed.
        logger.warning("schedule_override_realtime_publication_failed", extra={"override_id": payload["override_id"]})
