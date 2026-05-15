import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.api.v1 import webhooks


def make_json_request(payload: dict) -> Request:
    body = json.dumps(payload).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/webhooks/ubiquiti/lpr",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


def alarm_payload(plate: str, zones: list[int] | list[str]) -> dict:
    return {
        "alarm": {
            "name": "Home Assistant LPR",
            "sources": [{"type": "include", "device": "942A6FD09D64"}],
            "triggers": [
                {
                    "key": "license_plate_unknown",
                    "group": {"name": plate},
                    "value": plate,
                    "zones": {"line": [], "zone": zones, "loiter": []},
                    "device": "942A6FD09D64",
                    "eventId": "event-1",
                    "timestamp": 1777813142519,
                }
            ],
            "eventPath": "/protect/events/event/event-1",
        },
        "timestamp": 1777813143285,
    }


def multi_plate_alarm_payload(first_plate: str, second_plate: str, zones: list[int] | list[str]) -> dict:
    payload = alarm_payload(first_plate, zones)
    payload["alarm"]["triggers"].append(
        {
            "key": "license_plate_unknown",
            "group": {"name": second_plate},
            "value": second_plate,
            "zones": {"line": [], "zone": zones, "loiter": []},
            "device": "942A6FD09D64",
            "eventId": "event-1",
            "timestamp": 1777813142520,
        }
    )
    return payload


class FakeLprTimingRecorder:
    async def record_webhook_plate(self, _read):
        return None


class FakeUnifiPayloadRecorder:
    def __init__(self) -> None:
        self.calls = []

    async def record_unifi_payload(self, payload, *, registration_number):
        self.calls.append((payload, registration_number))


class FailingUnifiPayloadRecorder:
    def __init__(self, message: str) -> None:
        self.message = message

    async def record_unifi_payload(self, _payload, *, registration_number):
        raise RuntimeError(f"{self.message} for {registration_number}")


class FakeUnifiProtectService:
    async def resolve_lpr_smart_zone_names(self, zones, *, camera_identifier=None):
        return {
            "camera_identifier": camera_identifier,
            "raw_smart_zones": [str(zone) for zone in zones],
            "smart_zones": ["default" for _zone in zones],
        }


class FakeAccessEventService:
    def __init__(self) -> None:
        self.enqueued = []

    async def enqueue_plate_read(self, read):
        self.enqueued.append(read)


@pytest.fixture
def webhook_runtime(monkeypatch):
    published = []
    visual_recorder = FakeUnifiPayloadRecorder()
    presence_tracker = FakeUnifiPayloadRecorder()

    async def runtime_config():
        return SimpleNamespace(lpr_allowed_smart_zones=["default"])

    async def publish(event_type, payload):
        published.append((event_type, payload))

    monkeypatch.setattr(webhooks, "get_runtime_config", runtime_config)
    monkeypatch.setattr(webhooks, "get_lpr_timing_recorder", lambda: FakeLprTimingRecorder())
    monkeypatch.setattr(webhooks, "get_unifi_protect_service", lambda: FakeUnifiProtectService())
    monkeypatch.setattr(webhooks, "get_vehicle_visual_detection_recorder", lambda: visual_recorder)
    monkeypatch.setattr(webhooks, "get_vehicle_presence_tracker", lambda: presence_tracker)
    monkeypatch.setattr(webhooks.event_bus, "publish", publish)
    monkeypatch.setattr(webhooks.telemetry, "record_span", lambda *_args, **_kwargs: None)

    return SimpleNamespace(
        presence_tracker=presence_tracker,
        published=published,
        visual_recorder=visual_recorder,
    )


@pytest.mark.asyncio
async def test_lpr_webhook_with_empty_smart_zone_evidence_is_accepted(webhook_runtime) -> None:
    service = FakeAccessEventService()

    result = await webhooks.receive_ubiquiti_lpr(make_json_request(alarm_payload("AGS7X", [])), service)

    assert result == {"status": "accepted", "plate": "AGS7X"}
    assert len(service.enqueued) == 1
    smart_zone_evidence = service.enqueued[0].raw_payload[webhooks.SMART_ZONE_EVIDENCE_PAYLOAD_KEY]
    assert smart_zone_evidence["smart_zones"] == []
    assert smart_zone_evidence["smart_zone_evidence"]["present"] is True
    assert smart_zone_evidence["smart_zone_evidence"]["explicit_empty"] is True
    assert webhook_runtime.visual_recorder.calls
    assert webhook_runtime.presence_tracker.calls
    assert webhook_runtime.published == []


@pytest.mark.asyncio
async def test_lpr_webhook_preserves_secondary_plate_candidate(webhook_runtime) -> None:
    service = FakeAccessEventService()

    result = await webhooks.receive_ubiquiti_lpr(
        make_json_request(multi_plate_alarm_payload("DX66TUA", "MD25VNO", [2])),
        service,
    )

    assert result == {"status": "accepted", "plate": "DX66TUA"}
    assert len(service.enqueued) == 1
    assert service.enqueued[0].candidate_registration_numbers == ("DX66TUA", "MD25VNO")


@pytest.mark.asyncio
async def test_lpr_webhook_still_enqueues_when_diagnostic_recorders_fail(webhook_runtime, monkeypatch) -> None:
    service = FakeAccessEventService()
    monkeypatch.setattr(
        webhooks,
        "get_vehicle_visual_detection_recorder",
        lambda: FailingUnifiPayloadRecorder("visual recorder unavailable"),
    )
    monkeypatch.setattr(
        webhooks,
        "get_vehicle_presence_tracker",
        lambda: FailingUnifiPayloadRecorder("presence tracker unavailable"),
    )

    result = await webhooks.receive_ubiquiti_lpr(make_json_request(alarm_payload("AGS7X", [])), service)

    assert result == {"status": "accepted", "plate": "AGS7X"}
    assert len(service.enqueued) == 1
    assert webhook_runtime.published == [
        (
            "plate_read.diagnostics_failed",
            {
                "registration_number": "AGS7X",
                "source": "ubiquiti",
                "category": "lpr_telemetry",
                "level": "warning",
                "outcome": "failed",
                "diagnostics": [
                    {
                        "diagnostic": "vehicle_visual_detection",
                        "error": "RuntimeError: visual recorder unavailable for AGS7X",
                    },
                    {
                        "diagnostic": "vehicle_presence",
                        "error": "RuntimeError: presence tracker unavailable for AGS7X",
                    },
                ],
            },
        )
    ]


@pytest.mark.asyncio
async def test_lpr_webhook_without_smart_zone_value_is_still_accepted(webhook_runtime) -> None:
    service = FakeAccessEventService()

    payload = {"registrationNumber": "AGS7X", "confidence": 99}
    result = await webhooks.receive_ubiquiti_lpr(make_json_request(payload), service)

    assert result == {"status": "accepted", "plate": "AGS7X"}
    assert len(service.enqueued) == 1
    smart_zone_evidence = service.enqueued[0].raw_payload[webhooks.SMART_ZONE_EVIDENCE_PAYLOAD_KEY]
    assert smart_zone_evidence["smart_zones"] == []
    assert smart_zone_evidence["smart_zone_evidence"]["present"] is False
    assert smart_zone_evidence["smart_zone_evidence"]["explicit_empty"] is False
    assert webhook_runtime.visual_recorder.calls
    assert webhook_runtime.presence_tracker.calls
