"""Origin expiry remains independent from queue/retry clocks and manual tests."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.services.leaderboard_notifications import (
    chart_notification_denial,
    chart_notification_id,
    chart_notification_origin,
    chart_notification_requires_check,
)

NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)
EVENT = uuid.UUID(int=1)
RUN = chart_notification_id(7)


def notice():
    return {"event_type": "leaderboard_overtake", "leaderboard_origin": chart_notification_origin(7, EVENT, NOW)}


def test_live_chart_deadline_is_original_clock_including_exact_boundary():
    payload = notice()
    assert chart_notification_denial(payload, RUN, NOW + timedelta(seconds=299)) is None
    assert chart_notification_denial(payload, RUN, NOW + timedelta(seconds=300)) == "leaderboard_origin_expired"
    assert chart_notification_denial(payload, RUN, NOW + timedelta(seconds=900)) == "leaderboard_origin_expired"


def test_queue_or_retry_timestamp_does_not_refresh_chart_origin():
    payload = notice() | {"queued_at": (NOW + timedelta(seconds=299)).isoformat()}
    assert chart_notification_denial(payload, RUN, NOW + timedelta(seconds=301)) == "leaderboard_origin_expired"
    assert payload["leaderboard_origin"] == chart_notification_origin(7, EVENT, NOW)


@pytest.mark.parametrize("field,value", [
    ("version", True), ("generation", True), ("generation", 0),
    ("event_id", "bad"), ("origin_at", "2026-10-11T12:00:00"),
    ("origin_at", "9999-12-31T23:59:59-23:59"),
    ("deadline", (NOW + timedelta(seconds=301)).isoformat()),
])
def test_malformed_retained_chart_origin_is_denied(field, value):
    payload = notice()
    payload["leaderboard_origin"][field] = value
    assert chart_notification_denial(payload, RUN, NOW) == "leaderboard_origin_invalid"


def test_future_origin_and_wrong_dispatch_identity_are_denied():
    payload = notice()
    assert chart_notification_denial(payload, RUN, NOW - timedelta(seconds=1)) == "leaderboard_origin_invalid"
    assert chart_notification_denial(payload, uuid.uuid4(), NOW) == "leaderboard_origin_invalid"


def test_legacy_unattempted_chart_origin_is_explicitly_unavailable():
    assert chart_notification_denial({"event_type": "leaderboard_overtake"}, RUN, NOW) == "leaderboard_origin_unavailable"
    assert chart_notification_denial({"event_type": "other"}, RUN, NOW) is None


def test_confirmed_manual_chart_test_uses_its_separate_authority():
    payload = {"event_type": "leaderboard_overtake", "confirmed_delivery": {"user_id": "admin"}}
    assert not chart_notification_requires_check(payload)
    assert chart_notification_denial(payload, uuid.uuid4(), NOW) is None
    payload["leaderboard_origin"] = notice()["leaderboard_origin"]
    assert chart_notification_denial(payload, uuid.uuid4(), NOW + timedelta(seconds=900)) is None
