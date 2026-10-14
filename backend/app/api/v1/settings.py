from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.confirmations import require_confirmed_action
from app.api.dependencies import admin_user, current_user
from app.db.session import get_db_session
from app.models import User
from app.services.action_confirmations import ActionConfirmationError, consume_action_confirmation
from app.services.auth_secret_management import (
    AuthSecretRotationError,
    auth_secret_security_status,
    rotate_auth_secret,
)
from app.services.integration_testing import test_integration_connection
from app.services.settings import (
    UnknownDynamicSettingsError,
    get_runtime_config,
    get_runtime_config_for_session,
    list_settings,
    validate_dynamic_setting_keys,
)
from app.services.settings_activation import (
    SettingsActivationUnavailable,
    SettingsSaveReceipt,
    settings_activation,
)
from app.services.telemetry import (
    TELEMETRY_CATEGORY_INTEGRATIONS,
    actor_from_user,
    record_audit_log,
)

router = APIRouter()


class SettingsUpdateRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    confirmation_token: str | None = Field(default=None, max_length=160)


class ConnectionTestRequest(BaseModel):
    integration: str = Field(min_length=2, max_length=40)
    values: dict[str, Any] = Field(default_factory=dict)
    confirmation_token: str | None = Field(default=None, max_length=160)


class AuthSecretRotateRequest(BaseModel):
    confirmation_token: str | None = Field(default=None, max_length=160)
    new_secret: str | None = Field(default=None, max_length=512)


@router.get("")
async def get_settings(
    _: Annotated[User, Depends(current_user)], category: str | None = None
) -> list[dict[str, Any]]:
    return await list_settings(category)


@router.get("/runtime")
async def runtime_settings(_: Annotated[User, Depends(current_user)]) -> dict[str, Any]:
    config = await get_runtime_config()
    return {
        "app_name": config.app_name,
        "log_level": config.log_level,
        "site_timezone": config.site_timezone,
        "llm_provider": config.llm_provider,
    }


@router.get("/validation")
async def settings_validation_status(
    _: Annotated[User, Depends(admin_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str | bool]:
    try:
        await get_runtime_config_for_session(session)
    except ValueError as exc:
        return {"valid": False, "message": str(exc)}
    return {"valid": True, "message": "Current settings are valid."}


@router.get("/security/auth-secret")
async def get_auth_secret_status(_: Annotated[User, Depends(admin_user)]) -> dict[str, object]:
    return await auth_secret_security_status()


@router.post("/security/auth-secret/rotate")
async def rotate_auth_secret_endpoint(
    request: AuthSecretRotateRequest,
    user: Annotated[User, Depends(admin_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, object]:
    await require_confirmed_action(
        session,
        user=user,
        action="auth_secret.rotate",
        payload={"new_secret_provided": bool(request.new_secret)},
        confirmation_token=request.confirmation_token,
    )
    try:
        return await rotate_auth_secret(
            user=user,
            confirmed=True,
            new_secret=request.new_secret,
        )
    except AuthSecretRotationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("")
async def patch_settings(
    request: SettingsUpdateRequest,
    user: Annotated[User, Depends(admin_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SettingsSaveReceipt:
    try:
        validate_dynamic_setting_keys(request.values)
    except UnknownDynamicSettingsError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": str(exc),
                "unknown_keys": exc.unknown_keys,
                "allowed_keys": exc.allowed_keys,
            },
        ) from exc
    await require_confirmed_action(
        session,
        user=user,
        action="settings.update",
        payload={"values": request.values},
        confirmation_token=request.confirmation_token,
    )
    try:
        receipt = await settings_activation.save(request.values, user=user, source="settings_endpoint")
    except UnknownDynamicSettingsError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": str(exc),
                "unknown_keys": exc.unknown_keys,
                "allowed_keys": exc.allowed_keys,
            },
        ) from exc
    except SettingsActivationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return receipt


@router.post("/test")
async def test_connection(
    request: ConnectionTestRequest,
    user: Annotated[User, Depends(admin_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str | bool]:
    integration = request.integration.lower()
    values = request.values
    confirmation_payload = request.model_dump(exclude={"confirmation_token"}, exclude_none=True)
    try:
        await consume_action_confirmation(
            session,
            user=user,
            action="integration.test",
            payload=confirmation_payload,
            confirmation_token=request.confirmation_token,
        )
    except ActionConfirmationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    try:
        await test_integration_connection(integration, values)
    except Exception as exc:
        await record_audit_log(
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            action="integration.test",
            actor=actor_from_user(user),
            actor_user_id=user.id,
            target_entity="Integration",
            target_id=integration,
            target_label=integration,
            outcome="failed",
            level="error",
            metadata={"integration": integration, "error_class": type(exc).__name__},
        )
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=503, detail="Connection test failed. Review the selected provider configuration.") from exc
    await record_audit_log(
        category=TELEMETRY_CATEGORY_INTEGRATIONS,
        action="integration.test",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="Integration",
        target_id=integration,
        target_label=integration,
        metadata={"integration": integration},
    )
    return {"ok": True, "message": "Connection test succeeded."}
