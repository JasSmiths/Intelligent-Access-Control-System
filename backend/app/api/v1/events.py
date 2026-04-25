from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionLocal
from app.models import AccessEvent, Anomaly, Presence

router = APIRouter()


@router.get("/events")
async def list_events(limit: int = Query(default=50, ge=1, le=250)) -> list[dict]:
    async with AsyncSessionLocal() as session:
        events = (
            await session.scalars(
                select(AccessEvent)
                .options(selectinload(AccessEvent.anomalies))
                .order_by(AccessEvent.occurred_at.desc())
                .limit(limit)
            )
        ).all()

    return [
        {
            "id": str(event.id),
            "registration_number": event.registration_number,
            "direction": event.direction.value,
            "decision": event.decision.value,
            "confidence": event.confidence,
            "source": event.source,
            "occurred_at": event.occurred_at.isoformat(),
            "timing_classification": event.timing_classification.value,
            "anomaly_count": len(event.anomalies),
        }
        for event in events
    ]


@router.get("/presence")
async def list_presence() -> list[dict]:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.scalars(
                select(Presence).options(selectinload(Presence.person)).order_by(Presence.updated_at.desc())
            )
        ).all()

    return [
        {
            "person_id": str(row.person_id),
            "display_name": row.person.display_name,
            "state": row.state.value,
            "last_changed_at": row.last_changed_at.isoformat() if row.last_changed_at else None,
        }
        for row in rows
    ]


@router.get("/anomalies")
async def list_anomalies(limit: int = Query(default=50, ge=1, le=250)) -> list[dict]:
    async with AsyncSessionLocal() as session:
        anomalies = (
            await session.scalars(select(Anomaly).order_by(Anomaly.created_at.desc()).limit(limit))
        ).all()

    return [
        {
            "id": str(anomaly.id),
            "event_id": str(anomaly.event_id) if anomaly.event_id else None,
            "type": anomaly.anomaly_type.value,
            "severity": anomaly.severity.value,
            "message": anomaly.message,
            "created_at": anomaly.created_at.isoformat(),
        }
        for anomaly in anomalies
    ]
