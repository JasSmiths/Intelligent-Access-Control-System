"""Inert target/receipt contracts. No database connections or provider I/O."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest

from app.modules.access_devices.base import AccessDeviceBinding, AccessDeviceEntity
from app.modules.gate import access_devices as adapter_module
from app.modules.gate.base import CommandDelivery, GateCommandContext, GateState
from app.services.access_device_commands import AccessDeviceCommandJournal, DeviceCommandClaim, device_command_receipt
from app.services.access_devices import AccessDeviceOperationResult, AccessDeviceService
from app.services.access_device_configuration import AccessDeviceConfiguration


def config(**changes):
    return SimpleNamespace(**{"home_assistant_url": "http://synthetic.invalid", "home_assistant_token": "synthetic-only",
        "esphome_devices": [], "gate_control_provider": "home_assistant", "gate_failover_provider": "none",
        "gate_admission_device_key": "entry", **changes})


def device(key="entry", *, automatic=True):
    return AccessDeviceEntity(key=key, name=key, kind="gate", device_id=str(uuid.uuid4()),
        open_for_access=automatic, bindings={"home_assistant": AccessDeviceBinding("home_assistant", f"cover.{key}")})


def plan(service, devices, runtime, *, selected=None, automatic=True):
    return AccessDeviceConfiguration().target_plan(devices, runtime, action="open", target_device_key=selected,
                                require_admission=automatic, gate_only=True)


def test_selected_manual_gate_is_exact_and_does_not_require_automatic_membership():
    service = AccessDeviceService()
    entry, manual = device(), device("manual", automatic=False)
    result = plan(service, [entry, manual], config(gate_admission_device_key=None), selected="manual", automatic=False)
    assert [item["target_device_id"] for item in result["targets"]] == [manual.device_id]
    assert result["admission_target_device_id"] is None
    assert [item["device_key"] for item in plan(service, [entry, manual], config())["targets"]] == ["entry"]


@pytest.mark.parametrize("designation", [None, "missing", "manual"])
def test_automatic_target_plan_requires_explicit_commandable_automatic_admission(designation):
    with pytest.raises(ValueError):
        plan(AccessDeviceService(), [device(), device("manual", automatic=False)], config(gate_admission_device_key=designation))


def test_target_plan_fingerprint_changes_with_binding_or_configuration_but_contains_no_secret():
    service, entry = AccessDeviceService(), device()
    original = plan(service, [entry], config())
    endpoint = plan(service, [entry], config(home_assistant_url="http://different.invalid"))
    rebound = replace(entry, bindings={"home_assistant": AccessDeviceBinding("home_assistant", "cover.other")})
    changed = plan(service, [rebound], config())
    assert original != endpoint != changed
    assert "synthetic-only" not in str(original)
    assert "http://synthetic.invalid" not in str(original)


def test_known_duplicate_native_bindings_cannot_be_independent_physical_targets():
    entry = device()
    alias = replace(device("alias"), bindings=entry.bindings)
    with pytest.raises(ValueError, match="share a provider target"):
        plan(AccessDeviceService(), [entry, alias], config())


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_verified,secondary_verified,expected", [(True, False, True), (False, True, False)])
async def test_aggregate_admission_follows_designated_target_only(monkeypatch, entry_verified, secondary_verified, expected):
    entry, secondary = device(), device("secondary")
    outcomes = []
    for target, verified in [(entry, entry_verified), (secondary, secondary_verified)]:
        receipt = {"target_device_id": target.device_id, "state": "open" if verified else "unknown", "verified": verified,
                   "accepted": False, "delivery": "unknown", "requires_reconciliation": not verified}
        outcomes.append(AccessDeviceOperationResult(target, "open", False, GateState(receipt["state"]),
            delivery=CommandDelivery.UNKNOWN, metadata={"verified": verified, "target_receipt": receipt,
                "requires_reconciliation": not verified, "admission_target_device_id": entry.device_id}))

    class Service:
        async def open_access_gates(self, *_args, **_kwargs):
            return outcomes

    monkeypatch.setattr(adapter_module, "get_access_device_service", lambda: Service())
    result = await adapter_module.AccessDeviceGateController().open_gate("synthetic",
        command_context=GateCommandContext(None, "", "intent", "key", require_admission=False))
    assert result.metadata["admission_verified"] is expected
    assert result.metadata["mechanically_confirmed"] is False
    assert result.metadata["requires_reconciliation"] is True
    assert result.accepted is False
    assert len(result.metadata["target_receipts"]) == 2


class FixedClockSession:
    def __init__(self, row, now):
        self.row, self.now = row, now

    async def get(self, *_args, **_kwargs):
        return self.row

    async def scalar(self, _query):
        return self.now

    async def flush(self):
        pass


def prepared(now, *, expiry=None):
    return SimpleNamespace(id=uuid.uuid4(), state="prepared", lease_token="owned", lease_expires_at=now + timedelta(seconds=120),
        binding_snapshot={"expires_at": expiry.isoformat() if expiry else None,
                          "providers": [{"provider": "home_assistant", "external_id": "cover.synthetic"}]}, provider_receipts=[],
        accepted=None, gate_state="unknown", created_at=now - timedelta(seconds=60), attempted_at=None,
        completed_at=None, detail=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("age,allowed", [(59, True), (60, True), (61, False)])
async def test_recognition_dispatch_cutoff_is_exactly_sixty_seconds_inclusive(age, allowed):
    captured = datetime(2026, 9, 12, 12, tzinfo=UTC)
    now = captured + timedelta(seconds=age)
    row = prepared(now, expiry=captured + timedelta(seconds=60))
    result = await AccessDeviceCommandJournal().begin_attempt(FixedClockSession(row, now),
        DeviceCommandClaim(row, "owned", True), provider="home_assistant", external_id="cover.synthetic")
    assert result is allowed
    assert row.state == ("attempting" if allowed else "not_sent")
    assert len(row.provider_receipts) == int(allowed)


@pytest.mark.asyncio
async def test_exact_lease_expiry_is_not_dispatch_authority():
    now = datetime(2026, 9, 12, 12, tzinfo=UTC)
    row = prepared(now)
    row.lease_expires_at = now
    assert await AccessDeviceCommandJournal().begin_attempt(FixedClockSession(row, now),
        DeviceCommandClaim(row, "owned", True), provider="home_assistant", external_id="cover.synthetic") is False
    assert row.state == "not_sent"
    assert row.provider_receipts == []


def test_expired_attempt_holds_unknown_and_retains_physical_reservation():
    now = datetime(2026, 9, 12, 12, tzinfo=UTC)
    row = prepared(now)
    row.state, row.lease_expires_at, row.attempted_at = "attempting", now, now - timedelta(seconds=120)
    AccessDeviceCommandJournal.expire(row, now)
    assert row.state == "unknown"
    assert row.accepted is None
    assert row.lease_token is None
    assert row.gate_state == "unknown"


def test_unknown_verified_receipt_remains_truthful_after_observation_retention():
    now = datetime(2026, 9, 12, 12, tzinfo=UTC)
    target = uuid.uuid4()
    row = SimpleNamespace(id=uuid.uuid4(), target_device_id=target, device_key="entry", action="open", state="verified",
        accepted=None, gate_state="open", verification_observation_id=None, attempted_at=now,
        completed_at=now, provider_receipts=[], detail=None, binding_fingerprint="a" * 64,
        verification_evidence={"observation_id": str(uuid.uuid4()), "observed_at": now.isoformat(),
                               "state": "open", "target_device_id": str(target), "provider": "home_assistant"})
    receipt = device_command_receipt(row)
    assert receipt["accepted"] is False
    assert receipt["delivery"] == "unknown"
    assert receipt["verified"] is True
    assert receipt["requires_reconciliation"] is False
    assert receipt["verification_evidence"]["observed_at"] == now.isoformat()


def test_automatic_entry_policy_is_part_of_the_frozen_plan_and_requires_admission():
    service, entry = AccessDeviceService(), device()
    ordinary = plan(service, [entry], config())
    automatic = AccessDeviceConfiguration().target_plan([entry], config(), action="open", target_device_key=None,
        require_admission=True, gate_only=True, automatic_entry_policy=True)
    assert ordinary != automatic and automatic["automatic_entry_policy"] is True
    with pytest.raises(ValueError, match="admission-validated"):
        AccessDeviceConfiguration().target_plan([entry], config(), action="open", target_device_key=None,
            require_admission=False, gate_only=True, automatic_entry_policy=True)


@pytest.mark.parametrize("state,age,eligible", [("closed", 0, True), ("open", 10, True),
    ("opening", 0, True), ("unknown", 0, False), ("fault", 0, False),
    ("closing", 0, False), ("closed", 10.001, False), ("closed", -0.001, False)])
def test_new_fanout_attempt_requires_fresh_attributed_entry_sample(state, age, eligible):
    service, entry = AccessDeviceService(), device()
    frozen = plan(service, [entry], config())
    now = datetime(2026, 9, 12, 12, tzinfo=UTC)
    sample = {"state": state, "observed_at": (now - timedelta(seconds=age)).isoformat(),
              "target_device_id": entry.device_id, "binding_fingerprint": frozen["targets"][0]["binding_fingerprint"]}
    assert service._fresh_entry_sample(sample, frozen, now) is eligible
    assert service._fresh_entry_sample({**sample, "binding_fingerprint": "wrong"}, frozen, now) is False
