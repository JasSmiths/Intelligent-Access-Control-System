from fastapi import APIRouter, Depends, status

from app.modules.lpr.ubiquiti import UbiquitiLprAdapter, UbiquitiLprPayload
from app.services.access_events import AccessEventService, get_access_event_service

router = APIRouter()


@router.post("/ubiquiti/lpr", status_code=status.HTTP_202_ACCEPTED)
async def receive_ubiquiti_lpr(
    payload: UbiquitiLprPayload,
    service: AccessEventService = Depends(get_access_event_service),
) -> dict[str, str]:
    """Accept Ubiquiti LPR webhooks.

    Phase 2 will replace the placeholder enqueue call with debounce and
    confidence-window resolution. The API route already depends on an adapter so
    Ubiquiti-specific payload handling stays out of core event logic.
    """

    read = UbiquitiLprAdapter().to_plate_read(payload)
    await service.enqueue_plate_read(read)
    return {"status": "accepted", "plate": read.registration_number}
