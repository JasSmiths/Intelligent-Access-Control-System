"""Closed durable calendar run meaning and per-account checkpoint receipts."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.modules.icloud_calendar.contracts import CalendarCoverage
from app.services.workflows.execution_contracts import AutomationExecution, checked_execution

CALENDAR_LEASE_SECONDS = 30
CALENDAR_RENEW_SECONDS = 5


class CalendarRunConflict(ValueError):
    """A known run ID cannot be reassigned to another request or origin."""


class CalendarOwnershipLost(RuntimeError):
    """The captured attempt/account fence can no longer accept work."""


class CalendarOutcomeUnknown(RuntimeError):
    """An account commit must be resolved before that account may repeat."""


class CalendarLegacyRun(ValueError):
    """Historical context is readable but cannot be resumed by this owner."""


class CalendarAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token: uuid.UUID
    lease_until: AwareDatetime
    recovery_actor_user_id: uuid.UUID | None = None


class CalendarRunContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    origin: Literal["ui", "workflow"]
    original_actor_user_id: uuid.UUID | None
    execution: AutomationExecution | None = None
    account_ids: tuple[uuid.UUID, ...]
    site_timezone: str = Field(min_length=1)
    coverage: CalendarCoverage
    attempt: CalendarAttempt | None = None
    recovery_actor_user_id: uuid.UUID | None = None
    recovery_source: Literal["ui"] | None = None

    @model_validator(mode="after")
    def valid_origin_and_manifest(self) -> Self:
        if len(set(self.account_ids)) != len(self.account_ids):
            raise ValueError("Calendar account manifest contains duplicate identities.")
        if self.origin == "workflow":
            checked_execution(self.execution)
        elif self.execution is not None or self.original_actor_user_id is None:
            raise ValueError("A UI calendar run needs its actor and no workflow identity.")
        instants = (self.coverage.requested_start, self.coverage.requested_end,
                    self.coverage.effective_start, self.coverage.effective_end)
        if any(value.utcoffset() is None for value in instants):
            raise ValueError("Calendar coverage must retain timezone-aware instants.")
        if self.coverage.requested_end <= self.coverage.requested_start or self.coverage.effective_end <= self.coverage.effective_start:
            raise ValueError("Calendar coverage is not ordered.")
        return self

    @classmethod
    def stored(cls, value: object) -> Self:
        if value == {}:
            raise CalendarLegacyRun("This historical calendar run has no resumable account manifest. Start a new run.")
        return cls.model_validate(value)

    def matches_origin(
        self, origin: Literal["ui", "workflow"], actor_user_id: uuid.UUID | None,
        execution: AutomationExecution | None,
    ) -> bool:
        # Canonical checked execution is retained directly, not re-keyed into a
        # calendar-specific execution namespace. Recovery is a separate request.
        return (self.origin == origin and self.original_actor_user_id == actor_user_id
                and self.execution == execution)


CalendarCheckpointStatus = Literal[
    "ok", "removed", "stale_configuration", "busy", "interrupted", "incomplete",
    "requires_reauth", "error", "unknown",
]


class CalendarAccountCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: uuid.UUID
    apple_id: str
    display_name: str
    attempt_token: uuid.UUID
    status: CalendarCheckpointStatus
    events_scanned: int = Field(default=0, ge=0)
    events_matched: int = Field(default=0, ge=0)
    passes_created: int = Field(default=0, ge=0)
    passes_updated: int = Field(default=0, ge=0)
    passes_cancelled: int = Field(default=0, ge=0)
    passes_skipped: int = Field(default=0, ge=0)
    historical_coverage_skipped: int = Field(default=0, ge=0)
    error: str | None = None

    @property
    def terminal(self) -> bool:
        return self.status in {"ok", "removed", "stale_configuration"}

    @model_validator(mode="after")
    def only_committed_counts(self) -> Self:
        if self.status != "ok" and any((self.events_scanned, self.events_matched,
            self.passes_created, self.passes_updated, self.passes_cancelled,
            self.passes_skipped, self.historical_coverage_skipped)):
            raise ValueError("A failed calendar checkpoint cannot claim accepted counters.")
        return self


def checked_calendar_checkpoints(value: object) -> tuple[CalendarAccountCheckpoint, ...]:
    if not isinstance(value, list):
        raise TypeError("Calendar account checkpoints must be an ordered list.")
    checkpoints = tuple(CalendarAccountCheckpoint.model_validate(item) for item in value)
    if len({item.account_id for item in checkpoints}) != len(checkpoints):
        raise ValueError("Calendar account checkpoints contain duplicate identities.")
    return checkpoints


@dataclass(frozen=True)
class CalendarSettingRevision:
    """The actual setting primary key is its name; absence is explicit."""
    present: bool
    updated_at: datetime | None


@dataclass(frozen=True)
class CalendarAccountClaim:
    run_id: uuid.UUID
    attempt_token: uuid.UUID
    account_id: uuid.UUID
    account_token: uuid.UUID
    generation: int
    apple_id: str
    display_name: str
    encrypted_session_bundle: str
    setting_revision: CalendarSettingRevision
