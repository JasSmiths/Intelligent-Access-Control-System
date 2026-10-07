"""Datetime and timezone boundary behavior shared by workflow rendering."""

from datetime import UTC, datetime

import pytest

from app.services.workflows.automation_definition import parse_datetime
from app.services.workflows.visitor_notifications import (
    parse_datetime_value,
    safe_zoneinfo,
)


@pytest.mark.parametrize(
    "value",
    ["2026-10-06T12:00:00Z", "2026-10-06T12:00:00+00:00", "2026-10-06T12:00:00"],
)
def test_workflow_dates_accept_utc_suffix_offset_and_naive_inputs(value: str) -> None:
    expected = datetime(2026, 10, 6, 12, tzinfo=UTC)
    assert parse_datetime(value) == expected
    assert parse_datetime_value(value) == expected


@pytest.mark.parametrize("value", [None, "", "invalid date"])
def test_invalid_workflow_dates_remain_absent(value: str | None) -> None:
    assert parse_datetime(value) is None
    assert parse_datetime_value(value) is None


@pytest.mark.parametrize(
    "zone", [None, "", "Synthetic/Unknown", "/invalid/absolute/path", "../invalid/path"]
)
def test_invalid_or_absent_visitor_timezones_use_london(zone: str | None) -> None:
    assert safe_zoneinfo(zone).key == "Europe/London"


def test_valid_visitor_timezone_is_preserved() -> None:
    assert safe_zoneinfo("UTC").key == "UTC"
