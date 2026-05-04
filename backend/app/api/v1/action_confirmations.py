from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import admin_user
from app.db.session import get_db_session
from app.models import User
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
        return await create_action_confirmation(
            session,
            user=user,
            action=request.action,
            payload=request.payload,
            target_entity=request.target_entity,
            target_id=request.target_id,
            target_label=request.target_label,
            reason=request.reason,
            metadata=request.metadata,
        )
    except ActionConfirmationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
