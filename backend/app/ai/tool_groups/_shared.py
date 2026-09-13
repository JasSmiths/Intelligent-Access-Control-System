"""Shared runtime helpers for Alfred tool-group handlers.

Concrete tool handlers live beside their group catalogs; this module keeps the
utilities called by more than one handler. Import external dependencies at their use site.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.ai.context import get_chat_tool_context
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import (
    Person,
    User,
)
from app.services.alfred.answer_contracts import artifact_payload
from app.services.mutation_context import MutationError, require_active_admin
from app.services.settings import (
    get_runtime_config,
)

logger = get_logger(__name__)
DEFAULT_AGENT_TIMEZONE = "Europe/London"


async def _chat_context_user() -> User | None:
    context = get_chat_tool_context()
    user_id = _uuid_from_value(context.get("user_id"))
    if not user_id:
        return None
    async with AsyncSessionLocal() as session:
        return await session.get(User, user_id)


async def _require_admin_user(action: str) -> User | dict[str, Any]:
    context = get_chat_tool_context()
    if str(context.get("user_role") or "").lower() != "admin":
        return {"changed": False, "error": f"Admin access is required for {action}."}
    user = await _chat_context_user()
    try:
        require_active_admin(user)
    except MutationError:
        return {"changed": False, "error": f"Authenticated Admin context is required for {action}."}
    return user


def _schedule_answer_artifacts(payload: dict[str, Any], *, subject: str) -> list[dict[str, Any]]:
    allowed = bool(payload.get("allowed"))
    reason = str(payload.get("reason") or "").strip()
    checked_at = _compact_time_label(payload.get("checked_at_display"))
    display = reason or (f"{subject} is allowed at {checked_at}." if allowed else f"{subject} is not allowed at {checked_at}.")
    return [
        artifact_payload(
            domain="schedules",
            answer_type="schedule_access_verification",
            subject_label=subject,
            primary_fact={
                "id": "schedule.access_allowed",
                "label": "Schedule access allowed",
                "value": allowed,
                "display_value": display,
                "kind": "boolean",
                "source": "schedules",
                "must_appear": True,
            },
            supporting_facts=[
                {
                    "id": "schedule.checked_at",
                    "label": "Checked time",
                    "value": payload.get("checked_at"),
                    "display_value": checked_at,
                    "kind": "timestamp",
                    "source": "schedules",
                    "must_appear": False,
                }
            ],
            source_records=[
                {
                    "entity_type": payload.get("entity_type") or "schedule",
                    "source": payload.get("source"),
                    "schedule_id": payload.get("schedule_id"),
                    "schedule_name": payload.get("schedule_name"),
                    "checked_at": payload.get("checked_at"),
                }
            ],
            display={"voice": "natural_concise", "no_timezone_labels": True},
            canonical_text=display,
        )
    ]


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


async def _person_map(session) -> dict[str, dict[str, str]]:
    people = (
        await session.scalars(select(Person).options(selectinload(Person.group)))
    ).all()
    return {
        str(person.id): {
            "display_name": person.display_name,
            "group": person.group.name if person.group else "",
        }
        for person in people
    }


def _person_match_key(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def _person_record_matches(person: dict[str, str], requested: str) -> bool:
    requested_key = _person_match_key(requested)
    display_key = _person_match_key(person.get("display_name", ""))
    group_key = _person_match_key(person.get("group", ""))
    if not requested_key:
        return False
    if requested_key == display_key or requested_key in display_key:
        return True
    requested_tokens = set(requested_key.split())
    display_tokens = set(display_key.split())
    return bool(requested_tokens and requested_tokens <= display_tokens) or requested_key == group_key


def _entity_match_key(value: str) -> str:
    cleaned = _person_match_key(value)
    tokens = [
        token
        for token in cleaned.split()
        if token not in {"a", "an", "my", "of", "please", "the", "that", "this", "their", "vehicle", "car"}
    ]
    return " ".join(tokens)


def _entity_match_score(query_key: str, candidate_text: str, *, exact_value: str | None = None) -> int:
    candidate_key = _entity_match_key(candidate_text)
    exact_key = _entity_match_key(exact_value or "")
    if not query_key or not candidate_key:
        return 0
    if query_key == exact_key:
        return 100
    if query_key == candidate_key:
        return 95
    if query_key in candidate_key:
        return 80
    query_tokens = set(query_key.split())
    candidate_tokens = set(candidate_key.split())
    if query_tokens and query_tokens <= candidate_tokens:
        return 75
    overlap = query_tokens & candidate_tokens
    if overlap:
        return 50 + min(20, len(overlap) * 5)
    return 0


SECRET_OR_INTERNAL_KEY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "session",
    "token",
    "x-api-key",
)
LARGE_PAYLOAD_KEY_MARKERS = (
    "image",
    "photo",
    "profile_photo_data_url",
    "snapshot",
    "thumbnail",
    "video",
    "vehicle_photo_data_url",
)


def _compact_observation(value: Any) -> Any:
    return _strip_empty(_compact_value(value))


def _compact_value(
    value: Any,
    *,
    key: str | None = None,
    depth: int = 0,
    max_depth: int = 4,
    max_list_items: int = 10,
    max_dict_keys: int = 40,
) -> Any:
    key_lower = (key or "").lower()
    if any(marker in key_lower for marker in SECRET_OR_INTERNAL_KEY_MARKERS):
        return "[redacted]"
    if any(marker in key_lower for marker in LARGE_PAYLOAD_KEY_MARKERS):
        return "[omitted_large_media]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        return value if len(value) <= 800 else f"{value[:800]}... [truncated {len(value) - 800} chars]"
    if isinstance(value, list):
        if depth >= max_depth:
            return {"type": "list", "count": len(value)}
        compacted_list = [
            _compact_value(item, key=key, depth=depth + 1, max_depth=max_depth, max_list_items=max_list_items)
            for item in value[:max_list_items]
        ]
        if len(value) > max_list_items:
            compacted_list.append({"omitted_items": len(value) - max_list_items})
        return compacted_list
    if isinstance(value, dict):
        if depth >= max_depth:
            return {"type": "object", "key_count": len(value), "keys": list(map(str, value.keys()))[:20]}
        items = list(value.items())
        compacted_dict = {
            str(item_key): _compact_value(
                item_value,
                key=str(item_key),
                depth=depth + 1,
                max_depth=max_depth,
                max_list_items=max_list_items,
            )
            for item_key, item_value in items[:max_dict_keys]
        }
        if len(items) > max_dict_keys:
            compacted_dict["omitted_keys"] = len(items) - max_dict_keys
        return compacted_dict
    return str(value)


def _strip_empty(value: Any) -> Any:
    if isinstance(value, list):
        return [
            stripped
            for item in value
            if (stripped := _strip_empty(item)) not in (None, "", [], {})
        ]
    if isinstance(value, dict):
        return {
            key: stripped
            for key, item in value.items()
            if (stripped := _strip_empty(item)) not in (None, "", [], {})
        }
    return value


def _payload_summary(value: Any) -> Any:
    if value in (None, "", [], {}):
        return None
    return _compact_value(value, max_depth=2, max_list_items=6, max_dict_keys=24)


async def _resolve_cover_target(arguments: dict[str, Any], *, entity_type: str) -> dict[str, Any] | None:
    config = await get_runtime_config()
    requested_id = str(
        arguments.get("entity_id")
        or arguments.get("home_assistant_entity_id")
        or arguments.get("cover_entity_id")
        or ""
    ).strip()
    requested_name = _normalize(arguments.get("entity_name") or arguments.get("name") or arguments.get("target"))
    matches: list[tuple[int, dict[str, Any]]] = []

    for kind, entities in _cover_entities_by_kind(config).items():
        if entity_type not in {"door", kind}:
            continue
        setting_key = "home_assistant_gate_entities" if kind == "gate" else "home_assistant_garage_door_entities"
        for entity in entities:
            entity_id = str(entity.get("entity_id") or "")
            name = str(entity.get("name") or entity_id)
            match = {"kind": kind, "setting_key": setting_key, "entity": dict(entity)}
            if requested_id and entity_id == requested_id:
                matches.append((100, match))
            elif requested_name and (requested_name == name.lower() or requested_name in f"{name} {entity_id}".lower()):
                matches.append((90, match))
            elif requested_name:
                score = _cover_target_match_score(requested_name, name, entity_id, kind)
                if score >= 2:
                    matches.append((score, match))

    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    if len(matches) == 1 or matches[0][0] > matches[1][0]:
        return matches[0][1]
    return None


def _cover_target_match_score(requested: str, name: str, entity_id: str, kind: str) -> int:
    requested_key = _cover_match_key(requested)
    name_key = _cover_match_key(name)
    entity_key = _cover_match_key(entity_id)
    kind_key = _cover_match_key(kind)
    candidate_key = " ".join(part for part in [name_key, entity_key, kind_key] if part)
    if not requested_key or not candidate_key:
        return 0
    if requested_key == name_key or requested_key == entity_key:
        return 100
    if requested_key in candidate_key or name_key and name_key in requested_key:
        return 50
    requested_tokens = set(requested_key.split())
    candidate_tokens = set(candidate_key.split())
    return len(requested_tokens & candidate_tokens)


def _cover_match_key(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", value.lower())
    tokens = [
        token
        for token in cleaned.split()
        if token not in {"the", "a", "an", "cover", "entity", "id"}
    ]
    return " ".join(tokens)


def _cover_entities_by_kind(config: Any) -> dict[str, list[dict[str, Any]]]:
    return {
        "gate": list(config.home_assistant_gate_entities),
        "garage_door": list(config.home_assistant_garage_door_entities),
    }


def _parse_agent_datetime(value: Any, timezone_name: str) -> datetime:
    if not value:
        return _agent_now(timezone_name)
    text = str(value).strip()
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_agent_timezone(timezone_name))
    return parsed.astimezone(_agent_timezone(timezone_name))


def _uuid_from_value(value: Any) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _agent_timezone(timezone_name: str | None = None) -> ZoneInfo:
    try:
        return ZoneInfo(str(timezone_name or DEFAULT_AGENT_TIMEZONE))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_AGENT_TIMEZONE)


def _agent_now(timezone_name: str | None = None) -> datetime:
    return datetime.now(tz=_agent_timezone(timezone_name))


def _agent_datetime(value: datetime, timezone_name: str | None = None) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(_agent_timezone(timezone_name))


def _agent_datetime_iso(value: datetime, timezone_name: str | None = None) -> str:
    return _agent_datetime(value, timezone_name).isoformat()


def _agent_datetime_display(value: datetime, timezone_name: str | None = None) -> str:
    return _agent_datetime(value, timezone_name).strftime("%d %b %Y, %H:%M")


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def _preferred_subject_label(arguments: dict[str, Any], fallback: Any) -> str:
    for key in ("person", "visitor_name", "group", "registration_number"):
        value = str(arguments.get(key) or "").strip()
        if value:
            if key in {"person", "visitor_name"} and value == value.lower() and value.replace(" ", "").isalpha():
                return value.title()
            return value
    return str(fallback or "The matched subject").strip() or "The matched subject"


def _compact_time_label(display_value: Any) -> str:
    value = str(display_value or "").strip()
    if ", " not in value:
        return value
    return value.rsplit(", ", 1)[-1].strip() or value
