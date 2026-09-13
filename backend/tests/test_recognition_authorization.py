"""Recognition freshness is bounded by both observation and durable receipt."""
from datetime import UTC, datetime, timedelta

import pytest

from app.services.access.authorization import RecognitionAuthorizationDenied, recognition_deadline

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


@pytest.mark.parametrize("captured,received,expected", [
    (NOW, NOW, NOW + timedelta(seconds=60)),
    (NOW - timedelta(seconds=5), NOW, NOW + timedelta(seconds=55)),
    (NOW + timedelta(days=1), NOW, NOW + timedelta(seconds=60)),
    (NOW, NOW - timedelta(seconds=10), NOW + timedelta(seconds=50)),
])
def test_deadline_uses_earliest_authoritative_time(captured, received, expected):
    assert recognition_deadline(captured, received) == expected


def test_naive_observation_does_not_acquire_assumed_timezone():
    with pytest.raises(RecognitionAuthorizationDenied, match="timezone"):
        recognition_deadline(NOW.replace(tzinfo=None), NOW)
