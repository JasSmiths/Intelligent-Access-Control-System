from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionLocal
from app.models import AccessEvent, Anomaly, Person, Presence, User
from app.models.enums import AccessDecision, AccessDirection
from app.ai.providers import ImageAnalysisUnsupportedError, analyze_image_with_provider
from app.modules.dvla.vehicle_enquiry import DvlaVehicleEnquiryError, display_vehicle_record, normalize_registration_number
from app.modules.unifi_protect.client import UnifiProtectError
from app.modules.notifications.base import NotificationContext
from app.services.dvla import lookup_vehicle_registration
from app.services.notifications import get_notification_service
from app.services.settings import get_runtime_config
from app.services.unifi_protect import get_unifi_protect_service

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def as_llm_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


def build_agent_tools() -> dict[str, AgentTool]:
    tools = [
        AgentTool(
            name="query_presence",
            description="Return current presence state for everyone or a named person.",
            parameters={
                "type": "object",
                "properties": {"person": {"type": "string"}},
                "additionalProperties": False,
            },
            handler=query_presence,
        ),
        AgentTool(
            name="query_access_events",
            description="Return recent access events, optionally filtered by person, group/category, plate, or day.",
            parameters={
                "type": "object",
                "properties": {
                    "person": {"type": "string"},
                    "group": {"type": "string"},
                    "registration_number": {"type": "string"},
                    "day": {"type": "string", "enum": ["today", "yesterday", "recent"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "additionalProperties": False,
            },
            handler=query_access_events,
        ),
        AgentTool(
            name="query_anomalies",
            description="Return recent anomaly records and unresolved alerts.",
            parameters={
                "type": "object",
                "properties": {
                    "severity": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "additionalProperties": False,
            },
            handler=query_anomalies,
        ),
        AgentTool(
            name="summarize_access_rhythm",
            description="Summarize arrivals, exits, denials, and anomalies for a recent period.",
            parameters={
                "type": "object",
                "properties": {"day": {"type": "string", "enum": ["today", "yesterday", "recent"]}},
                "additionalProperties": False,
            },
            handler=summarize_access_rhythm,
        ),
        AgentTool(
            name="calculate_visit_duration",
            description="Calculate how long a person or group stayed on site today or recently.",
            parameters={
                "type": "object",
                "properties": {
                    "person": {"type": "string"},
                    "group": {"type": "string"},
                    "day": {"type": "string", "enum": ["today", "yesterday", "recent"]},
                },
                "additionalProperties": False,
            },
            handler=calculate_visit_duration,
        ),
        AgentTool(
            name="trigger_anomaly_alert",
            description="Send a contextual anomaly alert notification.",
            parameters={
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                    "message": {"type": "string"},
                },
                "required": ["subject", "severity", "message"],
                "additionalProperties": False,
            },
            handler=trigger_anomaly_alert,
        ),
        AgentTool(
            name="get_system_users",
            description="Return non-sensitive system account roster for access-control context.",
            parameters={
                "type": "object",
                "properties": {"include_inactive": {"type": "boolean"}},
                "additionalProperties": False,
            },
            handler=get_system_users,
        ),
        AgentTool(
            name="lookup_dvla_vehicle",
            description="Look up UK vehicle details from the DVLA Vehicle Enquiry Service by registration number.",
            parameters={
                "type": "object",
                "properties": {
                    "registration_number": {
                        "type": "string",
                        "description": "Vehicle registration number without spaces or punctuation.",
                    },
                },
                "required": ["registration_number"],
                "additionalProperties": False,
            },
            handler=lookup_dvla_vehicle,
        ),
        AgentTool(
            name="analyze_camera_snapshot",
            description="Fetch a current UniFi Protect camera snapshot and ask the active vision-capable AI provider to analyze it.",
            parameters={
                "type": "object",
                "properties": {
                    "camera_id": {"type": "string"},
                    "camera_name": {"type": "string"},
                    "prompt": {
                        "type": "string",
                        "description": "What to inspect in the snapshot.",
                    },
                    "provider": {"type": "string"},
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
            handler=analyze_camera_snapshot,
        ),
    ]
    return {tool.name: tool for tool in tools}


async def query_presence(arguments: dict[str, Any]) -> dict[str, Any]:
    person_filter = _normalize(arguments.get("person"))
    async with AsyncSessionLocal() as session:
        query = select(Presence).options(selectinload(Presence.person)).order_by(Presence.updated_at.desc())
        rows = (await session.scalars(query)).all()

    records = [
        {
            "person": row.person.display_name,
            "state": row.state.value,
            "last_changed_at": row.last_changed_at.isoformat() if row.last_changed_at else None,
        }
        for row in rows
        if not person_filter or person_filter in row.person.display_name.lower()
    ]
    return {"presence": records}


async def query_access_events(arguments: dict[str, Any]) -> dict[str, Any]:
    limit = int(arguments.get("limit") or 25)
    start, end = _period_bounds(arguments.get("day") or "recent")

    async with AsyncSessionLocal() as session:
        query = (
            select(AccessEvent)
            .options(selectinload(AccessEvent.vehicle), selectinload(AccessEvent.anomalies))
            .where(AccessEvent.occurred_at >= start, AccessEvent.occurred_at <= end)
            .order_by(AccessEvent.occurred_at.desc())
            .limit(limit)
        )
        events = (await session.scalars(query)).all()

        person_filter = _normalize(arguments.get("person"))
        group_filter = _normalize(arguments.get("group"))
        person_map = await _person_map(session)

    records = []
    for event in events:
        person = person_map.get(str(event.person_id)) if event.person_id else None
        if person_filter and (not person or person_filter not in person["display_name"].lower()):
            continue
        if group_filter and (not person or group_filter not in person.get("group", "").lower()):
            continue
        plate_filter = _normalize(arguments.get("registration_number"))
        if plate_filter and plate_filter not in event.registration_number.lower():
            continue
        records.append(
            {
                "person": person["display_name"] if person else None,
                "group": person.get("group") if person else None,
                "registration_number": event.registration_number,
                "direction": event.direction.value,
                "decision": event.decision.value,
                "confidence": event.confidence,
                "occurred_at": event.occurred_at.isoformat(),
                "anomaly_count": len(event.anomalies),
            }
        )

    return {"events": records, "count": len(records)}


async def query_anomalies(arguments: dict[str, Any]) -> dict[str, Any]:
    limit = int(arguments.get("limit") or 25)
    severity = _normalize(arguments.get("severity"))
    async with AsyncSessionLocal() as session:
        query = select(Anomaly).order_by(Anomaly.created_at.desc()).limit(limit)
        anomalies = (await session.scalars(query)).all()

    records = [
        {
            "type": anomaly.anomaly_type.value,
            "severity": anomaly.severity.value,
            "message": anomaly.message,
            "created_at": anomaly.created_at.isoformat(),
        }
        for anomaly in anomalies
        if not severity or severity == anomaly.severity.value
    ]
    return {"anomalies": records, "count": len(records)}


async def summarize_access_rhythm(arguments: dict[str, Any]) -> dict[str, Any]:
    result = await query_access_events({"day": arguments.get("day") or "today", "limit": 100})
    events = result["events"]
    return {
        "period": arguments.get("day") or "today",
        "total_events": len(events),
        "entries": sum(1 for event in events if event["direction"] == "entry"),
        "exits": sum(1 for event in events if event["direction"] == "exit"),
        "denials": sum(1 for event in events if event["decision"] == "denied"),
        "anomaly_events": sum(1 for event in events if event["anomaly_count"] > 0),
        "events": events[:10],
    }


async def calculate_visit_duration(arguments: dict[str, Any]) -> dict[str, Any]:
    result = await query_access_events(
        {
            "person": arguments.get("person"),
            "group": arguments.get("group"),
            "day": arguments.get("day") or "today",
            "limit": 100,
        }
    )
    events = sorted(result["events"], key=lambda item: item["occurred_at"])
    open_entry: datetime | None = None
    total = timedelta()
    intervals: list[dict[str, str]] = []

    for event in events:
        occurred = datetime.fromisoformat(event["occurred_at"])
        if event["decision"] != AccessDecision.GRANTED.value:
            continue
        if event["direction"] == AccessDirection.ENTRY.value:
            open_entry = occurred
        elif event["direction"] == AccessDirection.EXIT.value and open_entry:
            total += occurred - open_entry
            intervals.append({"entry": open_entry.isoformat(), "exit": occurred.isoformat()})
            open_entry = None

    if open_entry:
        now = datetime.now(tz=UTC)
        total += now - open_entry
        intervals.append({"entry": open_entry.isoformat(), "exit": "still_present"})

    return {
        "duration_seconds": int(total.total_seconds()),
        "duration_human": _human_duration(total),
        "intervals": intervals,
        "matched_events": len(events),
    }


async def trigger_anomaly_alert(arguments: dict[str, Any]) -> dict[str, Any]:
    notification = await get_notification_service().notify(
        NotificationContext(
            event_type="agent_anomaly_alert",
            subject=str(arguments["subject"]),
            severity=str(arguments["severity"]),
            facts={"message": str(arguments["message"])},
        )
    )
    return {"sent": True, "title": notification.title, "body": notification.body}


async def get_system_users(arguments: dict[str, Any]) -> dict[str, Any]:
    include_inactive = bool(arguments.get("include_inactive"))
    async with AsyncSessionLocal() as session:
        query = select(User).order_by(User.first_name, User.last_name)
        users = (await session.scalars(query)).all()

    records = [
        {
            "full_name": user.full_name,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "username": user.username,
            "role": user.role.value,
            "is_active": user.is_active,
        }
        for user in users
        if include_inactive or user.is_active
    ]
    return {"users": records, "count": len(records)}


async def lookup_dvla_vehicle(arguments: dict[str, Any]) -> dict[str, Any]:
    registration_number = normalize_registration_number(str(arguments.get("registration_number") or ""))
    if not registration_number:
        return {"error": "registration_number is required."}
    try:
        vehicle = await lookup_vehicle_registration(registration_number)
    except DvlaVehicleEnquiryError as exc:
        return {
            "registration_number": registration_number,
            "error": str(exc),
        }
    return {
        "registration_number": registration_number,
        "vehicle": vehicle,
        "display_vehicle": display_vehicle_record(vehicle, registration_number),
    }


async def analyze_camera_snapshot(arguments: dict[str, Any]) -> dict[str, Any]:
    camera_identifier = str(arguments.get("camera_id") or arguments.get("camera_name") or "").strip()
    if not camera_identifier:
        return {"error": "camera_id or camera_name is required."}
    prompt = str(arguments.get("prompt") or "Describe what is visible in this camera snapshot.").strip()
    runtime = await get_runtime_config()
    provider = str(arguments.get("provider") or runtime.llm_provider)

    try:
        media = await get_unifi_protect_service().snapshot(
            camera_identifier,
            width=runtime.unifi_protect_snapshot_width,
            height=runtime.unifi_protect_snapshot_height,
        )
        result = await analyze_image_with_provider(
            provider,
            prompt=prompt,
            image_bytes=media.content,
            mime_type=media.content_type,
        )
    except (UnifiProtectError, ImageAnalysisUnsupportedError, Exception) as exc:
        return {"camera": camera_identifier, "provider": provider, "error": str(exc)}

    return {
        "camera": camera_identifier,
        "provider": provider,
        "analysis": result.text,
        "snapshot_retained": False,
    }


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


def _period_bounds(day: str) -> tuple[datetime, datetime]:
    now = datetime.now(tz=UTC)
    if day == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now
    if day == "yesterday":
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return today - timedelta(days=1), today
    return now - timedelta(days=14), now


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def _human_duration(duration: timedelta) -> str:
    seconds = int(duration.total_seconds())
    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"
