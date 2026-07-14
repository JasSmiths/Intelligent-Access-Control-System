import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.modules.access_devices.base import (
    AccessDeviceBinding,
    AccessDeviceCommandResult,
    AccessDeviceDiscoveryItem,
    AccessDeviceEntity,
    AccessDeviceProviderUnavailable,
)
from app.modules.access_devices import esphome as esphome_module
from app.modules.access_devices.esphome import ESPHomeAccessDeviceProvider
from app.modules.access_devices.home_assistant import HomeAssistantAccessDeviceProvider
from app.modules.access_devices.registry import get_access_device_provider
from app.modules.gate.base import GateState
from app.services import access_devices as access_devices_module
from app.services.access_devices import (
    AccessDeviceOperationResult,
    AccessDeviceProviderAttempt,
    AccessDeviceService,
)


class FakeProvider:
    def __init__(
        self,
        name: str,
        *,
        unavailable: bool = False,
        accepted: bool = True,
        state: GateState = GateState.OPENING,
        command_states: list[GateState] | None = None,
    ) -> None:
        self.name = name
        self.unavailable = unavailable
        self.accepted = accepted
        self.state = state
        self.command_states = list(command_states or [])
        self.commands: list[tuple[str, str]] = []

    async def current_state(self, binding: AccessDeviceBinding) -> GateState:
        if self.unavailable:
            raise AccessDeviceProviderUnavailable(f"{self.name} down")
        return self.state

    async def command_cover(self, binding: AccessDeviceBinding, action: str, reason: str):
        self.commands.append((binding.external_id, action))
        if self.unavailable:
            raise AccessDeviceProviderUnavailable(f"{self.name} down")
        if self.command_states:
            self.state = self.command_states.pop(0)
        return AccessDeviceCommandResult(
            accepted=self.accepted,
            state=self.state,
            detail=reason,
            provider=self.name,
            external_id=binding.external_id,
        )


async def _wait_for_command(client: "FakeESPHomeClient") -> None:
    for _ in range(20):
        if client.commands:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("ESPHome command was not sent")


async def _noop_remember_state(*_args, **_kwargs) -> None:
    return None


def test_access_device_provider_registry_returns_singletons() -> None:
    assert get_access_device_provider("esphome") is get_access_device_provider("esphome")
    assert get_access_device_provider("home_assistant") is get_access_device_provider("home_assistant")


def test_command_evidence_distinguishes_withheld_attempted_accepted_and_verified() -> None:
    device = AccessDeviceEntity(
        key="main_garage_door",
        kind="garage_door",
        name="Main Garage Door",
    )
    service = AccessDeviceService()

    schedule_blocked = AccessDeviceOperationResult(
        device=device,
        action="open",
        accepted=False,
        state=GateState.FAULT,
        metadata={
            "schedule_denied": True,
            "schedule_evaluation": {
                "allowed": False,
                "reason_code": "schedule_outside_window",
            },
        },
    )
    provider_rejected = AccessDeviceOperationResult(
        device=device,
        action="open",
        accepted=False,
        state=GateState.FAULT,
        attempts=[AccessDeviceProviderAttempt(provider="esphome", accepted=False)],
    )
    accepted_unverified = AccessDeviceOperationResult(
        device=device,
        action="open",
        accepted=True,
        state=GateState.CLOSED,
        attempts=[AccessDeviceProviderAttempt(provider="esphome", accepted=True)],
    )
    verified = AccessDeviceOperationResult(
        device=device,
        action="open",
        accepted=True,
        state=GateState.OPEN,
        attempts=[
            AccessDeviceProviderAttempt(
                provider="esphome",
                accepted=True,
                verified=True,
            )
        ],
    )

    assert service._command_evidence_outcome(schedule_blocked) == (
        "withheld",
        "schedule_outside_window",
    )
    assert service._command_evidence_outcome(provider_rejected) == (
        "attempted",
        "integration_rejected",
    )
    assert service._command_evidence_outcome(accepted_unverified) == (
        "accepted",
        "device_state_unverified",
    )
    assert service._command_evidence_outcome(verified) == (
        "verified",
        "device_state_verified",
    )


