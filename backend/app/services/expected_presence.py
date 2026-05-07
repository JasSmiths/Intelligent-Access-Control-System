from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from statistics import median
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import AccessEvent, Person, Vehicle, VehiclePersonAssignment
from app.models.enums import AccessDecision, AccessDirection


LOOKBACK_WEEKS = 12
MIN_WEEKDAY_VISITS = 3
MIN_WEEKDAY_ATTENDANCE = 0.45
MIN_CANDIDATE_ATTENDANCE = 0.35
RECENT_WEEKDAY_OCCURRENCES = 4
LEARNING_MIN_COVERAGE = 0.5
MIN_TOTAL_TRAINING_VISITS = 4
MIN_RECENT_TRAINING_VISITS = 2
RECENT_TRAINING_WEEKS = 4


@dataclass(frozen=True)
class HistoricalPresenceEvent:
    person_id: str
    display_name: str
    occurred_at: datetime
    direction: AccessDirection


@dataclass
class VisitDay:
    person_id: str
    display_name: str
    local_date: date
    entry_minutes: list[int]
    exit_minutes: list[int]


async def expected_presence_today(
    session: AsyncSession,
    *,
    timezone_name: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    timezone = _timezone(timezone_name)
    generated_at = _ensure_aware(now) if now else datetime.now(tz=UTC)
    target_date = generated_at.astimezone(timezone).date()
    window_start = datetime.combine(
        target_date - timedelta(weeks=LOOKBACK_WEEKS),
        time.min,
        tzinfo=timezone,
    ).astimezone(UTC)
    people = (
        await session.scalars(
            select(Person).where(Person.is_active.is_(True)).order_by(Person.display_name)
        )
    ).all()
    active_people_by_id = {str(person.id): person for person in people}
    events = (
        await session.scalars(
            select(AccessEvent)
            .options(
                selectinload(AccessEvent.vehicle).selectinload(Vehicle.owner),
                selectinload(AccessEvent.vehicle)
                .selectinload(Vehicle.person_assignments)
                .selectinload(VehiclePersonAssignment.person),
            )
            .where(
                AccessEvent.decision == AccessDecision.GRANTED,
                AccessEvent.occurred_at >= window_start,
                AccessEvent.occurred_at <= generated_at,
                or_(AccessEvent.person_id.is_not(None), AccessEvent.vehicle_id.is_not(None)),
            )
            .order_by(AccessEvent.occurred_at)
        )
    ).all()
    historical = [
        event
        for row in events
        if (event := historical_event_from_access_event(row, active_people_by_id)) is not None
    ]
    return build_expected_presence_payload(
        historical,
        target_date=target_date,
        timezone_name=timezone.key,
        generated_at=generated_at,
    )


def build_expected_presence_payload(
    events: Sequence[HistoricalPresenceEvent],
    *,
    target_date: date,
    timezone_name: str,
    generated_at: datetime,
) -> dict[str, Any]:
    timezone = _timezone(timezone_name)
    window_start_date = target_date - timedelta(weeks=LOOKBACK_WEEKS)
    historical_events = [
        event
        for event in events
        if window_start_date <= event.occurred_at.astimezone(timezone).date() < target_date
    ]
    today_events = [
        event
        for event in events
        if event.occurred_at.astimezone(timezone).date() == target_date
    ]
    visits_by_person = _visit_days_by_person(
        historical_events,
        timezone=timezone,
        window_start_date=window_start_date,
        target_date=target_date,
    )

    expected_by_person: dict[str, dict[str, Any]] = {}
    regular_candidates = 0
    learned_candidates = 0
    training_candidate_people: set[str] = set()
    target_weekday = target_date.weekday()
    for person_id, visits_by_date in visits_by_person.items():
        entry_dates = sorted(
            local_date for local_date, visit in visits_by_date.items() if visit.entry_minutes
        )
        if not entry_dates:
            continue
        if _has_regular_training_evidence(entry_dates, target_date=target_date):
            training_candidate_people.add(person_id)

        weekday_evaluations = [
            _evaluate_person_weekday(
                visits_by_date,
                weekday=weekday,
                target_date=target_date,
                window_start_date=window_start_date,
            )
            for weekday in range(7)
        ]
        candidate = any(item["candidate"] for item in weekday_evaluations)
        learned = any(item["learned"] for item in weekday_evaluations)
        if candidate:
            regular_candidates += 1
            if learned:
                learned_candidates += 1

        today = weekday_evaluations[target_weekday]
        if not today["learned"]:
            continue
        weekday_visits = [
            visits_by_date[local_date]
            for local_date in sorted(today["visit_dates"])
            if local_date in visits_by_date
        ]
        display_name = weekday_visits[-1].display_name if weekday_visits else person_id
        expected_by_person[person_id] = _expected_person_payload(
            person_id=person_id,
            display_name=display_name,
            confidence=float(today["attendance_ratio"]),
            evidence_days=len(today["visit_dates"]),
            observed_weekdays=today["observed_weekdays"],
            typical_arrival=_typical_time(
                minute
                for visit in weekday_visits
                for minute in visit.entry_minutes[:1]
            ),
            typical_departure=_typical_time(
                minute
                for visit in weekday_visits
                for minute in visit.exit_minutes[-1:]
            ),
        )

    observed_today = _observed_people_today(today_events, timezone=timezone)
    learning_population = max(
        regular_candidates,
        len(training_candidate_people),
        len(observed_today),
    )
    coverage_ratio = (
        learned_candidates / learning_population
        if learning_population
        else 1.0
    )
    learning = bool(learning_population and coverage_ratio < LEARNING_MIN_COVERAGE)
    if learning:
        for observed in observed_today:
            expected_by_person.setdefault(observed["person_id"], observed)

    expected_people = sorted(
        expected_by_person.values(),
        key=lambda item: (-item["confidence"], item["display_name"]),
    )
    return {
        "date": target_date.isoformat(),
        "timezone": timezone.key,
        "generated_at": generated_at.astimezone(UTC).isoformat(),
        "count": len(expected_people),
        "learning": learning,
        "coverage": {
            "regular_candidates": regular_candidates,
            "learned_candidates": learned_candidates,
            "learning_population": learning_population,
            "ratio": round(coverage_ratio, 2),
        },
        "people": expected_people,
    }


def _expected_person_payload(
    *,
    person_id: str,
    display_name: str,
    confidence: float,
    evidence_days: int,
    observed_weekdays: int,
    typical_arrival: str | None,
    typical_departure: str | None,
) -> dict[str, Any]:
    return {
        "person_id": person_id,
        "display_name": display_name,
        "confidence": round(confidence, 2),
        "evidence_days": evidence_days,
        "observed_weekdays": observed_weekdays,
        "typical_arrival": typical_arrival,
        "typical_departure": typical_departure,
    }


def _observed_people_today(
    events: Sequence[HistoricalPresenceEvent],
    *,
    timezone: ZoneInfo,
) -> list[dict[str, Any]]:
    visits_by_person: dict[str, dict[str, Any]] = {}
    for event in events:
        item = visits_by_person.setdefault(
            event.person_id,
            {
                "person_id": event.person_id,
                "display_name": event.display_name,
                "entry_minutes": [],
                "exit_minutes": [],
            },
        )
        local_at = event.occurred_at.astimezone(timezone)
        minutes = local_at.hour * 60 + local_at.minute
        if event.direction == AccessDirection.ENTRY:
            item["entry_minutes"].append(minutes)
        elif event.direction == AccessDirection.EXIT:
            item["exit_minutes"].append(minutes)

    return [
        _expected_person_payload(
            person_id=str(item["person_id"]),
            display_name=str(item["display_name"]),
            confidence=0.0,
            evidence_days=0,
            observed_weekdays=0,
            typical_arrival=_typical_time(item["entry_minutes"]),
            typical_departure=_typical_time(item["exit_minutes"]),
        )
        for item in visits_by_person.values()
    ]


def _has_regular_training_evidence(
    entry_dates: Sequence[date],
    *,
    target_date: date,
) -> bool:
    if len(entry_dates) >= MIN_TOTAL_TRAINING_VISITS:
        return True
    recent_cutoff = target_date - timedelta(weeks=RECENT_TRAINING_WEEKS)
    recent_visits = sum(1 for local_date in entry_dates if local_date >= recent_cutoff)
    return recent_visits >= MIN_RECENT_TRAINING_VISITS


def historical_event_from_access_event(
    event: AccessEvent,
    active_people_by_id: Mapping[str, Person],
) -> HistoricalPresenceEvent | None:
    direction = _coerce_direction(getattr(event, "direction", None))
    if direction not in {AccessDirection.ENTRY, AccessDirection.EXIT}:
        return None

    direct_person_id = getattr(event, "person_id", None)
    if direct_person_id:
        person = active_people_by_id.get(str(direct_person_id))
        if not person:
            return None
        return HistoricalPresenceEvent(
            person_id=str(person.id),
            display_name=person.display_name,
            occurred_at=_ensure_aware(event.occurred_at),
            direction=direction,
        )

    person = _single_active_vehicle_person(getattr(event, "vehicle", None))
    if not person:
        return None
    return HistoricalPresenceEvent(
        person_id=str(person.id),
        display_name=person.display_name,
        occurred_at=_ensure_aware(event.occurred_at),
        direction=direction,
    )


def _visit_days_by_person(
    events: Sequence[HistoricalPresenceEvent],
    *,
    timezone: ZoneInfo,
    window_start_date: date,
    target_date: date,
) -> dict[str, dict[date, VisitDay]]:
    visits_by_person: dict[str, dict[date, VisitDay]] = {}
    for event in events:
        local_at = event.occurred_at.astimezone(timezone)
        local_date = local_at.date()
        if local_date < window_start_date or local_date >= target_date:
            continue
        visits_by_date = visits_by_person.setdefault(event.person_id, {})
        visit = visits_by_date.setdefault(
            local_date,
            VisitDay(
                person_id=event.person_id,
                display_name=event.display_name,
                local_date=local_date,
                entry_minutes=[],
                exit_minutes=[],
            ),
        )
        minutes = local_at.hour * 60 + local_at.minute
        if event.direction == AccessDirection.ENTRY:
            visit.entry_minutes.append(minutes)
            visit.entry_minutes.sort()
        elif event.direction == AccessDirection.EXIT:
            visit.exit_minutes.append(minutes)
            visit.exit_minutes.sort()
    return visits_by_person


def _evaluate_person_weekday(
    visits_by_date: Mapping[date, VisitDay],
    *,
    weekday: int,
    target_date: date,
    window_start_date: date,
) -> dict[str, Any]:
    visit_dates = sorted(
        local_date
        for local_date, visit in visits_by_date.items()
        if local_date.weekday() == weekday and visit.entry_minutes
    )
    if not visit_dates:
        return _weekday_result([], 0, 0.0, False, False)

    observed_start = max(window_start_date, visit_dates[0])
    observed_weekdays = _weekday_dates_between(observed_start, target_date, weekday)
    observed_count = max(len(observed_weekdays), 1)
    attendance_ratio = len(visit_dates) / observed_count
    recent_weekdays = observed_weekdays[-RECENT_WEEKDAY_OCCURRENCES:]
    has_recent_evidence = any(local_date in visit_dates for local_date in recent_weekdays)
    candidate = (
        len(visit_dates) >= MIN_WEEKDAY_VISITS
        and attendance_ratio >= MIN_CANDIDATE_ATTENDANCE
    )
    learned = (
        len(visit_dates) >= MIN_WEEKDAY_VISITS
        and attendance_ratio >= MIN_WEEKDAY_ATTENDANCE
        and has_recent_evidence
    )
    return _weekday_result(
        visit_dates,
        observed_count,
        attendance_ratio,
        candidate,
        learned,
    )


def _weekday_result(
    visit_dates: list[date],
    observed_weekdays: int,
    attendance_ratio: float,
    candidate: bool,
    learned: bool,
) -> dict[str, Any]:
    return {
        "visit_dates": visit_dates,
        "observed_weekdays": observed_weekdays,
        "attendance_ratio": attendance_ratio,
        "candidate": candidate,
        "learned": learned,
    }


def _weekday_dates_between(start_date: date, end_date: date, weekday: int) -> list[date]:
    offset = (weekday - start_date.weekday()) % 7
    current = start_date + timedelta(days=offset)
    dates: list[date] = []
    while current < end_date:
        dates.append(current)
        current += timedelta(days=7)
    return dates


def _single_active_vehicle_person(vehicle: Any) -> Any | None:
    if not vehicle:
        return None
    candidates: list[Any] = []
    for assignment in getattr(vehicle, "person_assignments", []) or []:
        person = getattr(assignment, "person", None)
        if person and getattr(person, "is_active", False):
            candidates.append(person)
    owner = getattr(vehicle, "owner", None)
    if owner and getattr(owner, "is_active", False):
        candidates.append(owner)

    unique: dict[str, Any] = {}
    for person in candidates:
        person_id = str(getattr(person, "id", ""))
        if person_id:
            unique[person_id] = person
    return next(iter(unique.values())) if len(unique) == 1 else None


def _typical_time(minutes: Any) -> str | None:
    values = [int(value) for value in minutes]
    if not values:
        return None
    typical = int(round(median(values)))
    return f"{typical // 60:02d}:{typical % 60:02d}"


def _coerce_direction(value: Any) -> AccessDirection | None:
    if isinstance(value, AccessDirection):
        return value
    try:
        return AccessDirection(str(value))
    except ValueError:
        return None


def _ensure_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value or "Europe/London")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")
