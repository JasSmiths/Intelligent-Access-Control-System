from difflib import SequenceMatcher
import re
from uuid import UUID

from pydantic import BaseModel, Field

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.confirmations import require_confirmed_action, send_confirmed_notification
from app.api.dependencies import admin_user, current_user
from app.db.session import get_db_session
from app.modules.dvla.vehicle_enquiry import DvlaVehicleEnquiryError, display_vehicle_record, normalize_registration_number
from app.models import Person, User
from app.modules.access_devices.registry import get_access_device_provider
from app.modules.access_devices.base import resolve_legacy_cover_key
from app.modules.home_assistant.covers import (
    cover_entity_state_payload,
    detected_garage_door_entities,
    detected_gate_entities,
    normalize_cover_entities,
)
from app.modules.home_assistant.client import (
    HomeAssistantClient as DefaultHomeAssistantClient,
    HomeAssistantError,
    HomeAssistantService,
    HomeAssistantState,
    get_home_assistant_client,
)
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError
from app.modules.notifications.apprise_client import (
    normalize_apprise_url,
    split_apprise_urls,
    summarize_apprise_url,
    validate_apprise_urls,
)
from app.services.dependency_updates import get_dependency_update_service
from app.services.access_devices import get_access_device_service
from app.services.dvla import lookup_vehicle_registration, normalize_vehicle_enquiry_response
from app.services.home_assistant import get_home_assistant_service
from app.services.gate_commands import GateCommandIntent, get_gate_command_coordinator
from app.services.maintenance import is_maintenance_mode_active
from app.services.notifications import get_notification_service
from app.services.settings import get_runtime_config, normalize_esphome_device_id, update_settings
from app.services.telemetry import (
    TELEMETRY_CATEGORY_CRUD,
    TELEMETRY_CATEGORY_INTEGRATIONS,
    actor_from_user,
    emit_audit_log,
    write_audit_log,
)

router = APIRouter()
HomeAssistantClient = DefaultHomeAssistantClient


def _home_assistant_client() -> DefaultHomeAssistantClient:
    if HomeAssistantClient is DefaultHomeAssistantClient:
        return get_home_assistant_client()
    return HomeAssistantClient()

async def _raise_if_maintenance_active() -> None:
    if await is_maintenance_mode_active():
        raise HTTPException(
            status_code=423,
            detail="Maintenance Mode is active. Automated actions are disabled.",
        )


async def _commit_if_supported(session: AsyncSession) -> None:
    commit = getattr(session, "commit", None)
    if commit is not None:
        await commit()


async def update_integration_settings(
    user: User,
    values: dict,
    *,
    before: dict,
    after: dict,
) -> None:
    try:
        await update_settings(values, user=user, source="integrations_endpoint")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if any(key.startswith("home_assistant_") for key in values):
        service = get_home_assistant_service()
        await service.stop()
        await service.start()
    if any(key.startswith("esphome_") or key.startswith("gate_") for key in values):
        await get_access_device_service().restart()
    if any(key.startswith(("home_assistant_", "esphome_", "apprise_")) for key in values):
        await get_dependency_update_service().sync_enrollment(reason="integration_settings_changed", user=user)


class GateOpenRequest(BaseModel):
    target_device_key: str | None = Field(default=None, min_length=1, max_length=120)
    reason: str = Field(default="Manual dashboard command", max_length=240)
    confirmation_token: str | None = Field(default=None, max_length=160)


class CoverCommandRequest(BaseModel):
    entity_id: str | None = Field(default=None, max_length=255)
    target: str | None = Field(default=None, max_length=120)
    action: str = Field(pattern="^(open|close)$")
    reason: str = Field(default="Manual dashboard command", max_length=240)
    confirmation_token: str | None = Field(default=None, max_length=160)


class AnnouncementRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    entity_id: str | None = None
    confirmation_token: str | None = Field(default=None, max_length=160)


class TestNotificationRequest(BaseModel):
    subject: str = Field(default="IACS test notification", max_length=120)
    severity: str = Field(default="info", max_length=40)
    message: str = Field(default="Notification integration test", max_length=500)
    confirmation_token: str | None = Field(default=None, max_length=160)


class TestHomeAssistantMobileNotificationRequest(BaseModel):
    service_name: str = Field(pattern=r"^notify\.mobile_app_[A-Za-z0-9_]+$", max_length=255)
    person_name: str = Field(default="this person", max_length=160)
    confirmation_token: str | None = Field(default=None, max_length=160)


