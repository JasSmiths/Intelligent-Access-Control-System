"""Synthetic GPS evidence contracts; no database, HTTP, camera or hardware I/O."""
import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import resident_recovery_evidence as evidence

NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)
PRIOR_ENTRY = NOW - timedelta(hours=1)


def config(**overrides):
    values = {
        "missed_exit_recovery_enabled": True,
        "missed_exit_recovery_gate_latitude": 51.5,
        "missed_exit_recovery_gate_longitude": -0.1,
        "gate_admission_device_key": "synthetic-gate",
        "home_assistant_url": "https://synthetic.invalid",
        "home_assistant_token": "synthetic-transport-value",
        "apprise_urls": "",
    }
    return SimpleNamespace(**(values | overrides))


def sample(seconds_before, distance, accuracy=20):
    return {"at": (NOW - timedelta(seconds=seconds_before)).isoformat(),
            "distance_m": distance, "accuracy_m": accuracy}


def return_samples():
    return [sample(420, 1500), sample(120, 700), sample(60, 350), sample(5, 100)]


def evaluate(samples, *, prior_entry_at=PRIOR_ENTRY, captured_at=NOW, now=NOW):
    return evidence.evaluate_journey(samples, prior_entry_at=prior_entry_at,
                                     captured_at=captured_at, now=now)


def state(*, latitude=51.501, longitude=-0.1, accuracy=20, at=NOW, source="gps"):
    return {"state": "not_home", "last_updated": at.isoformat(), "attributes": {
        "source_type": source, "latitude": latitude, "longitude": longitude,
        "gps_accuracy": accuracy,
    }}


def test_valid_return_requires_recent_away_and_approach_evidence():
    ready, reason, checks = evaluate(return_samples())
    assert ready is True
    assert reason == "phone_return_corroborated"
    assert checks["away_sample_count"] == 2
    assert checks["away_span_s"] == 300
    assert checks["approach_sample_count"] == 3
    assert checks["approach_span_s"] == 115
    assert checks["progress_beyond_uncertainty_m"] == 560
    assert checks["latest_age_at_capture_s"] == checks["latest_age_at_decision_s"] == 5
    assert checks["outward_leg"] is False
    assert checks["latest_distance_m"] == 100
    assert checks["latest_accuracy_m"] == 20


@pytest.mark.parametrize("samples, expected_reason", [
    ([], "prior_entry_or_samples_missing"),
    ([sample(420, 90), sample(120, 105), sample(60, 85), sample(5, 100)], "completed_arrival_episode"),
    ([sample(420, 515), sample(120, 505), sample(60, 350), sample(5, 100)], "away_not_established"),
    ([sample(400, 1500), sample(120, 700), sample(60, 350), sample(5, 100)], "away_not_established"),
    ([sample(420, 1500), sample(120, 700), sample(5, 100)], "approach_not_established"),
    ([sample(420, 1500), sample(120, 700), sample(60, 350), sample(30, 400), sample(5, 100)], "approach_not_established"),
    ([sample(420, 1500), sample(120, 700), sample(60, 350), sample(5, 140)], "outside_gate_proximity"),
])
def test_false_positive_trips_do_not_authorize_recovery(samples, expected_reason):
    ready, reason, _checks = evaluate(samples)
    assert ready is False
    assert reason == expected_reason


def test_short_approach_is_rejected_even_with_an_established_away_episode():
    samples = [sample(1000, 1500), sample(700, 700), sample(20, 600),
               sample(10, 350), sample(5, 100)]
    ready, reason, checks = evaluate(samples)
    assert ready is False
    assert reason == "approach_not_established"
    assert checks["approach_span_s"] == 15


def test_progress_inside_accuracy_uncertainty_is_rejected():
    samples = [sample(1000, 1500), sample(700, 700), sample(60, 300, 50),
               sample(30, 200, 50), sample(5, 100, 50)]
    ready, reason, checks = evaluate(samples)
    assert ready is False
    assert reason == "approach_not_established"
    assert checks["progress_beyond_uncertainty_m"] == 100


def test_old_excursion_cannot_be_reused_after_intervening_near_home_arrival():
    samples = [sample(2000, 1500), sample(1700, 700), sample(1600, 80),
               sample(120, 700), sample(60, 350), sample(5, 100)]
    ready, _reason, _checks = evaluate(samples)
    assert ready is False


@pytest.mark.parametrize("captured_at,now", [
    (NOW + timedelta(seconds=56), NOW),
    (NOW, NOW + timedelta(seconds=56)),
    (NOW - timedelta(seconds=6), NOW),
    (NOW, NOW - timedelta(seconds=6)),
])
def test_latest_sample_must_be_recent_at_both_capture_and_decision(captured_at, now):
    ready, reason, _checks = evaluate(return_samples(), captured_at=captured_at, now=now)
    assert ready is False
    assert reason == "location_stale"


def test_no_prior_entry_or_evidence_before_prior_entry_cannot_repair_an_exit():
    assert evaluate(return_samples(), prior_entry_at=None)[:2] == (
        False, "prior_entry_or_samples_missing")
    assert evaluate(return_samples(), prior_entry_at=NOW)[:2] == (
        False, "journey_before_prior_entry")


def test_only_post_entry_samples_count_towards_the_away_episode():
    ready, reason, checks = evaluate(return_samples(), prior_entry_at=NOW - timedelta(seconds=300))
    assert ready is False
    assert reason == "away_not_established"
    assert checks["away_sample_count"] == 1


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), "-inf", None, "invalid"])
def test_nonfinite_or_non_numeric_values_are_unusable(value):
    assert evidence.finite_number(value) is None


