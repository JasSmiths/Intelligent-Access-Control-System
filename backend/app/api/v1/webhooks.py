import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError

from app.core.logging import get_logger
from app.modules.lpr.base import PlateRead
from app.modules.lpr.ubiquiti import (
    UbiquitiLprAdapter,
    UbiquitiLprPayload,
    extract_plate_smart_zone_evidence,
)
from app.modules.unifi_protect.client import UnifiProtectError
from app.services.access_events import AccessEventService, get_access_event_service
from app.services.event_bus import event_bus
from app.services.settings import get_runtime_config
from app.services.lpr_timing import get_lpr_timing_recorder
from app.services.telemetry import TELEMETRY_CATEGORY_WEBHOOKS_API, telemetry
from app.services.unifi_protect import get_unifi_protect_service
from app.services.vehicle_visual_detections import (
    get_vehicle_presence_tracker,
    get_vehicle_visual_detection_recorder,
)
from app.services.whatsapp_messaging import get_whatsapp_messaging_service, load_whatsapp_config

router = APIRouter()
logger = get_logger(__name__)

SMART_ZONE_EVIDENCE_PAYLOAD_KEY = "_iacs_smart_zone_evidence"


@router.get("/whatsapp", response_class=PlainTextResponse)
async def verify_whatsapp_webhook(request: Request) -> PlainTextResponse:
    """Handle Meta's WhatsApp webhook verification challenge."""

    config = await load_whatsapp_config()
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and challenge is not None and token and token == config.webhook_verify_token:
        logger.info("whatsapp_webhook_verified")
        return PlainTextResponse(challenge, status_code=status.HTTP_200_OK)
    logger.warning(
        "whatsapp_webhook_verification_failed",
        extra={"mode": mode, "has_challenge": challenge is not None, "verify_token_configured": bool(config.webhook_verify_token)},
    )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="WhatsApp webhook verification failed.")


@router.post("/whatsapp", status_code=status.HTTP_202_ACCEPTED)
async def receive_whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Accept WhatsApp Cloud API message and status webhooks."""

    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid WhatsApp webhook JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="WhatsApp webhook payload must be a JSON object.")

    service = get_whatsapp_messaging_service()
    config = await load_whatsapp_config()
    signature_header = request.headers.get("x-hub-signature-256")
    signature_verified = False
    unsigned_allowed = False
    if config.enabled and not config.app_secret:
        logger.warning("whatsapp_webhook_signature_secret_missing")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="WhatsApp app secret is required before incoming webhooks can be accepted.",
        )
    if config.app_secret:
        signature_verified = service.validate_signature(raw_body, signature_header, config.app_secret)
        if not signature_verified:
            logger.warning(
                "whatsapp_webhook_signature_invalid",
                extra={"signature_present": bool(signature_header)},
            )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid WhatsApp webhook signature.")
    else:
        unsigned_allowed = True

    telemetry.record_span(
        "Webhook payload received",
        category=TELEMETRY_CATEGORY_WEBHOOKS_API,
        attributes={"source": "whatsapp"},
        output_payload={
            "payload_shape": _payload_shape(payload),
            "signature_verified": signature_verified,
            "unsigned_allowed": unsigned_allowed,
        },
    )
    background_tasks.add_task(
        service.handle_webhook_payload,
        payload,
        signature_verified=signature_verified,
        unsigned_allowed=unsigned_allowed,
    )
    return {"status": "accepted"}


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
    zone_evidence = extract_plate_smart_zone_evidence(raw_payload, read.registration_number)
    raw_smart_zones = zone_evidence.smart_zones
    smart_zones = raw_smart_zones
    zone_resolution: dict[str, Any] = {}
    zone_resolution_error = ""
    if raw_smart_zones and any(_looks_like_numeric_zone_id(zone) for zone in raw_smart_zones):
        try:
            zone_resolution = await get_unifi_protect_service().resolve_lpr_smart_zone_names(
                raw_smart_zones,
                camera_identifier=zone_evidence.camera_identifier,
            )
            resolved_smart_zones = zone_resolution.get("smart_zones")
            if isinstance(resolved_smart_zones, list):
                smart_zones = [str(zone) for zone in resolved_smart_zones]
        except UnifiProtectError as exc:
            zone_resolution_error = str(exc)
    runtime = await get_runtime_config()
    zone_evidence_detail = _smart_zone_evidence_detail(
        read,
        smart_zones=smart_zones,
        raw_smart_zones=raw_smart_zones,
        configured_smart_zones=runtime.lpr_allowed_smart_zones,
        zone_evidence=zone_evidence,
        zone_resolution=zone_resolution,
        zone_resolution_error=zone_resolution_error,
    )
    read = _read_with_smart_zone_evidence_metadata(read, zone_evidence_detail)

    await get_vehicle_visual_detection_recorder().record_unifi_payload(
        raw_payload,
        registration_number=read.registration_number,
    )
    await get_vehicle_presence_tracker().record_unifi_payload(
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
            "smart_zones": smart_zones,
            "raw_smart_zones": raw_smart_zones,
            "configured_smart_zones": runtime.lpr_allowed_smart_zones,
            "smart_zone_evidence": {
                "present": zone_evidence.present,
                "explicit_empty": zone_evidence.explicit_empty,
                "source": zone_evidence.source,
                "camera_identifier": zone_evidence.camera_identifier,
                "camera_id": zone_resolution.get("camera_id"),
                "camera_name": zone_resolution.get("camera_name"),
                "resolution_error": zone_resolution_error,
            },
        },
    )
    await service.enqueue_plate_read(read)
    return {"status": "accepted", "plate": read.registration_number}


def _smart_zone_evidence_detail(
    read: PlateRead,
    *,
    smart_zones: list[str],
    raw_smart_zones: list[str],
    configured_smart_zones: list[str],
    zone_evidence: Any,
    zone_resolution: dict[str, Any],
    zone_resolution_error: str,
) -> dict[str, Any]:
    return {
        "registration_number": read.registration_number,
        "smart_zones": smart_zones,
        "raw_smart_zones": raw_smart_zones,
        "configured_smart_zones": configured_smart_zones,
        "smart_zone_evidence": {
            "present": zone_evidence.present,
            "explicit_empty": zone_evidence.explicit_empty,
            "source": zone_evidence.source,
            "camera_identifier": zone_evidence.camera_identifier,
            "camera_id": zone_resolution.get("camera_id"),
            "camera_name": zone_resolution.get("camera_name"),
            "resolution_error": zone_resolution_error,
        },
    }


def _read_with_smart_zone_evidence_metadata(
    read: PlateRead,
    detail: dict[str, Any],
) -> PlateRead:
    raw_payload = dict(read.raw_payload or {})
    raw_payload[SMART_ZONE_EVIDENCE_PAYLOAD_KEY] = {
        "smart_zones": detail.get("smart_zones") or [],
        "raw_smart_zones": detail.get("raw_smart_zones") or [],
        "configured_smart_zones": detail.get("configured_smart_zones") or [],
        "smart_zone_evidence": detail.get("smart_zone_evidence") or {},
    }
    return PlateRead(
        registration_number=read.registration_number,
        confidence=read.confidence,
        source=read.source,
        captured_at=read.captured_at,
        raw_payload=raw_payload,
    )


def _payload_shape(value: Any, depth: int = 0) -> Any:
    if depth >= 3:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(key): _payload_shape(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [_payload_shape(value[0], depth + 1)] if value else []
    return type(value).__name__


def _looks_like_numeric_zone_id(value: Any) -> bool:
    return str(value or "").strip().isdigit()


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
