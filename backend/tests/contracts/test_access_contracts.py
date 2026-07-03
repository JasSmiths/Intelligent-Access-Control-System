from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from app.models.enums import AccessDecision, AccessDirection, PresenceState, TimingClassification
from app.modules.gate.base import GateState
from app.modules.lpr.ubiquiti import UbiquitiLprAdapter
from app.services.access.payloads import access_event_realtime_payload, notification_facts
from app.services.movement_fsm import (
    MovementDirectionFSM,
    MovementSuppressionFSM,
    MovementIntent,
    PlateReadMovementEvidence,
    ResolvedMovementWindow,
)

from .helpers import assert_contract_subset, load_contract_fixture


PERSON_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
VEHICLE_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
ENTRY_EVENT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
EXIT_EVENT_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
VISITOR_PASS_ID = uuid.UUID("88888888-8888-8888-8888-888888888888")
EXTERNAL_ADMISSION_EVENT_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _access_event(
    *,
    event_id: uuid.UUID,
    registration_number: str,
    direction: AccessDirection,
    decision: AccessDecision,
    confidence: float,
    occurred_at: datetime,
    person_id: uuid.UUID | None = PERSON_ID,
    vehicle_id: uuid.UUID | None = VEHICLE_ID,
    raw_payload: dict[str, Any] | None = None,
    source: str = "ubiquiti",
    timing_classification: TimingClassification = TimingClassification.NORMAL,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=event_id,
        person_id=person_id,
        vehicle_id=vehicle_id,
        registration_number=registration_number,
        direction=direction,
        decision=decision,
        confidence=confidence,
        source=source,
        occurred_at=occurred_at,
        timing_classification=timing_classification,
        raw_payload=raw_payload or {},
        snapshot_path=None,
    )


def test_lpr_arrival_contract_records_entry_and_gate_action_boundary() -> None:
    payload = load_contract_fixture("lpr/resident_arrival.json")
    read = UbiquitiLprAdapter().to_plate_read(payload)

    assert read.registration_number == "PE70DHX"
    assert read.confidence == 0.97
    assert "PE70DHX" in read.candidate_registration_numbers

    movement = MovementDirectionFSM().resolve(
        MovementIntent(
            source=read.source,
            captured_at=read.captured_at,
            registration_number=read.registration_number,
            allowed=True,
            person_known=True,
            vehicle_known=True,
            gate_state=GateState.CLOSED,
            presence_state=PresenceState.EXITED,
        )
    )

    assert movement.direction == AccessDirection.ENTRY
    assert movement.physical_action_required is True
    assert movement.resolution["physical_action"] == "gate.open"

    event = _access_event(
        event_id=ENTRY_EVENT_ID,
        registration_number=read.registration_number,
        direction=movement.direction,
        decision=AccessDecision.GRANTED,
        confidence=read.confidence,
        occurred_at=read.captured_at,
    )
    realtime_payload = access_event_realtime_payload(
        event,
        anomaly_count=0,
        visitor_pass=None,
        visitor_pass_mode=None,
    )

    assert_contract_subset(realtime_payload, load_contract_fixture("realtime/access_entry.json"))


def test_lpr_exit_contract_updates_presence_without_unneeded_gate_open() -> None:
    payload = load_contract_fixture("lpr/resident_exit.json")
    read = UbiquitiLprAdapter().to_plate_read(payload)

    movement = MovementDirectionFSM().resolve(
        MovementIntent(
            source=read.source,
            captured_at=read.captured_at,
            registration_number=read.registration_number,
            allowed=True,
            person_known=True,
            vehicle_known=True,
            gate_state=GateState.OPEN,
            presence_state=PresenceState.PRESENT,
        )
    )

    assert movement.direction == AccessDirection.EXIT
    assert movement.physical_action_required is False
    assert "physical_action" not in movement.resolution

    event = _access_event(
        event_id=EXIT_EVENT_ID,
        registration_number=read.registration_number,
        direction=movement.direction,
        decision=AccessDecision.GRANTED,
        confidence=read.confidence,
        occurred_at=read.captured_at,
    )
    realtime_payload = access_event_realtime_payload(
        event,
        anomaly_count=0,
        visitor_pass=None,
        visitor_pass_mode=None,
    )

    assert_contract_subset(realtime_payload, load_contract_fixture("realtime/access_exit.json"))


def test_known_resident_vehicle_contract_preserves_access_decision_facts() -> None:
    event = _access_event(
        event_id=ENTRY_EVENT_ID,
        registration_number="PE70DHX",
        direction=AccessDirection.ENTRY,
        decision=AccessDecision.GRANTED,
        confidence=0.97,
        occurred_at=_dt("2026-05-31T08:15:00+00:00"),
        raw_payload={"telemetry": {"trace_id": "trace-access-1"}},
    )
    person = SimpleNamespace(
        first_name="Steph",
        last_name="Smith",
        display_name="Steph Smith",
        group=SimpleNamespace(name="Residents"),
        pronouns="",
    )
    vehicle = SimpleNamespace(
        make="Tesla",
        model="Model Y",
        description="Steph's Tesla",
        registration_number="PE70DHX",
        color="Blue",
    )

    facts = notification_facts(
        event,
        person,
        vehicle,
        "Steph arrived.",
        dvla_enrichment={
            "make": "TESLA",
            "colour": "BLUE",
            "mot_status": "Valid",
            "tax_status": "Taxed",
        },
        schedule_allowed=True,
        garage_binding="garage.main",
        compliance_summary="MOT valid, taxed",
    )

    assert facts["display_name"] == "Steph Smith"
    assert facts["vehicle_display_name"] == "Tesla Model Y"
    assert facts["vehicle_make"] == "TESLA"
    assert facts["vehicle_color"] == "BLUE"
    assert facts["mot_status"] == "Valid"
    assert facts["tax_status"] == "Taxed"
    assert facts["direction"] == "entry"
    assert facts["decision"] == "granted"
    assert facts["schedule_allowed"] == "True"
    assert facts["garage_binding"] == "garage.main"
    assert facts["telemetry_trace_id"] == "trace-access-1"


