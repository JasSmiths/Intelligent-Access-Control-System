from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest

from app.modules.gate.home_assistant import HomeAssistantGateController
from app.models import GateMalfunctionState
from app.models.enums import GateMalfunctionStatus
from app.modules.gate.base import GateState
from app.services.gate_malfunctions import (
    ATTEMPT_OFFSETS_SECONDS,
    GateMalfunctionService,
    GateSnapshot,
    active_stuck_open_malfunction_at,
)


def test_gate_malfunction_retry_schedule_is_fixed() -> None:
    assert ATTEMPT_OFFSETS_SECONDS == {
        1: 5 * 60,
        2: 5 * 60 + 45,
        3: 10 * 60 + 45,
        4: 70 * 60 + 45,
        5: 190 * 60 + 45,
    }


def test_gate_snapshot_marks_opening_open_and_closing_as_unsafe() -> None:
    now = datetime(2026, 4, 26, 7, 35, tzinfo=UTC)
    for state in [GateState.OPENING, GateState.OPEN, GateState.CLOSING]:
        assert GateSnapshot("cover.gate", "Gate", state, now, now).unsafe_open

    assert not GateSnapshot("cover.gate", "Gate", GateState.CLOSED, now, now).unsafe_open


def test_gate_malfunction_summary_distinguishes_active_resolved_and_fubar() -> None:
    service = GateMalfunctionService()
    opened_at = datetime(2026, 4, 26, 7, 30, tzinfo=UTC)
    row = GateMalfunctionState(
        gate_entity_id="cover.gate",
        gate_name="Top Gate",
        status=GateMalfunctionStatus.ACTIVE,
        opened_at=opened_at,
        declared_at=opened_at + timedelta(minutes=5),
        fix_attempts_count=2,
    )

    assert "is open" in service._trace_summary(row)

    row.status = GateMalfunctionStatus.FUBAR
    row.fubar_at = opened_at + timedelta(hours=3, minutes=10, seconds=45)
    row.fix_attempts_count = 5
    assert "FUBAR" in service._trace_summary(row)

    row.status = GateMalfunctionStatus.RESOLVED
    row.resolved_at = opened_at + timedelta(minutes=12)
    assert "resolved" in service._trace_summary(row)


def test_gate_malfunction_history_cursor_round_trips_opened_at_and_id() -> None:
    service = GateMalfunctionService()
    opened_at = datetime(2026, 4, 26, 7, 30, tzinfo=UTC)
    row_id = uuid.uuid4()
    row = GateMalfunctionState(
        id=row_id,
        gate_entity_id="cover.gate",
        gate_name="Top Gate",
        status=GateMalfunctionStatus.ACTIVE,
        opened_at=opened_at,
        declared_at=opened_at + timedelta(minutes=5),
    )

    parsed_opened_at, parsed_id = service._parse_history_cursor(service._history_cursor(row))

    assert parsed_opened_at == opened_at
    assert parsed_id == row_id


class FakeScalarSequenceSession:
    def __init__(self, values) -> None:
        self._values = list(values)

    async def scalar(self, _statement):
        return self._values.pop(0) if self._values else None


@pytest.mark.asyncio
async def test_current_open_transition_does_not_reopen_existing_malfunction() -> None:
    service = GateMalfunctionService()
    opened_at = datetime(2026, 5, 2, 18, 9, 3, 317079, tzinfo=UTC)
    existing = GateMalfunctionState(
        gate_entity_id="cover.top_gate",
        gate_name="Top Gate",
        status=GateMalfunctionStatus.ACTIVE,
        opened_at=opened_at,
        declared_at=opened_at + timedelta(minutes=5),
    )
    snapshot = GateSnapshot(
        "cover.top_gate",
        "Top Gate",
        GateState.OPEN,
        opened_at,
        opened_at + timedelta(minutes=8),
    )
    current_open_observation = SimpleNamespace(
        previous_state=GateState.CLOSED.value,
        state_changed_at=opened_at,
        observed_at=opened_at,
    )

    reopened_at = await service._reopened_after_existing(
        FakeScalarSequenceSession([None, current_open_observation]),
        existing,
        snapshot,
    )

    assert reopened_at is None


