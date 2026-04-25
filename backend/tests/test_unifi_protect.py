from datetime import UTC, datetime
from types import SimpleNamespace

from app.ai.providers import _looks_like_ollama_vision_model
from app.modules.unifi_protect.client import serialize_unifi_camera, serialize_unifi_event


class EnumValue:
    def __init__(self, value: str) -> None:
        self.value = value


def test_camera_serialization_exposes_metadata_not_stream_urls() -> None:
    camera = SimpleNamespace(
        id="cam-1",
        display_name="Driveway",
        type=EnumValue("camera"),
        state=EnumValue("CONNECTED"),
        is_adopted=True,
        is_recording=True,
        is_recording_enabled=True,
        is_video_ready=True,
        is_motion_detected=False,
        is_smart_detected=True,
        last_motion=datetime(2026, 4, 25, tzinfo=UTC),
        last_motion_event_id="event-1",
        last_smart_detect=None,
        last_smart_detect_event_id=None,
        last_smart_audio_detect=None,
        last_smart_audio_detect_event_id=None,
        is_person_currently_detected=True,
        channels=[
            SimpleNamespace(
                id="0",
                name="High",
                width=1920,
                height=1080,
                fps=30,
                bitrate=6000,
                is_rtsp_enabled=True,
                is_package=False,
                rtsp_url="rtsp://secret",
            )
        ],
        feature_flags=SimpleNamespace(
            has_smart_detect=True,
            has_package_camera=False,
            smart_detect_types=[EnumValue("person")],
            smart_detect_audio_types=[],
        ),
        has_mic=True,
    )

    payload = serialize_unifi_camera(camera)

    assert payload["id"] == "cam-1"
    assert payload["detections"]["active"] == ["person"]
    assert payload["channels"][0]["is_rtsp_enabled"] is True
    assert "rtsp_url" not in payload["channels"][0]


def test_event_serialization_includes_proxy_urls() -> None:
    event = SimpleNamespace(
        id="event-1",
        type=EnumValue("smartDetectZone"),
        camera_id="cam-1",
        camera=SimpleNamespace(display_name="Driveway"),
        start=datetime(2026, 4, 25, tzinfo=UTC),
        end=datetime(2026, 4, 25, 0, 0, 10, tzinfo=UTC),
        score=92,
        smart_detect_types=[EnumValue("vehicle")],
        metadata=None,
    )

    payload = serialize_unifi_event(event)

    assert payload["thumbnail_url"].endswith("/events/event-1/thumbnail")
    assert payload["video_url"].endswith("/events/event-1/video")
    assert payload["smart_detect_types"] == ["vehicle"]


def test_ollama_vision_model_detection() -> None:
    assert _looks_like_ollama_vision_model("llama3.2-vision")
    assert _looks_like_ollama_vision_model("llava:latest")
    assert not _looks_like_ollama_vision_model("llama3")

