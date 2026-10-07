"""Provider extension/configuration contracts on disposable PostgreSQL only."""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
import copy
from dataclasses import asdict, dataclass, replace
from types import SimpleNamespace
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.db.session import AsyncSessionLocal
from app.models import AccessDevice, AccessDeviceProviderBinding, AuditLog, SystemSetting
from app.modules.access_devices import binding_policies as policies, registry
from app.modules.access_devices.base import AccessDeviceDiscoveryItem
from app.services import settings
from app.services.access_device_commands import AccessDeviceCommandJournal
from app.services.access_device_configuration import AccessDeviceConfiguration
from app.services.access_devices import AccessDeviceService

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def owned_resources(isolated_resources):
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE access_devices, access_device_command_records CASCADE"))
        await session.commit()
    settings.invalidate_runtime_config_cache()
    try:
        yield
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(text("TRUNCATE access_devices, access_device_command_records, system_settings, audit_logs CASCADE"))
            await session.commit()
        settings.invalidate_runtime_config_cache()


async def new_device():
    async with AsyncSessionLocal() as session:
        row = AccessDevice(key=f"synthetic_{uuid.uuid4().hex}", kind="gate", name="Synthetic gate", enabled=True, open_for_access=True)
        session.add(row)
        await session.commit()
        return row


class InertFixtureProvider:
    provider_key = "fixture"
    display_name = "Synthetic cover fixture"

    async def discover_covers(self, device_id=None):
        assert device_id is None
        return [AccessDeviceDiscoveryItem(external_id=" fixture:cover ", name="Synthetic cover", kind="gate",
            metadata={"partition": " north ", "future_vendor_metadata": {"ordered": [2, 1], "flag": None}})]

    async def configured(self):
        raise AssertionError("Runtime transport configuration must not be called")

    async def status(self, **kwargs):
        raise AssertionError("Provider status must not be called")

    async def command_cover(self, *args, **kwargs):
        raise AssertionError("Hardware commands are forbidden")

    async def current_state(self, *args, **kwargs):
        raise AssertionError("Provider state I/O is forbidden")

    async def observe_state(self, *args, **kwargs):
        raise AssertionError("Provider observation I/O is forbidden")

    async def subscribe_state_changes(self):
        raise AssertionError("Provider subscriptions are forbidden")
        yield  # Async iterator shape, never reached.


def fixture_policy(observed):
    def validate(value):
        result = dict(value or {})
        if not isinstance(result.get("partition"), str) or not result["partition"].strip():
            raise ValueError("Fixture partition is required")
        result["partition"] = result["partition"].strip()
        return result

    def configured(external_id, metadata, supplied):
        observed.append(("configured", supplied))
        return bool(supplied.fixture_region and metadata["partition"])

    def identity(external_id, metadata, supplied):
        observed.append(("identity", supplied))
        return [supplied.fixture_region, external_id, metadata["partition"]]

    def frozen(supplied):
        observed.append(("frozen", supplied))
        return {"region": supplied.fixture_region}

    return policies.BindingPolicy(provider="fixture", target_setting_keys=frozenset({"fixture_region"}),
        configured_setting_keys=frozenset({"fixture_region"}), configured_input=configured,
        physical_identity_input=identity, frozen_runtime_input=frozen, validate_config_input=validate)


async def test_third_provider_discovery_config_upsert_and_frozen_plan(monkeypatch):
    observed = []
    monkeypatch.setitem(policies.BINDING_POLICIES, "fixture", fixture_policy(observed))
    monkeypatch.setitem(registry._PROVIDERS, "fixture", InertFixtureProvider())
    row = await new_device()
    discovered, = await registry.get_access_device_provider("fixture").discover_covers()
    before = copy.deepcopy(discovered.metadata)
    service = AccessDeviceService()
    entity = await service.upsert_binding(str(row.id), "fixture", {"external_id": discovered.external_id, "config": discovered.metadata})
    binding = entity.bindings["fixture"]
    assert binding.external_id == "fixture:cover"
    assert binding.config == {**before, "partition": "north"}
    assert discovered.metadata == before
    async with AsyncSessionLocal() as session:
        persisted = await session.scalar(select(AccessDeviceProviderBinding).where(AccessDeviceProviderBinding.access_device_id == row.id))
        assert persisted.config == binding.config and persisted.external_id == binding.external_id
    supplied = SimpleNamespace(fixture_region="synthetic-region", gate_control_provider="home_assistant", gate_failover_provider="none")
    owner = AccessDeviceConfiguration()
    assert owner.eligibility(entity, supplied)["admission_eligible"]
    assert owner.binding_identity(binding, supplied) == ("fixture", owner.fingerprint(["synthetic-region", "fixture:cover", "north"]))
    target = owner.target_snapshot(entity, supplied)
    assert target["binding_snapshot"]["providers"] == [{"provider": "fixture", "external_id": "fixture:cover",
        "config_fingerprint": owner.fingerprint([binding.config, {"region": "synthetic-region"}])}]
    assert [kind for kind, _ in observed] == ["configured", "identity", "frozen"]
    assert all(value is supplied for _, value in observed)
    with pytest.raises(ValueError, match="partition"):
        await service.upsert_binding(str(row.id), "fixture", {"external_id": "changed", "config": {"partition": 42}})
    async with AsyncSessionLocal() as session:
        persisted = await session.scalar(select(AccessDeviceProviderBinding).where(AccessDeviceProviderBinding.access_device_id == row.id))
        assert persisted.config == binding.config and persisted.external_id == binding.external_id


