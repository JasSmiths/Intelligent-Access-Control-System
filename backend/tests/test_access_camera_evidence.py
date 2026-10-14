"""DB-free camera handoff contracts: real resolver/FSM, inert provider and trace sinks."""
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models import Person, Vehicle
from app.models.enums import AccessDirection, PresenceState
from app.modules.lpr.base import PlateRead
from app.services.access import evidence as owner
from app.services.access.evidence import AccessEvidenceResolver, PreparedCameraEvidence
from app.services.access.reads import GATE_OBSERVATION_PAYLOAD_KEY
from app.services.schedules import ScheduleEvaluation
from app.services.telemetry import CURRENT_PARENT_SPAN_ID, ActiveTrace

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


def read():
    return PlateRead("SYN123", 0.99, "synthetic", NOW, {
        "cameraId": "synthetic-camera", "deviceId": "synthetic-device", "eventId": "synthetic-event",
        GATE_OBSERVATION_PAYLOAD_KEY: {"state": "closed", "observed_at": NOW.isoformat()},
    })


def runtime():
    return SimpleNamespace(site_timezone="UTC", schedule_default_policy="allow", openai_model="synthetic",
                           unifi_protect_snapshot_width=640, unifi_protect_snapshot_height=360,
                           missed_exit_recovery_enabled=False)


class ProbeSession:
    def __init__(self):
        self.entered = True
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *args):
        self.exited = True

    async def close(self):
        self.exited = True

    async def get(self, model, identity):
        return SimpleNamespace(state=PresenceState.PRESENT)

    async def scalars(self, statement):
        return SimpleNamespace(all=list)

    async def execute(self, statement):
        return SimpleNamespace(first=lambda: SimpleNamespace(id=uuid.UUID(int=1),
            missed_exit_recovery_enabled=False, state=PresenceState.PRESENT))


@pytest.fixture
def trace():
    spans = []
    value = ActiveTrace(service=SimpleNamespace(enqueue_span=spans.append, enqueue_trace=lambda row: None),
                        name="Synthetic camera boundary", category="lpr")
    value.recorded_spans = spans
    try:
        yield value
    finally:
        value.finish()


def resolver_with_inert_domain(monkeypatch):
    person = Person(id=uuid.uuid4(), display_name="Synthetic Person", is_active=True)
    vehicle = Vehicle(id=uuid.uuid4(), registration_number="SYN123", person_id=person.id, owner=person, is_active=True)
    resolver = AccessEvidenceResolver(runtime())
    monkeypatch.setattr(resolver, "_lookup_active_vehicle", AsyncMock(return_value=vehicle))
    monkeypatch.setattr(resolver, "_schedule_evaluation_for_detection", AsyncMock(return_value=ScheduleEvaluation(True, "synthetic", reason="Synthetic permission")))
    return resolver, person


@pytest.mark.asyncio
async def test_probe_exits_session_before_snapshot_artifact_and_llm_and_restores_parent_span(monkeypatch, trace):
    resolver, person = resolver_with_inert_domain(monkeypatch)
    session = ProbeSession()
    monkeypatch.setattr(owner, "AsyncSessionLocal", lambda: session)
    parent = trace.start_span("Surrounding work")
    calls = []

    async def snapshot(*args, **kwargs):
        assert session.entered and session.exited
        calls.append("snapshot")
        return SimpleNamespace(content=b"inert synthetic bytes", content_type="image/jpeg")

    async def artifact(*args, **kwargs):
        assert session.exited
        calls.append("artifact")
        return {"id": "synthetic-artifact"}

    async def analyze(*args, **kwargs):
        assert session.exited
        calls.append("analysis")
        return SimpleNamespace(text='{"direction":"exit","confidence":0.91,"reason":"Synthetic"}')

    monkeypatch.setattr(owner, "get_unifi_protect_service", lambda: SimpleNamespace(snapshot=snapshot))
    monkeypatch.setattr(owner.telemetry, "store_artifact", artifact)
    monkeypatch.setattr(owner, "analyze_image_with_provider", analyze)
    monkeypatch.setattr(owner.event_bus, "publish", AsyncMock())
    observation = read()
    try:
        prepared = await resolver.prepare_camera_evidence(observation, observation, None, trace)
        assert prepared == PreparedCameraEvidence.for_read(person.id, observation, prepared.decision)
        assert prepared.decision["direction"] == "exit"
        assert calls == ["snapshot", "artifact", "analysis"]
        assert CURRENT_PARENT_SPAN_ID.get() == parent.span_id
        direction_spans = [span for span in trace.recorded_spans if span.name == "Direction Classification"]
        assert len(direction_spans) == 1 and direction_spans[0].ended_at is not None
    finally:
        parent.finish()


@pytest.mark.asyncio
async def test_unexpected_direction_failure_terminalizes_span_and_propagates(monkeypatch, trace):
    resolver, _person = resolver_with_inert_domain(monkeypatch)
    monkeypatch.setattr(resolver, "_resolve_direction", AsyncMock(side_effect=RuntimeError("Synthetic direction failure")))
    before = CURRENT_PARENT_SPAN_ID.get()
    with pytest.raises(RuntimeError, match="Synthetic direction failure"):
        await resolver.resolve(ProbeSession(), read(), read(), None, trace)
    assert CURRENT_PARENT_SPAN_ID.get() == before
    assert any(span.name == "Direction Classification" and span.status == "error" for span in trace.recorded_spans)


