from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.models.enums import MovementSagaState
from app.modules.gate.base import GateState
from app.services import movement_reconciliation as movement_reconciliation_module
from app.services.movement_reconciliation import MovementReconciliationService

from .helpers import assert_contract_subset, load_contract_fixture


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, event_type: str, payload: dict) -> None:
        self.published.append((event_type, payload))


def _saga(**overrides):
    values = {
        "id": uuid.UUID("55555555-5555-5555-5555-555555555555"),
        "state": MovementSagaState.COMPLETED,
        "reconciliation_required": False,
        "gate_command_required": True,
        "presence_committed": True,
        "failure_detail": None,
        "updated_at": _dt("2026-05-31T08:16:00+00:00"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_movement_reconciliation_contract_publishes_completed_saga_and_gate_command(monkeypatch) -> None:
    bus = _FakeBus()
    monkeypatch.setattr(movement_reconciliation_module, "event_bus", bus)
    command = SimpleNamespace(id=uuid.UUID("66666666-6666-6666-6666-666666666666"))

    await MovementReconciliationService()._publish_reconciled(_saga(), command, GateState.OPEN)

    expected_payload = load_contract_fixture("realtime/movement_reconciled.json")
    assert [event_type for event_type, _payload in bus.published] == [
        "movement_saga.reconciled",
        "gate.command.reconciled",
    ]
    assert_contract_subset(bus.published[0][1], expected_payload)
    assert_contract_subset(bus.published[1][1], expected_payload)


@pytest.mark.asyncio
async def test_movement_reconciliation_contract_fails_loudly_for_unresolved_saga(monkeypatch) -> None:
    bus = _FakeBus()
    monkeypatch.setattr(movement_reconciliation_module, "event_bus", bus)
    saga = _saga(
        state=MovementSagaState.FAILED,
        reconciliation_required=True,
        presence_committed=False,
        failure_detail="Gate stayed closed.",
    )

    await MovementReconciliationService()._publish_saga_failed(saga, "Gate stayed closed.")

    assert bus.published == [
        (
            "movement_saga.failed",
            {
                "movement_saga": {
                    "id": "55555555-5555-5555-5555-555555555555",
                    "state": "failed",
                    "reconciliation_required": True,
                    "gate_command_required": True,
                    "presence_committed": False,
                    "failure_detail": "Gate stayed closed.",
                    "updated_at": "2026-05-31T08:16:00+00:00",
                },
                "detail": "Gate stayed closed.",
            },
        )
    ]
