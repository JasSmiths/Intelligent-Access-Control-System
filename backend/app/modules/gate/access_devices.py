from __future__ import annotations

from typing import Any

from app.modules.access_devices.base import gate_receipt_projection
from app.modules.gate.base import GateCommandDelivery, GateCommandContext, GateCommandResult, GateController, GateState
from app.services.access_devices import get_access_device_service


class AccessDeviceGateController(GateController):
    """Gate controller backed by IACS access-device records and provider bindings."""

    async def preview_manual_gate_open(self) -> dict[str, Any]:
        return await get_access_device_service().preview_gate_open(require_admission=False)

    async def open_gate(self, reason: str, *, bypass_schedule: bool = False,
                        command_context: GateCommandContext) -> GateCommandResult:
        outcomes = await get_access_device_service().open_access_gates(
            reason, bypass_schedule=bypass_schedule, command_context=command_context)
        if not outcomes:
            return GateCommandResult(False, GateState.UNKNOWN, "No enabled access gates are configured.",
                                     delivery=GateCommandDelivery.NOT_SENT)
        receipts = [outcome.metadata["target_receipt"] for outcome in outcomes]
        admission_id = outcomes[0].metadata.get("admission_target_device_id")
        projection = gate_receipt_projection(receipts, admission_target_device_id=admission_id,
                                             expected_target_count=len(outcomes))
        return GateCommandResult(projection["accepted"], GateState(projection["state"]), reason, {
            **projection, "automatic_entry_precondition": outcomes[0].metadata.get("automatic_entry_precondition"),
            "access_device_outcomes": [outcome.as_payload() for outcome in outcomes],
            "primary_provider": outcomes[0].primary_provider, "used_provider": outcomes[0].used_provider,
            "failover_used": any(outcome.failover_used for outcome in outcomes),
        }, delivery=GateCommandDelivery(projection["delivery"]))

    async def current_state(self) -> GateState:
        return await get_access_device_service().read_admission_gate_state()
