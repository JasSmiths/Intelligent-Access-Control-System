"""Inert values shared by UniFi extraction, queues and current-evidence caches."""

from dataclasses import asdict, dataclass
from datetime import datetime
from time import monotonic
from typing import Any


@dataclass(slots=True)
class ProtectEvidenceGeneration:
    value: int = 0

    def invalidate(self) -> None:
        self.value += 1

    def stamp(self) -> "ProtectEvidenceStamp":
        return ProtectEvidenceStamp(self, self.value)


@dataclass(frozen=True, slots=True)
class ProtectEvidenceStamp:
    cell: ProtectEvidenceGeneration
    generation: int

    def current(self) -> bool:
        return self.cell.value == self.generation


@dataclass(frozen=True, slots=True)
class ProtectWorkValidity:
    stamp: ProtectEvidenceStamp
    deadline: float | None = None

    def current(self) -> bool:
        return self.stamp.current() and (self.deadline is None or monotonic() < self.deadline)


@dataclass(frozen=True, slots=True)
class ProtectTimingFields:
    object_id: str | None
    received_at: str | None
    event_id: str | None
    camera_id: str | None
    camera_name: str | None
    protect_action: str | None
    protect_model: str | None
    smart_detect_types: tuple[str | None, ...]

    def common(self) -> dict[str, Any]:
        fields = asdict(self)
        fields.pop("object_id")
        fields["smart_detect_types"] = list(self.smart_detect_types)
        return fields


@dataclass(frozen=True, slots=True)
class ProtectVisualFields:
    received_at: str | None
    event_id: str | None
    camera_id: str | None
    camera_name: str | None
    protect_action: str | None = "track_probe"
    protect_model: str | None = "event"

    def common(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProtectProbeContext:
    event_id: str
    received_at: datetime
    admitted_at: float
    deadline: float
    timing: ProtectTimingFields
    visual: ProtectVisualFields
