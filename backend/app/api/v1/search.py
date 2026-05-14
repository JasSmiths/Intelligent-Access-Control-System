import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import current_user
from app.db.session import get_db_session
from app.models import (
    AccessEvent,
    Anomaly,
    AutomationRule,
    Group,
    NotificationRule,
    Person,
    Schedule,
    User,
    Vehicle,
    VehiclePersonAssignment,
    VisitorPass,
)
from app.models.enums import UserRole

router = APIRouter()

SearchResultType = Literal[
    "person",
    "vehicle",
    "group",
    "schedule",
    "visitor_pass",
    "access_event",
    "alert",
    "user",
    "automation_rule",
    "notification_rule",
]
ViewTarget = Literal[
    "dashboard",
    "people",
    "groups",
    "schedules",
    "passes",
    "vehicles",
    "top_charts",
    "events",
    "alerts",
    "reports",
    "integrations",
    "logs",
    "settings",
    "settings_general",
    "settings_auth",
    "alfred_training",
    "settings_automations",
    "settings_notifications",
    "settings_lpr",
    "users",
]


class SearchTarget(BaseModel):
    view: ViewTarget
    route_search: str | None = None


class SearchPreviewFact(BaseModel):
    label: str
    value: str


class SearchPreview(BaseModel):
    title: str
    body: str | None = None
    badges: list[str]
    facts: list[SearchPreviewFact]


class GlobalSearchResult(BaseModel):
    id: str
    type: SearchResultType
    label: str
    subtitle: str
    filter_value: str
    target: SearchTarget
    preview: SearchPreview


@dataclass(frozen=True)
class SearchCandidate:
    result: GlobalSearchResult
    search_texts: tuple[str, ...]
    plate_texts: tuple[str, ...] = ()


