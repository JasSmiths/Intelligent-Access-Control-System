from datetime import UTC, datetime, timedelta
from itertools import permutations
from zoneinfo import ZoneInfo

import pytest

from app.models.enums import TimingClassification as Timing
from app.services.access.timing import classify_timing


def at(hour, minute=0, *, day=1):
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize("observed,expected", [(at(23, 14, day=10), Timing.EARLIER_THAN_USUAL),
    (at(23, 15, day=10), Timing.NORMAL), (at(0, 45, day=10), Timing.NORMAL),
    (at(0, 46, day=10), Timing.LATER_THAN_USUAL), (at(12, day=10), Timing.UNKNOWN)])
def test_midnight_thresholds_and_half_day_ambiguity(observed, expected):
    samples = [at(23, 55), at(0, day=2), at(0, 5, day=3)]
    for order in permutations(samples):
        assert classify_timing(order, observed, ZoneInfo("UTC")) == expected


@pytest.mark.parametrize("samples", [[], [at(8)], [at(8), at(9)], [at(0), at(0), at(12)], [at(0), at(6), at(12)]])
def test_sparse_or_widely_separated_routines_have_no_baseline(samples):
    assert classify_timing(samples, at(8), ZoneInfo("UTC")) == Timing.UNKNOWN


def test_explicit_site_timezone_and_dst_repeated_hour_use_wall_clock():
    london = ZoneInfo("Europe/London")
    samples = [datetime(2026, 10, 23, 0, 30, tzinfo=UTC), datetime(2026, 10, 24, 0, 30, tzinfo=UTC),
        datetime(2026, 10, 25, 0, 30, tzinfo=UTC)]
    repeated = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    assert classify_timing(samples, repeated, london) == Timing.NORMAL
    assert classify_timing(samples, repeated, ZoneInfo("UTC")) == Timing.LATER_THAN_USUAL
    # The spring transition similarly compares explicit local wall-clock time.
    spring = [datetime(2026, 3, 27+i, 2, 30, tzinfo=UTC) for i in range(3)]
    assert classify_timing(spring, datetime(2026, 3, 30, 1, 30, tzinfo=UTC), london) == Timing.NORMAL