class AddAppriseUrlRequest(BaseModel):
    url: str = Field(min_length=6, max_length=1200)
    confirmation_token: str | None = Field(default=None, max_length=160)


class ESPHomeDeviceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=6053, ge=1, le=65535)
    encryption_key: str | None = Field(default=None, max_length=512)
    timeout_seconds: float = Field(default=30.0, ge=5.0, le=120.0)
    enabled: bool = True
    confirmation_token: str | None = Field(default=None, max_length=160)


class ESPHomeDevicePatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    encryption_key: str | None = Field(default=None, max_length=512)
    timeout_seconds: float | None = Field(default=None, ge=5.0, le=120.0)
    enabled: bool | None = None
    confirmation_token: str | None = Field(default=None, max_length=160)


class IntegrationConfirmationRequest(BaseModel):
    confirmation_token: str | None = Field(default=None, max_length=160)


class DvlaLookupRequest(BaseModel):
    registration_number: str = Field(min_length=1, max_length=20)


@router.get("/home-assistant/status")
async def home_assistant_status(refresh: bool = False, _: User = Depends(current_user)) -> dict:
    return await get_home_assistant_service().status(refresh=refresh)


@router.get("/gate/status")
async def gate_status(refresh: bool = False, _: User = Depends(current_user)) -> dict:
    return await get_access_device_service().status(refresh=refresh)


@router.get("/esphome/devices")
async def esphome_devices(_: User = Depends(admin_user)) -> dict:
    config = await get_runtime_config()
    return {"devices": [_esphome_device_summary(device) for device in config.esphome_devices]}


