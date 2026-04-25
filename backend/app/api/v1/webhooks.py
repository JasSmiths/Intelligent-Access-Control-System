from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError

from app.core.logging import get_logger
from app.modules.lpr.ubiquiti import UbiquitiLprAdapter, UbiquitiLprPayload
from app.services.access_events import AccessEventService, get_access_event_service
from app.services.event_bus import event_bus

router = APIRouter()
logger = get_logger(__name__)


@router.post("/ubiquiti/lpr", status_code=status.HTTP_202_ACCEPTED)
async def receive_ubiquiti_lpr(
    request: Request,
    service: AccessEventService = Depends(get_access_event_service),
) -> dict[str, str]:
    """Accept Ubiquiti LPR webhooks.

    Phase 2 will replace the placeholder enqueue call with debounce and
    confidence-window resolution. The API route already depends on an adapter so
    Ubiquiti-specific payload handling stays out of core event logic.
    """

    raw_payload = await request.json()
    try:
        payload = UbiquitiLprPayload.model_validate(raw_payload)
    except ValidationError as exc:
        if _is_alarm_manager_test_payload(raw_payload):
            event_id = _alarm_manager_event_id(raw_payload) or "test"
            logger.info(
                "alarm_manager_test_webhook_received",
                extra={
                    "event_id": event_id,
                    "payload_shape": _payload_shape(raw_payload),
                },
            )
            await event_bus.publish(
                "webhook.test.received",
                {
                    "label": "Test Alarm Manager webhook",
                    "source": "ubiquiti_alarm_manager",
                    "event_id": event_id,
                },
            )
            return {"status": "accepted", "event": "Test Alarm Manager webhook"}

        logger.warning(
            "ubiquiti_lpr_payload_invalid",
            extra={
                "payload_shape": _payload_shape(raw_payload),
                "errors": exc.errors(include_url=False, include_input=False),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Ubiquiti LPR webhook did not include a recognizable plate.",
                "payload_shape": _payload_shape(raw_payload),
                "errors": exc.errors(include_url=False, include_input=False),
            },
        ) from exc

    read = UbiquitiLprAdapter().to_plate_read(payload)
    await service.enqueue_plate_read(read)
    return {"status": "accepted", "plate": read.registration_number}


def _payload_shape(value: Any, depth: int = 0) -> Any:
    if depth >= 3:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(key): _payload_shape(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [_payload_shape(value[0], depth + 1)] if value else []
    return type(value).__name__


def _is_alarm_manager_test_payload(payload: Any) -> bool:
    return _alarm_manager_event_id(payload) == "testEventId" or _has_trigger_device(payload, "FAKE_MAC")


def _alarm_manager_event_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    alarm = payload.get("alarm")
    if not isinstance(alarm, dict):
        return None
    event_path = str(alarm.get("eventPath") or "")
    if "testEventId" in event_path:
        return "testEventId"
    triggers = alarm.get("triggers")
    if not isinstance(triggers, list):
        return None
    for trigger in triggers:
        if isinstance(trigger, dict) and isinstance(trigger.get("eventId"), str):
            return trigger["eventId"]
    return None


def _has_trigger_device(payload: Any, device: str) -> bool:
    if not isinstance(payload, dict):
        return False
    alarm = payload.get("alarm")
    if not isinstance(alarm, dict):
        return False
    triggers = alarm.get("triggers")
    if not isinstance(triggers, list):
        return False
    return any(isinstance(trigger, dict) and trigger.get("device") == device for trigger in triggers)