def test_command_evidence_records_whether_a_command_was_sent(monkeypatch) -> None:
    device = AccessDeviceEntity(
        key="main_garage_door",
        kind="garage_door",
        name="Main Garage Door",
    )
    result = AccessDeviceOperationResult(
        device=device,
        action="open",
        accepted=False,
        state=GateState.FAULT,
        metadata={
            "schedule_denied": True,
            "schedule_evaluation": {
                "allowed": False,
                "reason_code": "schedule_outside_window",
            },
        },
    )
    spans = []
    audits = []
    monkeypatch.setattr(
        access_devices_module.telemetry,
        "record_span",
        lambda *args, **kwargs: spans.append((args, kwargs)),
    )
    monkeypatch.setattr(
        access_devices_module,
        "emit_audit_log",
        lambda **kwargs: audits.append(kwargs),
    )

    AccessDeviceService()._record_command_evidence(
        result,
        reason="Arrival automation",
        started_at=datetime(2026, 7, 14, 21, 47, tzinfo=UTC),
        trace_id="a" * 32,
    )

    assert spans[0][1]["output_payload"]["command_sent"] is False
    assert spans[0][1]["output_payload"]["dispatch_state"] == "withheld"
    assert audits[0]["action"] == "access_device.command.withheld"
    assert audits[0]["metadata"]["reason_code"] == "schedule_outside_window"


@pytest.mark.asyncio
async def test_home_assistant_command_stays_accepted_when_immediate_state_read_fails() -> None:
    class FakeHomeAssistantClient:
        def __init__(self) -> None:
            self.service_calls: list[tuple[str, dict[str, str]]] = []
            self.state_reads = 0

        async def call_service(self, service: str, data: dict[str, str]) -> None:
            self.service_calls.append((service, data))

        async def get_state(self, _entity_id: str):
            self.state_reads += 1
            raise RuntimeError("state unavailable after accepted service call")

    client = FakeHomeAssistantClient()
    provider = HomeAssistantAccessDeviceProvider(client=client)  # type: ignore[arg-type]
    binding = AccessDeviceBinding("home_assistant", "cover.top_gate")

    result = await provider.command_cover(binding, "open", "Resident arrival")

    assert result.accepted is True
    assert result.state is GateState.UNKNOWN
    assert result.provider == "home_assistant"
    assert result.external_id == "cover.top_gate"
    assert client.service_calls == [
        ("cover.open_cover", {"entity_id": "cover.top_gate"})
    ]
    assert client.state_reads == 1


