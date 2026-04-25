from fastapi import APIRouter, Depends

from app.modules.lpr.base import PlateRead, now_utc
from app.services.access_events import AccessEventService, get_access_event_service

router = APIRouter()


@router.post("/arrival/{registration_number}")
async def simulate_arrival(
    registration_number: str,
    service: AccessEventService = Depends(get_access_event_service),
) -> dict[str, str]:
    """Inject a synthetic plate read for local demos and automated tests."""

    plate = registration_number.strip().upper().replace(" ", "")
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
    service: AccessEventService = Depends(get_access_event_service),
) -> dict[str, str]:
    """Inject a rapid sequence of near-matches to exercise debounce logic."""

    plate = registration_number.strip().upper().replace(" ", "")
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
