from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services.restart_backfill import (
    MISSED_EVENT_BACKFILL_OVERLAP,
    MISSED_EVENT_RECONCILIATION_SOURCE,
    MissedAccessEventBackfillService,
    _direction_from_gate_observation,
    backfill_window_start,
    protect_event_ids_from_payload,
)
from app.models.enums import AccessDirection
from app.services.snapshot_recovery import protect_event_id_from_access_event


def test_backfill_window_uses_previous_runtime_heartbeat() -> None:
    startup = datetime(2026, 5, 2, 10, 0, tzinfo=UTC)
    previous = {"last_heartbeat_at": "2026-05-02T09:57:00+00:00"}

    window_start = backfill_window_start(previous, startup)

    assert window_start == datetime(2026, 5, 2, 9, 57, tzinfo=UTC) - MISSED_EVENT_BACKFILL_OVERLAP


def test_backfill_window_falls_back_to_latest_access_event() -> None:
    startup = datetime(2026, 5, 2, 10, 0, tzinfo=UTC)
    latest_event_at = datetime(2026, 5, 2, 9, 40, tzinfo=UTC)

    window_start = backfill_window_start({}, startup, latest_event_at=latest_event_at)

    assert window_start == latest_event_at - MISSED_EVENT_BACKFILL_OVERLAP


def test_protect_event_id_extraction_includes_backfill_payloads() -> None:
    payload = {
        "best": {"alarm": {"triggers": [{"eventId": "webhook-event"}]}},
        "protect_evidence": {"event_id": "backfill-event"},
        "backfill": {"protect_event_id": "manual-backfill-event"},
        "vehicle_session": {"protect_event_ids": ["session-event"]},
    }

    assert protect_event_ids_from_payload(payload) == {
        "webhook-event",
        "backfill-event",
        "manual-backfill-event",
        "session-event",
    }
    assert protect_event_id_from_access_event(SimpleNamespace(raw_payload=payload)) == "webhook-event"
    assert protect_event_id_from_access_event(
        SimpleNamespace(raw_payload={"protect_evidence": {"event_id": "backfill-event"}})
    ) == "backfill-event"


def test_gate_observation_direction_for_backfill() -> None:
    assert _direction_from_gate_observation(SimpleNamespace(state="closed")) == AccessDirection.ENTRY
    assert _direction_from_gate_observation(SimpleNamespace(state="open")) == AccessDirection.EXIT
    assert _direction_from_gate_observation(SimpleNamespace(state="opening")) == AccessDirection.EXIT
    assert _direction_from_gate_observation(SimpleNamespace(state="unknown")) is None


def test_reconciliation_service_uses_reconciliation_labels() -> None:
    service = MissedAccessEventBackfillService(source=MISSED_EVENT_RECONCILIATION_SOURCE)

    assert service._audit_backfill_action() == "access_event.reconciled"
    assert service._backfill_payload_source() == "protect_reconciliation"


@pytest.mark.asyncio
async def test_candidate_from_protect_track_normalizes_plate_and_confidence() -> None:
    class FakeProtect:
        async def event_lpr_track(self, event_id):
            assert event_id == "protect-1"
            return {
                "event": {
                    "id": "protect-1",
                    "camera_id": "camera-1",
                    "camera_name": "Gate",
                    "start": "2026-05-02T09:59:00+00:00",
                },
                "observations": [
                    {
                        "registration_number": "pe70 dhx",
                        "captured_at": "2026-05-02T09:59:01+00:00",
                        "confidence": 92,
                        "confidence_scale": "0_100",
                    }
                ],
            }

    candidate = await MissedAccessEventBackfillService()._candidate_from_protect_event(
        FakeProtect(),
        {
            "id": "protect-1",
            "type": "smartDetectZone",
            "smart_detect_types": ["vehicle"],
        },
    )

    assert candidate is not None
    assert candidate.registration_number == "PE70DHX"
    assert candidate.captured_at == datetime(2026, 5, 2, 9, 59, 1, tzinfo=UTC)
    assert candidate.confidence == 0.92
    assert candidate.camera_name == "Gate"