async def test_policy_without_runtime_cannot_write(monkeypatch):
    monkeypatch.setitem(policies.BINDING_POLICIES, "fixture", fixture_policy([]))
    row = await new_device()
    with pytest.raises(ValueError, match="composition mismatch"):
        await AccessDeviceService().upsert_binding(str(row.id), "fixture", {"external_id": "cover", "config": {"partition": "north"}})
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(AccessDeviceProviderBinding.id)) is None


async def test_blank_existing_binding_deletes_before_provider_config_validation(monkeypatch):
    policy = fixture_policy([])
    monkeypatch.setitem(policies.BINDING_POLICIES, "fixture", policy)
    monkeypatch.setitem(registry._PROVIDERS, "fixture", InertFixtureProvider())
    row, service = await new_device(), AccessDeviceService()
    await service.upsert_binding(str(row.id), "fixture", {"external_id": "cover", "config": {"partition": "north"}})
    def forbidden(value):
        raise AssertionError("Blank deletion must precede nonblank validation")
    monkeypatch.setitem(policies.BINDING_POLICIES, "fixture", replace(policy, validate_config_input=forbidden))
    entity = await service.upsert_binding(str(row.id), "fixture", {"external_id": "   ", "config": {"partition": 42}})
    assert entity.bindings == {}
    with pytest.raises(ValueError, match="external ID is required"):
        await service.upsert_binding(str(row.id), "fixture", {"external_id": "", "config": {"partition": 42}})


async def test_secret_blank_retention_clearable_secret_and_ordered_esphome_values():
    devices = [{"id": "second", "name": "Second", "host": "two.invalid", "encryption_key": "synthetic-two"},
               {"id": "first", "name": "First", "host": "one.invalid", "encryption_key": "synthetic-one"}]
    await settings.update_settings({"home_assistant_url": "https://synthetic.invalid///", "home_assistant_token": "synthetic-secret",
        "apprise_urls": "json://synthetic.invalid", "esphome_devices": devices})
    for blank in (None, ""):
        await settings.update_settings({"home_assistant_token": blank, "esphome_devices": blank})
    await settings.update_settings({"apprise_urls": ""})
    async with AsyncSessionLocal() as session:
        config = await settings.get_runtime_config_for_session(session)
        assert config.home_assistant_token == "synthetic-secret"
        assert config.home_assistant_url == "https://synthetic.invalid///"
        assert config.apprise_urls == ""
        assert config.esphome_devices == settings.normalize_esphome_devices(devices)
        assert [item["id"] for item in config.esphome_devices] == ["second", "first"]
        audits = list((await session.scalars(select(AuditLog))).all())
        assert audits and "synthetic-secret" not in str([audit.diff for audit in audits])


