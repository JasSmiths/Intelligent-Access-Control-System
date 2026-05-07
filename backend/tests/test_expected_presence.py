from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.models.enums import AccessDirection
from app.services.expected_presence import (
    HistoricalPresenceEvent,
    build_expected_presence_payload,
    historical_event_from_access_event,
)


TIMEZONE_NAME = "Europe/London"
TIMEZONE = ZoneInfo(TIMEZONE_NAME)
TARGET_MONDAY = date(2026, 5, 4)


def test_weekly_regular_is_expected_on_learned_weekday() -> None:
    person_id = str(uuid4())
    events = routine_events(
        person_id,
        "Ada Lovelace",
        weeks_before=[6, 5, 4, 3, 2, 1],
    )

    payload = build_payload(events)

    assert payload["count"] == 1
    assert payload["learning"] is False
    assert payload["people"][0]["person_id"] == person_id
    assert payload["people"][0]["confidence"] == 1.0
    assert payload["people"][0]["typical_arrival"] == "08:30"
    assert payload["people"][0]["typical_departure"] == "17:10"


def test_occasional_same_weekday_arrivals_are_not_expected() -> None:
    events = routine_events(
        str(uuid4()),
        "Occasional Visitor",
        weeks_before=[11, 7, 3],
    )

    payload = build_payload(events)

    assert payload["count"] == 0
    assert payload["coverage"]["regular_candidates"] == 0
    assert payload["learning"] is False


def test_stale_routine_drops_after_recent_misses() -> None:
    events = routine_events(
        str(uuid4()),
        "Stale Regular",
        weeks_before=[12, 11, 10, 9, 8, 7],
    )

    payload = build_payload(events)

    assert payload["count"] == 0
    assert payload["coverage"] == {
        "regular_candidates": 1,
        "learned_candidates": 0,
        "learning_population": 1,
        "ratio": 0.0,
    }
    assert payload["learning"] is True


def test_newer_routine_qualifies_after_repeated_recent_visits() -> None:
    person_id = str(uuid4())
    events = routine_events(
        person_id,
        "New Regular",
        weeks_before=[3, 2, 1],
    )

    payload = build_payload(events)

    assert payload["count"] == 1
    assert payload["coverage"] == {
        "regular_candidates": 1,
        "learned_candidates": 1,
        "learning_population": 1,
        "ratio": 1.0,
    }
    assert payload["people"][0]["person_id"] == person_id


def test_learning_is_true_until_at_least_half_of_regular_candidates_are_learned() -> None:
    stable_id = str(uuid4())
    stale_one_id = str(uuid4())
    stale_two_id = str(uuid4())
    events = [
        *routine_events(stable_id, "Stable Regular", weeks_before=[6, 5, 4, 3, 2, 1]),
        *routine_events(stale_one_id, "Stale One", weeks_before=[12, 11, 10, 9, 8, 7]),
        *routine_events(stale_two_id, "Stale Two", weeks_before=[12, 11, 10, 9, 8, 7]),
    ]

    payload = build_payload(events)

    assert payload["coverage"] == {
        "regular_candidates": 3,
        "learned_candidates": 1,
        "learning_population": 3,
        "ratio": 0.33,
    }
    assert payload["learning"] is True
    assert payload["count"] == 1


def test_learning_folds_in_people_observed_today() -> None:
    observed_id = str(uuid4())
    events = [
        historical_event(
            observed_id,
            "Observed Today",
            local_date=TARGET_MONDAY,
            local_time=time(8, 45),
            direction=AccessDirection.ENTRY,
        )
    ]

    payload = build_payload(events)

    assert payload["coverage"] == {
        "regular_candidates": 0,
        "learned_candidates": 0,
        "learning_population": 1,
        "ratio": 0.0,
    }
    assert payload["learning"] is True
    assert payload["count"] == 1
    assert payload["people"][0]["person_id"] == observed_id
    assert payload["people"][0]["typical_arrival"] == "08:45"
    assert payload["people"][0]["evidence_days"] == 0


def test_vehicle_fallback_skips_ambiguous_people() -> None:
    first = person("First Person")
    second = person("Second Person")
    event = access_event(
        vehicle=SimpleNamespace(
            owner=None,
            person_assignments=[
                SimpleNamespace(person=first),
                SimpleNamespace(person=second),
            ],
        )
    )

    assert historical_event_from_access_event(event, {}) is None


def test_vehicle_fallback_uses_single_active_person() -> None:
    assigned = person("Assigned Person")
    event = access_event(
        vehicle=SimpleNamespace(
            owner=None,
            person_assignments=[SimpleNamespace(person=assigned)],
        )
    )

    historical = historical_event_from_access_event(event, {})

    assert historical is not None
    assert historical.person_id == str(assigned.id)
    assert historical.display_name == "Assigned Person"


def routine_events(
    person_id: str,
    display_name: str,
    *,
    weeks_before: list[int],
) -> list[HistoricalPresenceEvent]:
    events: list[HistoricalPresenceEvent] = []
    for weeks in weeks_before:
        local_date = TARGET_MONDAY - timedelta(weeks=weeks)
        events.append(
            historical_event(
                person_id,
                display_name,
                local_date=local_date,
                local_time=time(8, 30),
                direction=AccessDirection.ENTRY,
            )
        )
        events.append(
            historical_event(
                person_id,
                display_name,
                local_date=local_date,
                local_time=time(17, 10),
                direction=AccessDirection.EXIT,
            )
        )
    return events


def historical_event(
    person_id: str,
    display_name: str,
    *,
    local_date: date,
    local_time: time,
    direction: AccessDirection,
) -> HistoricalPresenceEvent:
    local_at = datetime.combine(local_date, local_time, tzinfo=TIMEZONE)
    return HistoricalPresenceEvent(
        person_id=person_id,
        display_name=display_name,
        occurred_at=local_at.astimezone(UTC),
        direction=direction,
    )


def build_payload(events: list[HistoricalPresenceEvent]) -> dict:
    return build_expected_presence_payload(
        events,
        target_date=TARGET_MONDAY,
        timezone_name=TIMEZONE_NAME,
        generated_at=datetime(2026, 5, 4, 8, 0, tzinfo=UTC),
    )


def person(display_name: str, *, is_active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), display_name=display_name, is_active=is_active)


def access_event(*, vehicle: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        person_id=None,
        vehicle=vehicle,
        direction=AccessDirection.ENTRY,
        occurred_at=datetime(2026, 5, 1, 8, 30, tzinfo=UTC),
    )
