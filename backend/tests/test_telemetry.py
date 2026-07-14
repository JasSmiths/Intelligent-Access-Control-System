from datetime import UTC, datetime, timedelta
from typing import Any
import uuid

from app.api.v1 import telemetry as telemetry_api
from app.ai.providers import ToolCall
from app.ai.tools import set_chat_tool_context
from app.models import AccessEvent, AuditLog, GateCommandRecord, MovementSagaRecord, TelemetrySpan, TelemetryTrace
from app.models.enums import (
    AccessDecision,
    AccessDirection,
    GateCommandState,
    MovementSagaState,
    TimingClassification,
    UserRole,
)
from app.services.chat import ChatService
from app.services.telemetry import (
    TELEMETRY_CATEGORY_ALFRED,
    TELEMETRY_CATEGORY_INTEGRATIONS,
    TELEMETRY_CATEGORY_LPR,
    TelemetryService,
    audit_diff,
    sanitize_payload,
    sanitize_query_string,
    span_id,
    trace_id,
)


def test_telemetry_identifiers_are_opentelemetry_compatible() -> None:
    assert len(trace_id()) == 32
    assert len(span_id()) == 16


def test_telemetry_models_accept_relational_payloads() -> None:
    trace = TelemetryTrace(
        trace_id="0" * 32,
        name="Vehicle Arrival",
        category="lpr_telemetry",
        status="ok",
        level="info",
        started_at=datetime(2026, 4, 27, 12, 0, tzinfo=UTC),
        ended_at=datetime(2026, 4, 27, 12, 0, 2, tzinfo=UTC),
        duration_ms=2000,
        actor="System",
        source="ubiquiti",
        registration_number="AB12CDE",
        summary="Entry granted",
        context={"decision": "granted"},
    )
    span = TelemetrySpan(
        span_id="1" * 16,
        trace_id=trace.trace_id,
        name="Debounce & Confidence Aggregation",
        category="lpr_telemetry",
        step_order=1,
        started_at=trace.started_at,
        ended_at=trace.started_at + timedelta(milliseconds=1100),
        duration_ms=1100,
        status="ok",
        attributes={"candidate_count": 3},
    )
    audit = AuditLog(
        category="entity_management",
        action="vehicle.update",
        actor="Owner",
        target_entity="Vehicle",
        target_id="vehicle-1",
        diff={"old": {"schedule": "Mon-Fri"}, "new": {"schedule": "24/7"}},
        metadata_={"request_id": "req_1"},
    )

    assert trace.registration_number == "AB12CDE"
    assert span.trace_id == trace.trace_id
    assert audit.metadata_["request_id"] == "req_1"


def test_redaction_and_audit_diff_hide_secrets_and_media() -> None:
    payload = sanitize_payload(
        {
            "authorization": "Bearer secret",
            "home_assistant_token": "secret",
            "webhook_key": "hook-secret",
            "mount_options": "username=iacs,password=secret,vers=3.0,rw",
            "assistant_text": "Snapshot at /api/v1/ai/chat/files/abcDEF_123",
            "profile_photo_data_url": "data:image/png;base64,abcdef",
            "configuration_snapshot": {"schedule": "06:00-22:30"},
            "nested": {"api_key": "123", "safe": "ok"},
        }
    )

    assert payload["authorization"] == "[redacted]"
    assert payload["home_assistant_token"] == "[redacted]"
    assert payload["webhook_key"] == "[redacted]"
    assert payload["mount_options"] == "[redacted]"
    assert payload["assistant_text"] == "Snapshot at /api/v1/ai/chat/files/[redacted]"
    assert str(payload["profile_photo_data_url"]).startswith("[media redacted")
    assert payload["configuration_snapshot"] == {"schedule": "06:00-22:30"}
    assert payload["nested"]["api_key"] == "[redacted]"

    diff = audit_diff({"schedule": "Mon-Fri", "name": "Steph"}, {"schedule": "24/7", "name": "Steph"})
    assert diff == {"old": {"schedule": "Mon-Fri"}, "new": {"schedule": "24/7"}}


def test_query_string_redaction_hides_secret_values() -> None:
    sanitized = sanitize_query_string(
        "hub.mode=subscribe&hub.verify_token=secret-token&hub.challenge=123&confirmation_token=abc"
    )

    assert "secret-token" not in sanitized
    assert "abc" not in sanitized
    assert "hub.verify_token=%5Bredacted%5D" in sanitized
    assert "confirmation_token=%5Bredacted%5D" in sanitized
    assert "hub.challenge=123" in sanitized


