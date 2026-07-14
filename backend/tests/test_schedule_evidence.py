from datetime import UTC, datetime
from types import SimpleNamespace
import uuid

from app.models import Schedule
from app.services.schedules import evaluate_schedule_id, schedule_evaluation_payload


async def test_schedule_evidence_captures_evaluated_local_time_and_configuration() -> None:
    schedule_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    schedule = Schedule(
        id=schedule_id,
        name="Garage permitted hours",
        time_blocks={"1": [{"start": "06:00", "end": "22:30"}]},
    )

    class Session:
        async def get(self, model, row_id):
            assert model is Schedule
            assert row_id == schedule_id
            return schedule

    evaluation = await evaluate_schedule_id(
        Session(),
        schedule_id,
        datetime(2026, 7, 14, 21, 47, tzinfo=UTC),
        timezone_name="Europe/London",
        default_policy="deny",
        source="garage_door",
    )
    payload = schedule_evaluation_payload(evaluation)

    assert payload["allowed"] is False
    assert payload["reason_code"] == "schedule_outside_window"
    assert payload["evaluated_local_at"] == "2026-07-14T22:47:00+01:00"
    assert payload["allowed_intervals"] == [{"start": "06:00", "end": "22:30"}]
    assert payload["configuration_snapshot"]["source"] == "captured_at_evaluation"
    assert payload["configuration_snapshot"]["time_blocks"]["1"] == [
        {"start": "06:00", "end": "22:30"}
    ]


async def test_schedule_evidence_handles_naive_legacy_utc_timestamps() -> None:
    schedule = SimpleNamespace(
        id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        name="Always",
        time_blocks={"1": [{"start": "00:00", "end": "24:00"}]},
    )

    class Session:
        async def get(self, _model, _row_id):
            return schedule

    evaluation = await evaluate_schedule_id(
        Session(),
        schedule.id,
        datetime(2026, 7, 14, 12, 0),
        timezone_name="UTC",
        default_policy="deny",
        source="legacy",
    )

    assert evaluation.allowed is True
    assert evaluation.evaluated_at == datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
