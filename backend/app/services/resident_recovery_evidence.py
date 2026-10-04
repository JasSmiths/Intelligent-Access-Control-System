"""Pure, conservative iPhone journey corroboration. No provider requests."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

POLICY_VERSION = 1
MAX_SAMPLES = 64


def parse_time(value: Any) -> datetime | None:
    try:
        at = datetime.fromisoformat(str(value))
        return at.astimezone(UTC) if at.tzinfo else None
    except (ValueError, TypeError):
        return None


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def gate_coordinates(config: Any) -> tuple[float, float] | None:
    latitude = finite_number(getattr(config, "missed_exit_recovery_gate_latitude", None))
    longitude = finite_number(getattr(config, "missed_exit_recovery_gate_longitude", None))
    if latitude is None or longitude is None or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    return latitude, longitude


def configuration_binding(person: Any, config: Any) -> str:
    values = [POLICY_VERSION, str(person.id), person.is_active,
              getattr(person, "missed_exit_recovery_enabled", False),
              getattr(person, "missed_exit_recovery_tracker_entity_id", None),
              person.home_assistant_mobile_app_notify_service,
              getattr(config, "missed_exit_recovery_enabled", False), gate_coordinates(config),
              config.gate_admission_device_key]
    from app.services.notification_requests import configuration_binding as transport_binding
    values.append(transport_binding(config, [{"action": {"type": "mobile"}}]))
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def distance_metres(lat: float, lon: float, gate_lat: float, gate_lon: float) -> float:
    a, b = math.radians(lat), math.radians(gate_lat)
    angle = math.sin((b-a)/2)**2 + math.cos(a)*math.cos(b)*math.sin(math.radians(gate_lon-lon)/2)**2
    return 6371000 * 2 * math.asin(math.sqrt(min(1, max(0, angle))))


def location_sample(new_state: dict, old_state: dict, config: Any, now: datetime) -> tuple[dict | None, str]:
    coords = gate_coordinates(config)
    if coords is None:
        return None, "gate_coordinates_missing"
    attrs, previous = new_state.get("attributes") or {}, old_state.get("attributes") or {}
    if attrs.get("source_type") != "gps":
        return None, "tracker_not_gps"
    lat, lon, accuracy = (finite_number(attrs.get(key)) for key in ("latitude", "longitude", "gps_accuracy"))
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None, "invalid_coordinates"
    if accuracy is None or not 0 < accuracy <= 50:
        return None, "accuracy_insufficient"
    if not old_state or (lat == finite_number(previous.get("latitude")) and lon == finite_number(previous.get("longitude"))):
        return None, "coordinates_unchanged_or_bootstrap"
    at = parse_time(new_state.get("last_updated"))
    if at is None or at > now or now - at > timedelta(seconds=60):
        return None, "update_timestamp_invalid"
    old_lat, old_lon = finite_number(previous.get("latitude")), finite_number(previous.get("longitude"))
    old_at = parse_time(old_state.get("last_updated"))
    if old_lat is not None and old_lon is not None and old_at and at > old_at:
        movement = distance_metres(lat, lon, old_lat, old_lon)
        previous_accuracy = finite_number(previous.get("gps_accuracy")) or 0
        if max(0, movement-accuracy-previous_accuracy)/(at-old_at).total_seconds() > 55:
            return None, "implausible_location_jump"
    signature = hashlib.sha256(f"{lat:.8f},{lon:.8f}".encode()).hexdigest()
    return {"at": at.isoformat(), "distance_m": distance_metres(lat, lon, *coords),
            "accuracy_m": accuracy, "coordinate_hash": signature}, "qualifying_update"


def evaluate_journey(samples: list[dict], *, prior_entry_at: datetime | None,
                     captured_at: datetime, now: datetime) -> tuple[bool, str, dict]:
    checks: dict = {"policy_version": POLICY_VERSION, "sample_count": len(samples),
                    "accuracy_limit_m": 50, "away_threshold_m": 500, "arrival_threshold_m": 150,
                    "minimum_progress_m": 200, "freshness_limit_s": 60}
    valid = [s for s in samples if parse_time(s.get("at")) is not None]
    if not valid or prior_entry_at is None:
        return False, "prior_entry_or_samples_missing", checks
    # Evidence before the vehicle's actual prior entry cannot repair its exit.
    valid = [s for s in valid if parse_time(s["at"]) > prior_entry_at]
    if not valid:
        return False, "journey_before_prior_entry", checks
    terminal = next((i for i,s in enumerate(valid) if s["distance_m"]+s["accuracy_m"] <= 150), None)
    if terminal is not None and terminal != len(valid)-1:
        return False, "completed_arrival_episode", checks
    latest = valid[-1]
    latest_at = parse_time(latest["at"])
    age_at_capture = (captured_at-latest_at).total_seconds()
    age_at_decision = (now-latest_at).total_seconds()
    checks.update(latest_age_at_capture_s=age_at_capture, latest_age_at_decision_s=age_at_decision,
                  latest_distance_m=round(latest["distance_m"], 1), latest_accuracy_m=latest["accuracy_m"])
    if not 0 <= age_at_capture <= 60 or not 0 <= age_at_decision <= 60:
        return False, "location_stale", checks
    last_away_index = next((i for i in range(len(valid)-1, -1, -1)
                           if valid[i]["distance_m"]-valid[i]["accuracy_m"] >= 500), None)
    away = []
    if last_away_index is not None:
        first_away_index = last_away_index
        while first_away_index > 0 and valid[first_away_index-1]["distance_m"]-valid[first_away_index-1]["accuracy_m"] >= 500:
            first_away_index -= 1
        away = valid[first_away_index:last_away_index+1]
    away_span = (parse_time(away[-1]["at"])-parse_time(away[0]["at"])).total_seconds() if len(away)>=2 else 0
    checks.update(away_sample_count=len(away), away_span_s=away_span)
    if len(away)<2 or away_span<300:
        return False, "away_not_established", checks
    approach = [s for s in valid if parse_time(s["at"]) >= latest_at-timedelta(minutes=5)
                and parse_time(s["at"]) >= parse_time(away[-1]["at"])]
    span = (latest_at-parse_time(approach[0]["at"])).total_seconds() if approach else 0
    progress = approach[0]["distance_m"]-latest["distance_m"]-approach[0]["accuracy_m"]-latest["accuracy_m"] if approach else 0
    outward = any(right["distance_m"]-left["distance_m"] > right["accuracy_m"]+left["accuracy_m"]
                  for left,right in pairwise(approach))
    checks.update(approach_sample_count=len(approach), approach_span_s=span,
                  progress_beyond_uncertainty_m=round(progress, 1), outward_leg=outward)
    if len(approach)<3 or span<30 or progress<200 or outward:
        return False, "approach_not_established", checks
    if latest["distance_m"]+latest["accuracy_m"] > 150:
        return False, "outside_gate_proximity", checks
    checks["requirements"] = {"away": True, "approach": True, "freshness": True, "proximity": True}
    return True, "phone_return_corroborated", checks
