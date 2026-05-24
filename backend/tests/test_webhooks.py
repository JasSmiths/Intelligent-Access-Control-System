import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
import pytest
from starlette.requests import Request

from app import main as main_api
from app.api.v1 import webhooks


VALID_LPR_WEBHOOK_TOKEN = "test-lpr-webhook-token"
VALID_LPR_SOURCE_IP = "192.0.2.10"


def make_json_request(
    payload: dict,
    *,
    token: str | None = VALID_LPR_WEBHOOK_TOKEN,
    client_host: str = VALID_LPR_SOURCE_IP,
) -> Request:
    body = json.dumps(payload).encode()
    headers = [(b"content-type", b"application/json")]
    if token is not None:
        headers.append((b"x-iacs-lpr-token", token.encode()))

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/webhooks/ubiquiti/lpr",
            "query_string": b"",
            "headers": headers,
            "client": (client_host, 51234),
        },
        receive,
    )


def make_unreadable_lpr_request(
    *,
    token: str | None = VALID_LPR_WEBHOOK_TOKEN,
    client_host: str = VALID_LPR_SOURCE_IP,
) -> Request:
    headers = [(b"content-type", b"application/json")]
    if token is not None:
        headers.append((b"x-iacs-lpr-token", token.encode()))

    async def receive():
        raise AssertionError("LPR webhook body should not be read before authentication succeeds.")

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/webhooks/ubiquiti/lpr",
            "query_string": b"",
            "headers": headers,
            "client": (client_host, 51234),
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
    def __init__(self) -> None:
        self.calls: list[tuple[Any, datetime | None]] = []

    async def record_webhook_plate(self, read, *, received_at=None):
        self.calls.append((read, received_at))
        return None


class FakeUnifiPayloadRecorder:
    def __init__(self) -> None:
        self.calls: list[Any] = []

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
        self.enqueued: list[Any] = []

    async def enqueue_plate_read(self, read):
        self.enqueued.append(read)


@pytest.fixture
def webhook_runtime(monkeypatch):
    published = []
    timing_recorder = FakeLprTimingRecorder()
    visual_recorder = FakeUnifiPayloadRecorder()
    presence_tracker = FakeUnifiPayloadRecorder()

    async def runtime_config():
        return SimpleNamespace(
            lpr_allowed_smart_zones=["default"],
            lpr_webhook_token=VALID_LPR_WEBHOOK_TOKEN,
            lpr_webhook_allowed_source_ips=[VALID_LPR_SOURCE_IP],
        )

    async def publish(event_type, payload):
        published.append((event_type, payload))

    monkeypatch.setattr(webhooks, "get_runtime_config", runtime_config)
    monkeypatch.setattr(webhooks, "get_lpr_timing_recorder", lambda: timing_recorder)
    monkeypatch.setattr(webhooks, "get_unifi_protect_service", lambda: FakeUnifiProtectService())
    monkeypatch.setattr(webhooks, "get_vehicle_visual_detection_recorder", lambda: visual_recorder)
    monkeypatch.setattr(webhooks, "get_vehicle_presence_tracker", lambda: presence_tracker)
    monkeypatch.setattr(webhooks.event_bus, "publish", publish)
    monkeypatch.setattr(webhooks.telemetry, "record_span", lambda *_args, **_kwargs: None)

    return SimpleNamespace(
        presence_tracker=presence_tracker,
        published=published,
        timing_recorder=timing_recorder,
        visual_recorder=visual_recorder,
    )


@pytest.mark.asyncio
async def test_lpr_webhook_rejects_missing_token_before_reading_body(webhook_runtime) -> None:
    service = FakeAccessEventService()

    with pytest.raises(HTTPException) as exc:
        await webhooks.receive_ubiquiti_lpr(make_unreadable_lpr_request(token=None), service)

    assert exc.value.status_code == 401
    assert service.enqueued == []
    assert webhook_runtime.timing_recorder.calls == []


@pytest.mark.asyncio
async def test_lpr_webhook_rejects_wrong_token(webhook_runtime) -> None:
    service = FakeAccessEventService()

    with pytest.raises(HTTPException) as exc:
        await webhooks.receive_ubiquiti_lpr(
            make_json_request(alarm_payload("AGS7X", []), token="wrong-token"),
            service,
        )

    assert exc.value.status_code == 401
    assert service.enqueued == []
    assert webhook_runtime.timing_recorder.calls == []


