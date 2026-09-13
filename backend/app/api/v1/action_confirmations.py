from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import admin_user
from app.db.session import get_db_session
from app.models import User
from app.modules.access_devices.base import resolve_legacy_cover_key
from app.services.access_devices import get_access_device_service
from app.services.action_confirmations import ActionConfirmationError, create_action_confirmation

router = APIRouter()


class ActionConfirmationCreateRequest(BaseModel):
    action: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)
    target_entity: str | None = Field(default=None, max_length=120)
    target_id: str | None = Field(default=None, max_length=160)
    target_label: str | None = Field(default=None, max_length=240)
    reason: str | None = Field(default=None, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post("")
async def create_confirmation(
    request: ActionConfirmationCreateRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        metadata = {key: value for key, value in request.metadata.items() if key != "hardware_plan"}
        if request.action == "gate.open":
            metadata["hardware_plan"] = await get_access_device_service().preview_gate_open(
                target_device_key=request.payload.get("target_device_key"),
            )
        elif request.action in {"cover.open", "cover.close"}:
            key = resolve_legacy_cover_key(entity_id=request.payload.get("entity_id"), target=request.payload.get("target"))
            if not key:
                raise ValueError("A configured garage door target is required.")
            metadata["hardware_plan"] = await get_access_device_service().preview_device_command(
                key, request.action.split(".")[1],
            )
        return await create_action_confirmation(
            session,
            user=user,
            action=request.action,
            payload=request.payload,
            target_entity=request.target_entity,
            target_id=request.target_id,
            target_label=request.target_label,
            reason=request.reason,
            metadata=metadata,
        )
    except ActionConfirmationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
