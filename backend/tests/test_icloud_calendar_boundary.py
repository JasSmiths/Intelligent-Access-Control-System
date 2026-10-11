"""Real locked SDK/session/adapter and destructive-fetch acceptance boundaries."""
import json
from datetime import UTC, datetime
from time import monotonic
from urllib.parse import parse_qsl, urlsplit

import pytest
from pyicloud import PyiCloudService
from pyicloud.services import calendar as sdk_calendar
from requests import Response
from requests.adapters import HTTPAdapter

from app.modules.icloud_calendar.contracts import (
    CalendarCoverage,
    CalendarWork,
    ICloudCalendarIncomplete,
    ICloudCalendarStopped,
    ICloudCalendarTimeout,
)
from app.modules.icloud_calendar.events import normalize_snapshot
from app.modules.icloud_calendar.transport import CalendarHTTPAdapter


def coverage():
    return CalendarCoverage.capture(datetime(2026, 7, 1, tzinfo=UTC),
                                    datetime(2026, 7, 16, tzinfo=UTC),
                                    vendor_timezone="America/Los_Angeles")


def event(**changes):
    return {"guid": "event1", "pGuid": "calendar1", "title": "Meeting: Ada",
            "startDate": [20260702, 2026, 7, 2, 10, 0, 600],
            "endDate": [20260702, 2026, 7, 2, 11, 0, 660], "tz": "Europe/London",
            "privateComments": "Open Gate", "allDay": False, **changes}


def test_supported_empty_and_source_backed_metadata_are_complete():
    result = normalize_snapshot({"Event": [], "Collection": [{"guid": "cal"}],
                                 "Invitee": [{"guid": "invite", "pGuid": "event"}]}, coverage())
    assert result.events == () and result.excluded_all_day == 0


def test_valid_all_day_is_excluded_but_timed_nonmatching_is_decodable():
    result = normalize_snapshot({"Event": [event(allDay=True), event(privateComments="Other")]}, coverage())
    assert result.excluded_all_day == 1 and len(result.events) == 1
    assert result.events[0].notes == "Other"
    assert result.events[0].starts_at.isoformat() == "2026-07-02T10:00:00+01:00"


@pytest.mark.parametrize("payload", [
    None, [], {}, {"Event": None}, {"Event": {}}, {"Event": [], "hasMore": False},
    {"Event": [], "error": None}, {"Event": [], "Collection": None},
    {"Event": [], "Collection": [{}]}, {"Event": [], "Invitee": [{"guid": "x"}]},
    {"Event": [None]}, {"Event": [event(guid=None)]}, {"Event": [event(title=3)]},
    {"Event": [event(startDate="bad")]}, {"Event": [event(tz="Not/AZone")]},
    {"Event": [event(allDay="false")]}, {"Event": [event(privateComments={})]},
    {"Event": [event(endDate=[20260701, 2026, 7, 1, 9, 0, 540])]},
])
def test_unsupported_or_malformed_fetch_cannot_become_empty(payload):
    with pytest.raises(ICloudCalendarIncomplete, match="No visitor passes were changed"):
        normalize_snapshot(payload, coverage())


def test_real_sdk_and_adapter_send_frozen_coverage_with_native_bounds(tmp_path, monkeypatch):
    # Actual pyicloud imports and constructor, not an injected vendor module.
    api = PyiCloudService("inert@example.invalid", "inert-not-a-credential",
                         cookie_directory=str(tmp_path), authenticate=False)
    work = CalendarWork(monotonic() + 120)
    adapter = CalendarHTTPAdapter(work)
    adapter.bind_coverage("https://calendar.invalid/ca/events", coverage())
    api.session.mount("https://", adapter)
    api.data = {"dsInfo": {"dsid": "synthetic"}}
    monkeypatch.setattr(sdk_calendar, "get_localzone_name", lambda: "Asia/Tokyo")
    sent = []

    def inert_send(self, request, **kwargs):
        sent.append((request, kwargs))
        response = Response()
        response.status_code = 200
        response._content = json.dumps({"Event": []}).encode()
        response.headers["Content-Type"] = "application/json"
        response.url = request.url
        response.request = request
        return response

    monkeypatch.setattr(HTTPAdapter, "send", inert_send)
    try:
        sdk = sdk_calendar.CalendarService("https://calendar.invalid", api.session, {})
        assert sdk.refresh_client(coverage().requested_start, coverage().requested_end) == {"Event": []}
        pairs = parse_qsl(urlsplit(sent[-1][0].url).query)
        assert dict(pairs)["usertz"] == "America/Los_Angeles"
        assert dict(pairs)["startDate"] == "2026-07-01"
        assert dict(pairs)["endDate"] == "2026-07-16"
        assert dict(pairs)["dsid"] == "synthetic"
        assert sent[-1][1]["timeout"] == (10.0, 30.0)
        assert sent[-1][1]["verify"] is True
        # Reprepared/duplicate query parameters collapse only the coverage keys.
        api.session.request_raw("GET", "https://calendar.invalid/ca/events", params=[
            ("usertz", "UTC"), ("usertz", "Asia/Tokyo"), ("startDate", "bad"),
            ("endDate", "bad"), ("preserved", "one"), ("preserved", "two")])
        pairs = parse_qsl(urlsplit(sent[-1][0].url).query)
        assert [value for key, value in pairs if key == "usertz"] == ["America/Los_Angeles"]
        assert [value for key, value in pairs if key == "preserved"] == ["one", "two"]
        # Auth and another calendar host are never rewritten.
        api.session.request_raw("POST", "https://calendar.invalid/auth", params={"usertz": "UTC"})
        assert dict(parse_qsl(urlsplit(sent[-1][0].url).query))["usertz"] == "UTC"
        api.session.request_raw("GET", "https://other.invalid/ca/events", params={"usertz": "UTC"})
        assert dict(parse_qsl(urlsplit(sent[-1][0].url).query))["usertz"] == "UTC"
    finally:
        api.session.close()


def test_stop_and_deadline_prevent_actual_transport_send(monkeypatch):
    import requests
    work = CalendarWork(monotonic() + 120)
    adapter = CalendarHTTPAdapter(work)
    request = requests.Request("GET", "https://calendar.invalid/ca/events").prepare()
    monkeypatch.setattr(HTTPAdapter, "send", lambda *args, **kwargs: pytest.fail("transport started"))
    work.request_stop()
    with pytest.raises(ICloudCalendarStopped):
        adapter.send(request)
    work.deadline = monotonic() - 1
    with pytest.raises(ICloudCalendarTimeout):
        adapter.send(request)
