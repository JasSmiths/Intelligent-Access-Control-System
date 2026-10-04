"""Recovery integration decisions with synthetic sessions and inert provider seams."""
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from app.models import Person, Vehicle
from app.models.enums import AccessDecision, AccessDirection, PresenceState, TimingClassification
from app.modules.lpr.base import PlateRead
from app.services import resident_recovery as recovery
from app.services.access import evidence as owner
from app.services.access.evidence import AccessEvidenceResolver, PreparedCameraEvidence
from app.services.access.reads import GATE_OBSERVATION_PAYLOAD_KEY, KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY
from app.services.schedules import ScheduleEvaluation
from app.services.telemetry import ActiveTrace

NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)


def read(*, gate="closed", exact=True, explicit=None, observation_age=0):
    payload = {
        "cameraId": "synthetic-camera", "eventId": "synthetic-event",
        GATE_OBSERVATION_PAYLOAD_KEY: {"state": gate,
            "observed_at": (NOW - timedelta(seconds=observation_age)).isoformat()},
        KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY: {"exact": exact,
            "registration_number": "SYNTH01", "observed_registration_number": "SYNTHO1" if not exact else "SYNTH01"},
    }
    if explicit:
        payload["direction"] = explicit
    return PlateRead("SYNTH01", 0.99, "synthetic", NOW, payload)


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, model, identity, **kwargs):
        return SimpleNamespace(state=PresenceState.PRESENT)

    async def scalar(self, statement):
        return NOW

    async def scalars(self, statement):
        return SimpleNamespace(all=lambda: [])


@pytest.fixture
def trace():
    value = ActiveTrace(service=SimpleNamespace(enqueue_span=lambda row: None,
        enqueue_trace=lambda row: None), name="Synthetic recovery guard", category="lpr")
    try:
        yield value
    finally:
        value.finish()


def resolver(monkeypatch, *, schedule_allowed=True):
    runtime = SimpleNamespace(site_timezone="UTC", schedule_default_policy="allow",
        missed_exit_recovery_enabled=True, missed_exit_recovery_gate_latitude=51.5,
        missed_exit_recovery_gate_longitude=-0.1, gate_admission_device_key="synthetic-gate")
    person = Person(id=uuid.uuid4(), display_name="Synthetic Resident", is_active=True,
        missed_exit_recovery_enabled=True, missed_exit_recovery_tracker_entity_id="device_tracker.synthetic")
    vehicle = Vehicle(id=uuid.uuid4(), registration_number="SYNTH01", person_id=person.id,
        owner=person, is_active=True)
    value = AccessEvidenceResolver(runtime)
    monkeypatch.setattr(value, "_lookup_active_vehicle", AsyncMock(return_value=vehicle))
    monkeypatch.setattr(value, "_schedule_evaluation_for_detection", AsyncMock(
        return_value=ScheduleEvaluation(schedule_allowed, "synthetic", reason="Synthetic schedule")))
    monkeypatch.setattr(value, "_classify_timing", AsyncMock(return_value=TimingClassification.UNKNOWN))
    monkeypatch.setattr(recovery, "has_conflict", AsyncMock(return_value=True))
    monkeypatch.setattr(recovery, "blockers", AsyncMock(return_value=None))
    # The only camera/provider seam is inert in these tests. The final resolver
    # must consume prepared evidence rather than calling a provider in its transaction.
    monkeypatch.setattr(value, "_resolve_duplicate_arrival_with_camera",
        AsyncMock(side_effect=AssertionError("Unprepared camera request")))
    return value, person, vehicle


@pytest.mark.parametrize("gate", ["open", "opening", "closing"])
async def test_present_owner_at_departure_gate_preserves_ordinary_exit(monkeypatch, trace, gate):
    value, _person, _vehicle = resolver(monkeypatch)
    phone = AsyncMock(side_effect=AssertionError("An ordinary departure must not request phone recovery"))
    monkeypatch.setattr(recovery, "evaluate_phone", phone)
    observation = read(gate=gate)
    result = await value.resolve(FakeSession(), observation, observation, None, trace)
    assert result.plan.direction == AccessDirection.EXIT
    assert result.plan.decision == AccessDecision.GRANTED
    assert result.plan.gate_command_required is False
    assert "missed_exit_recovery" not in result.direction_resolution
    phone.assert_not_awaited()
    recovery.has_conflict.assert_not_awaited()
    value._resolve_duplicate_arrival_with_camera.assert_not_awaited()