@pytest.mark.parametrize("latitude,longitude", [(91, 0), (-91, 0), (0, 181),
    (0, -181), (float("nan"), 0), (0, float("inf")), (True, 0), (0, None)])
def test_gate_coordinate_bounds_are_validated(latitude, longitude):
    assert evidence.gate_coordinates(config(missed_exit_recovery_gate_latitude=latitude,
        missed_exit_recovery_gate_longitude=longitude)) is None


def test_valid_coordinate_boundaries_and_distance_are_finite():
    assert evidence.gate_coordinates(config(missed_exit_recovery_gate_latitude=-90,
        missed_exit_recovery_gate_longitude=180)) == (-90, 180)
    assert evidence.distance_metres(0, 0.001, 0, 0) == pytest.approx(111.195, abs=0.5)


@pytest.mark.parametrize("accuracy", [0, -1, 50.01, True, float("nan"), float("inf"), None])
def test_accuracy_outside_explicit_bound_is_rejected(accuracy):
    result, reason = evidence.location_sample(state(accuracy=accuracy), state(latitude=51.502), config(), NOW)
    assert result is None
    assert reason == "accuracy_insufficient"


@pytest.mark.parametrize("latitude,longitude", [(91, 0), (0, 181),
    (float("nan"), 0), (0, float("inf")), (True, 0)])
def test_invalid_tracker_coordinates_are_rejected(latitude, longitude):
    result, reason = evidence.location_sample(state(latitude=latitude, longitude=longitude),
        state(latitude=51.502), config(), NOW)
    assert result is None
    assert reason == "invalid_coordinates"


@pytest.mark.parametrize("at", [NOW - timedelta(seconds=61), NOW + timedelta(microseconds=1)])
def test_stale_or_future_tracker_timestamps_are_rejected(at):
    result, reason = evidence.location_sample(state(at=at), state(latitude=51.502), config(), NOW)
    assert result is None
    assert reason == "update_timestamp_invalid"


def test_only_coordinate_changes_from_an_existing_gps_tracker_are_evidence():
    old = state(at=NOW - timedelta(minutes=1))
    for new, previous in [(state(accuracy=15), old), (state(), {}), (state(source="router"), old)]:
        result, reason = evidence.location_sample(new, previous, config(), NOW)
        assert result is None
        assert reason in {"coordinates_unchanged_or_bootstrap", "tracker_not_gps"}


def test_qualifying_update_stores_distance_accuracy_and_signature_without_raw_coordinates():
    result, reason = evidence.location_sample(state(accuracy=50), state(latitude=51.502), config(), NOW)
    assert reason == "qualifying_update"
    assert set(result) == {"at", "distance_m", "accuracy_m", "coordinate_hash"}
    assert result["accuracy_m"] == 50
    assert result["distance_m"] == pytest.approx(111.195, abs=0.5)
    assert len(result["coordinate_hash"]) == 64


def test_opposite_side_teleport_is_rejected_even_at_same_gate_radius():
    old = state(latitude=51.49, at=NOW - timedelta(seconds=1))
    new = state(latitude=51.51)
    assert evidence.distance_metres(51.49, -0.1, 51.5, -0.1) == pytest.approx(
        evidence.distance_metres(51.51, -0.1, 51.5, -0.1), abs=0.01)
    result, reason = evidence.location_sample(new, old, config(), NOW)
    assert result is None
    assert reason == "implausible_location_jump"


@pytest.mark.parametrize("value", [None, "not-a-time", "2026-10-02T12:00:00"])
def test_naive_or_invalid_times_are_rejected(value):
    assert evidence.parse_time(value) is None


def test_explicit_timezone_times_are_normalized_to_utc():
    assert evidence.parse_time("2026-10-02T13:00:00+01:00") == NOW
    assert evidence.parse_time("2026-10-02T12:00:00Z") == NOW


@pytest.mark.parametrize("owner_field,config_field", [
    ("is_active", None), ("missed_exit_recovery_enabled", None),
    ("missed_exit_recovery_tracker_entity_id", None),
    ("home_assistant_mobile_app_notify_service", None),
    (None, "missed_exit_recovery_enabled"), (None, "missed_exit_recovery_gate_latitude"),
    (None, "gate_admission_device_key"), (None, "home_assistant_url"),
    (None, "home_assistant_token"), (None, "apprise_urls"),
])
def test_configuration_fingerprint_revokes_authority_after_owner_or_transport_change(monkeypatch, owner_field, config_field):
    from app.services import notification_requests
    monkeypatch.setattr(notification_requests, "confirmation_token_hash",
        lambda value: hashlib.sha256(value.encode()).hexdigest())
    person = SimpleNamespace(id="synthetic-owner", is_active=True,
        missed_exit_recovery_enabled=True, missed_exit_recovery_tracker_entity_id="device_tracker.synthetic",
        home_assistant_mobile_app_notify_service="notify.mobile_app_synthetic")
    runtime = config()
    original = evidence.configuration_binding(person, runtime)
    assert len(original) == 64
    if owner_field:
        value = getattr(person, owner_field)
        setattr(person, owner_field, not value if isinstance(value, bool) else value + "_changed")
    else:
        value = getattr(runtime, config_field)
        setattr(runtime, config_field, not value if isinstance(value, bool) else
                value + 0.01 if isinstance(value, float) else value + "_changed")
    assert evidence.configuration_binding(person, runtime) != original
