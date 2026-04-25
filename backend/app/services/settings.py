from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.db.session import AsyncSessionLocal
from app.models import SystemSetting


SECRET_KEYS = {
    "home_assistant_token",
    "apprise_urls",
    "openai_api_key",
    "gemini_api_key",
    "anthropic_api_key",
}

LEGACY_DEFAULT_REPLACEMENTS = {
    "openai_model": {"gpt-5": "gpt-4o"},
    "gemini_model": {"gemini-2.5-flash": "gemini-1.5-pro"},
    "anthropic_model": {"claude-sonnet-4-5": "claude-3-5-sonnet-latest"},
    "ollama_model": {"llama3.1": "llama3"},
}


DEFAULT_DYNAMIC_SETTINGS: dict[str, tuple[str, Any, str]] = {
    "app_name": ("general", settings.app_name, "Application display name."),
    "log_level": ("general", settings.log_level, "Backend log level."),
    "site_timezone": ("general", settings.site_timezone, "Site timezone."),
    "auth_cookie_name": ("auth", settings.auth_cookie_name, "HTTP-only auth cookie name."),
    "auth_access_token_minutes": ("auth", settings.auth_access_token_minutes, "Default session length in minutes."),
    "auth_remember_days": ("auth", settings.auth_remember_days, "Remember-me session length in days."),
    "auth_cookie_secure": ("auth", settings.auth_cookie_secure, "Set secure cookies only over HTTPS."),
    "lpr_debounce_quiet_seconds": ("lpr", settings.lpr_debounce_quiet_seconds, "Quiet period before resolving LPR reads."),
    "lpr_debounce_max_seconds": ("lpr", settings.lpr_debounce_max_seconds, "Maximum LPR debounce window."),
    "lpr_similarity_threshold": ("lpr", settings.lpr_similarity_threshold, "Plate similarity threshold."),
    "home_assistant_url": ("integrations", str(settings.home_assistant_url) if settings.home_assistant_url else "", "Home Assistant base URL."),
    "home_assistant_token": ("integrations", settings.home_assistant_token or "", "Home Assistant long-lived access token."),
    "home_assistant_gate_entity_id": ("integrations", settings.home_assistant_gate_entity_id or "", "Gate entity ID."),
    "home_assistant_gate_open_service": ("integrations", settings.home_assistant_gate_open_service, "Gate open service."),
    "home_assistant_tts_service": ("integrations", settings.home_assistant_tts_service, "TTS service name."),
    "home_assistant_default_media_player": ("integrations", settings.home_assistant_default_media_player or "", "Default announcement media player."),
    "home_assistant_presence_entities": ("integrations", settings.home_assistant_presence_entities, "Person-to-HA entity mapping."),
    "apprise_urls": ("integrations", settings.apprise_urls or "", "Apprise notification URLs."),
    "llm_provider": ("llm", settings.llm_provider, "Active LLM provider."),
    "llm_timeout_seconds": ("llm", settings.llm_timeout_seconds, "LLM HTTP timeout."),
    "openai_api_key": ("llm", settings.openai_api_key or "", "OpenAI API key."),
    "openai_model": ("llm", "gpt-4o", "OpenAI model."),
    "openai_base_url": ("llm", settings.openai_base_url, "OpenAI API base URL."),
    "gemini_api_key": ("llm", settings.gemini_api_key or "", "Gemini API key."),
    "gemini_model": ("llm", "gemini-1.5-pro", "Gemini model."),
    "gemini_base_url": ("llm", settings.gemini_base_url, "Gemini API base URL."),
    "anthropic_api_key": ("llm", settings.anthropic_api_key or "", "Anthropic API key."),
    "anthropic_model": ("llm", "claude-3-5-sonnet-latest", "Anthropic model."),
    "anthropic_base_url": ("llm", settings.anthropic_base_url, "Anthropic API base URL."),
    "ollama_base_url": ("llm", settings.ollama_base_url, "Ollama API base URL."),
    "ollama_model": ("llm", "llama3", "Ollama model."),
}


@dataclass(frozen=True)
class RuntimeConfig:
    app_name: str
    log_level: str
    site_timezone: str
    auth_cookie_name: str
    auth_access_token_minutes: int
    auth_remember_days: int
    auth_cookie_secure: bool
    lpr_debounce_quiet_seconds: float
    lpr_debounce_max_seconds: float
    lpr_similarity_threshold: float
    home_assistant_url: str
    home_assistant_token: str
    home_assistant_gate_entity_id: str
    home_assistant_gate_open_service: str
    home_assistant_tts_service: str
    home_assistant_default_media_player: str
    home_assistant_presence_entities: dict[str, str]
    apprise_urls: str
    llm_provider: str
    llm_timeout_seconds: float
    openai_api_key: str
    openai_model: str
    openai_base_url: str
    gemini_api_key: str
    gemini_model: str
    gemini_base_url: str
    anthropic_api_key: str
    anthropic_model: str
    anthropic_base_url: str
    ollama_base_url: str
    ollama_model: str


def public_value(record: SystemSetting) -> Any:
    if record.is_secret:
        encrypted = str(record.value.get("encrypted") or "")
        legacy_plain = str(record.value.get("plain") or "")
        return bool(encrypted or legacy_plain)
    return record.value.get("plain")


