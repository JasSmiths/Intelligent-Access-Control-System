from difflib import SequenceMatcher
import re
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import admin_user, current_user
from app.db.session import get_db_session
from app.modules.announcements.home_assistant_tts import AnnouncementTarget, HomeAssistantTtsAnnouncer
from app.modules.gate.home_assistant import HomeAssistantGateController
from app.models import User
from app.modules.home_assistant.client import HomeAssistantClient, HomeAssistantError, HomeAssistantState
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError
from app.modules.notifications.apprise_client import normalize_apprise_url, split_apprise_urls, validate_apprise_urls
from app.services.home_assistant import get_home_assistant_service
from app.services.notifications import get_notification_service
from app.services.settings import get_runtime_config, update_settings

router = APIRouter()


class GateOpenRequest(BaseModel):
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
    users = (await session.scalars(select(User).where(User.is_active.is_(True)).order_by(User.first_name, User.last_name))).all()

    return {
        "cover_entities": cover_entities,
        "media_player_entities": media_players,
        "person_entities": person_entities,
        "presence_mappings": [
            _suggest_presence_mapping(user, person_entities)
            for user in users
        ],
    }


@router.get("/apprise/urls")
async def apprise_urls(_: User = Depends(admin_user)) -> dict:
    config = await get_runtime_config()
    urls = [normalize_apprise_url(url) for url in split_apprise_urls(config.apprise_urls)]
    return {"urls": [_apprise_url_summary(index, url) for index, url in enumerate(urls)]}


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
    return {"urls": [_apprise_url_summary(index, url) for index, url in enumerate(urls)]}


@router.delete("/apprise/urls/{index}")
async def remove_apprise_url(index: int, _: User = Depends(admin_user)) -> dict:
    config = await get_runtime_config()
    urls = [normalize_apprise_url(url) for url in split_apprise_urls(config.apprise_urls)]
    if index < 0 or index >= len(urls):
        raise HTTPException(status_code=404, detail="Apprise URL not found.")
    urls.pop(index)
    await update_settings({"apprise_urls": "\n".join(urls)})
    return {"urls": [_apprise_url_summary(row_index, url) for row_index, url in enumerate(urls)]}


@router.post("/gate/open")
async def open_gate(request: GateOpenRequest) -> dict:
    result = await HomeAssistantGateController().open_gate(request.reason)
    if not result.accepted:
        raise HTTPException(status_code=503, detail=result.detail or "Gate command failed.")
    return {
        "accepted": result.accepted,
        "state": result.state.value,
        "detail": result.detail,
    }


@router.post("/announcements/say")
async def say_announcement(request: AnnouncementRequest) -> dict[str, str]:
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
async def send_test_notification(request: TestNotificationRequest) -> dict[str, str]:
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
    return {
        "entity_id": state.entity_id,
        "name": str(friendly_name) if friendly_name else _title_from_entity_id(state.entity_id),
        "state": state.state,
    }


def _apprise_url_summary(index: int, url: str) -> dict[str, str | int]:
    parsed = urlparse(url)
    label = _apprise_service_label(parsed.scheme)
    credentials = _apprise_credentials_preview(parsed)
    return {
        "index": index,
        "type": label,
        "scheme": parsed.scheme or "unknown",
        "preview": credentials,
    }


def _apprise_service_label(scheme: str) -> str:
    labels = {
        "pover": "Pushover",
        "mailto": "Email",
        "discord": "Discord",
        "slack": "Slack",
        "tgram": "Telegram",
        "telegram": "Telegram",
    }
    return labels.get(scheme, scheme.replace("_", " ").title() if scheme else "Unknown")


def _apprise_credentials_preview(parsed) -> str:
    if parsed.scheme == "pover" and parsed.username and parsed.hostname:
        return f"user {_prefix(parsed.username)} / app {_prefix(parsed.hostname)}"
    if parsed.username:
        return f"{_prefix(parsed.username)} / {parsed.hostname or 'service'}"
    if parsed.hostname:
        return _prefix(parsed.hostname)
    return "configured"


def _prefix(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) <= 6:
        return f"{cleaned}..."
    return f"{cleaned[:6]}..."


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
