from __future__ import annotations

from app.modules.gate.base import GateCommandResult, GateController, GateState
from app.services.access_devices import get_access_device_service


class AccessDeviceGateController(GateController):
    """Gate controller backed by IACS access-device records and provider bindings."""

    async def open_gate(self, reason: str, *, bypass_schedule: bool = False) -> GateCommandResult:
        outcomes = await get_access_device_service().open_access_gates(
            reason,
            bypass_schedule=bypass_schedule,
        )
        if not outcomes:
            return GateCommandResult(False, GateState.UNKNOWN, "No enabled access gates are configured.")
        failed = [outcome for outcome in outcomes if not outcome.accepted]
        state = outcomes[0].state if outcomes else GateState.UNKNOWN
        metadata = {
            "access_device_outcomes": [outcome.as_payload() for outcome in outcomes],
            "primary_provider": outcomes[0].primary_provider,
            "used_provider": outcomes[0].used_provider,
            "failover_used": any(outcome.failover_used for outcome in outcomes),
        }
        if failed:
            return GateCommandResult(
                False,
                GateState.FAULT,
                "; ".join(outcome.detail or outcome.device.name for outcome in failed),
                metadata,
            )
        return GateCommandResult(True, state, reason, metadata)

    async def current_state(self) -> GateState:
        service = get_access_device_service()
        gates = await service.list_devices(kind="gate", enabled_only=True)
        if not gates:
            return GateState.UNKNOWN
        try:
            result = await service.read_state(gates[0])
            return result.state
        except Exception:
            return GateState.UNKNOWN
