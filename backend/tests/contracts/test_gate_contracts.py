from __future__ import annotations

from datetime import datetime

from app.modules.gate.base import GateState
from app.services.gate_commands import GateCommandIntent, GateCommandOutcome

from .helpers import assert_contract_subset, load_contract_fixture


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_gate_command_success_contract_is_auditable_and_idempotent_payload() -> None:
    fixture = load_contract_fixture("realtime/gate_command_accepted.json")
    intent = GateCommandIntent(
        reason="Resident arrival PE70DHX",
        source="lpr",
        controller_name="configured",
        gate_key="top_gate",
        event_id="11111111-1111-1111-1111-111111111111",
        movement_saga_id="55555555-5555-5555-5555-555555555555",
        registration_number="PE70DHX",
        intent_id="intent-open-gate-1",
        idempotency_key="gate-command:open:top_gate:event:11111111-1111-1111-1111-111111111111",
    )
    outcome = GateCommandOutcome(
        intent=intent,
        accepted=True,
        state=GateState.OPENING,
        detail="Gate command accepted.",
        started_at=_dt("2026-05-31T08:15:00+00:00"),
        completed_at=_dt("2026-05-31T08:15:01+00:00"),
        mechanically_confirmed=True,
        command_id="66666666-6666-6666-6666-666666666666",
        metadata={"provider": "home_assistant"},
    )

    payload = outcome.as_payload()

    assert payload["accepted"] is True
    assert payload["requires_reconciliation"] is False
    assert payload["command_id"] == "66666666-6666-6666-6666-666666666666"
    assert_contract_subset(payload, fixture)


def test_gate_command_failure_contract_never_reports_rejected_provider_as_success() -> None:
    fixture = load_contract_fixture("realtime/gate_command_failed.json")
    intent = GateCommandIntent(
        reason="Provider failure while opening gate",
        source="lpr",
        controller_name="configured",
        gate_key="top_gate",
        event_id="11111111-1111-1111-1111-111111111111",
        movement_saga_id="55555555-5555-5555-5555-555555555555",
        registration_number="PE70DHX",
        intent_id="intent-open-gate-2",
    )
    outcome = GateCommandOutcome(
        intent=intent,
        accepted=False,
        state=GateState.FAULT,
        detail="Provider rejected gate open.",
        started_at=_dt("2026-05-31T08:16:00+00:00"),
        completed_at=_dt("2026-05-31T08:16:01+00:00"),
        mechanically_confirmed=False,
        exception_class="RuntimeError",
        command_id="77777777-7777-7777-7777-777777777777",
        metadata={"provider": "home_assistant"},
    )

    payload = outcome.as_payload()

    assert payload["accepted"] is False
    assert payload["requires_reconciliation"] is False
    assert payload["exception_class"] == "RuntimeError"
    assert_contract_subset(payload, fixture)
