"""Read-only, conservative mobile-app identity discovery for recovery drafts."""
import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import Person
from app.modules.home_assistant.client import HomeAssistantClient, HomeAssistantState


class RecoveryTracker(BaseModel):
    entity_id: str
    name: str
    available: bool
    eligible: bool
    reason: str | None


class RecoveryTrackerMapping(BaseModel):
    person_id: str
    notify_service_id: str | None
    suggested_tracker_entity_id: str | None = None
    status: Literal["matched", "ambiguous", "not_found", "unavailable"]
    reason: str | None


class RecoveryTrackerDiscovery(BaseModel):
    trackers: list[RecoveryTracker]
    mapping: RecoveryTrackerMapping | None
    status: Literal["complete", "unavailable"]
    reason: str | None


def _mobile_device(device: dict[str, Any], mobile_ids: set[str]) -> bool:
    return device.get("id") in mobile_ids or any(
        isinstance(identifier, (list, tuple)) and len(identifier) == 2
        and identifier[0] == "mobile_app"
        for identifier in (device.get("identifiers") or [])
    )


def _tracker_reason(
    entity: dict[str, Any] | None, device: dict[str, Any] | None,
    state: HomeAssistantState | None,
) -> str | None:
    if entity is None or device is None:
        return "registry_link_missing"
    if entity.get("platform") != "mobile_app":
        return "not_mobile_app"
    entry_id = entity.get("config_entry_id")
    if not entry_id or entry_id not in (device.get("config_entries") or []):
        return "registry_link_inconsistent"
    if entity.get("disabled_by") is not None or device.get("disabled_by") is not None:
        return "disabled"
    if device.get("manufacturer") != "Apple" or not str(device.get("model") or "").startswith("iPhone"):
        return "not_iphone"
    if state is None or state.state in {"unknown", "unavailable"}:
        return "tracker_unavailable"
    if state.attributes.get("source_type") != "gps":
        return "not_gps"
    return None


@dataclass(frozen=True)
class DiscoveryOwner:
    id: UUID
    is_active: bool
    notify_service: str | None
    shared_destination: bool


def build_discovery(
    owner: DiscoveryOwner, states: list[HomeAssistantState], advertised_services: set[str],
    entities: list[dict[str, Any]] | None, devices: list[dict[str, Any]] | None,
    *, device_services: dict[str, str] | None = None, unavailable_reason: str | None = None,
) -> RecoveryTrackerDiscovery:
    """Project safe fields only; identity never depends on resident/entity names."""
    registry_available = entities is not None and devices is not None and device_services is not None
    entity_rows = entities or []
    device_rows = devices or []
    if registry_available:
        for rows, key in ((entity_rows, "entity_id"), (device_rows, "id")):
            keys = [row.get(key) for row in rows]
            if any(not isinstance(value, str) or not value for value in keys) or len(set(keys)) != len(keys):
                raise ValueError("Invalid registry identity metadata")
        if any(not isinstance(row.get("config_entries"), list) or any(
            not isinstance(entry, str) for entry in row["config_entries"]
        ) for row in device_rows):
            raise ValueError("Invalid registry configuration metadata")
    registry_by_entity = {row.get("entity_id"): row for row in entity_rows}
    device_by_id = {row.get("id"): row for row in device_rows}
    state_by_entity = {state.entity_id: state for state in states}
    mobile_ids = {row["device_id"] for row in entity_rows
                  if row.get("platform") == "mobile_app" and isinstance(row.get("device_id"), str)}
    service_devices: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for device in device_rows:
        if _mobile_device(device, mobile_ids):
            service = (device_services or {}).get(device["id"])
            if not service:
                raise ValueError("Missing normalized mobile device identity")
            service_devices[service].append(device)

    tracker_ids = {state.entity_id for state in states if state.entity_id.startswith("device_tracker.")}
    tracker_ids.update(row["entity_id"] for row in entity_rows
                       if isinstance(row.get("entity_id"), str)
                       and row["entity_id"].startswith("device_tracker."))
    trackers = []
    for entity_id in sorted(tracker_ids):
        state = state_by_entity.get(entity_id)
        row = registry_by_entity.get(entity_id)
        tracker_device = device_by_id.get(row.get("device_id")) if row else None
        tracker_reason = _tracker_reason(row, tracker_device, state) if registry_available else "registry_unavailable"
        trackers.append(RecoveryTracker(
            entity_id=entity_id,
            name=str(state.attributes.get("friendly_name") or entity_id) if state else entity_id,
            available=state is not None and state.state not in {"unknown", "unavailable"},
            eligible=tracker_reason is None, reason=tracker_reason,
        ))
    tracker_by_id = {tracker.entity_id: tracker for tracker in trackers}
    mapping = None
    if owner.is_active:
        notify = owner.notify_service
        status: Literal["matched", "ambiguous", "not_found", "unavailable"] = "not_found"
        reason: str | None = "saved_notify_service_missing"
        suggestion = None
        if notify:
            matches = service_devices.get(notify, [])
            if owner.shared_destination:
                status, reason = "ambiguous", "notify_service_shared"
            elif not registry_available:
                status, reason = "unavailable", unavailable_reason or "registry_unavailable"
            elif notify not in advertised_services:
                status, reason = "unavailable", "saved_notify_service_unavailable"
            elif len(matches) > 1:
                status, reason = "ambiguous", "device_name_collision"
            elif not matches:
                reason = "device_not_found"
            else:
                device = matches[0]
                linked = [row for row in entity_rows
                          if row.get("platform") == "mobile_app" and row.get("device_id") == device.get("id")
                          and str(row.get("entity_id") or "").startswith("device_tracker.")]
                eligible = [tracker_by_id[row["entity_id"]] for row in linked
                            if tracker_by_id[row["entity_id"]].eligible]
                linked_entries = {row.get("config_entry_id") for row in linked}
                if len(linked_entries) > 1:
                    status, reason = "ambiguous", "multiple_mobile_app_registrations"
                elif len(eligible) > 1:
                    status, reason = "ambiguous", "multiple_eligible_trackers"
                elif not eligible:
                    status = "unavailable" if any(
                        tracker_by_id[row["entity_id"]].reason == "tracker_unavailable" for row in linked
                    ) else "not_found"
                    reason = "eligible_tracker_unavailable" if status == "unavailable" else "eligible_tracker_not_found"
                else:
                    status, reason, suggestion = "matched", None, eligible[0].entity_id
        mapping = RecoveryTrackerMapping(
            person_id=str(owner.id), notify_service_id=notify,
            suggested_tracker_entity_id=suggestion, status=status, reason=reason,
        )
    return RecoveryTrackerDiscovery(
        trackers=trackers, mapping=mapping,
        status="complete" if registry_available else "unavailable",
        reason=None if registry_available else unavailable_reason or "registry_unavailable",
    )