def test_telemetry_summary_helpers_count_rows_and_storage() -> None:
    counts = telemetry_api._count_rows_to_map([("info", 2), ("error", 1), (None, 3)])
    storage = telemetry_api._telemetry_storage_payload(
        database_size_bytes=128,
        log_file_size_bytes=64,
        artifact_size_bytes=32,
        file_count=4,
    )

    assert counts == {"info": 2, "error": 1, "unknown": 3}
    assert storage["total_size_bytes"] == 224
    assert storage["file_count"] == 4
    assert storage["updated_at"]


class FakeScalarResult:
    def __init__(self, rows) -> None:
        self._rows = rows

    def all(self):
        return self._rows


class CapturingAuditSession:
    def __init__(self) -> None:
        self.statement = None

    async def scalars(self, statement):
        self.statement = statement
        return FakeScalarResult([])


class WaterfallSession:
    def __init__(self, *, trace, access_event, spans, movement_saga) -> None:
        self.trace = trace
        self.access_event = access_event
        self.spans = spans
        self.movement_saga = movement_saga

    async def get(self, model, key):
        if model is TelemetryTrace and str(key) == self.trace.trace_id:
            return self.trace
        if model is AccessEvent and str(key) == str(self.access_event.id):
            return self.access_event
        return None

    async def scalar(self, _statement):
        return self.movement_saga

    async def scalars(self, _statement):
        return FakeScalarResult(self.spans)