@pytest.mark.asyncio
async def test_final_resolution_without_prepared_evidence_never_acquires_camera_io(monkeypatch, trace):
    resolver, _person = resolver_with_inert_domain(monkeypatch)
    forbidden = AsyncMock(side_effect=AssertionError("Final transaction reached optional vendor"))
    monkeypatch.setattr(resolver, "_resolve_duplicate_arrival_with_camera", forbidden)
    result = await resolver.resolve(ProbeSession(), read(), read(), None, trace)
    assert result.plan.direction == AccessDirection.ENTRY
    assert result.direction_resolution["camera_tiebreaker_ignored_reason"] == "low_confidence"
    forbidden.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["person", "source", "captured_at", "registration_number", "camera", "device", "event", "confidence", "candidates"])
async def test_prepared_evidence_cannot_be_reused_for_different_identity_or_observation(monkeypatch, field):
    resolver, person = resolver_with_inert_domain(monkeypatch)
    observation = read()
    prepared = PreparedCameraEvidence.for_read(person.id, observation, {"direction": "exit", "confidence": 0.99})
    if field == "person":
        person = Person(id=uuid.uuid4(), display_name="Other synthetic person", is_active=True)
    elif field in {"camera", "device", "event"}:
        key = {"camera": "cameraId", "device": "deviceId", "event": "eventId"}[field]
        observation = replace(observation, raw_payload={**observation.raw_payload, key: "different-synthetic"})
    else:
        values = {"source": "other-source", "captured_at": NOW + timedelta(seconds=1),
                  "registration_number": "OTHER123", "confidence": 0.7, "candidates": ("OTHER123",)}
        observation = replace(observation, **{"candidate_registration_numbers" if field == "candidates" else field: values[field]})
    direction, resolution = await resolver._resolve_direction(ProbeSession(), observation, person,
                                                             allowed=True, camera_evidence=prepared)
    assert direction == AccessDirection.ENTRY
    assert resolution["source"] == "gate_state"
    assert resolution["camera_tiebreaker_ignored_reason"] == "low_confidence"


@pytest.mark.asyncio
async def test_final_resolution_uses_matching_prepared_evidence_without_vendor(monkeypatch, trace):
    resolver, person = resolver_with_inert_domain(monkeypatch)
    observation = read()
    prepared = PreparedCameraEvidence.for_read(person.id, observation, {"direction": "exit", "confidence": 0.91})
    forbidden = AsyncMock(side_effect=AssertionError("Final transaction reached optional vendor"))
    monkeypatch.setattr(resolver, "_resolve_duplicate_arrival_with_camera", forbidden)
    result = await resolver.resolve(ProbeSession(), observation, observation, None, trace, camera_evidence=prepared)
    assert result.plan.direction == AccessDirection.EXIT
    assert result.direction_resolution["source"] == "camera_tiebreaker"
    forbidden.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_camera_request_skips_all_optional_vendor_work(monkeypatch, trace):
    resolver, _person = resolver_with_inert_domain(monkeypatch)
    session = ProbeSession()
    session.get = AsyncMock(return_value=None)
    monkeypatch.setattr(owner, "AsyncSessionLocal", lambda: session)
    forbidden = AsyncMock(side_effect=AssertionError("Unneeded camera request"))
    monkeypatch.setattr(resolver, "_resolve_duplicate_arrival_with_camera", forbidden)
    assert await resolver.prepare_camera_evidence(read(), read(), None, trace) is None
    assert session.exited
    forbidden.assert_not_awaited()


@pytest.mark.asyncio
async def test_probe_cancellation_exits_owned_session_and_does_not_call_vendor(monkeypatch, trace):
    import asyncio

    resolver, _person = resolver_with_inert_domain(monkeypatch)
    session = ProbeSession()
    monkeypatch.setattr(owner, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(resolver, "resolve", AsyncMock(side_effect=asyncio.CancelledError()))
    forbidden = AsyncMock(side_effect=AssertionError("Cancelled camera request"))
    monkeypatch.setattr(resolver, "_resolve_duplicate_arrival_with_camera", forbidden)
    with pytest.raises(asyncio.CancelledError):
        await resolver.prepare_camera_evidence(read(), read(), None, trace)
    assert session.exited
    forbidden.assert_not_awaited()




@pytest.mark.asyncio
async def test_vendor_cancellation_terminalizes_camera_span_without_reopening_probe_session(monkeypatch, trace):
    import asyncio

    resolver, _person = resolver_with_inert_domain(monkeypatch)
    session = ProbeSession()
    monkeypatch.setattr(owner, "AsyncSessionLocal", lambda: session)
    snapshot = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(owner, "get_unifi_protect_service", lambda: SimpleNamespace(snapshot=snapshot))
    before = CURRENT_PARENT_SPAN_ID.get()
    with pytest.raises(asyncio.CancelledError):
        await resolver.prepare_camera_evidence(read(), read(), None, trace)
    assert session.exited
    assert CURRENT_PARENT_SPAN_ID.get() == before
    assert any(span.name == "LLM Vision Direction Tie-breaker" and span.ended_at is not None
               for span in trace.recorded_spans)


@pytest.mark.asyncio
async def test_inactive_present_owner_keeps_camera_direction_evidence(monkeypatch, trace):
    resolver, person = resolver_with_inert_domain(monkeypatch)
    person.is_active = False
    session = ProbeSession()
    monkeypatch.setattr(owner, "AsyncSessionLocal", lambda: session)
    requested = AsyncMock(side_effect=owner.CameraEvidenceRequired(person))
    monkeypatch.setattr(resolver, "resolve", requested)
    camera = AsyncMock(return_value={"direction": "exit", "confidence": .95})
    monkeypatch.setattr(resolver, "_resolve_duplicate_arrival_with_camera", camera)
    observation = read()
    result = await resolver.prepare_camera_evidence(observation, observation, None, trace)
    assert result.person_id == person.id and result.decision["direction"] == "exit"
    requested.assert_awaited_once()
