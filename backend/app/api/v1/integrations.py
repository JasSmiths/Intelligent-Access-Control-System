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
from app.models import User
from app.modules.home_assistant.covers import (
    cover_entity_state_payload,
    command_cover,
    detected_garage_door_entities,
    detected_gate_entities,
    enabled_cover_entities,
    normalize_cover_entities,
)
from app.modules.home_assistant.client import HomeAssistantClient, HomeAssistantError, HomeAssistantState
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError
from app.modules.notifications.apprise_client import (
    normalize_apprise_url,
    split_apprise_urls,
    summarize_apprise_url,
    validate_apprise_urls,
)
from app.services.dvla import lookup_vehicle_registration
from app.services.home_assistant import get_home_assistant_service
from app.services.notifications import get_notification_service
from app.services.schedules import evaluate_schedule_id
from app.services.settings import get_runtime_config, update_settings

router = APIRouter()

GARAGE_COVER_ENTITIES = {
    "main_garage_door": "cover.main_garage_door",
    "mums_garage_door": "cover.mums_garage_door",
}


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


class AddAppriseUrlRequest(BaseModel):
    url: str = Field(min_length=6, max_length=1200)


class DvlaLookupRequest(BaseModel):
    registration_number: str = Field(min_length=1, max_length=20)


@router.get("/home-assistant/status")
async def home_assistant_status(_: User = Depends(current_user)) -> dict:
    return await get_home_assistant_service().status()


@router.get("/home-assistant/entities")
async def home_assistant_entities(
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    try:
        states = await HomeAssistantClient().list_states()
    except HomeAssistantError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    cover_entities = [_serialize_ha_entity(state) for state in states if state.entity_id.startswith("cover.")]
    media_players = [_serialize_ha_entity(state) for state in states if state.entity_id.startswith("media_player.")]
    person_entities = [_serialize_ha_entity(state) for state in states if state.entity_id.startswith("person.")]
    gate_suggestions = [cover_entity_state_payload(entity) for entity in detected_gate_entities(states)]
    garage_door_suggestions = [cover_entity_state_payload(entity) for entity in detected_garage_door_entities(states)]
    users = (await session.scalars(select(User).where(User.is_active.is_(True)).order_by(User.first_name, User.last_name))).all()

    return {
        "cover_entities": cover_entities,
        "gate_suggestions": gate_suggestions,
        "garage_door_suggestions": garage_door_suggestions,
        "media_player_entities": media_players,
        "person_entities": person_entities,
        "presence_mappings": [
            _suggest_presence_mapping(user, person_entities)
            for user in users
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
async def dvla_lookup(request: DvlaLookupRequest, _: User = Depends(current_user)) -> dict[str, object]:
    registration_number = normalize_registration_number(request.registration_number)
    try:
        vehicle = await lookup_vehicle_registration(registration_number)
    except DvlaVehicleEnquiryError as exc:
        status_code = exc.status_code if exc.status_code and exc.status_code >= 400 else 503
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {
        "registration_number": registration_number,
        "vehicle": vehicle,
        "display_vehicle": display_vehicle_record(vehicle, registration_number),
    }


@router.post("/gate/open")
async def open_gate(request: GateOpenRequest, _: User = Depends(current_user)) -> dict:
    result = await HomeAssistantGateController().open_gate(request.reason)
    if not result.accepted:
        raise HTTPException(status_code=503, detail=result.detail or "Gate command failed.")
    return {
        "accepted": result.accepted,
        "state": result.state.value,
        "detail": result.detail,
    }


@router.post("/cover/command")
async def cover_command(
    request: CoverCommandRequest,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
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
        raise HTTPException(status_code=503, detail=outcome.detail or "Garage door command failed.")
    return {
        "accepted": True,
        "entity_id": outcome.entity_id,
        "target": request.target or outcome.entity_id,
        "action": request.action,
        "state": outcome.state,
        "detail": request.reason,
    }


@router.post("/announcements/say")
async def say_announcement(request: AnnouncementRequest, _: User = Depends(current_user)) -> dict[str, str]:
    config = await get_runtime_config()
    target = request.entity_id or config.home_assistant_default_media_player
    if not target:
        raise HTTPException(status_code=400, detail="No media_player entity configured or supplied.")

    try:
        await HomeAssistantTtsAnnouncer().announce(AnnouncementTarget(target), request.message)
    except (HomeAssistantError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {"status": "sent", "entity_id": target}


@router.post("/notifications/test")
async def send_test_notification(
    request: TestNotificationRequest,
    _: User = Depends(current_user),
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
        raise HTTPException(status_code=503, detail=str(exc)) from exc
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


def _suggest_presence_mapping(user: User, person_entities: list[dict[str, str | None]]) -> dict:
    user_label = user.full_name or f"{user.first_name} {user.last_name}".strip() or user.username
    user_tokens = _name_tokens(user_label, user.username)
    best_entity: dict[str, str | None] | None = None
    best_score = 0.0

    for entity in person_entities:
        entity_label = f"{entity.get('entity_id', '')} {entity.get('name') or ''}"
        entity_tokens = _name_tokens(entity_label)
        token_score = len(user_tokens & entity_tokens) / max(len(user_tokens), 1)
        ratio_score = SequenceMatcher(None, " ".join(sorted(user_tokens)), " ".join(sorted(entity_tokens))).ratio()
        score = max(token_score, ratio_score)
        if score > best_score:
            best_score = score
            best_entity = entity

    return {
        "user_id": str(user.id),
        "username": user.username,
        "full_name": user_label,
        "suggested_entity_id": best_entity["entity_id"] if best_entity and best_score >= 0.45 else None,
        "suggested_name": best_entity["name"] if best_entity and best_score >= 0.45 else None,
        "confidence": round(best_score, 2) if best_entity else 0,
    }


def _name_tokens(*values: str | None) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        if not value:
            continue
        cleaned = value.lower().replace("person.", " ")
        tokens.update(part for part in re.split(r"[^a-z0-9]+", cleaned) if part)
    return tokens


def _title_from_entity_id(entity_id: str) -> str:
    return entity_id.split(".", 1)[-1].replace("_", " ").title()
