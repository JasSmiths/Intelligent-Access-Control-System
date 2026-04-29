from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.dependencies import admin_user, current_user
from app.models import User
from app.modules.notifications.apprise_client import validate_apprise_urls
from app.services.dependency_updates import get_dependency_update_service
from app.services.dvla import test_vehicle_enquiry_connection
from app.services.home_assistant import get_home_assistant_service
from app.services.settings import get_runtime_config, list_settings, update_settings
from app.services.telemetry import (
    TELEMETRY_CATEGORY_CRUD,
    TELEMETRY_CATEGORY_INTEGRATIONS,
    actor_from_user,
    audit_diff,
    emit_audit_log,
)
from app.services.unifi_protect import get_unifi_protect_service

router = APIRouter()


class SettingsUpdateRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class ConnectionTestRequest(BaseModel):
    integration: str = Field(min_length=2, max_length=40)
    values: dict[str, Any] = Field(default_factory=dict)


@router.get("")
async def get_settings(
    category: str | None = None,
    _: User = Depends(current_user),
) -> list[dict[str, Any]]:
    return await list_settings(category)


@router.get("/runtime")
async def runtime_settings(_: User = Depends(current_user)) -> dict[str, Any]:
    config = await get_runtime_config()
    return {
        "app_name": config.app_name,
        "log_level": config.log_level,
        "site_timezone": config.site_timezone,
        "llm_provider": config.llm_provider,
    }


@router.patch("")
async def patch_settings(
    request: SettingsUpdateRequest,
    user: User = Depends(admin_user),
) -> list[dict[str, Any]]:
    before = {row["key"]: row["value"] for row in await list_settings()}
    rows = await update_settings(request.values)
    after = {row["key"]: row["value"] for row in rows}
    changed = {key: after.get(key) for key in request.values if before.get(key) != after.get(key)}
    if changed:
        emit_audit_log(
            category=TELEMETRY_CATEGORY_CRUD,
            action="settings.update",
            actor=actor_from_user(user),
            actor_user_id=user.id,
            target_entity="SystemSetting",
            target_label=", ".join(sorted(changed.keys())[:8]),
            diff=audit_diff(
                {key: before.get(key) for key in changed},
                {key: after.get(key) for key in changed},
            ),
            metadata={"keys": sorted(changed.keys())},
        )
    if any(key.startswith("home_assistant_") for key in request.values):
        service = get_home_assistant_service()
        await service.stop()
        await service.start()
    if any(key.startswith("unifi_protect_") for key in request.values):
        await get_unifi_protect_service().restart()
    if any(
        key.startswith(("home_assistant_", "apprise_", "dvla_", "unifi_protect_", "llm_", "openai_", "gemini_", "anthropic_", "ollama_"))
        for key in request.values
    ):
        await get_dependency_update_service().sync_enrollment(reason="integration_settings_changed", user=user)
    return rows


@router.post("/test")
async def test_connection(
    request: ConnectionTestRequest,
    user: User = Depends(admin_user),
) -> dict[str, str | bool]:
    integration = request.integration.lower()
    values = request.values
    try:
        if integration == "home_assistant":
            await _test_home_assistant(values)
        elif integration == "apprise":
            await _test_apprise(values)
        elif integration == "dvla":
            await _test_dvla(values)
        elif integration == "unifi_protect":
            await _test_unifi_protect(values)
        elif integration == "openai":
            await _test_openai(values)
        elif integration == "gemini":
            await _test_gemini(values)
        elif integration in {"anthropic", "claude"}:
            await _test_anthropic(values)
        elif integration == "ollama":
            await _test_ollama(values)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown integration: {request.integration}")
    except Exception as exc:
        emit_audit_log(
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            action="integration.test",
            actor=actor_from_user(user),
            actor_user_id=user.id,
            target_entity="Integration",
            target_id=integration,
            target_label=integration,
            outcome="failed",
            level="error",
            metadata={"integration": integration, "error": str(exc)},
        )
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    emit_audit_log(
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


async def _test_home_assistant(values: dict[str, Any]) -> None:
    base_url = str(values.get("home_assistant_url") or "").rstrip("/")
    token = str(values.get("home_assistant_token") or "")
    if not base_url or not token:
        raise ValueError("Home Assistant URL and token are required.")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{base_url}/api/", headers={"Authorization": f"Bearer {token}"})
    _raise_for_test(response, "Home Assistant")


async def _test_apprise(values: dict[str, Any]) -> None:
    urls = str(values.get("apprise_urls") or "").strip()
    if not urls:
        urls = (await get_runtime_config()).apprise_urls.strip()
    validate_apprise_urls(urls)


async def _test_dvla(values: dict[str, Any]) -> None:
    await test_vehicle_enquiry_connection(values)


async def _test_unifi_protect(values: dict[str, Any]) -> None:
    await get_unifi_protect_service().test_connection(values)


async def _test_openai(values: dict[str, Any]) -> None:
    base_url = str(values.get("openai_base_url") or "https://api.openai.com/v1").rstrip("/")
    api_key = str(values.get("openai_api_key") or "")
    if not api_key:
        raise ValueError("OpenAI API key is required.")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{base_url}/models", headers={"Authorization": f"Bearer {api_key}"})
    _raise_for_test(response, "OpenAI")


async def _test_gemini(values: dict[str, Any]) -> None:
    base_url = str(values.get("gemini_base_url") or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    api_key = str(values.get("gemini_api_key") or "")
    if not api_key:
        raise ValueError("Gemini API key is required.")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{base_url}/models?key={api_key}")
    _raise_for_test(response, "Gemini")


async def _test_anthropic(values: dict[str, Any]) -> None:
    base_url = str(values.get("anthropic_base_url") or "https://api.anthropic.com/v1").rstrip("/")
    api_key = str(values.get("anthropic_api_key") or "")
    if not api_key:
        raise ValueError("Anthropic API key is required.")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{base_url}/models",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
    _raise_for_test(response, "Anthropic")


async def _test_ollama(values: dict[str, Any]) -> None:
    base_url = str(values.get("ollama_base_url") or "http://host.docker.internal:11434").rstrip("/")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{base_url}/api/tags")
    _raise_for_test(response, "Ollama")


def _raise_for_test(response: httpx.Response, label: str) -> None:
    if response.status_code >= 400:
        raise ValueError(f"{label} returned HTTP {response.status_code}: {response.text[:180]}")
