"""Closed optional-stage identities; retained links confer no provider authority."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

CompletionStage = Literal["visual", "snapshot", "visitor_link", "zone_shadow"]
CompletionResultKind = Literal["visual", "information"]
COMPLETION_STAGES: tuple[CompletionStage, ...] = (
    "visual",
    "snapshot",
    "visitor_link",
    "zone_shadow",
)


@dataclass(frozen=True)
class ExternalCompletionOrigin:
    """Explicit code-owned original gate observation, never inferred from a source name.

    Constructed only by external-admission persistence from its canonical synthetic
    read. Recovery uses the persisted checkpoint, never a fresh clock/trace field.
    """

    observation_id: uuid.UUID
    observed_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.observation_id, uuid.UUID)
            or not isinstance(self.observed_at, datetime)
            or self.observed_at.tzinfo is None
        ):
            raise ValueError(
                "External completion requires the original aware observation identity/time"
            )


@dataclass(frozen=True)
class RetainedResultReference:
    """Identity accepted durably before its original optional deadline."""

    kind: CompletionResultKind
    result_id: uuid.UUID
    revision: int
    accepted_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.kind, str)
            or self.kind not in {"visual", "information"}
            or not isinstance(self.result_id, uuid.UUID)
        ):
            raise ValueError("Unsupported retained result identity")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ValueError("Retained result revision must be positive")
        if not isinstance(self.accepted_at, datetime) or self.accepted_at.tzinfo is None:
            raise ValueError("Retained result acceptance requires an aware original timestamp")
