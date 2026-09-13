import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace as _SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.modules.gate.base import GateCommandNotSent, GateCommandResult, GateState
from app.services.movement_ledger import GateCommandLease
from app.services.gate_commands import GateCommandCoordinator, GateCommandIntent

SimpleNamespace = cast(Any, _SimpleNamespace)


class FakeGateCommandLedger:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.records: dict[Any, Any] = {}

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
            command_metadata={},
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
        metadata: dict[str, Any] | None = None,
    ):
        record = self.records[command_id]
        record.accepted = accepted
        record.gate_state = gate_state
        record.detail = detail
        record.mechanically_confirmed = mechanically_confirmed
        record.exception_class = exception_class
        record.command_metadata = metadata or {}
        record.completed_at = datetime.now(tz=UTC)
        record.updated_at = record.completed_at
        self._lock.release()
        return record


@pytest.mark.asyncio
async def test_gate_command_coordinator_serializes_open_commands() -> None:
    calls: list[str] = []

    class SlowGate:
        async def open_gate(self, reason: str, *, bypass_schedule: bool = False, command_context=None):
            calls.append(f"start:{reason}")
            await asyncio.sleep(0)
            calls.append(f"end:{reason}")
            return GateCommandResult(True, GateState.OPENING, reason)

        async def current_state(self) -> GateState:
            return GateState.OPENING

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
        async def open_gate(self, reason: str, *, bypass_schedule: bool = False, command_context=None):
            return GateCommandResult(True, GateState.CLOSED, reason)

        async def current_state(self) -> GateState:
            return GateState.CLOSED

    outcome = await GateCommandCoordinator(lambda _name: StaleGate(), ledger=FakeGateCommandLedger()).execute_open(
        GateCommandIntent(reason="stale state", source="test")
    )

    assert outcome.accepted is True
    assert outcome.mechanically_confirmed is False
    assert outcome.requires_reconciliation is True
    assert outcome.as_payload()["requires_reconciliation"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
async def test_gate_command_coordinator_normalizes_controller_exceptions(monkeypatch, error_type) -> None:
    class BrokenGate:
        async def open_gate(self, reason: str, *, bypass_schedule: bool = False, command_context=None):
            raise error_type("HA down")

        async def current_state(self) -> GateState:
            return GateState.FAULT

    coordinator = GateCommandCoordinator(lambda _name: BrokenGate(), ledger=FakeGateCommandLedger())
    monkeypatch.setattr(coordinator, "get_receipt", AsyncMock(return_value=None))
    outcome = await coordinator.execute_open(GateCommandIntent(reason="open", source="test"))

    assert outcome.accepted is False
    assert outcome.state == GateState.UNKNOWN
    assert outcome.requires_reconciliation is True
    assert outcome.as_payload()["delivery"] == "unknown"
    assert outcome.detail == "HA down"
    assert outcome.exception_class == error_type.__name__


@pytest.mark.asyncio
async def test_typed_pre_dispatch_refusal_is_not_sent() -> None:
    class RefusedGate:
        async def open_gate(self, reason, *, bypass_schedule=False, command_context=None):
            raise GateCommandNotSent("Synthetic invalid admission target")

    outcome = await GateCommandCoordinator(lambda _name: RefusedGate(), ledger=FakeGateCommandLedger()).execute_open(
        GateCommandIntent(reason="open", source="test"))
    assert outcome.delivery == "not_sent"
    assert outcome.requires_reconciliation is False


async def test_manual_preview_uses_configured_adapter_without_command_or_admission(monkeypatch):
    from app.modules.gate import access_devices as adapter_module
    from app.modules.gate.access_devices import AccessDeviceGateController
    plan = {"scope": "all", "targets": [{"device_id": "synthetic-gate"}], "require_admission": False}
    preview = AsyncMock(return_value=plan)
    hardware = AsyncMock(side_effect=AssertionError("Preview cannot issue hardware commands"))
    monkeypatch.setattr(adapter_module, "get_access_device_service", lambda: SimpleNamespace(
        preview_gate_open=preview, open_access_gates=hardware))
    selected = []
    def controller_factory(name):
        selected.append(name)
        return AccessDeviceGateController()
    ledger = FakeGateCommandLedger()
    coordinator = GateCommandCoordinator(controller_factory=controller_factory, ledger=ledger)
    assert await coordinator.preview_manual_gate_open() is plan
    assert selected == ["configured"]
    preview.assert_awaited_once_with(require_admission=False)
    hardware.assert_not_awaited()
    assert not ledger.records
