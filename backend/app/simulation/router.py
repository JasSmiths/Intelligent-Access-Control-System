from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.confirmations import require_confirmed_action
from app.api.dependencies import admin_user
from app.db.session import get_db_session
from app.models import User
from app.modules.lpr.base import PlateRead, now_utc
from app.services.maintenance import is_maintenance_mode_active
from app.services.access_events import AccessEventService, get_access_event_service
from app.simulation.scenarios import FullAccessFlowReport, FullAccessFlowRequest, run_full_access_flow

router = APIRouter()


class SimulationInjectionRequest(BaseModel):
    confirmation_token: str | None = Field(default=None, max_length=160)


async def _raise_if_maintenance_active() -> None:
    if await is_maintenance_mode_active():
        raise HTTPException(
            status_code=423,
            detail="Maintenance Mode is active. Automated actions are disabled.",
        )


@router.post("/arrival/{registration_number}")
async def simulate_arrival(
    registration_number: str,
    request: SimulationInjectionRequest | None = Body(default=None),
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
    service: AccessEventService = Depends(get_access_event_service),
) -> dict[str, str]:
    """Inject a synthetic plate read for local demos and automated tests."""

    await _raise_if_maintenance_active()
    plate = registration_number.strip().upper().replace(" ", "")
    await require_confirmed_action(
        session,
        user=user,
        action="simulation.arrival",
        payload={"registration_number": plate},
        confirmation_token=request.confirmation_token if request else None,
    )
    await service.enqueue_plate_read(
        PlateRead(
            registration_number=plate,
            confidence=0.98,
            source="simulator",
            captured_at=now_utc(),
            raw_payload={"simulated": True, "kind": "arrival"},
        )
    )
    return {"status": "simulated", "registration_number": plate}


@router.post("/misread-sequence/{registration_number}")
async def simulate_misread_sequence(
    registration_number: str,
    request: SimulationInjectionRequest | None = Body(default=None),
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
    service: AccessEventService = Depends(get_access_event_service),
) -> dict[str, str]:
    """Inject a rapid sequence of near-matches to exercise debounce logic."""

    await _raise_if_maintenance_active()
    plate = registration_number.strip().upper().replace(" ", "")
    await require_confirmed_action(
        session,
        user=user,
        action="simulation.misread_sequence",
        payload={"registration_number": plate},
        confirmation_token=request.confirmation_token if request else None,
    )
    candidates = [
        (plate.replace("0", "O") if "0" in plate else f"{plate[:-1]}8", 0.62),
        (plate, 0.82),
        (plate, 0.97),
    ]

    for candidate, confidence in candidates:
        await service.enqueue_plate_read(
            PlateRead(
                registration_number=candidate,
                confidence=confidence,
                source="simulator",
                captured_at=now_utc(),
                raw_payload={"simulated": True, "kind": "misread_sequence"},
            )
        )

    return {"status": "simulated", "registration_number": plate, "candidate_count": str(len(candidates))}


@router.post("/e2e/full-access-flow", response_model=FullAccessFlowReport)
async def simulate_full_access_flow(
    request: FullAccessFlowRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> FullAccessFlowReport:
    """Run the hardware-free end-to-end access-flow simulation suite."""

    try:
        await require_confirmed_action(
            session,
            user=user,
            action="simulation.full_access_flow",
            payload=request.model_dump(mode="json", exclude={"confirmation_token"}, exclude_none=True, exclude_unset=True),
            confirmation_token=request.confirmation_token,
        )
        return await run_full_access_flow(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
