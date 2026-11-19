"""Bounded authority for one live recognition's phone-location refresh."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

LOCATION_UPDATED = "resident.location_updated"
LOCATION_REFRESH_TRIGGER = "resident_location_refresh"


@dataclass(frozen=True)
class LocationRefresh:
    run_id: uuid.UUID
    person_id: uuid.UUID
    vehicle_id: uuid.UUID
    source: str
    captured_at: datetime
    requested_at: datetime
    expires_at: datetime
    binding: str
    epoch: str
    gate_observed_at: datetime | None = None
    gate_state: str | None = None
    delivery_failure: str | None = None

    def current(self, *, person_id: uuid.UUID, vehicle_id: uuid.UUID, source: str, captured_at: datetime,
                binding: str, epoch: str, now: datetime) -> bool:
        return (self.delivery_failure is None
                and self.person_id == person_id and self.vehicle_id == vehicle_id and self.source == source
                and self.captured_at == captured_at
                and self.binding == binding and self.epoch == epoch
                and self.requested_at <= now < self.expires_at
                and 0 < (self.expires_at - self.requested_at).total_seconds() <= 20)
