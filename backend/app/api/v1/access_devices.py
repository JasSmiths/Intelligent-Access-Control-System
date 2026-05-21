from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.dependencies import admin_user, current_user
from app.models import User
from app.modules.access_devices.registry import get_access_device_provider
from app.services.access_devices import get_access_device_service
from app.services.telemetry import (
    TELEMETRY_CATEGORY_CRUD,
    actor_from_user,
    emit_audit_log,
)

router = APIRouter()


class AccessDeviceRequest(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    kind: Literal["gate", "garage_door"]
    name: str = Field(min_length=1, max_length=160)
    enabled: bool = True
    schedule_id: str | None = None
    open_for_access: bool = True
    sort_order: int = 0


class AccessDevicePatchRequest(BaseModel):
    key: str | None = Field(default=None, min_length=1, max_length=120)
    kind: Literal["gate", "garage_door"] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=160)
    enabled: bool | None = None
    schedule_id: str | None = None
    open_for_access: bool | None = None
    sort_order: int | None = None


class ProviderBindingRequest(BaseModel):
    external_id: str = Field(default="", max_length=255)
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


@router.get("")
async def list_access_devices(
    kind: Literal["gate", "garage_door"] | None = Query(default=None),
    _: User = Depends(current_user),
) -> list[dict[str, Any]]:
    devices = await get_access_device_service().list_devices(kind=kind)
    return [serialize_access_device(device) for device in devices]


@router.post("")
async def create_access_device(request: AccessDeviceRequest, user: User = Depends(admin_user)) -> dict[str, Any]:
    try:
        device = await get_access_device_service().create_device(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    emit_audit_log(
        category=TELEMETRY_CATEGORY_CRUD,
        action="access_device.create",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="AccessDevice",
        target_id=device.key,
        target_label=device.name,
        metadata={"kind": device.kind},
    )
    return serialize_access_device(device)


@router.patch("/{device_id}")
async def update_access_device(
    device_id: str,
    request: AccessDevicePatchRequest,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    payload = request.model_dump(exclude_unset=True)
    try:
        device = await get_access_device_service().update_device(device_id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    emit_audit_log(
        category=TELEMETRY_CATEGORY_CRUD,
        action="access_device.update",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="AccessDevice",
        target_id=device.key,
        target_label=device.name,
        metadata={"keys": sorted(payload.keys())},
    )
    return serialize_access_device(device)


@router.delete("/{device_id}")
async def delete_access_device(device_id: str, user: User = Depends(admin_user)) -> dict[str, bool]:
    try:
        await get_access_device_service().delete_device(device_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    emit_audit_log(
        category=TELEMETRY_CATEGORY_CRUD,
        action="access_device.delete",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="AccessDevice",
        target_id=device_id,
    )
    return {"ok": True}


@router.put("/{device_id}/bindings/{provider}")
async def upsert_access_device_binding(
    device_id: str,
    provider: str,
    request: ProviderBindingRequest,
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    try:
        get_access_device_provider(provider)
        device = await get_access_device_service().upsert_binding(device_id, provider, request.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    emit_audit_log(
        category=TELEMETRY_CATEGORY_CRUD,
        action="access_device.binding.update",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="AccessDevice",
        target_id=device.key,
        target_label=device.name,
        metadata={"provider": provider, "configured": bool(request.external_id.strip())},
    )
    return serialize_access_device(device)


@router.get("/status")
async def access_device_status(refresh: bool = False, _: User = Depends(current_user)) -> dict[str, Any]:
    return await get_access_device_service().status(refresh=refresh)


def serialize_access_device(device) -> dict[str, Any]:
    return {
        "id": device.key,
        "key": device.key,
        "kind": device.kind,
        "name": device.name,
        "enabled": device.enabled,
        "schedule_id": device.schedule_id,
        "open_for_access": device.open_for_access,
        "sort_order": device.sort_order,
        "bindings": [
            {
                "provider": binding.provider,
                "external_id": binding.external_id,
                "enabled": binding.enabled,
                "config": binding.config,
            }
            for binding in device.bindings.values()
        ],
    }