@pytest.mark.asyncio
async def test_later_closed_observation_reopens_existing_malfunction_episode() -> None:
    service = GateMalfunctionService()
    opened_at = datetime(2026, 5, 2, 18, 9, 3, tzinfo=UTC)
    closed_at = opened_at + timedelta(minutes=8)
    existing = GateMalfunctionState(
        gate_entity_id="cover.top_gate",
        gate_name="Top Gate",
        status=GateMalfunctionStatus.ACTIVE,
        opened_at=opened_at,
        declared_at=opened_at + timedelta(minutes=5),
    )
    snapshot = GateSnapshot(
        "cover.top_gate",
        "Top Gate",
        GateState.OPEN,
        closed_at + timedelta(minutes=1),
        closed_at + timedelta(minutes=7),
    )
    closed_observation = SimpleNamespace(
        previous_state=GateState.OPEN.value,
        state_changed_at=closed_at,
        observed_at=closed_at,
    )

    reopened_at = await service._reopened_after_existing(
        FakeScalarSequenceSession([closed_observation]),
        existing,
        snapshot,
    )

    assert reopened_at == closed_at


@pytest.mark.asyncio
async def test_active_stuck_open_malfunction_context_requires_unsafe_gate_state() -> None:
    opened_at = datetime(2026, 5, 2, 18, 9, 3, tzinfo=UTC)
    row = GateMalfunctionState(
        id=uuid.uuid4(),
        gate_entity_id="cover.top_gate",
        gate_name="Top Gate",
        status=GateMalfunctionStatus.ACTIVE,
        opened_at=opened_at,
        declared_at=opened_at + timedelta(minutes=5),
        last_gate_state=GateState.OPEN.value,
    )

    active = await active_stuck_open_malfunction_at(
        FakeScalarSequenceSession([row]),
        observed_at=opened_at + timedelta(minutes=8),
        gate_state=GateState.OPEN,
    )
    inactive = await active_stuck_open_malfunction_at(
        FakeScalarSequenceSession([row]),
        observed_at=opened_at + timedelta(minutes=8),
        gate_state=GateState.CLOSED,
    )

    assert active is not None
    assert active.as_payload()["id"] == str(row.id)
    assert inactive is None


@pytest.mark.asyncio
async def test_manual_recheck_override_requires_confirmation() -> None:
    service = GateMalfunctionService()

    result = await service.override(
        "not-a-uuid",
        action="recheck_live_state",
        reason="test",
        actor="Tester",
        confirm=False,
    )

    assert result["requires_confirmation"] is True
    assert result["confirmation_field"] == "confirm"


@pytest.mark.asyncio
async def test_recovery_gate_open_bypasses_configured_schedule(monkeypatch) -> None:
    calls: list[str] = []

    class FakeClient:
        async def call_service(self, service_name, service_data):
            calls.append(service_name)
            assert service_data == {"entity_id": "cover.top_gate"}
            return {}

        async def get_state(self, entity_id):
            assert entity_id == "cover.top_gate"
            return SimpleNamespace(state="open")

    async def fake_runtime_config():
        return SimpleNamespace(
            home_assistant_gate_entities=[
                {
                    "entity_id": "cover.top_gate",
                    "name": "Top Gate",
                    "enabled": True,
                    "schedule_id": "blocked-schedule",
                }
            ],
            home_assistant_gate_open_service="cover.open_cover",
            home_assistant_gate_entity_id="",
            site_timezone="UTC",
            schedule_default_policy="deny",
        )

    async def schedule_should_not_run(*args, **kwargs):
        raise AssertionError("Recovery attempts must bypass gate access schedules.")

    monkeypatch.setattr("app.modules.gate.home_assistant.get_runtime_config", fake_runtime_config)
    monkeypatch.setattr("app.modules.gate.home_assistant.evaluate_schedule_id", schedule_should_not_run)

    result = await HomeAssistantGateController(FakeClient()).open_gate(
        "recovery",
        bypass_schedule=True,
    )

    assert result.accepted is True
    assert calls == ["cover.open_cover"]
