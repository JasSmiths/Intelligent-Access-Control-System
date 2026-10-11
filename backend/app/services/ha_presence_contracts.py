"""Closed HA attempt identities; desired presence is never permission to replay."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.modules.home_assistant.input_boolean_contracts import FrozenHaConnection, HaAction

HA_SETTING_KEYS = ("home_assistant_token", "home_assistant_url")


HaInvalidationReason = Literal["person_changed", "simulation_reset", "synthetic_cleanup", "binding_retired"]


@dataclass(frozen=True)
class HaAttemptClaim:
    attempt_id: uuid.UUID
    target_key: str
    generation: int
    barrier_version: int
    token: uuid.UUID
    epoch: int


@dataclass(frozen=True)
class PreparedHaAttempt:
    claim: HaAttemptClaim
    connection: FrozenHaConnection = field(repr=False)
    entity_id: str = field(repr=False)
    action: HaAction
    origin_at: datetime
    deadline: datetime
    start_deadline: float


@dataclass(frozen=True)
class HaReservationSummary:
    applied: int = 0
    invalidated: int = 0
    skipped: int = 0


class HaLeaseLost(RuntimeError):
    """No actual provider/receipt mutation is allowed after ownership expires."""


class HaConfigurationUnavailable(ValueError):
    """Current optional HA configuration cannot authorize an effect."""


@dataclass(frozen=True)
class HaCurrentPerson:
    person_id: uuid.UUID
    is_active: bool
    entity_ids: list[str]
    entry_action: str
    exit_action: str


class HaCoordinationBusy(RuntimeError):
    """Retry the complete caller transaction, preserving its original authority."""

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
