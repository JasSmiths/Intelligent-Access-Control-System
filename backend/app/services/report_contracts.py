"""Report-specific limits, request authority and truthful pipeline failures."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from math import ceil
from threading import Event
from time import monotonic

MAX_REPORT_ACTIVITY_ROWS = 10_000
MAX_REPORT_VEHICLES = 10_000
MAX_REPORT_IMAGE_BYTES = 25 * 1024 * 1024
MAX_REPORT_IMAGE_PIXELS = 64 * 1024 * 1024
REPORT_PIPELINE_SECONDS = 120.0
REPORT_COMMIT_ATTEMPTS = 3


class ReportExportError(Exception):
    """An invalid report request or unsupported source; safe detail for the user."""
    status_code = 400
    retry_after: str | None = None


class ReportLimitError(ReportExportError):
    status_code = 413


class ReportBusyError(ReportExportError):
    status_code = 503
    retry_after = "1"


class ReportTimeoutError(ReportExportError):
    status_code = 504


class ReportAuthorityError(ReportExportError):
    status_code = 401


class ReportOutcomeUnknown(ReportExportError):
    status_code = 503


@dataclass(frozen=True)
class ReportDownload:
    report_number: str
    pdf_path: str


@dataclass(frozen=True)
class ReportAuthority:
    user_id: uuid.UUID
    token: str = field(repr=False)


@dataclass(frozen=True)
class ReportRequest:
    person_id: uuid.UUID | None
    visitor_pass_id: uuid.UUID | None
    period_start: datetime
    period_end: datetime
    include_denied: bool
    include_snapshots: bool
    include_confidence: bool

    @property
    def options(self) -> dict[str, bool]:
        return {"include_denied": self.include_denied,
                "include_snapshots": self.include_snapshots,
                "include_confidence": self.include_confidence}


class ReportStoppedError(ReportBusyError):
    """No later stage/commit may begin after the request owner stops."""


@dataclass
class ReportWork:
    """One report's immutable deadline and cooperative, thread-safe stop flag."""
    deadline: float
    _stop: Event = field(default_factory=Event, repr=False)

    def request_stop(self) -> None:
        self._stop.set()

    def check(self) -> None:
        self.remaining()

    def remaining(self) -> float:
        now = monotonic()
        if now >= self.deadline:
            raise ReportTimeoutError("Report export exceeded its 120-second limit. Choose a shorter period and try again.")
        if self._stop.is_set():
            raise ReportStoppedError("Report export stopped before completion. Try again when the service is ready.")
        return self.deadline - now

    def remaining_milliseconds(self) -> int:
        # Both native APIs interpret zero as disabling their timeout.
        return max(1, ceil(self.remaining() * 1000))
