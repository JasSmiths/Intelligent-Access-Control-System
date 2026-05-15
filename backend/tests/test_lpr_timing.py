from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.lpr_timing import extract_unifi_protect_lpr_observations, extract_unifi_protect_track_observations


class EnumValue:
    def __init__(self, value: str) -> None:
        self.value = value


def test_extracts_plate_from_protect_detected_thumbnail_group() -> None:
    message = SimpleNamespace(
        action=EnumValue("update"),
        changed_data={"modelKey": "event"},
        new_obj=SimpleNamespace(
            id="event-1",
            camera_id="camera-1",
            camera=SimpleNamespace(display_name="Gate LPR"),
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
                        attributes=None,
                    )
                ]
            ),
        ),
    )

    observations = extract_unifi_protect_lpr_observations(
        message,
        received_at=datetime(2026, 4, 28, 12, 0, 3, tzinfo=UTC),
    )

    assert len(observations) == 1
    assert observations[0].registration_number == "AB12CDE"
    assert observations[0].source_detail == "event_thumbnail.group.matched_name"
    assert observations[0].camera_name == "Gate LPR"
    assert observations[0].confidence == 87


def test_extracts_plate_from_protect_detected_thumbnail_group_dict() -> None:
    message = SimpleNamespace(
        action=EnumValue("update"),
        changed_data={"modelKey": "event"},
        new_obj=SimpleNamespace(
            id="event-1",
            camera_id="camera-1",
            camera=SimpleNamespace(display_name="Gate LPR"),
            start=datetime(2026, 4, 28, 12, tzinfo=UTC),
            smart_detect_types=[EnumValue("licensePlate"), EnumValue("vehicle")],
            metadata=SimpleNamespace(
                detected_thumbnails=[
                    SimpleNamespace(
                        type="vehicle",
                        clock_best_wall=datetime(2026, 4, 28, 12, 0, 2, tzinfo=UTC),
                        confidence=92,
                        name=None,
                        group={"matchedName": "ab12 cde", "confidence": 87},
                        attributes=None,
                    )
                ]
            ),
        ),
    )

    observations = extract_unifi_protect_lpr_observations(
        message,
        received_at=datetime(2026, 4, 28, 12, 0, 3, tzinfo=UTC),
    )

    assert len(observations) == 1
    assert observations[0].registration_number == "AB12CDE"
    assert observations[0].source_detail == "event_thumbnail.group.matchedName"
    assert observations[0].payload_path == "new_obj.metadata.detected_thumbnails[0].group.matchedName"
    assert observations[0].confidence == 87


def test_extracts_plate_from_raw_changed_data() -> None:
    message = SimpleNamespace(
        action=EnumValue("update"),
        changed_data={
            "modelKey": "event",
            "metadata": {
                "detectedThumbnails": [
                    {
                        "type": "vehicle",
                        "attributes": {
                            "matchedName": "sv11 abc",
                        },
                    }
                ]
            },
        },
        new_obj=None,
    )

    observations = extract_unifi_protect_lpr_observations(
        message,
        received_at=datetime(2026, 4, 28, 12, 0, 3, tzinfo=UTC),
    )

    assert len(observations) == 1
    assert observations[0].registration_number == "SV11ABC"
    assert observations[0].source_detail == "websocket.changed_data.matchedName"
    assert observations[0].payload_path == "changed_data.metadata.detectedThumbnails[0].attributes.matchedName"


def test_treats_top_k_decimal_confidence_strings_as_possible_fields() -> None:
    message = SimpleNamespace(
        action=EnumValue("update"),
        changed_data={
            "modelKey": "event",
            "metadata": {
                "detectedThumbnails": [
                    {
                        "type": "vehicle",
                        "attributes": {
                            "namesTopK": ["AGS7X", "0.872063", "A6S7X", "0.396491"],
                        },
                    }
                ]
            },
        },
        new_obj=None,
    )

    observations = extract_unifi_protect_lpr_observations(
        message,
        received_at=datetime(2026, 4, 28, 12, 0, 3, tzinfo=UTC),
    )

    confidence_rows = [row for row in observations if row.raw_value in {"0.872063", "0.396491"}]
    assert confidence_rows
    assert all(row.registration_number == "" for row in confidence_rows)
    assert all(row.candidate_kind == "possible_lpr_field" for row in confidence_rows)


def test_extracts_direct_license_plate_from_smart_detect_track() -> None:
    observations = extract_unifi_protect_track_observations(
        {
            "payload": [
                {
                    "timestamp": 1777394788086,
                    "licensePlate": "pe70 dhx",
                    "name": "PE70DHX",
                    "confidence": 92,
                },
                {
                    "timestamp": 1777394788308,
                    "licensePlate": "PE70DHX",
                    "name": "PE70DHX",
                    "confidence": 88,
                },
            ]
        },
        event=SimpleNamespace(
            id="event-1",
            camera_id="camera-1",
            camera=SimpleNamespace(display_name="Gate LPR"),
            smart_detect_types=[EnumValue("licensePlate"), EnumValue("vehicle")],
        ),
        received_at=datetime(2026, 4, 28, 12, 0, 3, tzinfo=UTC),
        probe_attempt=2,
    )

    assert len(observations) == 1
    assert observations[0].source == "uiprotect_track"
    assert observations[0].registration_number == "PE70DHX"
    assert observations[0].source_detail == "smart_detect_track.licensePlate.attempt_2"
    assert observations[0].payload_path == "smartDetectTrack.payload[0].licensePlate"
    assert observations[0].captured_at == "2026-04-28T16:46:28.086000+00:00"
    assert observations[0].confidence == 92


def test_ignores_smart_detect_track_name_without_license_plate_field() -> None:
    observations = extract_unifi_protect_track_observations(
        {
            "payload": [
                {
                    "timestamp": 1778862489856,
                    "name": "Steph",
                    "confidence": 73,
                },
            ]
        },
        event=SimpleNamespace(
            id="event-1",
            camera_id="camera-1",
            camera=SimpleNamespace(display_name="Gate"),
            smart_detect_types=[EnumValue("face"), EnumValue("vehicle")],
        ),
        received_at=datetime(2026, 5, 15, 16, 31, 46, tzinfo=UTC),
    )

    assert observations == []
