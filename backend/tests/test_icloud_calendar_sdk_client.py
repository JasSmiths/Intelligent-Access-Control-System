"""Real installed SDK authentication/fetch through inert native HTTP transport."""
import asyncio
import json
from datetime import UTC, datetime
from time import monotonic

import pytest
from pyicloud import PyiCloudService
from requests import ConnectionError, Response, Session
from requests.adapters import HTTPAdapter

from app.modules.icloud_calendar.client import ICloudCalendarClient, _bundle_directory
from app.modules.icloud_calendar.contracts import CalendarCoverage, CalendarWork, ICloudCalendarClientError
from app.modules.icloud_calendar.native import CalendarNativeOwner


def current():
    return CalendarNativeOwner(CalendarWork(monotonic() + 120), asyncio.get_running_loop())


def cached_bundle(directory):
    api = PyiCloudService("inert@example.invalid", cookie_directory=str(directory), authenticate=False)
    try:
        api.session.data["session_token"] = "synthetic-not-a-provider-token"
        api.session.cookies.set("X-APPLE-WEBAUTH-TOKEN", "synthetic-not-a-provider-cookie")
        api.session._save_session_data()
        return _bundle_directory(directory)
    finally:
        api.session.close()


@pytest.mark.asyncio
async def test_actual_sdk_auth_has_finite_transport_before_first_request_and_cleans(tmp_path, monkeypatch):
    directory = tmp_path / "owned-auth"
    directory.mkdir()
    monkeypatch.setattr(ICloudCalendarClient, "_new_cookie_directory", lambda self: directory)
    attempts, closed = [], []
    original_close = Session.close

    def close(self):
        closed.append(self)
        original_close(self)

    def reject(self, request, **kwargs):
        attempts.append((request, kwargs))
        raise ConnectionError("inert provider rejection; never sent")

    monkeypatch.setattr(Session, "close", close)
    monkeypatch.setattr(HTTPAdapter, "send", reject)
    operation = current()
    try:
        with pytest.raises(ICloudCalendarClientError, match="Unable to connect to iCloud"):
            await asyncio.to_thread(ICloudCalendarClient().start_auth, "inert@example.invalid",
                                    "synthetic-not-a-credential", operation=operation)
        assert attempts
        assert all(item[1]["timeout"] == (10.0, 30.0) for item in attempts)
        assert all(item[1]["verify"] is True for item in attempts)
        assert closed and not directory.exists()
        assert not operation._registrations
    finally:
        operation.close()


@pytest.mark.asyncio
async def test_actual_sdk_cached_auth_fetch_uses_frozen_coverage_and_cleans(tmp_path, monkeypatch):
    bundle = cached_bundle(tmp_path / "bundle-source")
    directory = tmp_path / "owned-fetch"
    directory.mkdir()
    monkeypatch.setattr(ICloudCalendarClient, "_new_cookie_directory", lambda self: directory)
    attempts, closed = [], []
    original_close = Session.close

    def close(self):
        closed.append(self)
        original_close(self)

    def inert_send(self, request, **kwargs):
        attempts.append((request, kwargs))
        response = Response()
        response.status_code, response.url, response.request = 200, request.url, request
        response.headers["Content-Type"] = "application/json"
        if request.url.endswith("/validate"):
            payload = {"hsaTrustedBrowser": True, "dsInfo": {"dsid": "synthetic", "hsaVersion": 2},
                       "webservices": {"calendar": {"url": "https://calendar.invalid"}}}
        else:
            assert "/ca/events?" in request.url
            payload = {"Event": []}
        response._content = json.dumps(payload).encode()
        return response

    monkeypatch.setattr(Session, "close", close)
    monkeypatch.setattr(HTTPAdapter, "send", inert_send)
    operation = current()
    coverage = CalendarCoverage.capture(datetime(2026, 7, 1, tzinfo=UTC),
                                        datetime(2026, 7, 16, tzinfo=UTC),
                                        vendor_timezone="America/Los_Angeles")
    try:
        result = await asyncio.to_thread(ICloudCalendarClient().fetch_snapshot,
            apple_id="inert@example.invalid", session_bundle=bundle, coverage=coverage,
            operation=operation)
        assert result.events == () and result.coverage == coverage
        assert len(attempts) == 2
        assert "usertz=America%2FLos_Angeles" in attempts[-1][0].url
        assert all(item[1]["timeout"] == (10.0, 30.0) for item in attempts)
        assert closed and not directory.exists()
        assert not operation._registrations
    finally:
        operation.close()
