"""Historical movement persistence; deliberately has no hardware or visitor capability."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccessEvent
from app.models.enums import MovementSagaState
from app.modules.dvla.vehicle_enquiry import normalize_registration_number
from app.services.movement.admission import AdmissionResult, finalize_in_session
from app.services.movement_ledger import get_movement_ledger_repository


@dataclass(frozen=True)
class HistoricalSessionInput:
    debounce_seconds: float
    idle_seconds: float
    camera_id: str | None = None
    device_id: str | None = None
    protect_event_ids: set[str] = field(default_factory=set)
    ocr_variants: set[str] = field(default_factory=set)
    last_gate_state: str | None = None


async def persist_historical_event_in_session(session: AsyncSession, event: AccessEvent, *,
    idempotency_key: str, evidence: dict[str, Any], session_input: HistoricalSessionInput,
) -> AdmissionResult:
    """Persist one already-authorized historical event with atomic ordered presence.

    The caller owns identity/evidence discovery, existing authorization and audit
    intent. This owner adds no visitor consumption and cannot issue commands.
    """
    ledger = get_movement_ledger_repository()
    saga = await ledger.create_movement_saga(session, idempotency_key=idempotency_key,
        source=event.source, occurred_at=event.occurred_at, registration_number=event.registration_number,
        person_id=event.person_id, vehicle_id=event.vehicle_id, direction=event.direction,
        decision=event.decision, state=MovementSagaState.DIRECTION_RESOLVED,
        intent_payload={"source": event.source, "access_event_id": str(event.id),
                        "hardware_side_effects_enabled": False, "backfill": True},
        decision_payload={**evidence, "hardware_actions_suppressed": True, "backfill": True})
    await session.refresh(saga, with_for_update=True)
    if saga.access_event_id is not None and saga.access_event_id != event.id:
        raise ValueError("This historical identity already belongs to another durable event.")
    saga.access_event_id = event.id
    await session.flush()
    result = await finalize_in_session(session, saga_id=saga.id, mode="historical", historical_evidence=evidence)
    # Preserve historical suppression lifetimes; these are not live gate cycles.
    await ledger.upsert_movement_session(session, session_key=f"movement-session:{event.id}", source=event.source,
        access_event_id=event.id, movement_saga_id=saga.id, registration_number=event.registration_number,
        normalized_registration_number=normalize_registration_number(event.registration_number),
        direction=event.direction, decision=event.decision, started_at=event.occurred_at,
        last_seen_at=event.occurred_at,
        debounce_expires_at=event.occurred_at + timedelta(seconds=session_input.debounce_seconds),
        gate_cycle_expires_at=event.occurred_at + timedelta(seconds=session_input.debounce_seconds),
        idle_expires_at=event.occurred_at + timedelta(seconds=session_input.idle_seconds),
        camera_id=session_input.camera_id, device_id=session_input.device_id,
        protect_event_ids=session_input.protect_event_ids, ocr_variants=session_input.ocr_variants,
        last_gate_state=session_input.last_gate_state)
    return result