@pytest.mark.parametrize("camera", [None, {"direction": "entry", "confidence": 0.99}])
async def test_canonical_fuzzy_plate_cannot_gain_phone_camera_or_resident_authority(monkeypatch, trace, camera):
    value, person, _vehicle = resolver(monkeypatch)
    observation = read(exact=False)
    # Actual phone eligibility must reject the fuzzy match before any journey read.
    prepared = PreparedCameraEvidence.for_read(person.id, observation, camera) if camera else None
    result = await value.resolve(FakeSession(), observation, observation, None, trace,
                                 camera_evidence=prepared)
    detail = result.direction_resolution["missed_exit_recovery"]
    assert detail["checks"]["exact_plate"] is False
    assert detail["method"] == "none"
    assert detail["approval_eligible"] is False
    assert result.plan.decision == AccessDecision.DENIED
    assert result.plan.allowed is False
    assert result.plan.gate_command_required is False


@pytest.mark.parametrize("camera", [
    {"direction": "unknown", "confidence": 0.99},
    {"direction": "entry", "confidence": 0.1},
    {"direction": "unknown", "confidence": 0.0, "reason": "camera_timeout"},
])
async def test_ambiguous_or_timed_out_camera_does_not_grant_entry(monkeypatch, trace, camera):
    value, person, _vehicle = resolver(monkeypatch)
    monkeypatch.setattr(recovery, "evaluate_phone", AsyncMock(return_value=(
        False, "phone_journey_missing", {"exact_plate": True})))
    observation = read()
    prepared = PreparedCameraEvidence.for_read(person.id, observation, camera)
    result = await value.resolve(FakeSession(), observation, observation, None, trace,
                                 camera_evidence=prepared)
    detail = result.direction_resolution["missed_exit_recovery"]
    assert detail["method"] == "none"
    assert detail["approval_eligible"] is True
    assert result.plan.direction == AccessDirection.ENTRY
    assert result.plan.decision == AccessDecision.DENIED
    assert result.plan.gate_command_required is False
    value._resolve_duplicate_arrival_with_camera.assert_not_awaited()


async def test_clear_current_camera_evidence_can_resolve_recovery_as_entry(monkeypatch, trace):
    value, person, _vehicle = resolver(monkeypatch)
    monkeypatch.setattr(recovery, "evaluate_phone", AsyncMock(return_value=(
        False, "phone_journey_missing", {"exact_plate": True})))
    observation = read()
    prepared = PreparedCameraEvidence.for_read(person.id, observation,
        {"direction": "entry", "confidence": 0.99})
    result = await value.resolve(FakeSession(), observation, observation, None, trace,
                                 camera_evidence=prepared)
    assert result.direction_resolution["missed_exit_recovery"]["method"] == "camera_automatic"
    assert result.plan.decision == AccessDecision.GRANTED
    assert result.plan.gate_command_required is True


async def test_schedule_denial_cannot_gain_camera_or_resident_authority(monkeypatch, trace):
    value, person, _vehicle = resolver(monkeypatch, schedule_allowed=False)
    phone = AsyncMock(side_effect=AssertionError("A denied schedule cannot authorize phone recovery"))
    monkeypatch.setattr(recovery, "evaluate_phone", phone)
    observation = read()
    prepared = PreparedCameraEvidence.for_read(person.id, observation,
        {"direction": "entry", "confidence": 0.99})
    result = await value.resolve(FakeSession(), observation, observation, None, trace,
                                 camera_evidence=prepared)
    assert result.plan.decision == AccessDecision.DENIED
    assert result.plan.gate_command_required is False
    assert result.direction_resolution["missed_exit_recovery"]["approval_eligible"] is False
    phone.assert_not_awaited()


