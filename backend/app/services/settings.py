from dataclasses import dataclass
from time import monotonic
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.db.session import AsyncSessionLocal
from app.models import SystemSetting
from app.modules.home_assistant.covers import legacy_gate_entities, normalize_cover_entities


SECRET_KEYS = {
    "home_assistant_token",
    "apprise_urls",
    "discord_bot_token",
    "whatsapp_access_token",
    "whatsapp_webhook_verify_token",
    "whatsapp_app_secret",
    "dvla_api_key",
    "unifi_protect_username",
    "unifi_protect_password",
    "unifi_protect_api_key",
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

OBSOLETE_DYNAMIC_SETTINGS = {"notification_rules", "home_assistant_presence_entities"}


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
    "lpr_allowed_smart_zones": (
        "lpr",
        ["default"],
        "UniFi smart zone names or IDs allowed to produce access events. Empty or * accepts every zone.",
    ),
    "schedule_default_policy": (
        "access",
        "allow",
        "Access policy when no schedule is assigned. Use allow or deny.",
    ),
    "home_assistant_url": ("integrations", str(settings.home_assistant_url) if settings.home_assistant_url else "", "Home Assistant base URL."),
    "home_assistant_token": ("integrations", settings.home_assistant_token or "", "Home Assistant long-lived access token."),
    "home_assistant_gate_entity_id": ("integrations", settings.home_assistant_gate_entity_id or "", "Gate entity ID."),
    "home_assistant_gate_entities": (
        "integrations",
        legacy_gate_entities(settings.home_assistant_gate_entity_id or "", settings.home_assistant_gate_open_service),
        "Configured Home Assistant gate cover entities.",
    ),
    "home_assistant_gate_open_service": ("integrations", settings.home_assistant_gate_open_service, "Cover open service."),
    "home_assistant_garage_door_entities": (
        "integrations",
        [],
        "Configured Home Assistant garage door cover entities.",
    ),
    "home_assistant_tts_service": ("integrations", settings.home_assistant_tts_service, "TTS service name."),
    "home_assistant_default_media_player": ("integrations", settings.home_assistant_default_media_player or "", "Default announcement media player."),
    "apprise_urls": ("integrations", settings.apprise_urls or "", "Apprise notification URLs."),
    "discord_bot_token": ("integrations", "", "Discord bot token."),
    "discord_guild_allowlist": ("integrations", [], "Allowed Discord guild/server IDs."),
    "discord_channel_allowlist": ("integrations", [], "Allowed Discord channel IDs."),
    "discord_user_allowlist": ("integrations", [], "Allowed Discord user IDs."),
    "discord_role_allowlist": ("integrations", [], "Allowed Discord role IDs."),
    "discord_admin_role_ids": ("integrations", [], "Discord role IDs allowed to confirm Admin actions."),
    "discord_default_notification_channel_id": (
        "integrations",
        "",
        "Default Discord channel ID for notification workflows.",
    ),
    "discord_allow_direct_messages": ("integrations", False, "Allow direct messages to Alfred from allowed users."),
    "discord_require_mention": ("integrations", True, "Require @Alfred mentions for guild channel messages."),
    "whatsapp_enabled": ("integrations", False, "Enable WhatsApp Cloud API messaging."),
    "whatsapp_access_token": ("integrations", "", "Meta WhatsApp Cloud API access token."),
    "whatsapp_phone_number_id": ("integrations", "", "WhatsApp Business phone number ID."),
    "whatsapp_business_account_id": ("integrations", "", "WhatsApp Business Account ID."),
    "whatsapp_webhook_verify_token": ("integrations", "", "Webhook verification token for Meta setup."),
    "whatsapp_app_secret": ("integrations", "", "Optional Meta app secret for webhook signature validation."),
    "whatsapp_graph_api_version": ("integrations", "v25.0", "Meta Graph API version for WhatsApp Cloud API."),
    "whatsapp_visitor_pass_template_name": (
        "integrations",
        "visitor_pass_registration_request",
        "Approved WhatsApp utility template used to request Visitor Pass vehicle registrations.",
    ),
    "whatsapp_visitor_pass_template_language": (
        "integrations",
        "en_GB",
        "Language code for the Visitor Pass WhatsApp outreach template.",
    ),
    "dvla_api_key": ("integrations", "", "DVLA Vehicle Enquiry Service API key."),
    "dvla_vehicle_enquiry_url": (
        "integrations",
        "https://driver-vehicle-licensing.api.gov.uk/vehicle-enquiry/v1/vehicles",
        "DVLA Vehicle Enquiry Service endpoint URL.",
    ),
    "dvla_test_registration_number": ("integrations", "AA19AAA", "VRN used for DVLA connection tests."),
    "dvla_timeout_seconds": ("integrations", 10.0, "DVLA Vehicle Enquiry Service HTTP timeout."),
    "unifi_protect_host": ("integrations", "", "UniFi Protect console hostname or IP address."),
    "unifi_protect_port": ("integrations", 443, "UniFi Protect console HTTPS port."),
    "unifi_protect_username": ("integrations", "", "UniFi Protect local user username."),
    "unifi_protect_password": ("integrations", "", "UniFi Protect local user password."),
    "unifi_protect_api_key": ("integrations", "", "UniFi Protect Integration API key."),
    "unifi_protect_verify_ssl": ("integrations", False, "Verify the UniFi Protect console TLS certificate."),
    "unifi_protect_snapshot_width": ("integrations", 1280, "Default UniFi Protect snapshot width."),
    "unifi_protect_snapshot_height": ("integrations", 720, "Default UniFi Protect snapshot height."),
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
    "dependency_update_backup_storage_mode": (
        "updates",
        "local",
        "Update backup storage mode. Use local, nfs, or samba.",
    ),
    "dependency_update_backup_mount_source": (
        "updates",
        "",
        "NFS export or Samba share used by the generated Docker backup volume.",
    ),
    "dependency_update_backup_mount_options": (
        "updates",
        "",
        "Docker local volume mount options for NFS/CIFS update backup storage.",
    ),
    "dependency_update_backup_retention_days": (
        "updates",
        "",
        "Optional retention period for update backup archives.",
    ),
    "dependency_update_backup_min_free_bytes": (
        "updates",
        1073741824,
        "Minimum free bytes required before creating update backups.",
    ),
    "dependency_update_backup_config_status": (
        "updates",
        "active",
        "Current backup storage configuration state.",
    ),
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
    lpr_allowed_smart_zones: list[str]
    schedule_default_policy: str
    home_assistant_url: str
    home_assistant_token: str
    home_assistant_gate_entity_id: str
    home_assistant_gate_entities: list[dict[str, Any]]
    home_assistant_gate_open_service: str
    home_assistant_garage_door_entities: list[dict[str, Any]]
    home_assistant_tts_service: str
    home_assistant_default_media_player: str
    apprise_urls: str
    discord_bot_token: str
    discord_guild_allowlist: list[str]
    discord_channel_allowlist: list[str]
    discord_user_allowlist: list[str]
    discord_role_allowlist: list[str]
    discord_admin_role_ids: list[str]
    discord_default_notification_channel_id: str
    discord_allow_direct_messages: bool
    discord_require_mention: bool
    whatsapp_enabled: bool
    whatsapp_access_token: str
    whatsapp_phone_number_id: str
    whatsapp_business_account_id: str
    whatsapp_webhook_verify_token: str
    whatsapp_app_secret: str
    whatsapp_graph_api_version: str
    whatsapp_visitor_pass_template_name: str
    whatsapp_visitor_pass_template_language: str
    dvla_api_key: str
    dvla_vehicle_enquiry_url: str
    dvla_test_registration_number: str
    dvla_timeout_seconds: float
    unifi_protect_host: str
    unifi_protect_port: int
    unifi_protect_username: str
    unifi_protect_password: str
    unifi_protect_api_key: str
    unifi_protect_verify_ssl: bool
    unifi_protect_snapshot_width: int
    unifi_protect_snapshot_height: int
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
    dependency_update_backup_storage_mode: str
    dependency_update_backup_mount_source: str
    dependency_update_backup_mount_options: str
    dependency_update_backup_retention_days: str
    dependency_update_backup_min_free_bytes: int
    dependency_update_backup_config_status: str


_RUNTIME_CONFIG_CACHE: RuntimeConfig | None = None
_RUNTIME_CONFIG_CACHE_LOADED_AT = 0.0
_RUNTIME_CONFIG_CACHE_TTL_SECONDS = 2.0


def invalidate_runtime_config_cache() -> None:
    global _RUNTIME_CONFIG_CACHE, _RUNTIME_CONFIG_CACHE_LOADED_AT
    _RUNTIME_CONFIG_CACHE = None
    _RUNTIME_CONFIG_CACHE_LOADED_AT = 0.0


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


def bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def string_list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    raw = str(value).strip()
    if not raw:
        return []
    return [
        item.strip()
        for item in raw.replace(",", "\n").splitlines()
        if item.strip()
    ]


async def seed_dynamic_settings() -> None:
    async with AsyncSessionLocal() as session:
        await seed_dynamic_settings_for_session(session)
    invalidate_runtime_config_cache()


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
    records_by_key = {record.key: record for record in records}
    for record in records:
        if record.key in OBSOLETE_DYNAMIC_SETTINGS:
            await session.delete(record)
            records_by_key.pop(record.key, None)

    legacy_gate_entity_record = records_by_key.get("home_assistant_gate_entity_id")
    gate_entities_record = records_by_key.get("home_assistant_gate_entities")
    if legacy_gate_entity_record and gate_entities_record:
        legacy_gate_entity_id = str(decrypted_value(legacy_gate_entity_record) or "")
        configured_gate_entities = normalize_cover_entities(decrypted_value(gate_entities_record))
        if legacy_gate_entity_id and not configured_gate_entities:
            gate_open_service_record = records_by_key.get("home_assistant_gate_open_service")
            gate_open_service = (
                str(decrypted_value(gate_open_service_record))
                if gate_open_service_record
                else settings.home_assistant_gate_open_service
            )
            gate_entities_record.value = setting_payload(
                "home_assistant_gate_entities",
                legacy_gate_entities(legacy_gate_entity_id, gate_open_service),
            )
    for record in records:
        if record.key in OBSOLETE_DYNAMIC_SETTINGS:
            continue
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
    global _RUNTIME_CONFIG_CACHE, _RUNTIME_CONFIG_CACHE_LOADED_AT

    now = monotonic()
    if _RUNTIME_CONFIG_CACHE is not None and now - _RUNTIME_CONFIG_CACHE_LOADED_AT <= _RUNTIME_CONFIG_CACHE_TTL_SECONDS:
        return _RUNTIME_CONFIG_CACHE

    async with AsyncSessionLocal() as session:
        records = (await session.scalars(select(SystemSetting))).all()

    values = {
        key: default
        for key, (_, default, _) in DEFAULT_DYNAMIC_SETTINGS.items()
    }
    for record in records:
        values[record.key] = decrypted_value(record)

    config = RuntimeConfig(
        app_name=str(values["app_name"]),
        log_level=str(values["log_level"]),
        site_timezone=str(values["site_timezone"]),
        auth_cookie_name=str(values["auth_cookie_name"]),
        auth_access_token_minutes=int(values["auth_access_token_minutes"]),
        auth_remember_days=int(values["auth_remember_days"]),
        auth_cookie_secure=bool_value(values["auth_cookie_secure"]),
        lpr_debounce_quiet_seconds=float(values["lpr_debounce_quiet_seconds"]),
        lpr_debounce_max_seconds=float(values["lpr_debounce_max_seconds"]),
        lpr_similarity_threshold=float(values["lpr_similarity_threshold"]),
        lpr_allowed_smart_zones=string_list_value(values["lpr_allowed_smart_zones"]),
        schedule_default_policy=(
            "deny" if str(values["schedule_default_policy"]).strip().lower() == "deny" else "allow"
        ),
        home_assistant_url=str(values["home_assistant_url"] or ""),
        home_assistant_token=str(values["home_assistant_token"] or ""),
        home_assistant_gate_entity_id=str(values["home_assistant_gate_entity_id"] or ""),
        home_assistant_gate_entities=normalize_cover_entities(
            values["home_assistant_gate_entities"],
            default_open_service=str(values["home_assistant_gate_open_service"]),
        ),
        home_assistant_gate_open_service=str(values["home_assistant_gate_open_service"]),
        home_assistant_garage_door_entities=normalize_cover_entities(
            values["home_assistant_garage_door_entities"],
            default_open_service=str(values["home_assistant_gate_open_service"]),
        ),
        home_assistant_tts_service=str(values["home_assistant_tts_service"]),
        home_assistant_default_media_player=str(values["home_assistant_default_media_player"] or ""),
        apprise_urls=str(values["apprise_urls"] or ""),
        discord_bot_token=str(values["discord_bot_token"] or ""),
        discord_guild_allowlist=string_list_value(values["discord_guild_allowlist"]),
        discord_channel_allowlist=string_list_value(values["discord_channel_allowlist"]),
        discord_user_allowlist=string_list_value(values["discord_user_allowlist"]),
        discord_role_allowlist=string_list_value(values["discord_role_allowlist"]),
        discord_admin_role_ids=string_list_value(values["discord_admin_role_ids"]),
        discord_default_notification_channel_id=str(values["discord_default_notification_channel_id"] or ""),
        discord_allow_direct_messages=bool_value(values["discord_allow_direct_messages"]),
        discord_require_mention=bool_value(values["discord_require_mention"]),
        whatsapp_enabled=bool_value(values["whatsapp_enabled"]),
        whatsapp_access_token=str(values["whatsapp_access_token"] or ""),
        whatsapp_phone_number_id=str(values["whatsapp_phone_number_id"] or ""),
        whatsapp_business_account_id=str(values["whatsapp_business_account_id"] or ""),
        whatsapp_webhook_verify_token=str(values["whatsapp_webhook_verify_token"] or ""),
        whatsapp_app_secret=str(values["whatsapp_app_secret"] or ""),
        whatsapp_graph_api_version=str(values["whatsapp_graph_api_version"] or "v25.0"),
        whatsapp_visitor_pass_template_name=str(values["whatsapp_visitor_pass_template_name"] or ""),
        whatsapp_visitor_pass_template_language=str(values["whatsapp_visitor_pass_template_language"] or "en_GB"),
        dvla_api_key=str(values["dvla_api_key"] or ""),
        dvla_vehicle_enquiry_url=str(values["dvla_vehicle_enquiry_url"] or ""),
        dvla_test_registration_number=str(values["dvla_test_registration_number"] or ""),
        dvla_timeout_seconds=float(values["dvla_timeout_seconds"]),
        unifi_protect_host=str(values["unifi_protect_host"] or ""),
        unifi_protect_port=int(values["unifi_protect_port"] or 443),
        unifi_protect_username=str(values["unifi_protect_username"] or ""),
        unifi_protect_password=str(values["unifi_protect_password"] or ""),
        unifi_protect_api_key=str(values["unifi_protect_api_key"] or ""),
        unifi_protect_verify_ssl=bool_value(values["unifi_protect_verify_ssl"]),
        unifi_protect_snapshot_width=int(values["unifi_protect_snapshot_width"] or 1280),
        unifi_protect_snapshot_height=int(values["unifi_protect_snapshot_height"] or 720),
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
        dependency_update_backup_storage_mode=(
            str(values["dependency_update_backup_storage_mode"]).strip().lower()
            if str(values["dependency_update_backup_storage_mode"]).strip().lower() in {"local", "nfs", "samba"}
            else "local"
        ),
        dependency_update_backup_mount_source=str(values["dependency_update_backup_mount_source"] or ""),
        dependency_update_backup_mount_options=str(values["dependency_update_backup_mount_options"] or ""),
        dependency_update_backup_retention_days=str(values["dependency_update_backup_retention_days"] or ""),
        dependency_update_backup_min_free_bytes=int(values["dependency_update_backup_min_free_bytes"] or 1073741824),
        dependency_update_backup_config_status=(
            str(values["dependency_update_backup_config_status"]).strip().lower()
            if str(values["dependency_update_backup_config_status"]).strip().lower() in {"active", "pending_reboot", "error"}
            else "active"
        ),
    )
    _RUNTIME_CONFIG_CACHE = config
    _RUNTIME_CONFIG_CACHE_LOADED_AT = now
    return config


async def list_settings(category: str | None = None, *, reveal: bool = False) -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        query = select(SystemSetting).order_by(SystemSetting.category, SystemSetting.key)
        query = query.where(SystemSetting.key.notin_(OBSOLETE_DYNAMIC_SETTINGS))
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
    invalidate_runtime_config_cache()
    return await list_settings()
