from dataclasses import dataclass
from typing import Any, Protocol

DEFAULT_OPEN_SERVICE = "cover.open_cover"
DEFAULT_CLOSE_SERVICE = "cover.close_cover"


@dataclass(frozen=True)
class CoverCommandOutcome:
    entity_id: str
    name: str
    action: str
    accepted: bool
    state: str
    detail: str | None = None


class HomeAssistantCoverClient(Protocol):
    async def call_service(self, service_name: str, service_data: dict[str, Any]) -> dict[str, Any]:
        ...

    async def get_state(self, entity_id: str) -> Any:
        ...


def normalize_cover_entities(value: Any, *, default_open_service: str = DEFAULT_OPEN_SERVICE) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, dict):
        raw_entities = [value]
    elif isinstance(value, list):
        raw_entities = value
    else:
        return []

    entities: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_entities:
        entity = normalize_cover_entity(raw, default_open_service=default_open_service)
        if not entity or entity["entity_id"] in seen:
            continue
        entities.append(entity)
        seen.add(entity["entity_id"])
    return entities


def normalize_cover_entity(raw: Any, *, default_open_service: str = DEFAULT_OPEN_SERVICE) -> dict[str, Any] | None:
    if isinstance(raw, str):
        entity_id = raw.strip()
        name = title_from_entity_id(entity_id)
        enabled = True
        open_service = default_open_service
        close_service = DEFAULT_CLOSE_SERVICE
    elif isinstance(raw, dict):
        entity_id = str(raw.get("entity_id") or "").strip()
        name = str(raw.get("name") or "").strip() or title_from_entity_id(entity_id)
        enabled = bool(raw.get("enabled", True))
        open_service = default_open_service
        close_service = DEFAULT_CLOSE_SERVICE
    else:
        return None

    if not entity_id.startswith("cover."):
        return None
    return {
        "entity_id": entity_id,
        "name": name,
        "enabled": enabled,
        "open_service": open_service or default_open_service,
        "close_service": close_service or DEFAULT_CLOSE_SERVICE,
    }


def enabled_cover_entities(value: Any, *, default_open_service: str = DEFAULT_OPEN_SERVICE) -> list[dict[str, Any]]:
    return [
        entity
        for entity in normalize_cover_entities(value, default_open_service=default_open_service)
        if entity.get("enabled", True)
    ]


def legacy_gate_entities(entity_id: str, open_service: str) -> list[dict[str, Any]]:
    if not entity_id:
        return []
    return normalize_cover_entities(
        [
            {
                "entity_id": entity_id,
                "name": title_from_entity_id(entity_id),
                "enabled": True,
                "open_service": open_service,
                "close_service": DEFAULT_CLOSE_SERVICE,
            }
        ],
        default_open_service=open_service,
    )


def detected_gate_entities(states: list[Any]) -> list[dict[str, Any]]:
    return _detected_cover_entities(states, role="gate")


def detected_garage_door_entities(states: list[Any]) -> list[dict[str, Any]]:
    return _detected_cover_entities(states, role="garage")


def cover_entity_state_payload(entity: dict[str, Any], state: str | None = None) -> dict[str, Any]:
    return {
        "entity_id": str(entity["entity_id"]),
        "name": str(entity.get("name") or title_from_entity_id(str(entity["entity_id"]))),
        "enabled": bool(entity.get("enabled", True)),
        "open_service": str(entity.get("open_service") or DEFAULT_OPEN_SERVICE),
        "close_service": str(entity.get("close_service") or DEFAULT_CLOSE_SERVICE),
        "state": state,
    }


async def command_cover(
    client: HomeAssistantCoverClient,
    entity: dict[str, Any],
    action: str,
    reason: str,
) -> CoverCommandOutcome:
    if action not in {"open", "close"}:
        return CoverCommandOutcome(
            entity_id=str(entity["entity_id"]),
            name=str(entity.get("name") or entity["entity_id"]),
            action=action,
            accepted=False,
            state="unknown",
            detail=f"Unsupported cover action: {action}",
        )

    service_name = str(entity.get("open_service") if action == "open" else entity.get("close_service"))
    try:
        await client.call_service(service_name, {"entity_id": entity["entity_id"]})
        state = await client.get_state(str(entity["entity_id"]))
    except Exception as exc:
        return CoverCommandOutcome(
            entity_id=str(entity["entity_id"]),
            name=str(entity.get("name") or entity["entity_id"]),
            action=action,
            accepted=False,
            state="fault",
            detail=str(exc),
        )

    return CoverCommandOutcome(
        entity_id=str(entity["entity_id"]),
        name=str(entity.get("name") or entity["entity_id"]),
        action=action,
        accepted=True,
        state=normalize_cover_state(state.state),
        detail=reason,
    )


def title_from_entity_id(entity_id: str) -> str:
    return entity_id.split(".", 1)[-1].replace("_", " ").title() if entity_id else "Cover"


def normalize_cover_state(state: str) -> str:
    normalized = state.lower()
    if normalized in {"open", "on", "unlocked"}:
        return "open"
    if normalized == "opening":
        return "opening"
    if normalized == "closing":
        return "closing"
    if normalized in {"closed", "off", "locked"}:
        return "closed"
    if normalized == "fault":
        return "fault"
    return "unknown"


def _detected_cover_entities(states: list[Any], *, role: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for state in states:
        if not state.entity_id.startswith("cover."):
            continue
        label = f"{state.entity_id} {state.attributes.get('friendly_name') or ''}".lower()
        device_class = str(state.attributes.get("device_class") or "").lower()
        if role == "garage":
            is_match = device_class == "garage" or "garage" in label
        else:
            is_match = device_class == "gate" or "gate" in label
        if not is_match:
            continue
        matches.append(
            {
                "entity_id": state.entity_id,
                "name": str(state.attributes.get("friendly_name") or title_from_entity_id(state.entity_id)),
                "enabled": True,
                "open_service": DEFAULT_OPEN_SERVICE,
                "close_service": DEFAULT_CLOSE_SERVICE,
            }
        )
    return normalize_cover_entities(matches)