@pytest.mark.asyncio
async def test_access_device_command_uses_failover_only_for_unavailable_primary(monkeypatch) -> None:
    primary = FakeProvider("home_assistant", unavailable=True)
    failover = FakeProvider("esphome")

    async def fake_runtime_config():
        return SimpleNamespace(gate_control_provider="home_assistant", gate_failover_provider="esphome")

    monkeypatch.setattr(access_devices_module, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(
        access_devices_module,
        "get_access_device_provider",
        lambda name: primary if name == "home_assistant" else failover,
    )

    device = AccessDeviceEntity(
        key="top_gate",
        kind="gate",
        name="Top Gate",
        bindings={
            "home_assistant": AccessDeviceBinding("home_assistant", "cover.top_gate"),
            "esphome": AccessDeviceBinding("esphome", "garage_door"),
        },
    )

    service = AccessDeviceService()
    monkeypatch.setattr(service, "_remember_state", _noop_remember_state)

    outcome = await service._command_with_failover(device, "open", "test")

    assert outcome.accepted is True
    assert outcome.used_provider == "esphome"
    assert outcome.failover_used is True
    assert primary.commands == [("cover.top_gate", "open")]
    assert failover.commands == [("garage_door", "open")]


@pytest.mark.asyncio
async def test_access_device_command_uses_configured_binding_when_primary_missing(monkeypatch) -> None:
    failover = FakeProvider("esphome")

    async def fake_runtime_config():
        return SimpleNamespace(gate_control_provider="home_assistant", gate_failover_provider="esphome")

    monkeypatch.setattr(access_devices_module, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(access_devices_module, "get_access_device_provider", lambda _name: failover)

    device = AccessDeviceEntity(
        key="top_gate",
        kind="gate",
        name="Top Gate",
        bindings={"esphome": AccessDeviceBinding("esphome", "garage_door")},
    )

    service = AccessDeviceService()
    monkeypatch.setattr(service, "_remember_state", _noop_remember_state)

    outcome = await service._command_with_failover(device, "open", "test")

    assert outcome.accepted is True
    assert outcome.used_provider == "esphome"
    assert outcome.failover_used is False
    assert failover.commands == [("garage_door", "open")]


@pytest.mark.asyncio
async def test_access_device_command_does_not_repeat_after_accepted_unverified_state(monkeypatch) -> None:
    primary = FakeProvider(
        "esphome",
        state=GateState.CLOSED,
        command_states=[GateState.CLOSED, GateState.OPENING],
    )

    async def fake_runtime_config():
        return SimpleNamespace(gate_control_provider="esphome", gate_failover_provider="none")

    monkeypatch.setattr(access_devices_module, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(access_devices_module, "get_access_device_provider", lambda _name: primary)
    monkeypatch.setattr(access_devices_module, "COMMAND_CONFIRMATION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(access_devices_module, "COMMAND_CONFIRMATION_POLL_SECONDS", 0.01)

    device = AccessDeviceEntity(
        key="mums_garage_door",
        kind="garage_door",
        name="Mums Garage Door",
        bindings={"esphome": AccessDeviceBinding("esphome", "garage_door")},
    )

    service = AccessDeviceService()
    monkeypatch.setattr(service, "_remember_state", _noop_remember_state)

    outcome = await service._command_with_failover(device, "open", "test")

    assert outcome.accepted is True
    assert outcome.used_provider == "esphome"
    assert outcome.failover_used is False
    assert outcome.verified is False
    assert outcome.metadata["accepted_unverified"] is True
    assert primary.commands == [("garage_door", "open")]
    assert [attempt.verified for attempt in outcome.attempts] == [False]


@pytest.mark.asyncio
async def test_access_device_command_does_not_fail_over_after_accepted_unverified_state(monkeypatch) -> None:
    primary = FakeProvider("home_assistant", state=GateState.CLOSED)
    failover = FakeProvider("esphome", state=GateState.OPENING)

    async def fake_runtime_config():
        return SimpleNamespace(gate_control_provider="home_assistant", gate_failover_provider="esphome")

    monkeypatch.setattr(access_devices_module, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(
        access_devices_module,
        "get_access_device_provider",
        lambda name: primary if name == "home_assistant" else failover,
    )
    monkeypatch.setattr(access_devices_module, "COMMAND_CONFIRMATION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(access_devices_module, "COMMAND_CONFIRMATION_POLL_SECONDS", 0.01)

    device = AccessDeviceEntity(
        key="mums_garage_door",
        kind="garage_door",
        name="Mums Garage Door",
        bindings={
            "home_assistant": AccessDeviceBinding("home_assistant", "cover.mums_garage_door"),
            "esphome": AccessDeviceBinding("esphome", "garage_door"),
        },
    )

    service = AccessDeviceService()
    monkeypatch.setattr(service, "_remember_state", _noop_remember_state)

    outcome = await service._command_with_failover(device, "open", "test")

    assert outcome.accepted is True
    assert outcome.used_provider == "home_assistant"
    assert outcome.failover_used is False
    assert outcome.verified is False
    assert outcome.metadata["accepted_unverified"] is True
    assert primary.commands == [("cover.mums_garage_door", "open")]
    assert failover.commands == []
    assert any(attempt.confirmation_failed for attempt in outcome.attempts if attempt.provider == "home_assistant")


@pytest.mark.asyncio
async def test_access_device_close_retries_same_provider_before_failover(monkeypatch) -> None:
    primary = FakeProvider(
        "home_assistant",
        state=GateState.OPEN,
        command_states=[GateState.OPEN, GateState.OPEN],
    )
    failover = FakeProvider("esphome", state=GateState.CLOSED)

    async def fake_runtime_config():
        return SimpleNamespace(gate_control_provider="home_assistant", gate_failover_provider="esphome")

    monkeypatch.setattr(access_devices_module, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(
        access_devices_module,
        "get_access_device_provider",
        lambda name: primary if name == "home_assistant" else failover,
    )
    monkeypatch.setattr(access_devices_module, "CLOSE_COMMAND_CONFIRMATION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(access_devices_module, "COMMAND_CONFIRMATION_POLL_SECONDS", 0.01)

    device = AccessDeviceEntity(
        key="main_garage_door",
        kind="garage_door",
        name="Main Garage Door",
        bindings={
            "home_assistant": AccessDeviceBinding("home_assistant", "cover.main_garage_door"),
            "esphome": AccessDeviceBinding("esphome", "athom_garage_door"),
        },
    )
    service = AccessDeviceService()
    monkeypatch.setattr(service, "_remember_state", _noop_remember_state)

    outcome = await service._command_with_failover(device, "close", "test")

    assert outcome.accepted is True
    assert outcome.verified is True
    assert outcome.used_provider == "esphome"
    assert outcome.failover_used is True
    assert primary.commands == [
        ("cover.main_garage_door", "close"),
        ("cover.main_garage_door", "close"),
    ]
    assert failover.commands == [("athom_garage_door", "close")]


@pytest.mark.asyncio
async def test_access_device_close_tries_failover_once_before_failure(monkeypatch) -> None:
    primary = FakeProvider(
        "home_assistant",
        state=GateState.OPEN,
        command_states=[GateState.OPEN, GateState.OPEN],
    )
    failover = FakeProvider(
        "esphome",
        state=GateState.OPEN,
        command_states=[GateState.OPEN, GateState.OPEN],
    )

    async def fake_runtime_config():
        return SimpleNamespace(gate_control_provider="home_assistant", gate_failover_provider="esphome")

    monkeypatch.setattr(access_devices_module, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(
        access_devices_module,
        "get_access_device_provider",
        lambda name: primary if name == "home_assistant" else failover,
    )
    monkeypatch.setattr(access_devices_module, "CLOSE_COMMAND_CONFIRMATION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(access_devices_module, "COMMAND_CONFIRMATION_POLL_SECONDS", 0.01)

    device = AccessDeviceEntity(
        key="main_garage_door",
        kind="garage_door",
        name="Main Garage Door",
        bindings={
            "home_assistant": AccessDeviceBinding("home_assistant", "cover.main_garage_door"),
            "esphome": AccessDeviceBinding("esphome", "athom_garage_door"),
        },
    )
    service = AccessDeviceService()
    monkeypatch.setattr(service, "_remember_state", _noop_remember_state)

    outcome = await service._command_with_failover(device, "close", "test")

    assert outcome.accepted is False
    assert outcome.verified is False
    assert primary.commands == [
        ("cover.main_garage_door", "close"),
        ("cover.main_garage_door", "close"),
    ]
    assert failover.commands == [("athom_garage_door", "close")]
    assert [attempt.provider for attempt in outcome.attempts] == [
        "home_assistant",
        "home_assistant",
        "esphome",
    ]
    assert "did not report closed" in (outcome.detail or "")


@pytest.mark.asyncio
async def test_access_device_close_retry_can_confirm_without_failover(monkeypatch) -> None:
    primary = FakeProvider(
        "home_assistant",
        state=GateState.OPEN,
        command_states=[GateState.OPEN, GateState.CLOSED],
    )
    failover = FakeProvider("esphome", state=GateState.CLOSED)

    async def fake_runtime_config():
        return SimpleNamespace(gate_control_provider="home_assistant", gate_failover_provider="esphome")

    monkeypatch.setattr(access_devices_module, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(
        access_devices_module,
        "get_access_device_provider",
        lambda name: primary if name == "home_assistant" else failover,
    )
    monkeypatch.setattr(access_devices_module, "CLOSE_COMMAND_CONFIRMATION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(access_devices_module, "COMMAND_CONFIRMATION_POLL_SECONDS", 0.01)

    device = AccessDeviceEntity(
        key="main_garage_door",
        kind="garage_door",
        name="Main Garage Door",
        bindings={
            "home_assistant": AccessDeviceBinding("home_assistant", "cover.main_garage_door"),
            "esphome": AccessDeviceBinding("esphome", "athom_garage_door"),
        },
    )
    service = AccessDeviceService()
    monkeypatch.setattr(service, "_remember_state", _noop_remember_state)

    outcome = await service._command_with_failover(device, "close", "test")

    assert outcome.accepted is True
    assert outcome.verified is True
    assert outcome.used_provider == "home_assistant"
    assert outcome.failover_used is False
    assert primary.commands == [
        ("cover.main_garage_door", "close"),
        ("cover.main_garage_door", "close"),
    ]
    assert failover.commands == []


@pytest.mark.asyncio
async def test_access_device_command_rejects_when_provider_rejects_before_hardware_acceptance(monkeypatch) -> None:
    primary = FakeProvider("esphome", accepted=False, state=GateState.FAULT)

    async def fake_runtime_config():
        return SimpleNamespace(gate_control_provider="esphome", gate_failover_provider="none")

    monkeypatch.setattr(access_devices_module, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(access_devices_module, "get_access_device_provider", lambda _name: primary)

    device = AccessDeviceEntity(
        key="mums_garage_door",
        kind="garage_door",
        name="Mums Garage Door",
        bindings={"esphome": AccessDeviceBinding("esphome", "garage_door")},
    )

    service = AccessDeviceService()
    monkeypatch.setattr(service, "_remember_state", _noop_remember_state)

    outcome = await service._command_with_failover(device, "open", "test")

    assert outcome.accepted is False
    assert outcome.state is GateState.FAULT
    assert primary.commands == [("garage_door", "open")]
    assert outcome.detail == "test"


@pytest.mark.asyncio
async def test_access_device_read_state_uses_configured_binding_when_primary_missing(monkeypatch) -> None:
    home_assistant = FakeProvider("home_assistant", state=GateState.CLOSED)
    esphome = FakeProvider("esphome", state=GateState.OPEN)

    async def fake_runtime_config():
        return SimpleNamespace(gate_control_provider="esphome", gate_failover_provider="none")

    monkeypatch.setattr(access_devices_module, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(
        access_devices_module,
        "get_access_device_provider",
        lambda name: esphome if name == "esphome" else home_assistant,
    )

    device = AccessDeviceEntity(
        key="main_garage_door",
        kind="garage_door",
        name="Main Garage Door",
        bindings={"home_assistant": AccessDeviceBinding("home_assistant", "cover.main_garage_door")},
    )

    result = await AccessDeviceService().read_state(device)

    assert result.accepted is True
    assert result.provider == "home_assistant"
    assert result.state is GateState.CLOSED


@pytest.mark.asyncio
async def test_access_device_service_stop_closes_provider_lifecycle(monkeypatch) -> None:
    closed: list[str] = []

    class ClosableProvider:
        async def close(self) -> None:
            closed.append("closed")

    monkeypatch.setattr(access_devices_module, "access_device_provider_keys", lambda: ["esphome"])
    monkeypatch.setattr(access_devices_module, "get_access_device_provider", lambda _name: ClosableProvider())

    await AccessDeviceService().stop()

    assert closed == ["closed"]


@pytest.mark.asyncio
async def test_provider_state_event_updates_matching_access_device(monkeypatch) -> None:
    service = AccessDeviceService()
    captured: list[tuple[str, GateState, str | None, str | None]] = []
    device = AccessDeviceEntity(
        key="top_gate",
        kind="gate",
        name="Top Gate",
        bindings={
            "esphome": AccessDeviceBinding(
                "esphome",
                "gate",
                config={"device_id": "top_gate", "key": 7},
            )
        },
    )

    async def fake_list_devices(*, kind=None, enabled_only=False):
        return [device]

    async def fake_remember_state(access_device, state, *, provider, raw_state):
        captured.append((access_device.key, state, provider, raw_state))

    monkeypatch.setattr(service, "list_devices", fake_list_devices)
    monkeypatch.setattr(service, "_remember_state", fake_remember_state)

    await service.handle_provider_state_event(
        "esphome",
        {
            "type": "state",
            "device_id": "top_gate",
            "external_id": "gate",
            "key": 7,
            "state": "open",
            "raw_state": "position=1.0",
        },
    )

    assert captured == [("top_gate", GateState.OPEN, "esphome", "position=1.0")]


@pytest.mark.asyncio
async def test_esphome_provider_uses_binding_device_id_for_duplicate_cover_ids(monkeypatch) -> None:
    provider = ESPHomeAccessDeviceProvider()
    devices = [
        {"id": "top_gate", "name": "Top Gate", "host": "10.0.107.22"},
        {"id": "mums_garage_door", "name": "Mums Garage Door", "host": "10.0.107.18"},
    ]

    async def fake_configured_devices():
        return devices

    monkeypatch.setattr(provider, "_configured_devices", fake_configured_devices)

    device = await provider._device_for_binding(
        AccessDeviceBinding(
            "esphome",
            "garage_door",
            config={"device_id": "mums_garage_door", "key": 1402501725},
        )
    )

    assert device == devices[1]


@pytest.mark.asyncio
async def test_esphome_provider_uses_composite_external_id_for_device_selection(monkeypatch) -> None:
    provider = ESPHomeAccessDeviceProvider()
    devices = [
        {"id": "top_gate", "name": "Top Gate", "host": "10.0.107.22"},
        {"id": "mums_garage_door", "name": "Mums Garage Door", "host": "10.0.107.18"},
    ]

    async def fake_configured_devices():
        return devices

    monkeypatch.setattr(provider, "_configured_devices", fake_configured_devices)

    device = await provider._device_for_binding(
        AccessDeviceBinding("esphome", "mums_garage_door:garage_door")
    )

    assert device == devices[1]


@pytest.mark.asyncio
async def test_esphome_provider_rejects_ambiguous_binding_when_multiple_devices(monkeypatch) -> None:
    provider = ESPHomeAccessDeviceProvider()
    devices = [
        {"id": "top_gate", "name": "Top Gate", "host": "10.0.107.22"},
        {"id": "mums_garage_door", "name": "Mums Garage Door", "host": "10.0.107.18"},
    ]

    async def fake_configured_devices():
        return devices

    monkeypatch.setattr(provider, "_configured_devices", fake_configured_devices)

    with pytest.raises(AccessDeviceProviderUnavailable, match="device_id"):
        await provider._device_for_binding(AccessDeviceBinding("esphome", "garage_door"))


@pytest.mark.asyncio
async def test_esphome_provider_commands_with_configured_key_without_discovery(monkeypatch) -> None:
    provider = ESPHomeAccessDeviceProvider()
    client = FakeESPHomeClient()
    device = {
        "id": "top_gate",
        "name": "Top Gate",
        "host": "10.0.107.22",
        "port": 6053,
        "enabled": True,
    }

    disconnected_session = SimpleNamespace(connected=False)

    async def fake_device_for_binding(binding):
        return device

    async def fake_session_for_device(configured_device):
        assert configured_device == device
        return disconnected_session

    async def fake_connected_client(configured_device, on_stop=None, timeout_budget=None):
        assert configured_device == device
        assert timeout_budget == esphome_module.COLD_COMMAND_CONNECT_BUDGET_SECONDS
        return FakeESPHomeAio, client

    async def fake_sample_cover_state(aio, connected_client, key, *, timeout=3.0):
        assert connected_client == client
        assert key == 42
        return GateState.OPEN

    monkeypatch.setattr(provider, "_device_for_binding", fake_device_for_binding)
    monkeypatch.setattr(provider, "_session_for_device", fake_session_for_device)
    monkeypatch.setattr(provider, "_connected_client", fake_connected_client)
    monkeypatch.setattr(provider, "_sample_cover_state", fake_sample_cover_state)

    result = await provider.command_cover(
        AccessDeviceBinding(
            "esphome",
            "top_gate:garage_door",
            config={"device_id": "top_gate", "key": 42, "object_id": "garage_door"},
        ),
        "open",
        "test",
    )

    assert result.accepted is True
    assert result.state is GateState.OPEN
    assert client.commands == [(42, 1.0)]
    assert client.list_calls == 0
    assert result.metadata["cover_resolution"] == "binding_config"
    assert result.metadata["command_transport"] == "cold_connect"
    assert result.metadata["live_stream_connected"] is False
    assert result.metadata["timing_ms"]["total"] >= 0.0


@pytest.mark.asyncio
async def test_esphome_provider_live_stream_command_reuses_connected_client(monkeypatch) -> None:
    provider = ESPHomeAccessDeviceProvider()
    client = FakeESPHomeClient()
    connected_calls = 0
    device = {
        "id": "top_gate",
        "name": "Top Gate",
        "host": "10.0.107.22",
        "port": 6053,
        "enabled": True,
    }

    async def fake_configured_devices():
        return [device]

    async def fake_connected_client(configured_device, on_stop=None, timeout_budget=None):
        nonlocal connected_calls
        connected_calls += 1
        assert configured_device == device
        assert timeout_budget is None
        client.on_stop = on_stop
        return FakeESPHomeAio, client

    monkeypatch.setattr(esphome_module, "COMMAND_LIVE_STATE_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(provider, "_configured_devices", fake_configured_devices)
    monkeypatch.setattr(provider, "_connected_client", fake_connected_client)

    stream = provider.subscribe_state_changes()
    await asyncio.wait_for(stream.__anext__(), timeout=1.0)
    client.emit(FakeCoverState(7, 0.0))

    command = asyncio.create_task(
        provider.command_cover(
            AccessDeviceBinding(
                "esphome",
                "top_gate:gate",
                config={"device_id": "top_gate", "key": 7, "object_id": "gate"},
            ),
            "open",
            "test",
        )
    )
    await _wait_for_command(client)
    client.emit(FakeCoverState(7, 1.0))
    result = await asyncio.wait_for(command, timeout=1.0)
    await stream.aclose()

    assert result.accepted is True
    assert result.state is GateState.OPEN
    assert result.metadata["command_transport"] == "live_stream"
    assert result.metadata["live_stream_connected"] is True
    assert client.commands == [(7, 1.0)]
    assert connected_calls == 1


@pytest.mark.asyncio
async def test_esphome_provider_current_state_uses_live_cache(monkeypatch) -> None:
    provider = ESPHomeAccessDeviceProvider()
    client = FakeESPHomeClient()
    connected_calls = 0
    device = {
        "id": "top_gate",
        "name": "Top Gate",
        "host": "10.0.107.22",
        "port": 6053,
        "enabled": True,
    }

    async def fake_configured_devices():
        return [device]

    async def fake_connected_client(configured_device, on_stop=None, timeout_budget=None):
        nonlocal connected_calls
        connected_calls += 1
        client.on_stop = on_stop
        return FakeESPHomeAio, client

    monkeypatch.setattr(provider, "_configured_devices", fake_configured_devices)
    monkeypatch.setattr(provider, "_connected_client", fake_connected_client)

    stream = provider.subscribe_state_changes()
    await asyncio.wait_for(stream.__anext__(), timeout=1.0)
    client.emit(FakeCoverState(7, 1.0))

    state = await provider.current_state(
        AccessDeviceBinding(
            "esphome",
            "top_gate:gate",
            config={"device_id": "top_gate", "key": 7, "object_id": "gate"},
        )
    )
    await stream.aclose()

    assert state is GateState.OPEN
    assert connected_calls == 1


@pytest.mark.asyncio
async def test_esphome_provider_discovery_uses_live_stream_metadata(monkeypatch) -> None:
    provider = ESPHomeAccessDeviceProvider()
    client = FakeESPHomeClient()
    connected_calls = 0
    device = {
        "id": "top_gate",
        "name": "Top Gate",
        "host": "10.0.107.22",
        "port": 6053,
        "enabled": True,
    }

    async def fake_configured_devices():
        return [device]

    async def fake_connected_client(configured_device, on_stop=None, timeout_budget=None):
        nonlocal connected_calls
        connected_calls += 1
        assert configured_device == device
        assert timeout_budget is None
        client.on_stop = on_stop
        return FakeESPHomeAio, client

    monkeypatch.setattr(esphome_module, "LIVE_DISCOVERY_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(provider, "_configured_devices", fake_configured_devices)
    monkeypatch.setattr(provider, "_connected_client", fake_connected_client)

    items = await provider.discover_covers()
    await provider.close()

    assert len(items) == 1
    assert items[0].external_id == "gate"
    assert items[0].metadata["device_id"] == "top_gate"
    assert items[0].metadata["discovery_source"] == "live_stream"
    assert items[0].metadata["stream_connected"] is True
    assert connected_calls == 1
    assert client.list_calls == 1
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_esphome_provider_device_specific_discovery_keeps_all_sessions_synced(monkeypatch) -> None:
    provider = ESPHomeAccessDeviceProvider()
    devices = [
        {"id": "top_gate", "name": "Top Gate", "host": "10.0.107.22"},
        {"id": "main_garage_door", "name": "Main Garage Door", "host": "10.0.107.17"},
    ]
    synced_ids: list[list[str]] = []

    class FakeLiveSession:
        async def discovery_items(self, **_kwargs):
            return [AccessDeviceDiscoveryItem(external_id="gate", name="Gate", kind="gate")]

    async def fake_configured_devices():
        return devices

    async def fake_sync_sessions(configured_devices=None):
        synced_ids.append([str(device["id"]) for device in configured_devices])

    async def fake_session_for_device(_device):
        return FakeLiveSession()

    monkeypatch.setattr(provider, "_configured_devices", fake_configured_devices)
    monkeypatch.setattr(provider, "_sync_sessions", fake_sync_sessions)
    monkeypatch.setattr(provider, "_session_for_device", fake_session_for_device)

    await provider.discover_covers(device_id="top_gate")
    await provider.verify_live_device("top_gate")

    assert synced_ids == [
        ["top_gate", "main_garage_door"],
        ["top_gate", "main_garage_door"],
    ]


@pytest.mark.asyncio
async def test_esphome_provider_cold_reconnect_failure_is_unavailable(monkeypatch) -> None:
    provider = ESPHomeAccessDeviceProvider()
    device = {
        "id": "top_gate",
        "name": "Top Gate",
        "host": "10.0.107.22",
        "port": 6053,
        "enabled": True,
    }

    async def fake_device_for_binding(binding):
        return device

    async def fake_session_for_device(configured_device):
        return SimpleNamespace(connected=False)

    async def fake_connected_client(configured_device, on_stop=None, timeout_budget=None):
        raise AccessDeviceProviderUnavailable("Timed out while connecting")

    monkeypatch.setattr(provider, "_device_for_binding", fake_device_for_binding)
    monkeypatch.setattr(provider, "_session_for_device", fake_session_for_device)
    monkeypatch.setattr(provider, "_connected_client", fake_connected_client)

    with pytest.raises(AccessDeviceProviderUnavailable, match="cold reconnect failed"):
        await provider.command_cover(
            AccessDeviceBinding(
                "esphome",
                "top_gate:gate",
                config={"device_id": "top_gate", "key": 7, "object_id": "gate"},
            ),
            "open",
            "test",
        )


@pytest.mark.asyncio
async def test_esphome_provider_does_not_reuse_stale_disconnected_client(monkeypatch) -> None:
    provider = ESPHomeAccessDeviceProvider()
    stale_client = FakeESPHomeClient()
    cold_client = FakeESPHomeClient()
    device = {
        "id": "top_gate",
        "name": "Top Gate",
        "host": "10.0.107.22",
        "port": 6053,
        "enabled": True,
    }
    stale_session = SimpleNamespace(connected=False, client=stale_client)

    async def fake_device_for_binding(binding):
        return device

    async def fake_session_for_device(configured_device):
        return stale_session

    async def fake_connected_client(configured_device, on_stop=None, timeout_budget=None):
        return FakeESPHomeAio, cold_client

    async def fake_sample_cover_state(aio, connected_client, key, *, timeout=3.0):
        return GateState.OPEN

    monkeypatch.setattr(provider, "_device_for_binding", fake_device_for_binding)
    monkeypatch.setattr(provider, "_session_for_device", fake_session_for_device)
    monkeypatch.setattr(provider, "_connected_client", fake_connected_client)
    monkeypatch.setattr(provider, "_sample_cover_state", fake_sample_cover_state)

    result = await provider.command_cover(
        AccessDeviceBinding(
            "esphome",
            "top_gate:gate",
            config={"device_id": "top_gate", "key": 7, "object_id": "gate"},
        ),
        "open",
        "test",
    )

    assert result.accepted is True
    assert stale_client.commands == []
    assert cold_client.commands == [(7, 1.0)]
    assert result.metadata["command_transport"] == "cold_connect"


@pytest.mark.asyncio
async def test_esphome_provider_strips_composite_external_id_when_resolving_cover() -> None:
    provider = ESPHomeAccessDeviceProvider()
    client = FakeESPHomeClient()

    cover = await provider._resolve_cover(
        FakeESPHomeAio,
        client,
        AccessDeviceBinding("esphome", "top_gate:gate"),
    )

    assert cover.key == 7


class FakeCoverInfo:
    def __init__(self, key: int, object_id: str, name: str) -> None:
        self.key = key
        self.object_id = object_id
        self.name = name


class FakeCoverState:
    current_operation = None
    legacy_state = None

    def __init__(self, key: int, position: float) -> None:
        self.key = key
        self.position = position


class FakeESPHomeAio:
    CoverInfo = FakeCoverInfo
    CoverState = FakeCoverState


class FakeESPHomeClient:
    def __init__(self) -> None:
        self.callbacks = []
        self.on_stop = None
        self.disconnected = False
        self.list_calls = 0
        self.commands: list[tuple[int, float]] = []

    async def list_entities_services(self):
        self.list_calls += 1
        return [FakeCoverInfo(7, "gate", "Top Gate")], []

    def cover_command(self, *, key: int, position: float) -> None:
        self.commands.append((key, position))

    def subscribe_states(self, callback) -> None:
        self.callbacks.append(callback)

    def emit(self, state: FakeCoverState) -> None:
        assert self.callbacks
        for callback in list(self.callbacks):
            callback(state)

    async def disconnect(self) -> None:
        self.disconnected = True
        if self.on_stop is not None:
            await self.on_stop(True)


@pytest.mark.asyncio
async def test_esphome_provider_streams_cover_state_events(monkeypatch) -> None:
    provider = ESPHomeAccessDeviceProvider()
    client = FakeESPHomeClient()
    device = {
        "id": "top_gate",
        "name": "Top Gate",
        "host": "10.0.107.22",
        "port": 6053,
        "enabled": True,
    }

    async def fake_configured_devices():
        return [device]

    async def fake_connected_client(configured_device, on_stop=None, timeout_budget=None):
        assert configured_device == device
        client.on_stop = on_stop
        return FakeESPHomeAio, client

    monkeypatch.setattr(provider, "_configured_devices", fake_configured_devices)
    monkeypatch.setattr(provider, "_connected_client", fake_connected_client)

    stream = provider.subscribe_state_changes()
    connected = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
    client.emit(FakeCoverState(7, 1.0))
    state_event = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
    await stream.aclose()

    assert connected["type"] == "connected"
    assert connected["device_id"] == "top_gate"
    assert state_event["type"] == "state"
    assert state_event["provider"] == "esphome"
    assert state_event["device_id"] == "top_gate"
    assert state_event["external_id"] == "gate"
    assert state_event["key"] == 7
    assert state_event["state"] == "open"
    assert client.disconnected is True
