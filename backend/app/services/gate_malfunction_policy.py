"""Gate malfunction timing, normalized evidence and pure state representation."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.models import AccessEvent, GateMalfunctionState, Vehicle
from app.models.enums import GateMalfunctionStatus
from app.modules.gate.base import GateState

MALFUNCTION_TRIGGER_SECONDS = 5 * 60

ATTEMPT_OFFSETS_SECONDS = {
    1: 5 * 60,
    2: 5 * 60 + 45,
    3: 10 * 60 + 45,
    4: 70 * 60 + 45,
    5: 190 * 60 + 45,
}

MILESTONE_TRIGGERS = [
    (30 * 60, "30m", "Gate malfunction open for 30 minutes", "warning"),
    (60 * 60, "60m", "Gate malfunction open for 60 minutes", "critical"),
    (120 * 60, "2hrs", "Gate malfunction open for 2 hours", "critical"),
]

MALFUNCTION_STAGE_ORDER = {
    "initial": 0,
    "30m": 1,
    "60m": 2,
    "2hrs": 3,
    "fubar": 4,
    "resolved": 5,
}

UNSAFE_GATE_STATES = {GateState.OPEN, GateState.OPENING, GateState.CLOSING}

UNRESOLVED_STATUSES = {GateMalfunctionStatus.ACTIVE, GateMalfunctionStatus.FUBAR}

NOTIFICATION_TERMINAL_STATUSES = {"sent", "skipped", "review_required"}

NOTIFICATION_RETRY_SECONDS = [60, 5 * 60, 15 * 60]

NOTIFICATION_SENDING_STALE_SECONDS = 5 * 60

ATTEMPT_CLAIM_STALE_SECONDS = 5 * 60


@dataclass(frozen=True)
class GateSnapshot:
    entity_id: str
    name: str
    state: GateState
    state_changed_at: datetime | None
    observed_at: datetime
    keep_open_active: bool = False
    keep_open_entity_id: str | None = None

    @property
    def unsafe_open(self) -> bool:
        return self.state in UNSAFE_GATE_STATES


@dataclass(frozen=True)
class GateMalfunctionReadContext:
    id: uuid.UUID
    gate_entity_id: str
    gate_name: str | None
    status: GateMalfunctionStatus
    opened_at: datetime
    declared_at: datetime
    resolved_at: datetime | None
    last_gate_state: str | None

    def as_payload(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "gate_entity_id": self.gate_entity_id,
            "gate_name": self.gate_name,
            "status": self.status.value,
            "opened_at": self.opened_at.isoformat(),
            "declared_at": self.declared_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "last_gate_state": self.last_gate_state,
        }


def _coerce_gate_state_value(value: Any) -> GateState:
    if isinstance(value, GateState):
        return value
    try:
        return GateState(str(value or "").lower())
    except ValueError:
        return GateState.UNKNOWN


def access_event_details(event: AccessEvent) -> dict[str, Any]:
    return {
        "event_id": str(event.id),
        "registration_number": event.registration_number,
        "direction": event.direction.value,
        "decision": event.decision.value,
        "occurred_at": event.occurred_at.isoformat(),
    }


def vehicle_label(vehicle: Vehicle | None, fallback: str) -> str:
    if not vehicle:
        return fallback
    label = " ".join(part for part in [vehicle.make, vehicle.model] if part)
    return label or vehicle.description or vehicle.registration_number or fallback


def trace_summary(row: GateMalfunctionState) -> str:
    gate = row.gate_name or row.gate_entity_id
    if row.status == GateMalfunctionStatus.RESOLVED:
        return f"{gate} malfunction resolved after {format_duration(downtime_seconds(row))}."
    if row.status == GateMalfunctionStatus.FUBAR:
        return f"{gate} is FUBAR after {row.fix_attempts_count} automated recovery attempts."
    return f"{gate} is open; next recovery attempt is scheduled after {row.fix_attempts_count} attempts."


def downtime_seconds(row: GateMalfunctionState, *, now: datetime | None = None) -> int:
    end = row.resolved_at or row.fubar_at or now or datetime.now(tz=UTC)
    return max(0, int((end - row.opened_at).total_seconds()))


def format_duration(seconds: int) -> str:
    remaining = max(0, int(seconds))
    hours, remaining = divmod(remaining, 3600)
    minutes, seconds = divmod(remaining, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def normalize_notification_stage(value: Any) -> str:
    stage = str(value or "").strip().lower()
    return stage if stage in MALFUNCTION_STAGE_ORDER else "initial"


def coerce_gate_state(value: Any) -> GateState:
    return _coerce_gate_state_value(value)


def state_is_on(value: Any) -> bool:
    return str(value or "").strip().lower() == "on"


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def history_cursor(row: GateMalfunctionState) -> str:
    return f"{row.opened_at.isoformat()}|{row.id}"


def parse_history_cursor(value: str | None) -> tuple[datetime | None, uuid.UUID | None]:
    if not value or "|" not in value:
        return None, None
    opened_at_text, row_id_text = value.split("|", 1)
    opened_at = parse_datetime(opened_at_text)
    row_id = coerce_uuid(row_id_text)
    return opened_at, row_id


def coerce_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
