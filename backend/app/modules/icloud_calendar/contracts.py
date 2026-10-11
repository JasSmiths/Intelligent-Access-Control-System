"""Calendar vendor facts and one cooperative SDK-operation lifetime."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from threading import Event
from time import monotonic
from zoneinfo import ZoneInfo

from tzlocal import get_localzone_name

CALENDAR_ATTEMPT_SECONDS = 120.0
CALENDAR_CONNECT_SECONDS = 10.0
CALENDAR_READ_SECONDS = 30.0


class ICloudCalendarClientError(RuntimeError):
    """Safe, actionable failure at the provider boundary."""


class ICloudCalendarReauthRequired(ICloudCalendarClientError):
    """The saved provider session must be explicitly reconnected."""


class ICloudCalendarIncomplete(ICloudCalendarClientError):
    """An unsupported/undecodable response cannot authorize reconciliation."""


class ICloudCalendarStopped(ICloudCalendarClientError):
    """The actual child is draining and cannot start another stage."""


class ICloudCalendarTimeout(ICloudCalendarClientError):
    """The original whole-attempt deadline expired."""


@dataclass
class CalendarWork:
    deadline: float
    _stop: Event = field(default_factory=Event, repr=False)

    def request_stop(self) -> None:
        self._stop.set()

    def remaining(self) -> float:
        now = monotonic()
        if now >= self.deadline:
            raise ICloudCalendarTimeout("Calendar operation exceeded its 120-second limit.")
        if self._stop.is_set():
            raise ICloudCalendarStopped("Calendar operation stopped before completion.")
        return self.deadline - now

    def check(self) -> None:
        self.remaining()


@dataclass(frozen=True)
class CalendarCoverage:
    requested_start: datetime
    requested_end: datetime
    start_date: str
    end_date: str
    vendor_timezone: str
    effective_start: datetime
    effective_end: datetime

    @classmethod
    def capture(
        cls, starts_at: datetime, ends_at: datetime, *, vendor_timezone: str | None = None
    ) -> CalendarCoverage:
        if starts_at.utcoffset() is None or ends_at.utcoffset() is None or ends_at <= starts_at:
            raise ValueError("Calendar coverage needs an ordered timezone-aware range.")
        starts_at, ends_at = starts_at.astimezone(UTC), ends_at.astimezone(UTC)
        timezone_name = vendor_timezone or get_localzone_name()
        timezone = ZoneInfo(timezone_name)
        # Locked refresh_client formats the supplied UTC objects as API_DATE;
        # its end date is inclusive in the vendor's usertz.
        start_date, end_date = starts_at.strftime("%Y-%m-%d"), ends_at.strftime("%Y-%m-%d")
        effective_start = datetime.combine(date.fromisoformat(start_date), time(), timezone)
        effective_end = datetime.combine(
            date.fromisoformat(end_date) + timedelta(days=1), time(), timezone
        )
        return cls(starts_at, ends_at, start_date, end_date, timezone_name,
                   effective_start.astimezone(UTC), effective_end.astimezone(UTC))


@dataclass(frozen=True)
class ICloudCalendarEvent:
    calendar_id: str
    event_id: str
    title: str
    starts_at: datetime
    ends_at: datetime
    description: str
    notes: str


@dataclass(frozen=True)
class CalendarSnapshot:
    coverage: CalendarCoverage
    events: tuple[ICloudCalendarEvent, ...]
    excluded_all_day: int
