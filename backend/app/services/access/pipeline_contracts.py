"""Checked durable identities and compact handoffs; preview is never authority."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal


class PipelineLeaseLost(RuntimeError):
    """A fenced owner cannot mutate or attempt after claim loss/expiry."""


class HaCoordinationBusy(RuntimeError):
    """Retry the complete caller transaction, preserving its original authority."""


@dataclass(frozen=True)
class CoordinatorClaim:
    token: uuid.UUID
    epoch: int
    lease_until: datetime


@dataclass(frozen=True)
class IngestClaim:
    ingest_id: uuid.UUID
    token: uuid.UUID
    coordinator: CoordinatorClaim


@dataclass(frozen=True)
class WindowClaim:
    window_id: uuid.UUID
    token: uuid.UUID
    coordinator: CoordinatorClaim


@dataclass(frozen=True)
class EvidenceReference:
    window_id: uuid.UUID
    version: Literal[2] = 2


@dataclass(frozen=True)
class WindowSummary:
    evidence: EvidenceReference
    source: str
    open_ordinal: int
    count: int
    first_seen: datetime
    updated_at: datetime
    min_received_at: datetime
    first_ingest_id: uuid.UUID
    best_ingest_id: uuid.UUID
    anchor_ingest_id: uuid.UUID | None


@dataclass(frozen=True)
class HaPresenceReservation:
    """Proposed current presence projection, not an external-attempt checkpoint.

    Outer owners flush the bounded ordered proposals once after every domain
    lock/mutation. Historical proposals only invalidate; origin never renews.
    """

    person_id: uuid.UUID
    event_id: uuid.UUID
    presence_event_id: uuid.UUID
    presence_state: Literal["present", "exited"]
    origin_at: datetime | None
    live_eligible: bool


@dataclass(frozen=True)
class CompletionClaim:
    work_id: uuid.UUID
    event_id: uuid.UUID
    stage: Literal["visual", "snapshot", "visitor_link", "zone_shadow", "publication"]
    token: uuid.UUID
    origin_at: datetime | None
    deadline: datetime | None
