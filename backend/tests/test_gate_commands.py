import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.modules.gate.base import GateCommandResult, GateState
from app.services.movement_ledger import GateCommandLease
from app.services.gate_commands import GateCommandCoordinator, GateCommandIntent


class FakeGateCommandLedger:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.records = {}

    async def claim_gate_command(self, _intent) -> GateCommandLease:
        await self._lock.acquire()
        now = datetime.now(tz=UTC)
        record = SimpleNamespace(
            id=uuid.uuid4(),
            started_at=now,
            created_at=now,
            updated_at=now,
            completed_at=None,
            accepted=None,
            gate_state=None,
            detail=None,
            mechanically_confirmed=False,
            exception_class=None,
            lease_token="lease-token",
        )
        self.records[record.id] = record
        return GateCommandLease(record=record, lease_token="lease-token")

    async def complete_gate_command(
        self,
        command_id,
        *,
        lease_token: str,
        accepted: bool,
        gate_state: str,
        detail: str | None,
        mechanically_confirmed: bool,
        requires_reconciliation: bool,
        exception_class: str | None = None,
    ):
        record = self.records[command_id]
        record.accepted = accepted
        record.gate_state = gate_state
        record.detail = detail
        record.mechanically_confirmed = mechanically_confirmed
        record.exception_class = exception_class
        record.completed_at = datetime.now(tz=UTC)
        record.updated_at = record.completed_at
        self._lock.release()
        return record


@pytest.mark.asyncio
async def test_gate_command_coordinator_serializes_open_commands() -> None:
    calls: list[str] = []

    class SlowGate:
        async def open_gate(self, reason: str, *, bypass_schedule: bool = False):
            calls.append(f"start:{reason}")
            await asyncio.sleep(0)
            calls.append(f"end:{reason}")
            return GateCommandResult(True, GateState.OPENING, reason)

    coordinator = GateCommandCoordinator(lambda _name: SlowGate(), ledger=FakeGateCommandLedger())

    first, second = await asyncio.gather(
        coordinator.execute_open(GateCommandIntent(reason="one", source="test")),
        coordinator.execute_open(GateCommandIntent(reason="two", source="test")),
    )

    assert first.accepted is True
    assert first.mechanically_confirmed is True
    assert second.accepted is True
    assert calls == ["start:one", "end:one", "start:two", "end:two"]


@pytest.mark.asyncio
async def test_gate_command_coordinator_marks_accepted_stale_state_for_reconciliation() -> None:
    class StaleGate:
        async def open_gate(self, reason: str, *, bypass_schedule: bool = False):
            return GateCommandResult(True, GateState.CLOSED, reason)

    outcome = await GateCommandCoordinator(lambda _name: StaleGate(), ledger=FakeGateCommandLedger()).execute_open(
        GateCommandIntent(reason="stale state", source="test")
    )

    assert outcome.accepted is True
    assert outcome.mechanically_confirmed is False
    assert outcome.requires_reconciliation is True
    assert outcome.as_payload()["requires_reconciliation"] is True


@pytest.mark.asyncio
async def test_gate_command_coordinator_normalizes_controller_exceptions() -> None:
    class BrokenGate:
        async def open_gate(self, reason: str, *, bypass_schedule: bool = False):
            raise RuntimeError("HA down")

    outcome = await GateCommandCoordinator(lambda _name: BrokenGate(), ledger=FakeGateCommandLedger()).execute_open(
        GateCommandIntent(reason="open", source="test")
    )

    assert outcome.accepted is False
    assert outcome.state == GateState.FAULT
    assert outcome.detail == "HA down"
    assert outcome.exception_class == "RuntimeError"
