"""Pure provider configuration and fingerprint parity; no provider operations."""
import ast
import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.modules.access_devices import binding_policies as policies, registry
from app.modules.access_devices.base import AccessDeviceBinding, AccessDeviceEntity, binding_is_commandable
from app.services.access_device_configuration import AccessDeviceConfiguration


def config(**overrides):
    return SimpleNamespace(home_assistant_url="https://synthetic.invalid///", home_assistant_token="synthetic-token",
                           esphome_devices=[{"id": "esp-a", "host": "synthetic-a.invalid", "enabled": True}],
                           gate_control_provider="home_assistant", gate_failover_provider="esphome", **overrides)


@pytest.mark.parametrize("case", json.loads((Path(__file__).parent / "fixtures/provider_binding_fingerprints.json").read_text())["cases"], ids=lambda row: row["name"])
def test_exact_pre_extraction_physical_identity_and_frozen_snapshot_golden(case):
    owner = AccessDeviceConfiguration()
    device = AccessDeviceEntity(device_id="00000000-0000-0000-0000-000000000001", key="synthetic_gate", kind="gate", name="Synthetic",
        bindings={key: AccessDeviceBinding(**value) for key, value in case["bindings"].items()})
    settings = SimpleNamespace(**case["settings"])
    assert owner.provider_order(device, settings) == case["order"]
    assert {key: list(owner.binding_identity(binding, settings)) for key, binding in device.bindings.items()} == case["physical_identities"]
    assert owner.target_snapshot(device, settings) == case["target"]


@pytest.mark.parametrize("provider,external,metadata,enabled,devices,expected", [
    ("home_assistant", "cover.gate", {}, True, [], True),
    ("home_assistant", "   ", {}, True, [], False),
    ("home_assistant", "cover.gate", {}, False, [], False),
    ("retired", "gate", {}, True, [], False),
    ("esphome", "gate", {}, True, [{"id": "esp-a", "host": "synthetic.invalid"}], True),
    ("esphome", "esp-a:gate", {}, True, [{"id": "esp-a", "host": "synthetic.invalid"}], True),
    ("esphome", " esp-a:gate", {}, True, [{"id": "esp-a", "host": "synthetic.invalid"}], False),
    ("esphome", "gate", {"device_id": " esp-a "}, True, [{"id": "esp-a", "host": "synthetic.invalid"}], True),
    ("esphome", "unmatched:gate", {}, True, [{"id": "esp-a", "host": "synthetic.invalid"}], False),
    ("esphome", "esp-a:gate", {}, True, [{"id": "esp-a", "host": "synthetic.invalid", "enabled": False}], False),
    ("esphome", "esp-a:gate", {}, True, [{"id": "esp-a"}], False),
    ("esphome", "gate", {}, True, [], False),
    ("esphome", "gate", {}, True, [{"id": "a", "host": "a.invalid"}, {"id": "b", "host": "b.invalid"}], False),
    ("esphome", "gate", {}, False, [{"id": "a", "host": "a.invalid"}], False),
])
def test_configured_predicate_preserves_distinct_id_and_whitespace_semantics(provider, external, metadata, enabled, devices, expected):
    binding = AccessDeviceBinding(provider=provider, external_id=external, config=metadata, enabled=enabled)
    assert binding_is_commandable(binding, settings=SimpleNamespace(home_assistant_url="https://synthetic.invalid", home_assistant_token="synthetic", esphome_devices=devices)) is expected


@pytest.mark.parametrize("url,token", [("", "token"), ("url", ""), ("", "")])
def test_home_assistant_requires_both_existing_credentials(url, token):
    assert not binding_is_commandable(AccessDeviceBinding("home_assistant", "cover.gate"),
                                    settings=SimpleNamespace(home_assistant_url=url, home_assistant_token=token, esphome_devices=[]))


def test_identity_single_device_fallback_is_not_commandability_authority():
    binding = AccessDeviceBinding("esphome", "unmatched:gate")
    settings = config()
    policy = policies.require_binding_policy("esphome")
    assert policy.physical_identity_input(binding.external_id, binding.config, settings) == ["synthetic-a.invalid", 6053, "gate"]
    assert not policy.configured(binding.external_id, binding.config, enabled=True, settings=settings)


def test_vendor_configuration_metadata_and_existing_coercion_are_preserved():
    value = {"device_id": " esp-a ", "future_vendor_metadata": {"ordered": ["z", "a"], "flag": None}}
    before = copy.deepcopy(value)
    for policy in policies.BINDING_POLICIES.values():
        assert policy.validate_config(value) == before
        assert policy.validate_config(None) == {}
        assert policy.validate_config([["future", [2, 1]]]) == {"future": [2, 1]}
    assert value == before


def test_provider_order_keeps_primary_no_failover_and_enabled_rules():
    owner = AccessDeviceConfiguration()
    device = AccessDeviceEntity(key="gate", kind="gate", name="Gate", bindings={
        "home_assistant": AccessDeviceBinding("home_assistant", "cover.gate"),
        "esphome": AccessDeviceBinding("esphome", "esp-a:gate", enabled=False),
        "z_fixture": AccessDeviceBinding("z_fixture", "z"), "a_fixture": AccessDeviceBinding("a_fixture", "a"),
    })
    settings = config()
    settings.gate_control_provider = "z_fixture"
    settings.gate_failover_provider = "none"
    assert owner.provider_order(device, settings) == ["z_fixture", "home_assistant", "a_fixture"]