async def test_provider_setting_waits_for_target_lock_then_rechecks_unresolved_command(monkeypatch):
    row = await new_device()
    await AccessDeviceService().upsert_binding(str(row.id), "home_assistant", {"external_id": "cover.synthetic", "config": {"vendor": [2, 1]}})
    waiting = asyncio.Event()
    original = AccessDeviceCommandJournal.assert_configurable
    async def observe_lock(session, target_id):
        waiting.set()
        await original(session, target_id)
    monkeypatch.setattr(AccessDeviceCommandJournal, "assert_configurable", observe_lock)
    owner = AsyncSessionLocal()
    task = None
    try:
        await AccessDeviceCommandJournal.lock_target(owner, row.id)
        owner_pid = await owner.scalar(text("SELECT pg_backend_pid()"))
        task = asyncio.create_task(settings.update_settings({"home_assistant_url": "https://changed.invalid"}))
        await asyncio.wait_for(waiting.wait(), 8)
        # Observe the real blocked PostgreSQL lock; elapsed time is not proof.
        async with AsyncSessionLocal() as observer:
            for _ in range(200):
                blocked = await observer.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE :owner = ANY(pg_blocking_pids(pid)))"), {"owner": owner_pid})
                if blocked:
                    break
                await asyncio.sleep(.01)
            assert blocked and not task.done()
            assert await observer.scalar(select(SystemSetting.key)) is None
        identity = str(uuid.uuid4())
        claimed = await AccessDeviceCommandJournal().claim(owner, target={"target_device_id": str(row.id), "device_key": row.key, "kind": "gate",
            "binding_fingerprint": "a" * 64, "binding_snapshot": {"providers": [{"provider": "home_assistant", "external_id": "cover.synthetic"}]}},
            action="open", intent_id=identity, operation_key=identity, gate_command_id=None, expires_at=None)
        assert claimed.acquired
        await owner.commit()
        with pytest.raises(ValueError, match="unresolved command"):
            await asyncio.wait_for(task, 8)
        async with AsyncSessionLocal() as observer:
            assert await observer.scalar(select(SystemSetting.key)) is None
            assert await observer.scalar(select(AuditLog.id)) is None
    finally:
        await owner.rollback()
        await owner.close()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_third_typed_setting_reaches_real_admission_revalidation(monkeypatch):
    observed = []
    monkeypatch.setitem(policies.BINDING_POLICIES, "fixture", fixture_policy(observed))
    monkeypatch.setitem(registry._PROVIDERS, "fixture", InertFixtureProvider())
    # A real provider adds its typed field/conversion in the normal settings
    # owner. This inert fixture extends precisely that seam, not a dispatcher.
    @dataclass(frozen=True)
    class FixtureRuntimeConfig(settings.RuntimeConfig):
        fixture_region: str
    original = settings.runtime_config_from_values
    def from_values(values):
        return FixtureRuntimeConfig(**asdict(original(values)), fixture_region=str(values["fixture_region"] or ""))
    monkeypatch.setitem(settings.DEFAULT_DYNAMIC_SETTINGS, "fixture_region", ("fixture", "", "Synthetic region"))
    monkeypatch.setattr(settings, "runtime_config_from_values", from_values)
    row = await new_device()
    await AccessDeviceService().upsert_binding(str(row.id), "fixture", {"external_id": "cover", "config": {"partition": "north"}})
    await settings.update_settings({"fixture_region": "synthetic-region", "gate_admission_device_key": row.key})
    assert len(observed) == 1 and observed[0][0] == "configured"
    assert type(observed[0][1]) is FixtureRuntimeConfig
    assert observed[0][1].fixture_region == "synthetic-region"
    observed.clear()
    with pytest.raises(ValueError, match="admission gate must be enabled"):
        await settings.update_settings({"fixture_region": ""})
    assert len(observed) == 1 and observed[0][1].fixture_region == ""
    async with AsyncSessionLocal() as session:
        config = await settings.get_runtime_config_for_session(session)
        assert config.fixture_region == "synthetic-region"


async def test_corrupt_unrelated_setting_can_be_repaired_together_or_admission_unset():
    row = await new_device()
    await AccessDeviceService().upsert_binding(str(row.id), "home_assistant", {"external_id": "cover.synthetic"})
    await settings.update_settings({"home_assistant_url": "https://before.invalid", "home_assistant_token": "synthetic-token", "gate_admission_device_key": row.key})
    await settings.update_settings({"auth_access_token_minutes": "invalid-retained-value"})
    # A configured admission candidate now uses execution's full conversion.
    with pytest.raises(ValueError, match="invalid literal"):
        await settings.update_settings({"home_assistant_url": "https://after.invalid"})
    async with AsyncSessionLocal() as session:
        original = await session.scalar(select(SystemSetting).where(SystemSetting.key == "home_assistant_url"))
        assert settings.decrypted_value(original) == "https://before.invalid"
    await settings.update_settings({"home_assistant_url": "https://after.invalid", "auth_access_token_minutes": 12})
    async with AsyncSessionLocal() as session:
        config = await settings.get_runtime_config_for_session(session)
        assert config.home_assistant_url == "https://after.invalid" and config.auth_access_token_minutes == 12
    await settings.update_settings({"auth_access_token_minutes": "invalid-retained-value"})
    # Explicit unset still returns before conversion, providing a safe escape.
    await settings.update_settings({"gate_admission_device_key": None})
    async with AsyncSessionLocal() as session:
        records = {row.key: row for row in (await session.scalars(select(SystemSetting))).all()}
        assert settings.decrypted_value(records["gate_admission_device_key"]) is None
        assert settings.decrypted_value(records["auth_access_token_minutes"]) == "invalid-retained-value"
