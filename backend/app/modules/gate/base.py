from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class GateState(StrEnum):
    UNKNOWN = "unknown"
    CLOSED = "closed"
    OPENING = "opening"
    OPEN = "open"
    CLOSING = "closing"
    FAULT = "fault"


@dataclass(frozen=True)
class GateCommandResult:
    accepted: bool
    state: GateState
    detail: str | None = None


class GateController(Protocol):
    """Contract implemented by physical or smart-home gate controllers."""

    async def open_gate(self, reason: str) -> GateCommandResult:
        """Open the gate for an audited reason."""

    async def current_state(self) -> GateState:
        """Return the latest known gate state."""
