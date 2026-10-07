import json
from dataclasses import dataclass
from time import monotonic
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.db.session import AsyncSessionLocal
from app.models import AccessDevice, AccessDeviceProviderBinding, SystemSetting, User
from app.modules.access_devices.base import (
    AccessDeviceBinding,
    binding_is_commandable,
    validate_gate_admission_device_key,
)
from app.modules.home_assistant.covers import normalize_cover_entities
from app.services.access_device_commands import AccessDeviceCommandJournal
from app.services.mutation_context import load_active_admin
from app.services.telemetry import (
    TELEMETRY_CATEGORY_CRUD,
    actor_from_user,
    audit_diff,
    write_audit_log,
)

SECRET_KEYS = {
    "home_assistant_token",
    "esphome_devices",
    "apprise_urls",
    "dvla_api_key",
    "dvsa_client_secret",
    "dvsa_api_key",
    "unifi_protect_username",
    "unifi_protect_password",
    "unifi_protect_api_key",
    "openai_api_key",
    "gemini_api_key",
    "anthropic_api_key",
    "lpr_webhook_token",
}

CLEARABLE_SECRET_KEYS = {"apprise_urls"}


class UnknownDynamicSettingsError(ValueError):
    def __init__(self, unknown_keys: list[str]) -> None:
        self.unknown_keys = sorted(unknown_keys)
        self.allowed_keys = sorted(DEFAULT_DYNAMIC_SETTINGS.keys())
        super().__init__("Unknown dynamic setting key(s): " + ", ".join(self.unknown_keys))


def validate_dynamic_setting_keys(updates: dict[str, Any]) -> None:
    unknown_keys = sorted(str(key) for key in updates if str(key) not in DEFAULT_DYNAMIC_SETTINGS)
    if unknown_keys:
        raise UnknownDynamicSettingsError(unknown_keys)


