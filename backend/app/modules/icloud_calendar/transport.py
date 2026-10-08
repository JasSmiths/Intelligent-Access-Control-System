"""Provider-local bounds before the locked SDK's first authentication request."""
from __future__ import annotations

from math import isfinite
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from requests import PreparedRequest, Response
from requests.adapters import HTTPAdapter

from app.modules.icloud_calendar.contracts import (
    CALENDAR_CONNECT_SECONDS,
    CALENDAR_READ_SECONDS,
    CalendarCoverage,
    CalendarWork,
)


class CalendarHTTPAdapter(HTTPAdapter):
    def __init__(self, work: CalendarWork) -> None:
        # Locked PyiCloudSession uses requests' default adapter/retry state.
        super().__init__()
        self.work = work
        self._coverage: CalendarCoverage | None = None
        self._endpoint: tuple[str, str, str] | None = None

    def bind_coverage(self, endpoint: str, coverage: CalendarCoverage) -> None:
        parts = urlsplit(endpoint)
        if parts.scheme not in {"http", "https"} or not parts.netloc or parts.path != "/ca/events":
            raise ValueError("Calendar coverage must bind the exact calendar events endpoint.")
        self._endpoint = parts.scheme, parts.netloc, parts.path
        self._coverage = coverage

    def _freeze_coverage(self, request: PreparedRequest) -> None:
        if self._coverage is None or request.method != "GET" or not request.url:
            return
        parts = urlsplit(request.url)
        if (parts.scheme, parts.netloc, parts.path) != self._endpoint:
            return
        coverage = self._coverage
        params = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                  if key not in {"startDate", "endDate", "usertz"}]
        params.extend((("startDate", coverage.start_date), ("endDate", coverage.end_date),
                       ("usertz", coverage.vendor_timezone)))
        request.url = urlunsplit(parts._replace(query=urlencode(params)))

    def send(
        self, request: PreparedRequest, stream: bool = False, timeout: Any = None,
        verify: Any = True, cert: Any = None, proxies: Any = None,
    ) -> Response:
        remaining = self.work.remaining()
        connect, read = min(CALENDAR_CONNECT_SECONDS, remaining), min(CALENDAR_READ_SECONDS, remaining)
        # Retain a smaller explicit SDK limit while replacing its usual None.
        if isinstance(timeout, tuple) and len(timeout) == 2:
            connect = _smaller_timeout(timeout[0], connect)
            read = _smaller_timeout(timeout[1], read)
        elif isinstance(timeout, (int, float)) and not isinstance(timeout, bool):
            connect, read = _smaller_timeout(timeout, connect), _smaller_timeout(timeout, read)
        self._freeze_coverage(request)
        return super().send(request, stream=stream, timeout=(connect, read),
                            verify=verify, cert=cert, proxies=proxies)


def _smaller_timeout(value: object, bound: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value > 0:
        return min(float(value), bound)
    return bound
