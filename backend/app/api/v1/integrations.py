from difflib import SequenceMatcher
from datetime import UTC, datetime
import re

from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import admin_user, current_user
from app.db.session import get_db_session
from app.modules.dvla.vehicle_enquiry import DvlaVehicleEnquiryError, display_vehicle_record, normalize_registration_number
from app.modules.announcements.home_assistant_tts import AnnouncementTarget, HomeAssistantTtsAnnouncer
from app.modules.gate.home_assistant import HomeAssistantGateController
from app.models import Person, User
from app.modules.home_assistant.covers import (
    cover_entity_state_payload,
    command_cover,
    detected_garage_door_entities,
    detected_gate_entities,
    enabled_cover_entities,
    normalize_cover_entities,
)
from app.modules.home_assistant.client import (
    HomeAssistantClient,
    HomeAssistantError,
    HomeAssistantService,
    HomeAssistantState,
)
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError
from app.modules.notifications.apprise_client import (
    normalize_apprise_url,
    split_apprise_urls,
    summarize_apprise_url,
    validate_apprise_urls,
)
from app.modules.notifications.home_assistant_mobile import (
    HomeAssistantMobileAppNotifier,
    HomeAssistantMobileAppTarget,
)
from app.services.dvla import lookup_vehicle_registration, normalize_vehicle_enquiry_response
from app.services.home_assistant import get_home_assistant_service
from app.services.maintenance import is_maintenance_mode_active
from app.services.notifications import get_notification_service
from app.services.schedules import evaluate_schedule_id
from app.services.settings import get_runtime_config, update_settings
from app.services.telemetry import (
    TELEMETRY_CATEGORY_INTEGRATIONS,
    actor_from_user,
    emit_audit_log,
)

router = APIRouter()

GARAGE_COVER_ENTITIES = {
    "main_garage_door": "cover.main_garage_door",
    "mums_garage_door": "cover.mums_garage_door",
}


async def _raise_if_maintenance_active() -> None:
    if await is_maintenance_mode_active():
        raise HTTPException(
            status_code=423,
            detail="Maintenance Mode is active. Automated actions are disabled.",
        )


class GateOpenRequest(BaseModel):
    reason: str = Field(default="Manual dashboard command", max_length=240)


class CoverCommandRequest(BaseModel):
    entity_id: str | None = Field(default=None, max_length=255)
    target: str | None = Field(default=None, max_length=120)
    action: str = Field(pattern="^(open|close)$")
    reason: str = Field(default="Manual dashboard command", max_length=240)


class AnnouncementRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    entity_id: str | None = None


class TestNotificationRequest(BaseModel):
    subject: str = Field(default="IACS test notification", max_length=120)
    severity: str = Field(default="info", max_length=40)
    message: str = Field(default="Notification integration test", max_length=500)


class TestHomeAssistantMobileNotificationRequest(BaseModel):
    service_name: str = Field(pattern=r"^notify\.mobile_app_[A-Za-z0-9_]+$", max_length=255)
    person_name: str = Field(default="this person", max_length=160)


class AddAppriseUrlRequest(BaseModel):
    url: str = Field(min_length=6, max_length=1200)


class DvlaLookupRequest(BaseModel):
    registration_number: str = Field(min_length=1, max_length=20)


@router.get("/home-assistant/status")
async def home_assistant_status(refresh: bool = False, _: User = Depends(current_user)) -> dict:
    return await get_home_assistant_service().status(refresh=refresh)


