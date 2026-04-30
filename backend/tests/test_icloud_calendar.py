from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

import app.services.icloud_calendar as icloud_calendar_module
from app.ai import tools as ai_tools
from app.models import VisitorPass
from app.models.enums import VisitorPassStatus
from app.modules.icloud_calendar.client import (
    ICloudAuthSession,
    ICloudCalendarClient,
    ICloudCalendarEvent,
    _normalize_event,
)
from app.services.icloud_calendar import (
    ICLOUD_CALENDAR_SOURCE,
    ICloudCalendarError,
    ICloudCalendarService,
    PendingICloudAuth,
    calendar_visitor_name_for_event,
    calendar_pass_can_be_reconciled,
    event_contains_open_gate,
    fallback_visitor_name_from_calendar_title,
    source_metadata_for_event,
    source_reference_for_event,
    visitor_window_for_event,
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
        raw={},
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


@pytest.mark.asyncio
async def test_calendar_visitor_name_uses_llm_json_response(monkeypatch) -> None:
    class FakeProvider:
        name = "fake"

        async def complete(self, messages, tools=None, tool_results=None):
            assert "Memory Clinic: Vicky Thompson" in messages[-1].content
            return SimpleNamespace(text='{"visitor_name":"Vicky Thompson"}')

    async def fake_runtime_config():
        return SimpleNamespace(
            llm_provider="openai",
            openai_api_key="test-key",
            gemini_api_key="",
            anthropic_api_key="",
        )

    monkeypatch.setattr(icloud_calendar_module, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(icloud_calendar_module, "get_llm_provider", lambda provider_name: FakeProvider())

    result = await calendar_visitor_name_for_event(calendar_event(title="Memory Clinic: Vicky Thompson"))

    assert result.visitor_name == "Vicky Thompson"
    assert result.source == "llm"


def test_calendar_source_metadata_keeps_original_title_and_extracted_name() -> None:
    account = SimpleNamespace(id=uuid.uuid4(), apple_id="jas@example.com")
    event = calendar_event(title="Memory Clinic: Vicky Thompson")

    metadata = source_metadata_for_event(account, event, visitor_name="Vicky Thompson", visitor_name_source="llm")

    assert metadata["event_title"] == "Memory Clinic: Vicky Thompson"
    assert metadata["visitor_name"] == "Vicky Thompson"
    assert metadata["visitor_name_source"] == "llm"


def test_icloud_event_normalizer_accepts_apple_date_arrays_and_private_comments() -> None:
    event = _normalize_event(
        {
            "pGuid": "home",
            "guid": "evt-array",
            "title": "Chris Starkey",
            "startDate": [20260430, 2026, 4, 30, 11, 0, 660],
            "endDate": [20260430, 2026, 4, 30, 12, 0, 720],
            "localStartDate": [20260430, 2026, 4, 30, 10, 0, 600],
            "localEndDate": [20260430, 2026, 4, 30, 11, 0, 660],
            "tz": "Europe/London",
            "privateComments": "Please Open Gate for this visitor",
            "allDay": False,
        }
    )

    assert event is not None
    assert event.calendar_id == "home"
    assert event.event_id == "evt-array"
    assert event.starts_at.isoformat() == "2026-04-30T11:00:00+01:00"
    assert event.ends_at.isoformat() == "2026-04-30T12:00:00+01:00"
    assert event_contains_open_gate(event)


def test_icloud_event_normalizer_falls_back_to_local_date_arrays() -> None:
    event = _normalize_event(
        {
            "pGuid": "home",
            "guid": "evt-local-only",
            "title": "Chris Starkey",
            "localStartDate": [20260430, 2026, 4, 30, 11, 0, 660],
            "localEndDate": [20260430, 2026, 4, 30, 12, 0, 720],
            "tz": "Europe/London",
            "description": "Open Gate",
            "allDay": False,
        }
    )

    assert event is not None
    assert event.starts_at.isoformat() == "2026-04-30T11:00:00+01:00"
    assert event.ends_at.isoformat() == "2026-04-30T12:00:00+01:00"


def test_icloud_event_normalizer_accepts_pycloud_event_object_fields() -> None:
    event = _normalize_event(
        SimpleNamespace(
            pguid="home",
            guid="evt-object",
            title="Chris Starkey",
            local_start_date=[20260430, 2026, 4, 30, 11, 0, 660],
            local_end_date=[20260430, 2026, 4, 30, 12, 0, 720],
            tz="Europe/London",
            all_day=False,
            private_comments="Open Gate",
        )
    )

    assert event is not None
    assert event.starts_at.isoformat() == "2026-04-30T11:00:00+01:00"
    assert event.ends_at.isoformat() == "2026-04-30T12:00:00+01:00"
    assert event_contains_open_gate(event)


def test_icloud_fetch_keeps_raw_events_so_notes_are_not_dropped() -> None:
    class FakeCalendar:
        def __init__(self) -> None:
            self.as_objs = None

        def events(self, starts_at, ends_at):
            return [{"title": "Lossy wrapper path"}]

        def get_events(self, *, from_dt, to_dt, as_objs):
            self.as_objs = as_objs
            return [{"title": "Chris Starkey", "privateComments": "Open Gate"}]

    calendar = FakeCalendar()
    events = ICloudCalendarClient()._fetch_calendar_events(
        SimpleNamespace(calendar=calendar),
        datetime(2026, 4, 30, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
    )

    assert calendar.as_objs is False
    assert events == [{"title": "Chris Starkey", "privateComments": "Open Gate"}]


def test_icloud_fetch_prefers_raw_refresh_payload_when_available() -> None:
    class FakeCalendar:
        def events(self, starts_at, ends_at):
            return [{"title": "Lossy wrapper path"}]

        def get_events(self, *, from_dt, to_dt, as_objs):
            return [{"title": "Still not the raw refresh path"}]

        def refresh_client(self, starts_at, ends_at):
            return {"Event": [{"title": "Chris Starkey", "privateComments": "Open Gate"}]}

    events = ICloudCalendarClient()._fetch_calendar_events(
        SimpleNamespace(calendar=FakeCalendar()),
        datetime(2026, 4, 30, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
    )

    assert events == [{"title": "Chris Starkey", "privateComments": "Open Gate"}]


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


def test_modern_icloud_2fa_is_not_treated_as_legacy_2sa() -> None:
    client = ICloudCalendarClient()
    auth_session = ICloudAuthSession(
        apple_id="user@example.com",
        api=SimpleNamespace(requires_2sa=True, requires_2fa=True),
        cookie_directory=Path("/tmp/iacs-icloud-test"),
    )

    assert client.requires_2sa(auth_session)
    assert client.requires_2fa(auth_session)
    assert not client.requires_legacy_2sa(auth_session)

    auth_session.api = SimpleNamespace(requires_2sa=True, requires_2fa=False)
    assert client.requires_legacy_2sa(auth_session)


@pytest.mark.asyncio
async def test_bad_icloud_verification_code_keeps_handshake_for_retry() -> None:
    class RejectingClient:
        def validate_2fa_code(self, auth_session, code):
            return False

        def cleanup_auth_session(self, auth_session):
            raise AssertionError("Rejected codes should not clear the pending handshake.")

    service = ICloudCalendarService(client=RejectingClient())
    auth_session = ICloudAuthSession(
        apple_id="user@example.com",
        api=object(),
        cookie_directory=Path("/tmp/iacs-icloud-test"),
    )
    service._pending["handshake"] = PendingICloudAuth(
        handshake_id="handshake",
        apple_id="user@example.com",
        auth_session=auth_session,
    )

    with pytest.raises(ICloudCalendarError, match="Apple rejected"):
        await service.verify_auth(
            SimpleNamespace(),
            handshake_id="handshake",
            code="123456",
            user=SimpleNamespace(id=uuid.uuid4(), username="admin", display_name="Admin"),
        )

    assert "handshake" in service._pending


@pytest.mark.asyncio
async def test_alfred_icloud_sync_requires_confirmation(monkeypatch) -> None:
    async def fake_runtime_config():
        return SimpleNamespace(site_timezone="Europe/London")

    monkeypatch.setattr(ai_tools, "get_runtime_config", fake_runtime_config)
    result = await ai_tools.trigger_icloud_sync({"confirm": False})

    assert result["requires_confirmation"] is True
    assert result["confirmation_field"] == "confirm"
    assert result["target"] == "iCloud Calendar"
