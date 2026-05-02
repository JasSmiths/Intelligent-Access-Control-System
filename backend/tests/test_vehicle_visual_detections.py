from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.vehicle_visual_detections import (
    VehiclePresenceTracker,
    VehicleVisualDetectionRecorder,
    extract_unifi_protect_track_vehicle_visual_observations,
    extract_unifi_protect_vehicle_visual_observations,
)


class EnumValue:
    def __init__(self, value: str) -> None:
        self.value = value


def test_extracts_vehicle_colour_and_type_from_detected_thumbnail() -> None:
    message = SimpleNamespace(
        action=EnumValue("update"),
        changed_data={"modelKey": "event"},
        new_obj=SimpleNamespace(
            id="event-1",
            camera_id="camera-1",
            camera=SimpleNamespace(display_name="Gate"),
            start=datetime(2026, 4, 28, 12, tzinfo=UTC),
            smart_detect_types=[EnumValue("licensePlate"), EnumValue("vehicle")],
            metadata=SimpleNamespace(
                detected_thumbnails=[
                    SimpleNamespace(
                        type="vehicle",
                        clock_best_wall=datetime(2026, 4, 28, 12, 0, 2, tzinfo=UTC),
                        confidence=92,
                        name=None,
                        group=SimpleNamespace(matched_name="ab12 cde", confidence=87),
                        attributes=SimpleNamespace(
                            color=SimpleNamespace(val="gray", confidence=91),
                            vehicleType=SimpleNamespace(val="sedan", confidence=82),
                        ),
                    )
                ]
            ),
        ),
    )

    observations = extract_unifi_protect_vehicle_visual_observations(
        message,
        received_at=datetime(2026, 4, 28, 12, 0, 3, tzinfo=UTC),
    )

    assert len(observations) == 1
    assert observations[0].registration_number == "AB12CDE"
    assert observations[0].observed_vehicle_color == "Grey"
    assert observations[0].observed_vehicle_type == "Car"
    assert observations[0].vehicle_color_confidence == 91
    assert observations[0].vehicle_type_confidence == 82
    assert observations[0].camera_name == "Gate"


def test_extracts_vehicle_colour_and_type_from_smart_detect_track() -> None:
    observations = extract_unifi_protect_track_vehicle_visual_observations(
        {
            "payload": [
                {
                    "timestamp": 1777394788086,
                    "licensePlate": "ab12 cde",
                    "objectType": "vehicle",
                    "attributes": {
                        "color": {"val": "black", "confidence": 89},
                        "vehicleType": {"val": "van", "confidence": 78},
                    },
                }
            ]
        },
        event=SimpleNamespace(
            id="event-1",
            camera_id="camera-1",
            camera=SimpleNamespace(display_name="Gate"),
        ),
        received_at=datetime(2026, 4, 28, 12, 0, 3, tzinfo=UTC),
        probe_attempt=2,
    )

    assert len(observations) == 1
    assert observations[0].source == "uiprotect_track"
    assert observations[0].registration_number == "AB12CDE"
    assert observations[0].observed_vehicle_color == "Black"
    assert observations[0].observed_vehicle_type == "Van"
    assert observations[0].payload_path == "smartDetectTrack.payload[0].attributes"


@pytest.mark.asyncio
async def test_recorder_returns_nearest_matching_visual_detection() -> None:
    recorder = VehicleVisualDetectionRecorder()
    await recorder.record_unifi_protect_track(
        {
            "payload": [
                {
                    "timestamp": 1777394788086,
                    "licensePlate": "ab12 cde",
                    "objectType": "vehicle",
                    "attributes": {
                        "color": {"val": "silver", "confidence": 80},
                        "vehicleType": {"val": "truck", "confidence": 70},
                    },
                }
            ]
        },
        event=SimpleNamespace(id="event-1", camera_id="camera-1"),
        received_at=datetime(2026, 4, 28, 16, 46, 29, tzinfo=UTC),
    )

    match = await recorder.recent_match(
        "AB12CDE",
        occurred_at=datetime(2026, 4, 28, 16, 46, 28, tzinfo=UTC),
    )

    assert match is not None
    assert match["observed_vehicle_color"] == "Silver"
    assert match["observed_vehicle_type"] == "Truck"


@pytest.mark.asyncio
async def test_vehicle_presence_tracker_uses_camera_vehicle_detection() -> None:
    tracker = VehiclePresenceTracker()
    observed_at = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)

    await tracker.record_unifi_realtime_payload(
        {
            "camera": {
                "id": "camera-1",
                "name": "Gate",
                "detections": {"active": ["vehicle"]},
            }
        },
        received_at=observed_at,
    )

    evidence = await tracker.recent_evidence(
        camera_id="camera-1",
        observed_at=datetime(2026, 4, 28, 12, 0, 10, tzinfo=UTC),
        max_age_seconds=30,
    )

    assert evidence is not None
    assert evidence["source"] == "uiprotect_camera"
    assert evidence["camera_id"] == "camera-1"


@pytest.mark.asyncio
async def test_vehicle_presence_tracker_marks_ended_event_inactive() -> None:
    tracker = VehiclePresenceTracker()
    observed_at = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)

    await tracker.record_unifi_realtime_payload(
        {
            "event": {
                "id": "event-1",
                "camera_id": "camera-1",
                "camera_name": "Gate",
                "start": observed_at.isoformat(),
                "end": None,
                "smart_detect_types": ["vehicle"],
            }
        },
        received_at=observed_at,
    )
    assert await tracker.recent_evidence(
        event_ids={"event-1"},
        observed_at=observed_at + timedelta(seconds=5),
        max_age_seconds=30,
    )

    await tracker.record_unifi_realtime_payload(
        {
            "event": {
                "id": "event-1",
                "camera_id": "camera-1",
                "camera_name": "Gate",
                "start": observed_at.isoformat(),
                "end": (observed_at + timedelta(seconds=6)).isoformat(),
                "smart_detect_types": ["vehicle"],
            }
        },
        received_at=observed_at + timedelta(seconds=6),
    )

    evidence = await tracker.recent_evidence(
        event_ids={"event-1"},
        observed_at=observed_at + timedelta(seconds=7),
        max_age_seconds=30,
    )
    assert evidence is None


@pytest.mark.asyncio
async def test_vehicle_presence_tracker_expires_stale_evidence() -> None:
    tracker = VehiclePresenceTracker()
    observed_at = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)

    await tracker.record_unifi_protect_track(
        {
            "payload": [
                {
                    "timestamp": int(observed_at.timestamp() * 1000),
                    "licensePlate": "ab12 cde",
                    "objectType": "vehicle",
                }
            ]
        },
        event=SimpleNamespace(id="event-1", camera_id="camera-1"),
        received_at=observed_at,
    )

    evidence = await tracker.recent_evidence(
        registration_number="AB12CDE",
        observed_at=observed_at + timedelta(seconds=31),
        max_age_seconds=30,
    )
    assert evidence is None
