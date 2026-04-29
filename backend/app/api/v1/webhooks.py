from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError

from app.core.logging import get_logger
from app.modules.lpr.ubiquiti import UbiquitiLprAdapter, UbiquitiLprPayload
from app.services.access_events import AccessEventService, get_access_event_service
from app.services.event_bus import event_bus
from app.services.lpr_timing import get_lpr_timing_recorder
from app.services.telemetry import TELEMETRY_CATEGORY_WEBHOOKS_API, telemetry
from app.services.vehicle_visual_detections import get_vehicle_visual_detection_recorder

router = APIRouter()
logger = get_logger(__name__)


@router.post("/ubiquiti/lpr", status_code=status.HTTP_202_ACCEPTED)
async def receive_ubiquiti_lpr(
    request: Request,
    service: AccessEventService = Depends(get_access_event_service),
) -> dict[str, str]:
    """Accept Ubiquiti LPR webhooks.

    The API route only validates and normalizes the vendor payload; debounce,
    confidence-window resolution, access decisions, and anomaly handling stay in
    the access event service.
    """

    raw_payload = await request.json()
    telemetry.record_span(
        "Webhook payload received",
        category=TELEMETRY_CATEGORY_WEBHOOKS_API,
        attributes={"source": "ubiquiti_lpr"},
        output_payload={"payload_shape": _payload_shape(raw_payload)},
    )
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
            telemetry.record_span(
                "Webhook test payload accepted",
                category=TELEMETRY_CATEGORY_WEBHOOKS_API,
                attributes={"source": "ubiquiti_alarm_manager"},
                output_payload={"event_id": event_id},
            )
            return {"status": "accepted", "event": "Test Alarm Manager webhook"}

        logger.warning(
            "ubiquiti_lpr_payload_invalid",
            extra={
                "payload_shape": _payload_shape(raw_payload),
                "errors": exc.errors(include_url=False, include_input=False),
            },
        )
        telemetry.record_span(
            "Webhook payload validation failed",
            category=TELEMETRY_CATEGORY_WEBHOOKS_API,
            status="error",
            output_payload={
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
    await get_lpr_timing_recorder().record_webhook_plate(read)
    await get_vehicle_visual_detection_recorder().record_unifi_payload(
        raw_payload,
        registration_number=read.registration_number,
    )
    telemetry.record_span(
        "Webhook payload normalized to PlateRead",
        category=TELEMETRY_CATEGORY_WEBHOOKS_API,
        output_payload={
            "registration_number": read.registration_number,
            "confidence": read.confidence,
            "source": read.source,
        },
    )
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