@router.get("/home-assistant/entities")
async def home_assistant_entities(
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    try:
        client = HomeAssistantClient()
        states = await client.list_states()
        services = await client.list_services()
    except HomeAssistantError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    cover_entities = [_serialize_ha_entity(state) for state in states if state.entity_id.startswith("cover.")]
    media_players = [_serialize_ha_entity(state) for state in states if state.entity_id.startswith("media_player.")]
    mobile_app_notification_services = [
        _serialize_ha_service(service)
        for service in services
        if service.service_id.startswith("notify.mobile_app_")
    ]
    gate_suggestions = [cover_entity_state_payload(entity) for entity in detected_gate_entities(states)]
    garage_door_suggestions = [cover_entity_state_payload(entity) for entity in detected_garage_door_entities(states)]
    people = (
        await session.scalars(
            select(Person).where(Person.is_active.is_(True)).order_by(Person.first_name, Person.last_name)
        )
    ).all()

    return {
        "cover_entities": cover_entities,
        "gate_suggestions": gate_suggestions,
        "garage_door_suggestions": garage_door_suggestions,
        "media_player_entities": media_players,
        "mobile_app_notification_services": mobile_app_notification_services,
        "mobile_app_notification_mappings": [
            _suggest_mobile_app_notification_mapping(person, mobile_app_notification_services)
            for person in people
        ],
    }


@router.post("/home-assistant/gates/auto-detect")
async def auto_detect_home_assistant_gates(_: User = Depends(admin_user)) -> dict:
    try:
        states = await HomeAssistantClient().list_states()
    except HomeAssistantError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    detected = detected_gate_entities(states)
    config = await get_runtime_config()
    merged = _merge_cover_entities(
        config.home_assistant_gate_entities,
        detected,
        default_open_service=config.home_assistant_gate_open_service,
    )
    await update_settings({"home_assistant_gate_entities": merged})
    return {"gate_entities": [cover_entity_state_payload(entity) for entity in merged]}


@router.post("/home-assistant/garage-doors/auto-detect")
async def auto_detect_home_assistant_garage_doors(_: User = Depends(admin_user)) -> dict:
    try:
        states = await HomeAssistantClient().list_states()
    except HomeAssistantError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    detected = detected_garage_door_entities(states)
    config = await get_runtime_config()
    merged = _merge_cover_entities(
        config.home_assistant_garage_door_entities,
        detected,
        default_open_service=config.home_assistant_gate_open_service,
    )
    await update_settings({"home_assistant_garage_door_entities": merged})
    return {"garage_door_entities": [cover_entity_state_payload(entity) for entity in merged]}


@router.get("/apprise/urls")
async def apprise_urls(_: User = Depends(admin_user)) -> dict:
    config = await get_runtime_config()
    urls = [normalize_apprise_url(url) for url in split_apprise_urls(config.apprise_urls)]
    return {"urls": [summarize_apprise_url(index, url) for index, url in enumerate(urls)]}


@router.post("/apprise/urls")
async def add_apprise_url(request: AddAppriseUrlRequest, _: User = Depends(admin_user)) -> dict:
    normalized = normalize_apprise_url(request.url.strip())
    try:
        validate_apprise_urls(normalized)
    except NotificationDeliveryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    config = await get_runtime_config()
    urls = [normalize_apprise_url(url) for url in split_apprise_urls(config.apprise_urls)]
    if normalized not in urls:
        urls.append(normalized)
        await update_settings({"apprise_urls": "\n".join(urls)})
    return {"urls": [summarize_apprise_url(index, url) for index, url in enumerate(urls)]}


@router.delete("/apprise/urls/{index}")
async def remove_apprise_url(index: int, _: User = Depends(admin_user)) -> dict:
    config = await get_runtime_config()
    urls = [normalize_apprise_url(url) for url in split_apprise_urls(config.apprise_urls)]
    if index < 0 or index >= len(urls):
        raise HTTPException(status_code=404, detail="Apprise URL not found.")
    urls.pop(index)
    await update_settings({"apprise_urls": "\n".join(urls)})
    return {"urls": [summarize_apprise_url(row_index, url) for row_index, url in enumerate(urls)]}


@router.post("/dvla/lookup")
async def dvla_lookup(request: DvlaLookupRequest, user: User = Depends(current_user)) -> dict[str, object]:
    registration_number = normalize_registration_number(request.registration_number)
    try:
        vehicle = await lookup_vehicle_registration(registration_number)
    except DvlaVehicleEnquiryError as exc:
        emit_audit_log(
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            action="dvla.lookup",
            actor=actor_from_user(user),
            actor_user_id=user.id,
            target_entity="DVLA",
            target_id=registration_number,
            target_label=registration_number,
            outcome="failed",
            level="error",
            metadata={
                "registration_number": registration_number,
                "status_code": exc.status_code,
                "error": str(exc),
            },
        )
        status_code = exc.status_code if exc.status_code and exc.status_code >= 400 else 503
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    display_vehicle = display_vehicle_record(vehicle, registration_number)
    normalized_vehicle = normalize_vehicle_enquiry_response(
        vehicle,
        registration_number,
        display_vehicle=display_vehicle,
    )
    emit_audit_log(
        category=TELEMETRY_CATEGORY_INTEGRATIONS,
        action="dvla.lookup",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="DVLA",
        target_id=registration_number,
        target_label=display_vehicle,
        outcome="success",
        level="info",
        metadata={
            "registration_number": registration_number,
            "display_vehicle": display_vehicle,
        },
    )
    return {
        "registration_number": registration_number,
        "vehicle": vehicle,
        "display_vehicle": display_vehicle,
        "normalized_vehicle": normalized_vehicle.as_payload(),
    }


@router.post("/gate/open")
async def open_gate(request: GateOpenRequest, user: User = Depends(current_user)) -> dict:
    await _raise_if_maintenance_active()
    result = await HomeAssistantGateController().open_gate(request.reason)
    if not result.accepted:
        emit_audit_log(
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            action="gate.open",
            actor=actor_from_user(user),
            actor_user_id=user.id,
            target_entity="Gate",
            target_label="Home Assistant Gate",
            outcome="failed",
            level="error",
            metadata={"reason": request.reason, "detail": result.detail, "state": result.state.value},
        )
        raise HTTPException(status_code=503, detail=result.detail or "Gate command failed.")
    emit_audit_log(
        category=TELEMETRY_CATEGORY_INTEGRATIONS,
        action="gate.open",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="Gate",
        target_label="Home Assistant Gate",
        metadata={"reason": request.reason, "state": result.state.value, "detail": result.detail},
    )
    return {
        "accepted": result.accepted,
        "state": result.state.value,
        "detail": result.detail,
    }


@router.post("/cover/command")
async def cover_command(
    request: CoverCommandRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    await _raise_if_maintenance_active()
    entity_id = request.entity_id or (GARAGE_COVER_ENTITIES.get(request.target or "") if request.target else None)
    if not entity_id:
        raise HTTPException(status_code=400, detail="A configured garage door entity is required.")

    config = await get_runtime_config()
    configured_entities = {
        str(entity["entity_id"]): entity
        for entity in enabled_cover_entities(
            config.home_assistant_garage_door_entities,
            default_open_service=config.home_assistant_gate_open_service,
        )
    }
    entity = configured_entities.get(entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Garage door entity is not configured.")

    if request.action == "open":
        schedule_evaluation = await evaluate_schedule_id(
            session,
            entity.get("schedule_id"),
            datetime.now(tz=UTC),
            timezone_name=config.site_timezone,
            default_policy=config.schedule_default_policy,
            source="garage_door",
        )
        if not schedule_evaluation.allowed:
            raise HTTPException(
                status_code=403,
                detail=schedule_evaluation.reason or "Garage door is outside its assigned schedule.",
            )

    client = HomeAssistantClient()
    outcome = await command_cover(client, entity, request.action, request.reason)
    if not outcome.accepted:
        emit_audit_log(
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            action=f"cover.{request.action}",
            actor=actor_from_user(user),
            actor_user_id=user.id,
            target_entity="Cover",
            target_id=outcome.entity_id,
            target_label=outcome.name,
            outcome="failed",
            level="error",
            metadata={"reason": request.reason, "state": outcome.state, "detail": outcome.detail},
        )
        raise HTTPException(status_code=503, detail=outcome.detail or "Garage door command failed.")
    emit_audit_log(
        category=TELEMETRY_CATEGORY_INTEGRATIONS,
        action=f"cover.{request.action}",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="Cover",
        target_id=outcome.entity_id,
        target_label=outcome.name,
        metadata={"reason": request.reason, "state": outcome.state, "detail": outcome.detail},
    )
    return {
        "accepted": True,
        "entity_id": outcome.entity_id,
        "target": request.target or outcome.entity_id,
        "action": request.action,
        "state": outcome.state,
        "detail": request.reason,
    }


@router.post("/announcements/say")
async def say_announcement(request: AnnouncementRequest, user: User = Depends(current_user)) -> dict[str, str]:
    await _raise_if_maintenance_active()
    config = await get_runtime_config()
    target = request.entity_id or config.home_assistant_default_media_player
    if not target:
        raise HTTPException(status_code=400, detail="No media_player entity configured or supplied.")

    try:
        await HomeAssistantTtsAnnouncer().announce(AnnouncementTarget(target), request.message)
    except (HomeAssistantError, ValueError) as exc:
        emit_audit_log(
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            action="announcement.say",
            actor=actor_from_user(user),
            actor_user_id=user.id,
            target_entity="MediaPlayer",
            target_id=target,
            outcome="failed",
            level="error",
            metadata={"message": request.message, "error": str(exc)},
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    emit_audit_log(
        category=TELEMETRY_CATEGORY_INTEGRATIONS,
        action="announcement.say",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="MediaPlayer",
        target_id=target,
        metadata={"message": request.message},
    )
    return {"status": "sent", "entity_id": target}


@router.post("/home-assistant/mobile-notifications/test")
async def send_home_assistant_mobile_notification_test(
    request: TestHomeAssistantMobileNotificationRequest,
    user: User = Depends(current_user),
) -> dict[str, str]:
    person_name = request.person_name.strip() or "this person"
    try:
        await HomeAssistantMobileAppNotifier().send(
            HomeAssistantMobileAppTarget(request.service_name),
            "IACS Home Assistant test",
            f"Mobile notifications are linked for {person_name}.",
            NotificationContext(
                event_type="integration_test",
                subject="IACS Home Assistant test",
                severity="info",
                facts={"message": f"Mobile notifications are linked for {person_name}."},
            ),
        )
    except NotificationDeliveryError as exc:
        emit_audit_log(
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            action="notification.mobile_test",
            actor=actor_from_user(user),
            actor_user_id=user.id,
            target_entity="NotificationTarget",
            target_id=request.service_name,
            outcome="failed",
            level="error",
            metadata={"error": str(exc), "person_name": person_name},
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    emit_audit_log(
        category=TELEMETRY_CATEGORY_INTEGRATIONS,
        action="notification.mobile_test",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="NotificationTarget",
        target_id=request.service_name,
        metadata={"person_name": person_name},
    )
    return {"status": "sent", "service_name": request.service_name}


@router.post("/notifications/test")
async def send_test_notification(
    request: TestNotificationRequest,
    user: User = Depends(current_user),
) -> dict[str, str]:
    config = await get_runtime_config()
    if not config.apprise_urls:
        raise HTTPException(status_code=400, detail="Apprise is not configured.")

    try:
        notification = await get_notification_service().notify(
            NotificationContext(
                event_type="integration_test",
                subject=request.subject,
                severity=request.severity,
                facts={"message": request.message},
            ),
            raise_on_failure=True,
        )
    except NotificationDeliveryError as exc:
        emit_audit_log(
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            action="notification.test",
            actor=actor_from_user(user),
            actor_user_id=user.id,
            target_entity="Notification",
            target_label=request.subject,
            outcome="failed",
            level="error",
            metadata={"severity": request.severity, "error": str(exc)},
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    emit_audit_log(
        category=TELEMETRY_CATEGORY_INTEGRATIONS,
        action="notification.test",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="Notification",
        target_label=request.subject,
        metadata={"severity": request.severity},
    )
    return {"status": "sent", "title": notification.title, "body": notification.body}


def _serialize_ha_entity(state: HomeAssistantState) -> dict[str, str | None]:
    friendly_name = state.attributes.get("friendly_name")
    device_class = state.attributes.get("device_class")
    return {
        "entity_id": state.entity_id,
        "name": str(friendly_name) if friendly_name else _title_from_entity_id(state.entity_id),
        "state": state.state,
        "device_class": str(device_class) if device_class else None,
    }


def _serialize_ha_service(service: HomeAssistantService) -> dict[str, str | None]:
    return {
        "service_id": service.service_id,
        "name": service.name or _title_from_entity_id(service.service_id),
        "description": service.description,
    }


def _merge_cover_entities(
    existing: list[dict],
    detected: list[dict],
    *,
    default_open_service: str = "cover.open_cover",
) -> list[dict]:
    merged = normalize_cover_entities(existing, default_open_service=default_open_service)
    by_entity_id = {str(entity["entity_id"]): entity for entity in merged}
    for entity in normalize_cover_entities(detected, default_open_service=default_open_service):
        if entity["entity_id"] not in by_entity_id:
            merged.append(entity)
            by_entity_id[str(entity["entity_id"])] = entity
    return merged


def _suggest_mobile_app_notification_mapping(
    person: Person,
    mobile_services: list[dict[str, str | None]],
) -> dict:
    return _suggest_person_mapping(
        person,
        mobile_services,
        id_key="service_id",
        name_key="name",
        suggested_id_key="suggested_service_id",
        suggested_name_key="suggested_name",
    )


def _suggest_person_mapping(
    person: Person,
    candidates: list[dict[str, str | None]],
    *,
    id_key: str,
    name_key: str,
    suggested_id_key: str,
    suggested_name_key: str,
) -> dict:
    person_label = person.display_name or f"{person.first_name} {person.last_name}".strip()
    person_tokens = _name_tokens(person_label, person.first_name, person.last_name)
    best_entity: dict[str, str | None] | None = None
    best_score = 0.0

    for entity in candidates:
        entity_label = f"{entity.get(id_key, '')} {entity.get(name_key) or ''}"
        entity_tokens = _name_tokens(entity_label)
        token_score = len(person_tokens & entity_tokens) / max(len(person_tokens), 1)
        ratio_score = SequenceMatcher(None, " ".join(sorted(person_tokens)), " ".join(sorted(entity_tokens))).ratio()
        score = max(token_score, ratio_score)
        if score > best_score:
            best_score = score
            best_entity = entity

    return {
        "person_id": str(person.id),
        "first_name": person.first_name,
        "last_name": person.last_name,
        "display_name": person_label,
        suggested_id_key: best_entity[id_key] if best_entity and best_score >= 0.45 else None,
        suggested_name_key: best_entity[name_key] if best_entity and best_score >= 0.45 else None,
        "confidence": round(best_score, 2) if best_entity else 0,
    }


def _name_tokens(*values: str | None) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        if not value:
            continue
        cleaned = value.lower().replace("person.", " ").replace("notify.mobile_app_", " ")
        tokens.update(part for part in re.split(r"[^a-z0-9]+", cleaned) if part)
    return tokens


def _title_from_entity_id(entity_id: str) -> str:
    return entity_id.split(".", 1)[-1].replace("_", " ").title()
