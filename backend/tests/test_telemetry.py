from datetime import UTC, datetime, timedelta

from app.ai.providers import ToolCall
from app.ai.tools import set_chat_tool_context
from app.models import AuditLog, TelemetrySpan, TelemetryTrace
from app.services.chat import ChatService
from app.services.telemetry import (
    TELEMETRY_CATEGORY_ALFRED,
    TELEMETRY_CATEGORY_INTEGRATIONS,
    TELEMETRY_CATEGORY_LPR,
    TelemetryService,
    audit_diff,
    sanitize_payload,
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
            "profile_photo_data_url": "data:image/png;base64,abcdef",
            "nested": {"api_key": "123", "safe": "ok"},
        }
    )

    assert payload["authorization"] == "[redacted]"
    assert payload["home_assistant_token"] == "[redacted]"
    assert str(payload["profile_photo_data_url"]).startswith("[media redacted")
    assert payload["nested"]["api_key"] == "[redacted]"

    diff = audit_diff({"schedule": "Mon-Fri", "name": "Steph"}, {"schedule": "24/7", "name": "Steph"})
    assert diff == {"old": {"schedule": "Mon-Fri"}, "new": {"schedule": "24/7"}}


def test_lpr_trace_captures_ordered_spans(monkeypatch) -> None:
    service = TelemetryService()
    captured_traces = []
    captured_spans = []
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
