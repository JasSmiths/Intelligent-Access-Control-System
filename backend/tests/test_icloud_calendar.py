from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest

from app.models import VisitorPass
from app.models.enums import VisitorPassStatus
from app.modules.icloud_calendar.contracts import ICloudCalendarEvent
from app.services.calendar_accounts import serialize_icloud_account
from app.services.calendar_reconciliation import (
    ICLOUD_CALENDAR_SOURCE, calendar_visitor_name_for_event, calendar_pass_can_be_reconciled,
    event_contains_open_gate, fallback_visitor_name_from_calendar_title, source_metadata_for_event,
    source_reference_for_event, visitor_window_for_event,
)

def calendar_event(*, notes: str = "", description: str = "", title: str = "Chris Starkey") -> ICloudCalendarEvent:
    starts_at = datetime(2026, 4, 30, 11, 0, tzinfo=UTC)
    return ICloudCalendarEvent(
        calendar_id="home",
        event_id="evt-1",
        title=title,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1, minutes=15),
        description=description,
        notes=notes,
    )


def test_open_gate_marker_is_only_read_from_notes_or_description() -> None:
    assert event_contains_open_gate(calendar_event(notes="Please Open Gate for this visitor"))
    assert event_contains_open_gate(calendar_event(description="open gate"))
    assert not event_contains_open_gate(calendar_event(title="Open Gate"))
    assert not event_contains_open_gate(calendar_event(notes="Open the gate"))


def test_calendar_event_maps_to_asymmetric_visitor_pass_window() -> None:
    event = calendar_event()
    valid_from, valid_until = visitor_window_for_event(event)

    assert valid_from == datetime(2026, 4, 30, 10, 30, tzinfo=UTC)
    assert valid_until == datetime(2026, 4, 30, 12, 15, tzinfo=UTC)


def test_calendar_visitor_name_fallback_extracts_person_from_prefixed_title() -> None:
    assert fallback_visitor_name_from_calendar_title("Memory Clinic: Vicky Thompson") == "Vicky Thompson"


def test_calendar_source_metadata_keeps_original_title_and_extracted_name() -> None:
    account = SimpleNamespace(id=uuid.uuid4(), apple_id="jas@example.com")
    event = calendar_event(title="Memory Clinic: Vicky Thompson")

    metadata = source_metadata_for_event(account, event, visitor_name="Vicky Thompson", visitor_name_source="fallback")

    assert metadata["event_title"] == "Memory Clinic: Vicky Thompson"
    assert metadata["visitor_name"] == "Vicky Thompson"
    assert metadata["visitor_name_source"] == "fallback"


def test_icloud_account_serialization_maps_missing_password_to_reauth() -> None:
    account = SimpleNamespace(
        id=uuid.uuid4(),
        apple_id="user@example.com",
        display_name="user@example.com",
        status="error",
        is_active=True,
        last_auth_at=None,
        last_sync_at=None,
        last_sync_status="error",
        last_sync_summary=None,
        last_error="No password set",
        created_by_user_id=None,
        created_at=None,
        updated_at=None,
    )

    payload = serialize_icloud_account(account)

    assert payload["status"] == "requires_reauth"
    assert payload["last_error"] == "Stored iCloud session is no longer accepted by Apple. Reconnect this account."


def test_calendar_source_reference_is_stable_for_account_and_event() -> None:
    account_id = uuid.uuid4()
    event = calendar_event()

    assert source_reference_for_event(account_id, event) == f"icloud:{account_id}:home:evt-1"


def test_used_and_cancelled_calendar_passes_are_not_reconciled() -> None:
    row = VisitorPass(
        visitor_name="Chris Starkey",
        expected_time=datetime(2026, 4, 30, 11, 0, tzinfo=UTC),
        window_minutes=30,
        status=VisitorPassStatus.SCHEDULED,
        creation_source=ICLOUD_CALENDAR_SOURCE,
    )
    assert calendar_pass_can_be_reconciled(row)

    row.status = VisitorPassStatus.USED
    assert not calendar_pass_can_be_reconciled(row)

    row.status = VisitorPassStatus.CANCELLED
    assert not calendar_pass_can_be_reconciled(row)

    row.status = VisitorPassStatus.SCHEDULED
    row.creation_source = "ui"
    assert not calendar_pass_can_be_reconciled(row)


@pytest.mark.asyncio
async def test_calendar_visitor_name_uses_title_fallback() -> None:
    result = await calendar_visitor_name_for_event(calendar_event(title="Memory Clinic: Vicky Thompson"))
    assert result.visitor_name == "Vicky Thompson"
    assert result.source == "fallback"