@router.post("/esphome/devices")
async def add_esphome_device(
    request: ESPHomeDeviceRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    confirmation_payload = request.model_dump(
        mode="json",
        exclude={"confirmation_token"},
        exclude_none=True,
        exclude_unset=True,
    )
    await require_confirmed_action(
        session,
        user=user,
        action="esphome.device.create",
        payload=confirmation_payload,
        confirmation_token=request.confirmation_token,
    )
    config = await get_runtime_config()
    devices = [dict(device) for device in config.esphome_devices]
    device_id = _unique_esphome_device_id(request.name, devices)
    devices.append(_esphome_device_from_request(device_id, request))
    await update_integration_settings(
        user,
        {"esphome_devices": devices},
        before={"esphome_devices": [_esphome_device_summary(device) for device in config.esphome_devices]},
        after={"esphome_devices": [_esphome_device_summary(device) for device in devices]},
    )
    return {"devices": [_esphome_device_summary(device) for device in devices]}


@router.patch("/esphome/devices/{device_id}")
async def update_esphome_device(
    device_id: str,
    request: ESPHomeDevicePatchRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    confirmation_payload = request.model_dump(
        mode="json",
        exclude={"confirmation_token"},
        exclude_none=True,
        exclude_unset=True,
    )
    confirmation_payload["device_id"] = device_id
    await require_confirmed_action(
        session,
        user=user,
        action="esphome.device.update",
        payload=confirmation_payload,
        confirmation_token=request.confirmation_token,
    )
    config = await get_runtime_config()
    devices = [dict(device) for device in config.esphome_devices]
    index = _find_esphome_device_index(devices, device_id)
    if index is None:
        raise HTTPException(status_code=404, detail="ESPHome device not found.")
    before = [_esphome_device_summary(device) for device in devices]
    device = devices[index]
    updates = request.model_dump(exclude={"confirmation_token"}, exclude_unset=True)
    if "name" in updates:
        device["name"] = str(updates["name"] or "").strip()
    if "host" in updates:
        device["host"] = str(updates["host"] or "").strip()
    if "port" in updates and updates["port"] is not None:
        device["port"] = int(updates["port"])
    if "encryption_key" in updates and updates["encryption_key"] is not None:
        device["encryption_key"] = str(updates["encryption_key"] or "")
    if "timeout_seconds" in updates and updates["timeout_seconds"] is not None:
        device["timeout_seconds"] = float(updates["timeout_seconds"])
    if "enabled" in updates and updates["enabled"] is not None:
        device["enabled"] = bool(updates["enabled"])
    devices[index] = device
    await update_integration_settings(
        user,
        {"esphome_devices": devices},
        before={"esphome_devices": before},
        after={"esphome_devices": [_esphome_device_summary(row) for row in devices]},
    )
    return {"devices": [_esphome_device_summary(row) for row in devices]}


@router.delete("/esphome/devices/{device_id}")
async def remove_esphome_device(
    device_id: str,
    request: IntegrationConfirmationRequest | None = Body(default=None),
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    await require_confirmed_action(
        session,
        user=user,
        action="esphome.device.delete",
        payload={"device_id": device_id},
        confirmation_token=request.confirmation_token if request else None,
    )
    config = await get_runtime_config()
    devices = [dict(device) for device in config.esphome_devices]
    index = _find_esphome_device_index(devices, device_id)
    if index is None:
        raise HTTPException(status_code=404, detail="ESPHome device not found.")
    before = [_esphome_device_summary(device) for device in devices]
    devices.pop(index)
    await update_integration_settings(
        user,
        {"esphome_devices": devices},
        before={"esphome_devices": before},
        after={"esphome_devices": [_esphome_device_summary(device) for device in devices]},
    )
    return {"devices": [_esphome_device_summary(device) for device in devices]}


@router.get("/esphome/status")
async def esphome_status(refresh: bool = False, _: User = Depends(current_user)) -> dict:
    status = await get_access_device_provider("esphome").status(refresh=refresh)
    return status.__dict__


@router.post("/esphome/devices/{device_id}/test")
async def test_esphome_device(
    device_id: str,
    request: IntegrationConfirmationRequest | None = Body(default=None),
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    await require_confirmed_action(
        session,
        user=user,
        action="esphome.device.test",
        payload={"device_id": device_id},
        confirmation_token=request.confirmation_token if request else None,
    )
    try:
        provider = get_access_device_provider("esphome")
        verify_live_device = getattr(provider, "verify_live_device", None)
        entities = (
            await verify_live_device(device_id)
            if verify_live_device is not None
            else await provider.discover_covers(device_id=device_id)
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"ok": True, "cover_count": len(entities), "stream": "live"}


@router.get("/esphome/entities")
async def esphome_entities(device_id: str | None = None, _: User = Depends(admin_user)) -> dict:
    try:
        entities = await get_access_device_provider("esphome").discover_covers(device_id=device_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "cover_entities": [
            _serialize_esphome_entity(entity)
            for entity in entities
        ],
        "gate_suggestions": [
            {**_serialize_esphome_entity(entity), "enabled": True}
            for entity in entities
            if entity.kind == "gate"
        ],
        "garage_door_suggestions": [
            {**_serialize_esphome_entity(entity), "enabled": True}
            for entity in entities
            if entity.kind == "garage_door"
        ],
    }


@router.get("/home-assistant/entities")
async def home_assistant_entities(
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    try:
        client = _home_assistant_client()
        states = await client.list_states()
        services = await client.list_services()
    except HomeAssistantError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    cover_entities = [_serialize_ha_entity(state) for state in states if state.entity_id.startswith("cover.")]
    input_boolean_entities = [
        _serialize_ha_entity(state) for state in states if state.entity_id.startswith("input_boolean.")
    ]
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
        "input_boolean_entities": input_boolean_entities,
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
async def auto_detect_home_assistant_gates(
    request: IntegrationConfirmationRequest | None = Body(default=None),
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    await require_confirmed_action(
        session,
        user=user,
        action="home_assistant.gates.auto_detect",
        payload={},
        confirmation_token=request.confirmation_token if request else None,
    )
    try:
        states = await _home_assistant_client().list_states()
    except HomeAssistantError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    detected = detected_gate_entities(states)
    config = await get_runtime_config()
    merged = _merge_cover_entities(
        config.home_assistant_gate_entities,
        detected,
        default_open_service=config.home_assistant_gate_open_service,
    )
    before = {"home_assistant_gate_entities": config.home_assistant_gate_entities}
    after = {"home_assistant_gate_entities": merged}
    await update_integration_settings(
        user,
        {"home_assistant_gate_entities": merged},
        before=before,
        after=after,
    )
    return {"gate_entities": [cover_entity_state_payload(entity) for entity in merged]}


@router.post("/home-assistant/garage-doors/auto-detect")
async def auto_detect_home_assistant_garage_doors(
    request: IntegrationConfirmationRequest | None = Body(default=None),
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    await require_confirmed_action(
        session,
        user=user,
        action="home_assistant.garage_doors.auto_detect",
        payload={},
        confirmation_token=request.confirmation_token if request else None,
    )
    try:
        states = await _home_assistant_client().list_states()
    except HomeAssistantError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    detected = detected_garage_door_entities(states)
    config = await get_runtime_config()
    merged = _merge_cover_entities(
        config.home_assistant_garage_door_entities,
        detected,
        default_open_service=config.home_assistant_gate_open_service,
    )
    before = {"home_assistant_garage_door_entities": config.home_assistant_garage_door_entities}
    after = {"home_assistant_garage_door_entities": merged}
    await update_integration_settings(
        user,
        {"home_assistant_garage_door_entities": merged},
        before=before,
        after=after,
    )
    return {"garage_door_entities": [cover_entity_state_payload(entity) for entity in merged]}


@router.get("/apprise/urls")
async def apprise_urls(_: User = Depends(admin_user)) -> dict:
    config = await get_runtime_config()
    urls = [normalize_apprise_url(url) for url in split_apprise_urls(config.apprise_urls)]
    return {"urls": [summarize_apprise_url(index, url) for index, url in enumerate(urls)]}


@router.post("/apprise/urls")
async def add_apprise_url(
    request: AddAppriseUrlRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    normalized = normalize_apprise_url(request.url.strip())
    await require_confirmed_action(
        session,
        user=user,
        action="apprise.url.create",
        payload={"url": normalized},
        confirmation_token=request.confirmation_token,
    )
    try:
        validate_apprise_urls(normalized)
    except NotificationDeliveryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    config = await get_runtime_config()
    urls = [normalize_apprise_url(url) for url in split_apprise_urls(config.apprise_urls)]
    if normalized not in urls:
        before = {"apprise_urls": config.apprise_urls}
        urls.append(normalized)
        next_urls = "\n".join(urls)
        await update_integration_settings(
            user,
            {"apprise_urls": next_urls},
            before=before,
            after={"apprise_urls": next_urls},
        )
    return {"urls": [summarize_apprise_url(index, url) for index, url in enumerate(urls)]}


@router.delete("/apprise/urls/{index}")
async def remove_apprise_url(
    index: int,
    request: IntegrationConfirmationRequest | None = Body(default=None),
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    await require_confirmed_action(
        session,
        user=user,
        action="apprise.url.delete",
        payload={"index": index},
        confirmation_token=request.confirmation_token if request else None,
    )
    config = await get_runtime_config()
    urls = [normalize_apprise_url(url) for url in split_apprise_urls(config.apprise_urls)]
    if index < 0 or index >= len(urls):
        raise HTTPException(status_code=404, detail="Apprise URL not found.")
    before = {"apprise_urls": config.apprise_urls}
    urls.pop(index)
    next_urls = "\n".join(urls)
    await update_integration_settings(
        user,
        {"apprise_urls": next_urls},
        before=before,
        after={"apprise_urls": next_urls},
    )
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


@router.post("/gate/open", response_model=None)
async def open_gate(
    request: GateOpenRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict | JSONResponse:
    await _raise_if_maintenance_active()
    if not request.confirmation_token:
        raise HTTPException(status_code=428, detail="Server-side confirmation is required for this action.")
    try:
        plan = await get_access_device_service().preview_gate_open(target_device_key=request.target_device_key)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    confirmation = await require_confirmed_action(
        session,
        user=user,
        action="gate.open",
        payload=request.model_dump(exclude={"confirmation_token"}, exclude_none=True),
        confirmation_token=request.confirmation_token,
        expected_hardware_plan=plan,
    )
    result = await get_gate_command_coordinator().execute_open(
        GateCommandIntent(
            reason=request.reason,
            source="manual_admin",
            actor=actor_from_user(user),
            actor_user_id=str(user.id), auth_version=user.auth_session_version,
            metadata={"actor_user_id": str(user.id), "actor_auth_session_version": user.auth_session_version},
            intent_id=str(confirmation.id),
            idempotency_key=str(confirmation.id),
            target_device_key=request.target_device_key,
            target_plan=plan,
            require_admission=False,
            expires_at=confirmation.expires_at,
        )
    )
    receipt = {
        "accepted": result.accepted,
        "state": result.state.value,
        "detail": result.detail,
        "intent_id": result.intent.intent_id,
        "command_id": result.command_id,
        "mechanically_confirmed": result.mechanically_confirmed,
        "requires_reconciliation": result.requires_reconciliation,
        "delivery": result.delivery,
        "admission_verified": result.admission_verified,
        "target_receipts": result.target_receipts,
    }
    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_INTEGRATIONS,
        action="gate.open",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="Gate",
        target_label=request.target_device_key or "All configured access gates",
        outcome="uncertain" if result.delivery == "unknown" else "success" if result.accepted else "failed",
        level="warning" if result.delivery == "unknown" else "info" if result.accepted else "error",
        metadata={"reason": request.reason, **receipt},
    )
    await _commit_if_supported(session)
    if not result.accepted:
        # Keep the legacy HTTP status/detail while exposing the durable outcome.
        return JSONResponse(status_code=503, content={**receipt, "detail": result.detail or "Gate command failed."})
    return receipt


@router.get("/gate/commands/{command_id}")
async def gate_command_receipt(command_id: UUID, response: Response, user: User = Depends(admin_user)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    receipt = await get_gate_command_coordinator().get_receipt(command_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Gate command not found.")
    return receipt


@router.get("/gate/commands")
async def gate_command_receipt_by_intent(
    response: Response, intent_id: UUID | None = None, before_id: UUID | None = None,
    limit: int = Query(default=25, ge=1, le=100), user: User = Depends(admin_user),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    if intent_id is None:
        try:
            return await get_gate_command_coordinator().list_receipts(limit=limit, before_id=before_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    receipt = await get_gate_command_coordinator().get_receipt(intent_id=str(intent_id))
    if receipt is None:
        raise HTTPException(status_code=404, detail="No recorded gate command is available for this intent.")
    return receipt


@router.get("/cover/commands/{command_id}")
async def cover_command_receipt(command_id: UUID, response: Response, user: User = Depends(admin_user)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    receipt = await get_access_device_service().command_receipt(command_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Device command not found.")
    return receipt


@router.get("/cover/commands")
async def cover_command_receipt_by_intent(
    response: Response, intent_id: UUID | None = None, before_id: UUID | None = None,
    limit: int = Query(default=25, ge=1, le=100), user: User = Depends(admin_user),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    if intent_id is None:
        try:
            return await get_access_device_service().list_command_receipts(limit=limit, before_id=before_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    receipt = await get_access_device_service().command_receipt(intent_id=str(intent_id))
    if receipt is None:
        raise HTTPException(status_code=404, detail="No recorded device command is available for this intent.")
    return receipt


@router.post("/cover/command", response_model=None)
async def cover_command(
    request: CoverCommandRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict | JSONResponse:
    await _raise_if_maintenance_active()
    device_key = resolve_legacy_cover_key(entity_id=request.entity_id, target=request.target)
    if not device_key:
        raise HTTPException(status_code=400, detail="A configured garage door entity is required.")

    devices = {
        device.key: device
        for device in await get_access_device_service().list_devices(kind="garage_door", enabled_only=True)
    }
    device = devices.get(device_key)
    if not device:
        raise HTTPException(status_code=404, detail="Garage door entity is not configured.")

    try:
        plan = await get_access_device_service().preview_device_command(device.key, request.action)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    confirmation_payload = request.model_dump(exclude={"confirmation_token"}, exclude_none=True)
    confirmation = await require_confirmed_action(
        session,
        user=user,
        action=f"cover.{request.action}",
        payload=confirmation_payload,
        confirmation_token=request.confirmation_token,
        expected_hardware_plan=plan,
    )

    outcome = await get_access_device_service().command_device(
        device.key,
        request.action,
        request.reason,
        schedule_source="garage_door",
        actor_user_id=str(user.id), auth_version=user.auth_session_version,
        intent_id=str(confirmation.id),
        idempotency_key=str(confirmation.id),
        target_plan=plan,
        expires_at=confirmation.expires_at,
    )
    receipt = {
        "accepted": outcome.accepted,
        "entity_id": device.key,
        "target": request.target or device.key,
        "action": request.action,
        "state": outcome.state.value,
        "detail": outcome.detail or request.reason,
        "used_provider": outcome.used_provider,
        "failover_used": outcome.failover_used,
        "verified": outcome.verified,
        "delivery": outcome.delivery,
        "requires_reconciliation": outcome.requires_reconciliation,
        "command_id": outcome.metadata.get("command_id"),
        "target_receipt": outcome.metadata.get("target_receipt"),
    }
    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_INTEGRATIONS,
        action=f"cover.{request.action}",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="Cover",
        target_id=device.key,
        target_label=device.name,
        outcome="uncertain" if outcome.delivery == "unknown" else "success" if outcome.accepted else "failed",
        level="warning" if outcome.delivery == "unknown" else "info" if outcome.accepted else "error",
        metadata={"reason": request.reason, **receipt},
    )
    await _commit_if_supported(session)
    if not outcome.accepted:
        return JSONResponse(status_code=503, content={**receipt, "detail": outcome.detail or "Garage door command failed."})
    return receipt


@router.post("/announcements/say")
async def say_announcement(
    request: AnnouncementRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    await _raise_if_maintenance_active()
    config = await get_runtime_config()
    target = request.entity_id or config.home_assistant_default_media_player
    if not target:
        raise HTTPException(status_code=400, detail="No media_player entity configured or supplied.")

    confirmation_payload = request.model_dump(exclude={"confirmation_token"}, exclude_none=True)
    result = await send_confirmed_notification(
        session, user=user, action="announcement.say", payload=confirmation_payload,
        confirmation_token=request.confirmation_token,
        context=NotificationContext(event_type="integration_test", subject="Announcement", severity="info",
                                    facts={"message": request.message}),
        direct_action={"type": "voice", "delivery_mode": "literal", "target": target,
                       "title": "Announcement", "message": request.message,
                       "configured_default": not bool(request.entity_id)},
    )
    return {"status": "sent", "entity_id": target, "notification_run_id": result.run_id}



@router.post("/home-assistant/mobile-notifications/test")
async def send_home_assistant_mobile_notification_test(
    request: TestHomeAssistantMobileNotificationRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    person_name = request.person_name.strip() or "this person"
    body = f"Mobile notifications are linked for {person_name}."
    result = await send_confirmed_notification(
        session, user=user, action="notification.mobile_test",
        payload=request.model_dump(exclude={"confirmation_token"}, exclude_none=True),
        confirmation_token=request.confirmation_token,
        context=NotificationContext(event_type="integration_test", subject="IACS Home Assistant test",
                                    severity="info", facts={"message": body}),
        direct_action={"type": "mobile", "delivery_mode": "literal", "target": request.service_name,
                       "title": "IACS Home Assistant test", "message": body},
    )
    return {"status": "sent", "service_name": request.service_name, "notification_run_id": result.run_id}



@router.post("/notifications/test")
async def send_test_notification(
    request: TestNotificationRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str | None]:
    config = await get_runtime_config()
    if not config.apprise_urls:
        raise HTTPException(status_code=400, detail="Apprise is not configured.")

    result = await send_confirmed_notification(
        session, user=user, action="notification.test",
        payload=request.model_dump(exclude={"confirmation_token"}, exclude_none=True),
        confirmation_token=request.confirmation_token,
        context=NotificationContext(event_type="integration_test", subject=request.subject,
                                    severity=request.severity, facts={"message": request.message}),
    )
    return {
        "status": "sent", "delivery_status": result.status, "notification_run_id": result.run_id,
        "title": result.notification.title, "body": result.notification.body,
    }



def _esphome_device_summary(device: dict) -> dict:
    return {
        "id": str(device.get("id") or ""),
        "name": str(device.get("name") or ""),
        "host": str(device.get("host") or ""),
        "port": int(device.get("port") or 6053),
        "timeout_seconds": float(device.get("timeout_seconds") or 30.0),
        "enabled": bool(device.get("enabled", True)),
        "encryption_key_configured": bool(str(device.get("encryption_key") or "").strip()),
    }


def _esphome_device_from_request(device_id: str, request: ESPHomeDeviceRequest) -> dict:
    return {
        "id": device_id,
        "name": request.name.strip(),
        "host": request.host.strip(),
        "port": request.port,
        "encryption_key": str(request.encryption_key or ""),
        "timeout_seconds": request.timeout_seconds,
        "enabled": request.enabled,
    }


def _find_esphome_device_index(devices: list[dict], device_id: str) -> int | None:
    for index, device in enumerate(devices):
        if str(device.get("id") or "") == device_id:
            return index
    return None


def _unique_esphome_device_id(name: str, devices: list[dict]) -> str:
    base = normalize_esphome_device_id(name) or "esphome_device"
    existing = {str(device.get("id") or "") for device in devices}
    if base not in existing:
        return base
    suffix = 2
    while f"{base}_{suffix}" in existing:
        suffix += 1
    return f"{base}_{suffix}"


def _serialize_esphome_entity(entity) -> dict:
    device_id = str(entity.metadata.get("device_id") or "")
    external_id = str(entity.metadata.get("external_id") or entity.external_id)
    entity_id = f"{device_id}:{external_id}" if device_id else external_id
    return {
        "entity_id": entity_id,
        "name": (
            f"{entity.metadata.get('device_name')} - {entity.name}"
            if entity.metadata.get("device_name")
            else entity.name
        ),
        "state": entity.state,
        "device_class": entity.metadata.get("device_class"),
        "kind": entity.kind,
        "metadata": {**entity.metadata, "external_id": external_id},
    }


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
