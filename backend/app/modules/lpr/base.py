from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol


@dataclass(frozen=True)
class PlateRead:
    """Normalized LPR read emitted by any camera/vendor adapter."""

    registration_number: str
    confidence: float
    source: str
    captured_at: datetime
    raw_payload: dict
    candidate_registration_numbers: tuple[str, ...] = field(default_factory=tuple)


class LprAdapter(Protocol):
    """Contract for camera/vendor-specific LPR adapters."""

    source_name: str

    def to_plate_read(self, payload: object) -> PlateRead:
        """Normalize a vendor payload into a `PlateRead`."""


def now_utc() -> datetime:
    return datetime.now(tz=UTC)
