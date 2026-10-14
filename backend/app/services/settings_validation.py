"""Pure value contract used by settings persistence and runtime conversion.

Errors name fields and constraints only. Never include supplied values: several
fields contain credentials, including structured ESPHome configuration.
"""
from __future__ import annotations

import ipaddress
import json
import math
import re
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

NUMERIC_LIMITS: dict[str, tuple[float, float]] = {
    "auth_access_token_minutes": (5, 43200),
    "auth_remember_days": (1, 365),
    "lpr_debounce_quiet_seconds": (.5, 60),
    "lpr_debounce_max_seconds": (1, 300),
    "lpr_vehicle_session_idle_seconds": (1, 86400),
    "lpr_similarity_threshold": (0, 1),
    "dvla_timeout_seconds": (1, 120),
    "dvsa_timeout_seconds": (1, 20),
    "llm_timeout_seconds": (1, 300),
    "unifi_protect_port": (1, 65535),
    "unifi_protect_snapshot_width": (160, 4096),
    "unifi_protect_snapshot_height": (90, 2160),
    "missed_exit_recovery_gate_latitude": (-90, 90),
    "missed_exit_recovery_gate_longitude": (-180, 180),
}
CHOICES: dict[str, frozenset[str]] = {
    "log_level": frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}),
    "schedule_default_policy": frozenset({"allow", "deny"}),
    "lpr_zone_filter_mode": frozenset({"shadow", "live"}),
    "gate_control_provider": frozenset({"home_assistant", "esphome"}),
    "gate_failover_provider": frozenset({"none", "home_assistant", "esphome"}),
    "llm_provider": frozenset({"local", "openai", "gemini", "claude", "anthropic", "ollama"}),
}
OPTIONAL_NUMBERS = frozenset({"missed_exit_recovery_gate_latitude", "missed_exit_recovery_gate_longitude"})
STRING_LISTS = frozenset({"lpr_allowed_smart_zones", "lpr_webhook_allowed_source_ips", "lpr_webhook_trusted_proxy_ips"})
COVER_LISTS = frozenset({"home_assistant_gate_entities", "home_assistant_garage_door_entities"})


def invalid(key: str, constraint: str) -> ValueError:
    return ValueError(f"{key}: {constraint}")


def number(key: str, value: Any, minimum: float, maximum: float, *, integer: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise invalid(key, "must be a finite number")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise invalid(key, "must be a finite number") from None
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise invalid(key, f"must be finite and between {minimum:g} and {maximum:g}")
    if integer and not result.is_integer():
        raise invalid(key, "must be an integer")
    return result


def boolean(key: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int)) and str(value).strip().lower() in {"true", "1", "yes", "on", "false", "0", "no", "off"}:
        return str(value).strip().lower() in {"true", "1", "yes", "on"}
    raise invalid(key, "must be a boolean")


def string_list(key: str, value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.replace(",", "\n").splitlines() if item.strip()]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise invalid(key, "must be a list of strings")
    return [item.strip() for item in value if item.strip()]


def structured_list(key: str, value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            raise invalid(key, "must be a JSON array") from None
    if not isinstance(value, list):
        raise invalid(key, "must be an array")
    return value


def validate_settings_values(values: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """Normalize supported conversions and validate a complete prospective config."""
    normalized = dict(values)
    for key, default in defaults.items():
        value = values[key]
        if key in NUMERIC_LIMITS:
            if value is None and key in OPTIONAL_NUMBERS:
                continue
            result = number(key, value, *NUMERIC_LIMITS[key], integer=type(default) is int)
            normalized[key] = int(result) if type(default) is int else result
        elif type(default) is bool:
            if key == "missed_exit_recovery_enabled" and type(value) is not bool:
                raise invalid(key, "must be a boolean")
            normalized[key] = boolean(key, value)
        elif key in STRING_LISTS:
            normalized[key] = string_list(key, value)
        elif key in COVER_LISTS:
            # Historical settings also support one cover descriptor.
            covers = [value] if isinstance(value, dict) else structured_list(key, value)
            covers = [dict(item) if isinstance(item, dict) else item for item in covers]
            for item in covers:
                entity = item if isinstance(item, str) else item.get("entity_id") if isinstance(item, dict) else None
                if not isinstance(entity, str) or not entity.strip().startswith("cover."):
                    raise invalid(key, "each entry must identify a cover entity")
                if isinstance(item, dict) and "enabled" in item:
                    item["enabled"] = boolean(key, item["enabled"])
            normalized[key] = covers
        elif key == "esphome_devices":
            devices = [dict(item) if isinstance(item, dict) else item for item in structured_list(key, value)]
            for item in devices:
                if not isinstance(item, dict) or not isinstance(item.get("host"), str) or not item["host"].strip():
                    raise invalid(key, "each device needs a host")
                item["port"] = int(number(key, item.get("port", 6053), 1, 65535, integer=True))
                item["timeout_seconds"] = number(key, item.get("timeout_seconds", 30), 1, 120)
                item["enabled"] = boolean(key, item.get("enabled", True))
                for field in ("id", "key", "name", "encryption_key", "api_encryption_key", "noise_psk"):
                    if field in item and not isinstance(item[field], str):
                        raise invalid(key, "device identity and credentials must be strings")
            normalized[key] = devices
        elif value is None and key == "gate_admission_device_key":
            normalized[key] = ""
        elif not isinstance(value, str):
            raise invalid(key, "must be a string")
        if key in CHOICES:
            choice = str(normalized[key]).strip()
            choice = choice.upper() if key == "log_level" else choice.lower()
            if choice not in CHOICES[key]:
                raise invalid(key, "unsupported choice")
            normalized[key] = choice
    try:
        ZoneInfo(normalized["site_timezone"])
    except (ValueError, ZoneInfoNotFoundError):
        raise invalid("site_timezone", "must be an IANA timezone") from None
    if normalized["lpr_debounce_quiet_seconds"] > normalized["lpr_debounce_max_seconds"]:
        raise invalid("lpr_debounce_max_seconds", "must be at least lpr_debounce_quiet_seconds")
    coordinates = [normalized[key] for key in sorted(OPTIONAL_NUMBERS)]
    if (coordinates[0] is None) != (coordinates[1] is None):
        raise invalid("missed_exit_recovery_gate_latitude", "both gate coordinates must be set or cleared together")
    if normalized["missed_exit_recovery_enabled"] and (None in coordinates or not normalized["gate_admission_device_key"]):
        raise invalid("missed_exit_recovery_enabled", "configure gate coordinates and admission device before enabling recovery")
    for key in ("lpr_webhook_allowed_source_ips", "lpr_webhook_trusted_proxy_ips"):
        try:
            for item in normalized[key]:
                ipaddress.ip_network(item, strict=False)
        except ValueError:
            raise invalid(key, "must contain IP addresses or CIDR networks") from None
    if not re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", normalized["auth_cookie_name"]):
        raise invalid("auth_cookie_name", "must be a nonempty HTTP cookie name")
    for key in ("home_assistant_url", "dvsa_token_url", "dvla_vehicle_enquiry_url", "openai_base_url", "gemini_base_url", "anthropic_base_url", "ollama_base_url"):
        if not normalized[key]:
            continue
        try:
            parsed = urlsplit(normalized[key])
            valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname) and parsed.port != 0
        except ValueError:
            valid = False
        if not valid:
            raise invalid(key, "must be an HTTP or HTTPS URL")
    for key in ("app_name", "openai_model", "gemini_model", "anthropic_model", "ollama_model"):
        if not normalized[key].strip():
            raise invalid(key, "must be nonempty")
    return normalized