def test_affected_settings_keep_target_trigger_and_mixed_update_prefix_semantics():
    assert policies.target_setting_keys() == {"home_assistant_url", "home_assistant_token", "home_assistant_gate_open_service", "esphome_devices"}
    assert policies.affected_provider_names({"esphome_devices", "home_assistant_default_media_player"}) == ["home_assistant", "esphome"]
    assert policies.affected_provider_names({"home_assistant_token"}) == ["home_assistant"]
    assert policies.affected_provider_names({"site_timezone"}) == []


def test_policy_and_runtime_maps_are_exhaustive_inert_and_fail_closed(monkeypatch):
    policies.validate_policy_runtime_coverage(registry.access_device_provider_keys())
    assert registry.access_device_provider_keys() == ["home_assistant", "esphome"]
    with pytest.raises(ValueError, match="Duplicate"):
        policies.validate_policy_runtime_coverage(["home_assistant", "esphome", "esphome"])
    with pytest.raises(ValueError, match="missing runtime"):
        policies.validate_policy_runtime_coverage(["home_assistant"])
    with pytest.raises(ValueError, match="missing policy"):
        policies.validate_policy_runtime_coverage(["home_assistant", "esphome", "fixture"])
    monkeypatch.setitem(policies.BINDING_POLICIES, "fixture", replace(policies.BINDING_POLICIES["home_assistant"], provider="fixture"))
    with pytest.raises(ValueError, match="composition mismatch"):
        registry.get_access_device_provider("fixture")
    with pytest.raises(ValueError, match="Unsupported"):
        policies.require_binding_policy("unknown")
    tree = ast.parse(Path(policies.__file__).read_text())
    assert not any(isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.") for node in ast.walk(tree))


def test_complete_settings_object_reaches_all_third_policy_callbacks(monkeypatch):
    observed = []
    settings = config()
    settings.fixture_region = "synthetic-region"

    def configured(external_id, metadata, supplied):
        observed.append(("configured", supplied))
        return supplied.fixture_region == "synthetic-region" and metadata["partition"] == "north"

    def identity(external_id, metadata, supplied):
        observed.append(("identity", supplied))
        return [supplied.fixture_region, external_id, metadata["partition"]]

    def frozen(supplied):
        observed.append(("frozen", supplied))
        return {"region": supplied.fixture_region}

    def validate(value):
        result = dict(value or {})
        if not isinstance(result.get("partition"), str) or not result["partition"].strip():
            raise ValueError("Fixture partition is required")
        result["partition"] = result["partition"].strip()
        return result

    policy = policies.BindingPolicy(provider="fixture", target_setting_keys=frozenset({"fixture_region"}),
        configured_setting_keys=frozenset({"fixture_region"}), configured_input=configured,
        physical_identity_input=identity, frozen_runtime_input=frozen, validate_config_input=validate)
    monkeypatch.setitem(policies.BINDING_POLICIES, "fixture", policy)
    metadata = policy.validate_config({"partition": " north ", "vendor": {"arbitrary": [2, 1]}})
    binding = AccessDeviceBinding("fixture", "cover.synthetic", config=metadata)
    device = AccessDeviceEntity(key="gate", kind="gate", name="Gate", device_id="00000000-0000-0000-0000-000000000001", bindings={"fixture": binding})
    assert AccessDeviceConfiguration.eligibility(device, settings)["admission_eligible"]
    assert AccessDeviceConfiguration().binding_identity(binding, settings) == ("fixture", AccessDeviceConfiguration.fingerprint(["synthetic-region", "cover.synthetic", "north"]))
    snapshot = AccessDeviceConfiguration().target_snapshot(device, settings)
    assert snapshot["binding_snapshot"]["providers"][0]["config_fingerprint"] == AccessDeviceConfiguration.fingerprint([metadata, {"region": "synthetic-region"}])
    assert [name for name, _ in observed] == ["configured", "identity", "frozen"]
    assert all(value is settings for _, value in observed)
    assert "fixture_region" in policies.target_setting_keys() & policies.configured_setting_keys()
    assert policies.affected_provider_names({"fixture_region"}) == ["fixture"]
    with pytest.raises(ValueError, match="partition"):
        policy.validate_config({"partition": 42})


def test_configured_setting_keys_keep_gate_service_locking_distinction():
    assert policies.configured_setting_keys() == {"home_assistant_url", "home_assistant_token", "esphome_devices"}
    assert policies.target_setting_keys() == policies.configured_setting_keys() | {"home_assistant_gate_open_service"}


def test_configured_settings_outside_target_fingerprint_fail_closed(monkeypatch):
    from dataclasses import replace
    original = policies.BINDING_POLICIES["home_assistant"]
    invalid = replace(original, configured_setting_keys=original.configured_setting_keys | {"fixture_unfenced"})
    monkeypatch.setitem(policies.BINDING_POLICIES, "home_assistant", invalid)
    with pytest.raises(ValueError, match="must be target settings"):
        policies.validate_policy_runtime_coverage(registry.access_device_provider_keys())