DEFAULT_DYNAMIC_SETTINGS: dict[str, tuple[str, Any, str]] = {
    "missed_exit_recovery_enabled": (
        "missed_exit_recovery",
        False,
        "Enable opted-in resident missed exit recovery.",
    ),
    "missed_exit_recovery_gate_latitude": (
        "missed_exit_recovery",
        None,
        "Top gate latitude; exact-location iPhone tracking required.",
    ),
    "missed_exit_recovery_gate_longitude": ("missed_exit_recovery", None, "Top gate longitude."),
    "app_name": ("general", settings.app_name, "Application display name."),
    "log_level": ("general", settings.log_level, "Backend log level."),
    "site_timezone": ("general", settings.site_timezone, "Site timezone."),
    "auth_cookie_name": ("auth", settings.auth_cookie_name, "HTTP-only auth cookie name."),
    "auth_access_token_minutes": (
        "auth",
        settings.auth_access_token_minutes,
        "Default session length in minutes.",
    ),
    "auth_remember_days": (
        "auth",
        settings.auth_remember_days,
        "Remember-me session length in days.",
    ),
    "auth_cookie_secure": (
        "auth",
        settings.auth_cookie_secure,
        "Set secure cookies only over HTTPS.",
    ),
    "lpr_debounce_quiet_seconds": (
        "lpr",
        settings.lpr_debounce_quiet_seconds,
        "Quiet period before resolving LPR reads.",
    ),
    "lpr_debounce_max_seconds": (
        "lpr",
        settings.lpr_debounce_max_seconds,
        "Maximum LPR debounce window.",
    ),
    "lpr_vehicle_session_idle_seconds": (
        "lpr",
        settings.lpr_vehicle_session_idle_seconds,
        "Seconds without matching plate or vehicle detections before a physical gate visit is considered finished.",
    ),
    "lpr_similarity_threshold": (
        "lpr",
        settings.lpr_similarity_threshold,
        "Plate similarity threshold.",
    ),
    "lpr_allowed_smart_zones": (
        "lpr",
        ["default"],
        "UniFi smart-zone diagnostic list used for LPR zone-status visibility.",
    ),
    "lpr_zone_filter_mode": (
        "lpr",
        "shadow",
        "UniFi zone-status driveway filter mode. Use shadow to log only, or live to suppress invalid present zone/status reads.",
    ),
    "lpr_webhook_token": (
        "lpr",
        "",
        "Shared secret expected in the X-IACS-LPR-Token header on incoming UniFi LPR webhooks.",
    ),
    "lpr_webhook_allowed_source_ips": (
        "lpr",
        [],
        "Static UNVR source IP addresses or CIDR ranges allowed to send UniFi LPR webhooks.",
    ),
    "lpr_webhook_trusted_proxy_ips": (
        "lpr",
        [],
        "Trusted reverse-proxy source IPs or CIDRs whose forwarded client IP may be used for UniFi LPR source checks.",
    ),
    "schedule_default_policy": (
        "access",
        "allow",
        "Access policy when no schedule is assigned. Use allow or deny.",
    ),
    "gate_control_provider": (
        "access",
        settings.gate_controller
        if settings.gate_controller in {"home_assistant", "esphome"}
        else "home_assistant",
        "Primary provider for gate and garage-door cover commands.",
    ),
    "gate_failover_provider": (
        "access",
        "none",
        "Optional failover provider for gate and garage-door cover commands.",
    ),
    "gate_admission_device_key": (
        "access",
        "",
        "Explicit entry gate for automatic admission; must be enabled, commandable and an automatic-access target. Unset blocks activation.",
    ),
    "home_assistant_url": (
        "integrations",
        str(settings.home_assistant_url) if settings.home_assistant_url else "",
        "Home Assistant base URL.",
    ),
    "home_assistant_token": (
        "integrations",
        settings.home_assistant_token or "",
        "Home Assistant long-lived access token.",
    ),
    "home_assistant_gate_entities": (
        "integrations",
        [],
        "Configured Home Assistant gate cover entities.",
    ),
    "home_assistant_gate_open_service": (
        "integrations",
        settings.home_assistant_gate_open_service,
        "Cover open service.",
    ),
    "home_assistant_garage_door_entities": (
        "integrations",
        [],
        "Configured Home Assistant garage door cover entities.",
    ),
    "home_assistant_tts_service": (
        "integrations",
        settings.home_assistant_tts_service,
        "TTS service name.",
    ),
    "home_assistant_default_media_player": (
        "integrations",
        settings.home_assistant_default_media_player or "",
        "Default announcement media player.",
    ),
    "esphome_devices": ("integrations", "[]", "Configured ESPHome native API devices."),
    "apprise_urls": ("integrations", settings.apprise_urls or "", "Apprise notification URLs."),
    "dvsa_enabled": ("integrations", False, "Enable DVSA MOT History enrichment."),
    "dvsa_client_id": ("integrations", "", "DVSA client ID."),
    "dvsa_client_secret": ("integrations", "", "DVSA client secret."),
    "dvsa_api_key": ("integrations", "", "DVSA API key."),
    "dvsa_token_url": ("integrations", "", "Microsoft token URL supplied by DVSA."),
    "dvsa_scope": ("integrations", "https://tapi.dvsa.gov.uk/.default", "DVSA OAuth scope."),
    "dvsa_timeout_seconds": ("integrations", 10.0, "DVSA HTTP timeout."),
    "dvsa_test_registration_number": ("integrations", "AA19AAA", "Connection test registration."),
    "dvla_api_key": ("integrations", "", "DVLA Vehicle Enquiry Service API key."),
    "dvla_vehicle_enquiry_url": (
        "integrations",
        "https://driver-vehicle-licensing.api.gov.uk/vehicle-enquiry/v1/vehicles",
        "DVLA Vehicle Enquiry Service endpoint URL.",
    ),
    "dvla_test_registration_number": (
        "integrations",
        "AA19AAA",
        "VRN used for DVLA connection tests.",
    ),
    "dvla_timeout_seconds": ("integrations", 10.0, "DVLA Vehicle Enquiry Service HTTP timeout."),
    "unifi_protect_host": ("integrations", "", "UniFi Protect console hostname or IP address."),
    "unifi_protect_port": ("integrations", 443, "UniFi Protect console HTTPS port."),
    "unifi_protect_username": ("integrations", "", "UniFi Protect local user username."),
    "unifi_protect_password": ("integrations", "", "UniFi Protect local user password."),
    "unifi_protect_api_key": ("integrations", "", "UniFi Protect Integration API key."),
    "unifi_protect_verify_ssl": (
        "integrations",
        False,
        "Verify the UniFi Protect console TLS certificate.",
    ),
    "unifi_protect_snapshot_width": ("integrations", 1280, "Default UniFi Protect snapshot width."),
    "unifi_protect_snapshot_height": (
        "integrations",
        720,
        "Default UniFi Protect snapshot height.",
    ),
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
    missed_exit_recovery_enabled: bool
    missed_exit_recovery_gate_latitude: float | None
    missed_exit_recovery_gate_longitude: float | None
    app_name: str
    log_level: str
    site_timezone: str
    auth_cookie_name: str
    auth_access_token_minutes: int
    auth_remember_days: int
    auth_cookie_secure: bool
    lpr_debounce_quiet_seconds: float
    lpr_debounce_max_seconds: float
    lpr_vehicle_session_idle_seconds: float
    lpr_similarity_threshold: float
    lpr_allowed_smart_zones: list[str]
    lpr_zone_filter_mode: str
    lpr_webhook_token: str
    lpr_webhook_allowed_source_ips: list[str]
    lpr_webhook_trusted_proxy_ips: list[str]
    schedule_default_policy: str
    gate_control_provider: str
    gate_failover_provider: str
    gate_admission_device_key: str | None
    home_assistant_url: str
    home_assistant_token: str
    home_assistant_gate_entities: list[dict[str, Any]]
    home_assistant_gate_open_service: str
    home_assistant_garage_door_entities: list[dict[str, Any]]
    home_assistant_tts_service: str
    home_assistant_default_media_player: str
    esphome_devices: list[dict[str, Any]]
    apprise_urls: str
    dvsa_enabled: bool
    dvsa_client_id: str
    dvsa_client_secret: str
    dvsa_api_key: str
    dvsa_token_url: str
    dvsa_scope: str
    dvsa_timeout_seconds: float
    dvsa_test_registration_number: str
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
        plain_secret = str(record.value.get("plain") or "")
        return bool(encrypted or plain_secret)
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
        if key == "esphome_devices":
            encoded = (
                json.dumps(value if value is not None else [], separators=(",", ":"))
                if not isinstance(value, str)
                else value
            )
            return {"encrypted": encrypt_secret(encoded)} if encoded else {"encrypted": ""}
        return {"encrypted": encrypt_secret(str(value or ""))} if value else {"encrypted": ""}
    return {"plain": value}


def _migrate_secret_record(record: SystemSetting) -> bool:
    if record.key not in SECRET_KEYS or record.is_secret:
        return False
    plain_value = record.value.get("plain") if isinstance(record.value, dict) else record.value
    record.value = setting_payload(record.key, plain_value)
    record.is_secret = True
    return True


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
    return [item.strip() for item in raw.replace(",", "\n").splitlines() if item.strip()]


def json_list_value(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    raw = str(value).strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def normalize_esphome_device_id(value: str) -> str:
    return "_".join(str(value or "").strip().lower().replace("-", "_").split())


def normalize_esphome_devices(
    value: Any,
) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(json_list_value(value)):
        if not isinstance(item, dict):
            continue
        host = str(item.get("host") or "").strip()
        name = str(item.get("name") or host or f"ESPHome Device {index + 1}").strip()
        base_id = normalize_esphome_device_id(
            str(item.get("id") or item.get("key") or name or host)
        )
        device_id = base_id or f"esphome_{index + 1}"
        if device_id in seen:
            suffix = 2
            while f"{device_id}_{suffix}" in seen:
                suffix += 1
            device_id = f"{device_id}_{suffix}"
        seen.add(device_id)
        devices.append(
            {
                "id": device_id,
                "name": name,
                "host": host,
                "port": int(item.get("port") or 6053),
                "encryption_key": str(
                    item.get("encryption_key")
                    or item.get("api_encryption_key")
                    or item.get("noise_psk")
                    or ""
                ),
                "timeout_seconds": float(item.get("timeout_seconds") or 30.0),
                "enabled": bool_value(item.get("enabled", True)),
            }
        )
    return devices


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
    for record in records:
        _migrate_secret_record(record)
    await session.commit()


async def get_runtime_config() -> RuntimeConfig:
    global _RUNTIME_CONFIG_CACHE, _RUNTIME_CONFIG_CACHE_LOADED_AT

    now = monotonic()
    if (
        _RUNTIME_CONFIG_CACHE is not None
        and now - _RUNTIME_CONFIG_CACHE_LOADED_AT <= _RUNTIME_CONFIG_CACHE_TTL_SECONDS
    ):
        return _RUNTIME_CONFIG_CACHE

    async with AsyncSessionLocal() as session:
        config = await get_runtime_config_for_session(session)
    _RUNTIME_CONFIG_CACHE = config
    _RUNTIME_CONFIG_CACHE_LOADED_AT = now
    return config


async def get_runtime_config_for_session(session: AsyncSession) -> RuntimeConfig:
    """Read current configuration under the caller's transaction and locks.

    Hardware validation must use this read, then pass the returned snapshot to
    the provider. The process cache is only suitable for non-authoritative reads.
    """
    records = (
        await session.scalars(
            select(SystemSetting)
            .where(SystemSetting.key.in_(list(DEFAULT_DYNAMIC_SETTINGS)))
            .execution_options(populate_existing=True)
        )
    ).all()

    values = {key: default for key, (_, default, _) in DEFAULT_DYNAMIC_SETTINGS.items()}
    for record in records:
        values[record.key] = decrypted_value(record)

    config = RuntimeConfig(
        missed_exit_recovery_enabled=bool(values["missed_exit_recovery_enabled"]),
        missed_exit_recovery_gate_latitude=values["missed_exit_recovery_gate_latitude"],
        missed_exit_recovery_gate_longitude=values["missed_exit_recovery_gate_longitude"],
        app_name=str(values["app_name"]),
        log_level=str(values["log_level"]),
        site_timezone=str(values["site_timezone"]),
        auth_cookie_name=str(values["auth_cookie_name"]),
        auth_access_token_minutes=int(values["auth_access_token_minutes"]),
        auth_remember_days=int(values["auth_remember_days"]),
        auth_cookie_secure=bool_value(values["auth_cookie_secure"]),
        lpr_debounce_quiet_seconds=float(values["lpr_debounce_quiet_seconds"]),
        lpr_debounce_max_seconds=float(values["lpr_debounce_max_seconds"]),
        lpr_vehicle_session_idle_seconds=float(values["lpr_vehicle_session_idle_seconds"]),
        lpr_similarity_threshold=float(values["lpr_similarity_threshold"]),
        lpr_allowed_smart_zones=string_list_value(values["lpr_allowed_smart_zones"]),
        lpr_zone_filter_mode=(
            "live" if str(values["lpr_zone_filter_mode"]).strip().lower() == "live" else "shadow"
        ),
        lpr_webhook_token=str(values["lpr_webhook_token"] or ""),
        lpr_webhook_allowed_source_ips=string_list_value(values["lpr_webhook_allowed_source_ips"]),
        lpr_webhook_trusted_proxy_ips=string_list_value(values["lpr_webhook_trusted_proxy_ips"]),
        schedule_default_policy=(
            "deny" if str(values["schedule_default_policy"]).strip().lower() == "deny" else "allow"
        ),
        gate_control_provider=(
            str(values["gate_control_provider"]).strip().lower()
            if str(values["gate_control_provider"]).strip().lower() in {"home_assistant", "esphome"}
            else "home_assistant"
        ),
        gate_failover_provider=(
            str(values["gate_failover_provider"]).strip().lower()
            if str(values["gate_failover_provider"]).strip().lower()
            in {"none", "home_assistant", "esphome"}
            else "none"
        ),
        gate_admission_device_key=str(values["gate_admission_device_key"] or "").strip() or None,
        home_assistant_url=str(values["home_assistant_url"] or ""),
        home_assistant_token=str(values["home_assistant_token"] or ""),
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
        home_assistant_default_media_player=str(
            values["home_assistant_default_media_player"] or ""
        ),
        esphome_devices=normalize_esphome_devices(values["esphome_devices"]),
        apprise_urls=str(values["apprise_urls"] or ""),
        dvsa_enabled=bool_value(values["dvsa_enabled"]),
        dvsa_client_id=str(values["dvsa_client_id"] or ""),
        dvsa_client_secret=str(values["dvsa_client_secret"] or ""),
        dvsa_api_key=str(values["dvsa_api_key"] or ""),
        dvsa_token_url=str(values["dvsa_token_url"] or ""),
        dvsa_scope=str(values["dvsa_scope"] or ""),
        dvsa_timeout_seconds=min(20.0, max(1.0, float(values["dvsa_timeout_seconds"]))),
        dvsa_test_registration_number=str(values["dvsa_test_registration_number"] or ""),
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
    )
    return config


async def list_settings(
    category: str | None = None, *, reveal: bool = False
) -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        query = select(SystemSetting).order_by(SystemSetting.category, SystemSetting.key)
        query = query.where(SystemSetting.key.in_(list(DEFAULT_DYNAMIC_SETTINGS)))
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


async def update_settings(
    updates: dict[str, Any],
    *,
    user: User | None = None,
    source: str = "system",
) -> list[dict[str, Any]]:
    """Own setting writes and their mandatory audit in one transaction."""
    validate_dynamic_setting_keys(updates)

    async with AsyncSessionLocal() as session:
        current_actor = (
            await load_active_admin(
                session, user.id, auth_version=user.auth_session_version, lock=True
            )
            if user
            else None
        )
        target_config_keys = {
            "gate_admission_device_key",
            "gate_control_provider",
            "gate_failover_provider",
            "home_assistant_url",
            "home_assistant_token",
            "home_assistant_gate_open_service",
            "esphome_devices",
            "schedule_default_policy",
            "site_timezone",
        }
        if set(updates) & target_config_keys:
            query = select(AccessDevice.id)
            if set(updates) & {
                "gate_control_provider",
                "gate_failover_provider",
                "schedule_default_policy",
                "site_timezone",
            }:
                pass  # Provider order applies to every gate and garage.
            else:
                providers = []
                if "esphome_devices" in updates:
                    providers.append("esphome")
                if any(key.startswith("home_assistant_") for key in updates):
                    providers.append("home_assistant")
                affected = [
                    AccessDevice.provider_bindings.any(
                        AccessDeviceProviderBinding.provider.in_(providers)
                    )
                ]
                if "gate_admission_device_key" in updates:
                    affected.append(AccessDevice.kind == "gate")
                query = query.where(or_(*affected))
            target_ids = (await session.scalars(query.distinct().order_by(AccessDevice.id))).all()
            for target_id in target_ids:
                await AccessDeviceCommandJournal.assert_configurable(session, target_id)
        for key in sorted(updates):
            # Also serialize creation of a newly introduced setting with no row yet.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:setting))"),
                {"setting": f"iacs:setting:{key}"},
            )
        records = {
            record.key: record
            for record in (
                await session.scalars(
                    select(SystemSetting)
                    .order_by(SystemSetting.key)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        }
        before = {key: public_value(row) for key, row in records.items() if key in updates}
        changed_keys = []
        if set(updates) & {
            "gate_admission_device_key",
            "home_assistant_url",
            "home_assistant_token",
            "esphome_devices",
        }:
            candidate = {key: default for key, (_, default, _) in DEFAULT_DYNAMIC_SETTINGS.items()}
            candidate.update({key: decrypted_value(row) for key, row in records.items()})
            candidate.update(
                {
                    key: value
                    for key, value in updates.items()
                    if not (
                        key in records
                        and records[key].is_secret
                        and value in (None, "")
                        and key not in CLEARABLE_SECRET_KEYS
                    )
                }
            )
            await _validate_admission_setting(session, candidate)
        if any(key.startswith("missed_exit_recovery_") for key in updates):
            from types import SimpleNamespace

            from app.services.resident_recovery_evidence import gate_coordinates

            candidate = {key: default for key, (_, default, _) in DEFAULT_DYNAMIC_SETTINGS.items()}
            candidate.update({key: decrypted_value(row) for key, row in records.items()})
            candidate.update(updates)
            if type(candidate["missed_exit_recovery_enabled"]) is not bool:
                raise ValueError("Recovery enablement must be a boolean.")
            if candidate["missed_exit_recovery_enabled"] and (
                gate_coordinates(SimpleNamespace(**candidate)) is None
                or not candidate["gate_admission_device_key"]
            ):
                raise ValueError(
                    "Configure the top gate coordinates and admission gate before enabling recovery."
                )
            from app.models import ResidentRecoveryJourney

            journeys = list(
                (
                    await session.scalars(
                        select(ResidentRecoveryJourney)
                        .order_by(ResidentRecoveryJourney.person_id)
                        .with_for_update()
                    )
                ).all()
            )
            for journey in journeys:
                journey.samples, journey.invalid_reason = [], "recovery_settings_changed"
        for key, value in updates.items():
            category, _, description = DEFAULT_DYNAMIC_SETTINGS[key]
            record = records.get(key)
            if record:
                if (
                    record.is_secret
                    and (value is None or value == "")
                    and key not in CLEARABLE_SECRET_KEYS
                ):
                    continue
                if decrypted_value(record) == value:
                    continue
                record.value = setting_payload(key, value)
                record.is_secret = key in SECRET_KEYS
            else:
                record = SystemSetting(
                    key=key,
                    category=category,
                    value=setting_payload(key, value),
                    is_secret=key in SECRET_KEYS,
                    description=description,
                )
                session.add(record)
                records[key] = record
            changed_keys.append(key)
        if changed_keys:
            await write_audit_log(
                session,
                category=TELEMETRY_CATEGORY_CRUD,
                action="settings.update",
                actor=actor_from_user(current_actor) if current_actor else "System",
                actor_user_id=current_actor.id if current_actor else None,
                target_entity="SystemSetting",
                target_label=", ".join(sorted(changed_keys)[:8]),
                diff=audit_diff(
                    {key: before.get(key) for key in changed_keys},
                    {key: public_value(records[key]) for key in changed_keys},
                ),
                metadata={"keys": sorted(changed_keys), "source": source},
            )
        await session.commit()
    invalidate_runtime_config_cache()
    return await list_settings()


async def _validate_admission_setting(session: AsyncSession, candidate: dict[str, Any]) -> None:
    key = str(candidate["gate_admission_device_key"] or "").strip() or None
    if key is None:
        return  # Explicitly unset is valid configuration but blocks automatic admission.
    device = await session.scalar(
        select(AccessDevice)
        .where(AccessDevice.key == key)
        .options(selectinload(AccessDevice.provider_bindings))
        .with_for_update()
    )
    facts = []
    if device is not None:
        facts.append(
            {
                "key": device.key,
                "kind": device.kind,
                "enabled": device.enabled,
                "open_for_access": device.open_for_access,
                "commandable": any(
                    binding_is_commandable(
                        AccessDeviceBinding(
                            binding.provider,
                            binding.external_id,
                            binding.enabled,
                            binding.config or {},
                        ),
                        home_assistant_url=str(candidate["home_assistant_url"] or ""),
                        home_assistant_token=str(candidate["home_assistant_token"] or ""),
                        esphome_devices=normalize_esphome_devices(candidate["esphome_devices"]),
                    )
                    for binding in device.provider_bindings
                ),
            }
        )
    validate_gate_admission_device_key(key, device_facts=facts, allow_unset=False)
