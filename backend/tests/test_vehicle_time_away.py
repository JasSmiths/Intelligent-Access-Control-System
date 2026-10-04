from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from app.models.enums import AccessDecision, AccessDirection
from app.modules.notifications.base import NotificationContext
from app.services.notifications import VARIABLE_GROUPS, context_variables, render_template, sample_notification_context
from app.services.workflows.vehicle_away import vehicle_time_away_label, vehicle_time_away_seconds


@pytest.mark.parametrize("seconds, expected", [
    (None, ""), ("", ""), ("invalid", ""), ("nan", ""), ("inf", ""), (-1, ""), (0, ""),
    (30, "after less than a minute"), (60, "after 1m"), (59 * 60, "after 59m"),
    (3600, "after 1hr"), (8400, "after 2hrs 20m"), (11280, "after 3hrs 8m"),
    (86399, "after 23hrs 59m"), (86400, "after 1 day"),
    (3 * 86400 + 7 * 3600 + 14 * 60, "after 3 days"),
    (7 * 86400, "after 1 week"), (15 * 86400, "after 2 weeks"),
    (30 * 86400, "after 1 month"), (95 * 86400, "after 3 months"),
])
def test_absence_wording_and_precision(seconds, expected):
    assert vehicle_time_away_label(seconds) == expected


def test_catalog_preview_and_template_include_optional_phrase():
    assert any(variable["token"] == "@VehicleTimeAway"
               for group in VARIABLE_GROUPS for variable in group["items"])
    assert context_variables(sample_notification_context())["VehicleTimeAway"] == "after 2hrs 20m"
    context = NotificationContext("authorized_entry", "Arrival", "info", {
        "first_name": "Sam", "vehicle_time_away_seconds": "8400",
    })
    assert render_template("@FirstName arrived @VehicleTimeAway", context_variables(context)) == "Sam arrived after 2hrs 20m"
    assert render_template("Sam arrived @VehicleTimeAway", context_variables(
        NotificationContext("authorized_entry", "Arrival", "info", {}))) == "Sam arrived"


def test_arrival_copy_has_no_editor_separator_before_the_comma():
    context = NotificationContext("authorized_entry", "Arrival", "info", {
        "vehicle_time_away_seconds": "1560",
    })
    template = "Sylvia's Mercedes-Benz Slk has been detected at the gate @VehicleTimeAway , I've let her in."
    assert render_template(template, context_variables(context)) == (
        "Sylvia's Mercedes-Benz Slk has been detected at the gate after 26m, I've let her in.")


@pytest.mark.parametrize("punctuation", list(",.;:!?"))
def test_token_separator_cleanup_preserves_literal_and_value_whitespace(punctuation):
    template = f"Keep  literal , spacing.\n@VehicleTimeAway \t{punctuation} Next  line."
    assert render_template(template, {"VehicleTimeAway": "after 26m "}) == (
        f"Keep  literal , spacing.\nafter 26m {punctuation} Next  line.")


@pytest.mark.asyncio
async def test_absence_uses_arrival_time_and_timezone_offsets():
    arrival = SimpleNamespace(direction=AccessDirection.ENTRY, decision=AccessDecision.GRANTED,
        vehicle_id=uuid.uuid4(), registration_number="AB12CDE",
        occurred_at=datetime(2026, 10, 25, 2, 20, tzinfo=UTC))
    # 01:00 BST is 00:00 UTC on the clock-change day.
    departure = SimpleNamespace(direction=AccessDirection.EXIT,
        occurred_at=datetime.fromisoformat("2026-10-25T01:00:00+01:00"))
    session = SimpleNamespace(scalar=AsyncMock(return_value=departure))
    assert await vehicle_time_away_seconds(session, arrival) == 8400


@pytest.mark.asyncio
@pytest.mark.parametrize("previous", [None, SimpleNamespace(direction=AccessDirection.ENTRY)])
async def test_no_phrase_without_an_immediately_preceding_exit(previous):
    arrival = SimpleNamespace(direction=AccessDirection.ENTRY, decision=AccessDecision.GRANTED,
        vehicle_id=None, registration_number="AB12CDE", occurred_at=datetime.now(UTC))
    assert await vehicle_time_away_seconds(SimpleNamespace(scalar=AsyncMock(return_value=previous)), arrival) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("direction, decision", [
    (AccessDirection.EXIT, AccessDecision.GRANTED), (AccessDirection.ENTRY, AccessDecision.DENIED),
])
async def test_non_arrivals_do_not_query_history(direction, decision):
    session = SimpleNamespace(scalar=AsyncMock())
    assert await vehicle_time_away_seconds(session, SimpleNamespace(direction=direction, decision=decision)) is None
    session.scalar.assert_not_awaited()