@pytest.mark.asyncio
async def test_lpr_webhook_rejects_unconfigured_token_as_misconfigured(webhook_runtime, monkeypatch) -> None:
    service = FakeAccessEventService()

    async def runtime_config():
        return SimpleNamespace(
            lpr_allowed_smart_zones=["default"],
            lpr_webhook_token="",
            lpr_webhook_allowed_source_ips=[VALID_LPR_SOURCE_IP],
        )

    monkeypatch.setattr(webhooks, "get_runtime_config", runtime_config)

    with pytest.raises(HTTPException) as exc:
        await webhooks.receive_ubiquiti_lpr(make_unreadable_lpr_request(), service)

    assert exc.value.status_code == 503
    assert service.enqueued == []
    assert webhook_runtime.timing_recorder.calls == []


@pytest.mark.asyncio
async def test_lpr_webhook_rejects_disallowed_source_ip(webhook_runtime) -> None:
    service = FakeAccessEventService()

    with pytest.raises(HTTPException) as exc:
        await webhooks.receive_ubiquiti_lpr(
            make_json_request(alarm_payload("AGS7X", []), client_host="198.51.100.7"),
            service,
        )

    assert exc.value.status_code == 403
    assert service.enqueued == []
    assert webhook_runtime.timing_recorder.calls == []


@pytest.mark.asyncio
async def test_lpr_webhook_fails_closed_when_allowlist_has_no_valid_entries(webhook_runtime, monkeypatch) -> None:
    service = FakeAccessEventService()

    async def runtime_config():
        return SimpleNamespace(
            lpr_allowed_smart_zones=["default"],
            lpr_webhook_token=VALID_LPR_WEBHOOK_TOKEN,
            lpr_webhook_allowed_source_ips=["not-an-ip"],
        )

    monkeypatch.setattr(webhooks, "get_runtime_config", runtime_config)

    with pytest.raises(HTTPException) as exc:
        await webhooks.receive_ubiquiti_lpr(make_json_request(alarm_payload("AGS7X", [])), service)

    assert exc.value.status_code == 503
    assert service.enqueued == []
    assert webhook_runtime.timing_recorder.calls == []


@pytest.mark.asyncio
async def test_maintenance_mode_lpr_webhook_still_requires_authentication(monkeypatch) -> None:
    async def active_maintenance():
        return True

    async def runtime_config():
        return SimpleNamespace(
            lpr_webhook_token=VALID_LPR_WEBHOOK_TOKEN,
            lpr_webhook_allowed_source_ips=[VALID_LPR_SOURCE_IP],
        )

    async def call_next(_request):
        raise AssertionError("Unauthorized maintenance-mode LPR webhook should not continue.")

    monkeypatch.setattr(main_api, "is_maintenance_mode_active", active_maintenance)
    monkeypatch.setattr(main_api, "get_runtime_config", runtime_config)

    response = await main_api.maintenance_webhook_guard(
        make_unreadable_lpr_request(token=None),
        call_next,
    )

    assert response.status_code == 401


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
async def test_lpr_webhook_persists_ingest_metadata_and_timing_received_at(webhook_runtime, monkeypatch) -> None:
    service = FakeAccessEventService()
    webhook_received_at = datetime(2026, 5, 22, 12, 0, 1, 234000, tzinfo=UTC)
    monkeypatch.setattr(webhooks, "utc_now", lambda: webhook_received_at)
    monkeypatch.setattr(webhooks, "current_request_id", lambda: "req-test")
    monkeypatch.setattr(webhooks, "current_trace_id", lambda: "trace-test")

    result = await webhooks.receive_ubiquiti_lpr(
        make_json_request(
            {
                "registrationNumber": "ags7x",
                "confidence": 99,
                "capturedAt": "2026-05-22T12:00:00+00:00",
            }
        ),
        service,
    )

    assert result == {"status": "accepted", "plate": "AGS7X"}
    assert len(service.enqueued) == 1
    ingest = service.enqueued[0].raw_payload[webhooks.INGEST_METADATA_PAYLOAD_KEY]
    assert ingest == {
        "version": 1,
        "webhook_received_at": "2026-05-22T12:00:01.234000+00:00",
        "request_id": "req-test",
        "webhook_trace_id": "trace-test",
        "captured_to_webhook_ms": 1234.0,
        "path": "/api/v1/webhooks/ubiquiti/lpr",
        "payload_shape_version": 1,
        "payload_shape": {
            "registrationNumber": "str",
            "confidence": "int",
            "capturedAt": "str",
        },
    }
    assert webhook_runtime.timing_recorder.calls[0][1] is webhook_received_at
    assert (
        webhook_runtime.timing_recorder.calls[0][0].raw_payload[webhooks.INGEST_METADATA_PAYLOAD_KEY]
        == ingest
    )


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
