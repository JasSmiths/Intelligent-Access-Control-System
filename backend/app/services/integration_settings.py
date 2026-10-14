"""Explicit compound integration edits, applied to locked current settings.

No database, provider discovery or activation here. Settings persistence owns the
transaction; these operations describe only the accepted integration edit.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from app.core.auth_secret import get_auth_secret
from app.modules.home_assistant.covers import cover_entity_state_payload, normalize_cover_entities
from app.modules.notifications.apprise_urls import (
    normalize_apprise_url,
    split_apprise_urls,
    summarize_apprise_url,
)


class IntegrationSettingsConflict(ValueError):
    pass


class IntegrationSettingTargetMissing(ValueError):
    pass


@dataclass(frozen=True)
class ESPHomeEdit:
    operation: Literal["add", "update", "remove"]
    device_id: str | None = None
    values: dict[str, Any] = field(default_factory=dict)
    key: ClassVar[str] = "esphome_devices"


@dataclass(frozen=True)
class AppriseEdit:
    operation: Literal["add", "remove"]
    url: str = ""
    index: int = -1
    destination_id: str = ""
    revision: str = ""
    key: ClassVar[str] = "apprise_urls"


@dataclass(frozen=True)
class CoverDiscoveryMerge:
    key: Literal["home_assistant_gate_entities", "home_assistant_garage_door_entities"]
    detected: list[dict[str, Any]]


type IntegrationSettingsEdit = ESPHomeEdit | AppriseEdit | CoverDiscoveryMerge


def normalize_esphome_device_id(value: str) -> str:
    return "_".join(str(value or "").strip().lower().replace("-", "_").split())


def esphome_device_summary(device: dict[str, Any]) -> dict[str, Any]:
    return {key: device[key] for key in ("id", "name", "host", "port", "timeout_seconds", "enabled")} | {
        "encryption_key_configured": bool(str(device.get("encryption_key") or "").strip())}


def _fingerprint(domain: str, value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    return hmac.new(get_auth_secret().encode(), domain.encode() + b"\0" + body, hashlib.sha256).hexdigest()


def apprise_revision(configured: str) -> str:
    return _fingerprint("apprise-list-v1", [normalize_apprise_url(url) for url in split_apprise_urls(configured)])


def apprise_destinations(configured: str) -> list[dict[str, Any]]:
    urls = [normalize_apprise_url(url) for url in split_apprise_urls(configured)]
    revision = apprise_revision(configured)
    return [{**summarize_apprise_url(index, url),
             "destination_id": _fingerprint("apprise-destination-v1", url), "revision": revision}
            for index, url in enumerate(urls)]


def apply_integration_edit(edit: IntegrationSettingsEdit, current: dict[str, Any]) -> dict[str, Any]:
    if isinstance(edit, ESPHomeEdit):
        devices = [dict(device) for device in current[edit.key]]
        if edit.operation == "add":
            base = normalize_esphome_device_id(edit.values["name"]) or "esphome_device"
            existing = {device["id"] for device in devices}
            identity, suffix = base, 2
            while identity in existing:
                identity, suffix = f"{base}_{suffix}", suffix + 1
            devices.append({**edit.values, "id": identity})
        else:
            index = next((i for i, device in enumerate(devices) if device["id"] == edit.device_id), None)
            if index is None:
                raise IntegrationSettingTargetMissing("ESPHome device not found.")
            if edit.operation == "remove":
                devices.pop(index)
            else:
                devices[index].update({key: value for key, value in edit.values.items() if value is not None})
        return {edit.key: devices}
    if isinstance(edit, AppriseEdit):
        urls = [normalize_apprise_url(url) for url in split_apprise_urls(current[edit.key])]
        if edit.operation == "add":
            normalized = normalize_apprise_url(edit.url)
            if normalized not in urls:
                urls.append(normalized)
        else:
            if not hmac.compare_digest(edit.revision, _fingerprint("apprise-list-v1", urls)):
                raise IntegrationSettingsConflict("Apprise configuration changed. Refresh destinations and confirm the selected destination again.")
            if not 0 <= edit.index < len(urls):
                raise IntegrationSettingTargetMissing("Apprise URL not found.")
            if not hmac.compare_digest(edit.destination_id, _fingerprint("apprise-destination-v1", urls[edit.index])):
                raise IntegrationSettingsConflict("Apprise destination changed. Refresh destinations and create a fresh confirmation.")
            # The revision binds order and multiplicity. A fresh duplicate
            # selection removes exactly one occurrence, never a different URL.
            urls.pop(edit.index)
        return {edit.key: "\n".join(urls)}
    default = current["home_assistant_gate_open_service"]
    merged = normalize_cover_entities(current[edit.key], default_open_service=default)
    existing = {entity["entity_id"] for entity in merged}
    for entity in normalize_cover_entities(edit.detected, default_open_service=default):
        if entity["entity_id"] not in existing:
            merged.append(entity)
            existing.add(entity["entity_id"])
    return {edit.key: merged}


def integration_representation(edit: IntegrationSettingsEdit, accepted: dict[str, Any]) -> dict[str, Any]:
    if isinstance(edit, ESPHomeEdit):
        return {"devices": [esphome_device_summary(device) for device in accepted[edit.key]]}
    if isinstance(edit, AppriseEdit):
        return {"urls": apprise_destinations(accepted[edit.key])}
    key = "gate_entities" if edit.key == "home_assistant_gate_entities" else "garage_door_entities"
    return {key: [cover_entity_state_payload(entity) for entity in accepted[edit.key]]}