async def discover_recovery_trackers(
    session: AsyncSession, client: HomeAssistantClient, owner_id: UUID,
) -> RecoveryTrackerDiscovery | None:
    other = aliased(Person)
    shared = exists().where(
        other.id != Person.id,
        other.home_assistant_mobile_app_notify_service == Person.home_assistant_mobile_app_notify_service,
    )
    row = (await session.execute(select(
        Person.id, Person.is_active, Person.home_assistant_mobile_app_notify_service, shared,
    ).where(Person.id == owner_id))).one_or_none()
    # End this read-only request transaction before waiting for Home Assistant.
    await session.rollback()
    if row is None:
        return None
    owner = DiscoveryOwner(*row)
    states: list[HomeAssistantState] = []
    services: set[str] = set()
    try:
        async with asyncio.timeout(15):
            config = await client.config()
            states = await client.list_states(runtime_config=config)
            services = {service.service_id for service in await client.list_services(runtime_config=config)}
            entities, devices = await client.list_recovery_registries(runtime_config=config)
            mobile_ids = {row["device_id"] for row in entities
                          if row.get("platform") == "mobile_app" and isinstance(row.get("device_id"), str)}
            mobile_devices = [device for device in devices if _mobile_device(device, mobile_ids)]
            # HA derives the legacy notify destination from ORIGINAL name;
            # user overrides and friendly tracker names never participate.
            normalized = await client.normalize_mobile_app_service_names(
                [device.get("name") for device in mobile_devices], runtime_config=config,
            )
            device_services = {device.get("id"): service for device, service in zip(mobile_devices, normalized, strict=True)}
        result = build_discovery(owner, states, services, entities, devices, device_services=device_services)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - vendor failures must return only safe discovery status
        return build_discovery(owner, states, services, None, None, unavailable_reason="discovery_unavailable")
    mapping = result.mapping
    if mapping and mapping.suggested_tracker_entity_id and await session.scalar(select(exists().where(
        Person.id != owner_id, Person.missed_exit_recovery_tracker_entity_id == mapping.suggested_tracker_entity_id,
    ))):
        mapping.status = "ambiguous"
        mapping.reason = "tracker_assigned_elsewhere"
        mapping.suggested_tracker_entity_id = None
    return result
