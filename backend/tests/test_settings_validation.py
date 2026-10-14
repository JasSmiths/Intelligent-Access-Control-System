"""The settings owner validates the entire candidate without exposing values."""
import pytest

from app.services.settings import DEFAULT_DYNAMIC_SETTINGS, runtime_config_from_values


def candidate(**updates):
    return {**{key: default for key, (_, default, _) in DEFAULT_DYNAMIC_SETTINGS.items()}, **updates}


@pytest.mark.parametrize("key,value", [
    ("auth_access_token_minutes", "not-a-number"),
    ("auth_access_token_minutes", None),
    ("auth_access_token_minutes", True),
    ("lpr_debounce_quiet_seconds", float("nan")),
    ("lpr_debounce_max_seconds", float("inf")),
    ("llm_timeout_seconds", 1e100),
    ("schedule_default_policy", "permit-everyone"),
    ("gate_control_provider", "unknown"),
    ("lpr_zone_filter_mode", "unknown"),
    ("site_timezone", "Not/AZone"),
    ("auth_cookie_secure", "perhaps"),
    ("unifi_protect_port", 65536),
    ("unifi_protect_snapshot_width", 1),
    ("esphome_devices", "not-json"),
])
def test_invalid_candidate_is_rejected_without_echoing_value(key, value):
    with pytest.raises(ValueError) as error:
        runtime_config_from_values(candidate(**{key: value}))
    assert key in str(error.value)
    if isinstance(value, str) and value:
        assert value not in str(error.value)


def test_debounce_limits_must_be_consistent():
    with pytest.raises(ValueError, match="lpr_debounce"):
        runtime_config_from_values(candidate(lpr_debounce_quiet_seconds=8, lpr_debounce_max_seconds=6))


def test_supported_conversions_and_unset_optional_values():
    config = runtime_config_from_values(candidate(auth_access_token_minutes="60", auth_cookie_secure="yes",
        lpr_similarity_threshold="0.8", gate_admission_device_key=None))
    assert config.auth_access_token_minutes == 60
    assert config.auth_cookie_secure is True
    assert config.lpr_similarity_threshold == .8
    assert config.gate_admission_device_key is None