def test_visitor_vehicle_contract_links_pass_and_mode() -> None:
    payload = load_contract_fixture("lpr/visitor_arrival.json")
    read = UbiquitiLprAdapter().to_plate_read(payload)
    event = _access_event(
        event_id=uuid.UUID("99999999-9999-9999-9999-999999999999"),
        registration_number=read.registration_number,
        direction=AccessDirection.ENTRY,
        decision=AccessDecision.GRANTED,
        confidence=read.confidence,
        occurred_at=read.captured_at,
        person_id=None,
        vehicle_id=None,
    )
    visitor_pass = SimpleNamespace(id=VISITOR_PASS_ID, visitor_name="Taylor Visitor")

    realtime_payload = access_event_realtime_payload(
        event,
        anomaly_count=0,
        visitor_pass=visitor_pass,
        visitor_pass_mode="arrival",
    )

    assert realtime_payload["registration_number"] == "VIS1TOR"
    assert realtime_payload["visitor_pass_id"] == str(VISITOR_PASS_ID)
    assert realtime_payload["visitor_name"] == "Taylor Visitor"
    assert realtime_payload["visitor_pass_mode"] == "arrival"
    assert realtime_payload["decision"] == "granted"


def test_external_unknown_admission_contract_exposes_mode_and_source() -> None:
    event = _access_event(
        event_id=EXTERNAL_ADMISSION_EVENT_ID,
        registration_number="UNK123",
        direction=AccessDirection.ENTRY,
        decision=AccessDecision.GRANTED,
        confidence=0.91,
        occurred_at=_dt("2026-05-31T08:18:12+00:00"),
        person_id=None,
        vehicle_id=None,
        source="gate_state_changed",
        timing_classification=TimingClassification.UNKNOWN,
        raw_payload={
            "external_admission": {
                "mode": "arrival",
                "source": "gate_state_changed",
                "original_denied_access_event_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            }
        },
    )

    realtime_payload = access_event_realtime_payload(
        event,
        anomaly_count=1,
        visitor_pass=None,
        visitor_pass_mode=None,
    )

    assert_contract_subset(realtime_payload, load_contract_fixture("realtime/external_unknown_admission.json"))


def test_unknown_vehicle_contract_denies_and_avoids_hardware_side_effects() -> None:
    payload = load_contract_fixture("lpr/unknown_plate.json")
    read = UbiquitiLprAdapter().to_plate_read(payload)

    movement = MovementDirectionFSM().resolve(
        MovementIntent(
            source=read.source,
            captured_at=read.captured_at,
            registration_number=read.registration_number,
            allowed=False,
            person_known=False,
            vehicle_known=False,
            gate_state=GateState.CLOSED,
            presence_state=None,
        )
    )

    assert movement.direction == AccessDirection.DENIED
    assert movement.physical_action_required is False
    assert movement.resolution["source"] == "access_denied"
    assert movement.resolution["movement_state"] == "failed"


def test_suppressed_duplicate_reads_are_durable_movements_not_silent_drops() -> None:
    fixture = load_contract_fixture("lpr/duplicate_burst.json")
    window = ResolvedMovementWindow(
        source=fixture["resolved_window"]["source"],
        registration_number=fixture["resolved_window"]["registration_number"],
        first_seen=_dt(fixture["resolved_window"]["first_seen"]),
        debounce_expires_at=_dt(fixture["resolved_window"]["debounce_expires_at"]),
        gate_cycle_expires_at=_dt(fixture["resolved_window"]["gate_cycle_expires_at"]),
        direction=AccessDirection(fixture["resolved_window"]["direction"]),
        decision=AccessDecision(fixture["resolved_window"]["decision"]),
    )
    read = PlateReadMovementEvidence(
        source=fixture["read"]["source"],
        registration_number=fixture["read"]["registration_number"],
        captured_at=_dt(fixture["read"]["captured_at"]),
        gate_state=GateState(fixture["read"]["gate_state"]),
        direction_hint=AccessDirection(fixture["read"]["direction_hint"]),
        has_known_vehicle_match=fixture["read"]["has_known_vehicle_match"],
    )

    decision = MovementSuppressionFSM().classify_exact_plate_read(read, [window])
    realtime_payload = {
        "movement_state": decision.state.value,
        "reason": decision.reason,
        "kind": decision.kind.value if decision.kind else None,
        "registration_number": read.registration_number,
        "source": read.source,
    }

    assert decision.suppress is True
    assert decision.state.value == "suppressed"
    assert_contract_subset(realtime_payload, load_contract_fixture("realtime/suppressed_read.json"))
