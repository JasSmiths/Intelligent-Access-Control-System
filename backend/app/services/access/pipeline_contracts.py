"""Checked durable identities and compact handoffs; preview is never authority."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from app.modules.gate.base import GateState


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
    expected_revision: int
    origin_identity: tuple[str, ...]


@dataclass(frozen=True)
class TrustedGateObservation:
    """Code-owned synthetic/internal input; never constructed from webhook fields."""

    state: GateState
    observed_at: datetime
    controller: str = "synthetic"
    entity_id: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, GateState) or not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None:
            raise ValueError("Trusted gate observation requires canonical state and aware original time")

    def as_payload(self) -> dict[str, Any]:
        return {"state": self.state.value, "observed_at": self.observed_at.isoformat(),
                "controller": self.controller, "entity_id": self.entity_id, "detail": self.detail}


@dataclass(frozen=True)
class CoordinatorBoundary:
    phase: Literal["idle", "preparing", "flushing", "immediate_finalizing", "flush_finalizing"]
    ingest_id: uuid.UUID | None
    finalizing_window_id: uuid.UUID | None