def make_admin_user():
    now = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)
    return type(
        "TelemetryTestUser",
        (),
        {
            "id": uuid.uuid4(),
            "username": "admin",
            "full_name": "Admin User",
            "role": UserRole.ADMIN,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
    )()


async def test_audit_log_endpoint_applies_level_and_outcome_filters() -> None:
    session = CapturingAuditSession()

    response = await telemetry_api.list_audit_logs(
        level="warning",
        outcome="failed",
        from_at=None,
        to_at=None,
        limit=50,
        _=make_admin_user(),
        session=session,
    )

    assert session.statement is not None
    compiled = str(session.statement.compile(compile_kwargs={"literal_binds": True}))
    assert response == {"items": [], "next_cursor": None}
    assert "audit_logs.level = 'warning'" in compiled
    assert "audit_logs.outcome = 'failed'" in compiled


async def test_lpr_waterfall_can_be_loaded_by_access_event_id(monkeypatch) -> None:
    captured_at = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    webhook_received_at = captured_at + timedelta(milliseconds=275)
    finalized_at = captured_at + timedelta(seconds=2)
    created_at = finalized_at + timedelta(milliseconds=250)
    trace_id_value = "a" * 32
    event_id = uuid.uuid4()
    movement_saga_id = uuid.uuid4()
    command_id = uuid.uuid4()

    trace = TelemetryTrace(
        trace_id=trace_id_value,
        name="Plate Detection - AGS7X",
        category=TELEMETRY_CATEGORY_LPR,
        status="ok",
        level="info",
        started_at=captured_at,
        ended_at=created_at,
        duration_ms=2250,
        source="test",
        registration_number="AGS7X",
        access_event_id=event_id,
        summary="Granted entry for plate AGS7X",
        context={
            "first_seen": captured_at.isoformat(),
            "finalize_started_at": finalized_at.isoformat(),
            "webhook_received_at": webhook_received_at.isoformat(),
            "captured_to_webhook_ms": 275.0,
            "webhook_trace": {
                "source": "test",
                "registration_number": "AGS7X",
                "captured_at": captured_at.isoformat(),
                "received_at": webhook_received_at.isoformat(),
                "captured_to_webhook_ms": 275.0,
            },
        },
    )
    spans = [
        TelemetrySpan(
            span_id="1" * 16,
            trace_id=trace_id_value,
            name="Camera Capture to Webhook Receipt",
            category=TELEMETRY_CATEGORY_LPR,
            step_order=1,
            started_at=captured_at,
            ended_at=webhook_received_at,
            duration_ms=275,
            status="ok",
        ),
        TelemetrySpan(
            span_id="2" * 16,
            trace_id=trace_id_value,
            name="Webhook Receipt to Debounce Finalization",
            category=TELEMETRY_CATEGORY_LPR,
            step_order=2,
            started_at=webhook_received_at,
            ended_at=finalized_at,
            duration_ms=1725,
            status="ok",
        ),
    ]
    access_event = AccessEvent(
        id=event_id,
        registration_number="AGS7X",
        direction=AccessDirection.ENTRY,
        decision=AccessDecision.GRANTED,
        confidence=0.91,
        source="test",
        occurred_at=captured_at,
        timing_classification=TimingClassification.NORMAL,
        raw_payload={
            "telemetry": {"trace_id": trace_id_value},
            "webhook_trace": {
                "source": "test",
                "registration_number": "AGS7X",
                "captured_at": captured_at.isoformat(),
                "received_at": webhook_received_at.isoformat(),
                "captured_to_webhook_ms": 275.0,
            },
            "debounce": {
                "first_seen": captured_at.isoformat(),
                "updated_at": captured_at.isoformat(),
                "finalize_started_at": finalized_at.isoformat(),
                "candidates": [],
            },
        },
        created_at=created_at,
        updated_at=created_at,
    )
    command = GateCommandRecord(
        id=command_id,
        idempotency_key="gate-command:open:default:event:test",
        movement_saga_id=movement_saga_id,
        access_event_id=event_id,
        state=GateCommandState.ACCEPTED,
        action="open",
        source="lpr_access",
        gate_key="default",
        controller="access_device",
        reason="automatic_lpr_grant",
        registration_number="AGS7X",
        bypass_schedule=False,
        accepted=True,
        mechanically_confirmed=True,
        requires_reconciliation=False,
        command_metadata={"provider": "home_assistant"},
        created_at=created_at,
        updated_at=created_at,
    )
    movement_saga = MovementSagaRecord(
        id=movement_saga_id,
        idempotency_key="movement:test:AGS7X",
        source="test",
        state=MovementSagaState.COMPLETED,
        access_event_id=event_id,
        registration_number="AGS7X",
        direction=AccessDirection.ENTRY,
        decision=AccessDecision.GRANTED,
        occurred_at=captured_at,
        gate_command_required=True,
        presence_committed=True,
        reconciliation_required=False,
        intent_payload={"source": "test"},
        decision_payload={"direction": "entry"},
        state_history=[],
        gate_commands=[command],
        created_at=created_at,
        updated_at=created_at,
    )
    session = WaterfallSession(
        trace=trace,
        access_event=access_event,
        spans=spans,
        movement_saga=movement_saga,
    )

    async def flush_noop():
        return None

    class FakeTimingRecorder:
        async def recent(self, *, limit):
            assert limit == 200
            return [
                {
                    "source": "webhook",
                    "source_detail": "ubiquiti_lpr_webhook",
                    "registration_number": "AGS7X",
                    "received_at": webhook_received_at.isoformat(),
                },
                {
                    "source": "webhook",
                    "source_detail": "ubiquiti_lpr_webhook",
                    "registration_number": "OTHER",
                    "received_at": webhook_received_at.isoformat(),
                },
            ]

    monkeypatch.setattr(telemetry_api.telemetry, "flush", flush_noop)
    monkeypatch.setattr(telemetry_api, "get_lpr_timing_recorder", lambda: FakeTimingRecorder())

    response = await telemetry_api.get_lpr_waterfall(
        str(event_id),
        _=make_admin_user(),
        session=session,
    )

    assert response["trace"]["trace_id"] == trace_id_value
    assert response["access_event"]["id"] == str(event_id)
    assert response["movement_saga"]["id"] == str(movement_saga_id)
    assert response["gate_commands"][0]["id"] == str(command_id)
    assert response["webhook_trace"]["captured_to_webhook_ms"] == 275.0
    assert response["durable_latency"]["captured_to_webhook_ms"] == 275.0
    assert response["durable_latency"]["webhook_to_debounce_finalize_ms"] == 1725.0
    assert response["durable_latency"]["captured_to_access_event_created_ms"] == 2250.0
    assert [span["name"] for span in response["spans"]] == [
        "Camera Capture to Webhook Receipt",
        "Webhook Receipt to Debounce Finalization",
    ]
    assert response["recent_lpr_timing_observations"] == [
        {
            "source": "webhook",
            "source_detail": "ubiquiti_lpr_webhook",
            "registration_number": "AGS7X",
            "received_at": webhook_received_at.isoformat(),
        }
    ]

    query_response = await telemetry_api.get_lpr_waterfall_by_access_event(
        access_event_id=event_id,
        _=make_admin_user(),
        session=session,
    )
    assert query_response["trace"]["trace_id"] == trace_id_value
    assert query_response["access_event"]["id"] == str(event_id)


def test_lpr_trace_captures_ordered_spans(monkeypatch) -> None:
    service = TelemetryService()
    captured_traces: list[Any] = []
    captured_spans: list[Any] = []
    monkeypatch.setattr(service, "enqueue_trace", captured_traces.append)
    monkeypatch.setattr(service, "enqueue_span", captured_spans.append)

    started = datetime(2026, 4, 27, 12, 0, tzinfo=UTC)
    trace = service.start_trace(
        "Plate Detection - AB12CDE",
        category=TELEMETRY_CATEGORY_LPR,
        source="ubiquiti",
        registration_number="AB12CDE",
        started_at=started,
    )
    trace.record_span(
        "Debounce & Confidence Aggregation",
        started_at=started,
        ended_at=started + timedelta(milliseconds=1100),
        attributes={"candidate_count": 3},
    )
    schedule = trace.start_span("Schedule & Access Rule Evaluation")
    schedule.finish(output_payload={"allowed": True, "schedule_name": "24/7"})
    gate = trace.start_span("Home Assistant Gate Open Command Sent", category="integrations")
    gate.finish(output_payload={"accepted": True, "state": "opening"})
    trace.finish(summary="Entry granted", context={"decision": "granted"})

    assert captured_traces[0].summary == "Entry granted"
    assert [span.name for span in captured_spans] == [
        "Debounce & Confidence Aggregation",
        "Schedule & Access Rule Evaluation",
        "Home Assistant Gate Open Command Sent",
    ]
    assert captured_spans[0].duration_ms == 1100


def test_alfred_tool_audit_includes_provider_model_tool_and_outcome(monkeypatch) -> None:
    captured = []
    monkeypatch.setattr("app.services.chat.emit_audit_log", lambda **kwargs: captured.append(kwargs))
    token = set_chat_tool_context(
        {
            "user_id": "00000000-0000-0000-0000-000000000001",
            "session_id": "session-1",
            "provider": "openai",
            "model": "gpt-4o",
            "trigger": "user_requested",
        }
    )
    try:
        ChatService()._audit_agent_tool_call(
            ToolCall("call_1", "open_device", {"target": "main gate", "confirm": True}),
            {"opened": True, "accepted": True, "state": "opening"},
        )
        ChatService()._audit_agent_tool_call(
            ToolCall("call_2", "lookup_dvla_vehicle", {"registration_number": "AB12CDE"}),
            {"registration_number": "AB12CDE", "display_vehicle": "2026 Tesla Model Y"},
        )
    finally:
        set_chat_tool_context({}, token=token)

    assert len(captured) == 3
    row = captured[0]
    assert row["category"] == TELEMETRY_CATEGORY_ALFRED
    assert row["actor"] == "Alfred_AI"
    assert row["metadata"]["provider"] == "openai"
    assert row["metadata"]["model"] == "gpt-4o"
    assert row["metadata"]["tool"] == "open_device"
    assert row["metadata"]["state_changing"] is True
    assert row["outcome"] == "success"

    lookup_row = captured[1]
    assert lookup_row["metadata"]["tool"] == "lookup_dvla_vehicle"
    assert lookup_row["metadata"]["arguments"]["registration_number"] == "AB12CDE"
    assert lookup_row["metadata"]["state_changing"] is False
    assert lookup_row["outcome"] == "success"

    integration_row = captured[2]
    assert integration_row["category"] == TELEMETRY_CATEGORY_INTEGRATIONS
    assert integration_row["action"] == "dvla.lookup"
    assert integration_row["actor"] == "Alfred_AI"
    assert integration_row["target_entity"] == "DVLA"
    assert integration_row["target_id"] == "AB12CDE"
    assert integration_row["metadata"]["source"] == "alfred"
    assert integration_row["metadata"]["tool"] == "lookup_dvla_vehicle"