@router.get("", response_model=list[GlobalSearchResult], response_model_exclude_none=True)
async def global_search(
    q: str = Query(default="", max_length=120),
    limit: int = Query(default=12, ge=1, le=25),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[GlobalSearchResult]:
    query = q.strip()
    if not query:
        return []

    candidates = await collect_global_search_results(
        session,
        include_admin=user.role == UserRole.ADMIN,
    )
    return rank_search_results(candidates, query, limit=limit)


async def collect_global_search_results(
    session: AsyncSession,
    *,
    include_admin: bool,
) -> list[SearchCandidate]:
    candidates: list[SearchCandidate] = []
    candidates.extend(await _person_candidates(session))
    candidates.extend(await _vehicle_candidates(session))
    candidates.extend(await _group_candidates(session))
    candidates.extend(await _schedule_candidates(session))
    candidates.extend(await _visitor_pass_candidates(session))
    candidates.extend(await _access_event_candidates(session))
    candidates.extend(await _alert_candidates(session))
    if include_admin:
        candidates.extend(await _user_candidates(session))
        candidates.extend(await _automation_rule_candidates(session))
        candidates.extend(await _notification_rule_candidates(session))
    return candidates


def rank_search_results(
    candidates: list[SearchCandidate],
    query: str,
    *,
    limit: int,
) -> list[GlobalSearchResult]:
    scored: list[tuple[int, str, GlobalSearchResult]] = []
    for candidate in candidates:
        score = _candidate_score(candidate, query)
        if score is not None:
            scored.append((score, candidate.result.label.lower(), candidate.result))
    scored.sort(key=lambda item: (item[0], item[1], item[2].type))
    return [item[2] for item in scored[:limit]]


async def _person_candidates(session: AsyncSession) -> list[SearchCandidate]:
    rows = (
        await session.scalars(
            select(Person)
            .options(
                selectinload(Person.group),
                selectinload(Person.schedule),
                selectinload(Person.vehicles).selectinload(Vehicle.schedule),
                selectinload(Person.vehicle_assignments)
                .selectinload(VehiclePersonAssignment.vehicle)
                .selectinload(Vehicle.schedule),
            )
            .order_by(Person.display_name)
            .limit(300)
        )
    ).all()
    return [_person_candidate(row) for row in rows]


def _person_candidate(person: Person) -> SearchCandidate:
    vehicles = _assigned_vehicles_for_person(person)
    vehicle_regs = [_text(vehicle.registration_number) for vehicle in vehicles]
    group_name = _text(person.group.name if person.group else None)
    schedule_name = _text(person.schedule.name if person.schedule else None)
    badges = ["Person", "Active" if person.is_active else "Inactive"]
    if group_name:
        badges.append(group_name)
    facts = [
        *_fact("Group", group_name),
        *_fact("Schedule", schedule_name),
        *_fact("Vehicles", ", ".join(vehicle_regs)),
        *_fact("Garage doors", ", ".join(person.garage_door_entity_ids or [])),
    ]
    label = _text(person.display_name) or "Unnamed person"
    return SearchCandidate(
        result=GlobalSearchResult(
            id=str(person.id),
            type="person",
            label=label,
            subtitle="Directory person",
            filter_value=label,
            target=SearchTarget(view="people"),
            preview=SearchPreview(
                title=label,
                body=_text(person.notes),
                badges=badges,
                facts=facts,
            ),
        ),
        search_texts=(
            label,
            _text(person.first_name),
            _text(person.last_name),
            group_name,
            schedule_name,
            _text(person.home_assistant_mobile_app_notify_service),
            *vehicle_regs,
        ),
        plate_texts=tuple(vehicle_regs),
    )


async def _vehicle_candidates(session: AsyncSession) -> list[SearchCandidate]:
    rows = (
        await session.scalars(
            select(Vehicle)
            .options(
                selectinload(Vehicle.owner),
                selectinload(Vehicle.schedule),
                selectinload(Vehicle.person_assignments).selectinload(
                    VehiclePersonAssignment.person
                ),
            )
            .order_by(Vehicle.registration_number)
            .limit(300)
        )
    ).all()
    return [_vehicle_candidate(row) for row in rows]


def _vehicle_candidate(vehicle: Vehicle) -> SearchCandidate:
    owner_names = _owner_names_for_vehicle(vehicle)
    owner_label = ", ".join(owner_names)
    schedule_name = _text(vehicle.schedule.name if vehicle.schedule else None)
    vehicle_detail = " ".join(
        part for part in [_text(vehicle.color), _text(vehicle.make), _text(vehicle.model)] if part
    )
    registration = _text(vehicle.registration_number)
    facts = [
        *_fact("Owner", owner_label),
        *_fact("Vehicle", vehicle_detail),
        *_fact("Schedule", schedule_name),
        *_fact("MOT", _dated_status(vehicle.mot_status, vehicle.mot_expiry)),
        *_fact("Tax", _dated_status(vehicle.tax_status, vehicle.tax_expiry)),
    ]
    return SearchCandidate(
        result=GlobalSearchResult(
            id=str(vehicle.id),
            type="vehicle",
            label=registration,
            subtitle=owner_label or vehicle_detail or "Vehicle",
            filter_value=registration,
            target=SearchTarget(view="vehicles"),
            preview=SearchPreview(
                title=registration,
                body=_text(vehicle.description),
                badges=["Vehicle", "Active" if vehicle.is_active else "Inactive"],
                facts=facts,
            ),
        ),
        search_texts=(
            registration,
            owner_label,
            schedule_name,
            _text(vehicle.make),
            _text(vehicle.model),
            _text(vehicle.color),
            _text(vehicle.description),
        ),
        plate_texts=(registration,),
    )


async def _group_candidates(session: AsyncSession) -> list[SearchCandidate]:
    rows = (
        await session.scalars(
            select(Group).options(selectinload(Group.people)).order_by(Group.name).limit(150)
        )
    ).all()
    return [_group_candidate(row) for row in rows]


def _group_candidate(group: Group) -> SearchCandidate:
    category = _enum_value(group.category)
    facts = [
        *_fact("Category", _title(category)),
        *_fact("Subtype", group.subtype),
        *_fact("People", str(len(group.people)) if group.people is not None else ""),
    ]
    return SearchCandidate(
        result=GlobalSearchResult(
            id=str(group.id),
            type="group",
            label=group.name,
            subtitle=f"{_title(category)} group",
            filter_value=group.name,
            target=SearchTarget(view="groups"),
            preview=SearchPreview(
                title=group.name,
                body=_text(group.description),
                badges=["Group", _title(category)],
                facts=facts,
            ),
        ),
        search_texts=(group.name, category, _text(group.subtype), _text(group.description)),
    )


async def _schedule_candidates(session: AsyncSession) -> list[SearchCandidate]:
    rows = (await session.scalars(select(Schedule).order_by(Schedule.name).limit(150))).all()
    return [_schedule_candidate(row) for row in rows]


def _schedule_candidate(schedule: Schedule) -> SearchCandidate:
    blocks = schedule.time_blocks if isinstance(schedule.time_blocks, dict) else {}
    active_days = [day for day, ranges in blocks.items() if ranges]
    facts = [
        *_fact("Active days", ", ".join(day.title() for day in active_days)),
        *_fact("Updated", _format_datetime(schedule.updated_at)),
    ]
    return SearchCandidate(
        result=GlobalSearchResult(
            id=str(schedule.id),
            type="schedule",
            label=schedule.name,
            subtitle="Access schedule",
            filter_value=schedule.name,
            target=SearchTarget(view="schedules"),
            preview=SearchPreview(
                title=schedule.name,
                body=_text(schedule.description),
                badges=["Schedule"],
                facts=facts,
            ),
        ),
        search_texts=(schedule.name, _text(schedule.description), " ".join(active_days)),
    )


async def _visitor_pass_candidates(session: AsyncSession) -> list[SearchCandidate]:
    rows = (
        await session.scalars(
            select(VisitorPass)
            .order_by(VisitorPass.expected_time.desc(), VisitorPass.created_at.desc())
            .limit(300)
        )
    ).all()
    return [_visitor_pass_candidate(row) for row in rows]


def _visitor_pass_candidate(visitor_pass: VisitorPass) -> SearchCandidate:
    number_plate = _text(visitor_pass.number_plate)
    label = visitor_pass.visitor_name
    filter_value = number_plate or label
    facts = [
        *_fact("Plate", number_plate),
        *_fact("Phone", visitor_pass.visitor_phone),
        *_fact("Expected", _format_datetime(visitor_pass.expected_time)),
        *_fact("Arrived", _format_datetime(visitor_pass.arrival_time)),
        *_fact("Departed", _format_datetime(visitor_pass.departure_time)),
        *_fact(
            "Vehicle",
            " ".join(
                part for part in [visitor_pass.vehicle_colour, visitor_pass.vehicle_make] if part
            ),
        ),
    ]
    return SearchCandidate(
        result=GlobalSearchResult(
            id=str(visitor_pass.id),
            type="visitor_pass",
            label=label,
            subtitle=number_plate or _title(_enum_value(visitor_pass.status)),
            filter_value=filter_value,
            target=SearchTarget(view="passes"),
            preview=SearchPreview(
                title=label,
                body=None,
                badges=[
                    "Visitor Pass",
                    _title(_enum_value(visitor_pass.status)),
                    _title(_enum_value(visitor_pass.pass_type)),
                ],
                facts=facts,
            ),
        ),
        search_texts=(
            label,
            number_plate,
            _text(visitor_pass.visitor_phone),
            _enum_value(visitor_pass.status),
            _enum_value(visitor_pass.pass_type),
            _text(visitor_pass.vehicle_make),
            _text(visitor_pass.vehicle_colour),
        ),
        plate_texts=(number_plate,),
    )


async def _access_event_candidates(session: AsyncSession) -> list[SearchCandidate]:
    rows = (
        await session.scalars(
            select(AccessEvent)
            .order_by(AccessEvent.occurred_at.desc())
            .limit(250)
        )
    ).all()
    return [_access_event_candidate(row) for row in rows]


def _access_event_candidate(event: AccessEvent) -> SearchCandidate:
    visitor_pass = (
        event.raw_payload.get("visitor_pass") if isinstance(event.raw_payload, dict) else {}
    )
    visitor_name = _text(
        visitor_pass.get("visitor_name") if isinstance(visitor_pass, dict) else None
    )
    decision = _enum_value(event.decision)
    direction = _enum_value(event.direction)
    label = event.registration_number
    facts = [
        *_fact("Decision", _title(decision)),
        *_fact("Direction", _title(direction)),
        *_fact("Source", event.source),
        *_fact("Occurred", _format_datetime(event.occurred_at)),
        *_fact("Visitor", visitor_name),
    ]
    return SearchCandidate(
        result=GlobalSearchResult(
            id=str(event.id),
            type="access_event",
            label=label,
            subtitle=f"{_title(direction)} {_title(decision)}",
            filter_value=label,
            target=SearchTarget(view="events"),
            preview=SearchPreview(
                title=label,
                body=None,
                badges=["Access Event", _title(decision)],
                facts=facts,
            ),
        ),
        search_texts=(
            label,
            event.source,
            visitor_name,
            decision,
            direction,
            _enum_value(event.timing_classification),
        ),
        plate_texts=(label,),
    )


async def _alert_candidates(session: AsyncSession) -> list[SearchCandidate]:
    rows = (
        await session.scalars(
            select(Anomaly)
            .options(selectinload(Anomaly.event), selectinload(Anomaly.resolved_by))
            .order_by(Anomaly.created_at.desc())
            .limit(250)
        )
    ).all()
    return [_alert_candidate(row) for row in rows]


def _alert_candidate(alert: Anomaly) -> SearchCandidate:
    registration = _alert_registration(alert)
    status = "resolved" if alert.resolved_at else "open"
    label = registration or _title(_enum_value(alert.anomaly_type))
    message = _alert_message(alert)
    facts = [
        *_fact("Message", message),
        *_fact("Plate", registration),
        *_fact("Severity", _title(_enum_value(alert.severity))),
        *_fact("Status", _title(status)),
        *_fact("Created", _format_datetime(alert.created_at)),
    ]
    return SearchCandidate(
        result=GlobalSearchResult(
            id=str(alert.id),
            type="alert",
            label=label,
            subtitle=message,
            filter_value=registration or message,
            target=SearchTarget(view="alerts", route_search=f"?alert={alert.id}"),
            preview=SearchPreview(
                title=label,
                body=message,
                badges=["Alert", _title(_enum_value(alert.severity)), _title(status)],
                facts=facts,
            ),
        ),
        search_texts=(
            label,
            registration,
            message,
            _enum_value(alert.anomaly_type),
            _enum_value(alert.severity),
            status,
        ),
        plate_texts=(registration,),
    )


async def _user_candidates(session: AsyncSession) -> list[SearchCandidate]:
    rows = (
        await session.scalars(select(User).order_by(User.first_name, User.last_name).limit(200))
    ).all()
    return [_user_candidate(row) for row in rows]


def _user_candidate(user: User) -> SearchCandidate:
    display_name = (
        _text(user.full_name) or f"{user.first_name} {user.last_name}".strip() or user.username
    )
    facts = [
        *_fact("Username", user.username),
        *_fact("Email", user.email),
        *_fact("Mobile", user.mobile_phone_number),
        *_fact("Last login", _format_datetime(user.last_login_at)),
    ]
    return SearchCandidate(
        result=GlobalSearchResult(
            id=str(user.id),
            type="user",
            label=display_name,
            subtitle=f"@{user.username}",
            filter_value=display_name,
            target=SearchTarget(view="users"),
            preview=SearchPreview(
                title=display_name,
                body=None,
                badges=[
                    "User",
                    _title(_enum_value(user.role)),
                    "Active" if user.is_active else "Inactive",
                ],
                facts=facts,
            ),
        ),
        search_texts=(
            display_name,
            user.username,
            _text(user.email),
            _text(user.mobile_phone_number),
            _enum_value(user.role),
        ),
    )


async def _automation_rule_candidates(session: AsyncSession) -> list[SearchCandidate]:
    rows = (
        await session.scalars(
            select(AutomationRule)
            .order_by(AutomationRule.created_at.desc(), AutomationRule.name)
            .limit(200)
        )
    ).all()
    return [_automation_rule_candidate(row) for row in rows]


def _automation_rule_candidate(rule: AutomationRule) -> SearchCandidate:
    facts = [
        *_fact("Status", "Active" if rule.is_active else "Inactive"),
        *_fact("Triggers", ", ".join(rule.trigger_keys or [])),
        *_fact("Last run", _text(rule.last_run_status)),
        *_fact("Next run", _format_datetime(rule.next_run_at)),
    ]
    return SearchCandidate(
        result=GlobalSearchResult(
            id=str(rule.id),
            type="automation_rule",
            label=rule.name,
            subtitle="Automation rule",
            filter_value=rule.name,
            target=SearchTarget(view="settings_automations"),
            preview=SearchPreview(
                title=rule.name,
                body=_text(rule.description),
                badges=["Automation", "Active" if rule.is_active else "Inactive"],
                facts=facts,
            ),
        ),
        search_texts=(
            rule.name,
            _text(rule.description),
            " ".join(rule.trigger_keys or []),
            _text(rule.last_run_status),
        ),
    )


async def _notification_rule_candidates(session: AsyncSession) -> list[SearchCandidate]:
    rows = (
        await session.scalars(
            select(NotificationRule)
            .order_by(NotificationRule.created_at.desc(), NotificationRule.name)
            .limit(200)
        )
    ).all()
    return [_notification_rule_candidate(row) for row in rows]


def _notification_rule_candidate(rule: NotificationRule) -> SearchCandidate:
    facts = [
        *_fact("Status", "Active" if rule.is_active else "Inactive"),
        *_fact("Trigger", rule.trigger_event),
        *_fact("Last fired", _format_datetime(rule.last_fired_at)),
    ]
    return SearchCandidate(
        result=GlobalSearchResult(
            id=str(rule.id),
            type="notification_rule",
            label=rule.name,
            subtitle="Notification rule",
            filter_value=rule.name,
            target=SearchTarget(view="settings_notifications"),
            preview=SearchPreview(
                title=rule.name,
                body=None,
                badges=["Notification", "Active" if rule.is_active else "Inactive"],
                facts=facts,
            ),
        ),
        search_texts=(rule.name, rule.trigger_event, "active" if rule.is_active else "inactive"),
    )


def _candidate_score(candidate: SearchCandidate, query: str) -> int | None:
    plate_scores: list[int] = []
    text_scores: list[int] = []
    plate_query = _normalize_plate(query)
    if plate_query:
        for text in candidate.plate_texts:
            score = _score_plate(text, plate_query)
            if score is not None:
                plate_scores.append(score)
    for text in candidate.search_texts:
        score = _score_text(text, query)
        if score is not None:
            text_scores.append(score)
    if plate_scores:
        return min(plate_scores) + _plate_type_bias(candidate.result.type)
    if text_scores:
        return min(text_scores) + _text_type_bias(candidate.result.type)
    return None


def _score_plate(value: str, normalized_query: str) -> int | None:
    normalized_value = _normalize_plate(value)
    if not normalized_value:
        return None
    if normalized_value == normalized_query:
        return 0
    if normalized_value.startswith(normalized_query):
        return 10
    if normalized_query in normalized_value:
        return 30
    return None


def _score_text(value: str, query: str) -> int | None:
    normalized_value = _normalize_text(value)
    normalized_query = _normalize_text(query)
    if not normalized_value or not normalized_query:
        return None
    if normalized_value == normalized_query:
        return 0
    if normalized_value.startswith(normalized_query):
        return 10
    words = normalized_value.split()
    if any(word.startswith(normalized_query) for word in words):
        return 20
    query_tokens = normalized_query.split()
    if query_tokens and all(
        any(word.startswith(token) for word in words) for token in query_tokens
    ):
        return 22
    if normalized_query in normalized_value:
        return 30
    return None


def _plate_type_bias(result_type: SearchResultType) -> int:
    order: dict[str, int] = {
        "vehicle": 0,
        "access_event": 1,
        "person": 2,
        "visitor_pass": 2,
        "alert": 4,
        "group": 5,
        "schedule": 6,
        "user": 7,
        "automation_rule": 8,
        "notification_rule": 9,
    }
    return order.get(result_type, 20)


def _text_type_bias(result_type: SearchResultType) -> int:
    order: dict[str, int] = {
        "person": 0,
        "visitor_pass": 1,
        "vehicle": 2,
        "group": 3,
        "schedule": 4,
        "alert": 5,
        "access_event": 6,
        "user": 7,
        "automation_rule": 8,
        "notification_rule": 9,
    }
    return order.get(result_type, 20)


def _assigned_vehicles_for_person(person: Person) -> list[Vehicle]:
    assigned = [
        assignment.vehicle
        for assignment in person.vehicle_assignments or []
        if assignment.vehicle is not None
    ]
    return assigned or list(person.vehicles or [])


def _owner_names_for_vehicle(vehicle: Vehicle) -> list[str]:
    assigned = [
        _text(assignment.person.display_name)
        for assignment in vehicle.person_assignments or []
        if assignment.person is not None
    ]
    if assigned:
        return [name for name in assigned if name]
    if vehicle.owner:
        return [_text(vehicle.owner.display_name)]
    return []


def _alert_registration(alert: Anomaly) -> str:
    context = alert.context if isinstance(alert.context, dict) else {}
    registration = context.get("registration_number")
    if isinstance(registration, str) and registration.strip():
        return registration.strip()
    if alert.event:
        return _text(alert.event.registration_number)
    return ""


def _alert_message(alert: Anomaly) -> str:
    if _enum_value(alert.anomaly_type) == "unauthorized_plate":
        return "Unauthorised Plate, Access Denied"
    return _text(alert.message)


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _normalize_plate(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return _text(raw)


def _title(value: str) -> str:
    return _text(value).replace("_", " ").replace("-", " ").title()


def _fact(label: str, value: Any) -> list[SearchPreviewFact]:
    text = _text(value)
    return [SearchPreviewFact(label=label, value=text)] if text else []


def _format_datetime(value: datetime | date | None) -> str:
    if not value:
        return ""
    return value.isoformat()


def _dated_status(status_value: str | None, date_value: date | None) -> str:
    status_text = _text(status_value)
    date_text = _format_datetime(date_value)
    if status_text and date_text:
        return f"{status_text} until {date_text}"
    return status_text or date_text