def decrypted_value(record: SystemSetting) -> Any:
    if not record.is_secret:
        return record.value.get("plain")
    encrypted = str(record.value.get("encrypted") or "")
    if encrypted:
        return decrypt_secret(encrypted)
    return record.value.get("plain") or ""


def setting_payload(key: str, value: Any) -> dict[str, Any]:
    if key in SECRET_KEYS:
        return {"encrypted": encrypt_secret(str(value or ""))} if value else {"encrypted": ""}
    return {"plain": value}


async def seed_dynamic_settings() -> None:
    async with AsyncSessionLocal() as session:
        await seed_dynamic_settings_for_session(session)


async def seed_dynamic_settings_for_session(session: AsyncSession) -> None:
    existing = set((await session.scalars(select(SystemSetting.key))).all())
    for key, (category, default, description) in DEFAULT_DYNAMIC_SETTINGS.items():
        if key in existing:
            continue
        session.add(
            SystemSetting(
                key=key,
                category=category,
                value=setting_payload(key, default),
                is_secret=key in SECRET_KEYS,
                description=description,
            )
        )
    records = (await session.scalars(select(SystemSetting))).all()
    for record in records:
        plain_value = record.value.get("plain")
        replacement = (
            LEGACY_DEFAULT_REPLACEMENTS.get(record.key, {}).get(plain_value)
            if isinstance(plain_value, str)
            else None
        )
        if replacement:
            record.value = {"plain": replacement}
    await session.commit()


async def get_runtime_config() -> RuntimeConfig:
    async with AsyncSessionLocal() as session:
        records = (await session.scalars(select(SystemSetting))).all()

    values = {
        key: default
        for key, (_, default, _) in DEFAULT_DYNAMIC_SETTINGS.items()
    }
    for record in records:
        values[record.key] = decrypted_value(record)

    return RuntimeConfig(
        app_name=str(values["app_name"]),
        log_level=str(values["log_level"]),
        site_timezone=str(values["site_timezone"]),
        auth_cookie_name=str(values["auth_cookie_name"]),
        auth_access_token_minutes=int(values["auth_access_token_minutes"]),
        auth_remember_days=int(values["auth_remember_days"]),
        auth_cookie_secure=bool(values["auth_cookie_secure"]),
        lpr_debounce_quiet_seconds=float(values["lpr_debounce_quiet_seconds"]),
        lpr_debounce_max_seconds=float(values["lpr_debounce_max_seconds"]),
        lpr_similarity_threshold=float(values["lpr_similarity_threshold"]),
        home_assistant_url=str(values["home_assistant_url"] or ""),
        home_assistant_token=str(values["home_assistant_token"] or ""),
        home_assistant_gate_entity_id=str(values["home_assistant_gate_entity_id"] or ""),
        home_assistant_gate_open_service=str(values["home_assistant_gate_open_service"]),
        home_assistant_tts_service=str(values["home_assistant_tts_service"]),
        home_assistant_default_media_player=str(values["home_assistant_default_media_player"] or ""),
        home_assistant_presence_entities=dict(values["home_assistant_presence_entities"] or {}),
        apprise_urls=str(values["apprise_urls"] or ""),
        llm_provider=str(values["llm_provider"]),
        llm_timeout_seconds=float(values["llm_timeout_seconds"]),
        openai_api_key=str(values["openai_api_key"] or ""),
        openai_model=str(values["openai_model"]),
        openai_base_url=str(values["openai_base_url"]),
        gemini_api_key=str(values["gemini_api_key"] or ""),
        gemini_model=str(values["gemini_model"]),
        gemini_base_url=str(values["gemini_base_url"]),
        anthropic_api_key=str(values["anthropic_api_key"] or ""),
        anthropic_model=str(values["anthropic_model"]),
        anthropic_base_url=str(values["anthropic_base_url"]),
        ollama_base_url=str(values["ollama_base_url"]),
        ollama_model=str(values["ollama_model"]),
    )


async def list_settings(category: str | None = None, *, reveal: bool = False) -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        query = select(SystemSetting).order_by(SystemSetting.category, SystemSetting.key)
        if category:
            query = query.where(SystemSetting.category == category)
        records = (await session.scalars(query)).all()

    return [
        {
            "key": record.key,
            "category": record.category,
            "value": decrypted_value(record) if reveal else public_value(record),
            "is_secret": record.is_secret,
            "description": record.description,
        }
        for record in records
    ]


async def update_settings(updates: dict[str, Any]) -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        records = {
            record.key: record
            for record in (await session.scalars(select(SystemSetting))).all()
        }
        for key, value in updates.items():
            if key not in DEFAULT_DYNAMIC_SETTINGS:
                continue
            category, _, description = DEFAULT_DYNAMIC_SETTINGS[key]
            record = records.get(key)
            if record:
                if record.is_secret and value in {None, ""} and key != "apprise_urls":
                    continue
                record.value = setting_payload(key, value)
            else:
                record = SystemSetting(
                    key=key,
                    category=category,
                    value=setting_payload(key, value),
                    is_secret=key in SECRET_KEYS,
                    description=description,
                )
                session.add(record)
        await session.commit()
    return await list_settings()