async def test_explicit_departure_cannot_be_converted_to_phone_entry(monkeypatch, trace):
    value, _person, _vehicle = resolver(monkeypatch)
    monkeypatch.setattr(recovery, "evaluate_phone", AsyncMock(return_value=(
        True, "synthetic_phone_ready", {"exact_plate": True})))
    observation = read(explicit="exit")
    result = await value.resolve(FakeSession(), observation, observation, None, trace)
    assert result.plan.direction == AccessDirection.EXIT
    assert result.plan.gate_command_required is False
    assert result.direction_resolution["missed_exit_recovery"]["method"] == "none"


async def test_recovery_camera_timeout_is_bounded_and_discards_late_clear_entry(monkeypatch, trace):
    value, _person, _vehicle = resolver(monkeypatch)
    monkeypatch.setattr(recovery, "evaluate_phone", AsyncMock(return_value=(
        False, "phone_journey_missing", {"exact_plate": True})))
    monkeypatch.setattr(owner, "AsyncSessionLocal", FakeSession)
    timeout_values = []
    cancelled = asyncio.Event()

    async def delayed_camera(*args, **kwargs):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            return {"direction": "entry", "confidence": 0.99, "reason": "late synthetic result"}

    async def timed_out(tasks, *, timeout):
        timeout_values.append(timeout)
        await asyncio.sleep(0)
        return set(), set(tasks)

    monkeypatch.setattr(value, "_resolve_duplicate_arrival_with_camera", delayed_camera)
    monkeypatch.setattr(owner.asyncio, "wait", timed_out)
    observation = read()
    prepared = await value.prepare_camera_evidence(observation, observation, None, trace)
    assert timeout_values == [5]
    assert prepared.decision["direction"] == "unknown"
    assert prepared.decision["reason"] == "camera_timeout"
    await asyncio.sleep(0)
    assert cancelled.is_set()
    result = await value.resolve(FakeSession(), observation, observation, None, trace,
                                 camera_evidence=prepared)
    assert result.plan.decision == AccessDecision.DENIED
    assert result.plan.gate_command_required is False
    assert result.direction_resolution["missed_exit_recovery"]["method"] == "none"
@pytest.mark.parametrize("connected,expected_reason", [
    (False, "tracker_disconnected"), (True, "journey_configuration_or_connection_changed"),
])
async def test_disconnect_or_reconnect_revokes_a_previously_valid_journey(monkeypatch, connected, expected_reason):
    value, person, vehicle = resolver(monkeypatch)
    monkeypatch.setattr(recovery, "_connected", True)
    monkeypatch.setattr(recovery, "_epoch", "initial-synthetic-epoch")
    monkeypatch.setattr(recovery, "configuration_binding", lambda person, config: "synthetic-binding")
    samples = [{"at": (NOW - timedelta(seconds=age)).isoformat(), "distance_m": distance,
                "accuracy_m": 20} for age, distance in [(420, 1500), (120, 700), (60, 350), (5, 100)]]
    row = SimpleNamespace(epoch="initial-synthetic-epoch", binding="synthetic-binding", samples=samples,
        consumed_at=None, claimed_event_id=None, invalid_reason=None)
    session = FakeSession()
    session.get = AsyncMock(return_value=row)
    session.scalar = AsyncMock(side_effect=[NOW, NOW - timedelta(hours=1)])
    ready, reason, _checks = await recovery.evaluate_phone(session, person=person,
        vehicle=vehicle, read=read(), config=value._runtime)
    assert ready is True
    assert reason == "phone_return_corroborated"
    session.get.reset_mock()
    session.scalar = AsyncMock(return_value=NOW)
    recovery.invalidate_connection(connected=connected)
    ready, reason, _checks = await recovery.evaluate_phone(session, person=person,
        vehicle=vehicle, read=read(), config=value._runtime)
    assert ready is False
    assert reason == expected_reason
    if not connected:
        session.get.assert_not_awaited()
