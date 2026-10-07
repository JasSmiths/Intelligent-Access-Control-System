"""Current lifetime checks for vehicle-information notification intents."""
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import VehicleInformationJob


async def vehicle_information_notice_denial(session: AsyncSession, payload: dict[str, Any], run_id: uuid.UUID) -> str | None:
    facts = payload.get("facts") or {}
    event_id = facts.get("vehicle_information_event_id")
    if not event_id:
        return None
    try:
        identity = uuid.UUID(str(event_id))
    except ValueError:
        return "invalid_vehicle_information_origin"
    event_type = payload.get("event_type")
    if event_type not in {"expired_mot_detected", "expired_tax_detected"} or run_id != uuid.uuid5(identity, event_type):
        return "invalid_vehicle_information_origin"
    job = await session.get(VehicleInformationJob, identity)
    now = await session.scalar(select(func.clock_timestamp()))
    if not job or job.status != "completed" or not now or now >= job.deadline:
        return "vehicle_information_notice_expired"
    return None
